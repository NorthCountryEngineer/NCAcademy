"""Only a typed public educational task may reach OpenAI for this mode."""
import base64
import hashlib
import json

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select

from academy.db import Audit, Record
from academy.homework_ai import Configuration, BOUNDARY_VERSION
from academy.homework_deidentification import build_public_homework_prompt
from conftest import login
from test_curated_lessons import photo
from test_family_accounts import password_login, student
from test_homework_ai import environment, result, triage_result
from test_private_homework_help import BASE, conversation, enroll


def setup_public(client, app, monkeypatch, **changes):
    env = environment(client, app, monkeypatch, official_models=True, privacy_mode="deidentified",
        allow_local_students=True, use_school_teacher_personas=True,
        biography="PRIVATE_BIOGRAPHY_SENTINEL must never be a model instruction", **changes)
    app.state.settings.openai_student_approvals_json = "{}"
    calls = []
    def classify(connection, key, model, prompt, maximum):
        contract = connection["public_homework_contract"]
        assert prompt == build_public_homework_prompt(**contract)
        assert not {"safety_identifier", "openai_project", "openai_organization", "openai_inference_key_sha256"}.intersection(connection)
        payload = json.loads(prompt[1]["content"][0]["text"])
        calls.append({"model": model, "prompt": prompt, "payload": payload, "contract": contract})
        if model == "safety":
            return result({"verdict": "allow", "reason_codes": [], "input_risk_codes": []})
        answers = [
            "Make a model using circles for equal groups. What would each circle stand for?",
            "Move one counter into each circle in turn, keeping every circle equal. What stays the same after each round?",
            "Think of multiplication as the inverse of equal sharing. Which part of the drawing represents the divisor?",
        ]
        answer = answers[payload["teaching_stage"] % len(answers)]
        return result(triage_result(response={"mode": "guided_practice", "text": answer, "reference_ids": []}))
    monkeypatch.setattr(app.state, "classify", classify)
    env["public_calls"] = calls
    return env


def send(child, headers, session_id, message, key, **changes):
    return child.post(f"{BASE}/sessions/{session_id}/messages", json={"message": message,
        "idempotency_key": key, **changes}, headers=headers)


def create_child(client, app, env, *, username="sample.student", **changes):
    admin = login(client, "demo-admin")
    account = student(client, app, admin, username=username, course_ids=[], grade="", date_of_birth=None, **changes)
    child = TestClient(app, base_url=app.state.settings.app_url)
    headers = password_login(child, username)
    enroll(child, headers)
    session_id = conversation(child, headers, env["school"]).json()["id"]
    return child, headers, session_id, account


def test_default_is_deidentified_and_local_no_enrollment_math_needs_no_zdr_proof(client, app, monkeypatch):
    env = setup_public(client, app, monkeypatch)
    assert Configuration.model_fields["privacy_mode"].default == "deidentified"
    assert BOUNDARY_VERSION == "homework-ai-v4"
    child, headers, session_id, account = create_child(client, app, env)
    try:
        index = child.get(BASE, params={"school_id": env["school"]}).json()
        assert index["courses"] == [] and index["assistant"]["available"]
        assert index["assistant"]["privacy_mode"] == "deidentified" and index["assistant"]["local_only_images"]
        assert not index["assistant"]["openai_student_controls"]["required"]
        first = send(child, headers, session_id,
            "My name is PRIVATE_NAME_SENTINEL. Please explain 21 ÷ 3.", "public-math-first-question")
        assert first.status_code == 200 and first.json()["status"] == "released", first.text
        assert first.json()["deidentification"]["task_summary"] == "21 ÷ 3"
        assert len(env["public_calls"]) == 2
        assert [entry["model"] for entry in env["public_calls"]] == ["triage", "safety"]
        for entry in env["public_calls"]:
            assert entry["contract"]["include_image"] is False
            assert all(part["type"] == "text" for part in entry["prompt"][1]["content"])
            wire = json.dumps(entry["prompt"])
            assert "PRIVATE_NAME_SENTINEL" not in wire and "PRIVATE_BIOGRAPHY_SENTINEL" not in wire
            assert account["id"] not in wire and session_id not in wire and env["school"] not in wire
            assert entry["payload"]["task"] == {"kind": "arithmetic", "operation": "division", "values": [21, 3]}
            assert not {"question", "previous_messages", "student_learning_weights", "courses", "teachers", "profile"}.intersection(entry["payload"])
        repeated = send(child, headers, session_id,
            "My name is PRIVATE_NAME_SENTINEL. Please explain 21 ÷ 3.", "public-math-first-question")
        assert repeated.json() == first.json() and len(env["public_calls"]) == 2
        with app.state.database.factory() as db:
            row = db.get(Record, first.json()["id"])
            assert "__academy_pii__" in row.data and "PRIVATE_NAME_SENTINEL" not in row.data
            assert "PRIVATE_NAME_SENTINEL" in row.value["message"]
            sends = list(db.scalars(select(Record).where(Record.kind == "homework_ai_send", Record.parent_id == row.id)))
            assert len(sends) == 2 and all("__academy_pii__" in item.data for item in sends)
    finally:
        child.close()


