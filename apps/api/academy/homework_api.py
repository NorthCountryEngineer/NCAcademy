"""Student-owned Homework Help orchestration over the approved lesson selector.

Triage has no model, credentials, image access or authority of its own. It can
select only currently admitted classes; uncertain matches ask the student.
"""
import base64
import hashlib
import re
from urllib.parse import quote

from fastapi import Depends, HTTPException, Request, Response
from pydantic import BaseModel, ConfigDict, Field, model_validator
from sqlalchemy import JSON, cast, func, select

from .curated_lesson_api import _image_bytes, _image_metadata, _normalized_images, curated_turn_dict
from .curated_schemas import CuratedImage, CuratedLessonInput
from .db import Audit, Record, audit_dict, canonical, now, uid
from .learning_preferences import preference_snapshot, student_principal
from .lesson_api import authorize_lesson_teacher, course_for_student, lesson_session_id, protect_released_lesson, resolve_lesson_runtime, safe_lesson_data
from .permissions import bind_scope, require_school, scope_id
from .teacher_api import course_teacher_cards


MAX_DAILY_MESSAGES = 100
MAX_DAILY_IMAGE_BYTES = 10 * 1024 * 1024


class HomeworkSessionInput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    idempotency_key: str = Field(min_length=8, max_length=100, pattern=r"^[a-zA-Z0-9_-]+$")
    school_id: str | None = Field(default=None, min_length=1, max_length=64)


class HomeworkMessageInput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    message: str = Field(default="", max_length=10000)
    images: list[CuratedImage] = Field(default_factory=list, max_length=2)
    image_use_confirmed: bool = Field(default=False, strict=True)
    idempotency_key: str = Field(min_length=8, max_length=100, pattern=r"^[a-zA-Z0-9_-]+$")
    course_id: str | None = Field(default=None, min_length=1, max_length=64)
    teacher_profile_id: str | None = Field(default=None, min_length=1, max_length=64)

    @model_validator(mode="after")
    def context(self):
        if not self.message.strip() and not self.images:
            raise ValueError("Explain your homework question or attach a picture")
        if self.teacher_profile_id and not self.course_id:
            raise ValueError("A teacher choice requires a course choice")
        return self


class HomeworkResumeInput(BaseModel):
    model_config = ConfigDict(extra="forbid")


def _messages(db, identity, session_id):
    return list(db.scalars(select(Record).where(Record.tenant_id == identity["tenant_id"], Record.kind == "homework_message", Record.parent_id == session_id, Record.owner_id == identity["id"]).order_by(Record.created_at, Record.id)))


def _session_descriptor(row):
    return {"id": row.id, **{key: row.value[key] for key in ("created_at", "idempotency_key", "school_id")}}


def _courses(db, identity, settings, school_id=None):
    student_principal(db, identity, settings)
    if school_id:
        require_school(db, identity, school_id, settings, {"student"}, human=True)
    enrollment = db.scalars(select(Record).where(Record.tenant_id == identity["tenant_id"], Record.kind == "enrollment", Record.owner_id == identity["id"]))
    result = []
    for enrolled in enrollment:
        if enrolled.value.get("role") != "student":
            continue
        try:
            course, school = course_for_student(db, identity, enrolled.parent_id, settings)
            if school_id and school != school_id:
                continue
            cards = course_teacher_cards(db, identity, course, school, settings)
        except HTTPException:
            continue
        teachers = [{"id": item["id"], "name": item["name"], "images_allowed": item["curation_images_allowed"], "model_label": item["curation_model_label"], "max_input_chars": item["curation_max_input_chars"]} for item in cards if item["curation_available"]]
        result.append({"id": course.id, "name": course.value["title"], "school_id": school, "available": bool(teachers), "teachers": teachers})
    return sorted(result, key=lambda item: (item["name"].casefold(), item["id"]))


_STOP_WORDS = {"a", "an", "and", "are", "can", "class", "course", "do", "for", "from", "help", "homework", "how", "i", "in", "is", "me", "my", "of", "on", "question", "the", "this", "to", "understand", "with"}


def _terms(value):
    return set(re.findall(r"[a-z0-9]+", value.lower())) - _STOP_WORDS


