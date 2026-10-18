"""Homework routing cannot create a new provider or student-content boundary."""
import json

import pytest
from sqlalchemy import select

from academy import provider
from academy.db import Audit, Record, canonical
from conftest import login
from test_curated_lessons import environment, photo
from test_teacher_onboarding import create, assign, approve


def conversation(client, env, key="homework-conversation-123"):
    result = client.post("/api/homework-help/sessions", json={"school_id": env["school"], "idempotency_key": key}, headers=env["headers"])
    assert result.status_code == 200, result.text
    return result.json()


def ask(client, env, session, **changes):
    return client.post(f"/api/homework-help/sessions/{session['id']}/messages", json={"message": "How does carbon move through ecosystems?", "idempotency_key": "homework-message-123", **changes}, headers=env["headers"])


def test_routes_current_course_to_approved_teacher_exact_source_feedback_and_coursework(client, app, monkeypatch):
    env = environment(client, app, monkeypatch)
    env["headers"] = login(client, "demo-admin")
    assignment = client.post(f"/api/courses/{env['course']['id']}/assignments", json={"title": "Carbon ecosystems evidence", "module": "Carbon", "instructions": "Explain your own evidence.", "tutor_policy": {"mode": "off"}}, headers=env["headers"])
    assert assignment.status_code == 201, assignment.text
    env["headers"] = login(client)
    session = conversation(client, env)
    result = ask(client, env, session)
    assert result.status_code == 200, result.text
    value = result.json()
    assert value["status"] == "released" and value["triage"]["course_id"] == env["course"]["id"]
    assert value["triage"]["teacher_profile_id"] == env["profile"]["id"] and len(env["calls"]) == 1
    assert value["turn"]["lesson"]["sections"][0]["text"] in json.loads(env["calls"][0][3][1]["content"])["documents"][0]["excerpt"]
    assert [item["kind"] for item in value["references"]] == ["course", "assignment"]
    assert value["references"][1]["url"] == f"/#course={env['course']['id']}&assignment={assignment.json()['id']}"
    assert ask(client, env, session).json() == value and len(env["calls"]) == 1
    history = client.get(f"/api/homework-help/sessions/{session['id']}").json()
    assert history["messages"] == [value] and history["messages"][0]["idempotency_key"] == "homework-message-123"
    feedback = client.post("/api/me/feedback", json={"turn_id": value["turn"]["id"], "accuracy": None, "helpfulness": 4, "style_preferences": {"encouragement": 4}}, headers=env["headers"])
    assert feedback.status_code == 200, feedback.text
    assert "images_cipher" not in result.text and "synthetic-lesson-key" not in result.text


def test_followup_preserves_teacher_and_bounded_same_course_context(client, app, monkeypatch):
    env = environment(client, app, monkeypatch)
    session = conversation(client, env)
    first = ask(client, env, session).json()
    assert first["status"] == "released"
    second = ask(client, env, session, message="Can you explain that another way?", idempotency_key="followup-homework-123").json()
    assert second["triage"]["method"] == "conversation_course_v1" and second["status"] == "released"
    prompt = json.loads(env["calls"][1][3][1]["content"])["task_context"]
    assert prompt.startswith("Can you explain that another way?") and first["message"] in prompt
    assert second["triage"]["teacher_profile_id"] == first["triage"]["teacher_profile_id"]


def test_photo_only_clarifies_without_model_and_retains_encrypted_private_evidence(client, app, monkeypatch):
    env = environment(client, app, monkeypatch)
    session = conversation(client, env)
    raw = photo()
    value = ask(client, env, session, message="", images=[{"name": "homework.png", "data_url": raw}]).json()
    assert value["status"] == "clarify" and value["triage"]["course_id"] is None and not env["calls"]
    image = client.get(value["images"][0]["url"])
    assert image.status_code == 200 and image.headers["cache-control"] == "private, no-store"
    with app.state.database.factory() as db:
        stored = db.get(Record, value["id"]).value
        assert stored["images_cipher"] and raw not in canonical(stored)
        audit = canonical([json.loads(item.payload) for item in db.scalars(select(Audit).where(Audit.session_id == session["id"]))])
        assert "data:image" not in audit and "images_cipher" not in audit and stored["images_cipher"] not in audit
    login(client, "demo-student2")
    assert client.get(value["images"][0]["url"]).status_code == 404


