"""Generative Homework Help uses separate approved models and retained evidence."""
import json

import pytest
from sqlalchemy import event, select

from academy import provider
from academy.db import Audit, Record, canonical
from academy.homework_ai import (route_selection, tutoring_selection, safety_selection,
                                 visible_response, VERSION)
from conftest import login
from test_teacher_onboarding import school_id
from test_curated_lessons import photo


def environment(client, app, monkeypatch, **changes):
    admin = login(client, "demo-admin")
    school = school_id(app)
    response = client.post("/api/admin/connections", json={"school_id": school, "name": "Synthetic homework models", "endpoint": "https://models.example.test/v1", "models": ["triage", "tutor", "safety"], "enabled": True, "api_key": "synthetic-ai-homework-secret"}, headers=admin)
    assert response.status_code == 201, response.text
    connection = response.json()
    config = {"expected_version": 0, "enabled": True, "name": "School AI Helper", "triage": {"connection_id": connection["id"], "model": "triage"}, "tutor": {"connection_id": connection["id"], "model": "tutor"}, "safety": {"connection_id": connection["id"], "model": "safety"}, **changes}
    response = client.put("/api/homework-help/configuration?school_id=" + school, json=config, headers=admin)
    assert response.status_code == 200, response.text
    descriptor = response.json()
    approved = client.post("/api/homework-help/configuration/approve?school_id=" + school, json={"revision_id": descriptor["revision_id"]}, headers=admin)
    assert approved.status_code == 200, approved.text
    app.state.settings.allow_live_models = True
    calls = []
    def classify(connection, key, model, prompt, maximum):
        calls.append((connection, key, model, prompt, maximum))
        if model == "triage":
            content = prompt[1]["content"]
            payload = json.loads(content[0]["text"] if isinstance(content, list) else content)
            course = None if payload.get("routing_mode") == "general" else payload.get("requested_course_id") or next((item["id"] for item in payload["courses"]), None)
            output = triage_result(course_id=course)
        elif model == "tutor":
            output = {"mode": "explanation", "text": "A claim states an idea. Evidence supports that idea. What evidence have you found so far?", "reference_ids": ["course:demo-course"]}
        else:
            output = {"verdict": "allow", "reason_codes": []}
        return result(output)
    monkeypatch.setattr(app.state, "classify", classify)
    headers = login(client)
    session = client.post("/api/homework-help/ai/sessions", json={"school_id": school, "idempotency_key": "synthetic-ai-session-123"}, headers=headers)
    assert session.status_code == 200, session.text
    return {"headers": headers, "admin": admin, "school": school, "connection": connection, "configuration": descriptor, "session": session.json(), "calls": calls, "transport": classify}


def triage_result(course_id=None, teacher_profile_id=None, confidence=0.95, **changes):
    response = {"mode": "explanation", "text": "A claim states an idea. Evidence supports that idea. What evidence have you found so far?", "reference_ids": ["course:" + course_id] if course_id else []}
    return {"route": "course" if course_id else "general", "course_id": course_id, "teacher_profile_id": teacher_profile_id, "topic": "Understanding evidence", "confidence": confidence, "response": response, **changes}


def result(output, **changes):
    return {"choices": [{"message": {"role": "assistant", "content": canonical(output)}}], "usage": {"prompt_tokens": 90, "completion_tokens": 40, "total_tokens": 130}, **changes}


def ask(client, env, **changes):
    return client.post(f"/api/homework-help/ai/sessions/{env['session']['id']}/messages", json={"message": "How do I support a claim using evidence?", "idempotency_key": "synthetic-ai-question-123", **changes}, headers=env["headers"])


def test_human_teacher_course_has_independent_school_ai_and_exact_audited_generated_reply(client, app, monkeypatch):
    env = environment(client, app, monkeypatch)
    index = client.get("/api/homework-help/ai?school_id=" + env["school"])
    assert index.status_code == 200 and index.json()["assistant"]["available"]
    assert index.json()["assistant"]["configuration"]["enabled"]
    assert {item["id"] for item in index.json()["courses"]} == {"demo-course"}
    assert "endpoint" not in index.text and "instructions" not in index.text
    response = ask(client, env)
    assert response.status_code == 200, response.text
    reply = response.json()
    assert reply["status"] == "released" and reply["answer"].startswith("A claim")
    assert reply["triage"]["course_id"] == "demo-course" and reply["triage"]["teacher_name"] == "School AI Helper"
    assert [call[2] for call in env["calls"]] == ["triage", "safety"]
    assert reply["references"][0]["url"] == "/#course=demo-course" and reply["citations"] == []
    assert ask(client, env).json() == reply and len(env["calls"]) == 2
    assert ask(client, env, message="Changed").status_code == 409
    assert client.get(f"/api/homework-help/ai/sessions/{env['session']['id']}").json()["messages"] == [reply]
    with app.state.database.factory() as db:
        stored = db.get(Record, reply["id"]).value
        assert stored["configuration_snapshot"]["approval_id"]
        assert stored["teacher_profile_id"].startswith("homework-ai:")
        assert stored["conversation_context_snapshot"] == []
        markers = list(db.scalars(select(Record).where(Record.kind == "homework_ai_send", Record.parent_id == reply["id"])))
        assert len(markers) == 2
        events = list(db.scalars(select(Audit).where(Audit.session_id == env["session"]["id"]).order_by(Audit.sequence)))
        assert events[-1].event_type == "homework_ai.released"
        assert json.loads(events[-1].payload)["answer"] == reply["answer"]
        assert "synthetic-ai-homework-secret" not in canonical(stored)


