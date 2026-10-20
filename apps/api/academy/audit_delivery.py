"""Durable metadata outbox; raw audit evidence never leaves through Core Data.

Call enqueue_audit_metadata in the same transaction that appends an Audit row.
This module performs no I/O beyond that transaction and needs no PII key.
"""
from __future__ import annotations

from datetime import datetime, timezone
import hashlib
import json
import re

from sqlalchemy import BigInteger, Integer, String, Text, UniqueConstraint, event, func, select
from sqlalchemy.orm import Mapped, mapped_column

from .db import Base, canonical, now, uid


HEX = re.compile(r"[a-f0-9]{64}\Z")
EVENT = re.compile(r"[a-z][a-z0-9_.]{0,59}\Z")
STATES = {"admitted", "pending", "sent", "completed", "released", "refused", "blocked", "failed", "withheld", "uncertain", "not_sent", "clarification", "ready", "unavailable", "cancelled", "approved", "revoked"}
USAGE = {"prompt_tokens", "completion_tokens", "total_tokens", "input_tokens", "output_tokens", "cached_tokens"}
REFERENCE_FIELDS = {"school_id", "course_id", "teacher_id", "teacher_profile_id", "lesson_turn_id", "homework_session_id", "homework_message_id", "operation_id"}
SNAPSHOT_FIELDS = {"model_snapshot", "policy_snapshot", "integration_snapshot", "teacher_snapshot", "corpus_snapshot", "routing_snapshot", "configuration_snapshot", "grounding_snapshot", "policy", "model", "triage"}


def digest(value):
    return hashlib.sha256(canonical(value).encode()).hexdigest()


class AuditDeliveryOutbox(Base):
    __tablename__ = "audit_delivery_outbox"
    __table_args__ = (UniqueConstraint("tenant_id", "audit_id"), UniqueConstraint("tenant_id", "audit_sequence"))
    id: Mapped[str] = mapped_column(String(80), primary_key=True)
    tenant_id: Mapped[str] = mapped_column(String(64), index=True)
    audit_id: Mapped[str] = mapped_column(String(64))
    audit_sequence: Mapped[int] = mapped_column(Integer)
    school_ref: Mapped[str] = mapped_column(String(64), default="", index=True)
    metadata_json: Mapped[str] = mapped_column(Text)
    metadata_sha256: Mapped[str] = mapped_column(String(64))
    created_at: Mapped[str] = mapped_column(String(40), default=now)


class AuditDeliveryState(Base):
    __tablename__ = "audit_delivery_state"
    event_id: Mapped[str] = mapped_column(String(80), primary_key=True)
    tenant_id: Mapped[str] = mapped_column(String(64), index=True)
    status: Mapped[str] = mapped_column(String(20), default="pending", index=True)
    attempts: Mapped[int] = mapped_column(Integer, default=0)
    next_attempt_at: Mapped[int] = mapped_column(BigInteger, default=0)
    lease_token: Mapped[str] = mapped_column(String(64), default="")
    lease_until: Mapped[int] = mapped_column(BigInteger, default=0)
    destination_sha256: Mapped[str] = mapped_column(String(64), default="")
    updated_at: Mapped[str] = mapped_column(String(40), default=now)


class AuditDeliveryReceipt(Base):
    __tablename__ = "audit_delivery_receipts"
    id: Mapped[str] = mapped_column(String(64), primary_key=True, default=uid)
    tenant_id: Mapped[str] = mapped_column(String(64), index=True)
    event_id: Mapped[str] = mapped_column(String(80), index=True)
    attempt: Mapped[int] = mapped_column(Integer)
    outcome: Mapped[str] = mapped_column(String(32))
    http_status: Mapped[int] = mapped_column(Integer, default=0)
    core_record_id: Mapped[int] = mapped_column(BigInteger, default=0)
    destination_sha256: Mapped[str] = mapped_column(String(64))
    created_at: Mapped[str] = mapped_column(String(40), default=now)


@event.listens_for(AuditDeliveryOutbox, "before_update")
@event.listens_for(AuditDeliveryOutbox, "before_delete")
@event.listens_for(AuditDeliveryReceipt, "before_update")
@event.listens_for(AuditDeliveryReceipt, "before_delete")
def immutable(*_):
    raise ValueError("Audit delivery evidence is append-only")


def metadata_for(row, payload):
    """Allowlist structural evidence; never recursively copy a user payload.

    References are tenant-salted SHA256 pseudonyms, not names or upstream IDs.
    They remain sensitive operational metadata and are access controlled.
    """
    if not EVENT.fullmatch(row.event_type) or not HEX.fullmatch(row.hash) or not HEX.fullmatch(row.previous_hash) or type(row.sequence) is not int or row.sequence < 1:
        raise ValueError("Invalid structural audit metadata")
    timestamp = datetime.fromisoformat(row.created_at)
    if timestamp.tzinfo is None:
        raise ValueError("Audit metadata timestamp requires a timezone")
    result = {"schema_version": 1, "audit_sequence": row.sequence, "audit_sha256": row.hash,
              "previous_audit_sha256": row.previous_hash, "event_type": row.event_type,
              "occurred_at": timestamp.astimezone(timezone.utc).isoformat(),
              "tenant_ref": digest([row.tenant_id, "tenant"]),
              "event_ref": digest([row.tenant_id, "audit", row.id])}
    for field, value in (("session_ref", row.session_id),):
        if isinstance(value, str) and value:
            result[field] = digest([row.tenant_id, field, value])
    if isinstance(payload, dict):
        for field in REFERENCE_FIELDS:
            value = payload.get(field)
            if isinstance(value, str) and 1 <= len(value) <= 512:
                result[field.removesuffix("_id") + "_ref"] = digest([row.tenant_id, field, value])
        if isinstance(payload.get("status"), str) and payload["status"] in STATES:
            result["state"] = payload["status"]
        if isinstance(payload.get("phase"), str) and payload["phase"] in {"triage", "tutor", "safety"}:
            result["phase"] = payload["phase"]
        snapshots = {}
        for field in SNAPSHOT_FIELDS:
            value = payload.get(field)
            if isinstance(value, (dict, list, str)) and len(canonical(value)) <= 1_048_576:
                snapshots[field] = digest(value)
        if snapshots:
            result["snapshot_sha256"] = snapshots
        usage = payload.get("usage")
        if isinstance(usage, dict):
            safe = {key: value for key, value in usage.items() if key in USAGE and type(value) is int and 0 <= value <= 10**12}
            if safe:
                result["usage"] = safe
    validate_metadata(result)
    return result


