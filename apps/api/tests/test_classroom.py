"""Synthetic, owner-only Classroom OAuth and read operations; no real Google traffic."""
import json
import time
from urllib.parse import parse_qs, urlsplit

import pytest
from sqlalchemy import select

from academy.classroom import ACCOUNT, CALLBACK, EVENT, FLOW_COOKIE, LINK, OPERATION, account, key
from academy.db import Audit, Flow, Record, canonical
from academy.google_classroom_transport import GoogleClassroomFailure
from academy.permissions import default_school_id, membership_id
from conftest import login


BASE = "/api/courses/demo-course/classroom"


class FakeClassroom:
    def __init__(self):
        self.calls, self.on_call, self.error = [], None, None
        self.subject = "synthetic-google-subject-never-audit"
        self.google_id = "google-course-never-durable"
        self.pages = {None: {"courses": [{"id": self.google_id, "name": "Private Google Biology", "section": "Private section", "alternate_link": "https://classroom.google.com/c/synthetic"}], "next_page_token": None}}

    def note(self, kind, *args):
        self.calls.append((kind, *args))
        if self.on_call:
            self.on_call(kind)
        if self.error:
            raise self.error

    def authorize_url(self, state, code_challenge):
        self.note("authorize", state, code_challenge)
        return "https://accounts.google.com/o/oauth2/v2/auth?state=" + state + "&code_challenge=" + code_challenge

    def exchange(self, code, verifier):
        self.note("exchange", code, verifier)
        return {"access_token": "synthetic-google-access-secret", "refresh_token": "synthetic-google-refresh-secret", "expires_in": 3600, "scope": []}

    def userinfo(self, access_token):
        self.note("identity", access_token)
        return {"sub": self.subject}

    def courses(self, access_token, page_token=None):
        self.note("courses", access_token, page_token)
        return self.pages[page_token]

    def coursework(self, access_token, course_id, page_token=None):
        self.note("coursework", access_token, course_id, page_token)
        return {"assignments": [{"id": "google-task-never-durable", "course_id": course_id, "title": "Private assignment title", "description": "Private assignment description", "alternate_link": "https://classroom.google.com/c/synthetic/a/synthetic/details", "due_date": {"year": 2026, "month": 10, "day": 20}, "due_time": None, "work_type": "ASSIGNMENT"}], "next_page_token": None}

    def refresh(self, refresh_token):
        self.note("refresh", refresh_token)
        return {"access_token": "new-synthetic-access-secret", "refresh_token": None, "expires_in": 3600, "scope": []}

    def revoke(self, token):
        self.note("revoke", token)
        return True


@pytest.fixture
def google(app):
    app.state.settings.google_classroom_enabled = True
    app.state.google_classroom_transport = FakeClassroom()
    return app.state.google_classroom_transport


def start(client, headers, value="connect-logical-key"):
    response = client.post(BASE + "/connect", json={"idempotency_key": value}, headers=headers)
    assert response.status_code == 200, response.text
    return parse_qs(urlsplit(response.json()["authorization_url"]).query)["state"][0]


def complete(client, state, **params):
    return client.get(CALLBACK, params={"state": state, "code": "synthetic-oauth-code", **params}, follow_redirects=False)


def connect(client, google):
    headers = login(client)
    state = start(client, headers)
    response = complete(client, state)
    assert response.status_code == 303 and "classroom_status=connected" in response.headers["location"]
    return headers


def link_course(client, headers, google, *, key_value="link-logical-key", remote_id=None):
    version = client.get(BASE).json()["connection_version"]
    return client.post(BASE + "/link", json={"google_course_id": remote_id or google.google_id, "expected_connection_version": version, "idempotency_key": key_value}, headers=headers)


def test_status_disabled_is_local_and_student_owned(client, app):
    login(client)
    result = client.get(BASE)
    assert result.status_code == 200
    assert result.json() == {"enabled": False, "connected": False, "requires_reauth": False, "linked": False, "link_id": None, "connection_version": "unconnected", "reason": "Google Classroom is not enabled for this deployment."}
    headers = login(client)
    assert client.post(BASE + "/connect", json={"idempotency_key": "connect-logical-key"}, headers=headers).status_code == 503
    for user in ("demo-admin", "demo-school", "demo-instructor"):
        login(client, user)
        assert client.get(BASE).status_code == 403


