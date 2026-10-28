"""Synthetic names/grade selection with a deliberately narrow PII boundary."""
import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select

from academy.course_enrollment import grade_level
from academy.db import Audit, Record, canonical
from academy.permissions import default_school_id, membership_id
from conftest import login
from test_family_accounts import PASSWORD, parent, password_login, student
from test_school_permissions import teacher_agent


def math_class(client, headers, *, grades=(3,), school_id=None, teacher_id=None):
    payload = {"title": "Synthetic grade-independent class", "curriculum": {"state": "US-NY", "subject": "mathematics", "grades": list(grades), "academic_year": "2026-2027"}}
    if school_id:
        payload["school_id"] = school_id
    if teacher_id:
        payload["teacher_id"] = teacher_id
    response = client.post("/api/courses", json=payload, headers=headers)
    assert response.status_code == 201, response.text
    return response.json()


def search(client, headers, course, **body):
    return client.post(f"/api/courses/{course}/enrollment-candidates/search", json=body, headers=headers)


def ids(response):
    assert response.status_code == 200, response.text
    return {row["id"] for row in response.json()["candidates"]}


@pytest.mark.parametrize("value,expected", [("3", 3), ("Grade 3", 3), ("3rd", 3), (" 3rd grade ", 3),
    ("10th grade", 10), ("12", 12), ("K", 0), ("Kindergarten", 0), ("Grade K", 0),
    ("PK", -1), ("Pre-K", -1), ("Prekindergarten", -1), ("-1", -1), ("", None),
    ("Custom mixed-age group", None), ("13", None), ("grade 33", None)])
def test_explicit_grade_aliases_never_infer_age_or_custom_label(value, expected):
    assert grade_level(value) == expected


def test_course_default_names_search_all_grade_override_and_no_unrelated_pii(client, app):
    headers = login(client, "demo-admin")
    course = math_class(client, headers)
    third = student(client, app, headers, username="third.synthetic", name="Third Grade Sentinel", preferred_name="Train Sentinel", grade="3rd grade", course_ids=[])
    tenth = student(client, app, headers, username="tenth.synthetic", name="Tenth Grade Sentinel", grade="10", course_ids=[])
    adult = parent(client, headers, third)
    response = search(client, headers, course["id"])
    assert ids(response) == {third["id"]}
    value = response.json()
    assert value["course_grade_levels"] == [3] and value["selected_grade_levels"] == [3]
    candidate = value["candidates"][0]
    assert candidate == {"id": third["id"], "name": third["name"], "display_name": "Train Sentinel (Third Grade Sentinel)",
                         "preferred_name": "Train Sentinel", "username": third["username"], "grade": "3rd grade", "grade_level": 3,
                         "academic_year": "2026-2027", "role": "student", "principal_kind": "human"}
    assert ids(search(client, headers, course["id"], grade="10")) == {tenth["id"]}
    assert {third["id"], tenth["id"]} <= ids(search(client, headers, course["id"], grade="all"))
    assert adult["id"] not in ids(search(client, headers, course["id"], grade="all"))
    for needle in ("TRAIN sentinel", "Third Grade", "THIRD.synthetic"):
        found = search(client, headers, course["id"], search=needle)
        assert ids(found) == {third["id"]}
        assert "?" not in str(found.request.url) and needle not in str(found.request.url)
    for forbidden in (third["date_of_birth"], third["email"], third["phone"], third["notes"], PASSWORD, "profile_cipher", "parent_ids", "student_ids", "progress_snapshot"):
        assert forbidden not in response.text
    # A grade is a default discovery filter, not an enrollment prohibition.
    enrolled = client.post(f"/api/courses/{course['id']}/enrollments", json={"user_id": tenth["id"], "role": "student"}, headers=headers)
    assert enrolled.status_code == 200, enrolled.text
    assert tenth["id"] not in ids(search(client, headers, course["id"], grade="all"))
    roster = client.get(f"/api/courses/{course['id']}").json()["members"]
    assert next(row for row in roster if row["user_id"] == tenth["id"])["name"] == tenth["name"]
    catalog = client.get("/api/users").text
    assert third["name"] not in catalog and tenth["name"] not in catalog
    assert client.get("/api/audit/verify").json()["valid"]
    with app.state.database.factory() as db:
        stored = "\n".join(row.data for row in db.scalars(select(Record))) + "\n".join(row.payload for row in db.scalars(select(Audit)))
        for private in (third["name"], third["preferred_name"], third["username"], tenth["name"], third["email"], third["date_of_birth"]):
            assert private not in stored
        assert any(row.event_type == "enrollment.candidates_viewed" for row in db.scalars(select(Audit)))
        assert any(row.event_type == "enrollment.roster_viewed" for row in db.scalars(select(Audit)))


