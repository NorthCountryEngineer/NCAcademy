"""School administration and bounded resource descriptors; never credential minting."""
import hashlib
from fastapi import Depends, HTTPException, Request
from fastapi.responses import JSONResponse
from sqlalchemy import select
from .db import Record, RecordScope, Audit, audit_dict, canonical, uid, now
from .permissions import global_admin, school_role, require_school, scope_id, bind_scope, membership_id, normalized_role
from .schemas import SchoolInput, MembershipInput, AgentPlacementInput, ResourceInput, AssignmentInput, ReviewInput


def register_school_api(app, database, auth, settings, actor, staged_change, validate_binding, audit_actor):
    def find(db, identity, record_id, kind):
        row = db.get(Record, record_id)
        if not row or row.tenant_id != identity["tenant_id"] or row.kind != kind:
            raise HTTPException(404, "Record not found")
        return row

    def records(db, identity, kind, parent=None):
        query = select(Record).where(Record.tenant_id == identity["tenant_id"], Record.kind == kind)
        if parent is not None:
            query = query.where(Record.parent_id == parent)
        return list(db.scalars(query.order_by(Record.created_at, Record.id)))

    def add(db, identity, kind, data, school_id, owner="", parent=None):
        row = Record(id=uid(), tenant_id=identity["tenant_id"], kind=kind, parent_id=parent or school_id, owner_id=owner, data=canonical(data))
        db.add(row)
        db.flush()
        bind_scope(db, row, school_id)
        return row

    def school_dict(db, row, identity):
        role = school_role(db, identity, row.id, settings)
        human = identity.get("principal_kind", "human") == "human"
        manage = role in {"admin", "school"} and human
        return {"id": row.id, **row.value, "role": role, "effective_role": role, "capabilities": {"manage_school": manage, "manage_members": manage, "manage_agents": manage, "manage_resources": role in {"admin", "school", "teacher"}, "create_course": role in {"admin", "school", "teacher"}, "approve_changes": manage, "manage_connections": manage}}

    def course_access(db, identity, course_id, school_id, write=False):
        course = find(db, identity, course_id, "course")
        if scope_id(db, course) != school_id:
            raise HTTPException(403, "Class belongs to another school")
        role = require_school(db, identity, school_id, settings)
        if role not in {"admin", "school"}:
            member = next((r for r in records(db, identity, "enrollment", course_id) if r.owner_id == identity["id"]), None)
            if not (role == "teacher" and course.owner_id == identity["id"] or member and normalized_role(member.value.get("role")) == role) or write and role != "teacher":
                raise HTTPException(403, "Class assignment required")
        return course

    @app.get("/api/schools")
    def schools(identity=Depends(actor)):
        with database.factory() as db:
            return [school_dict(db, row, identity) for row in records(db, identity, "school") if school_role(db, identity, row.id, settings)]

    @app.post("/api/schools", status_code=201)
    def create_school(body: SchoolInput, identity=Depends(actor)):
        if not global_admin(identity):
            raise HTTPException(403, "Administrator permission required")
        with database.write() as db:
            row = Record(id=uid(), tenant_id=identity["tenant_id"], kind="school", data=canonical(body.model_dump()))
            db.add(row)
            db.flush()
            audit_actor(db, identity, "school.created", {"school_id": row.id, **row.value})
            return school_dict(db, row, identity)

    @app.put("/api/schools/{school_id}")
    def update_school(school_id: str, body: SchoolInput, identity=Depends(actor)):
        with database.write() as db:
            require_school(db, identity, school_id, settings, {"admin", "school"}, human=True)
            row = find(db, identity, school_id, "school")
            core_resource_id = body.core_resource_id if "core_resource_id" in body.model_fields_set else row.value.get("core_resource_id")
            if core_resource_id != row.value.get("core_resource_id") and not global_admin(identity):
                raise HTTPException(403, "Only Admin assigns Core resource references")
            if core_resource_id != row.value.get("core_resource_id") and any(p.value.get("active") for p in records(db, identity, "agent_placement", school_id)):
                raise HTTPException(409, "Deactivate agent placements before changing the school Core resource")
            row.set({**body.model_dump(), "core_resource_id": core_resource_id, **({"external_audit_required": True} if row.value.get("external_audit_required") else {})})
            audit_actor(db, identity, "school.updated", {"school_id": school_id, **row.value})
            return school_dict(db, row, identity)

    @app.get("/api/schools/{school_id}/members")
    def members(school_id: str, identity=Depends(actor)):
        with database.factory() as db:
            require_school(db, identity, school_id, settings, {"admin", "school"}, human=True)
            result = []
            for row in records(db, identity, "school_membership", school_id):
                user = db.get(Record, row.owner_id)
                result.append({"principal_id": row.owner_id, "name": user.value.get("name", "Unavailable member") if user else "Unavailable member", "role": normalized_role(row.value["role"]), "active": row.value.get("active", False), "principal_kind": "human"})
            return result

    @app.put("/api/schools/{school_id}/members/{principal_id}")
    def save_member(school_id: str, principal_id: str, body: MembershipInput, identity=Depends(actor)):
        with database.write() as db:
            require_school(db, identity, school_id, settings, {"admin", "school"}, human=True)
            user = find(db, identity, principal_id, "user")
            current = db.get(Record, membership_id(school_id, principal_id))
            if (body.role == "school" or current and current.value.get("role") == "school") and not global_admin(identity):
                raise HTTPException(403, "Only Admin grants or changes School administrators")
            if user.value.get("disabled") and body.active:
                raise HTTPException(422, "Disabled principal cannot receive access")
            value = body.model_dump()
            if current:
                current.set(value)
            else:
                current = Record(id=membership_id(school_id, principal_id), tenant_id=identity["tenant_id"], kind="school_membership", parent_id=school_id, owner_id=principal_id, data=canonical(value))
                db.add(current)
            audit_actor(db, identity, "school.membership_updated", {"school_id": school_id, "principal_id": principal_id, **value})
            return {"principal_id": principal_id, "name": user.value["name"], "principal_kind": "human", **value}

    @app.delete("/api/schools/{school_id}/members/{principal_id}")
    def revoke_member(school_id: str, principal_id: str, identity=Depends(actor)):
        with database.write() as db:
            require_school(db, identity, school_id, settings, {"admin", "school"}, human=True)
            row = db.get(Record, membership_id(school_id, principal_id))
            if not row or row.tenant_id != identity["tenant_id"]:
                raise HTTPException(404, "Membership not found")
            if row.value.get("role") == "school" and not global_admin(identity):
                raise HTTPException(403, "Only Admin changes School administrators")
            row.set({**row.value, "active": False})
            audit_actor(db, identity, "school.membership_revoked", {"school_id": school_id, "principal_id": principal_id})
            return {"active": False}

    def placement_dict(row):
        return {"id": row.id, "principal_id": row.owner_id, "role": "teacher", "principal_kind": "agent", **row.value}

    @app.get("/api/schools/{school_id}/agents")
    def agents(school_id: str, identity=Depends(actor)):
        with database.factory() as db:
            require_school(db, identity, school_id, settings, {"admin", "school"}, human=True)
            return [placement_dict(row) for row in records(db, identity, "agent_placement", school_id)]

    def save_placement(db, identity, school_id, body, row=None):
        require_school(db, identity, school_id, settings, {"admin", "school"}, human=True)
        school = find(db, identity, school_id, "school")
        if not school.value.get("core_resource_id") or body.core_resource_id != school.value["core_resource_id"]:
            raise HTTPException(422, "Placement requires the school's registered Core resource reference")
        if row and row.value["core_agent_id"] != body.core_agent_id:
            raise HTTPException(409, "Agent identity is immutable; create another placement")
        value = {**body.model_dump(), "school_id": school_id, "core_resource_type": "academy-school"}
        if row:
            row.set(value)
        else:
            row = add(db, identity, "agent_placement", value, school_id, "agent:" + body.core_agent_id)
        audit_actor(db, identity, "agent.placement_updated", {"school_id": school_id, "placement_id": row.id, "agent_id": row.owner_id, **value})
        return placement_dict(row)

    @app.post("/api/schools/{school_id}/agents", status_code=201)
    def create_agent(school_id: str, body: AgentPlacementInput, identity=Depends(actor)):
        with database.write() as db:
            return save_placement(db, identity, school_id, body)

    @app.put("/api/schools/{school_id}/agents/{placement_id}")
    def update_agent(school_id: str, placement_id: str, body: AgentPlacementInput, identity=Depends(actor)):
        with database.write() as db:
            row = find(db, identity, placement_id, "agent_placement")
            if row.parent_id != school_id:
                raise HTTPException(404, "Placement not found")
            return save_placement(db, identity, school_id, body, row)

    @app.delete("/api/schools/{school_id}/agents/{placement_id}")
    def revoke_agent(school_id: str, placement_id: str, identity=Depends(actor)):
        with database.write() as db:
            require_school(db, identity, school_id, settings, {"admin", "school"}, human=True)
            row = find(db, identity, placement_id, "agent_placement")
            if row.parent_id != school_id:
                raise HTTPException(404, "Placement not found")
            row.set({**row.value, "active": False})
            audit_actor(db, identity, "agent.placement_revoked", {"school_id": school_id, "placement_id": placement_id})
            return {"active": False}

    def resource_access(db, identity, row, write=False):
        sid = scope_id(db, row)
        role = require_school(db, identity, sid, settings)
        data = row.value
        if data["audience"] == "teachers" and role == "student":
            raise HTTPException(403, "Teaching resource required")
        if role == "student" and data["kind"] == "text" and not data.get("course_id"):
            school = db.get(Record, sid)
            if school.value.get("external_audit_required"):
                raise HTTPException(403, "Attach this resource to a class and complete external review before student use")
        if data.get("course_id"):
            course_access(db, identity, data["course_id"], sid, write)
            if role == "student" and data["kind"] == "text":
                from .external_review import filter_reviewed_documents
                from .standards import snapshot_material
                course = db.get(Record, data["course_id"])
                documents = [item for item in snapshot_material(db, course, sid, external=False)["documents"] if item["id"] == "resource:" + row.id]
                school = db.get(Record, sid)
                if school.value.get("external_audit_required") and not filter_reviewed_documents(db, course, sid, documents):
                    raise HTTPException(403, "Current external review and publication approval required")
        elif write and role not in {"admin", "school"}:
            raise HTTPException(403, "Teachers manage resources only for their assigned class")
        if write and role == "student":
            raise HTTPException(403, "Student resources are read-only")
        return sid

    def resource_dict(row, sid, db, identity):
        try:
            resource_access(db, identity, row, True)
            can_manage = row.value["kind"] != "core" or global_admin(identity)
        except HTTPException:
            can_manage = False
        return {"id": row.id, "school_id": sid, **row.value, "can_manage": can_manage}

    def authorize_descriptor(request, identity, value):
        if value["kind"] == "core":
            auth.authorize_resource(request, identity, value["permission"], value["core_resource_type"], value["core_resource_id"])

    @app.get("/api/schools/{school_id}/resources")
    def resources(school_id: str, request: Request, identity=Depends(actor)):
        with database.write() as db:
            require_school(db, identity, school_id, settings)
            result = []
            for row in records(db, identity, "resource"):
                if scope_id(db, row) != school_id:
                    continue
                try:
                    resource_access(db, identity, row)
                    authorize_descriptor(request, identity, row.value)
                except HTTPException as error:
                    if error.status_code in {403, 404}:
                        continue
                    raise
                result.append(resource_dict(row, school_id, db, identity))
            audit_actor(db, identity, "resource.listed", {"school_id": school_id, "count": len(result)})
            return result

    def validate_resource(db, identity, school_id, body, existing=None):
        role = require_school(db, identity, school_id, settings, {"admin", "school", "teacher"})
        if body.kind == "core":
            if not global_admin(identity):
                raise HTTPException(403, "Only Admin assigns Core-backed resources")
            if not body.core_resource_type or not body.core_resource_id or body.content:
                raise HTTPException(422, "Core descriptor requires a registered resource reference and no credential/content")
        elif body.core_resource_type or body.core_resource_id:
            raise HTTPException(422, "Text resources cannot contain Core bindings")
        if body.course_id:
            course_access(db, identity, body.course_id, school_id, True)
        elif role == "teacher":
            raise HTTPException(403, "Teachers manage resources only for their assigned class")
        if existing:
            resource_access(db, identity, existing, True)
            if existing.value.get("kind") == "core" and not global_admin(identity):
                raise HTTPException(403, "Only Admin changes Core resource assignments")

    @app.post("/api/schools/{school_id}/resources", status_code=201)
    def create_resource(school_id: str, body: ResourceInput, identity=Depends(actor)):
        with database.write() as db:
            validate_resource(db, identity, school_id, body)
            if identity.get("principal_kind") == "agent":
                return staged_change(db, identity, school_id, body.course_id, "resource.create", body.model_dump())
            row = add(db, identity, "resource", body.model_dump(), school_id, identity["id"])
            audit_actor(db, identity, "resource.created", {"school_id": school_id, "course_id": body.course_id, "resource_id": row.id, **row.value})
            return resource_dict(row, school_id, db, identity)

    @app.put("/api/resources/{resource_id}")
    def update_resource(resource_id: str, body: ResourceInput, identity=Depends(actor)):
        with database.write() as db:
            row = find(db, identity, resource_id, "resource")
            sid = scope_id(db, row)
            validate_resource(db, identity, sid, body, row)
            if identity.get("principal_kind") == "agent":
                return staged_change(db, identity, sid, body.course_id, "resource.update", body.model_dump(), row.id)
            row.set(body.model_dump())
            audit_actor(db, identity, "resource.updated", {"school_id": sid, "course_id": body.course_id, "resource_id": row.id, **row.value})
            return resource_dict(row, sid, db, identity)

    @app.delete("/api/resources/{resource_id}")
    def delete_resource(resource_id: str, identity=Depends(actor)):
        with database.write() as db:
            row = find(db, identity, resource_id, "resource")
            sid = resource_access(db, identity, row, True)
            if row.value["kind"] == "core" and not global_admin(identity):
                raise HTTPException(403, "Only Admin removes Core resource assignments")
            if identity.get("principal_kind") == "agent":
                return staged_change(db, identity, sid, row.value.get("course_id"), "resource.delete", {}, row.id)
            db.delete(row)
            db.delete(db.get(RecordScope, resource_id))
            audit_actor(db, identity, "resource.deleted", {"school_id": sid, "resource_id": resource_id})
            return {"deleted": True}

    @app.get("/api/resources/{resource_id}/resolve")
    def resolve_resource(resource_id: str, request: Request, identity=Depends(actor)):
        with database.write() as db:
            row = find(db, identity, resource_id, "resource")
            sid = resource_access(db, identity, row)
            authorize_descriptor(request, identity, row.value)
            audit_actor(db, identity, "resource.resolved", {"school_id": sid, "course_id": row.value.get("course_id"), "resource_id": resource_id, "kind": row.value["kind"]})
            result = resource_dict(row, sid, db, identity)
            if row.value["kind"] == "core":
                result.update(authorized=True, integration_available=False, message="Core authorization verified; this descriptor does not provide an integration transport")
            return result

    @app.get("/api/schools/{school_id}/audit/export")
    def school_audit_export(school_id: str, identity=Depends(actor)):
        with database.write() as db:
            require_school(db, identity, school_id, settings, {"admin", "school"}, human=True)
            audit_actor(db, identity, "audit.school_exported", {"school_id": school_id, "scope": "school_partial", "complete_tenant_chain": False})
            db.flush()
            scoped = {r.record_id for r in db.scalars(select(RecordScope).where(RecordScope.tenant_id == identity["tenant_id"], RecordScope.school_id == school_id))}
            session_ids = set()
            for row in db.scalars(select(Record).where(Record.tenant_id == identity["tenant_id"], Record.kind.in_(("turn", "draft", "submission", "lesson_turn")))):
                if row.id in scoped:
                    prefix = ":lesson:" if row.kind == "lesson_turn" else ":"
                    session_ids.add(hashlib.sha256((identity["tenant_id"] + prefix + row.parent_id + ":" + row.owner_id).encode()).hexdigest())
            events = []
            for row in db.scalars(select(Audit).where(Audit.tenant_id == identity["tenant_id"]).order_by(Audit.sequence)):
                event = audit_dict(row)
                payload = event["payload"]
                principal = payload.get("principal", {})
                declared = payload.get("school_id") or (principal.get("school_id") if isinstance(principal, dict) else None)
                if isinstance(declared, str):
                    matches = declared == school_id
                else:
                    matches = row.session_id in session_ids or any(isinstance(payload.get(key), str) and payload[key] in scoped for key in ("course_id", "assignment_id", "submission_id", "turn_id", "resource_id", "change_id", "placement_id"))
                    if row.event_type == "connection.updated":
                        matches = matches or payload.get("id") in scoped
                if matches:
                    events.append(event)
            return JSONResponse({"tenant_id": identity["tenant_id"], "school_id": school_id, "scope": "school_partial", "complete_tenant_chain": False, "chain_verification": "Requires the independent full tenant export and trusted checkpoint; this filtered view contains sequence gaps", "events": events}, headers={"Content-Disposition": 'attachment; filename="academy-school-audit.json"'})

    @app.get("/api/schools/{school_id}/changes")
    def changes(school_id: str, identity=Depends(actor)):
        with database.factory() as db:
            require_school(db, identity, school_id, settings, {"admin", "school"}, human=True)
            return [{"id": row.id, **row.value} for row in records(db, identity, "change_request", school_id)]

    def decide_change(db, identity, change_id, approve):
        row = find(db, identity, change_id, "change_request")
        value = row.value
        sid = scope_id(db, row)
        require_school(db, identity, sid, settings, {"admin", "school"}, human=True)
        if value["status"] != "pending":
            raise HTTPException(409, "Proposal has already been decided")
        operation = value["operation"]
        target = None
        if approve:
            placement = db.get(Record, value["author_context"].get("placement_id", ""))
            if not placement or placement.tenant_id != identity["tenant_id"] or placement.kind != "agent_placement" or placement.parent_id != sid or placement.owner_id != row.owner_id or not placement.value.get("active"):
                raise HTTPException(409, "Proposal author placement is no longer active")
            if hashlib.sha256(placement.data.encode()).hexdigest() != value.get("placement_snapshot_sha256"):
                raise HTTPException(409, "Proposal author placement changed; request a new proposal")
            if value.get("target_id"):
                previous = db.get(Record, value["target_id"])
                current_snapshot = hashlib.sha256(canonical({"id": previous.id, "parent_id": previous.parent_id, "owner_id": previous.owner_id, "data": previous.data}).encode()).hexdigest() if previous and previous.tenant_id == identity["tenant_id"] else None
                if not current_snapshot or current_snapshot != value.get("target_snapshot_sha256"):
                    raise HTTPException(409, "Proposal target changed; request a new proposal")
            course = None if operation == "course.create" else course_access(db, identity, value["course_id"], sid, True)
            if course:
                assigned = course.owner_id == row.owner_id or any(r.owner_id == row.owner_id and normalized_role(r.value.get("role")) == "teacher" for r in records(db, identity, "enrollment", course.id))
                if not assigned:
                    raise HTTPException(409, "Proposal author is no longer assigned to this class")
            if operation in {"course.create", "course.update"}:
                from .schemas import CourseInput
                content = CourseInput.model_validate(value["payload"])
                if operation == "course.create":
                    target = add(db, identity, "course", {"title": content.title, "description": content.description, "curriculum": content.curriculum.model_dump() if content.curriculum else None}, sid, row.owner_id, parent="")
                    target.parent_id = ""
                    add(db, identity, "enrollment", {"role": "teacher"}, sid, row.owner_id, target.id)
                else:
                    target = course
                    curriculum = target.value.get("curriculum")
                    if "curriculum" in content.model_fields_set:
                        curriculum = content.curriculum.model_dump() if content.curriculum else None
                    target.set({**target.value, "title": content.title, "description": content.description, "curriculum": curriculum})
                from .standards import build_course_corpus
                if target.value.get("curriculum") or "curriculum" in content.model_fields_set:
                    build_course_corpus(db, identity, target, sid, audit_actor)
            elif operation in {"assignment.create", "assignment.update"}:
                content = AssignmentInput.model_validate(value["payload"])
                validate_binding(db, content.tutor_policy.model_dump(), identity, sid)
                if operation == "assignment.create":
                    target = add(db, identity, "assignment", {**content.model_dump(), "version": 1, "authorship": {"agent_id": row.owner_id, "approved_by": identity["id"], "change_id": row.id}}, sid, row.owner_id, course.id)
                else:
                    target = find(db, identity, value["target_id"], "assignment")
                    if target.parent_id != course.id or scope_id(db, target) != sid:
                        raise HTTPException(409, "Proposal target changed scope")
                    target.set({**content.model_dump(), "version": target.value.get("version", 1) + 1, "authorship": {"agent_id": row.owner_id, "approved_by": identity["id"], "change_id": row.id}})
            elif operation == "review.create":
                content = ReviewInput.model_validate(value["payload"])
                submission = find(db, identity, value["target_id"], "submission")
                assignment = find(db, identity, submission.parent_id, "assignment")
                if assignment.parent_id != course.id or scope_id(db, submission) != sid:
                    raise HTTPException(409, "Proposal target changed scope")
                target = add(db, identity, "review", {"feedback": content.feedback, "created_at": now(), "authorship": {"agent_id": row.owner_id, "approved_by": identity["id"], "change_id": row.id}}, sid, row.owner_id, submission.id)
            elif operation in {"resource.create", "resource.update", "resource.delete"}:
                if operation == "resource.delete":
                    target = find(db, identity, value["target_id"], "resource")
                    if scope_id(db, target) != sid or target.value.get("course_id") != course.id or target.value["kind"] != "text":
                        raise HTTPException(409, "Proposal target changed scope")
                    db.delete(target)
                    db.delete(db.get(RecordScope, target.id))
                else:
                    content = ResourceInput.model_validate(value["payload"])
                    if content.kind != "text" or content.course_id != course.id:
                        raise HTTPException(409, "Agents propose class text resources only")
                    validate_resource(db, identity, sid, content)
                    if operation == "resource.create":
                        target = add(db, identity, "resource", content.model_dump(), sid, row.owner_id)
                    else:
                        target = find(db, identity, value["target_id"], "resource")
                        if scope_id(db, target) != sid or target.value.get("course_id") != course.id or target.value["kind"] != "text":
                            raise HTTPException(409, "Proposal target changed scope")
                        target.set(content.model_dump())
            else:
                raise HTTPException(422, "Unsupported proposal operation")
        value.update(status="approved" if approve else "rejected", decided_by=identity["id"], decided_at=now(), published_record_id=target.id if target else None)
        row.set(value)
        audit_actor(db, identity, "change.published" if approve else "change.rejected", {"school_id": sid, "course_id": value["course_id"], "change_id": row.id, "agent_id": row.owner_id, "proposal_operation": operation, "published_record_id": value["published_record_id"], "author_context": value["author_context"]})
        return {"id": row.id, **value}

    @app.post("/api/changes/{change_id}/approve")
    def approve_change(change_id: str, identity=Depends(actor)):
        with database.write() as db:
            return decide_change(db, identity, change_id, True)

    @app.post("/api/changes/{change_id}/reject")
    def reject_change(change_id: str, identity=Depends(actor)):
        with database.write() as db:
            return decide_change(db, identity, change_id, False)
