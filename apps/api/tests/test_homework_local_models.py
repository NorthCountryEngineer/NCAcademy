"""Explicit provider admission for encrypted local-family Homework Help."""

import json
from datetime import datetime, timedelta, timezone

import pytest
from fastapi import HTTPException
from fastapi.testclient import TestClient
from sqlalchemy import select

from academy.config import Settings
from academy.db import Audit, Record
from academy import homework_ai
from academy import provider
from academy.homework_ai import validate_teacher_execution, visible_response
from conftest import login
from test_agent_auth import core_environment
from test_family_accounts import password_login, student
from test_homework_ai import approve_synthetic_openai_project, environment, result, triage_result
from test_private_homework_help import BASE, QUESTION, ask, conversation, enable, enroll, screenshot


PRIVACY_REFERENCE = "Synthetic-only provider privacy review reference; no real project eligibility is asserted."


def revise(client, headers, school, *, approve=False, **changes):
    path = "/api/homework-help/configuration?school_id=" + school
    current = client.get(path).json()
    saved = client.put(path, json={**current["configuration"], "expected_version": current["version"], **changes}, headers=headers)
    assert saved.status_code == 200, saved.text
    if approve:
        approved = client.post("/api/homework-help/configuration/approve?school_id=" + school,
                              json={"revision_id": saved.json()["revision_id"]}, headers=headers)
        assert approved.status_code == 200, approved.text
    return saved.json()


def test_local_model_opt_in_needs_a_current_approval_and_privacy_evidence(client, app, monkeypatch):
    env = environment(client, app, monkeypatch, official_models=True)
    admin = login(client, "demo-admin")
    student(client, app, admin, course_ids=[])
    assert enable(client, admin, env["school"]).status_code == 200
    invalid = client.put("/api/homework-help/configuration?school_id=" + env["school"],
        json={**env["configuration"]["configuration"], "expected_version": env["configuration"]["version"],
              "allow_local_students": True, "local_student_privacy_confirmation": "  "}, headers=admin)
    assert invalid.status_code == 422

    with TestClient(app, base_url=app.state.settings.app_url) as child:
        headers = password_login(child, "sample.student")
        enroll(child, headers)
        descriptor = child.get(BASE, params={"school_id": env["school"]}).json()["assistant"]
        assert descriptor["private_mode"] and descriptor["available"]
        session_id = conversation(child, headers, env["school"]).json()["id"]
        original = ask(child, headers, session_id).json()
        assert original["private_mode"] and not env["calls"]

        draft = revise(client, admin, env["school"], allow_local_students=True,
                       local_student_privacy_confirmation=PRIVACY_REFERENCE)
        assert not draft["approved"]
        waiting = child.get(BASE, params={"school_id": env["school"]}).json()["assistant"]
        assert not waiting.get("private_mode") and not waiting["available"]
        pending = ask(child, headers, session_id, key="synthetic-unapproved-model-question").json()
        assert pending["status"] == "blocked" and not pending.get("private_mode") and not env["calls"]

        admitted = client.post("/api/homework-help/configuration/approve?school_id=" + env["school"],
                              json={"revision_id": draft["revision_id"]}, headers=admin)
        assert admitted.status_code == 200
        model_index = child.get(BASE, params={"school_id": env["school"]})
        assert model_index.status_code == 200
        descriptor = model_index.json()["assistant"]
        assert descriptor["available"] and not descriptor.get("private_mode")
        assert descriptor["photo_model_labels"]
        assert PRIVACY_REFERENCE not in model_index.text
        assert "endpoint" not in model_index.text
        assert child.get(BASE + f"/sessions/{session_id}").json()["messages"][0] == original
        assert ask(child, headers, session_id).json() == original and not env["calls"]
        continued = ask(child, headers, session_id, key="synthetic-real-model-after-private-hints",
                        message="I need a different explanation of that idea.").json()
        assert continued["status"] == "released" and not continued.get("private_mode")
        routing = json.loads(env["calls"][0][3][1]["content"])
        assert any(item["assistant"] == original["answer"] and item["engine"] == "reviewed_hint_library"
                   for item in routing["previous_messages"])


