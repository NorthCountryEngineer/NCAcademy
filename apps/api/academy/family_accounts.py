"""Private application-owned students, parents, enrollment and lifecycle evidence.

Names, contact details, usernames, enrollment metadata and historical snapshots
are encrypted with the independently configured family identity key. The shared
authorization tables retain only opaque local identifiers and access decisions.
"""
import hashlib

from fastapi import Depends, HTTPException
from sqlalchemy import select

from .db import Login, Record, RecordScope, canonical, now, uid
from .family_schemas import FamilyCreate, FamilyEnrollment, FamilyLifecycle, FamilyPassword, FamilyPatch, ParentStudents
from .permissions import bind_scope, global_admin, membership_id, scope_id

ACCOUNT = "local_account"
EVENT = "family_event"
ENROLLMENT = "family_enrollment"
LINK = "parent_student"
PROFILE_FIELDS = {"username", "name", "preferred_name", "date_of_birth", "email", "phone", "notes"}


def _records(db, tenant, kind, *, owner=None, parent=None):
    query = select(Record).where(Record.tenant_id == tenant, Record.kind == kind)
    if owner is not None:
        query = query.where(Record.owner_id == owner)
    if parent is not None:
        query = query.where(Record.parent_id == parent)
    return list(db.scalars(query.order_by(Record.created_at, Record.id)))


def local_user(db, tenant, user_id):
    row = db.get(Record, user_id)
    if not row or row.tenant_id != tenant or row.kind != "user" or row.value.get("auth_provider") != "academy" or row.value.get("role") not in {"student", "parent"}:
        return None
    return row


def _user(db, tenant, user_id):
    row = local_user(db, tenant, user_id)
    if row is None:
        raise HTTPException(404, "Local family account not found")
    return row


def _account(db, user):
    row = db.get(Record, "local-account:" + user.id)
    if not row or row.tenant_id != user.tenant_id or row.kind != ACCOUNT or row.owner_id != user.id:
        raise HTTPException(409, "Local identity configuration is unavailable")
    return row


def _decrypt(auth, row, purpose, field):
    return auth.family.decrypt(row.value[field], tenant_id=row.tenant_id, owner_id=row.owner_id, purpose=purpose)


def _profile(auth, account):
    return _decrypt(auth, account, "profile", "profile_cipher")


def _encrypted(auth, tenant, owner, purpose, value):
    return auth.family.encrypt(value, tenant_id=tenant, owner_id=owner, purpose=purpose)


def _require_version(account, expected):
    if account.value["version"] != expected:
        raise HTTPException(409, "Account changed; reload before saving")


def _advance(account):
    account.set({**account.value, "version": account.value["version"] + 1})


def _revoke_sessions(db, user):
    for row in list(db.scalars(select(Login).where(Login.tenant_id == user.tenant_id, Login.user_id == user.id))):
        db.delete(row)


def _event(db, auth, identity, user, event_type, details, audit_actor):
    account = _account(db, user)
    identifier = uid()
    payload = {"event_type": event_type, "created_at": now(), "actor_id": identity["id"], "version": account.value["version"], "details": details}
    row = Record(id=identifier, tenant_id=user.tenant_id, kind=EVENT, parent_id=user.id, owner_id=user.id, data=canonical({"event_cipher": _encrypted(auth, user.tenant_id, user.id, "family-event", payload)}))
    db.add(row)
    audit_actor(db, identity, "family." + event_type, {"user_id": user.id, "event_id": identifier, "version": account.value["version"]})
    return row


def qualifying_student(db, tenant, user_id):
    user = local_user(db, tenant, user_id)
    if not user or user.value["role"] != "student" or user.value.get("disabled") or user.value.get("family_status", "active") != "active":
        return False
    for member in _records(db, tenant, "school_membership", owner=user_id):
        school = db.get(Record, member.parent_id)
        if member.value.get("active") and member.value.get("role") == "student" and school and school.kind == "school" and school.tenant_id == tenant and not school.value.get("disabled"):
            return True
    return False


