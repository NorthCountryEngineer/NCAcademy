"""Student-owned, bounded presentation preferences; never teaching authority."""
import hashlib

from fastapi import Depends, HTTPException
from pydantic import BaseModel, ConfigDict, Field, StrictInt, model_validator
from sqlalchemy import func, select

from .db import Record, canonical, now, uid
from .permissions import bind_scope, school_role, scope_id


DIMENSIONS = ("reading", "diagrams", "video", "audio", "pace", "detail", "encouragement", "question_frequency", "structure")
QUESTIONS = [
    {"id": "reading", "prompt": "How much do you like learning by reading?", "low_label": "Less reading", "high_label": "More reading"},
    {"id": "diagrams", "prompt": "How much do diagrams and illustrations help you?", "low_label": "Fewer diagrams", "high_label": "More diagrams"},
    {"id": "video", "prompt": "Would you like reviewed videos when they are available?", "low_label": "Less video", "high_label": "More video"},
    {"id": "audio", "prompt": "Would you like the option to listen to lesson text?", "low_label": "Less audio", "high_label": "More audio"},
    {"id": "pace", "prompt": "What pace feels comfortable?", "low_label": "Small steps, more pauses", "high_label": "A quicker overview"},
    {"id": "detail", "prompt": "How much explanation do you prefer?", "low_label": "Keep it concise", "high_label": "More detail"},
    {"id": "encouragement", "prompt": "How much encouragement would you like?", "low_label": "Keep it straightforward", "high_label": "More encouragement"},
    {"id": "question_frequency", "prompt": "How often should your teacher ask reflection questions?", "low_label": "Fewer questions", "high_label": "More questions"},
    {"id": "structure", "prompt": "How much structure helps you follow a lesson?", "low_label": "A flexible overview", "high_label": "Clear numbered steps"},
]
ALGORITHM = "bounded-per-student-per-teacher-v1"
ADAPTATION_WINDOW = 128
NEUTRAL = {key: 2 for key in DIMENSIONS}


class PreferenceInput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    answers: dict[str, StrictInt]
    expected_version: int = Field(ge=0, strict=True)

    @model_validator(mode="after")
    def bounded(self):
        validate_dimensions(self.answers, complete=True)
        return self


class ResetInput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    expected_version: int = Field(ge=0, strict=True)


class StudentFeedbackInput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    turn_id: str = Field(min_length=1, max_length=64)
    accuracy: StrictInt | None = Field(ge=1, le=5)
    helpfulness: int = Field(ge=1, le=5, strict=True)
    style_preferences: dict[str, StrictInt] = Field(default_factory=dict)

    @model_validator(mode="after")
    def bounded(self):
        validate_dimensions(self.style_preferences)
        return self


def validate_dimensions(values, complete=False):
    if set(values) - set(DIMENSIONS) or complete and set(values) != set(DIMENSIONS) or any(type(item) is not int or not 0 <= item <= 4 for item in values.values()):
        raise ValueError("Use only the nine learning preferences, with whole-number choices from 0 to 4")


def student_principal(db, identity, settings):
    if identity.get("principal_kind", "human") != "human":
        raise HTTPException(403, "Human student permission required")
    if identity.get("role") == "student":
        return
    schools = db.scalars(select(Record).where(Record.tenant_id == identity["tenant_id"], Record.kind == "school"))
    if not any(school_role(db, identity, school.id, settings) == "student" for school in schools):
        raise HTTPException(403, "Student permission required")


def latest_preferences(db, identity):
    rows = list(db.scalars(select(Record).where(Record.tenant_id == identity["tenant_id"], Record.kind == "student_preference_revision", Record.owner_id == identity["id"])))
    return max(rows, key=lambda row: row.value["version"], default=None)


def preference_status(db, identity):
    row = latest_preferences(db, identity)
    return {"status": "complete" if row and row.value["complete"] else "required", "version": row.value["version"] if row else 0}


def feedback_scope(teacher_profile_id=None, course_id=None):
    return "teacher:" + teacher_profile_id if teacher_profile_id else "course:" + (course_id or "unassigned")


def feedback_partition(identity, target_scope, revision_id):
    return "feedback-scope:" + hashlib.sha256(canonical([identity["tenant_id"], identity["id"], target_scope, revision_id]).encode()).hexdigest()[:48]


