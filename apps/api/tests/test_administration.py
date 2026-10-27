"""Scoped administrative reviews, immutable evidence and metadata-only activity."""
from datetime import date, timedelta
import json

import pytest
from fastapi import HTTPException
from sqlalchemy import select, text
from sqlalchemy.exc import DatabaseError

from academy.db import Audit, Record, canonical, administration_schema_id
from academy.migrate import migrate
from academy.permissions import bind_scope
from academy.provider import ProviderFailure
from conftest import login
from test_teacher_onboarding import new_school, school_id
from test_teacher_models import connection


def base(app, school=None):
    return f"/api/schools/{school or school_id(app)}/administration"


def create(client, app, headers, collection="agents", **fields):
    response = client.post(base(app) + "/" + collection, json={"name": "Synthetic administrative review", **fields}, headers=headers)
    assert response.status_code == 201, response.text
    return response.json()


def definition(item, **fields):
    return {**{key: value for key, value in item.items() if key not in {"id", "school_id", "version", "revision_id", "updated_at", "builtin", "can_manage", "estimated_max_cost_usd"}}, "expected_version": item["version"], **fields}


def run(client, app, headers, agent, key="synthetic-admin-run-0001"):
    return client.post(base(app) + f"/agents/{agent['id']}/runs", json={"expected_version": agent["version"], "idempotency_key": key}, headers=headers)


def rows(app, kinds=None):
    with app.state.database.factory() as db:
        query = select(Record).order_by(Record.id)
        if kinds:
            query = query.where(Record.kind.in_(kinds))
        return [(row.id, row.kind, row.data) for row in db.scalars(query)]


def model_agent(client, app, headers, **fields):
    integrated = connection(client, headers, school_id(app))
    return create(client, app, headers, execution_mode="model_review", skill_ids=["readiness.inspect", "model.review"], primary={"connection_id": integrated["id"], "model": "economy"}, pricing={"input_per_million": 1, "output_per_million": 2, "verified_on": date.today().isoformat(), "confirmed": True}, per_run_budget_usd=1, **fields), integrated


def response(**changes):
    return {"choices": [{"message": {"role": "assistant", "content": json.dumps({"summary": "Review school setup before tutoring.", "findings": [], "recommendations": ["Approve current course corpora separately."]})}}], "usage": {"prompt_tokens": 100, "completion_tokens": 20, "total_tokens": 120}, **changes}


def test_local_review_records_real_steps_and_curriculum_draft_without_model_or_publication(client, app, monkeypatch):
    headers = login(client, "demo-school")
    calls = []; monkeypatch.setattr(app.state, "classify", lambda *args: calls.append(args))
    catalog = client.get(base(app)).json()
    assert set(catalog["capabilities"]) == {"manage", "run_local", "run_models"}
    assert {item["id"] for item in catalog["builtin_skills"]} == {"readiness.inspect", "curriculum.plan", "model.review"}
    agent = create(client, app, headers, skill_ids=["readiness.inspect", "curriculum.plan"])
    before = rows(app, ["course", "teacher_profile", "course_corpus", "corpus_approval", "school_membership", "enrollment"])
    result = run(client, app, headers, agent)
    assert result.status_code == 201, result.text
    value = result.json()
    assert value["status"] == "complete" and value["current"] and value["usage"] is None
    assert [step["code"] for step in value["steps"]] == ["readiness.inspect", "curriculum.plan"]
    assert value["report"]["counts"]["courses"] == 1 and value["report"]["curriculum_run_id"]
    assert not value["automatic_actions"] and not value["student_content_included"] and not value["resume_available"]
    assert run(client, app, headers, agent).json() == value
    detail = client.get(base(app) + "/runs/" + value["id"])
    assert detail.status_code == 200 and detail.json() == value
    assert client.get(base(app) + "/agents/" + agent["id"]).json()["id"] == agent["id"]
    assert rows(app, ["course", "teacher_profile", "course_corpus", "corpus_approval", "school_membership", "enrollment"]) == before
    assert calls == [] and len(rows(app, ["school_curriculum_run"])) == 1
    changed = client.put(base(app) + "/agents/" + agent["id"], json=definition(agent, instructions="Changed review focus"), headers=headers)
    assert changed.status_code == 200
    assert client.get(base(app) + "/runs/" + value["id"]).json()["current"] is False


