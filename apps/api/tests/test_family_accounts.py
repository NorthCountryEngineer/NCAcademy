"""Synthetic local-family administration, privacy and longitudinal access checks."""
import json

import pytest
from fastapi import HTTPException
from fastapi.testclient import TestClient
from sqlalchemy import select

from academy.db import Audit, Login, Record, canonical
from academy.family_accounts import validate_parent_account
from academy.permissions import default_school_id, membership_id
from conftest import login


PASSWORD = "synthetic student password 381"


def student(client, app, headers, **overrides):
    payload = {"kind": "student", "username": "sample.student", "password": PASSWORD, "name": "Synthetic Learner Sentinel", "preferred_name": "Sample Sentinel", "date_of_birth": "2010-04-12", "email": "student-sentinel@example.invalid", "phone": "555-0100 sentinel", "notes": "Private enrollment note sentinel", "must_change_password": False, "school_id": default_school_id(app.state.settings.tenant_id), "academic_year": "2026-2027", "grade": "10", "course_ids": ["demo-course"], **overrides}
    response = client.post("/api/admin/family-accounts", json=payload, headers=headers)
    assert response.status_code == 201, response.text
    return response.json()


def parent(client, headers, child, **overrides):
    response = client.post("/api/admin/family-accounts", json={"kind": "parent", "username": "sample.parent", "password": PASSWORD, "name": "Synthetic Parent Sentinel", "student_ids": [child["id"]], "must_change_password": False, **overrides}, headers=headers)
    assert response.status_code == 201, response.text
    return response.json()


def password_login(browser, username="sample.parent"):
    response = browser.post("/api/auth/password", json={"username": username, "password": PASSWORD}, headers={"Origin": "http://localhost:8090"})
    assert response.status_code == 200, response.text
    return {"Origin": "http://localhost:8090", "X-CSRF-Token": response.json()["csrf_token"]}


def test_admin_creates_encrypted_local_student_not_core_or_demo_identity(client, app):
    headers = login(client, "demo-admin")
    child = student(client, app, headers)
    assert child["username"] == "sample.student"
    assert child["name"] == "Synthetic Learner Sentinel"
    assert child["kind"] == "student" and not child["disabled"]
    assert child["parent_ids"] == []
    assert child["enrollments"][0]["course_ids"] == ["demo-course"]
    serialized = json.dumps(child)
    assert all(secret not in serialized for secret in (PASSWORD, "profile_cipher", "password_cipher", "scrypt", "digest", "salt"))
    config = client.get("/api/auth/config").json()
    assert child["id"] not in [row["id"] for row in config["demo_users"]]
    with app.state.database.factory() as db:
        rows = list(db.scalars(select(Record)))
        evidence = "\n".join(row.data for row in rows) + "\n".join(row.payload for row in db.scalars(select(Audit)))
        for secret in (PASSWORD, child["name"], child["username"], child["email"], child["phone"], child["notes"], child["date_of_birth"], child["preferred_name"]):
            assert secret not in evidence
        account = db.get(Record, "local-account:" + child["id"])
        assert set(account.value) == {"profile_cipher", "password_cipher", "version"}
        assert account.value["profile_cipher"]["alg"] == "AES-256-GCM"
        user = db.get(Record, child["id"])
        assert user.value["name"] != child["name"] and user.value["auth_provider"] == "academy"
        member = db.get(Record, membership_id(child["enrollments"][0]["school_id"], child["id"]))
        assert member.value["role"] == "student" and member.value["active"]


@pytest.mark.parametrize("user", ["demo-school", "demo-instructor", "demo-student"])
def test_only_global_human_admin_can_access_family_pii_or_manage_accounts(client, app, user):
    admin_headers = login(client, "demo-admin")
    child = student(client, app, admin_headers)
    headers = login(client, user)
    assert client.get("/api/admin/family-accounts").status_code == 403
    assert client.get("/api/admin/family-accounts/" + child["id"]).status_code == 403
    assert client.get("/api/admin/family-accounts/" + child["id"] + "/history").status_code == 403
    assert client.patch("/api/admin/family-accounts/" + child["id"], json={"expected_version": child["version"], "name": "Unauthorized"}, headers=headers).status_code == 403
    assert client.put("/api/admin/family-accounts/" + child["id"] + "/password", json={"expected_version": child["version"], "new_password": PASSWORD}, headers=headers).status_code == 403


