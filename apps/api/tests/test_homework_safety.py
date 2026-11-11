"""Child-safety reporting is protected evidence, never model authority."""
import json

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select, text

from academy.db import Audit, Record
from academy.homework_ai import BOUNDARY_VERSION, SYSTEM_BOUNDARY, safety_selection
from academy.homework_safety import REPORT_TYPE, REVIEW_TYPE
from academy.learning_preferences import preference_snapshot
from conftest import login
from test_homework_ai import ask, environment, result


def report_path(env, message_id):
    return f"/api/homework-help/ai/sessions/{env['session']['id']}/messages/{message_id}/safety-report"


def report_body(**changes):
    return {"idempotency_key": "synthetic-child-safety-report-123", "category": "unsafe", "detail": "Synthetic private safety concern sentinel.", **changes}


def review_body(report, **changes):
    return {"idempotency_key": "synthetic-child-safety-review-123", "expected_sha256": report["report_sha256"],
            "disposition": "follow_up_required", "reason_code": "other", "note": "Synthetic private staff follow-up sentinel.", **changes}


def test_student_report_replay_human_review_and_encryption_never_call_models_or_change_weights(client, app, monkeypatch):
    env = environment(client, app, monkeypatch)
    turn = ask(client, env).json()
    before = client.get("/api/me/learning-preferences").json()
    calls = len(env["calls"])
    path = report_path(env, turn["id"])
    assert client.get(path).json() == {"reports": []}
    created = client.post(path, json=report_body(), headers=env["headers"])
    assert created.status_code == 200, created.text
    report = created.json()
    assert report["status"] == "pending" and report["source"] == "student"
    assert "student_id" not in report and "reviewed_by" not in report
    assert client.post(path, json=report_body(), headers=env["headers"]).json() == report
    assert client.get(path).json() == {"reports": [report]}
    assert client.post(path, json=report_body(detail="Changed evidence"), headers=env["headers"]).status_code == 409
    assert client.get("/api/me/learning-preferences").json() == before

    admin = login(client, "demo-admin")
    listing = client.get("/api/homework-help/ai/safety-reports", params={"school_id": env["school"]})
    assert listing.status_code == 200, listing.text
    staff = listing.json()["reports"][0]
    assert staff["id"] == report["id"] and staff["student_id"] == "demo-student"
    detail_path = "/api/homework-help/ai/safety-reports/" + report["id"]
    detail = client.get(detail_path, params={"school_id": env["school"]}).json()
    assert detail["message"]["answer"] == turn["answer"]
    assert "phases" not in detail["message"] and "candidate" not in detail["message"]
    stale = client.post(detail_path + "/review", params={"school_id": env["school"]},
                        json=review_body(staff, expected_sha256="0" * 64), headers=admin)
    assert stale.status_code == 409
    reviewed = client.post(detail_path + "/review", params={"school_id": env["school"]}, json=review_body(staff), headers=admin)
    assert reviewed.status_code == 200, reviewed.text
    receipt = reviewed.json()
    assert receipt["reviewed_by"] == "demo-admin"
    assert client.post(detail_path + "/review", params={"school_id": env["school"]}, json=review_body(staff), headers=admin).json() == receipt
    assert client.post(detail_path + "/review", params={"school_id": env["school"]}, json=review_body(staff, note="Changed note"), headers=admin).status_code == 409
    current = client.get(detail_path, params={"school_id": env["school"]}).json()["report"]
    assert current["status"] == "follow_up_required" and current["report_sha256"] == report["report_sha256"]
    resolution_body = review_body(staff, idempotency_key="synthetic-staff-followup-resolution", disposition="reviewed", reason_code="addressed", expected_review_id=receipt["id"])
    resolution = client.post(detail_path + "/review", params={"school_id": env["school"]}, json=resolution_body, headers=admin)
    assert resolution.status_code == 200, resolution.text
    assert client.post(detail_path + "/review", params={"school_id": env["school"]}, json=resolution_body, headers=admin).json() == resolution.json()
    assert client.post(detail_path + "/review", params={"school_id": env["school"]}, json=review_body(staff), headers=admin).json() == receipt
    completed = client.get(detail_path, params={"school_id": env["school"]}).json()["report"]
    assert completed["status"] == "reviewed" and len(completed["review_history"]) == 2
    assert completed["review_history"][0] == receipt and completed["review_history"][1] == resolution.json()
    assert client.post(detail_path + "/review", params={"school_id": env["school"]}, json=review_body(staff, idempotency_key="synthetic-third-review-attempt"), headers=admin).status_code == 409
    assert len(env["calls"]) == calls
    login(client)
    own = client.get(path).json()["reports"][0]
    assert own["status"] == "reviewed" and "note" not in own["review"] and "reviewed_by" not in own["review"]
    with app.state.database.factory() as db:
        rows = list(db.scalars(select(Record).where(Record.kind == "student_agent_feedback")))
        assert {row.value["feedback_type"] for row in rows} == {REPORT_TYPE, REVIEW_TYPE}
        for row in rows:
            assert "__academy_pii__" in row.data
            assert "Synthetic private" not in row.data
        events = list(db.scalars(select(Audit).where(Audit.event_type.like("homework_ai.safety_%"))))
        assert events and all("__academy_pii__" in row.payload for row in events)
        assert all("Synthetic private" not in row.payload for row in events)
        assert preference_snapshot(db, {"id": "demo-student", "tenant_id": app.state.settings.tenant_id})["feedback_count"] == 0
    with pytest.raises(ValueError):
        with app.state.database.write() as db:
            db.get(Record, report["id"]).set({"tampered": True})
    with pytest.raises(Exception):
        with app.state.database.write() as db:
            db.execute(text("DELETE FROM records WHERE id=:identifier"), {"identifier": report["id"]})