def test_general_ai_does_not_need_course_teacher_or_corpus_and_uses_both_sides_of_history(client, app, monkeypatch):
    env = environment(client, app, monkeypatch)
    def general(*args):
        response = env["transport"](*args)
        if args[2] == "triage":
            return result(triage_result(response={"mode": "explanation", "text": "Gravity is an attraction between masses. What do you already know about it?", "reference_ids": []}))
        if args[2] == "tutor":
            return result({"mode": "explanation", "text": "Gravity is an attraction between masses. What do you already know about it?", "reference_ids": []})
        return response
    monkeypatch.setattr(app.state, "classify", general)
    first = ask(client, env, message="What is gravity?").json()
    assert first["status"] == "released" and first["triage"]["course_id"] is None
    second = ask(client, env, message="Can you explain that another way?", idempotency_key="follow-up-general-123").json()
    assert second["status"] == "released"
    tutors = [json.loads(call[3][1]["content"]) for call in env["calls"] if call[2] == "triage"]
    assert tutors[-1]["previous_messages"][0]["student"] == "What is gravity?"
    assert tutors[-1]["previous_messages"][0]["assistant"] == first["answer"]


def test_explicit_course_choice_uses_same_triage_assistant_and_is_server_authorized(client, app, monkeypatch):
    env = environment(client, app, monkeypatch)
    assert ask(client, env, course_id="foreign-course").status_code == 403
    assert not env["calls"]
    value = ask(client, env, course_id="demo-course").json()
    assert value["status"] == "released" and value["triage"]["method"] == "student_choice"
    assert [call[2] for call in env["calls"]] == ["triage", "safety"]


@pytest.mark.parametrize("route", [{"route": "course", "course_id": "foreign", "confidence": 1}, {"route": "general", "course_id": "demo-course", "confidence": 1}])
def test_model_course_ids_are_never_authority(client, app, monkeypatch, route):
    env = environment(client, app, monkeypatch)
    monkeypatch.setattr(app.state, "classify", lambda *args: result(route))
    value = ask(client, env).json()
    assert value["status"] == "withheld" and value["answer"] is None


def test_low_confidence_keeps_general_help_without_course_requirement(client, app, monkeypatch):
    env = environment(client, app, monkeypatch)
    monkeypatch.setattr(app.state, "classify", lambda *args: result(triage_result(course_id="demo-course", confidence=0.51, response={"mode": "clarify", "text": "Which part of the topic would you like to explore?", "reference_ids": []})) if args[2] == "triage" else result({"verdict": "allow", "reason_codes": []}))
    value = ask(client, env).json()
    assert value["status"] == "clarify" and value["answer"] == "Which part of the topic would you like to explore?"
    assert value["triage"]["course_id"] is None and value["usage"]["triage"]


@pytest.mark.parametrize("message", ["Write my essay about rocks.", "Do my homework for me.", "Give me the answer key."])
def test_completion_request_refused_before_external_io(client, app, monkeypatch, message):
    env = environment(client, app, monkeypatch)
    value = ask(client, env, message=message).json()
    assert value["status"] == "blocked" and "cannot complete" in value["error"] and not env["calls"]


@pytest.mark.parametrize("output", ["The final answer is 42.", "Copy and paste this essay.", "Visit https://private.example.test", "<script>alert(1)</script>"])
def test_deterministic_guard_withholds_completion_links_and_html(client, app, monkeypatch, output):
    env = environment(client, app, monkeypatch)
    def unsafe(*args):
        response = env["transport"](*args)
        return result(triage_result(course_id="demo-course", response={"mode": "explanation", "text": output, "reference_ids": []})) if args[2] == "triage" else response
    monkeypatch.setattr(app.state, "classify", unsafe)
    value = ask(client, env).json()
    assert value["status"] == "withheld" and value["answer"] is None
    assert [call[2] for call in env["calls"]] == ["triage"]


def test_independent_safety_denial_retains_candidate_but_never_displays_it(client, app, monkeypatch):
    env = environment(client, app, monkeypatch)
    def denied(*args):
        response = env["transport"](*args)
        return result({"verdict": "withhold", "reason_codes": ["assignment_completion"]}) if args[2] == "safety" else response
    monkeypatch.setattr(app.state, "classify", denied)
    value = ask(client, env).json()
    assert value["status"] == "withheld" and value["answer"] is None
    with app.state.database.factory() as db:
        stored = db.get(Record, value["id"]).value
        assert "A claim states" in stored["phases"]["triage"]["response"]["visible_content"]
        assert "assignment_completion" in stored["phases"]["safety"]["response"]["visible_content"]