def test_two_distinct_learners_have_identical_public_request_for_same_math(client, app, monkeypatch):
    env = setup_public(client, app, monkeypatch)
    first, headers, session_id, account = create_child(client, app, env)
    second, second_headers, second_session, second_account = create_child(client, app, env, username="other.student",
        name="OTHER_PRIVATE_LEARNER_SENTINEL", preferred_name="OTHER_PRIVATE_NAME")
    try:
        a = send(first, headers, session_id, "FIRST_PRIVATE_CHILD asks how 21 ÷ 3 works", "first-private-child-public-task")
        b = send(second, second_headers, second_session, "OTHER_PRIVATE_CHILD asks how 21 ÷ 3 works", "other-private-child-public-task")
        assert a.json()["status"] == b.json()["status"] == "released", (a.text, b.text)
        assert env["public_calls"][0]["prompt"] == env["public_calls"][2]["prompt"]
        assert env["public_calls"][1]["prompt"] == env["public_calls"][3]["prompt"]
    finally:
        first.close()
        second.close()


def test_original_worksheet_stays_local_and_question7_uses_same_source_but_clean_public_diagram(client, app, monkeypatch):
    from academy import homework_deidentification
    env = setup_public(client, app, monkeypatch, allow_images=True, triage_image_capable=True,
                       tutor_image_capable=True, safety_image_capable=True, max_daily_tokens=1000000)
    monkeypatch.setattr(homework_deidentification, "_read_image", lambda *_:
        "Name: PRIVATE_PHOTO_NAME_SENTINEL\n1. 18 ÷ 3\n7. Share 21 ladybugs equally among 3 students. How many ladybugs does each student get?")
    child, headers, session_id, account = create_child(client, app, env)
    try:
        assistant = child.get(BASE, params={"school_id": env["school"]}).json()["assistant"]
        photo_input = {"name": "PRIVATE_FILENAME_SENTINEL.png", "data_url": photo()}
        first = send(child, headers, session_id, "Help with this worksheet", "local-photo-first-public-turn",
            images=[photo_input], image_use_confirmed=True, expected_image_policy_sha256=assistant["image_policy_sha256"])
        assert first.json()["status"] == "released", first.text
        original_id = first.json()["id"]
        follow = send(child, headers, session_id, "Question 7. Look at the sheet.", "local-photo-question-seven",
            previous_image_message_id=original_id, image_use_confirmed=True,
            expected_image_policy_sha256=assistant["image_policy_sha256"])
        assert follow.json()["status"] == "released", follow.text
        assert follow.json()["deidentification"]["task_summary"] == "21 ÷ 3"
        assert follow.json()["deidentification"]["local_only_images"]
        current_calls = env["public_calls"][-2:]
        original_hash = first.json()["images"][0]["sha256"]
        for entry in env["public_calls"]:
            assert "PRIVATE_PHOTO_NAME_SENTINEL" not in json.dumps(entry["prompt"])
            assert "PRIVATE_FILENAME_SENTINEL" not in json.dumps(entry["prompt"])
            for part in entry["prompt"][1]["content"]:
                if part["type"] == "image_url":
                    raw = base64.b64decode(part["image_url"]["url"].split(",", 1)[1])
                    assert hashlib.sha256(raw).hexdigest() != original_hash
        assert all(entry["payload"]["task"]["values"] == [21, 3] for entry in current_calls)
        later = send(child, headers, session_id, "Why? I tried 7. STUDENT_ATTEMPT_SENTINEL", "local-check-method-only")
        assert later.json()["status"] == "released", later.text
        assert later.json()["answer"] != follow.json()["answer"]
        for entry in env["public_calls"][-2:]:
            assert entry["payload"]["task"]["values"] == [21, 3]
            assert entry["payload"]["guidance_action"] == "check_method"
            assert entry["payload"]["teaching_stage"] == 1
            assert "STUDENT_ATTEMPT_SENTINEL" not in json.dumps(entry["prompt"])
    finally:
        child.close()