def test_connect_link_read_and_disconnect_private_tokens(client, app, google):
    headers = connect(client, google)
    status = client.get(BASE).json()
    assert status["connected"] and not status["linked"]
    courses = client.get(BASE + "/google-courses").json()
    assert courses["courses"][0]["id"] == google.google_id
    linked = link_course(client, headers, google)
    assert linked.status_code == 200, linked.text
    assert linked.json()["linked"] and linked.json()["connection_version"] == status["connection_version"]
    page = client.get(BASE + "/coursework")
    assert page.status_code == 200, page.text
    assert page.json()["assignments"][0]["description"] == "Private assignment description"
    assert page.json()["course"]["id"] == google.google_id
    with app.state.database.factory() as db:
        records = list(db.scalars(select(Record).where(Record.kind.in_([ACCOUNT, LINK, EVENT, OPERATION]))))
        audit = " ".join(db.scalars(select(Audit.payload)))
        all_data = " ".join(row.data for row in records)
        for secret in (google.google_id, google.subject, "synthetic-google-access-secret", "synthetic-google-refresh-secret", "Private assignment", "Private Google Biology", "classroom.google.com"):
            assert secret not in audit and secret not in all_data
        accounts = [row for row in records if row.kind == ACCOUNT]
        assert app.state.auth.unseal(accounts[0].value["cipher"])["subject"] == google.subject
        operations = [row for row in records if row.kind == OPERATION]
        assert all(row.value["status"] == "released" for row in operations)
        assert all(row.value["markers"] for row in operations)
    response = client.post(BASE + "/disconnect", json={}, headers=headers)
    assert response.status_code == 200
    assert not response.json()["connected"] and not response.json()["linked"] and response.json()["revoked"] and not response.json()["uncertain"]
    assert google.calls[-1] == ("revoke", "synthetic-google-refresh-secret")


def test_account_and_links_are_personal_not_class_global(client, google):
    headers = connect(client, google)
    assert link_course(client, headers, google).status_code == 200
    login(client, "demo-student2")
    status = client.get(BASE).json()
    assert not status["connected"] and not status["linked"]
    assert client.get(BASE + "/coursework").status_code == 409
    login(client, "demo-student")
    assert client.get(BASE).json()["linked"]


def test_csrf_and_extra_inputs_rejected(client, google):
    headers = login(client)
    assert client.post(BASE + "/connect", json={"idempotency_key": "connect-logical-key"}).status_code == 403
    assert client.post(BASE + "/connect", json={"idempotency_key": "connect-logical-key", "scope": "write"}, headers=headers).status_code == 422
    assert client.post(BASE + "/link", json={"idempotency_key": "link-logical-key", "google_course_id": "../other", "expected_connection_version": "x"}, headers=headers).status_code == 422


@pytest.mark.parametrize("change", ["state", "cookie", "login", "school", "enrollment", "disconnect"])
def test_oauth_requires_exact_state_current_login_school_and_enrollment(client, app, google, change):
    headers = login(client)
    state = start(client, headers)
    if change == "state":
        state = "incorrect-state"
    elif change == "cookie":
        client.cookies.delete(FLOW_COOKIE)
    elif change == "login":
        login(client, "demo-student")
    elif change in {"school", "enrollment"}:
        with app.state.database.write() as db:
            if change == "school":
                row = db.get(Record, membership_id(default_school_id(app.state.settings.tenant_id), "demo-student"))
                row.set({**row.value, "active": False})
            else:
                db.delete(db.get(Record, "enrollment-demo-student"))
    else:
        assert client.post(BASE + "/disconnect", json={}, headers=headers).status_code == 200
    response = complete(client, state)
    assert response.status_code == 303 and "classroom_status=error" in response.headers["location"]
    assert not any(item[0] == "exchange" for item in google.calls)


def test_oauth_flow_consumed_before_exchange_and_replay_never_reexchanges(client, app, google):
    headers = login(client)
    state = start(client, headers)
    def check(kind):
        if kind == "exchange":
            with app.state.database.factory() as db:
                assert db.get(Flow, __import__("hashlib").sha256(state.encode()).hexdigest()) is None
                operation = db.scalar(select(Record).where(Record.kind == OPERATION))
                assert operation.value["markers"][-1]["step"] == "exchange"
    google.on_call = check
    assert "classroom_status=connected" in complete(client, state).headers["location"]
    client.cookies.set(FLOW_COOKIE, state)
    assert "classroom_status=error" in complete(client, state).headers["location"]
    assert len([item for item in google.calls if item[0] == "exchange"]) == 1