@pytest.mark.parametrize("collection", ["agents", "skills"])
def test_configuration_versions_archive_restore_and_stale_edits(client, app, collection):
    headers = login(client, "demo-admin")
    item = create(client, app, headers, collection)
    path = base(app) + "/" + collection + "/" + item["id"]
    updated = client.put(path, json=definition(item, name="Changed title"), headers=headers)
    assert updated.status_code == 200, updated.text
    assert client.put(path, json=definition(item), headers=headers).status_code == 409
    assert client.post(path + "/archive", json={"expected_version": 2}, headers=headers).status_code == 200
    archived = client.get(path).json(); assert archived["archived"] and archived["version"] == 3
    if collection == "agents":
        assert run(client, app, headers, archived).status_code == 409
    assert client.post(path + "/restore", json={"expected_version": 3}, headers=headers).status_code == 200
    versions = client.get(path + "/versions").json()
    assert [version["version"] for version in versions] == [4, 3, 2, 1]
    assert all(set(version) == {"id", "version", "configuration", "sha256", "actor_id", "created_at"} for version in versions)


def test_custom_skills_are_fixed_wrappers_and_disabled_changed_or_foreign_skills_block_runs(client, app):
    headers = login(client, "demo-admin")
    skill = create(client, app, headers, "skills", instructions="Review whether setup is ready.")
    agent = create(client, app, headers, skill_ids=[skill["id"]])
    assert run(client, app, headers, agent).status_code == 201
    assert client.put(base(app) + "/skills/" + skill["id"], json=definition(skill, enabled=False), headers=headers).status_code == 200
    assert run(client, app, headers, agent, "synthetic-admin-run-0002").status_code == 409
    assert client.post(base(app) + "/skills", json={"name": "No arbitrary HTTP", "base_action": "http.get", "url": "https://example.test"}, headers=headers).status_code == 422
    other = new_school(client, headers)
    assert client.post(base(app, other) + "/agents", json={"name": "Foreign skill", "skill_ids": [skill["id"]]}, headers=headers).status_code == 404
    assert client.post(base(app) + "/agents", json={"name": "No hidden model", "skill_ids": ["model.review"]}, headers=headers).status_code == 422


@pytest.mark.parametrize("user", ["demo-instructor", "demo-student", "demo-student2"])
def test_administration_never_grants_teachers_or_students_access(client, app, user):
    headers = login(client, "demo-admin"); agent = create(client, app, headers)
    headers = login(client, user)
    for suffix in ["", "/agents", "/skills", "/activity", "/agents/" + agent["id"], "/agents/" + agent["id"] + "/versions"]:
        assert client.get(base(app) + suffix).status_code == 403
    assert run(client, app, headers, agent).status_code == 403
    assert client.post(base(app) + "/skills", json={"name": "No"}, headers=headers).status_code == 403


def test_cross_school_and_agent_summary_admin_spoof_are_rejected(client, app, monkeypatch):
    headers = login(client, "demo-admin"); agent = create(client, app, headers); other = new_school(client, headers)
    assert client.get(base(app, other) + "/agents/" + agent["id"]).status_code == 404
    login(client, "demo-school")
    assert client.get(base(app, other)).status_code == 403
    identity = {"id": "agent:synthetic", "tenant_id": app.state.settings.tenant_id, "role": "admin", "principal_kind": "agent", "school_id": school_id(app), "permissions": []}
    monkeypatch.setattr(app.state.auth, "actor", lambda request: dict(identity))
    assert client.get(base(app)).status_code == 403
    assert client.post(base(app) + "/agents", json={"name": "No administrator power"}).status_code == 403


