"""Adversarial school/class/agent ACL and additive migration acceptance."""
import hashlib
import json
from uuid import uuid4

import pytest
from fastapi import HTTPException
from sqlalchemy import select

from academy.db import Base, Database, Record, RecordScope, Audit, AuditHead, audit, canonical, audit_dict
from academy.config import Settings
from academy.migrate import migrate
from academy.permissions import default_school_id, membership_id, scope_id, school_role
from conftest import login


def put_user(app, user_id, role="student"):
    with app.state.database.write() as db:
        db.add(Record(id=user_id, tenant_id=app.state.settings.tenant_id, kind="user", data=canonical({"name": user_id, "role": role})))


def grant(client, headers, school, user, role):
    response = client.put(f"/api/schools/{school}/members/{user}", json={"role": role}, headers=headers)
    assert response.status_code == 200, response.text


def new_school(client, headers, name="Other school", core_resource_id=None):
    response = client.post("/api/schools", json={"name": name, "core_resource_id": core_resource_id}, headers=headers)
    assert response.status_code == 201, response.text
    return response.json()["id"]


def test_school_scoped_roles_are_explicit_and_cross_school_denied(client, app):
    admin = login(client, "demo-admin")
    other = new_school(client, admin)
    put_user(app, "teacher-b", "teacher")
    grant(client, admin, other, "teacher-b", "teacher")
    course = client.post("/api/courses", json={"title": "School B", "school_id": other, "teacher_id": "teacher-b"}, headers=admin).json()
    login(client, "demo-instructor")
    own = client.get("/api/schools").json()
    assert len(own) == 1 and own[0]["effective_role"] == "teacher"
    assert client.get(f"/api/courses/{course['id']}").status_code == 403
    assert client.get(f"/api/users?school_id={other}").status_code == 403
    assert client.get(f"/api/admin/connections?school_id={other}").status_code == 403
    teacher = login(client, "teacher-b")
    assert client.get("/api/courses").json()[0]["id"] == course["id"]
    assert client.post("/api/courses", json={"title": "No", "school_id": own[0]["id"]}, headers=teacher).status_code == 403


def test_teacher_only_own_or_assigned_classes_and_no_role_escalation(client, app):
    admin = login(client, "demo-admin")
    school = client.get("/api/schools").json()[0]["id"]
    put_user(app, "other-teacher", "teacher")
    grant(client, admin, school, "other-teacher", "teacher")
    course = client.post("/api/courses", json={"title": "Private", "teacher_id": "other-teacher"}, headers=admin).json()
    teacher = login(client, "demo-instructor")
    assert client.get(f"/api/courses/{course['id']}").status_code == 403
    assert client.post("/api/courses/demo-course/enrollments", json={"user_id": "other-teacher", "role": "teacher"}, headers=teacher).status_code == 403
    assert client.put(f"/api/schools/{school}/members/demo-instructor", json={"role": "school"}, headers=teacher).status_code == 403
    assert client.post("/api/courses", json={"title": "No", "teacher_id": "other-teacher"}, headers=teacher).status_code == 403
    admin = login(client, "demo-admin")
    assert client.post(f"/api/courses/{course['id']}/enrollments", json={"user_id": "demo-instructor", "role": "teacher"}, headers=admin).status_code == 200
    login(client, "demo-instructor")
    assert client.get(f"/api/courses/{course['id']}").json()["can_manage"] is True
    admin = login(client, "demo-admin")
    assert client.delete(f"/api/courses/{course['id']}/enrollments/demo-instructor", headers=admin).status_code == 200
    login(client, "demo-instructor")
    assert client.get(f"/api/courses/{course['id']}").status_code == 403