def test_connect_idempotency_returns_same_flow_without_new_oauth_request(client, google):
    headers = login(client)
    first = start(client, headers)
    assert start(client, headers) == first
    complete(client, first)
    assert client.post(BASE + "/connect", json={"idempotency_key": "connect-logical-key"}, headers=headers).status_code == 409


@pytest.mark.parametrize("phase", ["exchange", "identity"])
def test_disconnect_during_oauth_never_resurrects_connection(client, app, google, phase):
    headers = login(client)
    state = start(client, headers)
    def changed(kind):
        if kind == phase:
            with app.state.database.write() as db:
                row = db.scalar(select(Record).where(Record.kind == ACCOUNT))
                row.set({"version": "new-disconnected-epoch", "token_revision": "changed", "status": "disconnected", "cipher": "", "expires": 0})
    google.on_call = changed
    assert "classroom_status=error" in complete(client, state).headers["location"]
    assert not client.get(BASE).json()["connected"]


def test_link_requires_current_google_student_course_and_correct_account_version(client, app, google):
    headers = connect(client, google)
    assert link_course(client, headers, google, remote_id="not-enrolled", key_value="denied-link-key").status_code == 403
    response = client.post(BASE + "/link", json={"google_course_id": google.google_id, "expected_connection_version": "stale", "idempotency_key": "link-stale-version"}, headers=headers)
    assert response.status_code == 409
    google.pages = {None: {"courses": [], "next_page_token": "page2"}, "page2": {"courses": [{"id": google.google_id, "name": "Biology", "section": "", "alternate_link": None}], "next_page_token": None}}
    assert link_course(client, headers, google).status_code == 200
    assert google.calls[-1][2] == "page2"
    calls = len(google.calls)
    assert link_course(client, headers, google).status_code == 200
    assert len(google.calls) == calls
    assert link_course(client, headers, google, remote_id="different").status_code == 409


def test_unbounded_or_repeated_membership_pages_fail_closed(client, google):
    headers = connect(client, google)
    google.pages = {None: {"courses": [], "next_page_token": "again"}, "again": {"courses": [], "next_page_token": "again"}}
    assert link_course(client, headers, google).status_code == 503
    assert len([item for item in google.calls if item[0] == "courses"]) == 10
    assert not client.get(BASE).json()["linked"]


@pytest.mark.parametrize("change", ["enrollment", "school", "account", "link", "login"])
def test_inflight_coursework_withheld_on_any_authority_change(client, app, google, change):
    headers = connect(client, google)
    assert link_course(client, headers, google).status_code == 200
    def changed(kind):
        if kind != "coursework":
            return
        with app.state.database.write() as db:
            if change == "enrollment":
                db.delete(db.get(Record, "enrollment-demo-student"))
            elif change == "school":
                row = db.get(Record, membership_id(default_school_id(app.state.settings.tenant_id), "demo-student"))
                row.set({**row.value, "active": False})
            elif change in {"account", "link"}:
                row = db.scalar(select(Record).where(Record.kind == (ACCOUNT if change == "account" else LINK)))
                row.set({**row.value, "version": "changed"})
            else:
                from academy.db import Login
                row = db.scalar(select(Login))
                db.delete(row)
    google.on_call = changed
    response = client.get(BASE + "/coursework")
    assert response.status_code in {401, 403, 409}
    assert "Private assignment" not in response.text
    with app.state.database.factory() as db:
        outcomes = list(db.scalars(select(Record).where(Record.kind == OPERATION)))
        assert outcomes[-1].value["status"] == "withheld"


def test_unlink_is_local_and_inflight_results_require_original_link(client, google):
    headers = connect(client, google)
    assert link_course(client, headers, google).status_code == 200
    calls = len(google.calls)
    response = client.post(BASE + "/unlink", json={}, headers=headers)
    assert response.status_code == 200 and not response.json()["linked"]
    assert len(google.calls) == calls
    assert client.get(BASE + "/coursework").status_code == 409