def test_custom_class_all_grades_unplaced_distinguishes_custom_label_and_paging(client, app):
    headers = login(client, "demo-admin")
    course = client.post("/api/courses", json={"title": "Third grade words do not establish grade placement"}, headers=headers).json()
    unplaced = student(client, app, headers, username="unplaced.synthetic", name="Alphabetical Unplaced", grade="", course_ids=[])
    custom = student(client, app, headers, username="custom.synthetic", name="Custom Placement", grade="Mixed-age expedition", course_ids=[])
    regular = student(client, app, headers, username="third.synthetic", grade="3", course_ids=[])
    response = search(client, headers, course["id"])
    assert {unplaced["id"], custom["id"], regular["id"]} <= ids(response)
    assert response.json()["course_grade_levels"] == [] and response.json()["selected_grade_levels"] is None
    unplaced_ids = ids(search(client, headers, course["id"], grade="unplaced"))
    assert unplaced["id"] in unplaced_ids and custom["id"] not in unplaced_ids
    candidate = next(row for row in response.json()["candidates"] if row["id"] == custom["id"])
    assert candidate["grade"] == "Mixed-age expedition" and candidate["grade_level"] is None
    page = search(client, headers, course["id"], grade="all", limit=1, offset=1).json()
    assert len(page["candidates"]) == 1 and page["total"] == response.json()["total"]
    assert page["candidates"] == response.json()["candidates"][1:2]
    assert ids(search(client, headers, course["id"], search="does-not-match")) == set()


def test_multiple_course_grades_use_current_school_placement_not_prior_year_or_other_school(client, app):
    headers = login(client, "demo-admin")
    course = math_class(client, headers, grades=(3, 4))
    child = student(client, app, headers, username="promoted.synthetic", grade="3", course_ids=[])
    sid = default_school_id(app.state.settings.tenant_id)
    promoted = client.post(f"/api/admin/family-accounts/{child['id']}/enrollments", json={"expected_version": child["version"], "school_id": sid, "academic_year": "2027-2028", "grade": "10", "course_ids": []}, headers=headers)
    assert promoted.status_code == 200, promoted.text
    other = client.post("/api/schools", json={"name": "Other synthetic school"}, headers=headers).json()["id"]
    placed = client.post(f"/api/admin/family-accounts/{child['id']}/enrollments", json={"expected_version": promoted.json()["version"], "school_id": other, "academic_year": "2027-2028", "grade": "3", "course_ids": []}, headers=headers)
    assert placed.status_code == 200, placed.text
    assert child["id"] not in ids(search(client, headers, course["id"]))
    current = next(row for row in search(client, headers, course["id"], grade="all").json()["candidates"] if row["id"] == child["id"])
    assert current["grade"] == "10" and current["academic_year"] == "2027-2028"
    fourth = student(client, app, headers, username="fourth.synthetic", grade="4", course_ids=[])
    assert ids(search(client, headers, course["id"])) == {fourth["id"]}
    assert search(client, headers, course["id"]).json()["selected_grade_levels"] == [3, 4]


