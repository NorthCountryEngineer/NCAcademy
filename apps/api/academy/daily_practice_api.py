"""Human-published, versioned Grade 3 practice and private student results."""
import hashlib
import time
from datetime import date, datetime
from secrets import token_hex
from typing import Annotated
from zoneinfo import ZoneInfo

from fastapi import Depends, HTTPException, Path, Query
from pydantic import BaseModel, ConfigDict, Field, StringConstraints, model_validator
from sqlalchemy import func, select

from .db import Login, Record, canonical, daily_practice_schema_id, now
from .family_accounts import ACCOUNT, local_user
from .grade3_practice_content import (
    BANK_VERSION,
    PRACTICE_LESSON_IDS,
    generate_practice,
    grade3_practice_course,
    grade_practice_item,
    practice_topics,
    public_practice_item,
)
from .permissions import bind_scope, membership_id, require_school, scope_id
from .standards import corpus_descriptor

ASSIGNMENT = "daily_practice_assignment"
PUBLICATION = "daily_practice_publication"
RESULT = "daily_practice_result"
CIPHER = "daily_practice_cipher"
ROOT = "/api/courses/{course_id}/daily-practice"
EASTERN = ZoneInfo("America/New_York")

Opaque = Annotated[str, StringConstraints(strict=True, min_length=1, max_length=64, pattern=r"^[a-zA-Z0-9_:-]+$")]
RequestID = Annotated[str, StringConstraints(strict=True, min_length=8, max_length=64, pattern=r"^[a-zA-Z0-9_-]+$")]
ISODate = Annotated[str, StringConstraints(strict=True, pattern=r"^[0-9]{4}-[0-9]{2}-[0-9]{2}$", min_length=10, max_length=10)]
Answer = Annotated[str, StringConstraints(strict=True, strip_whitespace=True, min_length=1, max_length=120)]
ResourcePath = Annotated[str, Path(min_length=1, max_length=64)]


class ExactBody(BaseModel):
    model_config = ConfigDict(extra="forbid")


class PracticeDraft(ExactBody):
    request_id: RequestID
    student_id: Opaque
    date: ISODate
    topic_id: Opaque | None = None
    count: int | None = Field(default=None, ge=1, le=20, strict=True)
    source_assignment_id: Opaque | None = None

    @model_validator(mode="after")
    def exact_mode(self):
        if bool(self.topic_id) == bool(self.source_assignment_id) or self.source_assignment_id and self.count is not None:
            raise ValueError("Choose a new topic and question count, or one saved assignment")
        try:
            date.fromisoformat(self.date)
        except ValueError as error:
            raise ValueError("Choose a valid assignment date") from error
        return self


class PublishPractice(ExactBody):
    pass


class PracticeSubmit(ExactBody):
    answers: dict[Opaque, Answer] = Field(min_length=1, max_length=20)


def _today():
    return datetime.now(EASTERN).date().isoformat()


def _current_date(day, today):
    if not max("2026-07-01", today) <= day <= "2027-06-30":
        raise HTTPException(422, "Choose today or a later date in the 2026–2027 school year")


def _digest(value):
    return hashlib.sha256(canonical(value).encode()).hexdigest()


def _key(prefix, *context):
    return prefix + _digest(context)[:48]


def _purpose(row):
    # Include every structural scope in the authenticated encryption context.
    return "daily-practice:" + _digest([row.kind, row.parent_id, row.owner_id])


def _value(auth, row):
    try:
        cipher = row.value[CIPHER]
    except KeyError as error:
        raise HTTPException(503, "Protected practice record is unavailable") from error
    return auth.family.decrypt(cipher, row.tenant_id, row.id, _purpose(row))


def _save(db, auth, course, school_id, student_id, key, kind, data):
    row = Record(id=key, tenant_id=course.tenant_id, parent_id=course.id, owner_id=student_id, kind=kind)
    row.data = canonical({CIPHER: auth.family.encrypt(data, row.tenant_id, row.id, _purpose(row))})
    db.add(row)
    db.flush()
    bind_scope(db, row, school_id)
    return row


def _records(course, kind):
    return select(Record).where(Record.tenant_id == course.tenant_id, Record.kind == kind, Record.parent_id == course.id)