def test_uncertain_model_call_not_retried_and_pending_crash_blocks_new_calls(client, app, monkeypatch):
    env = environment(client, app, monkeypatch)
    calls = []
    def failed(*args):
        calls.append(args)
        raise provider.ProviderFailure("Synthetic uncertain transport")
    monkeypatch.setattr(app.state, "classify", failed)
    first = ask(client, env).json()
    assert first["status"] == "withheld" and first["answer"] is None
    assert ask(client, env).json() == first and len(calls) == 1
    def crash(*args):
        raise RuntimeError("Process stopped after send mark")
    monkeypatch.setattr(app.state, "classify", crash)
    with pytest.raises(RuntimeError, match="Process stopped"):
        ask(client, env, idempotency_key="crashed-send-123")
    pending = ask(client, env, idempotency_key="crashed-send-123").json()
    assert pending["status"] == "pending" and pending["resume_available"] is False
    assert ask(client, env, idempotency_key="new-after-crash-123").status_code == 409


def test_model_gate_approval_and_credential_rotation_revoke_new_and_historical_replies(client, app, monkeypatch):
    env = environment(client, app, monkeypatch)
    value = ask(client, env).json()
    assert value["status"] == "released"
    app.state.settings.allow_live_models = False
    assert client.get(f"/api/homework-help/ai/sessions/{env['session']['id']}").json()["messages"][0]["status"] == "withheld"
    blocked = ask(client, env, idempotency_key="gate-disabled-123").json()
    assert blocked["status"] == "blocked" and len(env["calls"]) == 2
    app.state.settings.allow_live_models = True
    admin = login(client, "demo-admin")
    connection = env["connection"]
    changed = client.put("/api/admin/connections/" + connection["id"], json={**{key: connection[key] for key in ("name", "protocol", "endpoint", "models", "enabled", "version")}, "api_key": "rotated-synthetic-secret"}, headers=admin)
    assert changed.status_code == 200, changed.text
    descriptor = client.get("/api/homework-help/configuration?school_id=" + env["school"]).json()
    assert not descriptor["approved"] and not descriptor["available"]
    env["headers"] = login(client)
    assert ask(client, env, idempotency_key="rotated-model-123").json()["status"] == "blocked"


def test_revoke_during_triage_withholds_before_tutor_send(client, app, monkeypatch):
    env = environment(client, app, monkeypatch)
    def changed(*args):
        response = env["transport"](*args)
        if args[2] == "triage":
            app.state.settings.allow_live_models = False
        return response
    monkeypatch.setattr(app.state, "classify", changed)
    value = ask(client, env).json()
    assert value["status"] == "withheld" and value["answer"] is None
    assert len(env["calls"]) == 1


def test_terminal_audit_commit_failure_never_releases_or_resends(client, app, monkeypatch):
    env = environment(client, app, monkeypatch)
    def fail(mapper, connection, target):
        if target.event_type == "homework_ai.released":
            raise RuntimeError("Synthetic terminal audit failure")
    event.listen(Audit, "before_insert", fail)
    try:
        with pytest.raises(RuntimeError, match="Synthetic terminal"):
            ask(client, env)
    finally:
        event.remove(Audit, "before_insert", fail)
    recovered = ask(client, env).json()
    assert recovered["status"] == "pending" and recovered["answer"] is None and len(env["calls"]) == 2


@pytest.mark.parametrize("user", ["demo-instructor", "demo-school", "demo-admin", "demo-student2"])
def test_sessions_private_to_student_and_reviews_only_human_school_admin(client, app, monkeypatch, user):
    env = environment(client, app, monkeypatch)
    ask(client, env)
    env["headers"] = login(client, user)
    assert client.get(f"/api/homework-help/ai/sessions/{env['session']['id']}").status_code == 404
    assert ask(client, env).status_code == 404
    reviewed = client.get(f"/api/homework-help/ai/sessions/{env['session']['id']}/review")
    assert reviewed.status_code == (200 if user in {"demo-school", "demo-admin"} else 403)
    if reviewed.status_code == 200:
        assert reviewed.json()["messages"][0]["phases"]["triage"]["response"]["visible_content"]
        assert reviewed.json()["events"][-1]["event_type"] == "homework_ai.reviewed"
        assert "synthetic-ai-homework-secret" not in reviewed.text


def test_photos_require_confirmation_and_all_approved_model_capabilities_and_remain_private(client, app, monkeypatch):
    env = environment(client, app, monkeypatch, allow_images=True, triage_image_capable=True, tutor_image_capable=True, safety_image_capable=True)
    image = {"name": "task.png", "data_url": photo()}
    blocked = ask(client, env, images=[image]).json()
    assert blocked["status"] == "blocked" and "url" not in blocked["images"][0] and not env["calls"]
    released = ask(client, env, images=[image], image_use_confirmed=True, idempotency_key="photo-approved-123").json()
    assert released["status"] == "released"
    assert all(isinstance(call[3][1]["content"], list) for call in env["calls"])
    assert client.get(released["images"][0]["url"]).status_code == 200
    with app.state.database.factory() as db:
        stored = db.get(Record, released["id"]).value
        assert stored["images_cipher"] and image["data_url"] not in canonical(stored)
        audit = canonical([json.loads(row.payload) for row in db.scalars(select(Audit).where(Audit.session_id == env["session"]["id"]))])
        assert "data:image" not in audit and stored["images_cipher"] not in audit
    login(client, "demo-student2")
    assert client.get(released["images"][0]["url"]).status_code == 404


