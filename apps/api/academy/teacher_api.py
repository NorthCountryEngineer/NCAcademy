"""School teacher profiles and immutable approvals, separate from authority.

Profile/class links describe planning. They never create an account, school
membership, class enrollment, Core grant or external-agent admission.
"""
import hashlib

from fastapi import Depends, HTTPException
from pydantic import ValidationError
from sqlalchemy import select

from .db import Record, canonical, now, uid
from .permissions import bind_scope, membership_id, normalized_role, require_school, scope_id
from .teacher_schemas import AIBehavior, TeacherBinding, TeacherCourses, TeacherCreate, TeacherMetadata, TeacherUpdate, TeacherVersion, normalized_picture


METADATA_FIELDS = ("name", "biography", "pronouns", "title", "contact_email", "expertise", "picture", "ai_behavior", "tutor_model")
PLACEMENT_FIELDS = ("core_agent_id", "core_binding_id", "core_consumer_service_id", "environment", "task_id", "core_resource_type", "core_resource_id", "school_id")


def _rows(db, tenant_id, kind, parent_id=None):
    query = select(Record).where(Record.tenant_id == tenant_id, Record.kind == kind)
    if parent_id is not None:
        query = query.where(Record.parent_id == parent_id)
    return list(db.scalars(query.order_by(Record.created_at, Record.id)))


def _find(db, tenant_id, record_id, kind, school_id=None):
    row = db.get(Record, record_id)
    if not row or row.tenant_id != tenant_id or row.kind != kind or school_id is not None and scope_id(db, row) != school_id:
        raise HTTPException(404, "Record not found in this school")
    return row


def _assignments(db, profile):
    return [row for row in _rows(db, profile.tenant_id, "teacher_profile_course", profile.id) if row.value.get("active")]


def _placement_context(placement):
    return {"principal_id": placement.owner_id, **{key: placement.value.get(key) for key in PLACEMENT_FIELDS}}


def _corpus_reference(descriptor):
    # Keep immutable references instead of copying class materials into every
    # profile revision, list response or student-visible context endpoint.
    return {key: descriptor.get(key) for key in ("id", "snapshot_sha256", "configuration_sha256", "catalog_set_sha256", "status", "coverage", "standards_version", "mapping_version", "approval")}


def _binding_state(db, profile, school_id):
    """Current local eligibility only; Core checks still occur on each request."""
    data = profile.value
    if data["kind"] == "human":
        principal_id = data.get("principal_id")
        target = db.get(Record, principal_id) if principal_id else None
        member = db.get(Record, membership_id(school_id, principal_id)) if principal_id else None
        eligible = bool(target and target.tenant_id == profile.tenant_id and target.kind == "user" and not target.value.get("disabled") and member and member.tenant_id == profile.tenant_id and member.kind == "school_membership" and member.parent_id == school_id and member.owner_id == principal_id and member.value.get("active") and normalized_role(member.value.get("role")) == "teacher")
        return principal_id, eligible
    placement_id = data.get("agent_placement_id")
    placement = db.get(Record, placement_id) if placement_id else None
    school = db.get(Record, school_id)
    eligible = bool(placement and placement.tenant_id == profile.tenant_id and placement.kind == "agent_placement" and placement.parent_id == school_id and scope_id(db, placement) == school_id and placement.value.get("active") and placement.owner_id.startswith("agent:") and _placement_context(placement) == data.get("binding_context") and placement.value.get("core_resource_type") == "academy-school" and school and placement.value.get("core_resource_id") == school.value.get("core_resource_id"))
    return placement.owner_id if placement else None, eligible


def _teaches(db, course, principal_id):
    return bool(principal_id and (course.owner_id == principal_id or any(row.owner_id == principal_id and normalized_role(row.value.get("role")) == "teacher" for row in _rows(db, course.tenant_id, "enrollment", course.id))))


