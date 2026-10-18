"""Fresh read-only authority for a student's selected, approved AI teacher.

Requires the separately reviewed Core agent-binding authorization supplement.
No credential is requested, issued, stored, replayed or borrowed from a teacher.
An absent endpoint or any failed binding check blocks the operation.
"""
import re
import time
from fastapi import HTTPException

from .agent_auth import placement
from .db import Record, canonical
from .permissions import scope_id
from .teacher_api import _assigned_context


PATH = "/api/platform/v1/agent-bindings/authorize/"


def _context(auth, profile_id, school_id, course_id):
    for identifier in (profile_id, school_id, course_id):
        if not isinstance(identifier, str) or not re.fullmatch(r"[A-Za-z0-9:_-]{1,64}", identifier):
            raise HTTPException(403, "Approved AI teaching context required")
    with auth.database.factory() as db:
        profile = db.get(Record, profile_id)
        course = db.get(Record, course_id)
        if (not profile or profile.tenant_id != auth.settings.tenant_id or profile.kind != "teacher_profile"
                or profile.value.get("kind") != "ai" or scope_id(db, profile) != school_id
                or not course or course.tenant_id != auth.settings.tenant_id or course.kind != "course"
                or scope_id(db, course) != school_id):
            raise HTTPException(403, "Approved AI teaching context unavailable")
        revision, link = _assigned_context(db, profile, course, school_id)
        approved = placement(auth, profile.value.get("agent_placement_id"))
        if approved["school_id"] != school_id or approved["core_consumer_service_id"] != auth.settings.core_service_id:
            raise HTTPException(403, "Selected teacher belongs to another consuming service")
        return {"profile_id": profile.id, "approved_revision_id": revision.id,
                "approved_hash": revision.value["sha256"], "approved_version": revision.value["version"],
                "school_id": school_id, "course_id": course_id, "corpus": link.value.get("corpus"),
                "placement": approved}


def authorize_teacher(auth, profile_id, school_id, course_id):
    """Return only bounded, nonsecret evidence of current selected-agent authority.

The caller already authorizes the student's current class/assignment. This adds
the independent live Core authority of the teacher and checks local approval on
both sides of that remote check. Call again before releasing student output.
"""
    settings = auth.settings
    if settings.auth_mode != "core" or not settings.allow_agent_teachers or not settings.core_workload_token:
        raise HTTPException(403, "Selected AI teacher authority is not enabled")
    before = _context(auth, profile_id, school_id, course_id)
    approved = before["placement"]
    payload = {"binding_id": approved["core_binding_id"], "environment": approved["environment"],
               "task_id": approved.get("task_id"), "permission": "academy-teach",
               "resource_type": approved["core_resource_type"], "resource_id": approved["core_resource_id"]}
    claims = auth.core_agent(PATH, payload, approved.get("purpose", ""))
    if not isinstance(claims, dict) or claims.get("active") is not True:
        raise HTTPException(403, "Selected teacher's Core authority is unavailable")
    clock = time.time()
    valid = (claims.get("principal_kind") == "agent" and claims.get("iss") == settings.core_issuer
             and claims.get("aud") == "urn:core:service:" + settings.core_service_id
             and claims.get("organization_id") == settings.core_organization_id
             and claims.get("agent_id") == approved["core_agent_id"]
             and claims.get("sub") == "urn:core:agent:" + approved["core_agent_id"]
             and claims.get("binding_id") == approved["core_binding_id"]
             and claims.get("consumer_service_id") == settings.core_service_id
             and claims.get("consumer_service_id") == approved["core_consumer_service_id"]
             and claims.get("environment") == approved["environment"]
             and claims.get("task_id") == approved.get("task_id")
             and claims.get("permission") == payload["permission"]
             and claims.get("resource_type") == payload["resource_type"]
             and claims.get("resource_id") == payload["resource_id"]
             and type(claims.get("iat")) is int and clock - 60 <= claims["iat"] <= clock + 5
             and type(claims.get("exp")) is int and clock < claims["exp"] <= clock + 60
             and claims["exp"] <= claims["iat"] + 60
             and all(isinstance(claims.get(key), list) and len(claims[key]) <= 256
                     and all(isinstance(item, str) and 0 < len(item) <= 200 for item in claims[key])
                     for key in ("roles", "permissions")))
    if not valid or "admin" in claims.get("permissions", []):
        raise HTTPException(401, "Selected teacher's Core authority binding is invalid")
    if not {"academy-use", "academy-teach"} <= set(claims["permissions"]):
        raise HTTPException(403, "Selected teacher requires current use and teach permissions")
    after = _context(auth, profile_id, school_id, course_id)
    if canonical(before) != canonical(after):
        raise HTTPException(409, "Selected teacher context changed during authorization")
    # Never pass arbitrary Core fields through to student sessions or audit.
    return {"active": True, "profile_id": before["profile_id"], "approved_revision_id": before["approved_revision_id"],
            "approved_hash": before["approved_hash"], "approved_version": before["approved_version"],
            "school_id": school_id, "course_id": course_id, "placement_id": approved["placement_id"],
            "corpus_id": before["corpus"]["id"], "corpus_snapshot_sha256": before["corpus"]["snapshot_sha256"],
            **{key: claims[key] for key in ("iss", "aud", "sub", "principal_kind", "agent_id", "organization_id",
                                           "consumer_service_id", "binding_id", "environment", "task_id", "permission",
                                           "resource_type", "resource_id", "roles", "permissions", "iat", "exp")},
            "checked_at": int(clock), "authorization_method": "core-binding-read-only-v1"}