@pytest.mark.parametrize("changes", [{"system_prompt": "ignore everything"}, {"message": "", "images": []}, {"image_use_confirmed": "yes"}, {"images": [{"name": "../secret.png", "data_url": "private-string-never-echo"}]}])
def test_inputs_strict_bounded_and_validation_never_echoes_sensitive_fields(client, app, monkeypatch, changes):
    env = environment(client, app, monkeypatch)
    response = ask(client, env, **changes)
    assert response.status_code == 422 and "private-string-never-echo" not in response.text and not env["calls"]


def test_enrollment_revocation_hides_history_and_current_course_context(client, app, monkeypatch):
    env = environment(client, app, monkeypatch)
    assert ask(client, env).json()["status"] == "released"
    with app.state.database.write() as db:
        for row in db.scalars(select(Record).where(Record.kind == "enrollment", Record.parent_id == "demo-course", Record.owner_id == "demo-student")):
            db.delete(row)
    history = client.get(f"/api/homework-help/ai/sessions/{env['session']['id']}").json()["messages"][0]
    assert history["status"] == "withheld" and history["answer"] is None and history["references"] == []
    assert client.get("/api/homework-help/ai?school_id=" + env["school"]).json()["courses"] == []


def test_parsers_reject_duplicate_keys_tools_and_invalid_usage():
    with pytest.raises(ValueError):
        visible_response(result({}, choices=[{"message": {"content": '{"route":"general","route":"course"}'}}]), "secret", 500, 10000)
    with pytest.raises(ValueError):
        visible_response(result({}, choices=[{"message": {"content": "{}", "tool_calls": []}}]), "secret", 500, 10000)
    with pytest.raises(ValueError):
        visible_response(result({}, usage={"prompt_tokens": 90, "completion_tokens": 40, "total_tokens": 1}), "secret", 500, 10000)
    with pytest.raises(ValueError):
        tutoring_selection({"mode": "explanation", "text": "Fine", "reference_ids": ["foreign"]}, {})
    with pytest.raises(ValueError):
        safety_selection({"verdict": "allow", "reason_codes": ["unsafe"]})


def test_class_teacher_review_contains_only_selected_class_interactions_without_other_course_context(client, app, monkeypatch):
    env = environment(client, app, monkeypatch)
    first = ask(client, env).json()
    def general(*args):
        response = env["transport"](*args)
        if args[2] == "triage":
            return result(triage_result(response={"mode": "explanation", "text": "Gravity is an attraction between masses. What do you already know about it?", "reference_ids": []}))
        if args[2] == "tutor":
            return result({"mode": "explanation", "text": "Private unrelated question answer.", "reference_ids": []})
        return response
    monkeypatch.setattr(app.state, "classify", general)
    unrelated = ask(client, env, message="Private unrelated question", idempotency_key="other-context-123").json()
    assert unrelated["status"] == "released"
    login(client, "demo-instructor")
    catalog = client.get("/api/courses/demo-course/homework-help/review-sessions")
    assert catalog.status_code == 200 and catalog.json()[0]["message_count"] == 1
    review = client.get(f"/api/courses/demo-course/homework-help/sessions/{env['session']['id']}/review")
    assert review.status_code == 200, review.text
    assert [item["id"] for item in review.json()["messages"]] == [first["id"]]
    assert "Private unrelated" not in review.text
    assert "configuration_snapshot" not in review.text and "candidates" not in review.text and review.json()["events"] == []
    with app.state.database.write() as db:
        course = db.get(Record, "demo-course")
        course.owner_id = "other-teacher"
        for row in db.scalars(select(Record).where(Record.kind == "enrollment", Record.owner_id == "demo-instructor", Record.parent_id == "demo-course")):
            db.delete(row)
    assert client.get(f"/api/courses/demo-course/homework-help/sessions/{env['session']['id']}/review").status_code == 403


def test_approval_and_terminal_evidence_are_immutable(client, app, monkeypatch):
    env = environment(client, app, monkeypatch)
    reply = ask(client, env).json()
    with pytest.raises(ValueError, match="immutable"):
        with app.state.database.write() as db:
            row = db.get(Record, reply["id"])
            row.set({**row.value, "answer": "Changed after release"})
    with pytest.raises(ValueError, match="immutable"):
        with app.state.database.write() as db:
            row = db.get(Record, env["configuration"]["revision_id"])
            db.delete(row)


