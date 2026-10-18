"""Private Classroom connection metadata and safe local evidence.

Google identifiers and credentials live exclusively in encrypted envelopes.
Google content is transient: it is never a course corpus, model input, audit
payload or permanent assignment snapshot.
"""
import hashlib
import time

from fastapi import HTTPException
from sqlalchemy import select

from .db import Record, canonical, now, uid
from .lesson_api import course_for_student
from .permissions import bind_scope, scope_id

ACCOUNT = "classroom_account"
LINK = "classroom_link"
EVENT = "classroom_link_event"
OPERATION = "classroom_operation"
CALLBACK = "/api/google-classroom/callback"
FLOW_COOKIE = "academy_google_classroom_flow"
MAX_DAILY_OPERATIONS = 300


def key(*parts):
    return hashlib.sha256(canonical(parts).encode()).hexdigest()


def configuration_binding(settings):
    """A secret rotation retains authority; a different Google project does not."""
    return key("academy-google-classroom-config-v1", getattr(settings, "google_classroom_client_id", ""), settings.app_url + CALLBACK)


def same_actor(original, current):
    if any(original.get(name) != current.get(name) for name in ("id", "tenant_id", "login_id", "principal_kind")) or current.get("principal_kind", "human") != "human":
        raise HTTPException(403, "Student identity changed; sign in again")
    return current


def own_record(db, identity, identifier, kind, parent, school_id):
    row = db.get(Record, identifier)
    if row and (row.kind != kind or row.tenant_id != identity["tenant_id"] or row.owner_id != identity["id"] or row.parent_id != parent or scope_id(db, row) != school_id):
        raise HTTPException(409, "Private Classroom record failed integrity verification")
    return row


def account(db, identity, school_id, *, create=False):
    identifier = key(ACCOUNT, identity["tenant_id"], identity["id"], school_id)
    row = own_record(db, identity, identifier, ACCOUNT, school_id, school_id)
    if not row and create:
        row = Record(id=identifier, tenant_id=identity["tenant_id"], kind=ACCOUNT, parent_id=school_id, owner_id=identity["id"], data=canonical({"version": uid(), "token_revision": uid(), "status": "disconnected", "cipher": "", "expires": 0}))
        db.add(row)
        db.flush()
        bind_scope(db, row, school_id)
    return row


def link(db, identity, course_id, school_id):
    return own_record(db, identity, key(LINK, identity["tenant_id"], identity["id"], course_id), LINK, course_id, school_id)


def descriptor(db, identity, course_id, settings):
    _, school_id = course_for_student(db, identity, course_id, settings)
    enabled = bool(getattr(settings, "google_classroom_enabled", False))
    row = account(db, identity, school_id)
    value = row.value if row else {}
    changed = bool(value.get("cipher")) and value.get("configuration") != configuration_binding(settings)
    reauth = value.get("status") == "reauth" or changed
    connected = enabled and value.get("status") == "connected" and bool(value.get("cipher")) and not changed
    linked = link(db, identity, course_id, school_id)
    valid_link = bool(connected and linked and linked.value.get("account_version") == value.get("version") and linked.value.get("cipher"))
    return {"enabled": enabled, "connected": bool(connected), "requires_reauth": enabled and reauth, "linked": valid_link, "link_id": linked.id if valid_link else None, "connection_version": value.get("version", "unconnected"), "reason": "Google Classroom is not enabled for this deployment." if not enabled else "Reconnect your Google account." if reauth else None}


def context(db, identity, course_id, settings, *, linked=False, expected_version=None):
    _, school_id = course_for_student(db, identity, course_id, settings)
    if not getattr(settings, "google_classroom_enabled", False):
        raise HTTPException(503, "Google Classroom is not enabled for this deployment")
    row = account(db, identity, school_id)
    if not row or row.value.get("status") != "connected" or not row.value.get("cipher"):
        raise HTTPException(409, "Connect your Google account first")
    if row.value.get("configuration") != configuration_binding(settings):
        raise HTTPException(409, "Google connection configuration changed; reconnect your account")
    if expected_version is not None and row.value["version"] != expected_version:
        raise HTTPException(409, "Google connection changed; refresh this class")
    binding = link(db, identity, course_id, school_id)
    if linked and (not binding or not binding.value.get("cipher") or binding.value.get("account_version") != row.value["version"]):
        raise HTTPException(409, "Link your Google Classroom course first")
    return {"school_id": school_id, "account_id": row.id, "account_version": row.value["version"], "token_revision": row.value["token_revision"], "configuration": row.value["configuration"], "link_id": binding.id if linked else None, "link_version": binding.value["version"] if linked else None}, row


def revalidate(db, identity, course_id, settings, snapshot):
    current, row = context(db, identity, course_id, settings, linked=bool(snapshot.get("link_id")))
    if current != snapshot:
        raise HTTPException(403, "Google connection or class access changed; result withheld")
    return row


def clear_links(db, identity, school_id):
    rows = db.scalars(select(Record).where(Record.tenant_id == identity["tenant_id"], Record.kind == LINK, Record.owner_id == identity["id"]))
    for row in rows:
        if scope_id(db, row) == school_id and row.value.get("cipher"):
            row.set({"version": uid(), "account_version": "", "cipher": ""})


def event(db, identity, course_id, school_id, action, audit_actor, **metadata):
    value = {"action": action, "course_id": course_id, "school_id": school_id, "created_at": now(), **metadata}
    row = Record(id=uid(), tenant_id=identity["tenant_id"], kind=EVENT, parent_id=course_id or school_id, owner_id=identity["id"], data=canonical(value))
    db.add(row)
    db.flush()
    bind_scope(db, row, school_id)
    audit_actor(db, identity, "classroom." + action, {"event_id": row.id, **value})
    return row


def new_operation(db, identity, course_id, school_id, operation, *, identifier=None, idempotency_key=None):
    # Scalar count never deserializes previous private operation envelopes.
    from sqlalchemy import func
    count = db.scalar(select(func.count()).select_from(Record).where(Record.tenant_id == identity["tenant_id"], Record.kind == OPERATION, Record.owner_id == identity["id"], Record.created_at >= time.strftime("%Y-%m-%d", time.gmtime())))
    if count >= MAX_DAILY_OPERATIONS and operation != "revoke":
        raise HTTPException(429, "Daily Classroom request allowance reached")
    row = Record(id=identifier or uid(), tenant_id=identity["tenant_id"], kind=OPERATION, parent_id=course_id or school_id, owner_id=identity["id"], data=canonical({"operation": operation, "course_id": course_id, "school_id": school_id, "status": "pending", "created_at": now(), "markers": [], "idempotency_key": idempotency_key}))
    db.add(row)
    db.flush()
    bind_scope(db, row, school_id)
    return row