def test_casefolded_usernames_unique_and_reassignment_is_atomic(client, app):
    headers = login(client, "demo-admin")
    child = student(client, app, headers, username="Sample.Student")
    assert child["username"] == "sample.student"
    response = client.post("/api/admin/family-accounts", json={"kind": "student", "username": "SAMPLE.STUDENT", "password": PASSWORD, "name": "Other"}, headers=headers)
    assert response.status_code == 409
    updated = client.patch("/api/admin/family-accounts/" + child["id"], json={"expected_version": child["version"], "username": "renamed.student", "preferred_name": "Updated Sentinel"}, headers=headers)
    assert updated.status_code == 200, updated.text
    assert updated.json()["username"] == "renamed.student"
    with TestClient(app, base_url=app.state.settings.app_url) as browser:
        response = browser.post("/api/auth/password", json={"username": "sample.student", "password": PASSWORD}, headers={"Origin": "http://localhost:8090"})
        assert response.status_code == 401
        password_login(browser, "renamed.student")


def test_expected_version_blocks_stale_profile_password_lifecycle_and_enrollment(client, app):
    headers = login(client, "demo-admin")
    child = student(client, app, headers)
    response = client.patch("/api/admin/family-accounts/" + child["id"], json={"expected_version": child["version"], "name": "A changed synthetic name"}, headers=headers)
    assert response.status_code == 200
    operations = [("patch", "", {"name": "Stale name"}), ("put", "/password", {"new_password": "replacement synthetic password"}), ("post", "/lifecycle", {"status": "graduated", "academic_year": "2026-2027"}), ("post", "/enrollments", {"school_id": child["enrollments"][0]["school_id"], "academic_year": "2027-2028", "grade": "11", "course_ids": []})]
    for method, suffix, payload in operations:
        result = getattr(client, method)("/api/admin/family-accounts/" + child["id"] + suffix, json={"expected_version": child["version"], **payload}, headers=headers)
        assert result.status_code == 409, result.text


def test_parent_requires_at_least_one_current_local_enrolled_child(client, app):
    headers = login(client, "demo-admin")
    base = {"kind": "parent", "username": "sample.parent", "password": PASSWORD, "name": "Synthetic Parent"}
    for children in ([], ["demo-student"], ["missing"]):
        response = client.post("/api/admin/family-accounts", json={**base, "student_ids": children}, headers=headers)
        assert response.status_code == 422
    child = student(client, app, headers, school_id=None, academic_year=None, grade="", course_ids=[])
    response = client.post("/api/admin/family-accounts", json={**base, "student_ids": [child["id"]]}, headers=headers)
    assert response.status_code == 422
    response = client.post("/api/admin/family-accounts/" + child["id"] + "/enrollments", json={"expected_version": child["version"], "school_id": default_school_id(app.state.settings.tenant_id), "academic_year": "2026-2027", "grade": "10", "course_ids": []}, headers=headers)
    assert response.status_code == 200
    adult = parent(client, headers, child)
    assert adult["student_ids"] == [child["id"]]
    assert adult["enrollments"] == []
    with app.state.database.factory() as db:
        assert validate_parent_account(db, app.state.settings.tenant_id, adult["id"])


def test_parent_links_are_not_students_or_school_members(client, app):
    headers = login(client, "demo-admin")
    child = student(client, app, headers)
    adult = parent(client, headers, child)
    response = client.post("/api/admin/family-accounts/" + adult["id"] + "/enrollments", json={"expected_version": adult["version"], "school_id": child["enrollments"][0]["school_id"], "academic_year": "2026-2027", "course_ids": ["demo-course"]}, headers=headers)
    assert response.status_code == 422
    with app.state.database.factory() as db:
        assert not list(db.scalars(select(Record).where(Record.kind.in_(["enrollment", "school_membership"]), Record.owner_id == adult["id"])))