def validate_parent_account(db, tenant_id, user_id):
    """Read-only session admission: a parent always needs an enrolled local child.

    Disabled is intentionally evaluated by the caller so a parent automatically
    paused for enrollment loss can be reactivated after a valid enrollment.
    """
    user = local_user(db, tenant_id, user_id)
    if not user or user.value["role"] != "parent" or user.value.get("family_status", "active") != "active":
        return False
    return any(link.value.get("active") and qualifying_student(db, tenant_id, link.owner_id) for link in _records(db, tenant_id, LINK, parent=user_id))


def reconcile_parent_access(db, auth, identity, audit_actor):
    """Revoke orphan sessions atomically; restore only administratively active parents."""
    db.flush()
    for user in _records(db, identity["tenant_id"], "user"):
        if user.value.get("auth_provider") != "academy" or user.value.get("role") != "parent":
            continue
        permitted = validate_parent_account(db, user.tenant_id, user.id)
        disabled = not permitted
        if user.value.get("disabled", False) == disabled:
            continue
        account = _account(db, user)
        user.set({**user.value, "disabled": disabled, "credential_version": user.value.get("credential_version", 1) + 1})
        _revoke_sessions(db, user)
        _advance(account)
        _event(db, auth, identity, user, "access_reconciled", {"enabled": permitted, "reason": "enrolled_child_required"}, audit_actor)


def _school(db, tenant, identifier):
    row = db.get(Record, identifier)
    if not row or row.tenant_id != tenant or row.kind != "school" or row.value.get("disabled"):
        raise HTTPException(422, "An active school is required")
    return row


def _enrollment_value(auth, row):
    return _decrypt(auth, row, "family-enrollment", "enrollment_cipher")


def _set_enrollment(auth, row, value):
    row.set({"enrollment_cipher": _encrypted(auth, row.tenant_id, row.owner_id, "family-enrollment", value)})


def _delete_course_member(db, row):
    sidecar = db.get(RecordScope, row.id)
    if sidecar:
        db.delete(sidecar)
    db.delete(row)


def _student_members(db, user, school_id=None):
    result = []
    for row in _records(db, user.tenant_id, "enrollment", owner=user.id):
        if row.value.get("role") != "student":
            continue
        course = db.get(Record, row.parent_id)
        if course and course.kind == "course" and course.tenant_id == user.tenant_id and (school_id is None or scope_id(db, course) == school_id):
            result.append(row)
    return result


def _assign_enrollment(db, auth, user, school_id, year, grade, course_ids):
    if user.value["role"] != "student" or user.value.get("family_status", "active") != "active" or user.value.get("disabled"):
        raise HTTPException(422, "An active local Student is required")
    _school(db, user.tenant_id, school_id)
    for course_id in course_ids:
        course = db.get(Record, course_id)
        if not course or course.tenant_id != user.tenant_id or course.kind != "course" or scope_id(db, course) != school_id:
            raise HTTPException(422, "Every class must belong to the selected school")
    # One current grade/year per school, with prior years retained as encrypted
    # metadata and immutable events. Other schools remain independent.
    prior = []
    current = None
    previous_courses = [row.parent_id for row in _student_members(db, user, school_id)]
    previous_progress = _progress(db, user.tenant_id, user.id, set(previous_courses))
    for row in _records(db, user.tenant_id, ENROLLMENT, owner=user.id, parent=school_id):
        value = _enrollment_value(auth, row)
        if value.get("active"):
            prior.append({"id": row.id, **value})
        if value["academic_year"] == year:
            current = row
        if value.get("active"):
            _set_enrollment(auth, row, {**value, "active": False, "ended_at": now(), "progress_snapshot": previous_progress})
    for row in _student_members(db, user, school_id):
        if row.parent_id not in course_ids:
            _delete_course_member(db, row)
    member = db.get(Record, membership_id(school_id, user.id))
    value = {"role": "student", "active": True, "source": "local-family"}
    if member:
        if member.tenant_id != user.tenant_id or member.kind != "school_membership" or member.owner_id != user.id or member.parent_id != school_id:
            raise HTTPException(409, "School membership configuration is invalid")
        member.set(value)
    else:
        db.add(Record(id=membership_id(school_id, user.id), tenant_id=user.tenant_id, kind="school_membership", parent_id=school_id, owner_id=user.id, data=canonical(value)))
    for course_id in course_ids:
        if not any(row.parent_id == course_id for row in _student_members(db, user, school_id)):
            row = Record(id=uid(), tenant_id=user.tenant_id, kind="enrollment", parent_id=course_id, owner_id=user.id, data=canonical({"role": "student"}))
            db.add(row)
            db.flush()
            bind_scope(db, row, school_id)
    if current is None:
        current = Record(id=uid(), tenant_id=user.tenant_id, kind=ENROLLMENT, parent_id=school_id, owner_id=user.id)
        db.add(current)
    _set_enrollment(auth, current, {"academic_year": year, "grade": grade, "course_ids": sorted(course_ids), "active": True, "started_at": now()})
    return {"school_id": school_id, "academic_year": year, "grade": grade, "course_ids": sorted(course_ids), "previous": prior, "previous_course_ids": sorted(previous_courses), "previous_progress": previous_progress}


