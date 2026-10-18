"""Independent adversarial coverage of the external-source publication boundary."""
import json

import pytest

from academy.db import Record
from academy.permissions import membership_id
from conftest import login


def audit_response(verdict="approve", risks=()):
    return {"choices": [{"message": {"role": "assistant", "content": json.dumps({"verdict": verdict, "risk_codes": list(risks), "coverage": "supplied_text"})}}], "usage": {"prompt_tokens": 100, "completion_tokens": 20, "total_tokens": 120}}


def setup_agent(client, app, monkeypatch, human=True, connection=None):
    admin = login(client, "demo-admin")
    school = client.get("/api/schools").json()[0]["id"]
    if connection is None:
        response = client.post("/api/admin/connections", json={"school_id": school, "name": "Independent synthetic auditor", "endpoint": "https://models.example.test/v1", "models": ["review-only"], "enabled": True, "api_key": "synthetic-audit-key-never-live"}, headers=admin)
        assert response.status_code == 201, response.text
        connection = response.json()
    response = client.put(f"/api/schools/{school}/audit-agent", json={"name": "Public-source auditor", "connection_id": connection["id"], "model": "review-only" if "review-only" in connection["models"] else connection["models"][0], "require_human_approval": human}, headers=admin)
    assert response.status_code == 200, response.text
    app.state.settings.allow_live_models = True
    calls = []
    def classify(*args):
        calls.append(args)
        return audit_response()
    monkeypatch.setattr(app.state, "classify", classify)
    return {"school": school, "admin": admin, "connection": connection, "agent": response.json()["agent"], "calls": calls}


def submit_text(client, environment, course="demo-course"):
    response = client.post(f"/api/courses/{course}/external-resources", json={"kind": "external_text", "source_url": "https://education.example.test/carbon", "title": "Carbon reference", "content": "Plants take up carbon dioxide while other processes move carbon through ecosystems.", "rights_confirmed": True}, headers=environment["admin"])
    assert response.status_code == 201, response.text
    return response.json()


def run_review(client, environment, material, key="independent-review-12345"):
    response = client.post(f"/api/external-resources/{material['id']}/review", json={"expected_version": material["version"], "idempotency_key": key}, headers=environment["admin"])
    assert response.status_code == 200, response.text
    assert response.json()["status"] == "completed", response.text
    return response.json()


def approve(client, environment, material, review_id):
    return client.post(f"/api/external-resources/{material['id']}/approve", json={"expected_version": material["version"], "snapshot_sha256": material["snapshot_sha256"], "review_id": review_id}, headers=environment["admin"])


@pytest.mark.parametrize("human", [True, False])
@pytest.mark.parametrize("verdict", ["reject", "needs_human"])
def test_new_negative_review_immediately_withdraws_old_clean_publication(client, app, monkeypatch, human, verdict):
    env = setup_agent(client, app, monkeypatch, human=human)
    material = submit_text(client, env)
    clean = run_review(client, env, material)
    if human:
        assert approve(client, env, material, clean["review_id"]).status_code == 200
    login(client)
    assert client.get(f"/api/external-resources/{material['id']}").status_code == 200
    env["admin"] = login(client, "demo-admin")
    monkeypatch.setattr(app.state, "classify", lambda *_: audit_response(verdict, ["incomplete_evidence"]))
    run_review(client, env, material, key="negative-second-review-12345")
    # A human cannot revive the superseded clean result by replaying its ID.
    assert approve(client, env, material, clean["review_id"]).status_code == 409
    login(client)
    assert client.get(f"/api/external-resources/{material['id']}").status_code == 404
    assert client.get("/api/courses/demo-course/external-resources").json()["resources"] == []


def test_schoolwide_public_text_cannot_bypass_enabled_class_review_registry(client, app, monkeypatch):
    admin = login(client, "demo-admin")
    school = client.get("/api/schools").json()[0]["id"]
    result = client.post(f"/api/schools/{school}/resources", json={"name": "School-wide unchecked source", "content": "Unchecked external information", "audience": "all"}, headers=admin)
    assert result.status_code == 201
    resource = result.json()
    login(client)
    assert client.get(f"/api/resources/{resource['id']}/resolve").status_code == 200
    setup_agent(client, app, monkeypatch)
    login(client)
    assert client.get(f"/api/resources/{resource['id']}/resolve").status_code == 403
    assert resource["id"] not in [item["id"] for item in client.get(f"/api/schools/{school}/resources").json()]


