from concurrent.futures import ThreadPoolExecutor
import json

import pytest
from sqlalchemy import select, text
from sqlalchemy.exc import DatabaseError

from academy.db import Audit, Record
from academy import provider
from conftest import login, draft, model_assignment


def turn(client, headers, key="request-0001", message="What should I examine next?"):
    return client.post("/api/assignments/demo-assignment/tutor", json={"message": message, "idempotency_key": key}, headers=headers)


def test_auth_csrf_and_role_boundaries(client):
    assert client.get("/api/courses").status_code == 401
    headers = login(client)
    assert client.post("/api/courses", json={"title": "No"}, headers=headers).status_code == 403
    assert client.get("/api/admin/settings").status_code == 403
    assert client.get("/api/users").status_code == 403
    assert client.put("/api/assignments/demo-assignment/draft", json={"content": "x"}).status_code == 403
    assert client.put("/api/assignments/demo-assignment/draft", json={"content": "x"}, headers={**headers, "Origin": "https://foreign.test"}).status_code == 403
    assert client.get("/api/assignments/demo-assignment/submissions").status_code == 403
    assert client.get("/api/audit/sessions").status_code == 403


def test_unreleased_approved_bank_never_sent_to_student(client):
    login(client, "demo-instructor")
    teaching = client.get("/api/assignments/demo-assignment").json()
    secret_text = teaching["tutor_policy"]["hints"][0]["text"]
    login(client)
    for path in ("/api/assignments/demo-assignment", "/api/courses/demo-course", "/api/courses/demo-course/assignments"):
        response = client.get(path)
        assert response.status_code == 200
        assert secret_text not in response.text
        assert '"hints"' not in response.text and '"questions"' not in response.text


def test_course_and_tenant_isolation(client, app):
    headers = login(client, "demo-instructor")
    course = client.post("/api/courses", json={"title": "Private course"}, headers=headers).json()
    login(client)
    assert client.get("/api/courses/" + course["id"]).status_code == 403
    with app.state.database.write() as db:
        db.add(Record(id="foreign-assignment", tenant_id="another-tenant", kind="assignment", parent_id="demo-course", data="{}"))
    assert client.get("/api/assignments/foreign-assignment").status_code == 404


def test_submission_snapshot_ownership_and_database_immutability(client, app):
    headers = login(client)
    draft(client, headers)
    content = "This is entirely student-authored submitted work."
    submission = client.post("/api/assignments/demo-assignment/submit", json={"content": content}, headers=headers).json()
    assert submission["version"] == 1 and submission["assignment_snapshot"]["version"] == 1
    client.put("/api/assignments/demo-assignment/draft", json={"content": "Changed later"}, headers=headers)
    assert client.get("/api/assignments/demo-assignment").json()["submissions"][0]["content"] == content
    login(client, "demo-student2")
    own = client.get("/api/assignments/demo-assignment").json()
    assert own["draft"] is None and own["submissions"] == []
    with pytest.raises(DatabaseError), app.state.database.engine.begin() as connection:
        connection.execute(text("UPDATE records SET data='{}' WHERE id=:id"), {"id": submission["id"]})
    with pytest.raises(DatabaseError), app.state.database.engine.begin() as connection:
        connection.execute(text("DELETE FROM records WHERE id=:id"), {"id": submission["id"]})


def test_curated_requires_attempt_idempotency_and_replay(client):
    headers = login(client)
    assert turn(client, headers).json()["status"] == "blocked"
    draft(client, headers)
    result = turn(client, headers, "request-0002").json()
    assert result["status"] == "released" and result["approved_text"]
    again = turn(client, headers, "request-0002").json()
    assert again["id"] == result["id"]
    assert turn(client, headers, "request-0002", "different").status_code == 409
    assert "policy_snapshot" not in result and "prompt" not in result
    login(client, "demo-instructor")
    session = client.get("/api/audit/sessions").json()[0]
    replay = client.get("/api/audit/sessions/" + session["session_id"]).json()
    assert replay["turns"][-1]["draft_snapshot"].startswith("My own attempt")
    assert any(row["event_type"] == "audit.session_viewed" for row in replay["events"])
    assert any(row["event_type"] == "tutor.released" for row in replay["events"])


def test_turn_limits_enforced_server_side(client):
    headers = login(client, "demo-instructor")
    assignment = client.get("/api/assignments/demo-assignment").json()
    assignment["tutor_policy"]["max_turns"] = 1
    body = {key: assignment[key] for key in ("title", "module", "instructions", "tutor_policy")}
    assert client.put("/api/assignments/demo-assignment", json=body, headers=headers).status_code == 200
    headers = login(client)
    draft(client, headers)
    assert turn(client, headers).json()["status"] == "released"
    result = turn(client, headers, "request-0002").json()
    assert result["status"] == "blocked" and "turn limit" in result["error"]


def test_model_release_persisted_before_call_and_hidden_reasoning_removed(client, app):
    headers, _ = model_assignment(client, app)
    calls = []
    def classify(*args):
        calls.append(args)
        with app.state.database.factory() as db:
            assert db.scalar(select(Record).where(Record.kind == "turn")).value["status"] == "pending"
            assert db.scalar(select(Audit).where(Audit.event_type == "tutor.admitted"))
        return {"choices": [{"message": {"content": '{"selection_id":"uncertainty"}', "reasoning_content": "DO NOT STORE HIDDEN REASONING"}}], "usage": {"prompt_tokens": 30, "completion_tokens": 10, "total_tokens": 40}}
    app.state.classify = classify
    result = turn(client, headers).json()
    assert result["status"] == "released" and result["selected_id"] == "uncertainty"
    assert len(calls) == 1
    assert turn(client, headers).json()["id"] == result["id"] and len(calls) == 1
    login(client, "demo-admin")
    export = client.get("/api/admin/audit/export")
    assert "DO NOT STORE HIDDEN REASONING" not in export.text
    assert "unit-test-key-never-live" not in export.text
    assert client.get("/api/audit/verify").json()["valid"] is True