def test_school_admin_scope_and_no_global_admin_escalation(client, app):
    admin = login(client, "demo-admin")
    school = new_school(client, admin, core_resource_id="registered-school-b")
    put_user(app, "school-manager", "school")
    put_user(app, "new-teacher", "teacher")
    grant(client, admin, school, "school-manager", "school")
    manager = login(client, "school-manager")
    grant(client, manager, school, "new-teacher", "teacher")
    assert client.post("/api/schools", json={"name": "No"}, headers=manager).status_code == 403
    assert client.put(f"/api/schools/{school}", json={"name": "Renamed"}, headers=manager).json()["core_resource_id"] == "registered-school-b"
    assert client.put(f"/api/schools/{school}", json={"name": "No", "core_resource_id": "foreign"}, headers=manager).status_code == 403
    assert client.put(f"/api/schools/{school}/members/new-teacher", json={"role": "school"}, headers=manager).status_code == 403
    assert client.get("/api/admin/audit/export").status_code == 403
    assert client.get("/api/audit/verify").status_code == 403
    assert client.get("/api/courses/demo-course").status_code == 403


def test_membership_revocation_applies_next_request(client, app):
    admin = login(client, "demo-admin")
    school = client.get("/api/schools").json()[0]["id"]
    assert client.delete(f"/api/schools/{school}/members/demo-instructor", headers=admin).status_code == 200
    teacher = login(client, "demo-instructor")
    assert client.get("/api/courses").json() == []
    assert client.get("/api/courses/demo-course").status_code == 403
    assert client.post("/api/courses", json={"title": "No"}, headers=teacher).status_code == 403


def test_core_capability_ceiling_is_live_and_grants_are_additional(app):
    settings = app.state.settings
    school = default_school_id(settings.tenant_id)
    identity = {"id": "demo-instructor", "tenant_id": settings.tenant_id, "role": "teacher", "principal_kind": "human", "permissions": ["academy-use", "academy-teach"]}
    settings.auth_mode = "core"
    with app.state.database.factory() as db:
        assert school_role(db, identity, school, settings) == "teacher"
        identity["permissions"] = ["academy-use"]
        assert school_role(db, identity, school, settings) is None
        identity["role"] = "admin"
        identity["principal_kind"] = "agent"
        assert school_role(db, identity, school, settings) is None
    settings.auth_mode = "local"


def test_connection_binding_cannot_cross_school(client, app):
    admin = login(client, "demo-admin")
    other = new_school(client, admin)
    connection = client.post("/api/admin/connections", json={"name": "Other", "endpoint": "https://models.example.test/v1", "models": ["bounded"], "school_id": other}, headers=admin).json()
    body = {"title": "No", "instructions": "Own work", "tutor_policy": {"mode": "socratic", "connection_id": connection["id"], "model": "bounded", "questions": [{"id": "q", "text": "What evidence do you have?"}]}}
    assert client.post("/api/courses/demo-course/assignments", json=body, headers=admin).status_code == 403
    assert client.put(f"/api/admin/connections/{connection['id']}", json={**connection, "school_id": default_school_id(app.state.settings.tenant_id)}, headers=admin).status_code in {403, 422}


def test_student_enrollment_and_resources_do_not_grant_school_roles(client, app):
    admin = login(client, "demo-admin")
    school = client.get("/api/schools").json()[0]["id"]
    put_user(app, "unassigned-student")
    assert client.post("/api/courses/demo-course/enrollments", json={"user_id": "unassigned-student"}, headers=admin).status_code == 422
    teacher_only = client.post(f"/api/schools/{school}/resources", json={"name": "Teacher bank", "content": "Private teaching material", "audience": "teachers"}, headers=admin).json()
    public = client.post(f"/api/schools/{school}/resources", json={"name": "Library", "content": "School-wide reading"}, headers=admin).json()
    student = login(client)
    assert client.get(f"/api/resources/{teacher_only['id']}/resolve").status_code == 403
    assert client.get(f"/api/resources/{public['id']}/resolve").json()["content"] == "School-wide reading"
    assert client.post(f"/api/schools/{school}/resources", json={"name": "No"}, headers=student).status_code == 403
    assert client.get(f"/api/users?school_id={school}").status_code == 403