@pytest.mark.parametrize("question,status", [("PRIVATE_UNSUPPORTED_TEXT_SENTINEL", "clarify"), ("Someone is hurting me", "blocked")])
def test_unknown_and_high_risk_input_remain_local_with_no_model_fallback(client, app, monkeypatch, question, status):
    env = setup_public(client, app, monkeypatch)
    child, headers, session_id, account = create_child(client, app, env)
    try:
        response = send(child, headers, session_id, question, "strict-local-clarification-only")
        assert response.status_code == 200 and response.json()["status"] == status, response.text
        assert response.json()["deidentification"]["status"] == status
        assert not env["public_calls"] and not response.json()["execution"]["phases"]
        assert question not in (response.json()["answer"] or response.json()["error"] or "")
        with app.state.database.factory() as db:
            reports = [row for row in db.scalars(select(Record).where(Record.kind == "student_agent_feedback", Record.owner_id == account["id"]))
                       if row.value.get("feedback_type") == "homework_safety_report"]
            assert len(reports) == (1 if status == "blocked" else 0)
            assert all("__academy_pii__" in row.data for row in reports)
    finally:
        child.close()


def test_model_cannot_introduce_private_teacher_course_or_citation(client, app, monkeypatch):
    env = setup_public(client, app, monkeypatch)
    child, headers, session_id, account = create_child(client, app, env)
    original = app.state.classify
    def foreign(*args):
        value = original(*args)
        if args[2] == "triage":
            return result(triage_result(course_id="demo-course"))
        return value
    monkeypatch.setattr(app.state, "classify", foreign)
    try:
        response = send(child, headers, session_id, "Explain 21 ÷ 3", "public-task-foreign-route-denied")
        assert response.json()["status"] == "withheld" and len(env["public_calls"]) == 1
        assert response.json()["answer"] is None
    finally:
        child.close()


def test_new_public_content_default_cannot_approve_non_openai_or_bypass_raw_student_controls(client, app, monkeypatch):
    env = setup_public(client, app, monkeypatch)
    admin = login(client, "demo-admin")
    path = "/api/homework-help/configuration?school_id=" + env["school"]
    current = client.get(path).json()
    raw = client.put(path, json={**current["configuration"], "expected_version": current["version"],
        "privacy_mode": "raw_zdr", "local_student_privacy_confirmation": "Synthetic-only reference is not provider approval"}, headers=admin)
    assert raw.status_code == 200 and not raw.json()["approved"]
    refused = client.post("/api/homework-help/configuration/approve?school_id=" + env["school"],
        json={"revision_id": raw.json()["revision_id"]}, headers=admin)
    assert refused.status_code == 403 and not env["public_calls"]
    connection = env["connection"]
    changed = client.put("/api/admin/connections/" + connection["id"], json={
        "version": connection["version"], "name": connection["name"], "endpoint": "https://models.example.test/v1",
        "models": connection["models"], "enabled": True, "api_key": "synthetic-replacement-not-live"}, headers=admin)
    assert changed.status_code == 200, changed.text
    saved = client.put(path, json={**raw.json()["configuration"], "expected_version": raw.json()["version"],
        "privacy_mode": "deidentified", "local_student_privacy_confirmation": ""}, headers=admin)
    assert saved.status_code == 200
    refused = client.post("/api/homework-help/configuration/approve?school_id=" + env["school"],
        json={"revision_id": saved.json()["revision_id"]}, headers=admin)
    assert refused.status_code == 403 and not env["public_calls"]


