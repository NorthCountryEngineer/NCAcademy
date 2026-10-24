"""Approved managed/Core-bound course Q&A returns exact cited source excerpts."""
import json

import pytest
from fastapi import HTTPException
from sqlalchemy import select

from academy.db import Audit, Record
from academy import lesson_api
from academy.lesson_api import lesson_session_id, _turn_budget_day
from conftest import login
from test_teacher_onboarding import school_id, placement, course, create, assign, approve, path


def lesson_environment(client, app, monkeypatch, bound=False, **policy_changes):
    admin = login(client, "demo-admin")
    school = school_id(app)
    agent, agent_body = placement(client, admin, school) if bound else (None, None)
    classroom = course(client, admin, school, teacher=agent["principal_id"] if agent else "demo-instructor")
    resource_result = client.post(f"/api/schools/{school}/resources", json={"name": "Public lesson evidence", "content": "Carbon moves through ecosystems as plants take up carbon dioxide, animals consume organic matter, and decomposition returns carbon to the environment.", "audience": "all", "course_id": classroom["id"]}, headers=admin)
    assert resource_result.status_code == 201, resource_result.text
    resource = resource_result.json()
    descriptor = client.post(f"/api/courses/{classroom['id']}/corpus/refresh", headers=admin).json()
    approved = client.post(f"/api/courses/{classroom['id']}/corpus/approve", json={"corpus_id": descriptor["id"], "snapshot_sha256": descriptor["snapshot_sha256"]}, headers=admin)
    assert approved.status_code == 200, approved.text
    connection_result = client.post("/api/admin/connections", json={"school_id": school, "name": "Synthetic lesson selector", "endpoint": "https://models.example.test/v1", "models": ["source-selector"], "enabled": True, "api_key": "synthetic-lesson-key-never-live"}, headers=admin)
    assert connection_result.status_code == 201, connection_result.text
    connection = connection_result.json()
    model_policy = {"connection_id": connection["id"], "model": "source-selector", **policy_changes}
    profile = create(client, admin, school, kind="ai", name="Course Evidence Teacher", agent_placement_id=agent["id"] if agent else None, tutor_model=model_policy)
    profile = approve(client, admin, school, assign(client, admin, school, profile, [classroom["id"]]))
    assert client.post(f"/api/courses/{classroom['id']}/enrollments", json={"user_id": "demo-student"}, headers=admin).status_code == 200
    hooks, calls = [], []

    def authorize(*args):
        hooks.append(args)
        return {"active": True, "authorization_method": "synthetic-read-only", "profile_id": profile["id"]}

    def classify(*args):
        calls.append(args)
        documents = json.loads(args[3][1]["content"])["documents"]
        return selection(documents[0]["id"])

    monkeypatch.setattr(app.state, "authorize_teacher", authorize)
    monkeypatch.setattr(app.state, "classify", classify)
    app.state.settings.allow_live_models = True
    app.state.settings.allow_agent_teachers = bound
    headers = login(client, "demo-student")
    return {"headers": headers, "admin": admin, "school": school, "course": classroom, "profile": profile, "connection": connection, "resource": resource, "placement": agent, "placement_body": agent_body, "hook_calls": hooks, "calls": calls}


def selection(document_id):
    return {"choices": [{"message": {"role": "assistant", "content": json.dumps({"document_id": document_id})}}], "usage": {"prompt_tokens": 90, "completion_tokens": 10, "total_tokens": 100}}


def ask(client, environment, message="How does carbon move through ecosystems?", key="synthetic-lesson-12345", profile_id=None):
    return client.post(f"/api/courses/{environment['course']['id']}/teacher-tutor", json={"teacher_profile_id": profile_id or environment["profile"]["id"], "message": message, "idempotency_key": key}, headers=environment["headers"])