def test_parent_own_view_has_minimal_progress_not_child_pii_or_conversations(client, app):
    headers = login(client, "demo-admin")
    child = student(client, app, headers)
    adult = parent(client, headers, child)
    with TestClient(app, base_url=app.state.settings.app_url) as browser:
        password_login(browser)
        response = browser.get("/api/me/family")
        assert response.status_code == 200, response.text
        value = response.json()
        assert value["account"]["id"] == adult["id"] and "notes" not in value["account"]
        assert value["children"][0]["name"] == child["name"]
        assert value["children"][0]["progress"]["assignment_count"] == 1
        assert set(value["children"][0]) == {"id", "name", "preferred_name", "enrollments", "progress"}
        assert child["email"] not in response.text and child["notes"] not in response.text
        assert browser.get("/api/admin/family-accounts/" + child["id"]).status_code == 403
        assert browser.get("/api/courses/demo-course").status_code == 403


def test_last_student_withdrawal_revokes_parent_session_and_preserves_history(client, app):
    headers = login(client, "demo-admin")
    child = student(client, app, headers)
    adult = parent(client, headers, child)
    with TestClient(app, base_url=app.state.settings.app_url) as browser:
        password_login(browser)
        response = client.post("/api/admin/family-accounts/" + child["id"] + "/lifecycle", json={"expected_version": child["version"], "status": "withdrawn", "academic_year": "2026-2027", "grade": "10", "note": "Synthetic transfer sentinel"}, headers=headers)
        assert response.status_code == 200, response.text
        assert response.json()["disabled"]
        assert not response.json()["enrollments"][0]["active"]
        assert browser.get("/api/auth/me").status_code == 401
    adult_after = client.get("/api/admin/family-accounts/" + adult["id"]).json()
    assert adult_after["disabled"] and adult_after["student_ids"] == [child["id"]]
    history = client.get("/api/admin/family-accounts/" + child["id"] + "/history").json()["events"]
    changed = [row for row in history if row["event_type"] == "lifecycle_changed"][0]
    assert changed["details"]["note"] == "Synthetic transfer sentinel"
    assert changed["details"]["removed_course_ids"] == ["demo-course"]
    with app.state.database.factory() as db:
        assert not list(db.scalars(select(Login).where(Login.user_id == adult["id"])))
        assert not list(db.scalars(select(Record).where(Record.kind == "enrollment", Record.owner_id == child["id"])))


def test_parent_remains_active_with_another_enrolled_child(client, app):
    headers = login(client, "demo-admin")
    first = student(client, app, headers)
    second = student(client, app, headers, username="second.student", name="Another synthetic child")
    adult = parent(client, headers, first, student_ids=[first["id"], second["id"]])
    response = client.post("/api/admin/family-accounts/" + first["id"] + "/lifecycle", json={"expected_version": first["version"], "status": "graduated", "academic_year": "2026-2027"}, headers=headers)
    assert response.status_code == 200
    assert client.get("/api/admin/family-accounts/" + adult["id"]).json()["disabled"] is False
    with TestClient(app, base_url=app.state.settings.app_url) as browser:
        password_login(browser)
        assert [row["id"] for row in browser.get("/api/me/family").json()["children"]] == [second["id"]]
    current = client.get("/api/admin/family-accounts/" + adult["id"]).json()
    retained = client.put("/api/admin/family-accounts/" + adult["id"] + "/students", json={"expected_version": current["version"], "student_ids": [first["id"], second["id"]]}, headers=headers)
    assert retained.status_code == 200
    assert retained.json()["student_ids"] == [first["id"], second["id"]]