@pytest.mark.parametrize("confirmed", [True, False])
def test_explicit_photo_handoff_reuses_existing_consent_and_vision_gates(client, app, monkeypatch, confirmed):
    env = environment(client, app, monkeypatch)
    session = conversation(client, env)
    value = ask(client, env, session, course_id=env["course"]["id"], teacher_profile_id=env["profile"]["id"], message="", images=[{"name": "work.png", "data_url": photo()}], image_use_confirmed=confirmed).json()
    assert value["status"] == ("released" if confirmed else "blocked")
    assert len(env["calls"]) == int(confirmed)
    if confirmed:
        assert env["calls"][0][3][1]["content"][1]["image_url"]["url"].startswith("data:image/jpeg;base64,")


def test_ambiguous_courses_ask_then_student_selects_without_cross_course_context(client, app, monkeypatch):
    first = environment(client, app, monkeypatch)
    second = environment(client, app, monkeypatch)
    first["headers"] = second["headers"]
    session = conversation(client, first)
    ambiguous = ask(client, first, session, message="Please help with this topic").json()
    assert ambiguous["status"] == "clarify" and len(ambiguous["triage"]["candidates"]) == 2
    assert first["calls"] == second["calls"] == []
    chosen = ask(client, first, session, course_id=first["course"]["id"], teacher_profile_id=first["profile"]["id"], message="Carbon ecosystems", idempotency_key="chosen-first-course-123").json()
    assert chosen["status"] == "released"
    switched = ask(client, first, session, course_id=second["course"]["id"], teacher_profile_id=second["profile"]["id"], message="Help with this other topic", idempotency_key="chosen-second-course-123").json()
    assert switched["status"] == "released" and switched["triage"]["method"] == "student_choice"
    assert "Earlier student questions" not in json.loads(second["calls"][-1][3][1]["content"])["task_context"]


def test_multiple_approved_teachers_require_explicit_choice(client, app, monkeypatch):
    env = environment(client, app, monkeypatch)
    admin = login(client, "demo-admin")
    profile = create(client, admin, env["school"], kind="ai", name="Other approved teacher", tutor_model=env["profile"]["tutor_model"])
    profile = approve(client, admin, env["school"], assign(client, admin, env["school"], profile, [env["course"]["id"]]))
    env["headers"] = login(client)
    session = conversation(client, env)
    value = ask(client, env, session, course_id=env["course"]["id"]).json()
    assert value["status"] == "clarify" and value["triage"]["teacher_profile_id"] is None
    assert len(value["triage"]["candidates"][0]["teachers"]) == 2 and not env["calls"]
    chosen = ask(client, env, session, course_id=env["course"]["id"], teacher_profile_id=profile["id"], idempotency_key="other-teacher-choice-123").json()
    assert chosen["status"] == "released" and chosen["triage"]["teacher_profile_id"] == profile["id"]


@pytest.mark.parametrize("user", ["demo-instructor", "demo-school", "demo-admin", "demo-student2"])
def test_sessions_remain_student_owned_and_catalog_uses_current_enrollment(client, app, monkeypatch, user):
    env = environment(client, app, monkeypatch)
    session = conversation(client, env)
    value = ask(client, env, session).json()
    env["headers"] = login(client, user)
    assert client.get(f"/api/homework-help/sessions/{session['id']}").status_code == 404
    assert ask(client, env, session).status_code == 404
    index = client.get(f"/api/homework-help?school_id={env['school']}")
    if user == "demo-student2":
        assert index.status_code == 200 and index.json()["sessions"] == []
        assert env["course"]["id"] not in {item["id"] for item in index.json()["courses"]}
    else:
        assert index.status_code == 403
    assert len(env["calls"]) == 1 and value["status"] == "released"


def test_unknown_provider_outcome_is_never_automatically_retried_and_keys_cannot_change(client, app, monkeypatch):
    env = environment(client, app, monkeypatch)
    session = conversation(client, env)
    count = []
    def failed(*args):
        count.append(args)
        raise provider.ProviderFailure("Synthetic uncertain transport")
    monkeypatch.setattr(app.state, "classify", failed)
    first = ask(client, env, session)
    assert first.status_code == 200 and first.json()["status"] == "withheld"
    assert ask(client, env, session).json() == first.json() and len(count) == 1
    assert ask(client, env, session, message="Changed question").status_code == 409
    assert len(count) == 1


