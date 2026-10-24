"""Class configuration uses profiles independently of staff principal ownership."""
from sqlalchemy import select

import pytest

from academy.db import Audit, Record, canonical
from academy.permissions import bind_scope
from conftest import login
from test_teacher_onboarding import create, assign, approve, path, school_id, new_school, placement
from test_teacher_models import connection, policy, save, activate


GRADE_THREE = {"state": "US-NY", "subject": "mathematics", "grades": [3], "academic_year": "2026-2027"}
PRESERVED_KINDS = ["teacher_profile", "teacher_profile_course", "teacher_profile_revision", "course_corpus", "corpus_approval", "knowledge_workflow_run", "teacher_model_config", "teacher_model_revision", "teacher_model_approval"]


def records(app, kinds=None):
    with app.state.database.factory() as db:
        query = select(Record).order_by(Record.id)
        if kinds:
            query = query.where(Record.kind.in_(kinds))
        return [(row.id, row.kind, row.parent_id, row.owner_id, row.data) for row in db.scalars(query)]


def classroom(client, headers, school, **fields):
    response = client.post("/api/courses", json={"school_id": school, "title": "Grade 3 Mathematics", "curriculum": GRADE_THREE, **fields}, headers=headers)
    assert response.status_code == 201, response.text
    return response.json()


def update(client, headers, course, **fields):
    return client.put(f"/api/courses/{course['id']}", json={"title": course["title"], "description": course.get("description", ""), **fields}, headers=headers)


def test_class_creation_assigns_unready_ai_and_human_profiles_without_principal_grants(client, app):
    headers = login(client, "demo-admin"); school = school_id(app)
    ai = create(client, headers, school, kind="ai", name="Jim", ai_behavior={"tone": "warm", "instructions": "Ask a small question first."})
    human = create(client, headers, school, name="School teacher", contact_email="private@example.test")
    course = classroom(client, headers, school, teacher_id="demo-instructor", teacher_profile_ids=[ai["id"], human["id"]])
    assert course["teacher_id"] == "demo-instructor"
    assert set(course["teacher_profile_ids"]) == {ai["id"], human["id"]}
    assert course["teachers"] == []
    assert {item["name"] for item in course["assigned_teacher_profiles"]} == {"Jim", "School teacher"}
    for summary in course["assigned_teacher_profiles"]:
        assert set(summary) == {"id", "kind", "name", "status", "approval_current", "context_ready"}
        assert summary["approval_current"] is False and summary["context_ready"] is False
    profile = client.get(path(school, ai["id"])).json()
    assert profile["course_ids"] == [course["id"]] and profile["version"] == 2
    assert not profile["assigned_courses"][0]["corpus"]["ready_for_teaching"]
    assert profile["latest_onboarding_run"]["reason"] == "course_configuration"
    assert not profile["latest_onboarding_run"]["authority_granted"]
    with app.state.database.factory() as db:
        assert db.get(Record, course["id"]).owner_id == "demo-instructor"
        enrollments = list(db.scalars(select(Record).where(Record.kind == "enrollment", Record.parent_id == course["id"])))
        assert [row.owner_id for row in enrollments] == ["demo-instructor"]
        assert not list(db.scalars(select(Record).where(Record.kind.in_(["corpus_approval", "teacher_profile_revision", "teacher_model_approval"]))))
    # An actual class teacher may see the safe planning labels, not profile data.
    login(client, "demo-instructor")
    assert client.get(f"/api/courses/{course['id']}").json()["assigned_teacher_profiles"] == course["assigned_teacher_profiles"]
    headers = login(client, "demo-admin")
    assert client.post(f"/api/courses/{course['id']}/enrollments", json={"user_id": "demo-student"}, headers=headers).status_code == 200
    login(client)
    student_course = client.get(f"/api/courses/{course['id']}").json()
    assert student_course["teacher_profile_ids"] == [] and student_course["assigned_teacher_profiles"] == [] and student_course["teachers"] == []


def test_existing_teacher_assignment_is_preselected_and_noop_preserves_exact_approvals_and_models(client, app):
    headers = login(client, "demo-school"); school = school_id(app)
    course = classroom(client, headers, school)
    profile = approve(client, headers, school, assign(client, headers, school, create(client, headers, school, kind="ai", name="Jim"), [course["id"]]))
    integrated = connection(client, headers, school)
    activate(client, headers, school, profile, save(client, headers, school, profile, policy(integrated)))
    fetched = client.get(f"/api/courses/{course['id']}").json()
    assert fetched["teacher_profile_ids"] == [profile["id"]]
    assert fetched["assigned_teacher_profiles"][0]["approval_current"]
    assert fetched["teachers"] == []  # Pending math corpus must not hide its configuration.
    before = records(app, PRESERVED_KINDS)
    response = update(client, headers, fetched, curriculum=GRADE_THREE, teacher_profile_ids=[profile["id"]], expected_teacher_profile_ids=[profile["id"]])
    assert response.status_code == 200, response.text
    assert records(app, PRESERVED_KINDS) == before
    assert client.get(path(school, profile["id"])).json()["approval_current"]