def _scoped(db, course, school_id, key, kind, owner=None, *, optional=False):
    row = db.get(Record, key)
    if row is None and optional:
        return None
    if not row or row.tenant_id != course.tenant_id or row.kind != kind or row.parent_id != course.id or (owner is not None and row.owner_id != owner) or scope_id(db, row) != school_id:
        raise HTTPException(404, "Practice assignment not found")
    return row


def _linked(db, auth, course, school_id, row, kind):
    prefix = "practice-pub:" if kind == PUBLICATION else "practice-result:"
    linked = _scoped(db, course, school_id, _key(prefix, course.tenant_id, course.id, row.id), kind, row.owner_id, optional=True)
    if linked is None:
        return None
    data = _value(auth, linked)
    if data.get("assignment_id") != row.id:
        raise HTTPException(409, "Practice history integrity check failed")
    return data


def _eligible(db, course, school_id, student_id, enrollment=None):
    user = local_user(db, course.tenant_id, student_id)
    if not user or user.value.get("role") != "student" or user.value.get("principal_kind", "human") != "human" or user.value.get("disabled") or user.value.get("family_status", "active") != "active":
        return False
    account = db.get(Record, "local-account:" + student_id)
    if not account or account.kind != ACCOUNT or account.tenant_id != course.tenant_id or account.owner_id != student_id:
        return False
    member = db.get(Record, membership_id(school_id, student_id))
    if not member or member.kind != "school_membership" or member.tenant_id != course.tenant_id or member.parent_id != school_id or member.owner_id != student_id or member.value.get("active") is not True or member.value.get("role") != "student":
        return False
    if enrollment is None:
        enrollments = list(db.scalars(_records(course, "enrollment").where(Record.owner_id == student_id).limit(2)))
        if len(enrollments) != 1:
            return False
        enrollment = enrollments[0]
    return (enrollment.kind == "enrollment" and enrollment.tenant_id == course.tenant_id and
            enrollment.parent_id == course.id and enrollment.owner_id == student_id and
            enrollment.value.get("role") == "student" and scope_id(db, enrollment) == school_id)


def practice_lesson_documents(descriptor):
    """Only the current approved, admitted, packaged Grade 3 teaching references."""
    if not descriptor or not descriptor.get("ready_for_teaching"):
        return {}
    return {
        item["id"]: item for item in descriptor["documents"]
        if item.get("kind") == "source_reference" and item.get("audience") == "all"
        and item.get("content_role") == "original_local_teaching_summary"
        and item.get("teaching_pack_version") == descriptor.get("teaching_pack_version")
        and item["id"] in PRACTICE_LESSON_IDS
        and item.get("content_sha256") and item.get("content", "").strip()
    }


def approved_practice_corpus(db, course, school_id):
    if not grade3_practice_course(course):
        return None
    descriptor = corpus_descriptor(db, course, school_id)
    if descriptor.get("coverage") != "complete_standards" or not practice_lesson_documents(descriptor):
        return None
    return descriptor


def _require_corpus(descriptor):
    if descriptor is None:
        raise HTTPException(409, "A current human-approved Grade 3 mathematics corpus is required")
    return descriptor


def _binding(descriptor):
    return {"id": descriptor["id"], "snapshot_sha256": descriptor["snapshot_sha256"]}


def _lesson_bindings(items, descriptor):
    documents = practice_lesson_documents(descriptor)
    bindings = {}
    for item in items:
        lesson_id = item["lesson_id"]
        lesson = documents.get(lesson_id)
        if not lesson or not set(item["standards"]) <= set(lesson["standard_codes"]):
            raise HTTPException(409, "A linked lesson is not admitted for this assignment; review the current corpus")
        bindings[lesson_id] = lesson["content_sha256"]
    return bindings


def _current_draft(value, descriptor):
    if descriptor is None or value["corpus"] != _binding(descriptor):
        return False
    try:
        return value["lesson_bindings"] == _lesson_bindings(value["items"], descriptor)
    except HTTPException:
        return False


def _summary(row, value, publication, result, can_manage):
    status = "submitted" if result else "published" if publication else "draft"
    projection = {"id": row.id, "topic_id": value["topic_id"], "title": value["title"],
                  "date": value["date"], "item_count": len(value["items"]), "status": status}
    if can_manage:
        projection.update(student_id=row.owner_id, source_assignment_id=value["source_assignment_id"])
    if result:
        projection["result"] = {"correct": result["correct"], "total": result["total"]}
    return projection