@pytest.mark.parametrize("response", [
    {"choices": [{"message": {"content": "The answer is 42"}}]},
    {"choices": [{"message": {"content": '{"selection_id":"uncertainty","answer":"42"}'}}]},
    {"choices": [{"message": {"content": '{"selection_id":"not-approved"}'}}], "usage": {"prompt_tokens": 30, "completion_tokens": 10, "total_tokens": 40}},
    {"choices": [{"message": {"content": '{"selection_id":"uncertainty"}'}}], "usage": {"prompt_tokens": -30, "completion_tokens": 10, "total_tokens": -20}},
    {"choices": [{"message": {"content": '{"selection_id":"uncertainty"}'}}], "usage": {"prompt_tokens": 30, "completion_tokens": 10, "total_tokens": 999}},
])
def test_unapproved_model_text_or_bad_usage_withheld(client, app, response):
    headers, _ = model_assignment(client, app)
    app.state.classify = lambda *_: response
    result = turn(client, headers).json()
    assert result["status"] == "withheld" and result["approved_text"] is None


def test_uncertain_provider_failure_never_retries_same_request(client, app):
    headers, _ = model_assignment(client, app)
    calls = []
    def fail(*args):
        calls.append(args)
        raise provider.ProviderFailure("timeout")
    app.state.classify = fail
    result = turn(client, headers).json()
    assert result["status"] == "withheld" and "may be billable" in result["error"]
    assert turn(client, headers).json()["id"] == result["id"]
    assert len(calls) == 1


def test_live_access_disabled_by_default(client, app):
    headers, _ = model_assignment(client, app)
    app.state.settings.allow_live_models = False
    app.state.classify = lambda *_: pytest.fail("Live model gate bypassed")
    assert turn(client, headers).json()["status"] == "blocked"


def test_policy_change_during_model_call_withholds(client, app):
    headers, _ = model_assignment(client, app)
    original = app.state.classify
    def classify(*args):
        with app.state.database.write() as db:
            row = db.get(Record, "demo-assignment")
            data = row.value
            data["version"] += 1
            row.set(data)
        return original(*args)
    app.state.classify = classify
    result = turn(client, headers).json()
    assert result["status"] == "withheld" and "policy changed" in result["error"]


def test_connection_disable_during_model_call_withholds(client, app):
    headers, connection = model_assignment(client, app)
    original = app.state.classify
    def classify(*args):
        with app.state.database.write() as db:
            row = db.get(Record, connection["id"])
            data = row.value
            data.update(enabled=False, version=2)
            row.set(data)
        return original(*args)
    app.state.classify = classify
    result = turn(client, headers).json()
    assert result["status"] == "withheld" and "connection changed" in result["error"]


def test_credentials_status_only_encrypted_endpoint_change_and_validation(client, app):
    headers = login(client, "demo-admin")
    payload = {"name": "Models", "endpoint": "https://models.example.test/v1", "models": ["bounded"], "api_key": "top-secret-test-value"}
    response = client.post("/api/admin/connections", json=payload, headers=headers)
    assert response.status_code == 201 and "top-secret" not in response.text
    connection = response.json()
    assert connection["key_configured"]
    with app.state.database.factory() as db:
        assert "top-secret-test-value" not in db.get(Record, connection["id"]).data
    payload.update(version=connection["version"], endpoint="https://new.example.test/v1", api_key=None)
    assert client.put("/api/admin/connections/" + connection["id"], json=payload, headers=headers).status_code == 422
    payload["clear_key"] = True
    cleared = client.put("/api/admin/connections/" + connection["id"], json=payload, headers=headers)
    assert cleared.status_code == 200 and cleared.json()["key_configured"] is False
    invalid = client.post("/api/admin/connections", json={**payload, "api_key": "SECRET-INVALID-INPUT", "unknown": "also-secret"}, headers=headers)
    assert invalid.status_code == 422 and "SECRET-INVALID-INPUT" not in invalid.text and "also-secret" not in invalid.text


def test_append_only_audit_and_serialized_concurrent_writes(client, app):
    headers = login(client)
    draft(client, headers)
    with ThreadPoolExecutor(max_workers=4) as pool:
        responses = list(pool.map(lambda i: turn(client, headers, "parallel-" + str(i)), range(4)))
    assert all(response.status_code == 200 for response in responses)
    login(client, "demo-admin")
    assert client.get("/api/audit/verify").json()["valid"]
    with pytest.raises(DatabaseError), app.state.database.engine.begin() as connection:
        connection.execute(text("DELETE FROM audit_events"))


@pytest.mark.parametrize("url", ["http://models.example/v1", "https://127.0.0.1/v1", "https://169.254.169.254/latest", "https://user:password@models.example/v1", "https://models.example:8443/v1", "https://models.example/v1?secret=bad"])
def test_provider_endpoint_boundaries(url):
    with pytest.raises(ValueError):
        provider.endpoint(url)


def test_teacher_review_preserves_submission(client):
    headers = login(client)
    submitted = client.post("/api/assignments/demo-assignment/submit", json={"content": "Own work"}, headers=headers).json()
    headers = login(client, "demo-instructor")
    assert client.post("/api/submissions/" + submitted["id"] + "/review", json={"feedback": "Explain your evidence"}, headers=headers).status_code == 200
    login(client)
    version = client.get("/api/assignments/demo-assignment").json()["submissions"][0]
    assert version["content"] == "Own work" and version["feedback"] == "Explain your evidence"