def test_safety_requires_exact_current_approval_before_release(client, app, monkeypatch):
    env = environment(client, app, monkeypatch)
    def revoke(*args):
        response = env["transport"](*args)
        if args[2] == "safety":
            with app.state.database.write() as db:
                from academy.homework_ai import config_id
                row = db.get(Record, config_id(app.state.settings.tenant_id, env["school"]))
                row.set({**row.value, "approval_id": None})
        return response
    monkeypatch.setattr(app.state, "classify", revoke)
    reply = ask(client, env).json()
    assert reply["status"] == "withheld" and reply["answer"] is None and len(env["calls"]) == 2


def test_platform_local_student_transcripts_prompts_send_evidence_and_audits_are_encrypted(client, app, monkeypatch):
    from sqlalchemy import text
    from academy.learning_preferences import NEUTRAL
    from academy.pii_storage import ENVELOPE
    from test_local_pii import create_student, student_login
    env = environment(client, app, monkeypatch)
    student = create_student(client, app)
    monkeypatch.setattr(app.state.auth, "core", lambda *args, **kwargs: pytest.fail("Local family student must not contact Core"))
    env["headers"] = student_login(client)
    intake = client.get("/api/me/learning-preferences").json()
    assert client.put("/api/me/learning-preferences", json={"answers": NEUTRAL, "expected_version": intake["version"]}, headers=env["headers"]).status_code == 200
    env["session"] = client.post("/api/homework-help/ai/sessions", json={"school_id": env["school"], "idempotency_key": "local-private-session-123"}, headers=env["headers"]).json()
    question = "Synthetic private homework question about evidence at my school garden."
    reply = ask(client, env, message=question).json()
    assert reply["status"] == "released", reply
    feedback = client.post("/api/me/feedback", json={"turn_id": reply["id"], "accuracy": 2, "helpfulness": 4, "style_preferences": {"encouragement": 4}}, headers=env["headers"])
    assert feedback.status_code == 200, feedback.text
    assert feedback.json()["review_required"]
    with app.state.database.factory() as db:
        raw = " ".join(db.scalars(text("SELECT data FROM records"))) + " ".join(db.scalars(text("SELECT payload FROM audit_events")))
        assert question not in raw and reply["answer"] not in raw
        owned = list(db.scalars(select(Record).where(Record.owner_id == student["id"], Record.kind.in_(["homework_ai_session", "homework_ai_message", "homework_ai_send"]))))
        assert len(owned) == 4 and all(ENVELOPE in json.loads(row.data) for row in owned)
        assert "synthetic-ai-question-123" not in db.get(Record, reply["id"]).data
        events = list(db.scalars(select(Audit).where(Audit.session_id == env["session"]["id"])))
        assert all(ENVELOPE in json.loads(row.payload) for row in events)
    login(client, "demo-admin")
    replay = client.get(f"/api/homework-help/ai/sessions/{env['session']['id']}/review")
    assert replay.status_code == 200 and replay.json()["messages"][0]["answer"] == reply["answer"]
    assert any(row.get("private_payload", {}).get("answer") == reply["answer"] for row in replay.json()["events"])
    assert client.get("/api/audit/verify").json()["valid"]


def test_catalog_pagination_keeps_every_old_conversation_discoverable(client, app, monkeypatch):
    from academy.permissions import bind_scope
    env = environment(client, app, monkeypatch)
    with app.state.database.write() as db:
        for i in range(105):
            stamp = f"2026-10-{i // 24 + 1:02d}T{i % 24:02d}:00:00+00:00"
            row = Record(id=f"{i:064x}", tenant_id=app.state.settings.tenant_id, kind="homework_ai_session", parent_id=env["school"], owner_id="demo-student", created_at=stamp, data=canonical({"school_id": env["school"], "created_at": stamp, "idempotency_key": f"old-session-{i}"}))
            db.add(row)
            db.flush()
            bind_scope(db, row, env["school"])
    first = client.get("/api/homework-help/ai?school_id=" + env["school"]).json()
    assert len(first["sessions"]) == 100 and first["next_cursor"]
    second = client.get("/api/homework-help/ai?school_id=" + env["school"] + "&cursor=" + first["next_cursor"]).json()
    assert len(second["sessions"]) == 6 and second["next_cursor"] is None
    assert len({item["id"] for item in first["sessions"] + second["sessions"]}) == 106
    login(client, "demo-admin")
    page = client.get("/api/homework-help/ai/review-sessions?school_id=" + env["school"]).json()
    assert len(page["sessions"]) == 100 and page["next_cursor"]
    next_page = client.get("/api/homework-help/ai/review-sessions?school_id=" + env["school"] + "&cursor=" + page["next_cursor"]).json()
    assert len(next_page["sessions"]) == 6
    assert client.get("/api/homework-help/ai/review-sessions?school_id=" + env["school"] + "&cursor=invalid").status_code == 422