def test_enrollment_revocation_withdraws_historical_output_and_references(client, app, monkeypatch):
    env = environment(client, app, monkeypatch)
    session = conversation(client, env)
    value = ask(client, env, session).json()
    assert value["status"] == "released"
    with app.state.database.write() as db:
        for enrolled in db.scalars(select(Record).where(Record.kind == "enrollment", Record.parent_id == env["course"]["id"], Record.owner_id == "demo-student")):
            db.delete(enrolled)
    history = client.get(f"/api/homework-help/sessions/{session['id']}").json()["messages"][0]
    assert history["status"] == "withheld" and history["turn"]["lesson"] is None and history["references"] == []
    assert ask(client, env, session, course_id=env["course"]["id"], idempotency_key="revoked-course-request-123").status_code == 403
    assert len(env["calls"]) == 1


def test_human_school_admin_review_preserves_safe_evidence_and_audits_access(client, app, monkeypatch):
    env = environment(client, app, monkeypatch)
    session = conversation(client, env)
    value = ask(client, env, session, images=[{"name": "work.png", "data_url": photo()}], image_use_confirmed=True).json()
    login(client, "demo-instructor")
    assert client.get("/api/homework-help/review-sessions").status_code == 403
    assert client.get(f"/api/homework-help/sessions/{session['id']}/review").status_code == 403
    login(client, "demo-admin")
    catalog = client.get(f"/api/homework-help/review-sessions?school_id={env['school']}")
    assert catalog.status_code == 200 and catalog.json()[0]["id"] == session["id"]
    review = client.get(f"/api/homework-help/sessions/{session['id']}/review")
    assert review.status_code == 200, review.text
    assert review.json()["messages"][0]["turn"]["id"] == value["turn"]["id"]
    assert "images_cipher" not in review.text and "data:image" not in review.text and "synthetic-lesson-key" not in review.text
    assert review.json()["events"][-1]["event_type"] == "homework.reviewed"
    image = client.get(review.json()["messages"][0]["images"][0]["url"])
    assert image.status_code == 200 and image.headers["cache-control"] == "private, no-store"
    course_events = client.get(f"/api/audit/sessions/{value['turn']['session_id']}").json()["events"]
    assert any(item["event_type"] == "homework.teacher_handoff" for item in course_events)


@pytest.mark.parametrize("changes", [{"message": "", "images": []}, {"system_prompt": "Write answers"}, {"teacher_profile_id": "foreign-teacher"}, {"image_use_confirmed": "yes"}, {"images": [{"name": "../private.png", "data_url": "secret-never-echo"}]}])
def test_bounded_typed_inputs_reject_without_provider_or_sensitive_echo(client, app, monkeypatch, changes):
    env = environment(client, app, monkeypatch)
    result = ask(client, env, conversation(client, env), **changes)
    assert result.status_code == 422 and not env["calls"] and "secret-never-echo" not in result.text


def test_session_reconciliation_is_idempotent_and_completion_requests_are_blocked(client, app, monkeypatch):
    env = environment(client, app, monkeypatch)
    first = conversation(client, env)
    assert conversation(client, env) == first
    index = client.get(f"/api/homework-help?school_id={env['school']}").json()
    assert index["sessions"] == [first] and index["courses"][-1]["available"]
    blocked = ask(client, env, first, message="Write my essay about carbon ecosystems.").json()
    assert blocked["status"] == "blocked" and not env["calls"]
    assert blocked["turn"]["lesson"] is None


def test_lost_homework_receipt_after_durable_lesson_never_resends(client, app, monkeypatch):
    from sqlalchemy import event
    env = environment(client, app, monkeypatch)
    session = conversation(client, env)
    def fail_receipt(mapper, connection, target):
        if target.event_type == "homework.response_recorded":
            raise RuntimeError("Synthetic receipt commit failure")
    event.listen(Audit, "before_insert", fail_receipt)
    try:
        with pytest.raises(RuntimeError, match="Synthetic receipt"):
            ask(client, env, session)
    finally:
        event.remove(Audit, "before_insert", fail_receipt)
    recovered = ask(client, env, session)
    assert recovered.status_code == 200 and recovered.json()["status"] == "released"
    assert len(env["calls"]) == 1
    with app.state.database.factory() as db:
        turns = list(db.scalars(select(Record).where(Record.kind == "lesson_turn", Record.parent_id == env["course"]["id"])))
        assert len(turns) == 1 and turns[0].value["send_marker"]


def test_homework_shares_course_quota_with_existing_lesson_activity(client, app, monkeypatch):
    env = environment(client, app, monkeypatch, max_turns=1)
    session = conversation(client, env)
    assert ask(client, env, session).json()["status"] == "released"
    second_session = conversation(client, env, "second-conversation-123")
    blocked = ask(client, env, second_session, idempotency_key="second-conversation-message-123").json()
    assert blocked["status"] == "blocked" and "Daily course lesson turn limit" in blocked["turn"]["error"]
    assert len(env["calls"]) == 1