def _course_access(db, identity, course, school_id, settings):
    if course.tenant_id != identity["tenant_id"] or course.kind != "course" or scope_id(db, course) != school_id:
        raise HTTPException(404, "Class not found in this school")
    role = require_school(db, identity, school_id, settings)
    if role in {"admin", "school"}:
        return role
    if role == "teacher" and _teaches(db, course, identity["id"]):
        return role
    if role == "student" and any(row.owner_id == identity["id"] and row.value.get("role") == "student" for row in _rows(db, identity["tenant_id"], "enrollment", course.id)):
        return role
    raise HTTPException(403, "Current class membership required")


def _approved(db, profile, school_id):
    data = profile.value
    revision = db.get(Record, data["approved_revision_id"]) if data.get("approved_revision_id") else None
    if data.get("status") != "ready" or data.get("approved_version") != data["version"] or not revision or revision.kind != "teacher_profile_revision" or revision.tenant_id != profile.tenant_id or revision.parent_id != profile.id or scope_id(db, revision) != school_id:
        raise HTTPException(409, "Current teacher profile requires human approval")
    value = revision.value
    digest = hashlib.sha256(canonical(value["snapshot"]).encode()).hexdigest()
    if value.get("version") != data["version"] or value.get("sha256") != digest or digest != data.get("approved_hash"):
        raise HTTPException(409, "Teacher approval integrity check failed")
    snapshot = value["snapshot"]
    if snapshot.get("profile_id") != profile.id or any(snapshot.get(key) != data.get(key) for key in (*METADATA_FIELDS, "kind", "principal_id", "agent_placement_id", "binding_context") if key != "contact_email"):
        raise HTTPException(409, "Current teacher metadata does not match its immutable approval")
    return revision


def _planned_context(db, profile, course, school_id, require_binding=True, require_ready=True):
    from .standards import corpus_descriptor
    link = next((row for row in _assignments(db, profile) if row.value["course_id"] == course.id and scope_id(db, row) == school_id), None)
    principal_id, eligible = _binding_state(db, profile, school_id)
    if not link or require_binding and (not eligible or not _teaches(db, course, principal_id)):
        raise HTTPException(403, "Teacher requires an active identity and existing class teaching authority")
    if link.value.get("curriculum") != course.value.get("curriculum"):
        raise HTTPException(409, "Class curriculum changed; review its teacher assignment again")
    revision = _approved(db, profile, school_id)
    planned = next((item for item in revision.value["snapshot"]["courses"] if item["course_id"] == course.id), None)
    if not planned or planned.get("corpus") != link.value.get("corpus") or planned.get("curriculum") != course.value.get("curriculum"):
        raise HTTPException(409, "Approved teaching context no longer matches this class")
    current = corpus_descriptor(db, course, school_id)
    if (require_ready and not current.get("ready_for_teaching")) or _corpus_reference(current) != link.value.get("corpus"):
        raise HTTPException(409, "Class teaching materials changed; review its teacher assignment again")
    return revision, link


def _assigned_context(db, profile, course, school_id):
    return _planned_context(db, profile, course, school_id, require_binding=True)


def _approval_current(db, profile, school_id):
    """Configuration freshness only: no runtime gates or principal grants."""
    try:
        revision = _approved(db, profile, school_id)
        links = _assignments(db, profile)
        planned_ids = [item["course_id"] for item in revision.value["snapshot"]["courses"]]
        current_ids = [link.value["course_id"] for link in links]
        if len(set(planned_ids)) != len(planned_ids) or len(set(current_ids)) != len(current_ids) or set(current_ids) != set(planned_ids):
            return False
        for link in links:
            if scope_id(db, link) != school_id:
                return False
            course = _find(db, profile.tenant_id, link.value["course_id"], "course", school_id)
            # Profile review can pin pending class materials. Teaching paths
            # retain the default requirement for a separately approved corpus.
            _planned_context(db, profile, course, school_id, require_binding=False, require_ready=False)
        return True
    except (HTTPException, KeyError, TypeError, ValueError):
        return False