def test_model_review_requires_gate_prices_budget_and_registered_connection_before_send(client, app, monkeypatch):
    headers = login(client, "demo-admin"); agent, integrated = model_agent(client, app, headers)
    calls = []; monkeypatch.setattr(app.state, "classify", lambda *args: calls.append(args))
    assert run(client, app, headers, agent).status_code == 403
    assert rows(app, ["admin_run", "admin_run_send"]) == []
    app.state.settings.allow_live_models = True
    low = client.put(base(app) + "/agents/" + agent["id"], json=definition(agent, per_run_budget_usd=0.000001), headers=headers).json()
    assert run(client, app, headers, low).status_code == 429
    assert rows(app, ["admin_run", "admin_run_send"]) == [] and calls == []
    expired = definition(low); expired["pricing"]["verified_on"] = (date.today() - timedelta(days=91)).isoformat()
    assert client.put(base(app) + "/agents/" + agent["id"], json=expired, headers=headers).status_code == 409
    invalid = definition(low, execution_mode="model_review", primary=None)
    assert client.put(base(app) + "/agents/" + agent["id"], json=invalid, headers=headers).status_code == 422


def test_model_review_commits_marker_before_one_send_and_returns_only_advisory_report(client, app, monkeypatch):
    headers = login(client, "demo-admin"); agent, integrated = model_agent(client, app, headers)
    app.state.settings.allow_live_models = True; calls = []
    def provider(*args):
        calls.append(args)
        with app.state.database.factory() as db:
            marker = db.scalar(select(Record).where(Record.kind == "admin_run_send"))
            assert marker and marker.value["reserved_tokens"] > 1000
            assert db.get(Record, marker.parent_id).value["status"] == "pending"
        assert args[2] == "economy" and "demo-student" not in args[3][1]["content"] and "Jordan" not in args[3][1]["content"]
        return response()
    monkeypatch.setattr(app.state, "classify", provider)
    result = run(client, app, headers, agent)
    assert result.status_code == 201, result.text
    value = result.json(); assert value["status"] == "complete" and value["usage"]["total_tokens"] == 120
    assert value["estimated_cost_usd"] == "0.00014" and value["model"]["model"] == "economy"
    serialized = canonical(value)
    for forbidden in ["key_cipher", "credential_fingerprint", "synthetic-model-key", "models.example", "provider_evidence", '"snapshot"', '"prompt"']:
        assert forbidden not in serialized
    assert run(client, app, headers, agent).json() == value and len(calls) == 1
    assert run(client, app, headers, {**agent, "version": agent["version"] + 1}).status_code == 409


@pytest.mark.parametrize("limit", ["runs", "tokens", "input"])
def test_administrative_limits_admit_no_extra_provider_call_or_partial_draft(client, app, monkeypatch, limit):
    headers = login(client, "demo-admin")
    changes = {"max_daily_runs": 1} if limit == "runs" else {"max_daily_tokens": 10000, "instructions": "Bounded review context " * 150} if limit == "tokens" else {"max_input_chars": 1000}
    agent, _ = model_agent(client, app, headers, **changes); app.state.settings.allow_live_models = True
    calls = []; monkeypatch.setattr(app.state, "classify", lambda *args: calls.append(args) or response())
    if limit == "input":
        assert run(client, app, headers, agent).status_code == 422
        assert calls == [] and rows(app, ["admin_run", "admin_run_send"]) == []
    else:
        assert run(client, app, headers, agent).status_code == 201
        assert run(client, app, headers, agent, "synthetic-admin-over-limit").status_code == 429
        assert len(calls) == 1 and len(rows(app, ["admin_run", "admin_run_send"])) == 2


@pytest.mark.parametrize("failure", ["network", "invalid_json", "tool_call", "duplicate_key", "invalid_usage"])
def test_failed_or_uncertain_model_output_is_sealed_audited_and_never_resent(client, app, monkeypatch, failure):
    headers = login(client, "demo-admin"); agent, _ = model_agent(client, app, headers); app.state.settings.allow_live_models = True; calls = []
    def provider(*args):
        calls.append(args)
        if failure == "network":
            raise ProviderFailure("secret-provider-error-must-not-leak")
        raw = response()
        if failure == "invalid_json": raw["choices"][0]["message"]["content"] = "broken synthetic-model-key-never-live"
        if failure == "tool_call": raw["choices"][0]["message"]["tool_calls"] = [{"function": "evil"}]
        if failure == "duplicate_key": raw["choices"][0]["message"]["content"] = '{"summary":"a","summary":"b","findings":[],"recommendations":[]}'
        if failure == "invalid_usage": raw["usage"]["total_tokens"] = -1
        return raw
    monkeypatch.setattr(app.state, "classify", provider)
    result = run(client, app, headers, agent)
    assert result.status_code == 201, result.text
    value = result.json(); assert value["status"] == "failed" and value["report"] is None
    assert value["usage"] is not None if failure in {"invalid_json", "tool_call", "duplicate_key"} else value["usage"] is None
    assert "secret-provider" not in canonical(value)
    assert run(client, app, headers, agent).json() == value and len(calls) == 1
    assert len(rows(app, ["admin_run_send"])) == len(rows(app, ["admin_run_result"])) == 1
    assert "synthetic-model-key-never-live" not in canonical(rows(app, ["admin_run_result"]))