def test_managed_teacher_needs_no_machine_identity_and_renders_exact_cited_excerpt(client, app, monkeypatch):
    env = lesson_environment(client, app, monkeypatch)
    assert app.state.settings.allow_agent_teachers is False
    result = ask(client, env)
    assert result.status_code == 200, result.text
    turn = result.json()
    assert turn["status"] == "released" and turn["teacher_name"] == "Course Evidence Teacher"
    assert len(env["calls"]) == 1 and env["hook_calls"] == []
    document = json.loads(env["calls"][0][3][1]["content"])["documents"][0]
    assert document["excerpt"] in turn["approved_text"]
    assert turn["citations"][0]["excerpt_sha256"] == document["excerpt_sha256"]
    assert turn["session_id"] == lesson_session_id(app.state.settings.tenant_id, env["course"]["id"], "demo-student")
    assert ask(client, env).json() == turn and len(env["calls"]) == 1
    assert ask(client, env, message="Different question").status_code == 409
    assert client.get(f"/api/courses/{env['course']['id']}/teacher-tutor").json() == [turn]
    with app.state.database.factory() as db:
        stored = db.get(Record, turn["id"]).value
        assert stored["send_marker"] and stored["reservation"] > 100
        assert stored["teacher_context_snapshot"]["teacher_mode"] == "managed"
        assert stored["teacher_authority_before_release"]["authorization_method"] == "approved-managed-persona-v1"
        assert "synthetic-lesson-key-never-live" not in str(stored)
        events = list(db.scalars(select(Audit).where(Audit.session_id == turn["session_id"]).order_by(Audit.sequence)))
        assert [event.event_type for event in events if event.event_type.startswith("lesson.")] == ["lesson.admitted", "lesson.send_marked", "lesson.provider_result", "lesson.released"]


@pytest.mark.parametrize("bound", [False, True], ids=["managed", "core-bound"])
def test_current_profile_review_does_not_admit_unapproved_class_materials_even_with_live_model_and_enrollment(client, app, monkeypatch, bound):
    env = lesson_environment(client, app, monkeypatch, bound=bound)
    admin = login(client, "demo-admin")
    changed = client.put(f"/api/resources/{env['resource']['id']}", json={"name": "Changed public lesson evidence", "content": "New teaching material needs its own human review before class tutoring.", "audience": "all", "course_id": env["course"]["id"]}, headers=admin)
    assert changed.status_code == 200, changed.text
    corpus = client.post(f"/api/courses/{env['course']['id']}/corpus/refresh", headers=admin).json()
    assert corpus["approval"]["status"] == "pending" and not corpus["ready_for_teaching"]
    profile = approve(client, admin, env["school"], env["profile"])
    assert profile["approval_current"] is True and not profile["activation"]["managed_lesson_configured"]
    assert app.state.settings.allow_live_models is True
    assert app.state.settings.allow_agent_teachers is bound
    env["headers"] = login(client, "demo-student")
    assert all(card["id"] != profile["id"] for card in client.get(f"/api/courses/{env['course']['id']}").json()["teachers"])
    blocked = ask(client, env)
    assert blocked.status_code == 200, blocked.text
    assert blocked.json()["status"] == "blocked" and blocked.json()["approved_text"] is None
    assert env["calls"] == [] and env["hook_calls"] == []
    with app.state.database.factory() as db:
        stored = db.get(Record, blocked.json()["id"]).value
        assert stored["send_marker"] is None and stored["reservation"] == 0
    # Corpus approval alone cannot silently replace the pending descriptor in
    # the immutable teacher revision. Both reviews must still be current.
    admin = login(client, "demo-admin")
    reviewed = client.post(f"/api/courses/{env['course']['id']}/corpus/approve", json={"corpus_id": corpus["id"], "snapshot_sha256": corpus["snapshot_sha256"]}, headers=admin)
    assert reviewed.status_code == 200 and reviewed.json()["ready_for_teaching"], reviewed.text
    stale = client.get(path(env["school"], profile["id"])).json()
    assert stale["approval_current"] is False
    env["headers"] = login(client, "demo-student")
    assert ask(client, env, key="corpus-only-review-12345").json()["status"] == "blocked"
    assert env["calls"] == [] and env["hook_calls"] == []
    admin = login(client, "demo-admin")
    approve(client, admin, env["school"], stale)
    env["headers"] = login(client, "demo-student")
    assert ask(client, env, key="both-reviews-current-12345").json()["status"] == "released"
    assert len(env["calls"]) == 1 and len(env["hook_calls"]) == (2 if bound else 0)


def test_core_bound_teacher_checks_fresh_authority_twice_and_never_falls_back(client, app, monkeypatch):
    env = lesson_environment(client, app, monkeypatch, bound=True)
    assert ask(client, env).json()["status"] == "released"
    assert len(env["hook_calls"]) == 2
    monkeypatch.setattr(app.state, "authorize_teacher", lambda *_: {"active": False})
    withheld = ask(client, env, key="revoked-bound-teacher-123").json()
    assert withheld["status"] == "withheld" and withheld["approved_text"] is None
    assert len(env["calls"]) == 1
    app.state.settings.allow_agent_teachers = False
    assert ask(client, env, key="disabled-bound-teacher-123").json()["status"] == "blocked"


