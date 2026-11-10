"""School activation for provider-free Homework Help for local family accounts."""

from hashlib import sha256

from fastapi import HTTPException
from pydantic import BaseModel, ConfigDict, Field

from .db import Record, canonical, now
from .permissions import bind_scope, scope_id


KIND = "homework_private_policy"
ENGINE = "private-homework-coach-v1"
NAME = "Private Homework Help"
MAX_INPUT_CHARS = 1500
MAX_DAILY_TURNS = 25
DISABLED = "Private Homework Help is disabled for this school"


class PrivatePolicyInput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    enabled: bool = Field(strict=True)
    expected_version: int = Field(ge=0, strict=True)


def local_student(identity):
    return identity.get("auth_provider") == "academy" and identity.get("role") == "student"


def _pointer(db, identity, school_id):
    # School IDs are at most 64 characters; keep the pointer key short and tenant-scoped.
    key = sha256(canonical([KIND, identity["tenant_id"], school_id]).encode()).hexdigest()
    row = db.get(Record, key)
    if row and (row.kind != KIND or row.parent_id != school_id or row.tenant_id != identity["tenant_id"] or scope_id(db, row) != school_id):
        raise HTTPException(409, "Private Homework Help policy scope could not be verified")
    return key, row


def policy(db, identity, school_id):
    _, row = _pointer(db, identity, school_id)
    value = row.value if row else {}
    return {"enabled": value.get("enabled") is True and value.get("engine") == ENGINE,
            "version": value.get("version", 0)}


def save_policy(db, identity, school_id, body, audit_actor):
    key, row = _pointer(db, identity, school_id)
    current = row.value if row else {"version": 0, "enabled": False}
    if current["version"] != body.expected_version:
        raise HTTPException(409, "Private Homework Help policy changed; reload before saving")
    previous = current["enabled"] is True and current.get("engine") == ENGINE
    if previous == body.enabled and current["enabled"] == body.enabled:
        return {"enabled": previous, "version": current["version"]}
    value = {"enabled": body.enabled, "version": current["version"] + 1,
             "engine": ENGINE, "saved_at": now(), "saved_by": identity["id"]}
    if row:
        row.set(value)
    else:
        row = Record(id=key, tenant_id=identity["tenant_id"], kind=KIND, parent_id=school_id, owner_id=identity["id"], data=canonical(value))
        db.add(row)
        db.flush()
        bind_scope(db, row, school_id)
    audit_actor(db, identity, "homework_ai.private_policy_saved", {"school_id": school_id, **value, "previous_enabled": previous})
    return {"enabled": value["enabled"], "version": value["version"]}


def assistant_descriptor(db, identity, school_id):
    active = policy(db, identity, school_id)
    enabled = active["enabled"]
    return {"school_id": school_id, "version": active["version"], "private_mode": True,
            "revision_id": None, "approved_revision_id": None, "approved": enabled,
            "available": enabled, "configuration_sha256": None, "live_models_enabled": False,
            "approval_required": not enabled, "reapproval_required": False, "boundary_version": ENGINE,
            "unavailable_reason": None if enabled else DISABLED, "photo_model_labels": None,
            "configuration": {"enabled": enabled, "name": NAME,
                              "biography": "Reviewed study hints and picture reading inside Academy.",
                              "allow_images": True, "max_images": 2, "max_input_chars": MAX_INPUT_CHARS,
                              "max_output_tokens": 0, "max_daily_turns": MAX_DAILY_TURNS}}