def test_core_resource_descriptors_check_live_core_and_never_claim_transport(client, app, monkeypatch):
    admin = login(client, "demo-admin")
    school = client.get("/api/schools").json()[0]["id"]
    resource = client.post(f"/api/schools/{school}/resources", json={"name": "Assigned model", "kind": "core", "core_resource_type": "model-interface", "core_resource_id": "registered-resource", "permission": "academy-use"}, headers=admin).json()
    calls = []
    def allowed(request, identity, permission, resource_type, resource_id):
        calls.append((identity["id"], permission, resource_type, resource_id))
    monkeypatch.setattr(app.state.auth, "authorize_resource", allowed)
    resolved = client.get(f"/api/resources/{resource['id']}/resolve").json()
    assert resolved["authorized"] is True and resolved["integration_available"] is False
    assert len(calls) == 1 and "credential" not in resolved
    def revoked(*args):
        raise HTTPException(403, "Revoked")
    monkeypatch.setattr(app.state.auth, "authorize_resource", revoked)
    assert client.get(f"/api/resources/{resource['id']}/resolve").status_code == 403
    assert client.get(f"/api/schools/{school}/resources").json() == []


@pytest.fixture
def teacher_agent(client, app, monkeypatch):
    admin = login(client, "demo-admin")
    school = client.get("/api/schools").json()[0]["id"]
    assert client.put(f"/api/schools/{school}", json={"name": "Default school", "core_resource_id": "school-registered"}, headers=admin).status_code == 200
    agent_id, binding_id, consumer_id, task_id = (str(uuid4()) for _ in range(4))
    body = {"name": "Synthetic Teacher", "core_agent_id": agent_id, "core_binding_id": binding_id, "core_consumer_service_id": consumer_id, "environment": "production", "task_id": task_id, "core_resource_id": "school-registered", "purpose": "approved-teacher-test"}
    placement = client.post(f"/api/schools/{school}/agents", json=body, headers=admin).json()
    assert "id" in placement, placement
    principal_id = "agent:" + agent_id
    identity = {"id": principal_id, "tenant_id": app.state.settings.tenant_id, "name": "Synthetic Teacher", "role": "teacher", "principal_kind": "agent", "school_id": school, "placement_id": placement["id"], "account_id": agent_id, "binding_id": binding_id, "consumer_service_id": consumer_id, "environment": "production", "task_id": task_id, "permissions": ["academy-use", "academy-teach"], "can_audit": False, "csrf_token": "", "login_id": "agent-test"}
    original = app.state.auth.actor
    def actor(request):
        return dict(identity) if request.headers.get("X-Test-Agent") == "yes" else original(request)
    monkeypatch.setattr(app.state.auth, "actor", actor)
    course = client.post("/api/courses", json={"title": "Assigned agent class", "teacher_id": principal_id}, headers=admin).json()
    return {"identity": identity, "school": school, "placement": placement, "course": course, "headers": {"X-Test-Agent": "yes"}, "admin": admin}


def test_agent_has_teacher_read_parity_but_no_admin_student_impersonation(client, teacher_agent):
    data = teacher_agent
    headers = data["headers"]
    course = data["course"]["id"]
    assert client.get(f"/api/courses/{course}", headers=headers).status_code == 200
    assert client.get("/api/courses/demo-course", headers=headers).status_code == 403
    assert client.get(f"/api/schools/{data['school']}/members", headers=headers).status_code == 403
    assert client.get("/api/admin/settings", headers=headers).status_code == 403
    assert client.get("/api/admin/connections", headers=headers).status_code == 403
    assert client.get("/api/admin/audit/export", headers=headers).status_code == 403
    assert client.get("/api/audit/verify", headers=headers).status_code == 403
    assert client.get("/api/audit/sessions", headers=headers).status_code == 403
    assert client.put("/api/assignments/demo-assignment/draft", json={"content": "Forged"}, headers=headers).status_code == 403
    assert client.post("/api/assignments/demo-assignment/submit", json={"content": "Forged"}, headers=headers).status_code == 403


