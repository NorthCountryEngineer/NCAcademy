"""Fresh Core agent authority plus an explicit local school placement.

Opaque agent tokens never become human sessions or identity aliases. A caller
can select a placement ID, but all authority/context is loaded from that stored
approval and checked against Core on every request.
"""
import re
import time
from uuid import UUID

from fastapi import HTTPException

from .db import Record


def _uuid(value):
    try:
        return isinstance(value, str) and str(UUID(value)) == value
    except (ValueError, TypeError, AttributeError):
        return False


def placement(auth, placement_id):
    if not isinstance(placement_id, str) or not re.fullmatch(r"[A-Za-z0-9_-]{1,64}", placement_id):
        raise HTTPException(401, "Approved agent placement required")
    with auth.database.factory() as db:
        row = db.get(Record, placement_id)
        if not row or row.tenant_id != auth.settings.tenant_id or row.kind != "agent_placement":
            raise HTTPException(403, "Agent placement unavailable")
        data = row.value
        school = db.get(Record, row.parent_id)
        if not school or school.kind != "school" or school.tenant_id != row.tenant_id or school.value.get("active", True) is not True:
            raise HTTPException(403, "Agent school unavailable")
        valid = (data.get("active") is True and data.get("school_id") == row.parent_id
                 and data.get("environment") == auth.settings.core_environment
                 and data.get("core_resource_type") == "academy-school"
                 and isinstance(data.get("core_resource_id"), str)
                 and 1 <= len(data["core_resource_id"]) <= 200
                 and data["core_resource_id"] == school.value.get("core_resource_id")
                 and all(_uuid(data.get(key)) for key in ("core_agent_id", "core_binding_id", "core_consumer_service_id"))
                 and (data.get("task_id") is None or _uuid(data.get("task_id"))))
        if not valid or row.owner_id != "agent:" + str(data.get("core_agent_id")):
            raise HTTPException(403, "Agent placement binding invalid")
        purpose = data.get("purpose", "")
        if not isinstance(purpose, str) or len(purpose) > 80 or (purpose and not re.fullmatch(r"[-A-Za-z0-9_]+", purpose)):
            raise HTTPException(403, "Agent purpose configuration invalid")
        return {**data, "placement_id": row.id, "school_id": row.parent_id}


def introspect(auth, token, approved, permission, resource_type, resource_id):
    if not isinstance(resource_type, str) or not isinstance(resource_id, str) or not resource_type or not resource_id:
        raise HTTPException(403, "Registered Core resource required")
    payload = {"token": token, "environment": approved["environment"], "task_id": approved.get("task_id"),
               "permission": permission, "resource_type": resource_type, "resource_id": resource_id}
    claims = auth.core_agent("/api/platform/v1/agent-credentials/introspect/", payload, approved.get("purpose", ""))
    if claims.get("active") is not True:
        raise HTTPException(403, "Core agent access expired, revoked or denied")
    clock = time.time()
    valid = (claims.get("principal_kind") == "agent" and claims.get("iss") == auth.settings.core_issuer
             and claims.get("aud") == "urn:core:service:" + auth.settings.core_service_id
             and claims.get("organization_id") == auth.settings.core_organization_id
             and claims.get("agent_id") == approved["core_agent_id"]
             and claims.get("binding_id") == approved["core_binding_id"]
             and claims.get("consumer_service_id") == approved["core_consumer_service_id"]
             and claims.get("sub") == "urn:core:agent:" + approved["core_agent_id"]
             and claims.get("environment") == approved["environment"]
             and claims.get("task_id") == approved.get("task_id")
             and type(claims.get("exp")) is int and clock < claims["exp"] <= clock + 3600
             and all(isinstance(claims.get(key), list) and len(claims[key]) <= 256
                     and all(isinstance(item, str) and 0 < len(item) <= 200 for item in claims[key])
                     for key in ("roles", "permissions")))
    if not valid or "admin" in claims.get("permissions", []):
        raise HTTPException(401, "Core agent authority binding invalid")
    if not {"academy-use", "academy-teach", permission} <= set(claims["permissions"]):
        raise HTTPException(403, "Core agent teacher permissions required")
    return claims


def actor(auth, request):
    header = request.headers.get("authorization", "")
    parts = header.split(" ")
    if len(parts) != 2 or parts[0].lower() != "bearer" or not 1 <= len(parts[1]) <= 256 or any(c.isspace() or ord(c) < 33 or ord(c) > 126 for c in parts[1]):
        raise HTTPException(401, "Core agent Bearer credential required")
    if auth.settings.auth_mode != "core" or not auth.settings.allow_agent_teachers or not auth.settings.core_workload_token:
        raise HTTPException(403, "Agent teacher access is not enabled")
    token = parts[1]
    approved = placement(auth, request.headers.get("x-academy-agent-placement"))
    claims = introspect(auth, token, approved, "academy-teach", approved["core_resource_type"], approved["core_resource_id"])
    # A local revocation during the remote check takes effect before admission.
    if approved != placement(auth, approved["placement_id"]):
        raise HTTPException(403, "Agent placement changed; retry with current approval")
    request.state.academy_agent_token = token
    request.state.academy_agent_placement = approved
    return {"id": "agent:" + claims["agent_id"], "name": str(approved.get("name") or "AI teacher")[:200],
            "role": "teacher", "principal_kind": "agent", "tenant_id": auth.settings.tenant_id,
            "school_id": approved["school_id"], "placement_id": approved["placement_id"],
            "account_id": claims["agent_id"], "binding_id": claims["binding_id"],
            "consumer_service_id": claims["consumer_service_id"], "environment": approved["environment"],
            "task_id": approved.get("task_id"), "permissions": claims["permissions"],
            "can_audit": "academy-audit" in claims["permissions"], "csrf_token": "", "login_id": ""}


def authorize_resource(auth, request, identity, permission, resource_type, resource_id):
    token = getattr(request.state, "academy_agent_token", None)
    approved = getattr(request.state, "academy_agent_placement", None)
    if not token or not approved or identity.get("placement_id") != approved["placement_id"] or identity.get("id") != "agent:" + approved["core_agent_id"]:
        raise HTTPException(401, "Agent authorization required")
    if approved != placement(auth, approved["placement_id"]):
        raise HTTPException(403, "Agent placement changed")
    claims = introspect(auth, token, approved, permission, resource_type, resource_id)
    if approved != placement(auth, approved["placement_id"]):
        raise HTTPException(403, "Agent placement changed")
    return claims