def preference_snapshot(db, identity, teacher_profile_id=None, course_id=None, *, required=True, pending=None):
    revision = latest_preferences(db, identity)
    if required and (not revision or not revision.value["complete"]):
        raise HTTPException(409, "Complete your learning preferences before starting a new tutoring session")
    base = {key: value * 25 for key, value in (revision.value["answers"] if revision and revision.value["complete"] else NEUTRAL).items()}
    target_scope = feedback_scope(teacher_profile_id, course_id)
    partition = feedback_partition(identity, target_scope, revision.id if revision else None)
    conditions = (Record.tenant_id == identity["tenant_id"], Record.kind == "student_agent_feedback", Record.owner_id == identity["id"], Record.parent_id == partition)
    admitted = list(db.scalars(select(Record).where(*conditions).order_by(Record.created_at.desc(), Record.id.desc()).limit(ADAPTATION_WINDOW)))
    total = db.scalar(select(func.count()).select_from(Record).where(*conditions))
    if pending is not None:
        admitted = sorted([pending, *admitted], key=lambda row: (row.created_at, row.id), reverse=True)[:ADAPTATION_WINDOW]
        total += 1
    weights = dict(base)
    # Recompute from the immutable baseline and all explicit targets. Repeated
    # ratings cannot compound or change truth, permissions, safety or teacher policy.
    for key in DIMENSIONS:
        targets = [row.value["style_preferences"][key] * 25 for row in admitted if key in row.value["style_preferences"]]
        if targets:
            delta = max(-20, min(20, sum(targets) / len(targets) - base[key]))
            weights[key] = max(0, min(100, round(base[key] + delta * len(targets) / (len(targets) + 4))))
    material = {"algorithm": ALGORITHM, "revision_id": revision.id if revision else None, "version": revision.value["version"] if revision else 0, "scope": target_scope, "base_weights": base, "effective_weights": weights, "feedback_ids": sorted(row.id for row in admitted), "feedback_count": total, "adaptation_window": ADAPTATION_WINDOW}
    return {**material, "sha256": hashlib.sha256(canonical(material).encode()).hexdigest()}


def require_preference_snapshot(db, identity, snapshot, teacher_profile_id=None, course_id=None):
    if canonical(preference_snapshot(db, identity, teacher_profile_id, course_id)) != canonical(snapshot):
        raise HTTPException(409, "Learning preferences changed during this interaction")


def presentation_context(context, snapshot):
    """Map numeric targets to the existing fixed, admitted teaching styles."""
    if not snapshot:
        return context
    weights = snapshot["effective_weights"]
    behavior = dict(context.get("ai_behavior") or {})
    for dimension, field, low, high in (("encouragement", "tone", "formal", "warm"), ("detail", "detail", "concise", "detailed"), ("question_frequency", "question_frequency", "low", "high")):
        if weights[dimension] >= 65:
            behavior[field] = high
        elif weights[dimension] <= 35:
            behavior[field] = low
    return {**context, "ai_behavior": behavior, "student_learning_weights": weights}


def preference_document(db, identity):
    revision = latest_preferences(db, identity)
    # A GET can overlap another session's save under READ COMMITTED. Derive the
    # optimistic version and every editable value from this one immutable row.
    state = {"status": "complete" if revision and revision.value["complete"] else "required", "version": revision.value["version"] if revision else 0}
    answers = revision.value["answers"] if revision and state["status"] == "complete" else NEUTRAL
    return {**state, "revision_id": revision.id if revision else None, "answers": answers, "weights": {key: value * 25 for key, value in answers.items()}, "feedback_count": db.scalar(select(func.count()).select_from(Record).where(Record.tenant_id == identity["tenant_id"], Record.kind == "student_agent_feedback", Record.owner_id == identity["id"])), "questions": QUESTIONS, "adaptation": ALGORITHM}