def test_agent_assignment_is_staged_and_publication_audited(client, app, teacher_agent):
    data = teacher_agent
    course = data["course"]["id"]
    response = client.post(f"/api/courses/{course}/assignments", json={"title": "Proposed", "instructions": "Write your own work"}, headers=data["headers"])
    assert response.status_code == 202
    change_id = response.json()["change_request"]["id"]
    assert client.get(f"/api/courses/{course}/assignments", headers=data["headers"]).json() == []
    assert client.post(f"/api/changes/{change_id}/approve", headers=data["headers"]).status_code == 403
    approved = client.post(f"/api/changes/{change_id}/approve", headers=data["admin"])
    assert approved.status_code == 200, approved.text
    assignment = client.get(f"/api/courses/{course}/assignments", headers=data["headers"]).json()[0]
    assert assignment["authorship"]["agent_id"] == data["identity"]["id"]
    with app.state.database.factory() as db:
        proposed = db.scalar(select(Audit).where(Audit.event_type == "change.proposed"))
        evidence = json.loads(proposed.payload)
        assert evidence["principal"]["principal_kind"] == "agent"
        assert evidence["principal"]["binding_id"] == data["identity"]["binding_id"]
        assert evidence["principal"]["school_id"] == data["school"]
        assert db.scalar(select(Audit).where(Audit.event_type == "change.published"))
    assert client.get("/api/audit/verify").json()["valid"] is True


def test_revoked_agent_placement_denies_next_request_and_pending_approval(client, teacher_agent):
    data = teacher_agent
    response = client.post(f"/api/courses/{data['course']['id']}/assignments", json={"title": "Pending", "instructions": "Own work"}, headers=data["headers"])
    change = response.json()["change_request"]["id"]
    assert client.delete(f"/api/schools/{data['school']}/agents/{data['placement']['id']}", headers=data["admin"]).status_code == 200
    assert client.get(f"/api/courses/{data['course']['id']}", headers=data["headers"]).status_code == 403
    assert client.post(f"/api/changes/{change}/approve", headers=data["admin"]).status_code == 409


def test_agent_review_is_proposal_not_a_student_work_mutation(client, app, teacher_agent):
    data = teacher_agent
    course = data["course"]["id"]
    assignment = client.post(f"/api/courses/{course}/assignments", json={"title": "Own work", "instructions": "Student-authored"}, headers=data["admin"]).json()
    assert client.post(f"/api/courses/{course}/enrollments", json={"user_id": "demo-student"}, headers=data["admin"]).status_code == 200
    student = login(client)
    submitted = client.post(f"/api/assignments/{assignment['id']}/submit", json={"content": "Immutable student work"}, headers=student).json()
    roster = client.get(f"/api/assignments/{assignment['id']}/submissions", headers=data["headers"])
    assert roster.status_code == 200 and roster.json()[0]["content"] == "Immutable student work"
    proposed = client.post(f"/api/submissions/{submitted['id']}/review", json={"feedback": "Explain your evidence"}, headers=data["headers"])
    assert proposed.status_code == 202
    assert client.get(f"/api/assignments/{assignment['id']}").json()["submissions"][0]["feedback"] == ""
    admin = login(client, "demo-admin")
    change_id = proposed.json()["change_request"]["id"]
    assert client.post(f"/api/changes/{change_id}/approve", headers=admin).status_code == 200
    login(client)
    final = client.get(f"/api/assignments/{assignment['id']}").json()["submissions"][0]
    assert final["content"] == "Immutable student work" and final["feedback"] == "Explain your evidence"


def test_agent_scope_context_cannot_be_substituted(client, teacher_agent):
    data = teacher_agent
    data["identity"]["binding_id"] = str(uuid4())
    assert client.get(f"/api/courses/{data['course']['id']}", headers=data["headers"]).status_code == 403
    data["identity"]["role"] = "admin"
    assert client.get("/api/admin/audit/export", headers=data["headers"]).status_code == 403