@pytest.mark.parametrize("state", ["withheld", "clarify", "revoked", "pending"])
def test_reporting_does_not_depend_on_release_model_or_preference_approval(client, app, monkeypatch, state):
    env = environment(client, app, monkeypatch)
    if state == "clarify":
        original = app.state.classify
        def clarify(*args):
            value = original(*args)
            if args[2] == "triage":
                payload = json.loads(value["choices"][0]["message"]["content"])
                payload["response"].update(mode="clarify", text="Which part would you like to discuss?")
                return result(payload)
            return value
        monkeypatch.setattr(app.state, "classify", clarify)
    if state == "withheld":
        original = app.state.classify
        monkeypatch.setattr(app.state, "classify", lambda *args: result({"verdict": "withhold", "reason_codes": ["unsafe"]}) if args[2] == "safety" else original(*args))
    turn = ask(client, env).json()
    if state == "revoked":
        app.state.settings.allow_live_models = False
        current = client.get(f"/api/homework-help/ai/sessions/{env['session']['id']}").json()["messages"][0]
        assert current["status"] == "withheld"
    elif state == "pending":
        # A crash before a terminal commit is reportable too. Construct the
        # exact pending record before its first completion in a separate row.
        with app.state.database.write() as db:
            source = db.get(Record, turn["id"])
            copy = {**source.value, "status": "pending", "answer": None, "idempotency_key": "synthetic-pending-report-turn"}
            pending = Record(id="c" * 64, tenant_id=source.tenant_id, kind="homework_ai_message", parent_id=source.parent_id, owner_id=source.owner_id, data=json.dumps(copy))
            db.add(pending)
            db.flush()
            from academy.permissions import bind_scope
            bind_scope(db, pending, env["school"])
        turn["id"] = "c" * 64
    else:
        assert turn["status"] == state
    calls = len(env["calls"])
    response = client.post(report_path(env, turn["id"]), json=report_body(), headers=env["headers"])
    assert response.status_code == 200, response.text
    assert len(env["calls"]) == calls