def test_general_choice_uses_same_assistant_and_auto_followup_keeps_authorized_last_course_hint(client, app, monkeypatch):
    env = environment(client, app, monkeypatch)
    first = ask(client, env).json()
    assert first["status"] == "released"
    second = ask(client, env, message="Why?", idempotency_key="auto-followup-why-123").json()
    assert second["status"] == "released"
    route_prompt = json.loads([call for call in env["calls"] if call[2] == "triage"][-1][3][1]["content"])
    assert route_prompt["previous_messages"][-1]["course_id"] == "demo-course"
    assert route_prompt["previous_messages"][-1]["assistant"] == first["answer"]
    def general(*args):
        env["calls"].append(args)
        return result(triage_result(response={"mode": "explanation", "text": "General learning can cover many topics. What would you like to explore?", "reference_ids": []})) if args[2] == "triage" else result({"verdict": "allow", "reason_codes": []})
    monkeypatch.setattr(app.state, "classify", general)
    before = len(env["calls"])
    third = ask(client, env, message="Something unrelated", routing_mode="general", idempotency_key="chosen-general-123").json()
    assert third["status"] == "released" and third["triage"]["method"] == "student_general"
    assert [call[2] for call in env["calls"][before:]] == ["triage", "safety"]
    prompt = json.loads(env["calls"][before][3][1]["content"])
    assert prompt["previous_messages"] == []
    assert ask(client, env, course_id="demo-course", routing_mode="general", idempotency_key="conflicting-mode-123").status_code == 422


def test_optional_grounding_includes_only_current_approved_public_teaching_passages(client, app, monkeypatch):
    from test_lesson_tutoring import lesson_environment
    env = environment(client, app, monkeypatch)
    teacher = lesson_environment(client, app, monkeypatch)
    env["headers"] = teacher["headers"]
    calls = []
    def grounded(*args):
        calls.append(args)
        if args[2] == "triage":
            return result(triage_result(course_id=teacher["course"]["id"], teacher_profile_id=teacher["profile"]["id"]))
        if args[2] == "tutor":
            payload = json.loads(args[3][1]["content"])
            assert payload["reference_passages"]
            return result({"mode": "explanation", "text": "Carbon moves among living things and their environment. Which transfer are you studying?", "reference_ids": [payload["reference_passages"][0]["reference_id"]]})
        return result({"verdict": "allow", "reason_codes": []})
    monkeypatch.setattr(app.state, "classify", grounded)
    reply = ask(client, env, message="How does carbon move through ecosystems?", course_id=teacher["course"]["id"]).json()
    assert reply["status"] == "released", reply
    assert len(reply["citations"]) == 1 and reply["citations"][0]["content_sha256"]
    assert reply["citations"][0]["corpus_id"]
    tutor_payload = json.loads(next(call for call in calls if call[2] == "tutor")[3][1]["content"])
    assert all(item["excerpt"] and item["reference_id"].startswith("document:") for item in tutor_payload["reference_passages"])
    admin = login(client, "demo-admin")
    resource = teacher["resource"]
    changed = client.put("/api/resources/" + resource["id"], json={"name": "Changed lesson evidence", "content": "Current evidence changed, requiring fresh corpus approval.", "audience": "all", "course_id": teacher["course"]["id"]}, headers=admin)
    assert changed.status_code == 200, changed.text
    env["headers"] = login(client)
    history = client.get(f"/api/homework-help/ai/sessions/{env['session']['id']}").json()["messages"][0]
    assert history["status"] == "withheld" and not history["citations"]


def test_session_creation_is_bounded_per_student_across_schools(client, app, monkeypatch):
    from academy.permissions import bind_scope
    from academy.db import now
    env = environment(client, app, monkeypatch)
    with app.state.database.write() as db:
        for i in range(99):
            stamp = now()
            row = Record(id=f"{1000 + i:064x}", tenant_id=app.state.settings.tenant_id, kind="homework_ai_session", parent_id=env["school"], owner_id="demo-student", created_at=stamp, data=canonical({"school_id": env["school"], "created_at": stamp, "idempotency_key": f"daily-session-{i}"}))
            db.add(row)
            db.flush()
            bind_scope(db, row, env["school"])
    response = client.post("/api/homework-help/ai/sessions", json={"school_id": env["school"], "idempotency_key": "new-rate-limited-session"}, headers=env["headers"])
    assert response.status_code == 429
    # Reconciliation of an existing key is not another creation.
    existing = client.post("/api/homework-help/ai/sessions", json={"school_id": env["school"], "idempotency_key": env["session"]["idempotency_key"]}, headers=env["headers"])
    assert existing.status_code == 200 and existing.json()["id"] == env["session"]["id"]


def test_grounding_filters_teacher_only_and_standards_alignment_even_in_retrieval_results(monkeypatch):
    import academy.homework_ai as ai
    from types import SimpleNamespace
    docs = [
        {"id": "public", "kind": "source_reference", "audience": "all", "title": "Approved reading", "content": "A public teaching passage", "content_sha256": "a" * 64},
        {"id": "private", "kind": "instructional_supplement", "audience": "teachers", "title": "Private answer key", "content": "Private teacher-only sentinel", "content_sha256": "b" * 64},
        {"id": "standard", "kind": "standard_alignment", "audience": "all", "title": "Standard scope", "content": "A standard is not a teaching passage", "content_sha256": "c" * 64},
    ]
    monkeypatch.setattr(ai, "course_for_student", lambda *args: (SimpleNamespace(id="course"), "school"))
    monkeypatch.setattr(ai, "corpus_descriptor", lambda *args: {"ready_for_teaching": True, "id": "corpus", "snapshot_sha256": "d" * 64, "documents": docs})
    monkeypatch.setattr(ai, "retrieve_corpus", lambda *args: {"matches": [{"id": doc["id"], "content_sha256": doc["content_sha256"], "excerpt": doc["content"], "excerpt_start": 0} for doc in docs]})
    passages, snapshot = ai.optional_grounding(None, {}, None, "course", "question", None, "session")
    assert [item["document_id"] for item in passages] == ["public"]
    assert snapshot["corpus_id"] == "corpus"
    assert "Private teacher-only sentinel" not in canonical(passages)


