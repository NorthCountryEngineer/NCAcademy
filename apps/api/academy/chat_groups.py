"""NCAcademy group ACLs. Core Chat stores messages; membership stays local.

Only the NCAcademy installation is enrolled upstream. Joining a group never
confers school, class, Core or teaching authority. Every projection rechecks
current local grants instead of trusting historical membership receipts.
"""
import hashlib

from fastapi import HTTPException
from sqlalchemy import select

from .db import Record, canonical, now
from .permissions import bind_scope, membership_id, normalized_role, require_school, school_role, scope_id

GROUP = "academy_chat_group"
MEMBER = "academy_chat_member"
COMMAND = "academy_chat_command"
RECEIPT = "academy_chat_receipt"
PROPOSAL = "academy_chat_proposal"
EVENT = "academy_chat_membership_event"
ATTEMPT = "academy_chat_attempt"


def key(*parts):
    return hashlib.sha256(canonical(list(parts)).encode()).hexdigest()


def get_record(db, tenant, identifier, kind):
    row = db.get(Record, identifier)
    if not row or row.kind != kind or row.tenant_id != tenant:
        raise HTTPException(404, "Chat record not found")
    return row


def put(db, tenant, kind, identifier, parent, owner, data, school):
    row = Record(id=identifier, tenant_id=tenant, kind=kind, parent_id=parent, owner_id=owner, data=canonical(data))
    db.add(row)
    db.flush()
    bind_scope(db, row, school)
    return row


def principal(db, identity, principal_id, school_id, settings):
    """Descriptor contains a display name, never emails or Core credentials."""
    if principal_id.startswith("agent:"):
        placements = list(db.scalars(select(Record).where(Record.tenant_id == identity["tenant_id"], Record.kind == "agent_placement", Record.parent_id == school_id, Record.owner_id == principal_id).limit(2)))
        for row in placements:
            value = row.value
            if value.get("active") is True:
                return {"id": principal_id, "name": str(value.get("name") or "AI teacher")[:200], "role": "teacher", "principal_kind": "agent"}
        raise HTTPException(403, "Current school participant required")
    row = get_record(db, identity["tenant_id"], principal_id, "user")
    if row.value.get("disabled"):
        raise HTTPException(403, "Current school participant required")
    # Memberships and current principal activation are checked independently;
    # global administrators may review without a local membership.
    if principal_id == identity["id"] and identity.get("role") == "admin" and identity.get("principal_kind", "human") == "human":
        role = "admin"
    else:
        member = db.get(Record, membership_id(school_id, principal_id))
        if not member or member.tenant_id != identity["tenant_id"] or member.kind != "school_membership" or member.parent_id != school_id or member.owner_id != principal_id or not member.value.get("active"):
            raise HTTPException(403, "Current school participant required")
        role = normalized_role(member.value.get("role"))
        if role not in {"school", "teacher", "student"}:
            raise HTTPException(403, "Current school participant required")
    return {"id": principal_id, "name": str(row.value.get("name") or "School participant")[:200], "role": role, "principal_kind": "human"}


def author(identity):
    return {"id": identity["id"], "name": str(identity.get("name") or "School participant")[:200], "principal_kind": identity.get("principal_kind", "human")}


def course_access(db, identity, course_id, school_id, settings, teaching=False):
    row = get_record(db, identity["tenant_id"], course_id, "course")
    if scope_id(db, row) != school_id:
        raise HTTPException(403, "Class belongs to another school")
    role = require_school(db, identity, school_id, settings)
    if role in {"admin", "school"}:
        return row
    enrolled = db.scalar(select(Record).where(Record.tenant_id == identity["tenant_id"], Record.kind == "enrollment", Record.parent_id == course_id, Record.owner_id == identity["id"]))
    admitted = enrolled and normalized_role(enrolled.value.get("role")) == role
    if role == "teacher":
        admitted = admitted or row.owner_id == identity["id"]
    if not admitted or teaching and role != "teacher":
        raise HTTPException(403, "Current class membership required")
    return row


def member_row(db, group, principal_id):
    row = db.get(Record, key(MEMBER, group.id, principal_id))
    return row if row and row.kind == MEMBER and row.tenant_id == group.tenant_id and row.parent_id == group.id and row.owner_id == principal_id else None