def expire(app):
    with app.state.database.write() as db:
        row = db.scalar(select(Record).where(Record.kind == ACCOUNT))
        row.set({**row.value, "expires": 1})


def test_refresh_is_marked_bound_to_same_subject_and_retains_connection_epoch(client, app, google):
    connect(client, google)
    version = client.get(BASE).json()["connection_version"]
    expire(app)
    result = client.get(BASE + "/google-courses")
    assert result.status_code == 200, result.text
    assert [item[0] for item in google.calls][-3:] == ["refresh", "identity", "courses"]
    assert client.get(BASE).json()["connection_version"] == version
    with app.state.database.factory() as db:
        row = db.scalar(select(Record).where(Record.kind == ACCOUNT))
        assert "refresh_operation_id" not in row.value
        assert app.state.auth.unseal(row.value["cipher"])["refresh_token"] == "synthetic-google-refresh-secret"


@pytest.mark.parametrize("failure", ["timeout", "subject"])
def test_uncertain_refresh_or_subject_change_requires_new_connection(client, app, google, failure):
    connect(client, google)
    expire(app)
    if failure == "subject":
        google.subject = "foreign-google-subject"
    else:
        google.error = GoogleClassroomFailure("unavailable", uncertain=True)
    result = client.get(BASE + "/google-courses")
    assert result.status_code in {409, 503}
    status = client.get(BASE).json()
    assert status["requires_reauth"] and not status["connected"]
    calls = len(google.calls)
    assert client.get(BASE + "/google-courses").status_code == 409
    assert len(google.calls) == calls


def test_refresh_concurrency_barrier_blocks_second_refresh(client, app, google):
    connect(client, google)
    expire(app)
    def competing(kind):
        if kind == "refresh":
            response = client.get(BASE + "/google-courses")
            assert response.status_code == 409
    google.on_call = competing
    assert client.get(BASE + "/google-courses").status_code == 200
    assert len([item for item in google.calls if item[0] == "refresh"]) == 1


def test_disconnect_clears_local_access_before_revoke_and_records_failure(client, app, google):
    headers = connect(client, google)
    assert link_course(client, headers, google).status_code == 200
    def revoked(kind):
        if kind == "revoke":
            status = client.get(BASE).json()
            assert not status["connected"] and not status["linked"]
            raise GoogleClassroomFailure("unavailable", uncertain=True)
    google.on_call = revoked
    result = client.post(BASE + "/disconnect", json={}, headers=headers)
    assert result.status_code == 200 and result.json()["uncertain"] and not result.json()["revoked"]
    calls = len(google.calls)
    repeated = client.post(BASE + "/disconnect", json={}, headers=headers).json()
    assert not repeated["revoked"] and repeated["uncertain"]
    assert len(google.calls) == calls


def test_read_does_not_make_model_or_submission_calls(client, app, google):
    headers = connect(client, google)
    assert link_course(client, headers, google).status_code == 200
    app.state.classify = lambda *_: pytest.fail("Classroom cannot call a model")
    assert client.get(BASE + "/coursework").status_code == 200
    assert all(item[0] in {"authorize", "exchange", "identity", "courses", "coursework"} for item in google.calls)
    with app.state.database.factory() as db:
        assert not list(db.scalars(select(Record).where(Record.kind.in_(["homework_message", "lesson_turn", "submission"]))))


@pytest.mark.parametrize("change", ["course", "school"])
def test_owner_can_disconnect_after_class_or_school_revocation(client, app, google, change):
    headers = connect(client, google)
    assert link_course(client, headers, google).status_code == 200
    identifier = client.get("/api/me/google-classroom/connections").json()["connections"][0]["id"]
    with app.state.database.write() as db:
        if change == "course":
            db.delete(db.get(Record, "enrollment-demo-student"))
        else:
            row = db.get(Record, membership_id(default_school_id(app.state.settings.tenant_id), "demo-student"))
            row.set({**row.value, "active": False})
    assert client.post(BASE + "/disconnect", json={}, headers=headers).status_code == 403
    connections = client.get("/api/me/google-classroom/connections")
    assert connections.status_code == 200 and connections.json()["connections"][0]["connected"]
    result = client.post("/api/me/google-classroom/connections/" + identifier + "/disconnect", json={}, headers=headers)
    assert result.status_code == 200 and not result.json()["connected"] and result.json()["revoked"]
    with app.state.database.factory() as db:
        assert all(not row.value.get("cipher") for row in db.scalars(select(Record).where(Record.kind.in_([ACCOUNT, LINK]))))