def test_scoped_gate_allows_encrypted_local_vision_and_context_without_enrollment(client, app, monkeypatch):
    env = environment(client, app, monkeypatch, allow_local_students=True,
                      local_student_privacy_confirmation=PRIVACY_REFERENCE, allow_images=True,
                      triage_image_capable=True, tutor_image_capable=True, safety_image_capable=True)
    app.state.settings.allow_live_models = False
    app.state.settings.allow_homework_models = True
    admin = login(client, "demo-admin")
    child_account = student(client, app, admin, course_ids=[])
    assert client.get("/api/admin/settings").json()["live_models_enabled"] is False

    with TestClient(app, base_url=app.state.settings.app_url) as child:
        headers = password_login(child, "sample.student")
        assert conversation(child, headers, env["school"]).status_code == 409
        enroll(child, headers)
        session_id = conversation(child, headers, env["school"]).json()["id"]
        current = child.get(BASE, params={"school_id": env["school"]}).json()["assistant"]
        first = ask(child, headers, session_id, message="Help me read this worksheet, synthetic child question sentinel.",
                    images=[screenshot()], image_use_confirmed=True,
                    expected_image_policy_sha256=current["image_policy_sha256"]).json()
        assert first["status"] == "released" and not first.get("private_mode")
        assert [call[2] for call in env["calls"]] == ["triage", "safety"]
        assert first["images"] and not first["triage"]["course_id"]
        assert child.get(first["images"][0]["url"]).headers["cache-control"] == "private, no-store"
        source_calls = list(env["calls"])
        original_transport = app.state.classify

        def clarify(connection, key, model, prompt, maximum):
            if model == "triage":
                env["calls"].append((connection, key, model, prompt, maximum))
                return result(triage_result(response={"mode": "clarify", "text": "Which part of the diagram would you like to discuss?", "reference_ids": []}))
            return original_transport(connection, key, model, prompt, maximum)

        monkeypatch.setattr(app.state, "classify", clarify)
        clarified = ask(child, headers, session_id, key="synthetic-model-clarification", message="I need help with the diagram.").json()
        assert clarified["status"] == "clarify"
        monkeypatch.setattr(app.state, "classify", original_transport)
        follow_up = ask(child, headers, session_id, key="synthetic-model-followup", message="The left-hand part is confusing.").json()
        assert follow_up["status"] == "released"
        triage_prompt = next(call[3] for call in reversed(env["calls"]) if call[2] == "triage")
        context = json.loads(triage_prompt[1]["content"])["previous_messages"]
        assert context[-1]["assistant"] == clarified["answer"] and context[-1]["status"] == "clarify"
        safety_prompt = json.loads(env["calls"][-1][3][1]["content"])
        assert safety_prompt["previous_messages"][-1]["assistant"] == clarified["answer"]

        admin_review = client.get(BASE + f"/sessions/{session_id}/review")
        assert admin_review.status_code == 200 and admin_review.json()["messages"][0]["answer"] == first["answer"]
        with app.state.database.factory() as db:
            row = db.get(Record, first["id"])
            assert "__academy_pii__" in row.data and "synthetic child question sentinel" not in row.data
            assert row.value["configuration_snapshot"]["configuration"]["allow_local_students"]
            markers = list(db.scalars(select(Record).where(Record.kind == "homework_ai_send", Record.parent_id == first["id"])))
            assert len(markers) == 2 and all("__academy_pii__" in item.data for item in markers)
            audit = list(db.scalars(select(Audit).where(Audit.session_id == session_id)))
            assert audit and all("__academy_pii__" in item.payload for item in audit)
        assert child_account["id"] not in str([call[3] for call in source_calls])


def test_explicit_private_selection_and_inflight_model_revocation(client, app, monkeypatch):
    env = environment(client, app, monkeypatch, allow_local_students=True,
                      local_student_privacy_confirmation=PRIVACY_REFERENCE)
    admin = login(client, "demo-admin")
    student(client, app, admin, course_ids=[])
    assert enable(client, admin, env["school"]).status_code == 200
    with TestClient(app, base_url=app.state.settings.app_url) as child:
        headers = password_login(child, "sample.student")
        enroll(child, headers)
        session_id = conversation(child, headers, env["school"]).json()["id"]
        original = app.state.classify

        def revoke(connection, key, model, prompt, maximum):
            response = original(connection, key, model, prompt, maximum)
            if model == "triage":
                revise(client, admin, env["school"], approve=True, allow_local_students=False,
                       local_student_privacy_confirmation="")
            return response

        monkeypatch.setattr(app.state, "classify", revoke)
        withheld = ask(child, headers, session_id).json()
        assert withheld["status"] == "withheld" and withheld["answer"] is None
        assert [call[2] for call in env["calls"]] == ["triage"]
        assert child.get(BASE, params={"school_id": env["school"]}).json()["assistant"]["private_mode"]
        fallback = ask(child, headers, session_id, key="private-after-remote-revocation").json()
        assert fallback["private_mode"] and fallback["status"] == "released"


