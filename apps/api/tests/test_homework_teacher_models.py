"""Approved teacher model changes preserve Homework identity and replay."""
import json

import pytest
from sqlalchemy import select

from academy.db import Record, canonical, uid
from academy.homework_ai import config_id, digest
from academy.permissions import bind_scope
from academy.teacher_models import pointer_id
from conftest import login
from test_homework_ai import environment, ask, result, triage_result
from test_homework_ai_open_help import remove_enrollments
from test_lesson_tutoring import lesson_environment
from test_teacher_models import connection, policy, save, activate
from test_curated_lessons import photo


def setup(client, app, monkeypatch, *, opt_in=True, images=False, **changes):
    env = environment(client, app, monkeypatch, allow_teacher_models=opt_in,
                      allow_images=images, triage_image_capable=images,
                      tutor_image_capable=images, safety_image_capable=images)
    teacher = lesson_environment(client, app, monkeypatch)
    headers = login(client, "demo-admin")
    integrated = connection(client, headers, env["school"])
    routing = {"mode": "preview", "candidates": [{"id": "deep", "label": "Reasoning candidate", "connection_id": integrated["id"], "model": "reasoning", "tasks": ["reasoning"]}], "rules": [{"task": "reasoning", "candidate_id": "deep"}]}
    active = activate(client, headers, env["school"], teacher["profile"], save(client, headers, env["school"], teacher["profile"], policy(integrated, routing=routing, homework_max_output_tokens=300, **changes)))
    remove_enrollments(app)
    calls = []
    def classify(*args):
        calls.append(args)
        if args[2] == "triage":
            return result(triage_result(teacher_profile_id=teacher["profile"]["id"]))
        if args[2] == "safety":
            return result({"verdict": "allow", "reason_codes": []})
        return result({"mode": "explanation", "text": "An ecosystem connects living things and their surroundings. Which relationship would you like to explore?", "reference_ids": []})
    monkeypatch.setattr(app.state, "classify", classify)
    env["headers"] = login(client)
    return env, teacher, integrated, active, calls, classify


def test_text_handoff_uses_approved_teacher_primary_and_audits_preview_without_executing_it(client, app, monkeypatch):
    env, teacher, integrated, active, calls, _ = setup(client, app, monkeypatch)
    reply = ask(client, env, message="Why do ecosystems change?").json()
    assert reply["status"] == "released", reply
    assert [call[2] for call in calls] == ["triage", "economy", "safety"]
    assert calls[1][4] == 300
    prompt = json.loads(calls[1][3][1]["content"])
    assert prompt["assistant"]["name"] == teacher["profile"]["name"]
    assert prompt["assistant"]["behavior"] == teacher["profile"]["ai_behavior"]
    execution = reply["execution"]
    assert execution["selection_reason"] == "approved_teacher_primary"
    assert execution["preview"]["suggested_model"] == "reasoning"
    assert not execution["automatic_routing_enabled"] and execution["estimated_cost"] is None
    assert execution["phases"][1]["policy_revision_id"] == active["active"]["revision_id"]
    assert "endpoint" not in canonical(execution) and "key_cipher" not in canonical(execution)
    assert "synthetic-model-key" not in canonical(execution)
    with app.state.database.factory() as db:
        stored = db.get(Record, reply["id"]).value
        marker = next(row.value for row in db.scalars(select(Record).where(Record.kind == "homework_ai_send", Record.parent_id == reply["id"])) if row.value.get("phase") == "tutor")
        assert marker["model"] == "economy"
        assert marker["teacher_model_policy_snapshot"] == stored["teacher_model_policy_snapshot"]
        assert marker["routing_preview"]["suggested"]["model"] == "reasoning"
    assert ask(client, env, message="Why do ecosystems change?").json() == reply and len(calls) == 3


def test_model_swap_preserves_prior_conversation_and_teacher_and_uses_new_model_for_next_question(client, app, monkeypatch):
    env, teacher, integrated, active, calls, _ = setup(client, app, monkeypatch)
    first = ask(client, env).json()
    assert first["status"] == "released"
    with app.state.database.factory() as db:
        original = db.get(Record, teacher["profile"]["id"]).data
    headers = login(client, "demo-admin")
    activate(client, headers, env["school"], teacher["profile"], save(client, headers, env["school"], teacher["profile"], policy(integrated, "reasoning"), version=active["version"]))
    env["headers"] = login(client)
    history = client.get(f"/api/homework-help/ai/sessions/{env['session']['id']}").json()["messages"]
    assert history[0] == first
    second = ask(client, env, idempotency_key="second-model-question").json()
    assert second["status"] == "released", second
    assert [call[2] for call in calls] == ["triage", "economy", "safety", "triage", "reasoning", "safety"]
    with app.state.database.factory() as db:
        assert db.get(Record, teacher["profile"]["id"]).data == original