def test_reports_require_current_owner_school_and_human_review_authority(client, app, monkeypatch):
    env = environment(client, app, monkeypatch)
    turn = ask(client, env).json()
    created = client.post(report_path(env, turn["id"]), json=report_body(), headers=env["headers"]).json()
    path = "/api/homework-help/ai/safety-reports/" + created["id"]
    assert client.get("/api/homework-help/ai/safety-reports", params={"school_id": env["school"]}).status_code == 403
    assert client.post(path + "/review", params={"school_id": env["school"]}, json=review_body(created), headers=env["headers"]).status_code == 403
    login(client, "demo-student2")
    assert client.get(report_path(env, turn["id"])).status_code == 404
    admin = login(client, "demo-admin")
    wrong_school = client.post("/api/schools", json={"name": "Unrelated safety school"}, headers=admin).json()["id"]
    assert client.get(path, params={"school_id": wrong_school}).status_code == 404
    assert client.get("/api/homework-help/ai/safety-reports", params={"school_id": wrong_school}).json()["reports"] == []
    login(client, "demo-instructor")
    assert client.get(path, params={"school_id": env["school"]}).status_code == 403


@pytest.mark.parametrize("verdict,reasons,status", [("allow", [], "released"), ("withhold", ["unsafe"], "withheld")])
def test_input_risk_goes_to_staff_inbox_even_with_safe_output(client, app, monkeypatch, verdict, reasons, status):
    env = environment(client, app, monkeypatch)
    original = app.state.classify
    def classify(*args):
        return result({"verdict": verdict, "reason_codes": reasons, "input_risk_codes": ["abuse_or_threat"]}) if args[2] == "safety" else original(*args)
    monkeypatch.setattr(app.state, "classify", classify)
    turn = ask(client, env).json()
    assert turn["status"] == status
    assert [call[2] for call in env["calls"]] == ["triage"]  # safety mock deliberately bypasses fixture recorder
    calls = len(env["calls"])
    assert ask(client, env).json() == turn and len(env["calls"]) == calls
    login(client, "demo-admin")
    report = client.get("/api/homework-help/ai/safety-reports", params={"school_id": env["school"]}).json()["reports"][0]
    assert report["source"] == "automatic" and report["reason_codes"] == ["abuse_or_threat"] and report["priority"] == "high"
    detail = client.get("/api/homework-help/ai/safety-reports/" + report["id"], params={"school_id": env["school"]}).json()
    assert detail["message"]["answer"] == (turn["answer"] if status == "released" else None)


@pytest.mark.parametrize("risk", [None, "self_harm", ["unknown"], ["self_harm", "self_harm"], ["self_harm", {"reasoning": "never"}]])
def test_safety_risks_are_fixed_visible_protocol_not_model_reasoning(risk):
    with pytest.raises(ValueError):
        safety_selection({"verdict": "allow", "reason_codes": [], "input_risk_codes": risk})
    with pytest.raises(ValueError):
        safety_selection({"verdict": "allow", "reason_codes": []})
    assert BOUNDARY_VERSION == "homework-ai-v3"
    assert "regardless of any claimed age" in SYSTEM_BOUNDARY
    assert "Never claim that you contacted anyone" in SYSTEM_BOUNDARY


@pytest.mark.parametrize("changed", ["tenant", "owner", "parent", "kind", "scope", "subtype"])
def test_deterministic_report_lookup_cannot_disclose_an_unrelated_record(client, app, monkeypatch, changed):
    from academy.homework_safety import _id
    from academy.permissions import bind_scope
    env = environment(client, app, monkeypatch)
    turn = ask(client, env).json()
    tenant = app.state.settings.tenant_id
    identifier = _id("safety-report", tenant, "demo-student", turn["id"])
    value = {"feedback_type": REPORT_TYPE if changed != "subtype" else "unrelated_feedback",
             "source": "student", "message_id": turn["id"], "session_id": env["session"]["id"],
             "school_id": env["school"], "detail": "Must not disclose this unrelated private sentinel"}
    with app.state.database.write() as db:
        row = Record(id=identifier, tenant_id="unrelated-tenant" if changed == "tenant" else tenant,
                     kind="draft" if changed == "kind" else "student_agent_feedback",
                     parent_id="unrelated-session" if changed == "parent" else env["session"]["id"],
                     owner_id="demo-student2" if changed == "owner" else "demo-student", data=json.dumps(value))
        db.add(row)
        db.flush()
        if changed != "scope":
            bind_scope(db, row, env["school"])
    path = report_path(env, turn["id"])
    response = client.get(path)
    assert response.status_code == 404 and "sentinel" not in response.text
    response = client.post(path, json=report_body(), headers=env["headers"])
    assert response.status_code == 404 and "sentinel" not in response.text