def _deactivate_student(db, auth, user, *, school_id=None):
    removed = []
    course_members = _student_members(db, user, school_id)
    progress_snapshot = _progress(db, user.tenant_id, user.id, {row.parent_id for row in course_members})
    for row in course_members:
        removed.append(row.parent_id)
        _delete_course_member(db, row)
    for member in _records(db, user.tenant_id, "school_membership", owner=user.id):
        if member.value.get("role") == "student" and (school_id is None or member.parent_id == school_id):
            member.set({**member.value, "active": False})
    ended = []
    for row in _records(db, user.tenant_id, ENROLLMENT, owner=user.id):
        value = _enrollment_value(auth, row)
        if value.get("active") and (school_id is None or row.parent_id == school_id):
            ended.append({"id": row.id, "school_id": row.parent_id, **value})
            _set_enrollment(auth, row, {**value, "active": False, "ended_at": now(), "progress_snapshot": _progress(db, user.tenant_id, user.id, set(value.get("course_ids", [])))})
    _revoke_sessions(db, user)
    return {"ended_enrollments": ended, "removed_course_ids": sorted(removed), "progress_snapshot": progress_snapshot}


def _links(db, tenant, parent=None, student=None):
    return [r for r in _records(db, tenant, LINK, parent=parent, owner=student) if r.value.get("active")]


def _set_children(db, user, student_ids):
    if user.value["role"] != "parent" or not student_ids:
        raise HTTPException(422, "A parent requires at least one enrolled local student")
    children = [local_user(db, user.tenant_id, student_id) for student_id in student_ids]
    if any(not child or child.value["role"] != "student" for child in children) or not any(qualifying_student(db, user.tenant_id, student_id) for student_id in student_ids):
        raise HTTPException(422, "Link local Students and include at least one active school-enrolled child")
    previous = [row.owner_id for row in _links(db, user.tenant_id, parent=user.id)]
    for row in _records(db, user.tenant_id, LINK, parent=user.id):
        row.set({"active": row.owner_id in student_ids})
    for student_id in student_ids:
        key = "family-link:" + hashlib.sha256((user.id + ":" + student_id).encode()).hexdigest()[:48]
        if not db.get(Record, key):
            db.add(Record(id=key, tenant_id=user.tenant_id, kind=LINK, parent_id=user.id, owner_id=student_id, data=canonical({"active": True})))
    return previous