def test_public_runtime_revocation_withholds_after_single_durable_send_and_never_retries(client, app, monkeypatch):
    env = setup_public(client, app, monkeypatch)
    child, headers, session_id, account = create_child(client, app, env)
    original = app.state.classify
    def revoke(*args):
        response = original(*args)
        app.state.settings.allow_live_models = False
        app.state.settings.allow_homework_models = False
        return response
    monkeypatch.setattr(app.state, "classify", revoke)
    try:
        first = send(child, headers, session_id, "Explain 21 ÷ 3", "public-content-runtime-revocation")
        assert first.status_code == 200 and first.json()["status"] == "withheld", first.text
        assert len(env["public_calls"]) == 1
        repeated = send(child, headers, session_id, "Explain 21 ÷ 3", "public-content-runtime-revocation")
        assert repeated.json() == first.json() and len(env["public_calls"]) == 1
        with app.state.database.factory() as db:
            row = db.get(Record, first.json()["id"])
            assert set(row.value["phases"]) == {"triage"}
            assert "__academy_pii__" in row.data
    finally:
        child.close()


def test_repeating_model_text_is_withheld_instead_of_released_as_the_next_step(client, app, monkeypatch):
    env = setup_public(client, app, monkeypatch)
    child, headers, session_id, account = create_child(client, app, env)
    original = app.state.classify
    fixed = "Draw circles for equal groups. What would each circle represent?"
    def repeating(*args):
        output = original(*args)
        stage = args[0]["public_homework_contract"]["stage"]
        return result(triage_result(response={"mode": "guided_practice", "text": fixed, "reference_ids": []})) if args[2] == "triage" and stage != 1 else output
    monkeypatch.setattr(app.state, "classify", repeating)
    try:
        first = send(child, headers, session_id, "Explain 21 ÷ 3", "public-nonrepeating-initial")
        assert first.json()["status"] == "released"
        followup = send(child, headers, session_id, "Why? I need another explanation", "public-nonrepeating-followup")
        assert followup.json()["status"] == "released" and followup.json()["answer"] != first.json()["answer"]
        repeated = send(child, headers, session_id, "Why? Please explain another way", "public-nonrepeating-older-loop")
        assert repeated.json()["status"] == "withheld" and repeated.json()["answer"] is None
        assert len(env["public_calls"]) == 5
        with app.state.database.factory() as db:
            assert db.get(Record, repeated.json()["id"]).value["withholding_code"] == "repeated_public_explanation"
    finally:
        child.close()


def test_legacy_private_worksheet_and_transcript_are_preserved_when_school_enables_deidentified_models(client, app, monkeypatch):
    from academy import homework_deidentification, private_homework_coach
    from test_homework_local_models import revise
    from test_private_homework_help import enable
    env = setup_public(client, app, monkeypatch, allow_images=True, triage_image_capable=True,
                       tutor_image_capable=True, safety_image_capable=True, max_daily_tokens=1000000)
    child, headers, session_id, account = create_child(client, app, env)
    try:
        admin = login(client, "demo-admin")
        revise(client, admin, env["school"], approve=True, allow_local_students=False)
        assert enable(client, admin, env["school"]).status_code == 200
        monkeypatch.setattr(private_homework_coach, "_read_image", lambda *_: "Division: share twenty-one ladybugs equally among three students.")
        original = send(child, headers, session_id, "Help with division", "legacy-private-worksheet-source",
            images=[{"name": "PRIVATE_LEGACY_SOURCE.png", "data_url": photo()}], image_use_confirmed=True)
        assert original.status_code == 200 and original.json()["private_mode"], original.text
        assert original.json()["status"] == "released" and not env["public_calls"]
        revise(client, admin, env["school"], approve=True, allow_local_students=True)
        old = child.get(f"{BASE}/sessions/{session_id}").json()["messages"][0]
        assert old == original.json()
        monkeypatch.setattr(homework_deidentification, "_read_image", lambda *_:
            "7. Share 21 ladybugs equally among 3 students. How many ladybugs does each student get?")
        assistant = child.get(BASE, params={"school_id": env["school"]}).json()["assistant"]
        response = send(child, headers, session_id, "Question 7. Look at the sheet.", "legacy-source-public-followup",
            previous_image_message_id=original.json()["id"], image_use_confirmed=True,
            expected_image_policy_sha256=assistant["image_policy_sha256"])
        assert response.status_code == 200 and response.json()["status"] == "released", response.text
        assert response.json()["privacy_mode"] == "deidentified"
        assert all(call["payload"]["task"]["values"] == [21, 3] for call in env["public_calls"])
        assert len(env["public_calls"]) == 2
        assert child.get(f"{BASE}/sessions/{session_id}").json()["messages"][0] == original.json()
    finally:
        child.close()