def _triage(db, courses, body, previous):
    available = [item for item in courses if item["available"]]
    chosen = next((item for item in available if item["id"] == body.course_id), None)
    method = "student_choice" if chosen else "clarification"
    if body.course_id and not chosen:
        raise HTTPException(403, "Choose a currently enrolled course with an approved Homework Help teacher")
    # A photograph is never sent to a cross-course routing model. Ask explicitly
    # even when only one class is available, so the destination is understood.
    if not chosen and body.message.strip():
        scored = []
        for item in available:
            course = db.get(Record, item["id"])
            value = course.value
            curriculum = value.get("curriculum") or {}
            title_terms = _terms(item["name"])
            related = _terms(str(value.get("description", "")) + " " + str(curriculum.get("subject", "")))
            terms = _terms(body.message)
            score = len(terms & title_terms) * 3 + len(terms & related)
            scored.append((score, item))
        scored.sort(key=lambda item: (-item[0], item[1]["id"]))
        if scored and scored[0][0] >= 3 and (len(scored) == 1 or scored[0][0] > scored[1][0]):
            chosen, method = scored[0][1], "enrolled_course_terms_v1"
        elif previous and (not scored or scored[0][0] < 3):
            last_course = previous[-1].value["triage"].get("course_id")
            chosen = next((item for item in available if item["id"] == last_course), None)
            if chosen:
                method = "conversation_course_v1"
        elif len(available) == 1:
            chosen, method = available[0], "single_enrolled_course_v1"
    teacher = None
    if chosen:
        teacher = next((item for item in chosen["teachers"] if item["id"] == body.teacher_profile_id), None)
        if body.teacher_profile_id and not teacher:
            raise HTTPException(403, "Choose a currently approved teacher for this course")
        if not teacher and previous:
            prior = next((row.value["triage"] for row in reversed(previous) if row.value["triage"].get("course_id") == chosen["id"]), {})
            teacher = next((item for item in chosen["teachers"] if item["id"] == prior.get("teacher_profile_id")), None)
        if not teacher and len(chosen["teachers"]) == 1:
            teacher = chosen["teachers"][0]
    prompt = None if chosen and teacher else "Which course is this homework for?" if not chosen else "Which approved teacher would you like to help?"
    if not available:
        prompt = "Homework Help needs an enrolled course with an enabled, human-approved teacher and course material. Ask your school administrator."
    return {"method": method, "course_id": chosen["id"] if chosen else None, "course_name": chosen["name"] if chosen else None, "teacher_profile_id": teacher["id"] if teacher else None, "teacher_name": teacher["name"] if teacher else None, "prompt": prompt, "candidates": available if not chosen else [chosen]}


def _json_field(db, key, *, integer=False):
    # SQLite treats CAST(text AS JSON) as a numeric-affinity conversion. Use its
    # native extractor; PostgreSQL needs an actual JSON cast over the text store.
    if db.bind.dialect.name == "sqlite":
        return func.json_extract(Record.data, "$." + key)
    field = cast(Record.data, JSON)[key]
    return field.as_integer() if integer else field.as_string()


def _linked_turn(db, identity, data):
    outcome = _outcome(db, identity, data["homework_message_id"]) if data.get("homework_message_id") else None
    pointer = outcome.value.get("lesson_turn_id") if outcome else None
    if pointer:
        row = db.get(Record, pointer)
        if not row or row.kind != "lesson_turn" or row.tenant_id != identity["tenant_id"] or row.owner_id != identity["id"] or row.parent_id != data["triage"].get("course_id") or row.value.get("idempotency_key") != data.get("lesson_key"):
            raise HTTPException(409, "Homework lesson receipt failed integrity verification")
        return row
    if outcome:
        return None
    # Cold recovery only: the process may have committed a lesson before its
    # homework receipt. Filter the JSON key in the database; never transfer and
    # parse every encrypted/photo-bearing course turn to locate one receipt.
    key = data.get("lesson_key")
    if identity.get("auth_provider") == "academy":
        from .pii_storage import lookup_key
        key = lookup_key(db, identity["tenant_id"], identity["id"], key)
    rows = list(db.scalars(select(Record).where(Record.tenant_id == identity["tenant_id"], Record.kind == "lesson_turn", Record.parent_id == (data["triage"].get("course_id") or ""), Record.owner_id == identity["id"], _json_field(db, "idempotency_key") == key).limit(2)))
    if len(rows) > 1:
        raise HTTPException(409, "Homework lesson receipt is ambiguous")
    return rows[0] if rows else None


