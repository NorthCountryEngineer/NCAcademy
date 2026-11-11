"""Private, append-only child-safety reports and human school review.

Safety evidence uses an existing encrypted, immutable learning-record kind, but
never the preference partitions or adaptation protocol. No report sends a model
request, delivers a message, or claims that a person has been contacted.
"""
import hashlib
from typing import Literal

from fastapi import Depends, HTTPException
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import or_, select

from .db import Record, RecordScope, canonical, now
from .learning_preferences import student_principal
from .permissions import bind_scope, require_school, scope_id


VERSION = "homework-child-safety-v1"
REPORT_TYPE = "homework_safety_report"
REVIEW_TYPE = "homework_safety_review"
INPUT_RISK_CODES = {"self_harm", "sexual_exploitation", "abuse_or_threat", "unsafe_request", "private_data"}
HIGH_RISK_CODES = {"self_harm", "sexual_exploitation", "abuse_or_threat"}


def digest(value):
    return hashlib.sha256(canonical(value).encode()).hexdigest()


class SafetyReportInput(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)
    idempotency_key: str = Field(min_length=8, max_length=128)
    category: Literal["unsafe", "sexual_content", "bullying", "self_harm", "private_data", "other"]
    detail: str = Field(default="", max_length=1000)


class SafetyReviewInput(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)
    idempotency_key: str = Field(min_length=8, max_length=128)
    expected_sha256: str = Field(pattern=r"^[a-f0-9]{64}$")
    expected_review_id: str | None = Field(default=None, min_length=1, max_length=64)
    disposition: Literal["reviewed", "follow_up_required"]
    reason_code: Literal["addressed", "contacted_trusted_adult", "false_positive", "other"]
    note: str = Field(min_length=10, max_length=1000)


def _id(namespace, *parts):
    # Keep the established 64-character cursor contract, while keeping safety
    # records discoverable without querying personal encrypted payloads.
    return namespace + ":" + digest([VERSION, *parts])[:63 - len(namespace)]


def _add(db, identity, identifier, parent_id, student_id, school_id, value):
    row = Record(id=identifier, tenant_id=identity["tenant_id"], kind="student_agent_feedback",
                 parent_id=parent_id, owner_id=student_id, data=canonical(value))
    db.add(row)
    db.flush()
    bind_scope(db, row, school_id)
    return row


def record_automatic_risk(db, identity, turn, risk_codes, audit_actor):
    """Store fixed, visible risk classifications even when tutoring is safe."""
    if not risk_codes:
        return None
    codes = sorted(set(risk_codes))
    if any(code not in INPUT_RISK_CODES for code in codes):
        raise ValueError("Unknown child-safety input risk")
    data = turn.value
    identifier = _id("safety-auto", identity["tenant_id"], turn.id)
    existing = db.get(Record, identifier)
    if existing:
        if existing.value.get("reason_codes") != codes:
            raise ValueError("Safety evidence cannot change")
        return existing
    value = {"feedback_type": REPORT_TYPE, "version": VERSION, "source": "automatic",
             "session_id": turn.parent_id, "message_id": turn.id, "school_id": data["school_id"],
             "category": "self_harm" if "self_harm" in codes else "sexual_content" if "sexual_exploitation" in codes else "unsafe",
             "reason_codes": codes, "priority": "high" if HIGH_RISK_CODES.intersection(codes) else "normal",
             "detail": "", "created_at": now(), "boundary_version": data["configuration_snapshot"]["boundary_version"]}
    row = _add(db, identity, identifier, turn.parent_id, turn.owner_id, data["school_id"], value)
    audit_actor(db, identity, "homework_ai.safety_reported", {"school_id": data["school_id"],
        "student_id": turn.owner_id, "homework_session_id": turn.parent_id, "report_id": row.id, **value}, turn.parent_id)
    return row