@pytest.mark.parametrize("viewer", ["demo-admin", "demo-school", "demo-instructor"])
def test_only_current_human_course_managers_receive_minimum_names(client, app, viewer):
    headers = login(client, "demo-admin")
    child = student(client, app, headers, course_ids=[])
    login_headers = login(client, viewer)
    assert child["id"] in ids(search(client, login_headers, "demo-course", grade="all"))
    assert client.get("/api/admin/family-accounts/" + child["id"]).status_code == (200 if viewer == "demo-admin" else 403)
    if viewer == "demo-instructor":
        admin = login(client, "demo-admin")
        course = math_class(client, admin)
        headers = login(client, viewer)
        assert search(client, headers, course["id"]).status_code == 403
        sid = default_school_id(app.state.settings.tenant_id)
        admin = login(client, "demo-admin")
        assert client.delete(f"/api/schools/{sid}/members/demo-instructor", headers=admin).status_code == 200
        headers = login(client, viewer)
        assert search(client, headers, "demo-course", grade="all").status_code == 403


def test_students_parents_agents_foreign_schools_and_tenants_cannot_decrypt_candidates(client, app, monkeypatch):
    admin = login(client, "demo-admin")
    child = student(client, app, admin, course_ids=[])
    adult = parent(client, admin, child)
    foreign = client.post("/api/schools", json={"name": "Foreign scope"}, headers=admin).json()["id"]
    course = math_class(client, admin, school_id=foreign)
    with app.state.database.write() as db:
        db.add(Record(id="foreign-tenant-course", tenant_id="other-tenant", kind="course", data=canonical({"title": "Foreign tenant"})))
    real_decrypt = app.state.auth.family.decrypt
    calls = []
    def observed(*args, **kwargs):
        calls.append(kwargs.get("owner_id", args[2] if len(args) > 2 else None))
        return real_decrypt(*args, **kwargs)
    monkeypatch.setattr(app.state.auth.family, "decrypt", observed)
    for viewer in ("demo-student", "demo-instructor", "demo-school"):
        headers = login(client, viewer)
        if viewer == "demo-student":
            assert search(client, headers, "demo-course", grade="all").status_code == 403
        assert search(client, headers, course["id"], grade="all").status_code == 403
        assert search(client, headers, "foreign-tenant-course", grade="all").status_code == 404
    assert calls == []
    with TestClient(app, base_url=app.state.settings.app_url) as browser:
        headers = password_login(browser)
        calls.clear()
        assert search(browser, headers, "demo-course", grade="all").status_code == 403
        assert all(owner == adult["id"] for owner in calls)  # Authentication may read the parent's own name.
    # A forged machine principal must fail the explicit human gate even if an
    # upstream permission implementation were to admit it as a course manager.
    import academy.main as main
    original_actor = app.state.auth.actor
    monkeypatch.setattr(app.state.auth, "actor", lambda request: {**original_actor(request), "principal_kind": "agent", "school_id": foreign})
    monkeypatch.setattr(main, "school_role", lambda *_: "admin")
    original_require = main.require_school
    def permit_course_only(db, identity, school_id, settings, roles=None, human=False):
        if human:
            return original_require(db, identity, school_id, settings, roles, human)
        return "admin"
    monkeypatch.setattr(main, "require_school", permit_course_only)
    calls.clear()
    assert search(client, login(client, "demo-admin"), course["id"], grade="all").status_code == 403
    assert calls == []


def test_actual_assigned_core_agent_keeps_pseudonymous_roster_and_cannot_search(client, app, teacher_agent):
    data = teacher_agent
    child = student(client, app, data["admin"], course_ids=[data["course"]["id"]])
    response = client.get(f"/api/courses/{data['course']['id']}", headers=data["headers"])
    assert response.status_code == 200, response.text
    member = next(row for row in response.json()["members"] if row["user_id"] == child["id"])
    assert member["name"] == "Student " + child["id"][:8] and member["principal_kind"] == "human"
    teacher = next(row for row in response.json()["members"] if row["user_id"] == data["identity"]["id"])
    assert teacher["principal_kind"] == "agent" and teacher["role"] == "teacher"
    human_view = client.get(f"/api/courses/{data['course']['id']}")
    assert next(row for row in human_view.json()["members"] if row["user_id"] == data["identity"]["id"])["principal_kind"] == "agent"
    for private in (child["name"], child["username"], child["preferred_name"], child["email"]):
        assert private not in response.text
    assert search(client, data["headers"], data["course"]["id"], grade="all").status_code == 403