def _safe_teacher_context(db, identity, course, school_id, settings, profile_id, ai_only=False):
    role = _course_access(db, identity, course, school_id, settings)
    profile = _find(db, identity["tenant_id"], profile_id, "teacher_profile", school_id)
    if ai_only and profile.value["kind"] != "ai":
        raise HTTPException(422, "Tutoring context requires an AI teacher profile")
    revision, link = _assigned_context(db, profile, course, school_id)
    principal_id, _ = _binding_state(db, profile, school_id)
    if role == "teacher":
        expected_kind = "agent" if profile.value["kind"] == "ai" else "human"
        if principal_id != identity["id"] or identity.get("principal_kind", "human") != expected_kind or expected_kind == "agent" and identity.get("placement_id") != profile.value.get("agent_placement_id"):
            raise HTTPException(403, "Teachers may read only their own linked teaching context")
    snapshot = revision.value["snapshot"]
    return {"profile_id": profile.id, "name": snapshot["name"], "biography": snapshot["biography"], "ai_behavior": snapshot["ai_behavior"], "tutor_model": snapshot.get("tutor_model"), "approved_revision_id": revision.id, "approved_version": revision.value["version"], "approved_hash": revision.value["sha256"], "corpus": link.value.get("corpus")}


def resolve_teacher_context(db, identity, course, school_id, settings, profile_id):
    """Resolve approved AI metadata under existing class authority, including students.

    This is safe selector context, never a new policy or generated student text.
    Caller must separately enforce assignment/student ownership and provider gates.
    """
    return _safe_teacher_context(db, identity, course, school_id, settings, profile_id, ai_only=True)


def resolve_lesson_context(db, identity, course, school_id, settings, profile_id):
    """Managed personas have no principal authority; bound teachers never downgrade."""
    _course_access(db, identity, course, school_id, settings)
    profile = _find(db, identity["tenant_id"], profile_id, "teacher_profile", school_id)
    if profile.value["kind"] != "ai":
        raise HTTPException(422, "Lesson Q&A requires an AI teacher profile")
    bound = bool(profile.value.get("agent_placement_id"))
    revision, link = _planned_context(db, profile, course, school_id, require_binding=bound)
    snapshot = revision.value["snapshot"]
    return {"profile_id": profile.id, "teacher_mode": "core_agent" if bound else "managed", "name": snapshot["name"], "biography": snapshot["biography"], "ai_behavior": snapshot["ai_behavior"], "tutor_model": snapshot.get("tutor_model"), "approved_revision_id": revision.id, "approved_version": revision.value["version"], "approved_hash": revision.value["sha256"], "corpus": link.value.get("corpus")}


def course_teacher_cards(db, identity, course, school_id, settings):
    """Public class cards expose approved metadata only, never contact/binding IDs."""
    _course_access(db, identity, course, school_id, settings)
    result = []
    for profile in _rows(db, identity["tenant_id"], "teacher_profile", school_id):
        if scope_id(db, profile) != school_id:
            continue
        try:
            bound = bool(profile.value.get("agent_placement_id"))
            revision, _ = _planned_context(db, profile, course, school_id, require_binding=profile.value["kind"] == "human" or bound)
        except HTTPException:
            continue
        snapshot = revision.value["snapshot"]
        available = snapshot["kind"] == "ai" and bound and settings.allow_live_models and settings.allow_agent_teachers
        lesson_enabled = snapshot["kind"] == "ai" and settings.allow_live_models and (not bound or settings.allow_agent_teachers)
        model = snapshot.get("tutor_model")
        model_error = False
        if snapshot["kind"] == "ai":
            from .teacher_models import model_policy_context
            try:
                execution = model_policy_context(db, identity, profile, school_id, "lesson")
                if execution:
                    model = {**execution["policy"]["primary"], **execution["policy"]["lesson_policy"]}
            except HTTPException:
                model_error = True
        connection = db.get(Record, model["connection_id"]) if model else None
        configured = bool(not model_error and connection and connection.kind == "connection" and connection.tenant_id == course.tenant_id and scope_id(db, connection) == school_id and connection.value.get("enabled") and connection.value.get("key_cipher") and model["model"] in connection.value.get("models", []))
        result.append({"id": profile.id, "kind": snapshot["kind"], **{key: snapshot[key] for key in ("name", "biography", "pronouns", "title", "picture")}, "context_ready": True, "available_for_tutoring": bool(available), "lesson_mode": ("core_agent" if bound else "managed") if snapshot["kind"] == "ai" else None, "lesson_tutoring_available": bool(lesson_enabled and configured), "lesson_max_input_chars": model["max_input_chars"] if model else None, "lesson_max_turns": model["max_turns"] if model else None, "lesson_unavailable_reason": None if lesson_enabled and configured else "This teacher's approved model connection or lesson access is not enabled.", "curation_available": bool(lesson_enabled and configured and model.get("allow_lesson_curation", False)), "curation_images_allowed": bool(lesson_enabled and configured and model.get("allow_lesson_curation", False) and model.get("allow_assignment_images", False)), "curation_model_label": (str(connection.value.get("name", "School model"))[:200] + " · " + model["model"]) if configured else None, "curation_max_input_chars": model["max_input_chars"] if model else None, "curation_unavailable_reason": None if lesson_enabled and configured and model.get("allow_lesson_curation", False) else "Private lesson curation requires a current human-approved teacher policy and enabled model connection."})
    return result