def test_direct_class_resource_requires_exact_current_registry_review(client, app, monkeypatch):
    env = setup_agent(client, app, monkeypatch)
    response = client.post(f"/api/schools/{env['school']}/resources", json={"name": "Class source", "content": "Public carbon-cycle source for this class.", "audience": "all", "course_id": "demo-course"}, headers=env["admin"])
    assert response.status_code == 201, response.text
    resource = response.json()
    login(client)
    assert client.get(f"/api/resources/{resource['id']}/resolve").status_code == 403
    env["admin"] = login(client, "demo-admin")
    result = client.post("/api/courses/demo-course/external-resources/import-corpus", headers=env["admin"])
    assert result.status_code == 200, result.text
    candidates = [item for item in result.json()["resources"] if item.get("origin_document_id") == "resource:" + resource["id"]]
    assert len(candidates) == 1
    material = candidates[0]
    review = run_review(client, env, material)
    assert approve(client, env, material, review["review_id"]).status_code == 200
    login(client)
    assert client.get(f"/api/resources/{resource['id']}/resolve").status_code == 200
    env["admin"] = login(client, "demo-admin")
    result = client.put(f"/api/resources/{resource['id']}", json={"name": "Class source", "content": "Changed unchecked answer content.", "audience": "all", "course_id": "demo-course"}, headers=env["admin"])
    assert result.status_code == 200, result.text
    login(client)
    assert client.get(f"/api/resources/{resource['id']}/resolve").status_code == 403
    assert client.get(f"/api/external-resources/{material['id']}").status_code == 404


def test_disabling_agent_permanently_withdraws_schoolwide_and_class_public_information(client, app, monkeypatch):
    env = setup_agent(client, app, monkeypatch, human=False)
    material = submit_text(client, env)
    run_review(client, env, material)
    disabled = {key: value for key, value in env["agent"].items() if key not in {"id", "version", "revision_id"}}
    disabled.update(expected_version=env["agent"]["version"], enabled=False)
    result = client.put(f"/api/schools/{env['school']}/audit-agent", json=disabled, headers=env["admin"])
    assert result.status_code == 200, result.text
    assert result.json()["external_audit_required"] is True
    # An ordinary school edit must never clear the permanent review requirement.
    result = client.put(f"/api/schools/{env['school']}", json={"name": "Renamed audited school"}, headers=env["admin"])
    assert result.status_code == 200, result.text
    with app.state.database.factory() as db:
        assert db.get(Record, env["school"]).value["external_audit_required"] is True
    login(client)
    assert client.get(f"/api/external-resources/{material['id']}").status_code == 404
    assert client.get("/api/courses/demo-course/external-resources").json()["resources"] == []


@pytest.mark.parametrize("phase", ["before_send", "before_release"])
@pytest.mark.parametrize("change", ["global_model_gate", "agent_disabled", "connection_disabled", "resource_revoked", "teacher_membership_revoked"])
def test_review_revalidates_authority_policy_and_source_around_single_model_send(client, app, monkeypatch, phase, change):
    env = setup_agent(client, app, monkeypatch, human=False)
    material = submit_text(client, env)
    teacher = login(client, "demo-instructor")
    calls = []
    def mutate():
        if change == "global_model_gate":
            app.state.settings.allow_live_models = False
            return
        with app.state.database.write() as db:
            if change == "agent_disabled":
                row = db.get(Record, env["agent"]["id"])
                row.set({**row.value, "enabled": False})
            elif change == "connection_disabled":
                row = db.get(Record, env["connection"]["id"])
                row.set({**row.value, "enabled": False})
            elif change == "resource_revoked":
                row = db.get(Record, material["id"])
                row.set({**row.value, "revoked": True})
            else:
                row = db.get(Record, membership_id(env["school"], "demo-instructor"))
                row.set({**row.value, "active": False})
    if phase == "before_send":
        original = app.state.auth.actor
        seen = []
        def actor(request):
            identity = original(request)
            seen.append(request.url.path)
            if len(seen) == 2:
                mutate()
            return identity
        monkeypatch.setattr(app.state.auth, "actor", actor)
    def classify(*args):
        calls.append(args)
        if phase == "before_release":
            mutate()
        return audit_response()
    monkeypatch.setattr(app.state, "classify", classify)
    response = client.post(f"/api/external-resources/{material['id']}/review", json={"expected_version": 1, "idempotency_key": "race-revalidation-12345"}, headers=teacher)
    assert response.status_code == 200, response.text
    assert response.json()["status"] == "withheld" and response.json()["review_id"] is None
    assert len(calls) == (1 if phase == "before_release" else 0)
    login(client)
    assert client.get(f"/api/external-resources/{material['id']}").status_code == 404