def test_explicit_reactivation_and_reenrollment_restores_parent_not_prior_sessions(client, app):
    headers = login(client, "demo-admin")
    child = student(client, app, headers)
    adult = parent(client, headers, child)
    inactive = client.post("/api/admin/family-accounts/" + child["id"] + "/lifecycle", json={"expected_version": child["version"], "status": "suspended", "academic_year": "2026-2027"}, headers=headers).json()
    active = client.post("/api/admin/family-accounts/" + child["id"] + "/lifecycle", json={"expected_version": inactive["version"], "status": "active", "academic_year": "2027-2028"}, headers=headers)
    assert active.status_code == 200
    assert client.get("/api/admin/family-accounts/" + adult["id"]).json()["disabled"]
    response = client.post("/api/admin/family-accounts/" + child["id"] + "/enrollments", json={"expected_version": active.json()["version"], "school_id": child["enrollments"][0]["school_id"], "academic_year": "2027-2028", "grade": "11", "course_ids": []}, headers=headers)
    assert response.status_code == 200
    assert client.get("/api/admin/family-accounts/" + adult["id"]).json()["disabled"] is False


def test_grade_promotion_preserves_prior_year_and_removes_old_class(client, app):
    headers = login(client, "demo-admin")
    child = student(client, app, headers)
    response = client.post("/api/admin/family-accounts/" + child["id"] + "/enrollments", json={"expected_version": child["version"], "school_id": child["enrollments"][0]["school_id"], "academic_year": "2027-2028", "grade": "11", "course_ids": []}, headers=headers)
    assert response.status_code == 200, response.text
    enrollments = response.json()["enrollments"]
    assert len(enrollments) == 2
    assert [(row["academic_year"], row["grade"]) for row in enrollments if row["active"]] == [("2027-2028", "11")]
    history = client.get("/api/admin/family-accounts/" + child["id"] + "/history").json()["events"]
    last = [row for row in history if row["event_type"] == "enrollment_assigned"][-1]
    assert last["details"]["previous"][0]["grade"] == "10"
    assert last["details"]["previous_progress"]["assignment_count"] == 1
    assert [row for row in enrollments if not row["active"]][0]["progress_snapshot"]["assignment_count"] == 1
    with app.state.database.factory() as db:
        assert not list(db.scalars(select(Record).where(Record.kind == "enrollment", Record.owner_id == child["id"])))


def test_active_lifecycle_explicitly_promotes_grade_and_year_and_keeps_evidence(client, app):
    headers = login(client, "demo-admin")
    child = student(client, app, headers)
    response = client.post("/api/admin/family-accounts/" + child["id"] + "/lifecycle", json={"expected_version": child["version"], "status": "active", "school_id": child["enrollments"][0]["school_id"], "academic_year": "2027-2028", "grade": "11", "note": "Explicit synthetic promotion"}, headers=headers)
    assert response.status_code == 200, response.text
    placements = response.json()["enrollments"]
    assert [(row["academic_year"], row["grade"], row["course_ids"]) for row in placements if row["active"]] == [("2027-2028", "11", [])]
    assert [(row["academic_year"], row["grade"]) for row in placements if not row["active"]] == [("2026-2027", "10")]
    history = client.get("/api/admin/family-accounts/" + child["id"] + "/history").json()["events"]
    assert history[-1]["details"]["enrollment"]["previous"][0]["grade"] == "10"


def test_school_membership_revocation_cannot_leave_parent_logged_in(client, app):
    headers = login(client, "demo-admin")
    child = student(client, app, headers)
    adult = parent(client, headers, child)
    with TestClient(app, base_url=app.state.settings.app_url) as browser:
        password_login(browser)
        response = client.delete("/api/schools/" + child["enrollments"][0]["school_id"] + "/members/" + child["id"], headers=headers)
        assert response.status_code == 200, response.text
        assert browser.get("/api/auth/me").status_code == 401
    assert client.get("/api/admin/family-accounts/" + adult["id"]).json()["disabled"]
    assert not any(row["active"] for row in client.get("/api/admin/family-accounts/" + child["id"]).json()["enrollments"])