def test_homework_gate_is_separate_and_disabled_by_default(core_environment, monkeypatch):
    monkeypatch.delenv("ALLOW_HOMEWORK_MODELS", raising=False)
    assert Settings.load().allow_homework_models is False
    monkeypatch.setenv("ALLOW_HOMEWORK_MODELS", "true")
    value = Settings.load()
    assert value.allow_homework_models and not value.allow_live_models
    monkeypatch.setenv("ALLOW_HOMEWORK_MODELS", "enabled")
    with pytest.raises(ValueError, match="ALLOW_HOMEWORK_MODELS must be true or false"):
        Settings.load()


def test_selected_models_report_disabled_gate_without_silent_fixed_hint_fallback(client, app, monkeypatch):
    env = environment(client, app, monkeypatch, allow_local_students=True,
                      local_student_privacy_confirmation=PRIVACY_REFERENCE)
    admin = login(client, "demo-admin")
    student(client, app, admin, course_ids=[])
    assert enable(client, admin, env["school"]).status_code == 200
    with TestClient(app, base_url=app.state.settings.app_url) as child:
        headers = password_login(child, "sample.student")
        enroll(child, headers)
        session_id = conversation(child, headers, env["school"]).json()["id"]
        app.state.settings.allow_live_models = False
        app.state.settings.allow_homework_models = False
        descriptor = child.get(BASE, params={"school_id": env["school"]}).json()["assistant"]
        assert not descriptor["available"] and not descriptor.get("private_mode")
        assert "disabled" in descriptor["unavailable_reason"]
        blocked = ask(child, headers, session_id).json()
        assert blocked["status"] == "blocked" and not blocked.get("private_mode")
        assert not env["calls"]


def test_local_demo_cannot_enable_homework_providers(monkeypatch):
    monkeypatch.setenv("AUTH_MODE", "local")
    monkeypatch.setenv("APP_URL", "http://localhost:8090")
    monkeypatch.setenv("ALLOW_HOMEWORK_MODELS", "true")
    with pytest.raises(ValueError, match="disabled live models/agent credentials"):
        Settings.load()


def test_provider_empty_annotations_match_live_response_shape_without_admitting_metadata():
    response = result({"verdict": "allow", "reason_codes": []})
    response["choices"][0]["finish_reason"] = "stop"
    message = response["choices"][0]["message"]
    message.update(annotations=[], refusal=None)
    selected, usage, evidence = visible_response(response, "synthetic-key", 500, 10000)
    assert selected == {"verdict": "allow", "reason_codes": [], "input_risk_codes": []}
    assert usage["total_tokens"] == 130 and "annotations" not in evidence
    for invalid in ([{"type": "url_citation", "url": "https://untrusted.example/"}], None, {}, ""):
        message["annotations"] = invalid
        with pytest.raises(ValueError, match="Only visible assistant JSON"):
            visible_response(response, "synthetic-key", 500, 10000)
    message["annotations"] = []
    message["tool_calls"] = []
    with pytest.raises(ValueError, match="Only visible assistant JSON"):
        visible_response(response, "synthetic-key", 500, 10000)


def test_local_teacher_override_stays_within_verified_provider_projects(monkeypatch):
    monkeypatch.setattr(homework_ai, "require_model_policy_snapshot", lambda *args, **kwargs: {"verified": True})
    approved = {"id": "reviewed-project", "endpoint": "https://models.example.test/v1", "credential_fingerprint": "a" * 64}
    data = {"teacher_profile_id": "synthetic-teacher", "school_id": "synthetic-school",
            "teacher_model_override": True, "configuration_snapshot": {"connections": {"triage": approved}},
            "teacher_model_policy_snapshot": {"connection_snapshot": dict(approved)}}
    local = {"auth_provider": "academy"}
    assert validate_teacher_execution(None, local, data) == {"verified": True}
    for field, value in (("id", "other-project"), ("credential_fingerprint", "b" * 64), ("endpoint", "https://other.example/v1")):
        data["teacher_model_policy_snapshot"]["connection_snapshot"] = {**approved, field: value}
        with pytest.raises(HTTPException, match="provider project"):
            validate_teacher_execution(None, local, data)
        assert validate_teacher_execution(None, {"auth_provider": "core"}, data) == {"verified": True}