def reviewed_lesson_environment(client, app, monkeypatch):
    from test_lesson_tutoring import lesson_environment
    from test_teacher_onboarding import approve as approve_teacher
    env = lesson_environment(client, app, monkeypatch, allow_lesson_curation=True, max_total_tokens=200000)
    auditor = setup_agent(client, app, monkeypatch, human=False, connection=env["connection"])
    result = client.post(f"/api/courses/{env['course']['id']}/external-resources/import-corpus", headers=auditor["admin"])
    assert result.status_code == 200, result.text
    candidates = result.json()["resources"]
    for index, material in enumerate(candidates):
        run_review(client, auditor, material, key=f"independent-corpus-review-{index:05}")
    descriptor = client.post(f"/api/courses/{env['course']['id']}/corpus/refresh", headers=auditor["admin"]).json()
    result = client.post(f"/api/courses/{env['course']['id']}/corpus/approve", json={"corpus_id": descriptor["id"], "snapshot_sha256": descriptor["snapshot_sha256"]}, headers=auditor["admin"])
    assert result.status_code == 200, result.text
    env["profile"] = approve_teacher(client, auditor["admin"], env["school"], env["profile"])
    env["admin"] = auditor["admin"]
    env["headers"] = login(client)
    env["review_candidate"] = next(item for item in candidates if item["origin_document_id"] == "resource:" + env["resource"]["id"])
    env["calls"] = []
    return env


@pytest.mark.parametrize("activity", ["qa", "curated"])
def test_source_revocation_withdraws_student_history_and_idempotent_result_but_preserves_audit_record(client, app, monkeypatch, activity):
    from test_lesson_tutoring import ask, selection
    from test_curated_lessons import curate, selection as curated_selection
    env = reviewed_lesson_environment(client, app, monkeypatch)
    document_id = "resource:" + env["resource"]["id"]
    def classify(*args):
        env["calls"].append(args)
        return selection(document_id) if activity == "qa" else curated_selection(document_id)
    monkeypatch.setattr(app.state, "classify", classify)
    send = ask if activity == "qa" else curate
    first = send(client, env)
    assert first.status_code == 200, first.text
    released = first.json()
    assert released["status"] == "released" and released["citations"][0]["external_review"]
    env["admin"] = login(client, "demo-admin")
    candidate = env["review_candidate"]
    result = client.post(f"/api/external-resources/{candidate['id']}/revoke", json={"expected_version": candidate["version"], "reason": "New unsuitable source finding"}, headers=env["admin"])
    assert result.status_code == 200, result.text
    env["headers"] = login(client)
    historical = send(client, env).json()
    assert historical["status"] == "withheld" and historical["approved_text"] is None and historical["citations"] == []
    if activity == "curated":
        assert historical["lesson"] is None
        progress = client.post(f"/api/courses/{env['course']['id']}/curated-lessons/{released['id']}/progress", json={"section_id": "section-1", "event": "viewed"}, headers=env["headers"])
        assert progress.status_code == 409
    route = "teacher-tutor" if activity == "qa" else "curated-lessons"
    history = client.get(f"/api/courses/{env['course']['id']}/{route}").json()
    assert history == [historical] and len(env["calls"]) == 1
    with app.state.database.factory() as db:
        stored = db.get(Record, released["id"]).value
        assert stored["status"] == "released" and stored["citations"] == released["citations"]


@pytest.mark.parametrize("phase", ["before_send", "before_release"])
def test_source_publication_revocation_blocks_tutoring_at_send_and_release(client, app, monkeypatch, phase):
    from test_lesson_tutoring import ask, selection
    env = reviewed_lesson_environment(client, app, monkeypatch)
    def revoke():
        with app.state.database.write() as db:
            row = db.get(Record, env["review_candidate"]["id"])
            row.set({**row.value, "revoked": True})
    if phase == "before_send":
        original = app.state.auth.actor
        seen = []
        def actor(request):
            identity = original(request)
            seen.append(request.url.path)
            if len(seen) == 2:
                revoke()
            return identity
        monkeypatch.setattr(app.state.auth, "actor", actor)
    def classify(*args):
        env["calls"].append(args)
        if phase == "before_release":
            revoke()
        return selection("resource:" + env["resource"]["id"])
    monkeypatch.setattr(app.state, "classify", classify)
    result = ask(client, env)
    assert result.status_code == 200, result.text
    assert result.json()["status"] == "withheld" and result.json()["approved_text"] is None
    assert len(env["calls"]) == (1 if phase == "before_release" else 0)