def test_assigning_and_clearing_one_class_preserves_other_classes_personality_and_model_policy(client, app):
    headers = login(client, "demo-admin"); school = school_id(app)
    first, second = classroom(client, headers, school), classroom(client, headers, school, title="Grade 3 Science")
    profile = approve(client, headers, school, assign(client, headers, school, create(client, headers, school, kind="ai", name="Jim", biography="Persistent teacher identity", ai_behavior={"tone": "formal", "instructions": "Use short practice questions."}), [first["id"]]))
    integrated = connection(client, headers, school)
    activate(client, headers, school, profile, save(client, headers, school, profile, policy(integrated)))
    models = records(app, ["teacher_model_config", "teacher_model_revision", "teacher_model_approval"])
    assert models
    with app.state.database.factory() as db:
        existing_link = next(row for row in db.scalars(select(Record).where(Record.kind == "teacher_profile_course", Record.parent_id == profile["id"])) if row.value["course_id"] == first["id"])
        original_link = (existing_link.id, existing_link.data)
    added = update(client, headers, second, teacher_profile_ids=[profile["id"]], expected_teacher_profile_ids=[])
    assert added.status_code == 200, added.text
    after = client.get(path(school, profile["id"])).json()
    assert set(after["course_ids"]) == {first["id"], second["id"]}
    assert after["version"] == profile["version"] + 1 and after["approved_revision_id"] is None
    for key in ["id", "name", "biography", "ai_behavior", "principal_id", "agent_placement_id", "tutor_model"]:
        assert after[key] == profile[key]
    assert records(app, ["teacher_model_config", "teacher_model_revision", "teacher_model_approval"]) == models
    removed = update(client, headers, second, teacher_profile_ids=[], expected_teacher_profile_ids=[profile["id"]])
    assert removed.status_code == 200, removed.text
    assert removed.json()["teacher_profile_ids"] == [] and removed.json()["teacher_id"] == second["teacher_id"]
    assert client.get(path(school, profile["id"])).json()["course_ids"] == [first["id"]]
    with app.state.database.factory() as db:
        assert db.get(Record, original_link[0]).data == original_link[1]
    assert records(app, ["teacher_model_config", "teacher_model_revision", "teacher_model_approval"]) == models


def test_omitted_selection_preserves_links_and_stale_explicit_selection_rolls_back_course_edit(client, app):
    headers = login(client, "demo-admin"); school = school_id(app)
    first, second = create(client, headers, school, kind="ai"), create(client, headers, school, kind="ai")
    course = classroom(client, headers, school, teacher_profile_ids=[first["id"]])
    before = records(app, PRESERVED_KINDS)
    assert update(client, headers, course).status_code == 200
    assert records(app, PRESERVED_KINDS) == before
    assert update(client, headers, course, teacher_profile_ids=[first["id"], second["id"]], expected_teacher_profile_ids=[first["id"]]).status_code == 200
    before = records(app)
    with app.state.database.factory() as db:
        audit_count = len(list(db.scalars(select(Audit))))
    stale = update(client, headers, course, title="Stale overwrite", teacher_profile_ids=[], expected_teacher_profile_ids=[first["id"]])
    assert stale.status_code == 409, stale.text
    assert records(app) == before
    with app.state.database.factory() as db:
        assert len(list(db.scalars(select(Audit)))) == audit_count