def test_daily_budget_denial_has_no_provider_send_marker(client, app, monkeypatch):
    env = environment(client, app, monkeypatch, max_daily_tokens=1000, max_daily_turns=1)
    reply = ask(client, env).json()
    assert reply["status"] == "withheld" and not env["calls"]
    with app.state.database.factory() as db:
        value = db.get(Record, reply["id"]).value
        assert value["phases"] == {} and value["withholding_code"] == "budget_exceeded"
        assert value["uncertainty"] == "No model send was marked"
    assert ask(client, env, idempotency_key="another-daily-question-123").json()["status"] == "blocked"


def crashed_question(client, app, monkeypatch, env):
    calls = []
    def crash(*args):
        calls.append(args)
        raise RuntimeError("Synthetic process lost after durable send mark")
    monkeypatch.setattr(app.state, "classify", crash)
    with pytest.raises(RuntimeError, match="Synthetic process lost"):
        ask(client, env)
    pending = ask(client, env).json()
    assert pending["status"] == "pending" and len(calls) == 1
    return pending, calls


def close_body(review, message_id, reason="Operator reviewed the durable marker and closed the abandoned request."):
    item = next(item for item in review["messages"] if item["id"] == message_id)
    return {"expected_sha256": item["reconciliation"]["expected_sha256"], "reason": reason}


def close_path(env, message_id):
    return f"/api/homework-help/ai/sessions/{env['session']['id']}/messages/{message_id}/close-uncertain"


def test_operator_closure_unblocks_new_questions_preserves_markers_and_never_retries_original(client, app, monkeypatch):
    env = environment(client, app, monkeypatch)
    pending, calls = crashed_question(client, app, monkeypatch, env)
    assert ask(client, env, idempotency_key="blocked-until-reconcile").status_code == 409
    admin = login(client, "demo-admin")
    review = client.get(f"/api/homework-help/ai/sessions/{env['session']['id']}/review").json()
    command = close_body(review, pending["id"])
    closed = client.post(close_path(env, pending["id"]), json=command, headers=admin)
    assert closed.status_code == 200, closed.text
    assert closed.json()["status"] == "withheld" and not closed.json()["already_closed"]
    assert "may be billable" in closed.json()["uncertainty"] and len(calls) == 1
    repeat = client.post(close_path(env, pending["id"]), json=command, headers=admin)
    assert repeat.status_code == 200 and repeat.json()["already_closed"]
    assert repeat.json()["closed_at"] == closed.json()["closed_at"] and len(calls) == 1
    assert client.post(close_path(env, pending["id"]), json={**command, "reason": "Different attempted reconciliation"}, headers=admin).status_code == 409
    with app.state.database.factory() as db:
        saved = db.get(Record, pending["id"]).value
        assert saved["operator_closure"]["expected_sha256"] == command["expected_sha256"]
        assert saved["phases"]["triage"]["send_marker"] and saved["reservation"] > 0
        markers = list(db.scalars(select(Record).where(Record.kind == "homework_ai_send", Record.parent_id == pending["id"])))
        assert len(markers) == 1
        events = list(db.scalars(select(Audit).where(Audit.session_id == env["session"]["id"], Audit.event_type == "homework_ai.withheld")))
        assert len(events) == 1 and json.loads(events[0].payload)["operator_closure"]["reason"] == command["reason"]
    env["headers"] = login(client)
    replay = ask(client, env).json()
    assert replay["status"] == "withheld" and replay["answer"] is None and len(calls) == 1
    monkeypatch.setattr(app.state, "classify", env["transport"])
    new = ask(client, env, idempotency_key="new-after-reconciliation").json()
    assert new["status"] == "released"


@pytest.mark.parametrize("user,allowed", [("demo-student", False), ("demo-student2", False), ("demo-instructor", False), ("demo-school", True)])
def test_operator_closure_requires_human_school_admin(client, app, monkeypatch, user, allowed):
    from academy.homework_ai import digest
    env = environment(client, app, monkeypatch)
    pending, calls = crashed_question(client, app, monkeypatch, env)
    with app.state.database.factory() as db:
        expected = digest(db.get(Record, pending["id"]).value)
    headers = login(client, user)
    response = client.post(close_path(env, pending["id"]), json={"expected_sha256": expected, "reason": "Reviewed abandoned question and its send marker."}, headers=headers)
    assert response.status_code == (200 if allowed else 403), response.text
    assert len(calls) == 1