def _detail(row, value, publication, result, descriptor, can_manage, today, *, target_eligible=False):
    data = _summary(row, value, publication, result, can_manage)
    exact = _current_draft(value, descriptor)
    data["items"] = [public_practice_item(item) for item in value["items"]]
    data["can_submit"] = bool(not can_manage and publication and not result and exact and value["date"] <= today)
    data["can_publish"] = bool(can_manage and not publication and exact and today <= value["date"] and target_eligible)
    if result:
        documents = practice_lesson_documents(descriptor) if exact else {}
        lessons = []
        for lesson_id in result["lesson_ids"]:
            lesson = documents.get(lesson_id)
            if lesson:
                lessons.append({"id": lesson_id, "title": lesson["title"], "text": lesson["content"],
                                "standards": lesson["standard_codes"], "source_url": lesson["source_url"]})
        data["result"].update(items=[{key: item[key] for key in ("id", "correct", "answer", "lesson_id")} for item in result["items"]],
                              lessons=lessons, lessons_withheld=len(lessons) != len(result["lesson_ids"]))
    return data


def register_daily_practice_api(app, database, auth, settings, actor, membership, audit_actor):
    def access(db, identity, course_id, staff=False):
        if identity.get("principal_kind", "human") != "human":
            raise HTTPException(403, "Human class access required")
        # The actor has already checked the live credential and CSRF. Recheck
        # its local binding under the same write lock as any subsequent release.
        login = db.get(Login, identity["login_id"]) if identity.get("login_id") else None
        user = db.get(Record, identity["id"])
        if (not login or login.tenant_id != identity["tenant_id"] or login.user_id != identity["id"]
                or login.csrf != identity.get("csrf_token") or login.expires <= time.time()
                or not user or user.kind != "user" or user.tenant_id != identity["tenant_id"]
                or user.value.get("disabled") or user.value.get("must_change_password")):
            raise HTTPException(401, "Session changed; sign in again")
        course = membership(db, course_id, identity, staff)
        school_id = scope_id(db, course)
        role = require_school(db, identity, school_id, settings, {"admin", "school", "teacher", "student"}, human=True)
        school = db.get(Record, school_id)
        if school.value.get("disabled"):
            raise HTTPException(403, "An active school is required")
        if role == "student" and (identity.get("auth_provider") != "academy" or not _eligible(db, course, school_id, identity["id"])):
            raise HTTPException(403, "Current local student class enrollment required")
        if not grade3_practice_course(course):
            raise HTTPException(404, "Daily practice is available for the reviewed New York Grade 3 mathematics class")
        marker = db.get(Record, daily_practice_schema_id(course.tenant_id))
        if not marker or marker.tenant_id != course.tenant_id or marker.kind != "schema" or marker.value.get("version") != 17:
            raise HTTPException(503, "Daily practice storage is not ready")
        return course, school_id, role != "student"

    def evidence(db, identity, event, course, school_id, row=None, **details):
        scope = {"course_id": course.id, "school_id": school_id}
        if row:
            scope.update(assignment_id=row.id, student_id=row.owner_id)
        audit_actor(db, identity, "daily_practice." + event, {**scope, **details})

    def state(db, identity, course, school_id, assignment_id, can_manage, today):
        row = _scoped(db, course, school_id, assignment_id, ASSIGNMENT, None if can_manage else identity["id"])
        publication = _linked(db, auth, course, school_id, row, PUBLICATION)
        if not can_manage and (not publication or publication["date"] > today):
            raise HTTPException(404, "Practice assignment not found")
        value = _value(auth, row)
        if publication and (publication["date"] != value["date"] or publication["assignment_sha256"] != _digest(value)):
            raise HTTPException(409, "Practice publication integrity check failed")
        result = _linked(db, auth, course, school_id, row, RESULT)
        if result and (not publication or result["assignment_sha256"] != publication["assignment_sha256"]):
            raise HTTPException(409, "Practice result integrity check failed")
        return row, value, publication, result

    @app.get(ROOT)
    def practice_workspace(course_id: ResourcePath, offset: Annotated[int, Query(ge=0, le=100000)] = 0,
                           limit: Annotated[int, Query(ge=1, le=50)] = 30, identity=Depends(actor)):
        with database.write() as db:
            course, school_id, can_manage = access(db, identity, course_id)
            today = _today()
            descriptor = approved_practice_corpus(db, course, school_id)
            eligible = []
            assignments = []
            today_assignments = []
            if can_manage:
                query = _records(course, ASSIGNMENT)
                total = db.scalar(select(func.count()).select_from(query.subquery()))
                for row in db.scalars(query.order_by(Record.created_at.desc(), Record.id.desc()).offset(offset).limit(limit)):
                    assignments.append(_summary(*state(db, identity, course, school_id, row.id, True, today), True))
                eligible = sorted({member.owner_id for member in db.scalars(_records(course, "enrollment"))
                                   if _eligible(db, course, school_id, member.owner_id, member)})
            else:
                # Never decrypt another student's records. Availability dates
                # stay encrypted; stream only this learner's publication rows.
                total = 0
                query = _records(course, PUBLICATION).where(Record.owner_id == identity["id"]).order_by(Record.created_at.desc(), Record.id.desc())
                for publication_row in db.scalars(query.execution_options(yield_per=50)):
                    if scope_id(db, publication_row) != school_id:
                        raise HTTPException(409, "Practice history scope integrity check failed")
                    publication = _value(auth, publication_row)
                    if publication["date"] > today:
                        continue
                    on_page = offset <= total < offset + limit
                    on_today = publication["date"] == today and len(today_assignments) < 50
                    if on_page or on_today:
                        summary = _summary(*state(db, identity, course, school_id, publication["assignment_id"], False, today), False)
                        if on_page:
                            assignments.append(summary)
                        if on_today:
                            today_assignments.append(summary)
                    total += 1
            evidence(db, identity, "history_viewed", course, school_id, count=len(assignments))
            return {"today": today, "can_manage": can_manage, "ready": descriptor is not None,
                    "topics": practice_topics() if can_manage else [], "eligible_student_ids": eligible,
                    "assignments": assignments, "today_assignments": today_assignments, "total": total,
                    "limit": limit, "offset": offset}

    @app.post(ROOT + "/drafts", status_code=201)
    def draft_practice(course_id: ResourcePath, body: PracticeDraft, identity=Depends(actor)):
        with database.write() as db:
            course, school_id, _ = access(db, identity, course_id, staff=True)
            today = _today()
            if not _eligible(db, course, school_id, body.student_id):
                raise HTTPException(422, "Choose an active local student currently enrolled in this class")
            key = _key("practice:", course.tenant_id, course.id, identity["id"], body.request_id)
            descriptor = approved_practice_corpus(db, course, school_id)
            request_sha = _digest(body.model_dump(exclude={"request_id"}))
            if db.get(Record, key):
                row, value, publication, result = state(db, identity, course, school_id, key, True, today)
                if row.owner_id != body.student_id or value["request_sha256"] != request_sha:
                    raise HTTPException(409, "This draft request has already been used with different inputs")
                return _detail(row, value, publication, result, descriptor, True, today, target_eligible=True)
            _current_date(body.date, today)
            descriptor = _require_corpus(descriptor)
            seed = token_hex(32)
            if body.source_assignment_id:
                _, source, publication, _ = state(db, identity, course, school_id, body.source_assignment_id, True, today)
                if not publication:
                    raise HTTPException(409, "Publish an assignment before reusing its saved questions")
                items, title, topic_id, bank_version, seed = (source[field] for field in ("items", "title", "topic_id", "bank_version", "seed"))
            else:
                topic = next((item for item in practice_topics() if item["id"] == body.topic_id), None)
                if not topic:
                    raise HTTPException(422, "Choose a reviewed Grade 3 mathematics topic")
                topic_id, title, bank_version = topic["id"], topic["title"], BANK_VERSION
                try:
                    items = generate_practice(topic_id, seed, body.count or 8)
                except ValueError:
                    raise HTTPException(409, "Could not prepare a distinct set of questions; try a shorter assignment") from None
            value = {"topic_id": topic_id, "title": title, "date": body.date, "items": items,
                     "bank_version": bank_version, "seed": seed, "source_assignment_id": body.source_assignment_id,
                     "request_sha256": request_sha, "created_by": identity["id"], "created_at": now(),
                     "corpus": _binding(descriptor), "lesson_bindings": _lesson_bindings(items, descriptor)}
            row = _save(db, auth, course, school_id, body.student_id, key, ASSIGNMENT, value)
            evidence(db, identity, "draft_created", course, school_id, row, assignment_sha256=_digest(value),
                     source_assignment_id=body.source_assignment_id, item_count=len(items), corpus=value["corpus"])
            return _detail(row, value, None, None, descriptor, True, today, target_eligible=True)

    @app.get(ROOT + "/{assignment_id}")
    def read_practice(course_id: ResourcePath, assignment_id: ResourcePath, identity=Depends(actor)):
        with database.write() as db:
            course, school_id, can_manage = access(db, identity, course_id)
            today = _today()
            row, value, publication, result = state(db, identity, course, school_id, assignment_id, can_manage, today)
            descriptor = approved_practice_corpus(db, course, school_id)
            detail = _detail(row, value, publication, result, descriptor, can_manage, today,
                             target_eligible=can_manage and _eligible(db, course, school_id, row.owner_id))
            evidence(db, identity, "assignment_viewed", course, school_id, row, status=detail["status"],
                     lesson_ids=[lesson["id"] for lesson in detail.get("result", {}).get("lessons", [])])
            return detail

    @app.post(ROOT + "/{assignment_id}/publish")
    def publish_practice(course_id: ResourcePath, assignment_id: ResourcePath, body: PublishPractice, identity=Depends(actor)):
        with database.write() as db:
            course, school_id, _ = access(db, identity, course_id, staff=True)
            today = _today()
            row, value, publication, result = state(db, identity, course, school_id, assignment_id, True, today)
            descriptor = approved_practice_corpus(db, course, school_id)
            if publication:
                return _detail(row, value, publication, result, descriptor, True, today)
            if not _eligible(db, course, school_id, row.owner_id):
                raise HTTPException(409, "The student no longer has an active class enrollment")
            _current_date(value["date"], today)
            if not _current_draft(value, descriptor):
                raise HTTPException(409, "The approved corpus changed; review a new draft before publishing")
            key = _key("practice-pub:", course.tenant_id, course.id, row.id)
            publication = {"assignment_id": row.id, "assignment_sha256": _digest(value), "date": value["date"],
                           "published_by": identity["id"], "published_at": now()}
            _save(db, auth, course, school_id, row.owner_id, key, PUBLICATION, publication)
            evidence(db, identity, "published", course, school_id, row, assignment_sha256=publication["assignment_sha256"],
                     publication_id=key, corpus=value["corpus"])
            return _detail(row, value, publication, None, descriptor, True, today)

    @app.post(ROOT + "/{assignment_id}/submit")
    def submit_practice(course_id: ResourcePath, assignment_id: ResourcePath, body: PracticeSubmit, identity=Depends(actor)):
        with database.write() as db:
            course, school_id, can_manage = access(db, identity, course_id)
            if can_manage:
                raise HTTPException(403, "Only the assigned student can submit their work")
            today = _today()
            row, value, publication, result = state(db, identity, course, school_id, assignment_id, False, today)
            descriptor = approved_practice_corpus(db, course, school_id)
            if result:
                if {item["id"]: item["answer"] for item in result["items"]} != body.answers:
                    raise HTTPException(409, "This assignment was already submitted")
                return _detail(row, value, publication, result, descriptor, False, today)
            if not _current_draft(value, descriptor):
                raise HTTPException(409, "The linked lessons changed. Ask your teacher to review and reassign this practice")
            if set(body.answers) != {item["id"] for item in value["items"]}:
                raise HTTPException(422, "Answer each question in this exact assignment")
            graded = []
            for item in value["items"]:
                answer = body.answers[item["id"]]
                if item["answer_type"] == "choice" and answer not in {option["id"] for option in item["options"]}:
                    raise HTTPException(422, "Select one of the shown choices")
                graded.append({"id": item["id"], "answer": answer, "correct": grade_practice_item(item, answer), "lesson_id": item["lesson_id"]})
            lesson_ids = list(dict.fromkeys(item["lesson_id"] for item in graded if not item["correct"]))
            key = _key("practice-result:", course.tenant_id, course.id, row.id)
            result = {"assignment_id": row.id, "assignment_sha256": publication["assignment_sha256"],
                      "submitted_at": now(), "correct": sum(item["correct"] for item in graded), "total": len(graded),
                      "items": graded, "lesson_ids": lesson_ids}
            _save(db, auth, course, school_id, row.owner_id, key, RESULT, result)
            evidence(db, identity, "submitted", course, school_id, row, result_id=key,
                     assignment_sha256=result["assignment_sha256"], result_sha256=_digest(result),
                     correct=result["correct"], total=result["total"],
                     lesson_ids=lesson_ids, corpus=value["corpus"])
            return _detail(row, value, publication, result, descriptor, False, today)