@pytest.mark.parametrize("change", ["agent", "skill", "connection", "membership", "session", "gate"])
def test_inflight_change_withholds_and_commits_outcome_before_error(client, app, monkeypatch, change):
    headers = login(client, "demo-school")
    skill = create(client, app, headers, "skills", base_action="model.review")
    integrated = connection(client, headers, school_id(app))
    agent = create(client, app, headers, execution_mode="model_review", skill_ids=["readiness.inspect", skill["id"]], primary={"connection_id": integrated["id"], "model": "economy"}, pricing={"input_per_million": 1, "output_per_million": 2, "verified_on": date.today().isoformat(), "confirmed": True}, per_run_budget_usd=1)
    app.state.settings.allow_live_models = True
    def provider(*args):
        if change == "gate": app.state.settings.allow_live_models = False
        else:
            with app.state.database.write() as db:
                if change in {"agent", "skill", "connection"}:
                    target = db.get(Record, {"agent": agent["id"], "skill": skill["id"], "connection": integrated["id"]}[change]); target.set({**target.value, "enabled": False})
                elif change == "membership":
                    from academy.permissions import membership_id
                    target = db.get(Record, membership_id(school_id(app), "demo-school")); target.set({**target.value, "active": False})
                else:
                    from academy.db import Login
                    login_record = db.scalar(select(Login).where(Login.user_id == "demo-school")); login_record.expires = 0
        return response()
    monkeypatch.setattr(app.state, "classify", provider)
    result = run(client, app, headers, agent)
    assert result.status_code == 409, result.text
    outcomes = rows(app, ["admin_run_result"]); assert len(outcomes) == 1
    outcome = json.loads(outcomes[0][2]); assert outcome["status"] == "withheld" and outcome["report"] is None and outcome["provider_report"]
    assert json.loads(rows(app, ["admin_run"])[0][2])["status"] == "withheld"


def test_pre_send_revocation_retains_reservation_but_records_no_provider_call_or_spend(client, app, monkeypatch):
    headers = login(client, "demo-admin"); agent, _ = model_agent(client, app, headers)
    app.state.settings.allow_live_models = True
    original_actor = app.state.auth.actor; authorizations = []; calls = []
    def reauthorize(request):
        identity = original_actor(request); authorizations.append(identity)
        # Route dependency and admission refresh succeed. Revoke immediately
        # before the separate post-marker check which precedes provider I/O.
        if len(authorizations) == 3:
            with app.state.database.write() as db:
                target = db.get(Record, agent["id"]); target.set({**target.value, "enabled": False})
        return identity
    monkeypatch.setattr(app.state.auth, "actor", reauthorize)
    monkeypatch.setattr(app.state, "classify", lambda *args: calls.append(args) or response())
    result = run(client, app, headers, agent)
    assert result.status_code == 409, result.text
    assert calls == []
    outcome = json.loads(rows(app, ["admin_run_result"])[0][2])
    assert outcome["status"] == "withheld" and outcome["provider_call_attempted"] is False and outcome["live_model_used"] is False
    assert outcome["estimated_cost_usd"] == "0" and outcome["cost_status"] == "no_provider_call" and float(outcome["reserved_cost_usd"]) > 0
    marker = json.loads(rows(app, ["admin_run_send"])[0][2]); assert marker["reserved_cost_usd"] == outcome["reserved_cost_usd"]
    job_id = rows(app, ["admin_run"])[0][0]
    detail = client.get(base(app) + "/runs/" + job_id).json()
    assert detail["provider_call_attempted"] is False and detail["estimated_cost_usd"] == "0" and detail["reserved_cost_usd"] == marker["reserved_cost_usd"]
    with app.state.database.factory() as db:
        event = db.scalars(select(Audit).where(Audit.event_type == "administration.run_completed")).first()
        payload = json.loads(event.payload)
        assert payload["live_model_used"] is False and payload["estimated_cost_usd"] == "0" and payload["cost_status"] == "no_provider_call"
    assert run(client, app, headers, agent).status_code == 201 and calls == []