def register_learning_preferences_api(app, database, auth, settings, actor, audit_actor):
    @app.get("/api/me/learning-preferences")
    def get_preferences(identity=Depends(actor)):
        with database.factory() as db:
            student_principal(db, identity, settings)
            return preference_document(db, identity)

    def save(body, identity, complete):
        with database.write() as db:
            student_principal(db, identity, settings)
            state = preference_status(db, identity)
            if state["version"] != body.expected_version:
                raise HTTPException(409, "Learning preferences changed; reload before saving")
            data = {"version": state["version"] + 1, "complete": complete, "answers": body.answers if complete else NEUTRAL, "created_at": now(), "algorithm": ALGORITHM}
            revision = Record(id=uid(), tenant_id=identity["tenant_id"], kind="student_preference_revision", owner_id=identity["id"], data=canonical(data))
            db.add(revision)
            db.flush()
            audit_actor(db, identity, "student.preferences_saved" if complete else "student.preferences_reset", {"revision_id": revision.id, **data})
            return preference_document(db, identity)

    @app.put("/api/me/learning-preferences")
    @app.patch("/api/me/learning-preferences")
    def put_preferences(body: PreferenceInput, identity=Depends(actor)):
        return save(body, identity, True)

    @app.post("/api/me/learning-preferences/reset")
    def reset_preferences(body: ResetInput, identity=Depends(actor)):
        return save(body, identity, False)

    def authorized_turn(db, identity, turn_id):
        # Imports are local to avoid a module cycle with interaction snapshots.
        from .lesson_api import course_for_student, protect_released_lesson, lesson_turn_dict, authorize_lesson_teacher
        from .teacher_api import resolve_lesson_context, resolve_teacher_context
        student_principal(db, identity, settings)
        turn = db.get(Record, turn_id)
        if not turn or turn.tenant_id != identity["tenant_id"] or turn.owner_id != identity["id"] or turn.kind not in {"turn", "lesson_turn", "homework_ai_message"}:
            raise HTTPException(404, "Student interaction not found")
        if turn.kind == "homework_ai_message":
            from .homework_ai import feedback_context
            context = feedback_context(db, identity, turn, settings, app=app)
            return turn, context["course_id"], context["school_id"]
        task = db.get(Record, turn.parent_id) if turn.kind == "turn" else None
        if turn.kind == "turn" and (not task or task.tenant_id != identity["tenant_id"] or task.kind != "assignment"):
            raise HTTPException(404, "Assignment not found")
        course_id = task.parent_id if task else turn.parent_id
        course, school_id = course_for_student(db, identity, course_id, settings)
        if scope_id(db, turn) != school_id or task and scope_id(db, task) != school_id:
            raise HTTPException(403, "Current class scope required")
        value = turn.value
        if value.get("status") != "released":
            raise HTTPException(409, "Feedback requires a released interaction")
        if task and task.value.get("version", 1) != value.get("policy_snapshot", {}).get("assignment_version"):
            raise HTTPException(409, "Assignment changed; feedback is unavailable for this interaction")
        if turn.kind == "lesson_turn" and protect_released_lesson(db, course, school_id, lesson_turn_dict(turn))["status"] != "released":
            raise HTTPException(409, "Current teaching source review required")
        profile_id = value.get("teacher_profile_id")
        if profile_id:
            context = (resolve_lesson_context if turn.kind == "lesson_turn" else resolve_teacher_context)(db, identity, course, school_id, settings, profile_id)
            old = {key: item for key, item in value["teacher_context_snapshot"].items() if key != "retrieval"}
            if canonical(context) != canonical(old):
                raise HTTPException(409, "Teacher approval or corpus changed; feedback is unavailable")
            if context.get("teacher_mode") == "core_agent" or turn.kind == "turn":
                if not settings.allow_agent_teachers:
                    raise HTTPException(403, "Core teacher admission is disabled")
                authorize_lesson_teacher(app, profile_id, school_id, course_id, {**context, "teacher_mode": "core_agent"})
        return turn, course_id, school_id

    def feedback_id(identity, turn_id):
        return "feedback:" + hashlib.sha256((identity["tenant_id"] + ":" + identity["id"] + ":" + turn_id).encode()).hexdigest()[:48]

    def feedback_document(row):
        return {"id": row.id, **{key: item for key, item in row.value.items() if key not in {"scope", "preference_revision_id", "teacher_profile_id", "course_id", "school_id", "learning_snapshot"}}}

    @app.get("/api/me/feedback")
    def get_feedback(turn_id: str, identity=Depends(actor)):
        if not 1 <= len(turn_id) <= 64:
            raise HTTPException(422, "Invalid interaction ID")
        with database.factory() as db:
            authorized_turn(db, identity, turn_id)
            row = db.get(Record, feedback_id(identity, turn_id))
            return feedback_document(row) if row else None

    @app.post("/api/me/feedback")
    def give_feedback(body: StudentFeedbackInput, identity=Depends(actor)):
        with database.write() as db:
            turn, course_id, school_id = authorized_turn(db, identity, body.turn_id)
            supplied = body.model_dump()
            key = feedback_id(identity, turn.id)
            previous = db.get(Record, key)
            if previous:
                if any(previous.value.get(name) != item for name, item in supplied.items()):
                    raise HTTPException(409, "Feedback was already recorded for this interaction")
                return feedback_document(previous)
            profile_id = turn.value.get("teacher_profile_id")
            snapshot = preference_snapshot(db, identity, profile_id, course_id)
            if turn.value.get("learning_preferences_snapshot", {}).get("revision_id") != snapshot["revision_id"]:
                raise HTTPException(409, "Learning preferences were revised; give feedback on a new interaction")
            data = {**supplied, "accuracy_status": "not_assessed" if body.accuracy is None else "student_rating", "scope": feedback_scope(profile_id, course_id), "teacher_profile_id": profile_id, "course_id": course_id, "school_id": school_id, "preference_revision_id": snapshot["revision_id"], "learning_snapshot": snapshot, "review_required": body.accuracy is not None and body.accuracy <= 2, "created_at": now()}
            row = Record(id=key, tenant_id=identity["tenant_id"], kind="student_agent_feedback", parent_id=feedback_partition(identity, data["scope"], snapshot["revision_id"]), owner_id=identity["id"], created_at=data["created_at"], data=canonical(data))
            updated = preference_snapshot(db, identity, profile_id, course_id, pending=row)
            data["effective_weights"] = updated["effective_weights"]
            row.set(data)
            db.add(row)
            db.flush()
            bind_scope(db, row, school_id)
            audit_actor(db, identity, "student.agent_feedback", {"feedback_id": row.id, **data, "resulting_learning_snapshot": updated, "accuracy_action": "not_assessed" if body.accuracy is None else "human_review_flag_only"}, turn.value.get("session_id") or hashlib.sha256((identity["tenant_id"] + ":" + turn.parent_id + ":" + identity["id"]).encode()).hexdigest())
            return {**feedback_document(row), "effective_weights": updated["effective_weights"]}