def test_schema2_backfill_preserves_original_records_audits_and_submission_bytes(tmp_path):
    settings = Settings("sqlite:///" + str(tmp_path / "legacy.db"), "local", "http://localhost:8090", "a-test-bootstrap-key-long-enough", False, False)
    database = Database(settings)
    Base.metadata.create_all(database.engine)
    with database.write() as db:
        for record_id, kind, parent, owner, data in (("legacy-teacher", "user", "", "", {"name": "Teacher", "role": "instructor"}), ("legacy-class", "course", "", "", {"title": "Original"}), ("legacy-enrollment", "enrollment", "legacy-class", "legacy-teacher", {"role": "instructor"}), ("legacy-submission", "submission", "legacy-task", "student", {"content": "Immutable bytes", "assignment_snapshot": {"version": 1}})):
            db.add(Record(id=record_id, tenant_id=settings.tenant_id, kind=kind, parent_id=parent, owner_id=owner, data=json.dumps(data)))
        db.flush()
        audit(db, settings.tenant_id, "original", "legacy.original", {"original": "evidence"})
    with database.factory() as db:
        original_records = {r.id: (r.data, r.parent_id, r.owner_id, r.created_at) for r in db.scalars(select(Record))}
        original_audit = [audit_dict(r) for r in db.scalars(select(Audit))]
    migrate(settings)
    migrate(settings)
    with database.factory() as db:
        for record_id, expected in original_records.items():
            row = db.get(Record, record_id)
            assert (row.data, row.parent_id, row.owner_id, row.created_at) == expected
            if row.kind != "user":
                assert scope_id(db, row) == default_school_id(settings.tenant_id)
        assert audit_dict(db.get(Audit, original_audit[0]["id"])) == original_audit[0]
        assert db.get(Record, membership_id(default_school_id(settings.tenant_id), "legacy-teacher")).value["role"] == "teacher"
        assert len(list(db.scalars(select(Audit).where(Audit.event_type == "schema.school_scopes_initialized")))) == 1
    database.engine.dispose()


def test_aggregate_school_admin_does_not_lend_audit_rights_to_other_school_teacher(client, app, monkeypatch):
    student = login(client)
    assert client.put("/api/assignments/demo-assignment/draft", json={"content": "Recorded student attempt"}, headers=student).status_code == 200
    admin = login(client, "demo-admin")
    school_a = default_school_id(app.state.settings.tenant_id)
    school_b = new_school(client, admin)
    grant(client, admin, school_b, "demo-instructor", "school")
    login(client, "demo-instructor")
    session_id = client.get("/api/audit/sessions").json()[0]["session_id"]
    identity = {"id": "demo-instructor", "tenant_id": app.state.settings.tenant_id, "role": "school", "principal_kind": "human", "permissions": ["academy-use", "academy-school", "academy-teach"], "can_audit": True}
    monkeypatch.setattr(app.state.auth, "actor", lambda request: dict(identity))
    app.state.settings.core_organization_id = app.state.settings.tenant_id
    app.state.settings.auth_mode = "core"
    assert client.get(f"/api/audit/sessions?school_id={school_a}").status_code == 403
    assert client.get(f"/api/audit/sessions/{session_id}").status_code == 403
    assert client.get(f"/api/audit/sessions?school_id={school_b}").status_code == 200
    identity["permissions"].append("academy-audit")
    replay = client.get(f"/api/audit/sessions/{session_id}")
    assert replay.status_code == 200 and replay.json()["complete_tenant_chain"] is False
    assert "Recorded student attempt" in replay.text


def test_agent_requires_explicit_audit_permission_for_assigned_class_evidence(client, teacher_agent):
    data = teacher_agent
    course = data["course"]["id"]
    assignment = client.post(f"/api/courses/{course}/assignments", json={"title": "Own work", "instructions": "Write it yourself"}, headers=data["admin"]).json()
    assert client.post(f"/api/courses/{course}/enrollments", json={"user_id": "demo-student"}, headers=data["admin"]).status_code == 200
    student = login(client)
    assert client.put(f"/api/assignments/{assignment['id']}/draft", json={"content": "Observable recorded attempt"}, headers=student).status_code == 200
    assert client.get("/api/audit/sessions", headers=data["headers"]).status_code == 403
    data["identity"]["permissions"].append("academy-audit")
    data["identity"]["can_audit"] = True
    sessions = client.get("/api/audit/sessions", headers=data["headers"]).json()
    assert len(sessions) == 1
    replay = client.get(f"/api/audit/sessions/{sessions[0]['session_id']}", headers=data["headers"])
    assert replay.status_code == 200 and "Observable recorded attempt" in replay.text
    assert client.put(f"/api/assignments/{assignment['id']}/draft", json={"content": "No impersonation"}, headers=data["headers"]).status_code == 403
    assert client.post(f"/api/assignments/{assignment['id']}/submit", json={"content": "No impersonation"}, headers=data["headers"]).status_code == 403
    data["identity"]["permissions"].remove("academy-audit")
    assert client.get(f"/api/audit/sessions/{sessions[0]['session_id']}", headers=data["headers"]).status_code == 403


