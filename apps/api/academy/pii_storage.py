"""Encrypt new local-family educational payloads without changing audit hashes.

Structural IDs remain queryable. Personal content is decrypted only in the API
process using its separately injected PII key, after normal endpoint authority.
Archive/export verification uses the exact ciphertext committed to the chain.
"""
import json
import hashlib
import hmac

from fastapi import HTTPException
from sqlalchemy import event
from sqlalchemy.orm import Session

from .db import Audit, Record, SEALED_JOB_KINDS, canonical

ENVELOPE = "__academy_pii__"
PRIVATE_KINDS = {
    "homework_ai_session", "homework_ai_message", "homework_ai_send",
    "draft", "submission", "review", "turn", "lesson_turn", "homework_session",
    "homework_message", "homework_outcome", "student_voice_preference",
    "student_preference_revision", "student_agent_feedback", "voice_operation",
    "change_request",
}


def _record(db, key):
    return next((row for row in db.new if isinstance(row, Record) and row.id == key), None) or db.get(Record, key)


def local_owner(db, tenant, key, depth=0):
    if not key or depth > 4:
        return False
    row = _record(db, key)
    if not row or row.tenant_id != tenant:
        return False
    if row.kind == "user":
        return row.value.get("auth_provider") == "academy"
    if row.kind == "change_request":
        return local_owner(db, tenant, row.value.get("target_id"), depth + 1)
    return local_owner(db, tenant, row.owner_id, depth + 1) or local_owner(db, tenant, row.parent_id, depth + 1)


def _purpose(row):
    return "record:" + hashlib.sha256(canonical([row.kind, row.parent_id or "", row.owner_id or ""]).encode()).hexdigest()


def lookup_key(db, tenant, owner, value):
    crypto = db.info.get("family_crypto")
    if crypto is None:
        raise HTTPException(503, "Protected student record is unavailable")
    return hmac.new(crypto._key("record-lookup-index"), canonical([tenant, owner, value]).encode(), hashlib.sha256).hexdigest()


def record_value(row):
    value = json.loads(row.data)
    if ENVELOPE not in value:
        return value
    crypto = getattr(row, "_family_crypto", None)
    if crypto is None:
        raise HTTPException(503, "Protected student record is unavailable")
    result = crypto.decrypt(value[ENVELOPE], row.tenant_id, row.id, _purpose(row))
    if row.kind in SEALED_JOB_KINDS and value.get("status") != result.get("status"):
        raise HTTPException(503, "Protected student record is unavailable")
    return result


@event.listens_for(Record, "load")
@event.listens_for(Audit, "load")
def attach_crypto(row, context):
    row._family_crypto = context.session.info.get("family_crypto")


@event.listens_for(Session, "before_flush")
def protect_records(db, _context, _instances):
    crypto = db.info.get("family_crypto")
    for row in list(db.new) + list(db.dirty):
        if not isinstance(row, Record) or row.kind not in PRIVATE_KINDS:
            continue
        value = json.loads(row.data)
        if ENVELOPE in value:
            continue
        targeted = row.kind == "change_request" and local_owner(db, row.tenant_id, value.get("target_id"))
        if not (targeted or local_owner(db, row.tenant_id, row.owner_id) or local_owner(db, row.tenant_id, row.parent_id)):
            continue
        if crypto is None:
            raise HTTPException(503, "Protected student storage requires its encryption key")
        row._family_crypto = crypto
        if not row.id:
            from .db import uid
            row.id = uid()
        wrapped = {ENVELOPE: crypto.encrypt(value, row.tenant_id, row.id, _purpose(row))}
        if row.kind in SEALED_JOB_KINDS:
            wrapped["status"] = value.get("status")
        # Bound operational indexes carry no question, transcript, answer or
        # source content. Request keys are keyed digests rather than raw input.
        if "idempotency_key" in value:
            wrapped["idempotency_key"] = lookup_key(db, row.tenant_id, row.owner_id, value["idempotency_key"])
        for field in ("teacher_profile_id", "budget_day", "image_bytes"):
            if field in value:
                wrapped[field] = value[field]
        if isinstance(value.get("recording"), dict):
            wrapped["recording"] = {"duration_ms": value["recording"].get("duration_ms", 0)}
        if isinstance(value.get("text"), str):
            wrapped["text_characters"] = len(value["text"])
        row.data = canonical(wrapped)


def protect_audit(db, row, payload):
    crypto = db.info.get("family_crypto")
    keys = {"student_id", "user_id", "principal_id", "submission_id", "turn_id", "lesson_turn_id", "message_id", "homework_session_id", "change_id", "target_id"}
    related = [value for key, value in payload.items() if key in keys and isinstance(value, str)]
    if not (local_owner(db, row.tenant_id, row.actor_id) or any(local_owner(db, row.tenant_id, key) for key in related)):
        return payload
    if crypto is None:
        raise HTTPException(503, "Protected student audit requires its encryption key")
    # Only opaque access-control IDs remain alongside the authenticated envelope.
    scope = {key: payload[key] for key in ("school_id", "course_id", "assignment_id") if isinstance(payload.get(key), str)}
    return {**scope, ENVELOPE: crypto.encrypt(payload, row.tenant_id, row.id, "audit:" + row.event_type)}


def audit_payload(row):
    value = json.loads(row.payload)
    if ENVELOPE not in value:
        return value
    crypto = getattr(row, "_family_crypto", None)
    if crypto is None:
        raise HTTPException(503, "Protected student audit is unavailable")
    return crypto.decrypt(value[ENVELOPE], row.tenant_id, row.id, "audit:" + row.event_type)