def test_changing_teacher_does_not_send_previous_persona_conversation(client, app, monkeypatch):
    env = environment(client, app, monkeypatch)
    admin = login(client, "demo-admin")
    other = create(client, admin, env["school"], kind="ai", name="Another teaching persona", tutor_model=env["profile"]["tutor_model"])
    other = approve(client, admin, env["school"], assign(client, admin, env["school"], other, [env["course"]["id"]]))
    env["headers"] = login(client)
    session = conversation(client, env)
    first = ask(client, env, session, course_id=env["course"]["id"], teacher_profile_id=env["profile"]["id"], message="Carbon ecosystems: my first persona's question").json()
    assert first["status"] == "released"
    changed = ask(client, env, session, course_id=env["course"]["id"], teacher_profile_id=other["id"], message="Explain carbon ecosystems", idempotency_key="changed-persona-homework-123").json()
    assert changed["status"] == "released"
    task = json.loads(env["calls"][-1][3][1]["content"])["task_context"]
    assert "first persona" not in task and "Earlier student questions" not in task
    with app.state.database.factory() as db:
        assert db.get(Record, changed["id"]).value["conversation_context_snapshot"]["messages"] == []


def test_pre_admission_refusal_has_final_unsent_outcome_not_permanent_pending(client, app, monkeypatch):
    from fastapi import HTTPException
    from academy import curated_lesson_api
    env = environment(client, app, monkeypatch)
    session = conversation(client, env)
    def no_access(*args):
        raise HTTPException(403, "Synthetic revoked admission")
    monkeypatch.setattr(curated_lesson_api, "course_for_student", no_access)
    result = ask(client, env, session)
    assert result.status_code == 200, result.text
    value = result.json()
    assert value["status"] == "blocked" and value["turn"] is None and value["resume_available"] is False
    assert "No teacher model was called" in value["error"] and not env["calls"]
    history = client.get(f"/api/homework-help/sessions/{session['id']}").json()["messages"]
    assert history == [value]
    assert client.post(f"/api/homework-help/sessions/{session['id']}/messages/{value['id']}/resume", json={}, headers=env["headers"]).json() == value
    assert not env["calls"]
    with app.state.database.factory() as db:
        outcomes = list(db.scalars(select(Record).where(Record.kind == "homework_outcome")))
        assert len(outcomes) == 1 and outcomes[0].value["model_call_made"] is False
        assert any(item.event_type == "homework.not_sent" for item in db.scalars(select(Audit).where(Audit.session_id == session["id"])))


def test_saved_question_crash_before_admission_can_explicitly_resume_same_lesson_key(client, app, monkeypatch):
    from academy import curated_lesson_api
    env = environment(client, app, monkeypatch)
    session = conversation(client, env)
    original = curated_lesson_api.course_for_student
    def crash(*args):
        raise RuntimeError("Synthetic crash before lesson admission")
    monkeypatch.setattr(curated_lesson_api, "course_for_student", crash)
    with pytest.raises(RuntimeError, match="Synthetic crash"):
        ask(client, env, session, images=[{"name": "homework.png", "data_url": photo()}], image_use_confirmed=True)
    monkeypatch.setattr(curated_lesson_api, "course_for_student", original)
    saved = client.get(f"/api/homework-help/sessions/{session['id']}").json()["messages"][0]
    assert saved["status"] == "pending" and saved["turn"] is None and saved["resume_available"] is True
    with app.state.database.factory() as db:
        original_key = db.get(Record, saved["id"]).value["lesson_key"]
    path = f"/api/homework-help/sessions/{session['id']}/messages/{saved['id']}/resume"
    result = client.post(path, json={}, headers=env["headers"])
    assert result.status_code == 200 and result.json()["status"] == "released" and not result.json()["resume_available"]
    assert len(env["calls"]) == 1 and isinstance(env["calls"][0][3][1]["content"], list)
    assert client.post(path, json={}, headers=env["headers"]).json() == result.json() and len(env["calls"]) == 1
    with app.state.database.factory() as db:
        turns = list(db.scalars(select(Record).where(Record.kind == "lesson_turn", Record.parent_id == env["course"]["id"])))
        assert len(turns) == 1 and turns[0].value["idempotency_key"] == original_key