def test_agent_course_metadata_waits_for_human_publication(client, teacher_agent):
    data = teacher_agent
    proposed = client.post("/api/courses", json={"title": "AI proposed class", "school_id": data["school"]}, headers=data["headers"])
    assert proposed.status_code == 202
    assert len(client.get("/api/courses", headers=data["headers"]).json()) == 1
    approved = client.post(f"/api/changes/{proposed.json()['change_request']['id']}/approve", headers=data["admin"])
    assert approved.status_code == 200, approved.text
    courses = client.get("/api/courses", headers=data["headers"]).json()
    assert len(courses) == 2 and all(c["teacher_id"] == data["identity"]["id"] for c in courses)


def test_real_postgres_school_backfill_preserves_immutable_evidence(monkeypatch):
    import os
    from types import SimpleNamespace
    from sqlalchemy.engine import make_url
    configured = os.getenv("ACADEMY_TEST_POSTGRES_URL", "")
    if not configured:
        pytest.skip("Set ACADEMY_TEST_POSTGRES_URL for isolated school migration PostgreSQL acceptance")
    owner_url = make_url(configured)
    if owner_url.drivername != "postgresql+psycopg" or owner_url.host not in {"127.0.0.1", "localhost", "::1"} or owner_url.database != "academy_privilege_test":
        pytest.fail("School migration test refuses a non-loopback or non-synthetic database")
    monkeypatch.delenv("ACADEMY_RUNTIME_DB_USERNAME", raising=False)
    monkeypatch.delenv("ACADEMY_RUNTIME_DB_PASSWORD", raising=False)
    suffix = uuid4().hex
    settings = SimpleNamespace(database_url=configured, auth_mode="local", tenant_id="school-migration-" + suffix, demo_seed=False)
    database = Database(settings)
    Base.metadata.create_all(database.engine)
    owner, course, enrollment, submission = (prefix + suffix for prefix in ("teacher-", "course-", "enrollment-", "submission-"))
    with database.write() as db:
        originals = [(owner, "user", "", "", {"name": "Legacy teacher", "role": "instructor"}), (course, "course", "", owner, {"title": "Original course"}), (enrollment, "enrollment", course, owner, {"role": "instructor"}), (submission, "submission", "legacy-assignment", "legacy-student", {"content": "Original immutable evidence"})]
        for record_id, kind, parent, principal, value in originals:
            db.add(Record(id=record_id, tenant_id=settings.tenant_id, kind=kind, parent_id=parent, owner_id=principal, data=json.dumps(value)))
        db.flush()
        original_event = audit(db, settings.tenant_id, owner, "legacy.preserved", {"submission_id": submission})
        original_event_id = original_event.id
    with database.factory() as db:
        before = {r.id: (r.data, r.owner_id, r.parent_id, r.created_at) for r in db.scalars(select(Record).where(Record.tenant_id == settings.tenant_id))}
        event_before = audit_dict(db.get(Audit, original_event_id))
    migrate(settings)
    migrate(settings)
    with database.factory() as db:
        assert audit_dict(db.get(Audit, original_event_id)) == event_before
        for record_id, expected in before.items():
            row = db.get(Record, record_id)
            assert (row.data, row.owner_id, row.parent_id, row.created_at) == expected
            if row.kind != "user":
                assert scope_id(db, row) == default_school_id(settings.tenant_id)
        assert db.get(Record, membership_id(default_school_id(settings.tenant_id), owner)).value["role"] == "teacher"
    database.engine.dispose()


