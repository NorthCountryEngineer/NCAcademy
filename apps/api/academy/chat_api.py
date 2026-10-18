"""Application-scoped NCE Core Chat with current NCAcademy group ACLs.

Core receives only the installation actor. The authenticated human/agent author
is retained in immutable local command/delivery evidence, never impersonated by
an upstream participant. An uncertain create is never repeated; message retries
reuse the exact original Core request ID only after an explicit user action.
"""
from datetime import datetime, timezone
import hashlib
import time

from fastapi import Depends, HTTPException, Request, Response
from sqlalchemy import JSON, case, cast, func, or_, select
from sqlalchemy.orm import aliased

from .chat_groups import ATTEMPT, COMMAND, EVENT, GROUP, MEMBER, PROPOSAL, RECEIPT, access, author, course_access, describe, ensure_class_group, get_record, key, member_row, membership_change, principal, put
from .chat_schemas import ChatBootstrapInput, ChatGroupInput, ChatMemberInput, ChatMessageInput, ChatReviewInput, EmptyChatInput
from .core_chat_transport import CoreChatFailure
from .db import Record, RecordScope, canonical, now, uid
from .permissions import normalized_role, require_school, school_role, scope_id


MAX_GROUPS = 100
MAX_DAILY_MESSAGES = 200


def _same_actor(first, current):
    fields = ("id", "tenant_id", "principal_kind", "login_id", "placement_id", "account_id", "binding_id", "consumer_service_id", "environment", "task_id")
    if any(first.get(field) != current.get(field) for field in fields):
        raise HTTPException(403, "Chat identity changed; sign in again")
    return current


def _authority(identity):
    return {field: identity.get(field) for field in ("id", "name", "tenant_id", "role", "principal_kind", "placement_id", "account_id", "binding_id", "consumer_service_id", "environment", "task_id", "school_id", "permissions")}


def _scope(group):
    data = group.value
    return {field: data.get(field) for field in ("school_id", "course_id", "visibility", "join_policy", "required", "topic", "room_id")}


def _hash(text):
    return hashlib.sha256(text.encode()).hexdigest()


def _json_field(db, model, name):
    # Field names are fixed by call sites, never request text.
    if db.bind.dialect.name == "sqlite":
        return func.json_extract(model.data, "$." + name)
    return cast(model.data, JSON)[name].as_string()


def _eligible_course_ids(db, identity, school_id, role):
    course, enrollment, scope = aliased(Record), aliased(Record), aliased(RecordScope)
    query = select(course.id).join(scope, scope.record_id == course.id).where(course.tenant_id == identity["tenant_id"], course.kind == "course", scope.tenant_id == identity["tenant_id"], scope.school_id == school_id)
    if role not in {"admin", "school"}:
        admitted = select(enrollment.parent_id).where(enrollment.tenant_id == identity["tenant_id"], enrollment.kind == "enrollment", enrollment.owner_id == identity["id"], _json_field(db, enrollment, "role").in_(["teacher", "instructor"] if role == "teacher" else ["student"]))
        query = query.where(or_(course.id.in_(admitted), course.owner_id == identity["id"] if role == "teacher" else False))
    return query


def _wire_request(data):
    return {"conversation_id": data["group_scope"]["room_id"], "request_id": data["request_id"], "text": data["text"], "ai": data["ai"], "agent_name": data["author"]["name"][:160] if data["ai"] else "", "reply_to": data.get("reply_to")}


def _command_evidence(command_id, data):
    # Explicit safe fields only: no cookies, user/agent tokens, workload secrets
    # or transport exception bodies. The audit archive can reconstruct the exact
    # original wire operation without requiring mutable database joins.
    return {"id": command_id, "created_at": data["created_at"], "idempotency_key": data["idempotency_key"], "group_scope": data["group_scope"], "author": data["author"], "authority": data["authority"], "text_sha256": data["text_sha256"], "agent_placement_sha256": data.get("agent_placement_sha256"), "wire_request": _wire_request(data)}


def _delivery_evidence(result):
    return {field: result[field] for field in ("organization_id", "conversation_id", "authority_issuer", "message", "created") if field in result}