def test_different_schools_are_independent_and_cross_school_class_rejected(client, app):
    headers = login(client, "demo-admin")
    child = student(client, app, headers)
    school = client.post("/api/schools", json={"name": "Second synthetic school"}, headers=headers).json()
    base = {"expected_version": child["version"], "school_id": school["id"], "academic_year": "2026-2027", "grade": "10"}
    rejected = client.post("/api/admin/family-accounts/" + child["id"] + "/enrollments", json={**base, "course_ids": ["demo-course"]}, headers=headers)
    assert rejected.status_code == 422
    response = client.post("/api/admin/family-accounts/" + child["id"] + "/enrollments", json={**base, "course_ids": []}, headers=headers)
    assert response.status_code == 200
    assert len([row for row in response.json()["enrollments"] if row["active"]]) == 2
    assert client.get("/api/admin/family-accounts?school_id=" + school["id"]).json()["accounts"][0]["id"] == child["id"]


def test_password_reset_revokes_existing_sessions_and_never_returns_password(client, app):
    headers = login(client, "demo-admin")
    child = student(client, app, headers)
    with TestClient(app, base_url=app.state.settings.app_url) as browser:
        password_login(browser, "sample.student")
        replacement = "replacement student password 109"
        response = client.put("/api/admin/family-accounts/" + child["id"] + "/password", json={"expected_version": child["version"], "new_password": replacement}, headers=headers)
        assert response.status_code == 200, response.text
        assert response.json()["must_change_password"]
        assert replacement not in response.text and "password_cipher" not in response.text
        assert browser.get("/api/auth/me").status_code == 401
    history = client.get("/api/admin/family-accounts/" + child["id"] + "/history")
    assert replacement not in history.text and PASSWORD not in history.text
    assert "password_reset" in history.text


def test_profile_and_event_envelopes_are_owner_bound_and_history_immutable(client, app):
    headers = login(client, "demo-admin")
    child = student(client, app, headers)
    second = student(client, app, headers, username="second.student", name="Other student")
    with app.state.database.factory() as db:
        account = db.get(Record, "local-account:" + child["id"])
        envelope = account.value["profile_cipher"]
        with pytest.raises(HTTPException) as unavailable:
            app.state.auth.family.decrypt(envelope, app.state.settings.tenant_id, second["id"], "profile")
        assert unavailable.value.status_code == 503
        event = db.scalar(select(Record).where(Record.kind == "family_event", Record.owner_id == child["id"]))
        event_id = event.id
    with pytest.raises(ValueError):
        with app.state.database.write() as db:
            row = db.get(Record, event_id)
            row.set({"event_cipher": {}})
    with pytest.raises(ValueError):
        with app.state.database.write() as db:
            db.delete(db.get(Record, event_id))


def test_wrong_tenant_and_nonlocal_target_not_exposed(client, app):
    headers = login(client, "demo-admin")
    assert client.get("/api/admin/family-accounts/demo-student").status_code == 404
    with app.state.database.write() as db:
        db.add(Record(id="foreign-family", tenant_id="foreign", kind="user", data=canonical({"name": "foreign", "role": "student", "auth_provider": "academy"})))
    assert client.get("/api/admin/family-accounts/foreign-family").status_code == 404


def test_invalid_and_password_validation_errors_do_not_echo_pii(client, app):
    headers = login(client, "demo-admin")
    response = client.post("/api/admin/family-accounts", json={"kind": "student", "username": "invalid username sentinel", "name": "Sentinel", "password": "secret-sentinel"}, headers=headers)
    assert response.status_code == 422
    assert "invalid username sentinel" not in response.text and "secret-sentinel" not in response.text
    response = client.post("/api/admin/family-accounts", json={"kind": "student", "username": "valid.student", "name": "Sentinel", "password": "short-sentinel"}, headers=headers)
    assert response.status_code == 422
    assert "short-sentinel" not in response.text


def test_parent_cannot_unlink_every_child_and_cannot_link_unenrolled_student(client, app):
    headers = login(client, "demo-admin")
    child = student(client, app, headers)
    adult = parent(client, headers, child)
    for children in ([], ["demo-student"], [adult["id"]]):
        response = client.put("/api/admin/family-accounts/" + adult["id"] + "/students", json={"expected_version": adult["version"], "student_ids": children}, headers=headers)
        assert response.status_code == 422
    assert client.get("/api/admin/family-accounts/" + adult["id"]).json()["student_ids"] == [child["id"]]