def access(db, identity, group, settings, *, read=False, manage=False):
    data = group.value
    if scope_id(db, group) != data["school_id"]:
        raise HTTPException(409, "Group scope failed verification")
    role = require_school(db, identity, data["school_id"], settings)
    if data.get("course_id"):
        course_access(db, identity, data["course_id"], data["school_id"], settings)
    agent = identity.get("principal_kind", "human") == "agent"
    # Agents can participate only within their actual assigned class. A managed
    # persona alone is never a principal and cannot use these endpoints.
    if agent and (not data.get("course_id") or role != "teacher"):
        raise HTTPException(403, "Agent teachers require an assigned class group")
    manager = role in {"admin", "school"} and not agent or role == "teacher" and group.owner_id == identity["id"]
    if data["visibility"] == "class":
        membership = "active"
        manager = manager or role == "teacher"
    else:
        row = member_row(db, group, identity["id"])
        membership = row.value.get("status", "none") if row else "none"
        if manager:
            membership = "active"
    allowed = manager or membership == "active"
    if manage and not manager:
        raise HTTPException(403, "Group management permission required")
    if read and not allowed:
        raise HTTPException(403, "Join this group before reading or sending")
    discover = data["visibility"] != "private" or manager or membership in {"active", "invited", "requested"}
    if not discover:
        raise HTTPException(404, "Chat group not found")
    return {"role": role, "membership": membership, "manager": manager, "allowed": allowed, "human": not agent}


def describe(db, identity, group, settings):
    data, acl = group.value, access(db, identity, group, settings)
    ready = data["status"] == "ready" and bool(getattr(settings, "core_chat_enabled", False))
    can_join = not acl["allowed"] and data["visibility"] != "class" and (acl["membership"] == "invited" or data["join_policy"] in {"open", "approval"})
    return {"id": group.id, **{field: data.get(field) for field in ("title", "description", "school_id", "course_id", "visibility", "join_policy", "required", "status", "error")}, "membership": acl["membership"], "can_read": acl["allowed"] and ready, "can_send": acl["allowed"] and ready, "can_manage": acl["manager"], "can_review": acl["manager"] and acl["human"] and acl["role"] in {"admin", "school"}, "can_join": can_join, "can_leave": acl["membership"] in {"active", "requested", "invited"} and not acl["manager"] and data["visibility"] != "class", "can_recover": acl["manager"] and data["status"] in {"uncertain", "pending", "unavailable"}}


def membership_change(db, identity, group, principal_id, status, action, audit_actor):
    row = member_row(db, group, principal_id)
    prior = row.value.get("status", "none") if row else "none"
    if prior == status:
        return
    data = {"status": status, "updated_at": now()}
    if row:
        row.set(data)
    else:
        row = put(db, identity["tenant_id"], MEMBER, key(MEMBER, group.id, principal_id), group.id, principal_id, data, group.value["school_id"])
    event = {"group_id": group.id, "school_id": group.value["school_id"], "course_id": group.value.get("course_id"), "principal_id": principal_id, "previous": prior, "status": status, "action": action, "created_at": now()}
    put(db, identity["tenant_id"], EVENT, key(EVENT, group.id, principal_id, event), group.id, identity["id"], event, group.value["school_id"])
    audit_actor(db, identity, "chat.membership_changed", event, group.id)


def ensure_class_group(db, identity, course, school_id, audit_actor):
    """Materialize one required class registry. No Core calls or student grants.

    Safe for course creation/publication/enrollment hooks inside their existing
    transaction. Membership is always derived, so changing enrollment immediately
    changes chat access without a fan-out update or an upstream removal race.
    """
    if course.tenant_id != identity["tenant_id"] or course.kind != "course" or scope_id(db, course) != school_id:
        raise HTTPException(403, "Class school scope required")
    identifier = key(GROUP, identity["tenant_id"], "required-class", course.id)
    prior = db.get(Record, identifier)
    if prior:
        if prior.kind != GROUP or prior.tenant_id != identity["tenant_id"] or prior.value.get("course_id") != course.id or prior.value.get("school_id") != school_id:
            raise HTTPException(409, "Class group registry conflict")
        return prior
    value = {"school_id": school_id, "course_id": course.id, "title": str(course.value.get("title") or "Class")[:120], "description": "Required class conversation", "visibility": "class", "join_policy": "open", "required": True, "status": "pending", "error": None, "room_id": None, "created_at": now(), "topic": "ncacademy:" + identifier, "provision_started": None}
    row = put(db, identity["tenant_id"], GROUP, identifier, school_id, course.owner_id, value, school_id)
    audit_actor(db, identity, "chat.class_group_registered", {"group_id": row.id, "course_id": course.id, "school_id": school_id}, row.id)
    return row