def test_inactive_disabled_nonstudents_and_other_school_students_are_excluded(client, app):
    headers = login(client, "demo-admin")
    course = math_class(client, headers)
    active = student(client, app, headers, username="active.synthetic", grade="3", course_ids=[])
    disabled = student(client, app, headers, username="disabled.synthetic", grade="3", course_ids=[])
    inactive = student(client, app, headers, username="inactive.synthetic", grade="3", course_ids=[])
    agent = student(client, app, headers, username="machine.synthetic", grade="3", course_ids=[])
    other_sid = client.post("/api/schools", json={"name": "Other school"}, headers=headers).json()["id"]
    other = student(client, app, headers, username="other.synthetic", school_id=other_sid, grade="3", course_ids=[])
    sid = default_school_id(app.state.settings.tenant_id)
    with app.state.database.write() as db:
        user = db.get(Record, disabled["id"])
        user.set({**user.value, "disabled": True})
        member = db.get(Record, membership_id(sid, inactive["id"]))
        member.set({**member.value, "active": False})
        machine = db.get(Record, agent["id"])
        machine.set({**machine.value, "principal_kind": "agent"})
    assert ids(search(client, headers, course["id"])) == {active["id"]}
    for identifier in (disabled["id"], inactive["id"], other["id"], agent["id"]):
        assert client.post(f"/api/courses/{course['id']}/enrollments", json={"user_id": identifier}, headers=headers).status_code == 422


def test_authorized_search_decrypts_selected_school_only_and_student_roster_stays_private(client, app, monkeypatch):
    headers = login(client, "demo-admin")
    course = math_class(client, headers)
    own = student(client, app, headers, username="own.synthetic", grade="3", course_ids=[])
    other_sid = client.post("/api/schools", json={"name": "Other school"}, headers=headers).json()["id"]
    foreign = student(client, app, headers, username="foreign.synthetic", school_id=other_sid, grade="3", course_ids=[])
    original = app.state.auth.family.decrypt
    owners = []
    def observe(*args, **kwargs):
        owners.append(kwargs.get("owner_id", args[2] if len(args) > 2 else None))
        return original(*args, **kwargs)
    monkeypatch.setattr(app.state.auth.family, "decrypt", observe)
    assert ids(search(client, headers, course["id"])) == {own["id"]}
    assert own["id"] in owners and foreign["id"] not in owners
    assert client.post(f"/api/courses/{course['id']}/enrollments", json={"user_id": own["id"]}, headers=headers).status_code == 200
    with TestClient(app, base_url=app.state.settings.app_url) as browser:
        local = password_login(browser, "own.synthetic")
        value = browser.get(f"/api/courses/{course['id']}").json()
        assert value["members"] == []
        assert search(browser, local, course["id"], grade="all").status_code == 403
    with app.state.database.write() as db:
        school = db.get(Record, course["school_id"])
        school.set({**school.value, "disabled": True})
    assert search(client, headers, course["id"]).status_code == 403
    owners.clear()
    response = client.get(f"/api/courses/{course['id']}")
    assert response.status_code == 200
    assert own["name"] not in response.text and owners == []


@pytest.mark.parametrize("body", [{"grade": "13"}, {"grade": "Grade 3"}, {"offset": -1}, {"offset": 100001}, {"limit": 0},
    {"limit": 101}, {"search": "sensitive-sentinel" * 10}, {"school_id": "foreign-school"}, {"role": "teacher"}])
def test_search_body_bounds_forbidden_scope_and_validation_does_not_echo_pii(client, body):
    headers = login(client, "demo-admin")
    response = search(client, headers, "demo-course", **body)
    assert response.status_code == 422
    assert "sensitive-sentinel" not in response.text


def test_candidate_search_requires_csrf_and_does_not_offer_get_name_search(client):
    login(client, "demo-admin")
    response = search(client, {}, "demo-course", search="Synthetic Private Name")
    assert response.status_code == 403
    assert client.get("/api/courses/demo-course/enrollment-candidates/search").status_code == 405