def test_activity_is_paginated_metadata_only_and_does_not_decrypt_private_jobs(client, app, monkeypatch):
    headers = login(client, "demo-admin"); school = school_id(app); other = new_school(client, headers)
    marker = "PRIVATE_STUDENT_NAME_PROMPT_ANSWER_SECRET"
    with app.state.database.write() as db:
        for index, (kind, scope, value) in enumerate([("homework_ai_message", school, {"status": "pending", "__academy_pii__": marker, "error": marker, "completed_at": marker}), ("lesson_turn", school, {"status": marker, "message": marker, "owner_name": marker}), ("external_review_job", other, {"status": "pending", "title": marker}), ("knowledge_workflow_run", school, {"status": "completed", "teacher_name": marker})]):
            row = Record(id=f"synthetic-activity-{index}", tenant_id=app.state.settings.tenant_id, kind=kind, parent_id="opaque-parent", owner_id="opaque-owner", data=canonical(value)); db.add(row); db.flush(); bind_scope(db, row, scope)
    monkeypatch.setattr(app.state.auth.family, "decrypt", lambda *args: pytest.fail("Activity must never decrypt student data"))
    first = client.get(base(app) + "/activity?limit=1&offset=0").json()
    assert first["total"] == 3 and len(first["items"]) == 1
    full = client.get(base(app) + "/activity?limit=100").json()
    assert marker not in canonical(full)
    assert {item["status"] for item in full["items"]} == {"pending", "recorded", "completed"}
    assert all(not item["can_open"] and item["agent_id"] is None for item in full["items"])
    assert client.get(base(app) + "/activity?kind=homework&status=pending").json()["total"] == 1
    assert client.get(base(app) + "/activity?kind=unknown").status_code == 422
    assert client.get(base(app) + "/activity?limit=101").status_code == 422


def test_schema13_protects_revisions_send_results_terminal_runs_and_preserves_old_records(client, app):
    headers = login(client, "demo-admin"); agent = create(client, app, headers)
    value = run(client, app, headers, agent).json()
    before = rows(app); migrate(app.state.settings); assert rows(app) == before
    with app.state.database.factory() as db:
        assert db.get(Record, administration_schema_id(app.state.settings.tenant_id)).value["version"] == 13
        targets = list(db.scalars(select(Record).where(Record.kind.in_(["admin_agent_revision", "admin_run_result", "admin_run"]))))
        identifiers = [row.id for row in targets]
    for identifier in identifiers:
        with pytest.raises(DatabaseError), app.state.database.write() as db:
            db.execute(text("UPDATE records SET data='{}' WHERE id=:id"), {"id": identifier})
        with pytest.raises(DatabaseError), app.state.database.write() as db:
            db.execute(text("DELETE FROM records WHERE id=:id"), {"id": identifier})
    assert client.get("/api/ready").status_code == 200


def test_schema13_readiness_requires_owner_marker_and_preserves_existing_audit_prefix(client, app):
    with app.state.database.factory() as db:
        original = [(row.id, row.payload, row.hash) for row in db.scalars(select(Audit).order_by(Audit.sequence))]
        prior = {row.id: row.data for row in db.scalars(select(Record).where(Record.kind == "schema")) if row.id != administration_schema_id(app.state.settings.tenant_id)}
    with app.state.database.write() as db:
        db.delete(db.get(Record, administration_schema_id(app.state.settings.tenant_id)))
    assert client.get("/api/ready").status_code == 503
    migrate(app.state.settings)
    with app.state.database.factory() as db:
        current = [(row.id, row.payload, row.hash) for row in db.scalars(select(Audit).order_by(Audit.sequence))]
        assert current[:len(original)] == original and len(current) == len(original) + 1
        assert all(db.get(Record, identifier).data == data for identifier, data in prior.items())
    assert client.get("/api/ready").status_code == 200