def course_teacher_profile_ids(db, course, school_id):
    """Planning links for staff configuration, independent of runtime readiness."""
    result = []
    for profile in _rows(db, course.tenant_id, "teacher_profile", school_id):
        if scope_id(db, profile) != school_id:
            continue
        if any(link.value.get("course_id") == course.id and scope_id(db, link) == school_id for link in _assignments(db, profile)):
            result.append(profile.id)
    return result


def course_teacher_profile_descriptors(db, course, school_id, ready_ids):
    """Minimal staff-only planning labels; never private profile configuration."""
    result = []
    for profile_id in course_teacher_profile_ids(db, course, school_id):
        profile = _find(db, course.tenant_id, profile_id, "teacher_profile", school_id)
        result.append({"id": profile.id, "kind": profile.value["kind"], "name": profile.value["name"], "status": profile.value["status"], "approval_current": _approval_current(db, profile, school_id), "context_ready": profile.id in ready_ids})
    return result


def assign_course_teacher_profiles(db, identity, course, school_id, profile_ids, settings, audit_actor, *, expected_profile_ids=None):
    """Synchronize one class's profile links without changing its principal owner.

    Call within the course write transaction. Planning assignment never creates
    principal authority, profile approval, corpus approval or a model policy.
    """
    from .school_curriculum import record_teacher_workflow
    from .standards import build_course_corpus, corpus_descriptor

    require_school(db, identity, school_id, settings, {"admin", "school"}, human=True)
    selected = {}
    for profile_id in profile_ids:
        profile = _find(db, identity["tenant_id"], profile_id, "teacher_profile", school_id)
        if profile.value.get("status") == "archived":
            raise HTTPException(409, "Restore the teacher profile before assigning classes")
        selected[profile.id] = profile
    current = set(course_teacher_profile_ids(db, course, school_id))
    if expected_profile_ids is not None and current != set(expected_profile_ids):
        raise HTTPException(409, "Class teacher assignments changed; reload before saving")
    changed = current.symmetric_difference(selected)
    # In particular, opening and saving an already assigned teacher must not
    # revoke the profile's independent approval or rebuild its course corpus.
    if not changed:
        return
    if any(len(_assignments(db, selected[profile_id])) >= 100 for profile_id in set(selected) - current):
        raise HTTPException(422, "A teacher profile may have at most 100 assigned classes")
    corpus = build_course_corpus(db, identity, course, school_id, audit_actor) if set(selected) - current else None
    for profile_id in sorted(changed):
        profile = selected.get(profile_id) or _find(db, identity["tenant_id"], profile_id, "teacher_profile", school_id)
        links = [link for link in _rows(db, course.tenant_id, "teacher_profile_course", profile.id) if link.value.get("course_id") == course.id]
        if len(links) > 1 or any(scope_id(db, link) != school_id for link in links):
            raise HTTPException(409, "Teacher class assignment integrity check failed")
        if profile_id in selected:
            value = {"course_id": course.id, "active": True, "curriculum": course.value.get("curriculum"), "corpus": _corpus_reference(corpus)}
            if links:
                links[0].set(value)
            else:
                link = Record(id=uid(), tenant_id=course.tenant_id, kind="teacher_profile_course", parent_id=profile.id, owner_id=identity["id"], data=canonical(value))
                db.add(link)
                db.flush()
                bind_scope(db, link, school_id)
        else:
            links[0].set({**links[0].value, "active": False})
        profile.set({**profile.value, "version": profile.value["version"] + 1, "approved_revision_id": None, "approved_version": None, "approved_hash": None, "updated_at": now()})
        assigned = []
        for link in _assignments(db, profile):
            target = _find(db, course.tenant_id, link.value["course_id"], "course", school_id)
            current_corpus = corpus_descriptor(db, target, school_id)
            assigned.append({"course_id": target.id, "title": target.value["title"], "curriculum": target.value.get("curriculum"), "corpus": {**_corpus_reference(current_corpus), "ready_for_teaching": current_corpus.get("ready_for_teaching", False), "gaps": current_corpus.get("gaps", [])}})
        audit_actor(db, identity, "teacher_profile.courses_assigned", {"school_id": school_id, "course_id": course.id, "teacher_profile_id": profile.id, "kind": profile.value["kind"], "version": profile.value["version"], "status": profile.value["status"], "course_ids": [item["course_id"] for item in assigned], "authority_granted": False})
        record_teacher_workflow(db, identity, school_id, profile, assigned, audit_actor, reason="course_configuration")