def test_policy_change_during_model_call_withholds_and_never_resends(client, app, monkeypatch):
    env, teacher, integrated, active, calls, transport = setup(client, app, monkeypatch)
    headers = login(client, "demo-admin")
    key = pointer_id(app.state.settings.tenant_id, teacher["profile"]["id"])
    with app.state.database.factory() as db:
        previous = db.get(Record, key).value
    activate(client, headers, env["school"], teacher["profile"], save(client, headers, env["school"], teacher["profile"], policy(integrated, "reasoning"), version=active["version"]))
    with app.state.database.write() as db:
        row = db.get(Record, key); replacement = row.value; row.set(previous)
    def change_after_send(*args):
        response = transport(*args)
        if args[2] == "economy":
            with app.state.database.write() as db:
                db.get(Record, key).set(replacement)
        return response
    monkeypatch.setattr(app.state, "classify", change_after_send)
    env["headers"] = login(client)
    reply = ask(client, env).json()
    assert reply["status"] == "withheld" and reply["answer"] is None
    assert [call[2] for call in calls] == ["triage", "economy"]
    assert ask(client, env).json() == reply and len(calls) == 2


@pytest.mark.parametrize("mode", ["school_opt_out", "photos"])
def test_teacher_override_never_widens_school_opt_in_or_photo_destinations(client, app, monkeypatch, mode):
    env, teacher, integrated, active, calls, _ = setup(client, app, monkeypatch, opt_in=mode != "school_opt_out", images=mode == "photos")
    body = {"images": [{"name": "task.png", "data_url": photo()}], "image_use_confirmed": True} if mode == "photos" else {}
    reply = ask(client, env, **body).json()
    assert reply["status"] == "released", reply
    assert [call[2] for call in calls] == ["triage", "tutor", "safety"]
    assert reply["execution"]["selection_reason"] == ("homework_photos_use_school_models" if mode == "photos" else "approved_school_models")
    assert all(phase["source"] == "school_policy" for phase in reply["execution"]["phases"])


def test_explicit_disabled_teacher_homework_policy_does_not_silently_fall_back(client, app, monkeypatch):
    env, teacher, integrated, active, calls, _ = setup(client, app, monkeypatch, homework_enabled=False)
    reply = ask(client, env).json()
    assert reply["status"] == "withheld" and reply["answer"] is None
    assert [call[2] for call in calls] == ["triage"]


def test_configuration_without_new_opt_in_keeps_legacy_snapshot_shape_and_approval(client, app, monkeypatch):
    env = environment(client, app, monkeypatch)
    with app.state.database.write() as db:
        pointer = db.get(Record, config_id(app.state.settings.tenant_id, env["school"]))
        old = db.get(Record, pointer.value["revision_id"])
        raw = {key: value for key, value in old.value["configuration"].items() if key != "allow_teacher_models"}
        data = {**old.value, "configuration": raw, "configuration_sha256": digest(raw)}
        revision = Record(id=uid(), tenant_id=old.tenant_id, kind="homework_ai_config_revision", parent_id=env["school"], owner_id="demo-admin", data=canonical(data))
        db.add(revision); db.flush(); bind_scope(db, revision, env["school"])
        prior = db.get(Record, pointer.value["approval_id"])
        approval = Record(id=uid(), tenant_id=old.tenant_id, kind="homework_ai_approval", parent_id=revision.id, owner_id="demo-admin", data=canonical({**prior.value, "configuration_sha256": digest(raw)}))
        db.add(approval); db.flush(); bind_scope(db, approval, env["school"])
        pointer.set({**pointer.value, "revision_id": revision.id, "approval_id": approval.id})
    reply = ask(client, env).json()
    assert reply["status"] == "released", reply
    with app.state.database.factory() as db:
        assert "allow_teacher_models" not in db.get(Record, reply["id"]).value["configuration_snapshot"]["configuration"]
    assert client.get(f"/api/homework-help/ai/sessions/{env['session']['id']}").json()["messages"][0] == reply