def test_school_review_catalog_transcript_and_photos_do_not_cross_school(client, app, monkeypatch):
    from test_school_permissions import new_school, grant
    env = environment(client, app, monkeypatch)
    own = conversation(client, env)
    admin = login(client, "demo-admin")
    other = new_school(client, admin)
    grant(client, admin, other, "demo-student", "student")
    env["headers"] = login(client)
    foreign = client.post("/api/homework-help/sessions", json={"school_id": other, "idempotency_key": "other-school-conversation-123"}, headers=env["headers"]).json()
    attachment = ask(client, env, foreign, message="", images=[{"name": "work.png", "data_url": photo()}]).json()
    assert attachment["status"] == "clarify" and not env["calls"]
    login(client, "demo-school")
    listing = client.get("/api/homework-help/review-sessions")
    assert listing.status_code == 200 and [item["id"] for item in listing.json()] == [own["id"]]
    assert client.get(f"/api/homework-help/review-sessions?school_id={other}").status_code == 403
    assert client.get(f"/api/homework-help/sessions/{foreign['id']}/review").status_code == 403
    assert client.get(attachment["images"][0]["url"]).status_code == 404


def test_new_conversation_cannot_bypass_daily_picture_storage_budget_and_retry_is_free(client, app, monkeypatch):
    from academy import homework_api
    env = environment(client, app, monkeypatch)
    first = conversation(client, env)
    picture = {"name": "homework.png", "data_url": photo()}
    saved = ask(client, env, first, message="", images=[picture]).json()
    assert saved["status"] == "clarify" and not env["calls"]
    with app.state.database.factory() as db:
        used = db.get(Record, saved["id"]).value["image_bytes"]
    assert used > 0
    monkeypatch.setattr(homework_api, "MAX_DAILY_IMAGE_BYTES", used)
    second = conversation(client, env, "another-picture-conversation-123")
    denied = ask(client, env, second, message="", images=[picture], idempotency_key="another-picture-message-123")
    assert denied.status_code == 429 and "picture limit" in denied.text
    assert ask(client, env, first, message="", images=[picture]).json() == saved
    assert client.get(f"/api/homework-help/sessions/{second['id']}").json()["messages"] == []
    with app.state.database.factory() as db:
        assert len(list(db.scalars(select(Record).where(Record.kind == "homework_message")))) == 1


def test_normal_history_uses_immutable_exact_turn_receipt_without_scanning_course_history(client, app, monkeypatch):
    from sqlalchemy import event
    env = environment(client, app, monkeypatch)
    session = conversation(client, env)
    value = ask(client, env, session).json()
    with app.state.database.factory() as db:
        receipts = list(db.scalars(select(Record).where(Record.kind == "homework_outcome", Record.parent_id == value["id"])))
        assert len(receipts) == 1 and receipts[0].value["lesson_turn_id"] == value["turn"]["id"]
    queries = []
    def collect(connection, cursor, statement, parameters, context, many):
        if "JSON_EXTRACT" in statement.upper() or "JSON_QUOTE" in statement.upper():
            queries.append(statement)
    event.listen(app.state.database.engine, "before_cursor_execute", collect)
    try:
        history = client.get(f"/api/homework-help/sessions/{session['id']}")
    finally:
        event.remove(app.state.database.engine, "before_cursor_execute", collect)
    assert history.status_code == 200 and history.json()["messages"] == [value]
    assert queries == []


def test_explicit_reconcile_seals_missing_link_receipt_without_provider_resend(client, app, monkeypatch):
    from sqlalchemy import event
    env = environment(client, app, monkeypatch)
    session = conversation(client, env)
    def fail_link(mapper, connection, target):
        if target.event_type == "homework.lesson_linked":
            raise RuntimeError("Synthetic lost lesson-link receipt")
    event.listen(Audit, "before_insert", fail_link)
    try:
        with pytest.raises(RuntimeError, match="lost lesson-link receipt"):
            ask(client, env, session)
    finally:
        event.remove(Audit, "before_insert", fail_link)
    history = client.get(f"/api/homework-help/sessions/{session['id']}").json()["messages"][0]
    assert history["status"] == "released" and not history["resume_available"]
    with app.state.database.factory() as db:
        assert list(db.scalars(select(Record).where(Record.kind == "homework_outcome"))) == []
    reconciled = client.post(f"/api/homework-help/sessions/{session['id']}/messages/{history['id']}/resume", json={}, headers=env["headers"])
    assert reconciled.status_code == 200 and reconciled.json() == history
    assert len(env["calls"]) == 1
    with app.state.database.factory() as db:
        receipts = list(db.scalars(select(Record).where(Record.kind == "homework_outcome")))
        assert len(receipts) == 1 and receipts[0].value["lesson_turn_id"] == history["turn"]["id"]