def _enrollments(db, auth, user):
    result = []
    for row in _records(db, user.tenant_id, ENROLLMENT, owner=user.id):
        school = db.get(Record, row.parent_id)
        value = _enrollment_value(auth, row)
        member = db.get(Record, membership_id(row.parent_id, user.id))
        current = bool(member and member.tenant_id == user.tenant_id and member.kind == "school_membership" and member.parent_id == row.parent_id and member.owner_id == user.id and member.value.get("active") and member.value.get("role") == "student" and school and school.tenant_id == user.tenant_id and school.kind == "school" and not school.value.get("disabled") and not user.value.get("disabled"))
        value["active"] = bool(value.get("active") and current)
        if value["active"]:
            value["course_ids"] = sorted(member.parent_id for member in _student_members(db, user, row.parent_id))
        result.append({"id": row.id, "school_id": row.parent_id, "school_name": school.value.get("name", "Unavailable school") if school and school.tenant_id == user.tenant_id and school.kind == "school" else "Unavailable school", **value})
    # Existing school/class administration can grant a local Student access
    # before a grade placement is recorded. Report the actual access without
    # inventing a school year or grade; the next explicit placement supersedes it.
    covered = {row["school_id"] for row in result if row["active"]}
    for member in _records(db, user.tenant_id, "school_membership", owner=user.id):
        if member.parent_id in covered or not member.value.get("active") or member.value.get("role") != "student":
            continue
        school = db.get(Record, member.parent_id)
        if school and school.tenant_id == user.tenant_id and school.kind == "school" and not school.value.get("disabled"):
            result.append({"id": member.id, "school_id": school.id, "school_name": school.value.get("name", "Unavailable school"), "academic_year": "", "grade": "", "course_ids": sorted(row.parent_id for row in _student_members(db, user, school.id)), "active": True, "started_at": member.created_at})
    return result


def _descriptor(db, auth, user, *, own=False):
    account = _account(db, user)
    profile = _profile(auth, account)
    if own:
        profile.pop("notes", None)
    return {"id": user.id, "kind": user.value["role"], **profile, "status": user.value.get("family_status", "active"), "disabled": bool(user.value.get("disabled")), "must_change_password": bool(user.value.get("must_change_password")), "version": account.value["version"], "created_at": user.created_at, "enrollments": _enrollments(db, auth, user), "student_ids": [row.owner_id for row in _links(db, user.tenant_id, parent=user.id)] if user.value["role"] == "parent" else [], "parent_ids": [row.parent_id for row in _links(db, user.tenant_id, student=user.id)] if user.value["role"] == "student" and not own else []}


def record_enrollment_change(db, auth, identity, user_id, school_id, course_id, active, audit_actor):
    """Called in the same write transaction as legacy class enrollment routes."""
    user = local_user(db, identity["tenant_id"], user_id)
    if not user or user.value["role"] != "student":
        return
    for row in _records(db, user.tenant_id, ENROLLMENT, owner=user_id, parent=school_id):
        value = _enrollment_value(auth, row)
        if value.get("active"):
            course_ids = set(value.get("course_ids", []))
            course_ids.add(course_id) if active else course_ids.discard(course_id)
            _set_enrollment(auth, row, {**value, "course_ids": sorted(course_ids)})
    account = _account(db, user)
    _advance(account)
    _event(db, auth, identity, user, "class_enrollment_changed", {"school_id": school_id, "course_id": course_id, "active": active}, audit_actor)
    reconcile_parent_access(db, auth, identity, audit_actor)


def record_school_membership_change(db, auth, identity, user_id, school_id, active, audit_actor):
    """Keep encrypted history and parent admission current for existing school UI."""
    user = local_user(db, identity["tenant_id"], user_id)
    if not user or user.value["role"] != "student":
        return
    details = {"school_id": school_id, "active": active}
    if not active:
        details.update(_deactivate_student(db, auth, user, school_id=school_id))
    account = _account(db, user)
    _advance(account)
    _event(db, auth, identity, user, "school_enrollment_changed", details, audit_actor)
    reconcile_parent_access(db, auth, identity, audit_actor)


