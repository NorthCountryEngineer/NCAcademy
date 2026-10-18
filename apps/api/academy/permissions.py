"""Current school grants are an additional ACL under Core's capability ceiling."""
import hashlib
from fastapi import HTTPException
from sqlalchemy import select
from .db import Record, RecordScope, canonical


def normalized_role(role):
    return "teacher" if role == "instructor" else role


def default_school_id(tenant_id):
    return "school:" + hashlib.sha256((tenant_id + ":default-school-v2").encode()).hexdigest()[:40]


def membership_id(school_id, principal_id):
    return "member:" + hashlib.sha256((school_id + ":" + principal_id).encode()).hexdigest()[:48]


def global_admin(identity):
    return identity.get("principal_kind", "human") == "human" and identity.get("role") == "admin"


def scope_id(db, record):
    scope = db.get(RecordScope, record.id)
    return scope.school_id if scope and scope.tenant_id == record.tenant_id else None


def bind_scope(db, record, school_id):
    existing = db.get(RecordScope, record.id)
    if existing and (existing.school_id != school_id or existing.tenant_id != record.tenant_id):
        raise HTTPException(409, "School ownership is immutable")
    if not existing:
        db.add(RecordScope(record_id=record.id, tenant_id=record.tenant_id, school_id=school_id))
        db.flush()


def school_role(db, identity, school_id, settings):
    if not school_id:
        return None
    school = db.get(Record, school_id)
    if not school or school.kind != "school" or school.tenant_id != identity["tenant_id"]:
        return None
    if global_admin(identity):
        return "admin"
    if identity.get("principal_kind", "human") == "agent":
        placement = db.get(Record, identity.get("placement_id", ""))
        if not placement or placement.kind != "agent_placement" or placement.tenant_id != identity["tenant_id"] or placement.parent_id != school_id or placement.owner_id != identity["id"]:
            return None
        data = placement.value
        if not data.get("active") or identity.get("school_id") != school_id or data.get("school_id") != school_id:
            return None
        for claim, field in (("account_id", "core_agent_id"), ("binding_id", "core_binding_id"), ("consumer_service_id", "core_consumer_service_id"), ("environment", "environment"), ("task_id", "task_id")):
            if identity.get(claim) != data.get(field):
                return None
        if data.get("core_resource_type") != "academy-school" or data.get("core_resource_id") != school.value.get("core_resource_id"):
            return None
        return "teacher" if {"academy-use", "academy-teach"} <= set(identity.get("permissions", [])) else None
    member = db.get(Record, membership_id(school_id, identity["id"]))
    if not member or member.kind != "school_membership" or member.parent_id != school_id or member.owner_id != identity["id"] or member.tenant_id != identity["tenant_id"] or not member.value.get("active"):
        return None
    role = normalized_role(member.value.get("role"))
    if role not in {"school", "teacher", "student"}:
        return None
    if identity.get("auth_provider") == "academy":
        # Local family accounts cannot acquire staff or upstream authority.
        return "student" if identity.get("role") == "student" and role == "student" else None
    if settings.auth_mode == "core":
        required = {"school": "academy-school", "teacher": "academy-teach", "student": "academy-use"}[role]
        if required not in identity.get("permissions", []) or "academy-use" not in identity.get("permissions", []):
            return None
    return role


def require_school(db, identity, school_id, settings, roles=None, human=False):
    role = school_role(db, identity, school_id, settings)
    if not role or roles and role not in roles or human and identity.get("principal_kind", "human") != "human":
        raise HTTPException(403, "School permission required")
    return role


def migrate_school_scopes(db, settings):
    """Backfill scope sidecars only; historical records and audit bytes stay intact."""
    tenant = settings.tenant_id
    marker_id = "school-schema:" + hashlib.sha256(tenant.encode()).hexdigest()[:40]
    if db.get(Record, marker_id):
        return False
    school_id = default_school_id(tenant)
    if not db.get(Record, school_id):
        db.add(Record(id=school_id, tenant_id=tenant, kind="school", data=canonical({"name": "Default school", "description": "Existing curriculum and memberships", "core_resource_id": None})))
    db.flush()
    records = list(db.scalars(select(Record).where(Record.tenant_id == tenant)))
    for row in records:
        if row.kind in {"course", "assignment", "enrollment", "connection", "draft", "submission", "review", "turn"}:
            bind_scope(db, row, school_id)
    roles = {}
    for row in records:
        if row.kind == "user":
            role = normalized_role(row.value.get("role", "student"))
            if role in {"school", "teacher", "student"}:
                roles[row.id] = role
    for row in records:
        if row.kind == "enrollment" and normalized_role(row.value.get("role")) == "teacher":
            roles[row.owner_id] = "teacher"
    for principal, role in roles.items():
        key = membership_id(school_id, principal)
        if not db.get(Record, key):
            db.add(Record(id=key, tenant_id=tenant, kind="school_membership", parent_id=school_id, owner_id=principal, data=canonical({"role": role, "active": True, "source": "schema-2-legacy-backfill"})))
    db.add(Record(id=marker_id, tenant_id=tenant, kind="schema", data=canonical({"version": 2, "default_school_id": school_id})))
    db.flush()
    return True