def test_operator_closure_rejects_stale_hash_foreign_message_and_cross_school(client, app, monkeypatch):
    from academy.permissions import bind_scope
    from academy.homework_ai import digest
    env = environment(client, app, monkeypatch)
    pending, calls = crashed_question(client, app, monkeypatch, env)
    admin = login(client, "demo-admin")
    review = client.get(f"/api/homework-help/ai/sessions/{env['session']['id']}/review").json()
    command = close_body(review, pending["id"])
    assert client.post(close_path(env, pending["id"]), json={**command, "expected_sha256": "0" * 64}, headers=admin).status_code == 409
    assert client.post(close_path(env, "f" * 64), json=command, headers=admin).status_code == 404
    with app.state.database.write() as db:
        other_school = Record(id="other-school-reconcile", tenant_id=app.state.settings.tenant_id, kind="school", data=canonical({"name": "Unassigned school"}))
        db.add(other_school)
        db.flush()
        conversation = Record(id="a" * 64, tenant_id=app.state.settings.tenant_id, kind="homework_ai_session", parent_id=other_school.id, owner_id="demo-student", data=canonical({"school_id": other_school.id, "created_at": "2026-10-20T00:00:00+00:00", "idempotency_key": "other-school-conversation"}))
        db.add(conversation)
        db.flush()
        bind_scope(db, conversation, other_school.id)
        data = {**db.get(Record, pending["id"]).value, "session_id": conversation.id, "school_id": other_school.id}
        row = Record(id="b" * 64, tenant_id=app.state.settings.tenant_id, kind="homework_ai_message", parent_id=conversation.id, owner_id="demo-student", data=canonical(data))
        db.add(row)
        db.flush()
        bind_scope(db, row, other_school.id)
        expected = digest(data)
    school_headers = login(client, "demo-school")
    path = f"/api/homework-help/ai/sessions/{'a' * 64}/messages/{'b' * 64}/close-uncertain"
    assert client.post(path, json={"expected_sha256": expected, "reason": command["reason"]}, headers=school_headers).status_code == 403
    assert len(calls) == 1


@pytest.mark.parametrize("phase", ["triage", "safety"])
def test_operator_closure_during_model_call_seals_message_and_retains_late_result_without_release(client, app, monkeypatch, phase):
    from fastapi.testclient import TestClient
    env = environment(client, app, monkeypatch)
    with TestClient(app, base_url=app.state.settings.app_url) as operator:
        admin = login(operator, "demo-admin")
        closed = []
        def close_during_call(*args):
            response = env["transport"](*args)
            if args[2] == phase:
                review = operator.get(f"/api/homework-help/ai/sessions/{env['session']['id']}/review").json()
                pending = next(item for item in review["messages"] if item["status"] == "pending")
                command = close_body(review, pending["id"])
                closed_response = operator.post(close_path(env, pending["id"]), json=command, headers=admin)
                assert closed_response.status_code == 200, closed_response.text
                closed.append(closed_response.json())
            return response
        monkeypatch.setattr(app.state, "classify", close_during_call)
        reply = ask(client, env).json()
        assert closed and reply["status"] == "withheld" and reply["answer"] is None
        assert len(env["calls"]) == ["triage", "safety"].index(phase) + 1
        review = operator.get(f"/api/homework-help/ai/sessions/{env['session']['id']}/review").json()
        retained = review["messages"][0]
        assert retained["withholding_code"] == "operator_closed_uncertain"
        assert retained["phases"][phase]["response"] is None
        assert len(retained["late_provider_receipts"]) == 1
        assert retained["late_provider_receipts"][0]["phase"] == phase
        assert retained["late_provider_receipts"][0]["response"]["visible_content"]
        assert any(event.get("payload", {}).get("late_after_terminal") for event in review["events"])
        assert ask(client, env).json()["status"] == "withheld"
        assert len(env["calls"]) == ["triage", "safety"].index(phase) + 1


def test_operator_closure_during_failed_model_call_retains_late_uncertainty(client, app, monkeypatch):
    from fastapi.testclient import TestClient
    env = environment(client, app, monkeypatch)
    with TestClient(app, base_url=app.state.settings.app_url) as operator:
        admin = login(operator, "demo-admin")
        def close_then_fail(*args):
            review = operator.get(f"/api/homework-help/ai/sessions/{env['session']['id']}/review").json()
            pending = review["messages"][0]
            command = close_body(review, pending["id"])
            assert operator.post(close_path(env, pending["id"]), json=command, headers=admin).status_code == 200
            raise provider.ProviderFailure("Synthetic network outcome remained uncertain")
        monkeypatch.setattr(app.state, "classify", close_then_fail)
        reply = ask(client, env).json()
        assert reply["status"] == "withheld" and reply["answer"] is None
        review = operator.get(f"/api/homework-help/ai/sessions/{env['session']['id']}/review").json()
        assert review["messages"][0]["late_provider_receipts"][0]["error_code"] == "provider_outcome_uncertain"