def test_proposal_target_edit_cannot_be_silently_overwritten(client, teacher_agent):
    data = teacher_agent
    course = data["course"]["id"]
    body = {"title": "Original", "instructions": "Original human instructions"}
    assignment = client.post(f"/api/courses/{course}/assignments", json=body, headers=data["admin"]).json()
    proposed = client.put(f"/api/assignments/{assignment['id']}", json={"title": "Agent proposal", "instructions": "Proposed instructions"}, headers=data["headers"])
    assert proposed.status_code == 202
    human_update = client.put(f"/api/assignments/{assignment['id']}", json={"title": "New human revision", "instructions": "Preserve this newer revision"}, headers=data["admin"])
    assert human_update.status_code == 200
    approval = client.post(f"/api/changes/{proposed.json()['change_request']['id']}/approve", headers=data["admin"])
    assert approval.status_code == 409 and "target changed" in approval.json()["detail"]
    assert client.get(f"/api/assignments/{assignment['id']}").json()["title"] == "New human revision"


def test_placement_context_change_invalidates_previously_proposed_work(client, teacher_agent):
    data = teacher_agent
    proposed = client.post(f"/api/courses/{data['course']['id']}/assignments", json={"title": "Old grant", "instructions": "Proposed before binding change"}, headers=data["headers"])
    assert proposed.status_code == 202
    placement_input = {key: value for key, value in data["placement"].items() if key in {"name", "core_agent_id", "core_binding_id", "core_consumer_service_id", "environment", "task_id", "active", "purpose", "core_resource_id"}}
    placement_input["core_binding_id"] = str(uuid4())
    changed = client.put(f"/api/schools/{data['school']}/agents/{data['placement']['id']}", json=placement_input, headers=data["admin"])
    assert changed.status_code == 200
    approval = client.post(f"/api/changes/{proposed.json()['change_request']['id']}/approve", headers=data["admin"])
    assert approval.status_code == 409 and "placement changed" in approval.json()["detail"]


def test_school_audit_export_is_partial_and_excludes_other_school_events(client, app):
    admin = login(client, "demo-admin")
    school_a = default_school_id(app.state.settings.tenant_id)
    school_b = new_school(client, admin)
    resource = client.post(f"/api/schools/{school_a}/resources", json={"name": "School A only", "content": "A material"}, headers=admin).json()
    secret_b = client.post(f"/api/schools/{school_b}/resources", json={"name": "School B only", "content": "B material"}, headers=admin).json()
    export = client.get(f"/api/schools/{school_a}/audit/export")
    assert export.status_code == 200
    body = export.json()
    assert body["complete_tenant_chain"] is False and body["scope"] == "school_partial"
    assert "head" not in body and resource["id"] in export.text and secret_b["id"] not in export.text
    headers = login(client, "demo-school")
    assert client.get(f"/api/schools/{school_a}/audit/export").status_code == 200
    assert client.get(f"/api/schools/{school_b}/audit/export").status_code == 403
    assert client.get("/api/admin/audit/export").status_code == 403
    login(client, "demo-instructor")
    assert client.get(f"/api/schools/{school_a}/audit/export").status_code == 403


def test_school_audit_does_not_attribute_denied_agent_attempt_to_target_school(client, app, teacher_agent):
    data = teacher_agent
    other = new_school(client, data["admin"])
    course = client.post("/api/courses", json={"title": "Foreign target", "school_id": other}, headers=data["admin"]).json()
    assert client.get(f"/api/courses/{course['id']}", headers=data["headers"]).status_code == 403
    own_export = client.get(f"/api/schools/{data['school']}/audit/export").json()
    foreign_export = client.get(f"/api/schools/{other}/audit/export").json()
    own_attempt = [e for e in own_export["events"] if e["event_type"] == "agent.request_admitted"]
    assert own_attempt and own_attempt[-1]["payload"]["course_id"] == course["id"]
    assert not any(e["event_type"] == "agent.request_admitted" for e in foreign_export["events"])