def test_completed_model_phases_charge_known_usage_and_allow_the_next_phase(client, app, monkeypatch):
    env = environment(client, app, monkeypatch)
    first = ask(client, env["headers"], env["session"]["id"]).json()
    assert first["status"] == "released"
    with app.state.database.factory() as db:
        stored = db.get(Record, first["id"]).value
        assert stored["reservation"] == 260
        assert all(phase["charge"]["charged_tokens"] == 130 for phase in stored["phases"].values())
        max_reservation = max(phase["send_marker"]["reservation"] for phase in stored["phases"].values())

    with TestClient(app, base_url=app.state.settings.app_url) as admin:
        admin_headers = login(admin, "demo-admin")
        revise(admin, admin_headers, env["school"], approve=True, max_daily_tokens=max_reservation + 1000)
    fresh = conversation(client, env["headers"], env["school"], key="synthetic-known-charge-next-session").json()
    second = ask(client, env["headers"], fresh["id"], key="synthetic-known-charge-next-question").json()
    assert second["status"] == "released"
    assert [call[2] for call in env["calls"]] == ["triage", "safety", "triage", "safety"]


def test_uncertain_model_phase_keeps_its_full_reservation_and_never_retries(client, app, monkeypatch):
    env = environment(client, app, monkeypatch)
    attempted = []

    def uncertain(*args):
        attempted.append(args)
        raise provider.ProviderFailure("Synthetic uncertain outcome")

    monkeypatch.setattr(app.state, "classify", uncertain)
    first = ask(client, env["headers"], env["session"]["id"]).json()
    assert first["status"] == "withheld" and first["answer"] is None
    with app.state.database.factory() as db:
        stored = db.get(Record, first["id"]).value
        marker = stored["phases"]["triage"]["send_marker"]
        assert stored["reservation"] == marker["reservation"]
        assert stored["phases"]["triage"]["response"] is None and "charge" not in stored["phases"]["triage"]
    assert ask(client, env["headers"], env["session"]["id"]).json() == first
    assert len(attempted) == 1


def test_fast_processing_is_scoped_to_approved_official_connections(client, app, monkeypatch):
    env = environment(client, app, monkeypatch)
    with TestClient(app, base_url=app.state.settings.app_url) as admin:
        headers = login(admin, "demo-admin")
        saved = admin.get("/api/homework-help/configuration?school_id=" + env["school"]).json()
        invalid = admin.put("/api/homework-help/configuration?school_id=" + env["school"],
                            json={**saved["configuration"], "expected_version": saved["version"], "processing_tier": "fast"}, headers=headers)
        assert invalid.status_code == 403
        official = admin.post("/api/admin/connections", json={"school_id": env["school"], "name": "Synthetic official provider shape",
            "endpoint": "https://api.openai.com/v1", "models": ["triage", "tutor", "safety"],
            "enabled": True, "api_key": "synthetic-only-no-live-access"}, headers=headers)
        assert official.status_code == 201, official.text
        approve_synthetic_openai_project(app, official.json()["id"])
        refs = {phase: {"connection_id": official.json()["id"], "model": phase} for phase in ("triage", "tutor", "safety")}
        revise(admin, headers, env["school"], approve=True, processing_tier="fast", **refs)
    original = app.state.classify

    def fast(connection, key, model, prompt, maximum):
        assert connection["processing_tier"] == "fast"
        response = original(connection, key, model, prompt, maximum)
        response["service_tier"] = "fast"
        return response

    monkeypatch.setattr(app.state, "classify", fast)
    turn = ask(client, env["headers"], env["session"]["id"]).json()
    assert turn["status"] == "released"
    assert all(phase["requested_processing_tier"] == "fast" and phase["service_tier"] == "fast"
               for phase in turn["execution"]["phases"])
    with app.state.database.factory() as db:
        stored = db.get(Record, turn["id"]).value
        assert all(phase["send_marker"]["processing_tier"] == "fast" for phase in stored["phases"].values())
        assert all("processing_tier" not in phase["send_marker"]["connection_snapshot"] for phase in stored["phases"].values())