def _outcome(db, identity, message_id):
    key = hashlib.sha256(canonical(["homework_outcome", identity["tenant_id"], message_id]).encode()).hexdigest()
    row = db.get(Record, key)
    return row if row and row.kind == "homework_outcome" and row.tenant_id == identity["tenant_id"] and row.owner_id == identity["id"] and row.parent_id == message_id else None


def _references(db, identity, settings, data, turn):
    if not turn or turn.get("status") != "released":
        return []
    course_id = data["triage"]["course_id"]
    course, _ = course_for_student(db, identity, course_id, settings)
    encoded = quote(course_id, safe="")
    result = [{"kind": "course", "id": course_id, "course_id": course_id, "title": course.value["title"], "url": "/#course=" + encoded}]
    # Coursework is selected only from this currently enrolled class. Its title
    # and student instructions already have the same enrollment read boundary.
    terms = _terms(data["message"])
    tasks = db.scalars(select(Record).where(Record.tenant_id == identity["tenant_id"], Record.kind == "assignment", Record.parent_id == course_id))
    matches = []
    for task in tasks:
        if scope_id(db, task) != data["school_id"]:
            continue
        title = task.value.get("title", "")
        score = len(terms & _terms(title)) * 3 + len(terms & _terms(task.value.get("module", "")))
        if score:
            matches.append((score, task.id, title))
    for _, task_id, title in sorted(matches, key=lambda item: (-item[0], item[1]))[:3]:
        result.append({"kind": "assignment", "id": task_id, "course_id": course_id, "title": title, "url": "/#course=" + encoded + "&assignment=" + quote(task_id, safe="")})
    return result


def homework_message_projection(db, identity, row, settings, app):
    data, turn = row.value, None
    triage = data["triage"]
    linked = None if triage["prompt"] else _linked_turn(db, identity, data)
    outcome = _outcome(db, identity, row.id)
    status = "clarify" if triage["prompt"] else outcome.value["status"] if outcome else "pending"
    error = outcome.value["error"] if outcome else None
    resume_available = not triage["prompt"] and not linked and not outcome
    if resume_available:
        error = "Your question was saved, but teaching has not started. Resume this question to send it to the selected teacher."
    if linked:
        turn = curated_turn_dict(linked)
        if turn["status"] == "released":
            try:
                course, school, context, _, _, _ = resolve_lesson_runtime(db, identity, triage["course_id"], triage["teacher_profile_id"], settings)
                if canonical(context) != canonical(linked.value.get("teacher_context_snapshot")):
                    raise HTTPException(409)
                authorize_lesson_teacher(app, triage["teacher_profile_id"], school, triage["course_id"], context)
                turn = protect_released_lesson(db, course, school, turn)
            except HTTPException:
                turn = {**turn, "status": "withheld", "lesson": None, "approved_text": None, "citations": [], "images": [{key: value for key, value in item.items() if key != "url"} for item in turn.get("images", [])], "error": "Current class, teacher or source authorization is required. Ask your teacher for help."}
        status = turn["status"]
    images = [{**item, "url": f"/api/homework-help/sessions/{row.parent_id}/messages/{row.id}/images/{item['id']}"} for item in data["images"]]
    return {"id": row.id, "message": data["message"], "created_at": data["created_at"], "idempotency_key": data["idempotency_key"], "status": status, "error": error, "resume_available": resume_available, "triage": triage, "turn": turn, "images": images, "references": _references(db, identity, settings, data, turn)}