@pytest.mark.parametrize("invalid", ["foreign_school", "foreign_tenant", "archived", "missing"])
def test_profile_scope_or_archive_failure_rolls_back_whole_course_transaction(client, app, invalid):
    headers = login(client, "demo-admin"); school = school_id(app)
    valid = create(client, headers, school, kind="ai")
    course = classroom(client, headers, school)
    if invalid == "foreign_school":
        invalid_id = create(client, headers, new_school(client, headers), kind="ai")["id"]
    elif invalid == "archived":
        archived = create(client, headers, school, kind="ai")
        assert client.delete(path(school, archived["id"]), headers=headers).status_code == 200
        invalid_id = archived["id"]
    elif invalid == "foreign_tenant":
        invalid_id = "foreign-tenant-teacher-profile"
        with app.state.database.write() as db:
            row = Record(id=invalid_id, tenant_id="foreign-tenant", kind="teacher_profile", parent_id=school, data=canonical({"name": "Hidden", "kind": "ai", "status": "draft"}))
            db.add(row); db.flush(); bind_scope(db, row, school)
    else:
        invalid_id = "missing-profile"
    before = records(app)
    failed = update(client, headers, course, title="Must roll back", teacher_profile_ids=[valid["id"], invalid_id])
    assert failed.status_code == (409 if invalid == "archived" else 404), failed.text
    assert records(app) == before
    failed_create = client.post("/api/courses", json={"school_id": school, "title": "No partial course", "teacher_profile_ids": [valid["id"], invalid_id]}, headers=headers)
    assert failed_create.status_code == failed.status_code
    assert records(app) == before


@pytest.mark.parametrize("user", ["demo-instructor", "demo-student"])
def test_only_human_school_managers_can_change_profile_assignments(client, app, user):
    headers = login(client, "demo-admin"); school = school_id(app)
    profile = create(client, headers, school, kind="ai")
    course = classroom(client, headers, school, teacher_id="demo-instructor")
    headers = login(client, user)
    before = records(app)
    assert update(client, headers, course, teacher_profile_ids=[profile["id"]]).status_code == 403
    assert client.post("/api/courses", json={"school_id": school, "title": "No profile grant", "teacher_profile_ids": []}, headers=headers).status_code == 403
    assert records(app) == before


def test_agent_cannot_smuggle_profile_assignments_into_course_proposals(client, app, monkeypatch):
    headers = login(client, "demo-admin"); school = school_id(app)
    agent, claims = placement(client, headers, school)
    profile = create(client, headers, school, kind="ai")
    course = classroom(client, headers, school, teacher_id=agent["principal_id"])
    identity = {"id": agent["principal_id"], "tenant_id": app.state.settings.tenant_id, "role": "teacher", "principal_kind": "agent", "school_id": school, "placement_id": agent["id"], "permissions": ["academy-use", "academy-teach"], "account_id": claims["core_agent_id"], "binding_id": claims["core_binding_id"], "consumer_service_id": claims["core_consumer_service_id"], "environment": claims["environment"], "task_id": claims["task_id"]}
    monkeypatch.setattr(app.state.auth, "actor", lambda request: dict(identity))
    before = records(app)
    assert update(client, {}, course, teacher_profile_ids=[profile["id"]]).status_code == 403
    assert client.post("/api/courses", json={"school_id": school, "title": "No profile proposal", "teacher_profile_ids": []}).status_code == 403
    assert records(app) == before


def test_course_assignment_cannot_bypass_teacher_class_limit(client, app):
    headers = login(client, "demo-admin"); school = school_id(app)
    profile = create(client, headers, school, kind="ai")
    with app.state.database.write() as db:
        for index in range(100):
            course = Record(id=f"synthetic-class-{index}", tenant_id=app.state.settings.tenant_id, kind="course", owner_id="demo-admin", data=canonical({"title": f"Existing class {index}", "description": "", "curriculum": None}))
            link = Record(id=f"synthetic-link-{index}", tenant_id=course.tenant_id, kind="teacher_profile_course", parent_id=profile["id"], owner_id="demo-admin", data=canonical({"course_id": course.id, "active": True, "curriculum": None, "corpus": None}))
            db.add_all([course, link]); db.flush()
            bind_scope(db, course, school); bind_scope(db, link, school)
    before = records(app)
    response = client.post("/api/courses", json={"school_id": school, "title": "Over capacity", "teacher_profile_ids": [profile["id"]]}, headers=headers)
    assert response.status_code == 422, response.text
    assert records(app) == before


@pytest.mark.parametrize("fields", [{"teacher_profile_ids": None}, {"teacher_profile_ids": ["same", "same"]}, {"teacher_profile_ids": [""]}, {"teacher_profile_ids": ["x" * 65]}, {"teacher_profile_ids": [str(n) for n in range(101)]}, {"expected_teacher_profile_ids": None}, {"expected_teacher_profile_ids": []}])
def test_profile_selection_rejects_invalid_or_ambiguous_inputs(client, app, fields):
    headers = login(client, "demo-admin")
    assert client.post("/api/courses", json={"school_id": school_id(app), "title": "Invalid selection", **fields}, headers=headers).status_code == 422