def register_chat_api(app, database, auth, settings, actor, audit_actor):
    def transport():
        if not getattr(settings, "core_chat_enabled", False):
            raise CoreChatFailure("disabled", status_code=503)
        value = getattr(app.state, "core_chat_transport", None)
        if value is None:
            raise HTTPException(503, "NCE Core Chat is not configured")
        return value

    def group(db, identity, group_id, **requirements):
        row = get_record(db, identity["tenant_id"], group_id, GROUP)
        access(db, identity, row, settings, **requirements)
        return row

    def fresh(request, identity):
        return _same_actor(identity, auth.actor(request))

    def command(db, identity, group_row, command_id, *, review=False):
        row = get_record(db, identity["tenant_id"], command_id, COMMAND)
        if row.parent_id != group_row.id or scope_id(db, row) != group_row.value["school_id"]:
            raise HTTPException(404, "Chat command not found")
        if row.owner_id != identity["id"]:
            if not review:
                raise HTTPException(404, "Chat command not found")
            acl = access(db, identity, group_row, settings, manage=True)
            if not acl["human"] or acl["role"] not in {"admin", "school"}:
                raise HTTPException(403, "Human School/Admin publication review required")
        return row

    def receipt(db, row):
        result = db.get(Record, key(RECEIPT, row.id))
        if result and (result.kind != RECEIPT or result.tenant_id != row.tenant_id or result.parent_id != row.parent_id or result.value.get("command_id") != row.id):
            raise HTTPException(409, "Chat receipt failed integrity verification")
        return result

    def attempts(db, row):
        return list(db.scalars(select(Record).where(Record.tenant_id == row.tenant_id, Record.kind == ATTEMPT, Record.parent_id == row.id).order_by(Record.created_at.desc(), Record.id.desc()).limit(10)))

    def project_command(db, identity, group_row, row, *, review=False):
        access(db, identity, group_row, settings, read=True)
        command(db, identity, group_row, row.id, review=review)
        data, outcome = row.value, receipt(db, row)
        previous = attempts(db, row)
        latest = previous[0].value if previous else None
        status = outcome.value["status"] if outcome else latest["status"] if latest else "pending_review" if data.get("ai") else "pending"
        can_retry = not outcome and status in {"pending", "failed", "uncertain"} and (row.owner_id == identity["id"] or review and access(db, identity, group_row, settings)["human"])
        return {"id": row.id, "group_id": group_row.id, "operation": data["operation"], "status": status, "text": data.get("text", ""), "author": data["author"], "created_at": data["created_at"], "message_id": outcome.value.get("message_id") if outcome else None, "can_retry": can_retry, "error": outcome.value.get("error") if outcome else latest.get("error") if latest else None, "proposal_id": key(PROPOSAL, row.id) if data.get("ai") else None}

    def candidates(db, identity, group_row):
        result = []
        sid = group_row.value["school_id"]
        rows = list(db.scalars(select(Record).where(Record.tenant_id == identity["tenant_id"], Record.kind == "school_membership", Record.parent_id == sid).order_by(Record.id).limit(500)))
        rows += list(db.scalars(select(Record).where(Record.tenant_id == identity["tenant_id"], Record.kind == "agent_placement", Record.parent_id == sid).order_by(Record.id).limit(100)))
        seen = set()
        for row in rows:
            if row.owner_id in seen:
                continue
            seen.add(row.owner_id)
            try:
                item = principal(db, identity, row.owner_id, sid, settings)
                # A roster is display-only. Derive class eligibility from actual
                # enrollment, never manufacture a credential-bearing identity.
                if group_row.value.get("course_id"):
                    course = get_record(db, identity["tenant_id"], group_row.value["course_id"], "course")
                    assigned = db.scalar(select(Record).where(Record.tenant_id == identity["tenant_id"], Record.kind == "enrollment", Record.parent_id == course.id, Record.owner_id == item["id"]))
                    if item["role"] not in {"school", "admin"} and not (assigned and normalized_role(assigned.value.get("role")) == item["role"] or item["role"] == "teacher" and course.owner_id == item["id"]):
                        continue
                if item["principal_kind"] == "agent" and not group_row.value.get("course_id"):
                    continue
                member = member_row(db, group_row, item["id"])
                status = "active" if group_row.value["visibility"] == "class" or item["id"] == group_row.owner_id else member.value.get("status", "none") if member else "none"
                result.append({**item, "membership": status})
            except HTTPException:
                continue
        return result

    def member_catalog(db, identity, group_row):
        acl = access(db, identity, group_row, settings, read=True)
        available = candidates(db, identity, group_row)
        # Current members may see names of the group they joined; only human
        # managers can discover the rest of their current school/course roster.
        return {"members": [item for item in available if item["membership"] in {"active", "requested", "invited"} and (acl["manager"] or item["membership"] == "active")], "candidates": available if acl["manager"] and acl["human"] and group_row.value["visibility"] != "class" else [], "can_manage": acl["manager"] and acl["human"] and group_row.value["visibility"] != "class"}

    def index(db, identity, school_id, discover=False):
        role = require_school(db, identity, school_id, settings)

        def field(model, name):
            return _json_field(db, model, name)

        course_query = _eligible_course_ids(db, identity, school_id, role)
        query = select(Record).where(Record.tenant_id == identity["tenant_id"], Record.kind == GROUP, Record.parent_id == school_id)
        membership = aliased(Record)
        joined = select(membership.parent_id).where(membership.tenant_id == identity["tenant_id"], membership.kind == MEMBER, membership.owner_id == identity["id"], field(membership, "status").in_(["active", "requested", "invited"]))
        group_course = field(Record, "course_id")
        visibility = field(Record, "visibility")
        if role not in {"admin", "school"}:
            query = query.where(or_(group_course.is_(None), group_course.in_(course_query)))
            if identity.get("principal_kind", "human") == "agent":
                query = query.where(group_course.is_not(None))
            query = query.where(or_(visibility.in_(["class", "school"]), Record.owner_id == identity["id"], Record.id.in_(joined)))
            if not discover:
                query = query.where(or_(visibility == "class", Record.owner_id == identity["id"], Record.id.in_(joined)))
        # Filter inaccessible private/classes in SQL BEFORE the bounded page.
        # Required enrolled-class groups sort first, so unrelated older rooms
        # cannot displace them. Truncation is explicit for unusually large schools.
        required_first = field(Record, "required") == (1 if db.bind.dialect.name == "sqlite" else "true")
        query = query.order_by(case((required_first, 0), else_=1), Record.created_at, Record.id).limit(MAX_GROUPS + 1)
        records = list(db.scalars(query))
        groups = []
        for row in records[:MAX_GROUPS]:
            try:
                groups.append(describe(db, identity, row, settings))
            except HTTPException:
                continue
        courses = []
        course_rows = list(db.scalars(select(Record).where(Record.tenant_id == identity["tenant_id"], Record.kind == "course", Record.id.in_(course_query)).order_by(Record.id).limit(501)))
        for row in course_rows[:500]:
            try:
                course_access(db, identity, row.id, school_id, settings)
                courses.append({"id": row.id, "title": row.value.get("title", "Class")})
            except HTTPException:
                continue
        return {"groups": groups, "can_create": role in {"admin", "school", "teacher"}, "transport": {"enabled": bool(getattr(settings, "core_chat_enabled", False))}, "courses": courses, "school_id": school_id, "truncated": len(records) > MAX_GROUPS, "courses_truncated": len(course_rows) > 500}

    def provision(request, identity, group_id):
        # The durable marker commits before I/O. Bootstrap can create a required
        # class registry but cannot repeat an already-marked Core room creation.
        if not getattr(settings, "core_chat_enabled", False):
            return
        accepted = fresh(request, identity)
        with database.write() as db:
            row = group(db, accepted, group_id)
            data = row.value
            if data["status"] == "ready" or data.get("provision_started"):
                return
            data["provision_started"] = now()
            data["status"], data["error"] = "pending", None
            row.set(data)
            operation = key(COMMAND, row.id, "create")
            if not db.get(Record, operation):
                put(db, identity["tenant_id"], COMMAND, operation, row.id, identity["id"], {"operation": "create", "author": author(identity), "authority": _authority(identity), "created_at": now(), "topic": data["topic"], "title": data["title"]}, data["school_id"])
            audit_actor(db, accepted, "chat.group_create_send_marked", {"group_id": row.id, "school_id": data["school_id"], "course_id": data.get("course_id"), "command_id": operation, "send_marker": data["provision_started"], "wire_request": {"title": data["title"], "topic": data["topic"], "kind": "group", "ai_enabled": True}}, row.id)
        result, failure = None, None
        try:
            result = transport().create_group(data["title"], data["topic"])
            if not isinstance(result, dict) or not isinstance(result.get("conversation_id"), str) or not result["conversation_id"]:
                raise ValueError()
        except Exception as error:
            failure = "uncertain" if not isinstance(error, CoreChatFailure) or error.uncertain else "unavailable"
        with database.write() as db:
            row = get_record(db, identity["tenant_id"], group_id, GROUP)
            current = row.value
            if failure:
                current["create_uncertain"] = failure == "uncertain"
                current["status"], current["error"] = failure, "Core group creation is unresolved. An authorized manager must explicitly recover the exact group; creation will not be repeated automatically."
            else:
                current["status"], current["room_id"], current["error"] = "ready", result["conversation_id"], None
                put(db, identity["tenant_id"], RECEIPT, key(RECEIPT, operation), row.id, identity["id"], {"command_id": operation, "status": "delivered", "created_at": now(), "room_id": result["conversation_id"]}, current["school_id"])
            row.set(current)
            audit_actor(db, identity, "chat.group_provisioned" if not failure else "chat.group_create_unresolved", {"group_id": row.id, "school_id": current["school_id"], "course_id": current.get("course_id"), "command_id": operation, "status": current["status"], "creation_receipt": {field: result[field] for field in ("organization_id", "conversation_id") if result and field in result} if not failure else None}, row.id)
        fresh(request, identity)
        with database.factory() as db:
            group(db, identity, group_id)

    def revalidate_command(db, identity, group_row, row):
        access(db, identity, group_row, settings, read=True)
        if not getattr(settings, "core_chat_enabled", False):
            raise HTTPException(503, "NCE Core Chat is disabled")
        data = row.value
        if canonical(data["group_scope"]) != canonical(_scope(group_row)) or group_row.value["status"] != "ready":
            raise HTTPException(409, "Group context changed; original message cannot be sent")
        if not data.get("ai") and row.owner_id != identity["id"]:
            raise HTTPException(403, "Only the original author can send this message")
        if data.get("ai"):
            placement_row = get_record(db, identity["tenant_id"], data["authority"].get("placement_id", ""), "agent_placement")
            if data.get("agent_placement_sha256") != _hash(placement_row.data):
                raise HTTPException(409, "Original agent placement changed after this proposal")
        if data.get("reply_to"):
            referenced = db.get(Record, key(RECEIPT, identity["tenant_id"], group_row.id, data["reply_to"]))
            if not referenced or referenced.kind != RECEIPT or referenced.tenant_id != identity["tenant_id"] or referenced.parent_id != group_row.id or referenced.value.get("record_type") != "delivery":
                raise HTTPException(409, "Reply target is not an admitted group message")

    def approved_agent(db, group_row, row):
        """Fresh read-only agent binding authority, independent of reviewer."""
        from .agent_auth import placement
        identity = row.value["authority"]
        placement_row = get_record(db, identity["tenant_id"], identity.get("placement_id", ""), "agent_placement")
        if row.value.get("agent_placement_sha256") != _hash(placement_row.data):
            raise HTTPException(409, "Original agent placement changed after this proposal")
        approved = placement(auth, identity.get("placement_id"))
        if approved["core_consumer_service_id"] != settings.core_service_id or approved["school_id"] != group_row.value["school_id"] or identity["id"] != "agent:" + approved["core_agent_id"] or any(identity.get(claim) != approved.get(field) for claim, field in (("account_id", "core_agent_id"), ("binding_id", "core_binding_id"), ("consumer_service_id", "core_consumer_service_id"), ("environment", "environment"), ("task_id", "task_id"))):
            raise HTTPException(403, "Original agent teaching authority changed")
        access(db, identity, group_row, settings, read=True)
        payload = {"binding_id": approved["core_binding_id"], "environment": approved["environment"], "task_id": approved.get("task_id"), "permission": "academy-teach", "resource_type": approved["core_resource_type"], "resource_id": approved["core_resource_id"]}
        claims = auth.core_agent("/api/platform/v1/agent-bindings/authorize/", payload, approved.get("purpose", ""))
        clock = time.time()
        exact = {"active": True, "principal_kind": "agent", "iss": settings.core_issuer, "aud": "urn:core:service:" + settings.core_service_id, "organization_id": settings.core_organization_id, "agent_id": approved["core_agent_id"], "sub": "urn:core:agent:" + approved["core_agent_id"], "binding_id": approved["core_binding_id"], "consumer_service_id": approved["core_consumer_service_id"], "environment": approved["environment"], "task_id": approved.get("task_id"), **{field: payload[field] for field in ("permission", "resource_type", "resource_id")}}
        if not isinstance(claims, dict) or claims.get("active") is not True or any(claims.get(field) != value for field, value in exact.items()) or type(claims.get("iat")) is not int or not clock - 60 <= claims["iat"] <= clock + 5 or type(claims.get("exp")) is not int or not clock < claims["exp"] <= claims["iat"] + 60 or not isinstance(claims.get("permissions"), list) or len(claims["permissions"]) > 256 or any(not isinstance(value, str) or not 0 < len(value) <= 200 for value in claims["permissions"]) or not {"academy-use", "academy-teach"} <= set(claims["permissions"]) or "admin" in claims["permissions"]:
            raise HTTPException(403, "Original agent live teaching authority unavailable")
        if approved != placement(auth, identity["placement_id"]):
            raise HTTPException(409, "Agent placement changed during publication review")
        return {"placement_id": identity["placement_id"], "agent_id": claims["agent_id"], "binding_id": claims["binding_id"], "checked_at": now()}

    def send(request, identity, group_id, command_id, *, retry=False, review=False):
        accepted = fresh(request, identity)
        # Agent binding checks involve Core I/O, so do them outside the database
        # write transaction. Local scope is checked again before the marker.
        with database.factory() as db:
            group_row = group(db, accepted, group_id, read=True, manage=review)
            row = command(db, accepted, group_row, command_id, review=review)
            revalidate_command(db, accepted, group_row, row)
            data = row.value
            if data.get("ai"):
                if not review or accepted.get("principal_kind", "human") != "human" or school_role(db, accepted, group_row.value["school_id"], settings) not in {"admin", "school"}:
                    raise HTTPException(403, "Human publication approval required")
                agent_authority = approved_agent(db, group_row, row)
            else:
                agent_authority = None
        accepted = fresh(request, identity)
        with database.write() as db:
            group_row = group(db, accepted, group_id, read=True, manage=review)
            row = command(db, accepted, group_row, command_id, review=review)
            revalidate_command(db, accepted, group_row, row)
            if receipt(db, row):
                return project_command(db, accepted, group_row, row, review=review)
            previous = attempts(db, row)
            if previous:
                if not retry:
                    return project_command(db, accepted, group_row, row, review=review)
                if previous[0].value["status"] not in {"pending", "failed", "uncertain"} or len(previous) >= 10:
                    raise HTTPException(409, "This message cannot be retried")
            if data.get("ai"):
                access(db, data["authority"], group_row, settings, read=True)
            attempt_id = uid()
            attempt_data = {"status": "pending", "send_marker": now(), "error": None, "command_id": row.id, "reviewer": author(accepted) if review else None, "agent_authority": agent_authority}
            put(db, identity["tenant_id"], ATTEMPT, attempt_id, row.id, row.owner_id, attempt_data, data["school_id"])
            audit_actor(db, accepted, "chat.message_send_marked", {"group_id": group_id, "school_id": data["school_id"], "course_id": data.get("course_id"), "command_id": row.id, "attempt_id": attempt_id, "author": data["author"], "text_sha256": data["text_sha256"], "send_marker": attempt_data["send_marker"], "human_approval": review, "agent_authority": agent_authority, "command_snapshot": _command_evidence(row.id, data)}, group_id)
        result, failure = None, None
        try:
            result = transport().send(data["group_scope"]["room_id"], data["request_id"], data["text"], ai=data["ai"], agent_name=_wire_request(data)["agent_name"], reply_to=data.get("reply_to"))
            message = result["message"]
            install = getattr(settings, "core_chat_installation_id", "")
            if not isinstance(message, dict) or message.get("text") != data["text"] or message.get("actor_kind") != ("ai" if data["ai"] else "application") or message.get("actor_id") != install or message.get("via_installation_id") != install or message.get("origin_issuer") != settings.core_issuer or message.get("reply_to") != data.get("reply_to") or data["ai"] and message.get("agent_name") != _wire_request(data)["agent_name"] or not isinstance(message.get("id"), str) or type(message.get("cursor")) is not int:
                raise ValueError()
        except Exception as error:
            failure = "uncertain" if not isinstance(error, CoreChatFailure) or error.uncertain else "failed"
        with database.write() as db:
            group_row = get_record(db, identity["tenant_id"], group_id, GROUP)
            row = get_record(db, identity["tenant_id"], command_id, COMMAND)
            attempt = get_record(db, identity["tenant_id"], attempt_id, ATTEMPT)
            evidence = attempt.value
            evidence["status"] = failure or "delivered"
            evidence["completed_at"] = now()
            evidence["error"] = "Message delivery is unresolved. Check this receipt and explicitly retry the original command if allowed; it will not be sent automatically." if failure else None
            attempt.set(evidence)
            if not failure:
                message = result["message"]
                delivery = {"record_type": "delivery", "command_id": row.id, "message_id": message["id"], "message": message, "author": data["author"], "text_sha256": data["text_sha256"], "ai": data["ai"], "human_approval": review, "reviewer": author(identity) if review else None, "created_at": now()}
                identifier = key(RECEIPT, identity["tenant_id"], group_id, message["id"])
                prior = db.get(Record, identifier)
                if prior and (prior.kind != RECEIPT or prior.tenant_id != identity["tenant_id"] or prior.parent_id != group_id or prior.value.get("command_id") != row.id or canonical(prior.value.get("message")) != canonical(message) or canonical(prior.value.get("author")) != canonical(data["author"])):
                    # A duplicated provider ID can never adopt a different local
                    # author or text. Preserve an uncertain attempt for review.
                    evidence["status"], evidence["error"] = "uncertain", "Core message identity conflicted with an earlier delivery; output withheld."
                    attempt.set(evidence)
                else:
                    if not prior:
                        put(db, identity["tenant_id"], RECEIPT, identifier, group_id, row.owner_id, delivery, data["school_id"])
                    if not receipt(db, row):
                        put(db, identity["tenant_id"], RECEIPT, key(RECEIPT, row.id), group_id, row.owner_id, {"record_type": "outcome", "command_id": row.id, "status": "delivered", "message_id": message["id"], "created_at": now(), "error": None}, data["school_id"])
            audit_actor(db, identity, "chat.message_delivered" if evidence["status"] == "delivered" else "chat.message_unresolved", {"group_id": group_id, "school_id": data["school_id"], "course_id": data.get("course_id"), "command_id": row.id, "attempt_id": attempt_id, "status": evidence["status"], "message_id": result["message"]["id"] if evidence["status"] == "delivered" else None, "author": data["author"], "text": data["text"], "text_sha256": data["text_sha256"], "command_snapshot": _command_evidence(row.id, data), "core_delivery": _delivery_evidence(result) if evidence["status"] == "delivered" else None}, group_id)
        accepted = fresh(request, identity)
        with database.factory() as db:
            group_row = group(db, accepted, group_id, read=True, manage=review)
            row = command(db, accepted, group_row, command_id, review=review)
            if data.get("ai"):
                approved_agent(db, group_row, row)
            return project_command(db, accepted, group_row, row, review=review)

    @app.get("/api/chat/groups")
    def list_groups(school_id: str, discover: bool = False, identity=Depends(actor)):
        with database.factory() as db:
            return index(db, identity, school_id, discover)

    @app.post("/api/chat/bootstrap")
    def bootstrap(body: ChatBootstrapInput, request: Request, identity=Depends(actor)):
        identifiers = []
        with database.write() as db:
            role = require_school(db, identity, body.school_id, settings)
            eligible = _eligible_course_ids(db, identity, body.school_id, role)
            course_rows = list(db.scalars(select(Record).where(Record.tenant_id == identity["tenant_id"], Record.kind == "course", Record.id.in_(eligible)).order_by(Record.id).limit(501)))
            for course in course_rows[:500]:
                try:
                    course_access(db, identity, course.id, body.school_id, settings)
                except HTTPException:
                    continue
                row = ensure_class_group(db, identity, course, body.school_id, audit_actor)
                if not row.value.get("provision_started") and len(identifiers) < 2:
                    identifiers.append(row.id)
        for identifier in identifiers:
            provision(request, identity, identifier)
        with database.factory() as db:
            result = index(db, fresh(request, identity), body.school_id)
            result["bootstrap_truncated"] = len(course_rows) > 500
            return result

    @app.post("/api/chat/groups", status_code=201)
    def create(body: ChatGroupInput, request: Request, identity=Depends(actor)):
        identifier = key(GROUP, identity["tenant_id"], identity["id"], body.idempotency_key)
        payload = body.model_dump()
        with database.write() as db:
            role = require_school(db, identity, body.school_id, settings, {"admin", "school", "teacher"})
            if body.course_id:
                course_access(db, identity, body.course_id, body.school_id, settings, teaching=True)
            if identity.get("principal_kind", "human") == "agent" and not body.course_id:
                raise HTTPException(403, "Agent teachers create groups only in their assigned classes")
            prior = db.get(Record, identifier)
            if prior:
                access(db, identity, prior, settings)
                if canonical(prior.value.get("request")) != canonical(payload):
                    raise HTTPException(409, "Group idempotency key was used for another request")
            else:
                count = db.scalar(select(func.count()).select_from(Record).where(Record.tenant_id == identity["tenant_id"], Record.kind == GROUP, Record.parent_id == body.school_id))
                if count >= MAX_GROUPS:
                    raise HTTPException(403, "School group limit reached")
                value = {**payload, "required": False, "status": "pending", "error": None, "room_id": None, "created_at": now(), "topic": "ncacademy:" + identifier, "provision_started": None, "request": payload}
                prior = put(db, identity["tenant_id"], GROUP, identifier, body.school_id, identity["id"], value, body.school_id)
                if body.visibility != "class":
                    membership_change(db, identity, prior, identity["id"], "active", "created", audit_actor)
                audit_actor(db, identity, "chat.group_created", {"group_id": identifier, "school_id": body.school_id, "course_id": body.course_id, "title": body.title, "description": body.description, "visibility": body.visibility, "join_policy": body.join_policy}, identifier)
        provision(request, identity, identifier)
        with database.factory() as db:
            return describe(db, fresh(request, identity), get_record(db, identity["tenant_id"], identifier, GROUP), settings)

    @app.get("/api/chat/groups/{group_id}")
    def detail(group_id: str, identity=Depends(actor)):
        with database.factory() as db:
            return describe(db, identity, group(db, identity, group_id), settings)

    @app.post("/api/chat/groups/{group_id}/join")
    def join(group_id: str, body: EmptyChatInput, identity=Depends(actor)):
        with database.write() as db:
            row = group(db, identity, group_id)
            item = describe(db, identity, row, settings)
            if item["membership"] == "active":
                return item
            if not item["can_join"]:
                raise HTTPException(403, "An invitation or current class membership is required")
            status = "active" if item["membership"] == "invited" or row.value["join_policy"] == "open" else "requested"
            membership_change(db, identity, row, identity["id"], status, "joined" if status == "active" else "requested", audit_actor)
            return describe(db, identity, row, settings)

    @app.post("/api/chat/groups/{group_id}/leave")
    def leave(group_id: str, body: EmptyChatInput, identity=Depends(actor)):
        with database.write() as db:
            row = group(db, identity, group_id)
            item = describe(db, identity, row, settings)
            if not item["can_leave"]:
                raise HTTPException(403, "Required class membership and group ownership cannot be left")
            membership_change(db, identity, row, identity["id"], "none", "left", audit_actor)
            return describe(db, identity, row, settings)

    @app.get("/api/chat/groups/{group_id}/members")
    def members(group_id: str, identity=Depends(actor)):
        with database.factory() as db:
            return member_catalog(db, identity, group(db, identity, group_id, read=True))

    @app.post("/api/chat/groups/{group_id}/members")
    def change_member(group_id: str, body: ChatMemberInput, identity=Depends(actor)):
        with database.write() as db:
            row = group(db, identity, group_id, manage=True)
            acl = access(db, identity, row, settings)
            if not acl["human"] or row.value["visibility"] == "class":
                raise HTTPException(403, "Human managers change optional group membership only")
            available = {item["id"]: item for item in candidates(db, identity, row)}
            if body.principal_id not in available or body.principal_id == row.owner_id:
                raise HTTPException(403, "Choose an eligible current participant other than the owner")
            prior = available[body.principal_id]["membership"]
            if body.action == "approve" and prior != "requested":
                raise HTTPException(409, "This participant has no current join request")
            if body.action == "reject" and prior != "requested":
                raise HTTPException(409, "This participant has no current join request")
            status = {"invite": "invited", "approve": "active", "remove": "none", "reject": "none"}[body.action]
            membership_change(db, identity, row, body.principal_id, status, body.action, audit_actor)
            return member_catalog(db, identity, row)

    @app.get("/api/chat/groups/{group_id}/messages")
    def messages(group_id: str, request: Request, after: int = 0, limit: int = 100, identity=Depends(actor)):
        if after < 0 or after > 2**63 - 1 or limit < 1 or limit > 100:
            raise HTTPException(422, "Choose a bounded message cursor and page size")
        with database.factory() as db:
            row = group(db, identity, group_id, read=True)
            if not getattr(settings, "core_chat_enabled", False):
                raise HTTPException(503, "NCE Core Chat is disabled")
            if row.value["status"] != "ready":
                raise HTTPException(409, "Core group is not ready")
            snapshot = _scope(row)
        try:
            result = transport().messages(snapshot["room_id"], after=after, limit=limit)
        except CoreChatFailure:
            raise HTTPException(503, "Core Chat is unavailable; no local replacement history is served") from None
        accepted = fresh(request, identity)
        with database.write() as db:
            row = group(db, accepted, group_id, read=True)
            if canonical(snapshot) != canonical(_scope(row)):
                raise HTTPException(409, "Group context changed")
            admitted, withheld = [], 0
            for message in result["messages"]:
                receipt_row = db.get(Record, key(RECEIPT, identity["tenant_id"], group_id, message["id"]))
                if not receipt_row or receipt_row.kind != RECEIPT or receipt_row.tenant_id != identity["tenant_id"] or receipt_row.parent_id != group_id:
                    withheld += 1
                    continue
                evidence = receipt_row.value
                original = db.get(Record, evidence.get("command_id", ""))
                if evidence.get("record_type") != "delivery" or canonical(evidence.get("message")) != canonical(message) or not original or original.kind != COMMAND or original.tenant_id != identity["tenant_id"] or original.parent_id != group_id or original.value.get("text_sha256") != _hash(message["text"]) or canonical(original.value.get("author")) != canonical(evidence.get("author")) or evidence.get("ai") and not evidence.get("human_approval"):
                    withheld += 1
                    continue
                admitted.append({"id": message["id"], "sequence": message["cursor"], "text": message["text"], "created_at": message["created"], "author": evidence["author"], "ai": evidence["ai"], "reply_to": message.get("reply_to")})
            audit_actor(db, accepted, "chat.messages_read", {"group_id": group_id, "school_id": row.value["school_id"], "course_id": row.value.get("course_id"), "after": after, "returned": len(admitted), "withheld": withheld}, group_id)
            return {"messages": admitted, "next_after": result["next_cursor"], "has_more": len(result["messages"]) == limit}

    @app.post("/api/chat/groups/{group_id}/messages")
    def create_message(group_id: str, body: ChatMessageInput, request: Request, response: Response, identity=Depends(actor)):
        if len(body.text.encode()) > 16384:
            raise HTTPException(422, "Message is too large")
        identifier = key(COMMAND, identity["tenant_id"], group_id, identity["id"], body.idempotency_key)
        with database.write() as db:
            row = group(db, identity, group_id, read=True)
            if not getattr(settings, "core_chat_enabled", False):
                raise HTTPException(503, "NCE Core Chat is disabled")
            if row.value["status"] != "ready":
                raise HTTPException(409, "Core group is not ready")
            prior = db.get(Record, identifier)
            if prior:
                if prior.kind != COMMAND or prior.tenant_id != identity["tenant_id"] or prior.parent_id != group_id or prior.owner_id != identity["id"] or prior.value.get("text") != body.text or prior.value.get("reply_to") != body.reply_to:
                    raise HTTPException(409, "Message idempotency key was used for another request")
                return project_command(db, identity, row, prior)
            day = datetime.now(timezone.utc).date().isoformat()
            operation = func.json_extract(Record.data, "$.operation") if db.bind.dialect.name == "sqlite" else cast(Record.data, JSON)["operation"].as_string()
            daily = db.scalar(select(func.count()).select_from(Record).where(Record.tenant_id == identity["tenant_id"], Record.kind == COMMAND, Record.owner_id == identity["id"], Record.created_at >= day, operation == "message"))
            if daily >= MAX_DAILY_MESSAGES:
                raise HTTPException(403, "Daily chat message allowance reached")
            is_ai = identity.get("principal_kind", "human") == "agent"
            data = {"operation": "message", "idempotency_key": body.idempotency_key, "request_id": uid(), "school_id": row.value["school_id"], "course_id": row.value.get("course_id"), "group_scope": _scope(row), "text": body.text, "text_sha256": _hash(body.text), "reply_to": body.reply_to, "author": author(identity), "authority": _authority(identity), "ai": is_ai, "created_at": now()}
            if is_ai:
                placement_row = get_record(db, identity["tenant_id"], identity.get("placement_id", ""), "agent_placement")
                data["agent_placement_sha256"] = _hash(placement_row.data)
            new = put(db, identity["tenant_id"], COMMAND, identifier, group_id, identity["id"], data, data["school_id"])
            revalidate_command(db, identity, row, new)
            if is_ai:
                put(db, identity["tenant_id"], PROPOSAL, key(PROPOSAL, identifier), group_id, identity["id"], {"command_id": identifier, "school_id": data["school_id"], "course_id": data["course_id"], "created_at": now()}, data["school_id"])
                response.status_code = 202
            audit_actor(db, identity, "chat.agent_message_proposed" if is_ai else "chat.message_admitted", {"group_id": group_id, "command_id": identifier, "school_id": data["school_id"], "course_id": data["course_id"], "author": data["author"], "text": data["text"], "text_sha256": data["text_sha256"], "reply_to": body.reply_to, "command_snapshot": _command_evidence(identifier, data)}, group_id)
            if is_ai:
                return project_command(db, identity, row, new)
        return send(request, identity, group_id, identifier)

    @app.get("/api/chat/groups/{group_id}/commands")
    def lookup(group_id: str, idempotency_key: str, identity=Depends(actor)):
        if not 8 <= len(idempotency_key) <= 100:
            raise HTTPException(422, "Invalid chat key")
        with database.factory() as db:
            row = group(db, identity, group_id, read=True)
            item = command(db, identity, row, key(COMMAND, identity["tenant_id"], group_id, identity["id"], idempotency_key))
            return project_command(db, identity, row, item)

    @app.get("/api/chat/groups/{group_id}/commands/{command_id}")
    def command_detail(group_id: str, command_id: str, identity=Depends(actor)):
        with database.factory() as db:
            row = group(db, identity, group_id, read=True)
            return project_command(db, identity, row, command(db, identity, row, command_id))

    @app.post("/api/chat/groups/{group_id}/commands/{command_id}/retry")
    def retry_message(group_id: str, command_id: str, body: EmptyChatInput, request: Request, identity=Depends(actor)):
        with database.factory() as db:
            row = group(db, identity, group_id, read=True)
            item = command(db, identity, row, command_id)
            if item.value.get("ai"):
                raise HTTPException(403, "Human publication review must retry an agent proposal")
        return send(request, identity, group_id, command_id, retry=True)

    @app.get("/api/chat/groups/{group_id}/proposals")
    def proposals(group_id: str, identity=Depends(actor)):
        with database.factory() as db:
            row = group(db, identity, group_id, read=True, manage=True)
            if identity.get("principal_kind", "human") != "human" or school_role(db, identity, row.value["school_id"], settings) not in {"admin", "school"}:
                raise HTTPException(403, "Human School/Admin publication review required")
            result = []
            for proposed in db.scalars(select(Record).where(Record.tenant_id == identity["tenant_id"], Record.kind == PROPOSAL, Record.parent_id == group_id).order_by(Record.created_at.desc()).limit(100)):
                item = command(db, identity, row, proposed.value["command_id"], review=True)
                result.append(project_command(db, identity, row, item, review=True))
            return {"proposals": result}

    @app.post("/api/chat/groups/{group_id}/proposals/{proposal_id}/review")
    def review(group_id: str, proposal_id: str, body: ChatReviewInput, request: Request, identity=Depends(actor)):
        with database.write() as db:
            row = group(db, identity, group_id, read=True, manage=True)
            if identity.get("principal_kind", "human") != "human" or school_role(db, identity, row.value["school_id"], settings) not in {"admin", "school"}:
                raise HTTPException(403, "Human School/Admin publication review required")
            proposed = get_record(db, identity["tenant_id"], proposal_id, PROPOSAL)
            if proposed.parent_id != row.id:
                raise HTTPException(404, "Chat proposal not found")
            item = command(db, identity, row, proposed.value["command_id"], review=True)
            if receipt(db, item):
                return project_command(db, identity, row, item, review=True)
            if body.decision == "reject":
                if attempts(db, item):
                    raise HTTPException(409, "A sent proposal cannot be rejected; inspect its delivery receipt")
                put(db, identity["tenant_id"], RECEIPT, key(RECEIPT, item.id), row.id, item.owner_id, {"record_type": "outcome", "command_id": item.id, "status": "rejected", "created_at": now(), "error": None, "reviewer": author(identity)}, row.value["school_id"])
                audit_actor(db, identity, "chat.agent_message_rejected", {"group_id": row.id, "command_id": item.id, "school_id": row.value["school_id"], "course_id": row.value.get("course_id"), "text_sha256": item.value["text_sha256"]}, row.id)
                return project_command(db, identity, row, item, review=True)
        return send(request, identity, group_id, item.id, retry=True, review=True)

    @app.post("/api/chat/groups/{group_id}/recover")
    def recover(group_id: str, body: EmptyChatInput, request: Request, identity=Depends(actor)):
        with database.factory() as db:
            row = group(db, identity, group_id, manage=True)
            if row.value["status"] == "ready":
                return describe(db, identity, row, settings)
            data = row.value
        # Core's joined-conversation list has no owner/sole-participant proof.
        # A matching title/topic can be forged by a native Core participant; it
        # cannot authorize exposing student messages in that room. An uncertain
        # create therefore stays fail-closed for operator reconciliation.
        if data.get("create_uncertain") or data.get("provision_started") and data["status"] != "unavailable":
            raise HTTPException(409, "Core group creation is uncertain. Core does not provide the ownership and sole-participant proof needed for safe automatic recovery; an operator must reconcile the original creation. No new room was created.")
        accepted = fresh(request, identity)
        with database.write() as db:
            row = group(db, accepted, group_id, manage=True)
            current = row.value
            if current.get("create_uncertain") or current.get("provision_started") and current["status"] != "unavailable":
                raise HTTPException(409, "The original creation must be reconciled by an operator")
            current["provision_started"] = None
            row.set(current)
            audit_actor(db, accepted, "chat.group_connect_requested", {"group_id": row.id, "school_id": current["school_id"], "course_id": current.get("course_id"), "known_unsent": True}, row.id)
        provision(request, accepted, group_id)
        with database.factory() as db:
            return describe(db, fresh(request, identity), get_record(db, identity["tenant_id"], group_id, GROUP), settings)