def validate_metadata(value):
    required = {"schema_version", "audit_sequence", "audit_sha256", "previous_audit_sha256", "event_type", "occurred_at", "tenant_ref", "event_ref"}
    optional = {"session_ref", "state", "phase", "usage", "snapshot_sha256"} | {field.removesuffix("_id") + "_ref" for field in REFERENCE_FIELDS}
    if not isinstance(value, dict) or not required <= set(value) or set(value) - required - optional or type(value["schema_version"]) is not int or value["schema_version"] != 1 or type(value["audit_sequence"]) is not int or value["audit_sequence"] < 1:
        raise ValueError("Invalid Core audit metadata schema")
    if not isinstance(value["event_type"], str) or not EVENT.fullmatch(value["event_type"]):
        raise ValueError("Invalid Core audit metadata event")
    timestamp = datetime.fromisoformat(value["occurred_at"])
    if timestamp.tzinfo is None:
        raise ValueError("Invalid Core audit timestamp")
    for key, item in value.items():
        if key.endswith("_ref") or key in {"audit_sha256", "previous_audit_sha256"}:
            if not isinstance(item, str) or not HEX.fullmatch(item):
                raise ValueError("Invalid opaque Core audit reference")
    if "state" in value and (not isinstance(value["state"], str) or value["state"] not in STATES):
        raise ValueError("Invalid Core audit state")
    if "phase" in value and (not isinstance(value["phase"], str) or value["phase"] not in {"triage", "tutor", "safety"}):
        raise ValueError("Invalid Core audit provider phase")
    for field, allowed in (("usage", USAGE), ("snapshot_sha256", SNAPSHOT_FIELDS)):
        if field not in value:
            continue
        entries = value[field]
        if not isinstance(entries, dict) or not entries or set(entries) - allowed:
            raise ValueError("Invalid Core audit metadata fields")
        for item in entries.values():
            if field == "usage" and (type(item) is not int or not 0 <= item <= 10**12):
                raise ValueError("Invalid Core audit usage scalar")
            if field == "snapshot_sha256" and (not isinstance(item, str) or not HEX.fullmatch(item)):
                raise ValueError("Invalid Core audit snapshot digest")
    if len(canonical(value).encode()) > 8192:
        raise ValueError("Core audit metadata exceeds bounded size")


def enqueue_audit_metadata(db, row, payload):
    value = metadata_for(row, payload)
    event_id = "academy-audit-" + digest([row.tenant_id, row.id])
    encoded = canonical(value)
    existing = next((entry for entry in db.new if isinstance(entry, AuditDeliveryOutbox) and entry.id == event_id), None)
    if existing is None:
        existing = db.get(AuditDeliveryOutbox, event_id)
    if existing is not None:
        if existing.metadata_json != encoded:
            raise ValueError("Audit delivery metadata cannot change")
        return existing
    entry = AuditDeliveryOutbox(id=event_id, tenant_id=row.tenant_id, audit_id=row.id,
        audit_sequence=row.sequence, school_ref=value.get("school_ref", ""), metadata_json=encoded, metadata_sha256=hashlib.sha256(encoded.encode()).hexdigest(), created_at=row.created_at)
    db.add(entry)
    db.add(AuditDeliveryState(event_id=event_id, tenant_id=row.tenant_id, status="pending", attempts=0,
        next_attempt_at=0, lease_token="", lease_until=0, destination_sha256="", updated_at=row.created_at))
    return entry


def delivery_status(db, tenant_id, school_id=None):
    scope = [AuditDeliveryOutbox.tenant_id == tenant_id]
    if school_id is not None:
        scope.append(AuditDeliveryOutbox.school_ref == digest([tenant_id, "school_id", school_id]))
    counts = dict(db.execute(select(AuditDeliveryState.status, func.count()).join(AuditDeliveryOutbox, AuditDeliveryState.event_id == AuditDeliveryOutbox.id).where(*scope).group_by(AuditDeliveryState.status)).all())
    oldest = db.scalar(select(func.min(AuditDeliveryOutbox.created_at)).join(AuditDeliveryState, AuditDeliveryState.event_id == AuditDeliveryOutbox.id).where(*scope, AuditDeliveryState.status != "delivered"))
    latest = db.scalar(select(func.max(AuditDeliveryReceipt.created_at)).join(AuditDeliveryOutbox, AuditDeliveryReceipt.event_id == AuditDeliveryOutbox.id).where(*scope, AuditDeliveryReceipt.outcome.in_(("created", "duplicate"))))
    return {"mode": "metadata_only", "network_from_api": False, "counts": counts,
            "pending_events": sum(count for status, count in counts.items() if status != "delivered"),
            "oldest_pending_at": oldest, "last_delivery_at": latest,
            "archive_independent": True}