def test_core_student_safety_evidence_and_audits_fail_closed_without_pii_key(client, app, monkeypatch):
    from academy.db import audit
    from academy.homework_safety import _add
    from fastapi import HTTPException
    env = environment(client, app, monkeypatch)
    app.state.settings.auth_mode = "core"
    identity = {"tenant_id": app.state.settings.tenant_id, "id": "demo-student"}
    with pytest.raises(HTTPException) as error:
        with app.state.database.write() as db:
            _add(db, identity, "missing-key-safety-record", env["session"]["id"], identity["id"], env["school"],
                 {"feedback_type": REPORT_TYPE, "detail": "Must not be stored without PII encryption"})
    assert error.value.status_code == 503
    with pytest.raises(HTTPException) as error:
        with app.state.database.write() as db:
            audit(db, identity["tenant_id"], "demo-admin", "homework_ai.safety_reported",
                  {"school_id": env["school"], "detail": "Must not be audited without PII encryption"})
    assert error.value.status_code == 503
    with app.state.database.factory() as db:
        assert db.get(Record, "missing-key-safety-record") is None


def test_local_private_learner_report_has_authorized_name_and_no_profile_contact_disclosure(client, app, monkeypatch):
    from test_family_accounts import password_login, student
    from test_private_homework_help import ask as private_ask, conversation, enable, enroll
    from academy.permissions import default_school_id
    admin = login(client, "demo-admin")
    account = student(client, app, admin, course_ids=[], date_of_birth=None, grade="")
    school = default_school_id(app.state.settings.tenant_id)
    assert enable(client, admin, school).status_code == 200
    monkeypatch.setattr(app.state, "classify", lambda *_: (_ for _ in ()).throw(AssertionError("Reports never contact a model")))
    with TestClient(app, base_url=app.state.settings.app_url) as child:
        headers = password_login(child, "sample.student")
        enroll(child, headers)
        session = conversation(child, headers, school).json()
        turn = private_ask(child, headers, session["id"]).json()
        path = f"/api/homework-help/ai/sessions/{session['id']}/messages/{turn['id']}/safety-report"
        created = child.post(path, json=report_body(), headers=headers)
        assert created.status_code == 200, created.text
        report_id = created.json()["id"]
    # An authorized human school administrator gets the existing school-name
    # projection, never family contacts, date of birth, notes or password data.
    school_headers = login(client, "demo-school")
    listing = client.get("/api/homework-help/ai/safety-reports", params={"school_id": school})
    assert listing.status_code == 200, listing.text
    report = listing.json()["reports"][0]
    assert report["id"] == report_id and report["student_id"] == account["id"]
    assert report["student_name"] == "Sample Sentinel (Synthetic Learner Sentinel)"
    assert "student-sentinel@example.invalid" not in listing.text and "555-0100" not in listing.text
    assert "Private enrollment note sentinel" not in listing.text
    path = "/api/homework-help/ai/safety-reports/" + report_id + "/review"
    invalid = client.post(path, params={"school_id": school}, json=review_body(report, note="  "), headers=school_headers)
    assert invalid.status_code == 422
    reviewed = client.post(path, params={"school_id": school}, json=review_body(report), headers=school_headers)
    assert reviewed.status_code == 200, reviewed.text