@pytest.mark.parametrize("failure", ["missing", "expired", "misbound"])
def test_openai_provider_proof_revocation_blocks_unknown_age_core_students_and_reapproval(client, app, monkeypatch, failure):
    env = environment(client, app, monkeypatch, official_models=True)
    first = client.post(f"{BASE}/sessions/{env['session']['id']}/messages", json={"message": "Explain the idea without doing my work.",
        "idempotency_key": "synthetic-before-provider-proof-revocation"}, headers=env["headers"]).json()
    assert first["status"] == "released"
    initial_calls = len(env["calls"])
    if failure == "missing":
        app.state.settings.openai_student_approvals_json = "{}"
    elif failure == "expired":
        timestamp = datetime.now(timezone.utc)
        approve_synthetic_openai_project(app, env["connection"]["id"],
            verified_at=(timestamp - timedelta(days=2)).isoformat(), expires_at=(timestamp - timedelta(days=1)).isoformat())
    else:
        approve_synthetic_openai_project(app, env["connection"]["id"], credential_fingerprint="0" * 64)
    unavailable = client.get(BASE, params={"school_id": env["school"]}).json()["assistant"]
    assert not unavailable["available"] and "Zero Data Retention" in unavailable["unavailable_reason"]
    blocked = client.post(f"{BASE}/sessions/{env['session']['id']}/messages", json={"message": "Give me a new explanation.",
        "idempotency_key": "synthetic-after-provider-proof-revocation"}, headers=env["headers"]).json()
    assert blocked["status"] == "blocked" and len(env["calls"]) == initial_calls
    assert client.get(f"{BASE}/sessions/{env['session']['id']}").json()["messages"][0]["status"] == "withheld"
    admin = login(client, "demo-admin")
    response = client.post("/api/homework-help/configuration/approve", params={"school_id": env["school"]},
        json={"revision_id": env["configuration"]["revision_id"]}, headers=admin)
    assert response.status_code == 403


def test_project_binding_change_needs_human_reapproval_but_same_permission_proof_renewal_preserves_history(client, app, monkeypatch):
    env = environment(client, app, monkeypatch, official_models=True)
    path = f"{BASE}/sessions/{env['session']['id']}"
    first = client.post(path + "/messages", json={"message": "Explain the idea without doing my work.",
        "idempotency_key": "synthetic-project-binding-before"}, headers=env["headers"]).json()
    assert first["status"] == "released"
    old = client.get(BASE, params={"school_id": env["school"]}).json()["assistant"]
    timestamp = datetime.now(timezone.utc)
    approve_synthetic_openai_project(app, env["connection"]["id"], evidence_sha256="c" * 64,
        verified_at=timestamp.isoformat(), expires_at=(timestamp + timedelta(days=2)).isoformat())
    renewed = client.get(BASE, params={"school_id": env["school"]}).json()["assistant"]
    assert renewed["available"] and renewed["image_policy_sha256"] == old["image_policy_sha256"]
    assert client.get(path).json()["messages"][0]["answer"] == first["answer"]
    approve_synthetic_openai_project(app, env["connection"]["id"], project_id="proj_different_synthetic")
    changed = client.get(BASE, params={"school_id": env["school"]}).json()["assistant"]
    assert not changed["approved"] and not changed["available"]
    assert changed["image_policy_sha256"] != old["image_policy_sha256"]
    assert client.get(path).json()["messages"][0]["status"] == "withheld"
    admin = login(client, "demo-admin")
    approved = client.post("/api/homework-help/configuration/approve", params={"school_id": env["school"]},
        json={"revision_id": env["configuration"]["revision_id"]}, headers=admin)
    assert approved.status_code == 200 and approved.json()["approved"]
    headers = login(client)
    second = client.post(path + "/messages", json={"message": "Can you explain another way?", "idempotency_key": "synthetic-project-binding-after"}, headers=headers).json()
    assert second["status"] == "released"
    assert all(call[0]["openai_project"] == "proj_different_synthetic" for call in env["calls"][-2:])
    assert all(call[0]["safety_identifier"].startswith("academy_") and "demo-student" not in call[0]["safety_identifier"] for call in env["calls"])


def test_provider_proof_changed_inflight_stops_next_phase_without_retry(client, app, monkeypatch):
    env = environment(client, app, monkeypatch, official_models=True)
    original = app.state.classify
    def revoke(*args):
        response = original(*args)
        app.state.settings.openai_student_approvals_json = "{}"
        return response
    monkeypatch.setattr(app.state, "classify", revoke)
    response = client.post(f"{BASE}/sessions/{env['session']['id']}/messages", json={"message": "Explain the idea.",
        "idempotency_key": "synthetic-inflight-provider-proof-revocation"}, headers=env["headers"])
    assert response.status_code == 200 and response.json()["status"] == "withheld"
    assert [call[2] for call in env["calls"]] == ["triage"]
    assert client.post(f"{BASE}/sessions/{env['session']['id']}/messages", json={"message": "Explain the idea.",
        "idempotency_key": "synthetic-inflight-provider-proof-revocation"}, headers=env["headers"]).json() == response.json()
    assert len(env["calls"]) == 1