def register_homework_safety_api(app, database, auth, settings, actor, audit_actor, page_rows):
    def own_turn(db, identity, session_id, message_id):
        student_principal(db, identity, settings)
        session = db.get(Record, session_id)
        if (not session or session.kind != "homework_ai_session" or session.tenant_id != identity["tenant_id"]
                or session.owner_id != identity["id"] or scope_id(db, session) != session.parent_id):
            raise HTTPException(404, "Homework conversation not found")
        require_school(db, identity, session.parent_id, settings, {"student"}, human=True)
        turn = db.get(Record, message_id)
        if (not turn or turn.kind != "homework_ai_message" or turn.tenant_id != identity["tenant_id"]
                or turn.owner_id != identity["id"] or turn.parent_id != session_id or scope_id(db, turn) != session.parent_id):
            raise HTTPException(404, "Homework question not found")
        # Reporting is independent of model, teacher, preference, class and
        # release state. A withdrawn or withheld answer can still be reported.
        return session, turn

    def staff_school(db, identity, school_id):
        if not school_id:
            candidates = []
            for row in db.scalars(select(Record).where(Record.tenant_id == identity["tenant_id"], Record.kind == "school")):
                try:
                    require_school(db, identity, row.id, settings, {"admin", "school"}, human=True)
                    candidates.append(row.id)
                except HTTPException:
                    pass
            if len(candidates) != 1:
                raise HTTPException(422 if candidates else 403, "Choose an authorized school")
            school_id = candidates[0]
        require_school(db, identity, school_id, settings, {"admin", "school"}, human=True)
        return school_id

    def staff_report(db, identity, identifier, school_id):
        school_id = staff_school(db, identity, school_id)
        row = db.get(Record, identifier)
        if (not row or row.kind != "student_agent_feedback" or row.tenant_id != identity["tenant_id"]
                or scope_id(db, row) != school_id or row.value.get("feedback_type") != REPORT_TYPE):
            raise HTTPException(404, "Safety report not found")
        return row

    def review_for(db, report):
        reviews = []
        for namespace in ("safety-review", "safety-resolve"):
            row = db.get(Record, _id(namespace, report.tenant_id, report.id))
            if row and (row.kind != "student_agent_feedback" or row.parent_id != report.id or row.owner_id != report.owner_id
                        or row.tenant_id != report.tenant_id or row.value.get("feedback_type") != REVIEW_TYPE
                        or scope_id(db, row) != scope_id(db, report)):
                raise HTTPException(409, "Safety review integrity check failed")
            if row:
                reviews.append({"id": row.id, **row.value})
        return reviews

    def descriptor(db, report, *, staff=False):
        value, reviews = report.value, review_for(db, report)
        review = reviews[-1] if reviews else None
        result = {"id": report.id, **{key: value[key] for key in
                  ("session_id", "message_id", "category", "detail", "source", "reason_codes", "priority", "created_at")},
                  "report_sha256": digest(value), "status": review["disposition"] if review else "pending"}
        if staff:
            student = db.get(Record, report.owner_id)
            name = "Student"
            if student and student.kind == "user" and student.tenant_id == report.tenant_id:
                from .course_enrollment import student_projection
                name = student_projection(db, auth, student, value["school_id"])["display_name"]
            result.update(student_id=report.owner_id,
                          student_name=name,
                          school_id=value["school_id"], review=review, review_history=reviews)
        elif review:
            result["review"] = {key: review[key] for key in ("id", "disposition", "created_at")}
        return result

    def own_existing_report(db, identity, session, turn, identifier):
        row = db.get(Record, identifier)
        if row and (row.kind != "student_agent_feedback" or row.tenant_id != identity["tenant_id"]
                    or row.owner_id != identity["id"] or row.parent_id != session.id
                    or scope_id(db, row) != session.parent_id or row.value.get("feedback_type") != REPORT_TYPE
                    or row.value.get("message_id") != turn.id or row.value.get("session_id") != session.id
                    or row.value.get("school_id") != session.parent_id or row.value.get("source") != "student"):
            raise HTTPException(404, "Safety report not found")
        return row

    @app.get("/api/homework-help/ai/sessions/{session_id}/messages/{message_id}/safety-report")
    def own_reports(session_id: str, message_id: str, identity=Depends(actor)):
        with database.factory() as db:
            session, turn = own_turn(db, identity, session_id, message_id)
            identifier = _id("safety-report", identity["tenant_id"], identity["id"], message_id)
            row = own_existing_report(db, identity, session, turn, identifier)
            return {"reports": [descriptor(db, row)] if row else []}

    @app.post("/api/homework-help/ai/sessions/{session_id}/messages/{message_id}/safety-report")
    def report(session_id: str, message_id: str, body: SafetyReportInput, identity=Depends(actor)):
        with database.write() as db:
            session, turn = own_turn(db, identity, session_id, message_id)
            identifier = _id("safety-report", identity["tenant_id"], identity["id"], message_id)
            supplied = body.model_dump()
            previous = own_existing_report(db, identity, session, turn, identifier)
            if previous:
                if any(previous.value.get(key) != value for key, value in supplied.items()):
                    raise HTTPException(409, "A different safety report was already recorded for this question")
                return descriptor(db, previous)
            value = {"feedback_type": REPORT_TYPE, "version": VERSION, **supplied, "source": "student",
                     "session_id": session_id, "message_id": message_id, "school_id": session.parent_id,
                     "reason_codes": [], "priority": "high" if body.category in {"self_harm", "sexual_content", "bullying"} else "normal", "created_at": now()}
            row = _add(db, identity, identifier, session_id, identity["id"], session.parent_id, value)
            audit_actor(db, identity, "homework_ai.safety_reported", {"school_id": session.parent_id,
                "student_id": identity["id"], "homework_session_id": session_id, "report_id": row.id, **value}, session_id)
            return descriptor(db, row)

    @app.get("/api/homework-help/ai/safety-reports")
    def reports(school_id: str | None = None, cursor: str | None = None, identity=Depends(actor)):
        with database.write() as db:
            selected = staff_school(db, identity, school_id)
            query = select(Record).join(RecordScope, RecordScope.record_id == Record.id).where(
                Record.tenant_id == identity["tenant_id"], Record.kind == "student_agent_feedback",
                RecordScope.tenant_id == identity["tenant_id"], RecordScope.school_id == selected,
                or_(Record.id.like("safety-report:%"), Record.id.like("safety-auto:%")))
            rows, next_cursor = page_rows(db, query, cursor)
            result = [descriptor(db, row, staff=True) for row in rows if row.value.get("feedback_type") == REPORT_TYPE]
            audit_actor(db, identity, "homework_ai.safety_catalog_accessed", {"school_id": selected, "count": len(result)})
            return {"reports": result, "next_cursor": next_cursor}

    @app.get("/api/homework-help/ai/safety-reports/{report_id}")
    def report_detail(report_id: str, school_id: str | None = None, identity=Depends(actor)):
        with database.write() as db:
            row = staff_report(db, identity, report_id, school_id)
            turn = db.get(Record, row.value["message_id"])
            if (not turn or turn.kind != "homework_ai_message" or turn.parent_id != row.parent_id
                    or turn.tenant_id != row.tenant_id or turn.owner_id != row.owner_id or scope_id(db, turn) != scope_id(db, row)):
                raise HTTPException(409, "Reported interaction integrity check failed")
            value = turn.value
            # Hidden candidates and provider reasoning are never this projection.
            message = {"id": turn.id, **{key: value.get(key) for key in ("message", "created_at", "status", "error")},
                       "answer": value.get("answer") if value["status"] in {"released", "clarify"} else None}
            audit_actor(db, identity, "homework_ai.safety_report_viewed", {"school_id": scope_id(db, row),
                "student_id": row.owner_id, "homework_session_id": row.parent_id, "report_id": row.id}, row.parent_id)
            return {"report": descriptor(db, row, staff=True), "message": message}

    @app.post("/api/homework-help/ai/safety-reports/{report_id}/review")
    def review(report_id: str, body: SafetyReviewInput, school_id: str | None = None, identity=Depends(actor)):
        with database.write() as db:
            report = staff_report(db, identity, report_id, school_id)
            if digest(report.value) != body.expected_sha256:
                raise HTTPException(409, "The safety report changed; reload the exact report before review")
            supplied = body.model_dump()
            previous_reviews = review_for(db, report)
            for previous in previous_reviews:
                if previous["idempotency_key"] == body.idempotency_key:
                    if any(previous.get(key) != value for key, value in supplied.items()):
                        raise HTTPException(409, "This safety review key already has different evidence")
                    return previous
            previous = previous_reviews[-1] if previous_reviews else None
            if previous and (len(previous_reviews) != 1 or previous["disposition"] != "follow_up_required"
                             or body.disposition != "reviewed" or body.expected_review_id != previous["id"]):
                raise HTTPException(409, "Resolve only the exact current follow-up review; completed evidence cannot change")
            if not previous and body.expected_review_id:
                raise HTTPException(409, "No current follow-up review matches this resolution")
            value = {"feedback_type": REVIEW_TYPE, "version": VERSION, **supplied, "report_id": report.id,
                     "school_id": scope_id(db, report), "reviewed_by": identity["id"], "created_at": now()}
            row = _add(db, identity, _id("safety-resolve" if previous else "safety-review", identity["tenant_id"], report.id), report.id,
                       report.owner_id, scope_id(db, report), value)
            audit_actor(db, identity, "homework_ai.safety_reviewed", {"school_id": scope_id(db, report),
                "student_id": report.owner_id, "homework_session_id": report.parent_id, "report_id": report.id, **value}, report.parent_id)
            return {"id": row.id, **value}