def register_homework_api(app, database, auth, settings, actor, audit_actor, curate_lesson):
    def session(db, identity, session_id):
        row = db.get(Record, session_id)
        if not row or row.kind != "homework_session" or row.tenant_id != identity["tenant_id"] or row.owner_id != identity["id"]:
            raise HTTPException(404, "Homework Help conversation not found")
        require_school(db, identity, row.value["school_id"], settings, {"student"}, human=True)
        return row

    def seal_link(db, identity, row, linked):
        data = row.value
        if _outcome(db, identity, row.id):
            return
        if not linked or linked.kind != "lesson_turn" or linked.tenant_id != identity["tenant_id"] or linked.owner_id != identity["id"] or linked.parent_id != data["triage"]["course_id"] or linked.value.get("idempotency_key") != data["lesson_key"]:
            raise HTTPException(409, "Homework lesson receipt failed integrity verification")
        value = {"status": linked.value["status"], "lesson_turn_id": linked.id, "error": None, "created_at": now()}
        key = hashlib.sha256(canonical(["homework_outcome", identity["tenant_id"], row.id]).encode()).hexdigest()
        outcome = Record(id=key, tenant_id=identity["tenant_id"], kind="homework_outcome", parent_id=row.id, owner_id=identity["id"], data=canonical(value))
        db.add(outcome)
        db.flush()
        bind_scope(db, outcome, data["school_id"])
        audit_actor(db, identity, "homework.lesson_linked", {"homework_session_id": row.parent_id, "homework_message_id": row.id, "school_id": data["school_id"], **value}, row.parent_id)

    @app.get("/api/homework-help")
    def index(school_id: str | None = None, identity=Depends(actor)):
        with database.factory() as db:
            courses = _courses(db, identity, settings, school_id)
            sessions = []
            for row in db.scalars(select(Record).where(Record.tenant_id == identity["tenant_id"], Record.kind == "homework_session", Record.owner_id == identity["id"]).order_by(Record.created_at.desc(), Record.id.desc()).limit(100)):
                if school_id and row.value["school_id"] != school_id:
                    continue
                try:
                    require_school(db, identity, row.value["school_id"], settings, {"student"}, human=True)
                    sessions.append(_session_descriptor(row))
                except HTTPException:
                    continue
            return {"courses": courses, "sessions": sessions}

    @app.post("/api/homework-help/sessions")
    def create(body: HomeworkSessionInput, identity=Depends(actor)):
        with database.write() as db:
            student_principal(db, identity, settings)
            school = body.school_id
            if not school:
                schools = {item["school_id"] for item in _courses(db, identity, settings)}
                if len(schools) != 1:
                    raise HTTPException(409, "Choose your school before starting Homework Help")
                school = schools.pop()
            require_school(db, identity, school, settings, {"student"}, human=True)
            preference_snapshot(db, identity)
            previous = list(db.scalars(select(Record).where(Record.tenant_id == identity["tenant_id"], Record.kind == "homework_session", Record.owner_id == identity["id"])))
            existing = next((row for row in previous if row.value["idempotency_key"] == body.idempotency_key), None)
            if existing:
                if existing.value["school_id"] != school:
                    raise HTTPException(409, "Conversation key was used for a different school")
                return _session_descriptor(existing)
            created = now()
            if sum(row.created_at[:10] == created[:10] for row in previous) >= 30:
                raise HTTPException(429, "Daily Homework Help conversation limit reached")
            row = Record(id=uid(), tenant_id=identity["tenant_id"], kind="homework_session", owner_id=identity["id"], data=canonical({"created_at": created, "idempotency_key": body.idempotency_key, "school_id": school}))
            db.add(row)
            db.flush()
            bind_scope(db, row, school)
            audit_actor(db, identity, "homework.session_created", {"homework_session_id": row.id, "school_id": school}, row.id)
            return _session_descriptor(row)

    @app.get("/api/homework-help/sessions/{session_id}")
    def history(session_id: str, identity=Depends(actor)):
        with database.factory() as db:
            row = session(db, identity, session_id)
            return {**_session_descriptor(row), "messages": [homework_message_projection(db, identity, item, settings, app) for item in _messages(db, identity, session_id)]}

    @app.post("/api/homework-help/sessions/{session_id}/messages")
    def message(session_id: str, body: HomeworkMessageInput, request: Request, identity=Depends(actor)):
        with database.write() as db:
            conversation = session(db, identity, session_id)
            school = conversation.value["school_id"]
            preference_snapshot(db, identity)
            images = _normalized_images(body)
            fingerprint = hashlib.sha256(canonical({**body.model_dump(exclude={"images", "idempotency_key"}), "images": _image_metadata(images)}).encode()).hexdigest()
            previous = _messages(db, identity, session_id)
            row = next((item for item in previous if item.value["idempotency_key"] == body.idempotency_key), None)
            if row and row.value["request_sha256"] != fingerprint:
                raise HTTPException(409, "Message key was used for a different question or attachment")
            if not row:
                if len(previous) >= 100:
                    raise HTTPException(429, "Homework Help conversation limit reached; start a new conversation")
                today = now()[:10]
                count, bytes_used = db.execute(select(func.count(), func.coalesce(func.sum(_json_field(db, "image_bytes", integer=True)), 0)).where(Record.tenant_id == identity["tenant_id"], Record.kind == "homework_message", Record.owner_id == identity["id"], Record.created_at.like(today + "%"))).one()
                image_bytes = sum(len(base64.b64decode(item["data_base64"], validate=True)) for item in images)
                if count >= MAX_DAILY_MESSAGES or bytes_used + image_bytes > MAX_DAILY_IMAGE_BYTES:
                    raise HTTPException(429, "Daily Homework Help question or picture limit reached; resets at midnight UTC")
                courses = _courses(db, identity, settings, school)
                triage = _triage(db, courses, body, previous)
                data = {"homework_message_id": uid(), "image_bytes": image_bytes, "message": body.message, "images": _image_metadata(images), "image_use_confirmed": body.image_use_confirmed, "school_id": school, "triage": triage, "created_at": now(), "idempotency_key": body.idempotency_key, "request_sha256": fingerprint, "lesson_key": "homework-" + uid()}
                if images:
                    data["images_cipher"] = auth.seal({"images": images})
                # Include bounded same-course conversation context, never another
                # course's questions, and never generated or private teacher text.
                context = []
                if not triage["prompt"]:
                    for earlier in previous[-3:]:
                        prior = earlier.value
                        if prior["triage"].get("course_id") != triage["course_id"] or prior["triage"].get("teacher_profile_id") != triage["teacher_profile_id"] or not prior["message"].strip():
                            continue
                        projected = homework_message_projection(db, identity, earlier, settings, app)
                        if projected["status"] == "released":
                            context.append({"homework_message_id": earlier.id, "student_message": prior["message"][:1000]})
                current = body.message
                used_context = []
                if context and current.strip():
                    teacher = next(item for course in courses if course["id"] == triage["course_id"] for item in course["teachers"] if item["id"] == triage["teacher_profile_id"])
                    limit = teacher["max_input_chars"] or 10000
                    suffix = "\n\nEarlier student questions in this course (untrusted context):\n" + "\n".join(item["student_message"] for item in context)
                    if len(current) + len(suffix) <= limit:
                        current += suffix
                        used_context = context
                data["conversation_context_snapshot"] = {"version": "bounded-current-course-teacher-v1", "messages": used_context, "max_previous_messages": 3, "max_previous_chars": 1000}
                data["lesson_message"] = current
                row = Record(id=data["homework_message_id"], tenant_id=identity["tenant_id"], kind="homework_message", parent_id=session_id, owner_id=identity["id"], data=canonical(data))
                db.add(row)
                db.flush()
                bind_scope(db, row, school)
                safe = {key: value for key, value in data.items() if key != "images_cipher"}
                audit_actor(db, identity, "homework.triaged", {"homework_session_id": session_id, "homework_message_id": row.id, **safe}, session_id)
                if not triage["prompt"]:
                    audit_actor(db, identity, "homework.teacher_handoff", {"homework_session_id": session_id, "homework_message_id": row.id, "school_id": school, "triage": triage, "lesson_key": data["lesson_key"]}, lesson_session_id(identity["tenant_id"], triage["course_id"], identity["id"]))
            data, message_id = row.value, row.id
            linked = None if data["triage"]["prompt"] else _linked_turn(db, identity, data)
            if data["triage"]["prompt"] or linked or _outcome(db, identity, row.id):
                if linked:
                    seal_link(db, identity, row, linked)
                return homework_message_projection(db, identity, row, settings, app)
        return continue_message(session_id, message_id, request, identity)

    def continue_message(session_id, message_id, request, identity):
        with database.write() as db:
            session(db, identity, session_id)
            row = db.get(Record, message_id)
            if not row or row.kind != "homework_message" or row.tenant_id != identity["tenant_id"] or row.parent_id != session_id or row.owner_id != identity["id"]:
                raise HTTPException(404, "Homework question not found")
            data = row.value
            linked = None if data["triage"]["prompt"] else _linked_turn(db, identity, data)
            if data["triage"]["prompt"] or linked or _outcome(db, identity, row.id):
                if linked:
                    seal_link(db, identity, row, linked)
                return homework_message_projection(db, identity, row, settings, app)
            school = data["school_id"]
        # Existing curation owns admission, image consent, pending barriers,
        # quota reservations, fresh authorization, send markers and release audit.
        try:
            restored = _image_bytes(auth, data) if data["images"] else []
            curation = CuratedLessonInput(teacher_profile_id=data["triage"]["teacher_profile_id"], message=data["lesson_message"], images=[{"name": metadata["name"], "data_url": "data:image/jpeg;base64," + base64.b64encode(raw).decode("ascii")} for metadata, raw in restored], image_use_confirmed=data["image_use_confirmed"], idempotency_key=data["lesson_key"])
            result_turn = curate_lesson(data["triage"]["course_id"], curation, request, identity)
            with database.write() as db:
                seal_link(db, identity, db.get(Record, message_id), db.get(Record, result_turn["id"]))
        except HTTPException:
            # A pre-admission refusal can occur after our immutable question was
            # saved. A linked lesson, even pending, always owns its own outcome.
            # Without one, no model send marker exists and the refusal is final.
            with database.write() as db:
                row = db.get(Record, message_id)
                if not _linked_turn(db, identity, data) and not _outcome(db, identity, message_id):
                    value = {"status": "blocked", "error": "Homework Help access changed before teaching began. No teacher model was called. Check your course and teacher, then send a new question.", "created_at": now(), "model_call_made": False}
                    key = hashlib.sha256(canonical(["homework_outcome", identity["tenant_id"], message_id]).encode()).hexdigest()
                    outcome = Record(id=key, tenant_id=identity["tenant_id"], kind="homework_outcome", parent_id=message_id, owner_id=identity["id"], data=canonical(value))
                    db.add(outcome)
                    db.flush()
                    bind_scope(db, outcome, school)
                    audit_actor(db, identity, "homework.not_sent", {"homework_session_id": session_id, "homework_message_id": message_id, "school_id": school, **value}, session_id)
        with database.write() as db:
            session(db, identity, session_id)
            row = db.get(Record, message_id)
            result = homework_message_projection(db, identity, row, settings, app)
            audit_actor(db, identity, "homework.response_recorded", {"homework_session_id": session_id, "homework_message_id": message_id, "school_id": school, "course_id": data["triage"]["course_id"], "lesson_turn_id": result["turn"]["id"] if result["turn"] else None, "status": result["status"], "references": result["references"]}, session_id)
            return result

    @app.post("/api/homework-help/sessions/{session_id}/messages/{message_id}/resume")
    def resume(session_id: str, message_id: str, body: HomeworkResumeInput, request: Request, identity=Depends(actor)):
        return continue_message(session_id, message_id, request, identity)

    @app.get("/api/homework-help/review-sessions")
    def review_sessions(school_id: str | None = None, identity=Depends(actor)):
        with database.write() as db:
            allowed = set()
            schools = db.scalars(select(Record).where(Record.tenant_id == identity["tenant_id"], Record.kind == "school"))
            for school in schools:
                if school_id and school.id != school_id:
                    continue
                try:
                    require_school(db, identity, school.id, settings, {"admin", "school"}, human=True)
                    allowed.add(school.id)
                except HTTPException:
                    continue
            if not allowed:
                raise HTTPException(403, "Human School/Admin review permission required")
            result = []
            conversations = db.scalars(select(Record).where(Record.tenant_id == identity["tenant_id"], Record.kind == "homework_session").order_by(Record.created_at.desc(), Record.id.desc()))
            for row in conversations:
                if row.value["school_id"] not in allowed:
                    continue
                student = db.get(Record, row.owner_id)
                messages = _messages(db, {**identity, "id": row.owner_id}, row.id)
                result.append({**_session_descriptor(row), "student_id": row.owner_id, "student_name": student.value.get("name", "Student") if student and student.tenant_id == identity["tenant_id"] and student.kind == "user" else "Student", "message_count": len(messages), "last_activity": messages[-1].created_at if messages else row.created_at})
                if len(result) >= 100:
                    break
            audit_actor(db, identity, "homework.review_catalog_accessed", {"school_id": school_id, "count": len(result)})
            return result

    @app.get("/api/homework-help/sessions/{session_id}/review")
    def review(session_id: str, identity=Depends(actor)):
        with database.write() as db:
            conversation = db.get(Record, session_id)
            if not conversation or conversation.kind != "homework_session" or conversation.tenant_id != identity["tenant_id"]:
                raise HTTPException(404, "Homework Help conversation not found")
            school = conversation.value["school_id"]
            require_school(db, identity, school, settings, {"admin", "school"}, human=True)
            student = {**identity, "id": conversation.owner_id}
            messages = []
            for row in _messages(db, student, session_id):
                value = {key: item for key, item in row.value.items() if key != "images_cipher"}
                value["images"] = [{**item, "url": f"/api/homework-help/sessions/{session_id}/messages/{row.id}/images/{item['id']}"} for item in value["images"]]
                linked = None if row.value["triage"]["prompt"] else _linked_turn(db, student, row.value)
                outcome = _outcome(db, student, row.id)
                messages.append({"id": row.id, **value, "outcome": outcome.value if outcome else None, "turn": {"id": linked.id, **safe_lesson_data(linked.value, linked.parent_id, linked.id)} if linked else None})
            audit_actor(db, identity, "homework.reviewed", {"homework_session_id": session_id, "school_id": school, "student_id": conversation.owner_id}, session_id)
            db.flush()
            events = [audit_dict(row, reveal=True) for row in db.scalars(select(Audit).where(Audit.tenant_id == identity["tenant_id"], Audit.session_id == session_id).order_by(Audit.sequence))]
            return {"session": {**_session_descriptor(conversation), "student_id": conversation.owner_id}, "messages": messages, "events": events, "scope": "session_partial", "complete_tenant_chain": False}

    @app.get("/api/homework-help/sessions/{session_id}/messages/{message_id}/images/{image_id}")
    def image(session_id: str, message_id: str, image_id: str, identity=Depends(actor)):
        with database.write() as db:
            conversation = db.get(Record, session_id)
            if not conversation or conversation.kind != "homework_session" or conversation.tenant_id != identity["tenant_id"]:
                raise HTTPException(404, "Homework picture not found")
            if conversation.owner_id == identity["id"]:
                session(db, identity, session_id)
            else:
                try:
                    require_school(db, identity, conversation.value["school_id"], settings, {"admin", "school"}, human=True)
                except HTTPException:
                    raise HTTPException(404, "Homework picture not found") from None
            row = db.get(Record, message_id)
            if not row or row.kind != "homework_message" or row.tenant_id != identity["tenant_id"] or row.owner_id != conversation.owner_id or row.parent_id != session_id:
                raise HTTPException(404, "Homework picture not found")
            found = next(((metadata, raw) for metadata, raw in _image_bytes(auth, row.value) if metadata["id"] == image_id), None)
            if not found:
                raise HTTPException(404, "Homework picture not found")
            audit_actor(db, identity, "homework.image_viewed", {"homework_session_id": session_id, "homework_message_id": message_id, "student_id": conversation.owner_id, "image": found[0]}, session_id)
            return Response(found[1], media_type="image/jpeg", headers={"Cache-Control": "private, no-store", "X-Content-Type-Options": "nosniff"})