def _progress(db, tenant, student_id, course_ids):
    assignments = {row.id for row in _records(db, tenant, "assignment") if row.parent_id in course_ids}
    submissions = [row for row in _records(db, tenant, "submission", owner=student_id) if row.parent_id in assignments]
    submission_ids = {row.id for row in submissions}
    reviewed = {row.parent_id for row in _records(db, tenant, "review") if row.parent_id in submission_ids}
    lessons = [row for row in _records(db, tenant, "lesson_turn", owner=student_id) if row.parent_id in course_ids and row.value.get("status") == "released"]
    return {"assignment_count": len(assignments), "submitted_assignment_count": len({row.parent_id for row in submissions}), "reviewed_submission_count": len(reviewed), "lesson_count": len(lessons)}


def register_family_api(app, database, auth, settings, actor, audit_actor):
    def administrator(identity):
        if not global_admin(identity):
            raise HTTPException(403, "A human Administrator is required to manage local family accounts")

    @app.get("/api/admin/family-accounts")
    def accounts(school_id: str | None = None, kind: str | None = None, include_inactive: bool = False, identity=Depends(actor)):
        administrator(identity)
        if kind not in {None, "student", "parent"}:
            raise HTTPException(422, "Choose student or parent")
        with database.write() as db:
            if school_id:
                _school(db, identity["tenant_id"], school_id)
            result = []
            for user in _records(db, identity["tenant_id"], "user"):
                if not local_user(db, identity["tenant_id"], user.id) or kind and user.value["role"] != kind or not include_inactive and user.value.get("disabled"):
                    continue
                descriptor = _descriptor(db, auth, user)
                school_match = any(row["school_id"] == school_id and (include_inactive or row["active"]) for row in descriptor["enrollments"])
                if school_id and user.value["role"] == "parent":
                    school_match = any(member.parent_id == school_id and member.value.get("active") for child in descriptor["student_ids"] for member in _records(db, identity["tenant_id"], "school_membership", owner=child))
                if not school_id or school_match:
                    result.append(descriptor)
            audit_actor(db, identity, "family.catalog_read", {"count": len(result)})
            return {"accounts": result}

    @app.post("/api/admin/family-accounts", status_code=201)
    def create(body: FamilyCreate, identity=Depends(actor)):
        administrator(identity)
        username = auth.family.normalize_username(body.username)
        verifier = auth.family.password_hash(body.password.get_secret_value())
        with database.write() as db:
            claim_id = "local-username:" + auth.family.username_key(identity["tenant_id"], username)
            if db.get(Record, claim_id):
                raise HTTPException(409, "Username is unavailable")
            identifier = uid()
            profile = {key: value for key, value in body.model_dump(mode="json", exclude={"password"}).items() if key in PROFILE_FIELDS}
            profile["username"] = username
            user = Record(id=identifier, tenant_id=identity["tenant_id"], kind="user", data=canonical({"name": body.kind.title() + " " + identifier[:8], "role": body.kind, "auth_provider": "academy", "disabled": False, "credential_version": 1, "must_change_password": body.must_change_password, "family_status": "active"}))
            db.add(user)
            db.add(Record(id="local-account:" + identifier, tenant_id=user.tenant_id, kind=ACCOUNT, owner_id=identifier, data=canonical({"profile_cipher": _encrypted(auth, user.tenant_id, identifier, "profile", profile), "password_cipher": _encrypted(auth, user.tenant_id, identifier, "password", verifier), "version": 1})))
            db.add(Record(id=claim_id, tenant_id=user.tenant_id, kind="local_username", owner_id=identifier, data="{}"))
            db.flush()
            _event(db, auth, identity, user, "created", {"kind": body.kind, "profile": profile}, audit_actor)
            if body.kind == "student" and body.school_id:
                details = _assign_enrollment(db, auth, user, body.school_id, body.academic_year, body.grade, body.course_ids)
                _event(db, auth, identity, user, "enrollment_assigned", details, audit_actor)
            if body.kind == "parent":
                _set_children(db, user, body.student_ids)
                _event(db, auth, identity, user, "children_assigned", {"student_ids": body.student_ids, "previous_student_ids": []}, audit_actor)
            reconcile_parent_access(db, auth, identity, audit_actor)
            return _descriptor(db, auth, user)

    @app.get("/api/admin/family-accounts/{user_id}")
    def detail(user_id: str, identity=Depends(actor)):
        administrator(identity)
        with database.write() as db:
            user = _user(db, identity["tenant_id"], user_id)
            audit_actor(db, identity, "family.profile_read", {"user_id": user.id})
            return _descriptor(db, auth, user)

    @app.patch("/api/admin/family-accounts/{user_id}")
    def update(user_id: str, body: FamilyPatch, identity=Depends(actor)):
        administrator(identity)
        with database.write() as db:
            user = _user(db, identity["tenant_id"], user_id)
            account = _account(db, user)
            _require_version(account, body.expected_version)
            old = _profile(auth, account)
            changes = body.model_dump(mode="json", exclude={"expected_version"}, exclude_unset=True)
            if "username" in changes:
                changes["username"] = auth.family.normalize_username(changes["username"])
                old_id = "local-username:" + auth.family.username_key(user.tenant_id, old["username"])
                new_id = "local-username:" + auth.family.username_key(user.tenant_id, changes["username"])
                if old_id != new_id:
                    if db.get(Record, new_id):
                        raise HTTPException(409, "Username is unavailable")
                    claim = db.get(Record, old_id)
                    if not claim or claim.kind != "local_username" or claim.owner_id != user.id or claim.tenant_id != user.tenant_id:
                        raise HTTPException(409, "Username configuration is unavailable")
                    db.delete(claim)
                    db.add(Record(id=new_id, tenant_id=user.tenant_id, kind="local_username", owner_id=user.id, data="{}"))
                    user.set({**user.value, "credential_version": user.value["credential_version"] + 1})
                    _revoke_sessions(db, user)
            profile = {**old, **changes}
            account.set({**account.value, "profile_cipher": _encrypted(auth, user.tenant_id, user.id, "profile", profile), "version": account.value["version"] + 1})
            _event(db, auth, identity, user, "profile_updated", {"profile": profile, "changed_fields": sorted(changes)}, audit_actor)
            return _descriptor(db, auth, user)

    @app.put("/api/admin/family-accounts/{user_id}/password")
    def password(user_id: str, body: FamilyPassword, identity=Depends(actor)):
        administrator(identity)
        verifier = auth.family.password_hash(body.new_password.get_secret_value())
        with database.write() as db:
            user = _user(db, identity["tenant_id"], user_id)
            account = _account(db, user)
            _require_version(account, body.expected_version)
            account.set({**account.value, "password_cipher": _encrypted(auth, user.tenant_id, user.id, "password", verifier), "version": account.value["version"] + 1})
            user.set({**user.value, "must_change_password": body.must_change_password, "credential_version": user.value["credential_version"] + 1})
            _revoke_sessions(db, user)
            _event(db, auth, identity, user, "password_reset", {"must_change_password": body.must_change_password}, audit_actor)
            return _descriptor(db, auth, user)

    @app.post("/api/admin/family-accounts/{user_id}/enrollments")
    def enroll(user_id: str, body: FamilyEnrollment, identity=Depends(actor)):
        administrator(identity)
        with database.write() as db:
            user = _user(db, identity["tenant_id"], user_id)
            account = _account(db, user)
            _require_version(account, body.expected_version)
            details = _assign_enrollment(db, auth, user, body.school_id, body.academic_year, body.grade, body.course_ids)
            _advance(account)
            _event(db, auth, identity, user, "enrollment_assigned", details, audit_actor)
            reconcile_parent_access(db, auth, identity, audit_actor)
            return _descriptor(db, auth, user)

    @app.post("/api/admin/family-accounts/{user_id}/lifecycle")
    def lifecycle(user_id: str, body: FamilyLifecycle, identity=Depends(actor)):
        administrator(identity)
        with database.write() as db:
            user = _user(db, identity["tenant_id"], user_id)
            account = _account(db, user)
            _require_version(account, body.expected_version)
            if body.school_id:
                _school(db, user.tenant_id, body.school_id)
            previous = user.value.get("family_status", "active")
            if user.value["role"] == "parent" and body.status == "active":
                user.set({**user.value, "family_status": "active"})
                if not validate_parent_account(db, user.tenant_id, user.id):
                    raise HTTPException(422, "An active parent requires an enrolled local child")
            details = {"previous_status": previous, **body.model_dump(exclude={"expected_version"})}
            if user.value["role"] == "student" and body.status != "active":
                details.update(_deactivate_student(db, auth, user))
            user.set({**user.value, "family_status": body.status, "disabled": body.status != "active", "credential_version": user.value["credential_version"] + 1})
            if user.value["role"] == "student" and body.status == "active" and body.school_id:
                prior = next((_enrollment_value(auth, row) for row in _records(db, user.tenant_id, ENROLLMENT, owner=user.id, parent=body.school_id) if _enrollment_value(auth, row).get("active")), None)
                course_ids = prior.get("course_ids", []) if prior and prior["academic_year"] == body.academic_year else []
                grade = body.grade if body.grade is not None else prior.get("grade", "") if prior else ""
                details["enrollment"] = _assign_enrollment(db, auth, user, body.school_id, body.academic_year, grade, course_ids)
            _revoke_sessions(db, user)
            _advance(account)
            _event(db, auth, identity, user, "lifecycle_changed", details, audit_actor)
            reconcile_parent_access(db, auth, identity, audit_actor)
            return _descriptor(db, auth, user)

    @app.put("/api/admin/family-accounts/{user_id}/students")
    def children(user_id: str, body: ParentStudents, identity=Depends(actor)):
        administrator(identity)
        with database.write() as db:
            user = _user(db, identity["tenant_id"], user_id)
            account = _account(db, user)
            _require_version(account, body.expected_version)
            previous = _set_children(db, user, body.student_ids)
            _advance(account)
            _event(db, auth, identity, user, "children_assigned", {"student_ids": body.student_ids, "previous_student_ids": previous}, audit_actor)
            reconcile_parent_access(db, auth, identity, audit_actor)
            return _descriptor(db, auth, user)

    @app.get("/api/admin/family-accounts/{user_id}/history")
    def history(user_id: str, identity=Depends(actor)):
        administrator(identity)
        with database.write() as db:
            user = _user(db, identity["tenant_id"], user_id)
            events = [{"id": row.id, **_decrypt(auth, row, "family-event", "event_cipher")} for row in _records(db, user.tenant_id, EVENT, parent=user.id, owner=user.id)]
            audit_actor(db, identity, "family.history_read", {"user_id": user.id, "count": len(events)})
            return {"events": events}

    @app.get("/api/me/family")
    def own(identity=Depends(actor)):
        if identity.get("principal_kind", "human") != "human":
            raise HTTPException(403, "A human local account is required")
        with database.write() as db:
            user = _user(db, identity["tenant_id"], identity["id"])
            children = []
            if user.value["role"] == "parent":
                if not validate_parent_account(db, user.tenant_id, user.id):
                    raise HTTPException(403, "An enrolled child is required")
                for link in _links(db, user.tenant_id, parent=user.id):
                    if not qualifying_student(db, user.tenant_id, link.owner_id):
                        continue
                    child = _user(db, user.tenant_id, link.owner_id)
                    profile = _profile(auth, _account(db, child))
                    enrollments = [row for row in _enrollments(db, auth, child) if row["active"]]
                    current_schools = {row["school_id"] for row in enrollments}
                    course_ids = {member.parent_id for school_id in current_schools for member in _student_members(db, child, school_id)}
                    children.append({"id": child.id, "name": profile["name"], "preferred_name": profile.get("preferred_name", ""), "enrollments": enrollments, "progress": _progress(db, child.tenant_id, child.id, course_ids)})
            audit_actor(db, identity, "family.own_profile_read", {"user_id": user.id, "child_count": len(children)})
            return {"account": _descriptor(db, auth, user, own=True), "children": children}