def register_teacher_api(app, database, auth, settings, actor, audit_actor):
    def manage(db, identity, school_id):
        require_school(db, identity, school_id, settings, {"admin", "school"}, human=True)

    def find(db, identity, school_id, profile_id):
        return _find(db, identity["tenant_id"], profile_id, "teacher_profile", school_id)

    def add(db, identity, school_id, kind, parent_id, value):
        row = Record(id=uid(), tenant_id=identity["tenant_id"], kind=kind, parent_id=parent_id, owner_id=identity["id"], data=canonical(value))
        db.add(row)
        db.flush()
        bind_scope(db, row, school_id)
        return row

    def version(row, expected):
        if row.value["version"] != expected:
            raise HTTPException(409, "Teacher profile changed; reload before saving")

    def invalidate(value):
        return {**value, "version": value["version"] + 1, "approved_revision_id": None, "approved_version": None, "approved_hash": None, "updated_at": now()}

    def metadata(value, kind):
        # Revalidate merged partial input and retain only the bounded picture form.
        source = {key: value.get(key) for key in METADATA_FIELDS}
        if source.get("picture"):
            source["picture"] = {key: source["picture"][key] for key in ("media_type", "data_base64")}
        try:
            checked = TeacherMetadata.model_validate(source)
        except ValidationError:
            raise HTTPException(422, "Teacher metadata is invalid") from None
        data = checked.model_dump()
        if kind == "human" and (data["ai_behavior"] is not None or data["tutor_model"] is not None):
            raise HTTPException(422, "Human teachers cannot contain AI behavior")
        if kind == "ai" and data["ai_behavior"] is None:
            data["ai_behavior"] = AIBehavior().model_dump()
        if data["picture"]:
            data["picture"] = normalized_picture(data["picture"]["data_base64"])
        return data

    def validate_model(db, identity, school_id, value):
        policy = value.get("tutor_model")
        if policy:
            connection = _find(db, identity["tenant_id"], policy["connection_id"], "connection", school_id)
            if policy["model"] not in connection.value.get("models", []):
                raise HTTPException(422, "Choose a model assigned to this school connection")

    def bind(db, identity, school_id, value, principal_id, placement_id):
        if principal_id:
            if value["kind"] != "human":
                raise HTTPException(422, "Only human profiles link human principals")
            target = _find(db, identity["tenant_id"], principal_id, "user")
            member = db.get(Record, membership_id(school_id, target.id))
            if target.value.get("disabled") or not member or member.tenant_id != identity["tenant_id"] or member.kind != "school_membership" or member.parent_id != school_id or member.owner_id != target.id or not member.value.get("active") or normalized_role(member.value.get("role")) != "teacher":
                raise HTTPException(422, "An active existing school Teacher is required")
            return {**value, "principal_id": target.id, "binding_context": {"principal_id": target.id}}
        if placement_id:
            if value["kind"] != "ai":
                raise HTTPException(422, "Only AI profiles link agent placements")
            placement = _find(db, identity["tenant_id"], placement_id, "agent_placement", school_id)
            if placement.parent_id != school_id or not placement.owner_id.startswith("agent:") or placement.value.get("school_id") != school_id:
                raise HTTPException(422, "An exact existing school agent placement is required")
            return {**value, "agent_placement_id": placement.id, "binding_context": _placement_context(placement)}
        return value

    def audit_profile(db, identity, operation, school_id, row, **extra):
        value = row.value
        picture = value.get("picture")
        # Raster bytes/contact details are not copied into the audit chain.
        audit_actor(db, identity, operation, {"school_id": school_id, "teacher_profile_id": row.id, "kind": value["kind"], "version": value["version"], "status": value["status"], "principal_id": value.get("principal_id"), "agent_placement_id": value.get("agent_placement_id"), "picture_sha256": picture.get("sha256") if picture else None, **extra})

    def profile_dict(db, row, school_id):
        from .standards import corpus_descriptor
        from .school_curriculum import workflow_descriptor
        value = row.value
        approval_current = _approval_current(db, row, school_id)
        principal_id, eligible = _binding_state(db, row, school_id)
        assigned = []
        for link in _assignments(db, row):
            course = db.get(Record, link.value["course_id"])
            if not course or course.tenant_id != row.tenant_id or course.kind != "course" or scope_id(db, course) != school_id:
                continue
            current = corpus_descriptor(db, course, school_id)
            assigned.append({"id": course.id, "title": course.value["title"], "curriculum": course.value.get("curriculum"), "corpus": {**_corpus_reference(current), "ready_for_teaching": current.get("ready_for_teaching", False), "gaps": current.get("gaps", [])}, "has_teaching_authority": eligible and _teaches(db, course, principal_id)})
        lesson_model_configured = bool(value.get("tutor_model"))
        if value["kind"] == "ai" and approval_current:
            from .teacher_models import model_policy_context
            try:
                execution = model_policy_context(db, {"tenant_id": row.tenant_id}, row, school_id, "lesson")
                if execution:
                    lesson_model_configured = True
            except HTTPException:
                lesson_model_configured = False
        workflow = next((item for item in reversed(_rows(db, row.tenant_id, "knowledge_workflow_run", school_id)) if item.value.get("teacher_profile_id") == row.id), None)
        return {"id": row.id, "school_id": school_id, **{key: item for key, item in value.items() if key != "binding_context"}, "latest_onboarding_run": workflow_descriptor(db, workflow, db.get(Record, school_id)) if workflow else None, "tutor_model": value.get("tutor_model"), "approval_current": approval_current, "course_ids": [item["id"] for item in assigned], "assigned_courses": assigned, "can_manage": True, "authority_granted": False, "activation": {"mode": "human" if value["kind"] == "human" else "core_agent" if value.get("agent_placement_id") else "managed", "managed_lesson_configured": bool(value["kind"] == "ai" and not value.get("agent_placement_id") and lesson_model_configured and approval_current and assigned and all(item["corpus"]["ready_for_teaching"] for item in assigned)), "identity_linked": bool(value.get("principal_id") or value.get("agent_placement_id")), "identity_eligible": eligible, "eligibility_scope": "local_only", "agent_admission_enabled": settings.allow_agent_teachers, "live_models_enabled": settings.allow_live_models, "runtime_active": False, "authority_granted": False}}

    @app.get("/api/schools/{school_id}/teachers")
    def teachers(school_id: str, identity=Depends(actor)):
        with database.factory() as db:
            manage(db, identity, school_id)
            return [profile_dict(db, row, school_id) for row in _rows(db, identity["tenant_id"], "teacher_profile", school_id) if scope_id(db, row) == school_id]

    @app.post("/api/schools/{school_id}/teachers", status_code=201)
    def create_teacher(school_id: str, body: TeacherCreate, identity=Depends(actor)):
        with database.write() as db:
            manage(db, identity, school_id)
            value = {**metadata(body.model_dump(), body.kind), "kind": body.kind, "status": body.status or ("draft" if body.kind == "ai" else "pending"), "principal_id": None, "agent_placement_id": None, "binding_context": None, "version": 1, "approved_revision_id": None, "approved_version": None, "approved_hash": None, "updated_at": now()}
            value = bind(db, identity, school_id, value, body.principal_id, body.agent_placement_id)
            validate_model(db, identity, school_id, value)
            row = add(db, identity, school_id, "teacher_profile", school_id, value)
            audit_profile(db, identity, "teacher_profile.created", school_id, row)
            from .school_curriculum import record_teacher_workflow
            record_teacher_workflow(db, identity, school_id, row, [], audit_actor, reason="profile_created")
            return profile_dict(db, row, school_id)

    @app.get("/api/schools/{school_id}/teachers/{profile_id}")
    def teacher(school_id: str, profile_id: str, identity=Depends(actor)):
        with database.factory() as db:
            manage(db, identity, school_id)
            return profile_dict(db, find(db, identity, school_id, profile_id), school_id)

    @app.put("/api/schools/{school_id}/teachers/{profile_id}")
    def update_teacher(school_id: str, profile_id: str, body: TeacherUpdate, identity=Depends(actor)):
        with database.write() as db:
            manage(db, identity, school_id)
            row = find(db, identity, school_id, profile_id)
            version(row, body.expected_version)
            updates = body.model_dump(exclude_unset=True, exclude={"expected_version"})
            merged = {**row.value, **updates}
            if merged.get("status") is None:
                raise HTTPException(422, "Profile status cannot be null")
            value = {**merged, **metadata(merged, row.value["kind"])}
            validate_model(db, identity, school_id, value)
            row.set(invalidate(value))
            audit_profile(db, identity, "teacher_profile.updated", school_id, row)
            return profile_dict(db, row, school_id)

    @app.delete("/api/schools/{school_id}/teachers/{profile_id}")
    def archive_teacher(school_id: str, profile_id: str, identity=Depends(actor)):
        with database.write() as db:
            manage(db, identity, school_id)
            row = find(db, identity, school_id, profile_id)
            row.set(invalidate({**row.value, "status": "archived"}))
            audit_profile(db, identity, "teacher_profile.archived", school_id, row)
            return profile_dict(db, row, school_id)

    @app.post("/api/schools/{school_id}/teachers/{profile_id}/bind")
    def bind_teacher(school_id: str, profile_id: str, body: TeacherBinding, identity=Depends(actor)):
        with database.write() as db:
            manage(db, identity, school_id)
            row = find(db, identity, school_id, profile_id)
            version(row, body.expected_version)
            if row.value.get("principal_id") or row.value.get("agent_placement_id"):
                raise HTTPException(409, "Teacher identity binding is immutable; create another profile")
            row.set(invalidate(bind(db, identity, school_id, row.value, body.principal_id, body.agent_placement_id)))
            audit_profile(db, identity, "teacher_profile.bound", school_id, row)
            return profile_dict(db, row, school_id)

    @app.put("/api/schools/{school_id}/teachers/{profile_id}/courses")
    def assign_courses(school_id: str, profile_id: str, body: TeacherCourses, identity=Depends(actor)):
        from .standards import build_course_corpus
        from .school_curriculum import record_teacher_workflow
        with database.write() as db:
            manage(db, identity, school_id)
            row = find(db, identity, school_id, profile_id)
            version(row, body.expected_version)
            if row.value["status"] == "archived":
                raise HTTPException(409, "Restore the profile before assigning classes")
            courses = [_find(db, identity["tenant_id"], course_id, "course", school_id) for course_id in body.course_ids]
            # Managers have actual school authority. A profile remains planning
            # metadata even when its eventual teacher is not enrolled in a class.
            links = {link.value["course_id"]: link for link in _rows(db, identity["tenant_id"], "teacher_profile_course", row.id)}
            for link in links.values():
                if link.value["course_id"] not in body.course_ids:
                    link.set({**link.value, "active": False})
            results = []
            for course in courses:
                corpus = build_course_corpus(db, identity, course, school_id, audit_actor)
                value = {"course_id": course.id, "active": True, "curriculum": course.value.get("curriculum"), "corpus": _corpus_reference(corpus)}
                if course.id in links:
                    links[course.id].set(value)
                else:
                    add(db, identity, school_id, "teacher_profile_course", row.id, value)
                results.append({"course_id": course.id, "title": course.value["title"], "curriculum": course.value.get("curriculum"), "corpus": {**_corpus_reference(corpus), "ready_for_teaching": corpus.get("ready_for_teaching", False), "gaps": corpus.get("gaps", [])}})
            row.set(invalidate(row.value))
            audit_profile(db, identity, "teacher_profile.courses_assigned", school_id, row, course_ids=body.course_ids, authority_granted=False)
            workflow = record_teacher_workflow(db, identity, school_id, row, results, audit_actor)
            return {**profile_dict(db, row, school_id), "onboarding_run": workflow}

    @app.post("/api/schools/{school_id}/teachers/{profile_id}/approve")
    def approve_teacher(school_id: str, profile_id: str, body: TeacherVersion, identity=Depends(actor)):
        from .standards import corpus_descriptor
        with database.write() as db:
            manage(db, identity, school_id)
            row = find(db, identity, school_id, profile_id)
            version(row, body.expected_version)
            if row.value["status"] == "archived":
                raise HTTPException(409, "Restore the profile before approval")
            validate_model(db, identity, school_id, row.value)
            # Profile review pins each current class context without approving
            # its teaching materials. Runtime teaching still requires its own
            # current corpus approval, as well as this exact profile revision.
            for link in _assignments(db, row):
                course = _find(db, identity["tenant_id"], link.value["course_id"], "course", school_id)
                current = corpus_descriptor(db, course, school_id)
                link.set({**link.value, "curriculum": course.value.get("curriculum"), "corpus": _corpus_reference(current)})
            value = invalidate({**row.value, "status": "ready"})
            snapshot = {"profile_id": row.id, "kind": value["kind"], "version": value["version"], **{key: value.get(key) for key in METADATA_FIELDS if key != "contact_email"}, "principal_id": value.get("principal_id"), "agent_placement_id": value.get("agent_placement_id"), "binding_context": value.get("binding_context"), "courses": [{key: link.value[key] for key in ("course_id", "curriculum", "corpus")} for link in _assignments(db, row)]}
            digest = hashlib.sha256(canonical(snapshot).encode()).hexdigest()
            revision = add(db, identity, school_id, "teacher_profile_revision", row.id, {"version": value["version"], "snapshot": snapshot, "sha256": digest, "approved_by": identity["id"], "approved_at": now()})
            row.set({**value, "approved_revision_id": revision.id, "approved_version": value["version"], "approved_hash": digest})
            audit_profile(db, identity, "teacher_profile.approved", school_id, row, revision_id=revision.id, revision_hash=digest)
            return profile_dict(db, row, school_id)

    @app.get("/api/schools/{school_id}/teachers/{profile_id}/context")
    def teaching_context(school_id: str, profile_id: str, course_id: str, identity=Depends(actor)):
        with database.write() as db:
            course = _find(db, identity["tenant_id"], course_id, "course", school_id)
            context = _safe_teacher_context(db, identity, course, school_id, settings, profile_id)
            audit_actor(db, identity, "teacher_profile.context_read", {"school_id": school_id, "course_id": course_id, "teacher_profile_id": profile_id, "revision_id": context["approved_revision_id"], "revision_hash": context["approved_hash"]})
            return context