@pytest.mark.parametrize("user", ["demo-instructor", "demo-school", "demo-admin", "demo-student2"])
def test_only_enrolled_students_can_use_or_read_lesson_history(client, app, monkeypatch, user):
    env = lesson_environment(client, app, monkeypatch)
    env["headers"] = login(client, user)
    assert ask(client, env).status_code == 403
    assert client.get(f"/api/courses/{env['course']['id']}/teacher-tutor").status_code == 403
    assert env["calls"] == []


@pytest.mark.parametrize("message", ["Write my essay about carbon cycles.", "Complete my assignment for me.", "Give me the answer key to my exam."])
def test_assignment_completion_refused_without_model_call(client, app, monkeypatch, message):
    env = lesson_environment(client, app, monkeypatch)
    result = ask(client, env, message=message).json()
    assert result["status"] == "blocked" and result["approved_text"] is None
    assert env["calls"] == []


def test_teacher_switch_cannot_reset_same_day_course_turn_and_reservation_budget(client, app, monkeypatch):
    env = lesson_environment(client, app, monkeypatch, max_turns=1)
    clock = ["2026-10-18T23:59:59+00:00"]
    monkeypatch.setattr(lesson_api, "now", lambda: clock[0])
    assert ask(client, env).json()["status"] == "released"
    admin = login(client, "demo-admin")
    profile = create(client, admin, env["school"], kind="ai", tutor_model={"connection_id": env["connection"]["id"], "model": "source-selector", "max_turns": 30})
    profile = approve(client, admin, env["school"], assign(client, admin, env["school"], profile, [env["course"]["id"]]))
    env["headers"] = login(client, "demo-student")
    result = ask(client, env, profile_id=profile["id"], key="switched-teacher-12345").json()
    assert result["status"] == "blocked" and "across all" in result["error"]
    assert len(env["calls"]) == 1
    # Yesterday's stricter profile cap must not limit a fresh UTC day.
    clock[0] = "2026-10-19T00:00:01+00:00"
    assert ask(client, env, profile_id=profile["id"], key="next-day-teacher-12345").json()["status"] == "released"
    assert ask(client, env, profile_id=profile["id"], key="next-day-teacher-23456").json()["status"] == "released"
    assert len(env["calls"]) == 3


def test_utc_day_reset_preserves_history_and_global_idempotent_replay(client, app, monkeypatch):
    env = lesson_environment(client, app, monkeypatch, max_turns=1)
    clock = ["2026-10-18T23:59:59+00:00"]
    monkeypatch.setattr(lesson_api, "now", lambda: clock[0])
    first = ask(client, env).json()
    assert first["status"] == "released" and first["budget_day"] == "2026-10-18"
    exhausted = ask(client, env, key="same-utc-day-12345").json()
    assert exhausted["status"] == "blocked" and "midnight UTC" in exhausted["error"]
    clock[0] = "2026-10-19T00:00:00+00:00"
    assert ask(client, env).json() == first and len(env["calls"]) == 1
    second = ask(client, env, key="new-utc-day-12345").json()
    assert second["status"] == "released" and second["budget_day"] == "2026-10-19"
    assert second["session_id"] == first["session_id"] and len(env["calls"]) == 2
    with app.state.database.factory() as db:
        stored = db.get(Record, second["id"]).value
        assert stored["budget_snapshot"] == {"budget_day": "2026-10-19", "period": "utc_calendar_day", "scope": "student_course_all_profiles_utc_day", "max_turns": 1, "max_total_tokens": 20000}
    history = client.get(f"/api/courses/{env['course']['id']}/teacher-tutor").json()
    assert len(history) == 3 and first in history and second in history