def test_owner_cleanup_rejects_other_students_and_administrators(client, google):
    connect(client, google)
    identifier = client.get("/api/me/google-classroom/connections").json()["connections"][0]["id"]
    for user in ("demo-student2", "demo-admin"):
        headers = login(client, user)
        assert client.get("/api/me/google-classroom/connections").json() == {"connections": []}
        assert client.post("/api/me/google-classroom/connections/" + identifier + "/disconnect", json={}, headers=headers).status_code == 404


def test_owner_cleanup_is_not_blocked_by_daily_read_quota(client, app, google, monkeypatch):
    import academy.classroom
    headers = connect(client, google)
    monkeypatch.setattr(academy.classroom, "MAX_DAILY_OPERATIONS", 0)
    assert client.get(BASE + "/google-courses").status_code == 429
    result = client.post(BASE + "/disconnect", json={}, headers=headers)
    assert result.status_code == 200 and result.json()["revoked"] and not result.json()["connected"]


@pytest.mark.parametrize("params", [{"code": "", "error": "access_denied"}, {"code": ""}])
def test_google_denial_commits_consumed_flow_and_cannot_replay(client, app, google, params):
    headers = login(client)
    state = start(client, headers)
    assert "classroom_status=error" in complete(client, state, **params).headers["location"]
    with app.state.database.factory() as db:
        assert db.get(Flow, __import__("hashlib").sha256(state.encode()).hexdigest()) is None
        operation = db.scalar(select(Record).where(Record.kind == OPERATION))
        assert operation.value["status"] == "withheld" and not operation.value["markers"]
    client.cookies.set(FLOW_COOKIE, state)
    assert "classroom_status=error" in complete(client, state).headers["location"]
    assert not any(item[0] == "exchange" for item in google.calls)


@pytest.mark.parametrize("changed", ["client", "callback"])
def test_oauth_configuration_change_rejects_callback_before_exchange(client, app, google, changed):
    headers = login(client)
    state = start(client, headers)
    if changed == "client":
        app.state.settings.google_classroom_client_id = "different-project-client"
    else:
        app.state.settings.app_url = "http://localhost:8091"
    response = complete(client, state)
    assert response.status_code == 303 and "classroom_status=error" in response.headers["location"]
    assert not any(item[0] == "exchange" for item in google.calls)


def test_changed_google_project_requires_reconnect_and_safe_owner_erasure(client, app, google):
    headers = connect(client, google)
    assert link_course(client, headers, google).status_code == 200
    app.state.settings.google_classroom_client_id = "different-project-client"
    status = client.get(BASE).json()
    assert status["requires_reauth"] and not status["connected"] and not status["linked"]
    own = client.get("/api/me/google-classroom/connections").json()["connections"][0]
    assert own["requires_reauth"] and not own["connected"]
    calls = len(google.calls)
    assert client.get(BASE + "/google-courses").status_code == 409
    assert client.get(BASE + "/coursework").status_code == 409
    result = client.post(BASE + "/disconnect", json={}, headers=headers)
    assert result.status_code == 200 and result.json()["uncertain"] and not result.json()["revoked"]
    assert len(google.calls) == calls


def test_same_google_client_secret_rotation_keeps_account_authority(client, app, google):
    connect(client, google)
    app.state.settings.google_classroom_client_secret = "rotated-synthetic-client-secret"
    assert client.get(BASE).json()["connected"]
    assert client.get(BASE + "/google-courses").status_code == 200


def test_owner_can_erase_unreadable_ciphertext(client, app, google):
    headers = connect(client, google)
    with app.state.database.write() as db:
        row = db.scalar(select(Record).where(Record.kind == ACCOUNT))
        row.set({**row.value, "cipher": "unreadable-private-envelope"})
    result = client.post(BASE + "/disconnect", json={}, headers=headers)
    assert result.status_code == 200 and not result.json()["connected"] and result.json()["uncertain"]
    with app.state.database.factory() as db:
        assert not db.scalar(select(Record).where(Record.kind == ACCOUNT)).value["cipher"]