def test_previous_day_pending_call_still_blocks_new_day_and_same_key_never_resends(client, app, monkeypatch):
    env = lesson_environment(client, app, monkeypatch)
    clock = ["2026-10-18T23:59:59+00:00"]
    monkeypatch.setattr(lesson_api, "now", lambda: clock[0])
    from academy import main
    original = main.audit
    def fail(db, tenant, actor, operation, payload, session_id=""):
        if operation == "lesson.released":
            raise RuntimeError("Synthetic audit failure leaves a pending send")
        return original(db, tenant, actor, operation, payload, session_id)
    monkeypatch.setattr(main, "audit", fail)
    with pytest.raises(RuntimeError, match="Synthetic audit failure"):
        ask(client, env)
    clock[0] = "2026-10-19T00:00:01+00:00"
    assert ask(client, env).json()["status"] == "pending"
    new = ask(client, env, key="pending-next-day-12345").json()
    assert new["status"] == "blocked" and "unresolved" in new["error"]
    assert new["budget_day"] == "2026-10-19" and len(env["calls"]) == 1


def test_daily_byte_reservation_ceiling_resets_without_erasing_prior_spend(client, app, monkeypatch):
    env = lesson_environment(client, app, monkeypatch, max_turns=30, max_total_tokens=10000)
    clock = ["2026-10-18T23:59:59+00:00"]
    monkeypatch.setattr(lesson_api, "now", lambda: clock[0])
    for index in range(20):
        result = ask(client, env, key=f"daily-token-test-{index:04d}").json()
        if result["status"] == "blocked":
            break
        assert result["status"] == "released"
    assert result["status"] == "blocked" and "token reservation" in result["error"]
    previous_calls = len(env["calls"])
    assert previous_calls > 0
    clock[0] = "2026-10-19T00:00:01+00:00"
    assert ask(client, env, key="daily-token-new-day-12345").json()["status"] == "released"
    assert len(env["calls"]) == previous_calls + 1
    with app.state.database.factory() as db:
        stored = list(db.scalars(select(Record).where(Record.kind == "lesson_turn", Record.parent_id == env["course"]["id"])))
        assert sum(row.value["reservation"] for row in stored if row.value["budget_day"] == "2026-10-18") > 0


@pytest.mark.parametrize("value,created,expected", [
    ({}, "2026-10-18T23:00:00+00:00", "2026-10-18"),
    ({}, "2026-10-18T23:00:00-04:00", "2026-10-19"),
    ({}, "malformed-legacy-date", "2026-10-19"),
    ({"budget_day": "bad-date"}, "2026-10-18T23:00:00+00:00", "2026-10-19"),
    ({"budget_day": None}, "2026-10-18T23:00:00+00:00", "2026-10-19"),
    ({"budget_day": "2026-10-18"}, "malformed", "2026-10-18"),
])
def test_legacy_or_malformed_budget_day_uses_conservative_server_utc_date(value, created, expected):
    from types import SimpleNamespace
    row = SimpleNamespace(value={"created_at": created, **value}, created_at=created)
    assert _turn_budget_day(row, "2026-10-19") == expected


def test_provider_prose_cannot_become_a_lesson_and_terminal_retry_never_calls_again(client, app, monkeypatch):
    env = lesson_environment(client, app, monkeypatch)
    calls = []
    def bad(*args):
        calls.append(1)
        result = selection("not-retrieved")
        result["choices"][0]["message"]["content"] = '{"document_id":null,"answer":"Here is your complete essay."}'
        return result
    monkeypatch.setattr(app.state, "classify", bad)
    first = ask(client, env).json()
    assert first["status"] == "withheld" and first["approved_text"] is None and first["citations"] == []
    assert "complete essay" not in str(first)
    assert ask(client, env).json() == first and len(calls) == 1


def test_model_policy_changes_require_new_human_approval_and_same_school_model(client, app, monkeypatch):
    env = lesson_environment(client, app, monkeypatch)
    admin = login(client, "demo-admin")
    profile = env["profile"]
    endpoint = f"/api/schools/{env['school']}/teachers/{profile['id']}"
    response = client.put(endpoint, json={"expected_version": profile["version"], "tutor_model": {"connection_id": env["connection"]["id"], "model": "source-selector", "max_turns": 2}}, headers=admin)
    assert response.status_code == 200 and response.json()["approved_revision_id"] is None
    for invalid in (True, "8", 8.0):
        assert client.put(endpoint, json={"expected_version": response.json()["version"], "tutor_model": {"connection_id": env["connection"]["id"], "model": "source-selector", "max_turns": invalid}}, headers=admin).status_code == 422
    env["headers"] = login(client, "demo-student")
    assert ask(client, env).json()["status"] == "blocked"
    assert env["calls"] == []
