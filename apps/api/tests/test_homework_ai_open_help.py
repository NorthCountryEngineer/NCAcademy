"""School-scoped open Homework Help and optional approved persona handoffs."""
import json

import pytest
from sqlalchemy import select

from academy.db import Audit, Record, canonical, uid
from academy.homework_ai import BOUNDARY_VERSION, VERSION, config_id, digest
from academy.permissions import bind_scope, membership_id
from conftest import login
from test_homework_ai import environment, ask, result, triage_result
from test_lesson_tutoring import lesson_environment


def remove_enrollments(app):
    with app.state.database.write() as db:
        for row in db.scalars(select(Record).where(Record.kind == "enrollment", Record.owner_id == "demo-student")):
            db.delete(row)


def test_no_enrollments_can_open_send_reopen_and_audit_general_homework(client, app, monkeypatch):
    env = environment(client, app, monkeypatch)
    remove_enrollments(app)
    index = client.get("/api/homework-help/ai?school_id=" + env["school"]).json()
    assert index["courses"] == [] and index["assistant"]["available"]
    reply = ask(client, env, message="How does gravity work?").json()
    assert reply["status"] == "released", reply
    assert reply["triage"]["course_id"] is None
    assert reply["triage"]["responder"] == "school_assistant" and reply["teacher_profile_id"] is None
    assert [call[2] for call in env["calls"]] == ["triage", "safety"]
    assert reply["references"] == reply["citations"] == []
    assert ask(client, env, message="How does gravity work?").json() == reply
    assert len(env["calls"]) == 2
    assert client.get(f"/api/homework-help/ai/sessions/{env['session']['id']}").json()["messages"] == [reply]
    summaries = client.get("/api/homework-help/ai?school_id=" + env["school"]).json()["sessions"]
    assert summaries[0]["title"] == "How does gravity work?"
    admin = login(client, "demo-admin")
    reviewed = client.get(f"/api/homework-help/ai/sessions/{env['session']['id']}/review")
    assert reviewed.status_code == 200
    assert set(reviewed.json()["messages"][0]["phases"]) == {"triage", "safety"}
    assert client.get("/api/audit/verify").json()["valid"]


def test_general_reply_survives_unrelated_enrollment_changes_but_school_membership_still_required(client, app, monkeypatch):
    env = environment(client, app, monkeypatch)
    reply = ask(client, env, routing_mode="general").json()
    remove_enrollments(app)
    assert client.get(f"/api/homework-help/ai/sessions/{env['session']['id']}").json()["messages"][0]["status"] == "released"
    with app.state.database.write() as db:
        member = db.get(Record, membership_id(env["school"], "demo-student"))
        member.set({**member.value, "active": False})
    assert client.get(f"/api/homework-help/ai/sessions/{env['session']['id']}").status_code == 403
    assert ask(client, env, idempotency_key="revoked-school-question").status_code == 403
    assert len(env["calls"]) == 2


def teacher_transport(monkeypatch, app, env, teacher, *, on_tutor=None):
    calls = []
    def classify(*args):
        calls.append(args)
        if args[2] == "triage":
            return result(triage_result(teacher_profile_id=teacher["profile"]["id"], topic="Ecosystems"))
        if args[2] == "tutor":
            if on_tutor:
                on_tutor()
            return result({"mode": "explanation", "text": "An ecosystem connects living things with their surroundings. Which relationship would you like to explore?", "reference_ids": []})
        return result({"verdict": "allow", "reason_codes": []})
    monkeypatch.setattr(app.state, "classify", classify)
    env["headers"] = teacher["headers"]
    return calls


@pytest.mark.parametrize("bound", [False, True])
def test_approved_school_ai_teacher_can_help_non_enrolled_student_without_private_class_context(client, app, monkeypatch, bound):
    env = environment(client, app, monkeypatch)
    teacher = lesson_environment(client, app, monkeypatch, bound=bound)
    remove_enrollments(app)
    calls = teacher_transport(monkeypatch, app, env, teacher)
    reply = ask(client, env, message="Can you explain ecosystems?").json()
    assert reply["status"] == "released", reply
    assert reply["triage"]["responder"] == "teacher"
    assert reply["teacher_profile_id"] == teacher["profile"]["id"]
    assert reply["triage"]["teacher_name"] == "Course Evidence Teacher"
    assert reply["triage"]["course_id"] is None and not reply["references"] and not reply["citations"]
    assert [call[2] for call in calls] == ["triage", "tutor", "safety"]
    for call in calls:
        prompt = canonical(call[3])
        assert teacher["course"]["id"] not in prompt
        assert teacher["resource"]["id"] not in prompt
        assert teacher["course"]["title"] not in prompt
        assert "plants take up carbon dioxide" not in prompt
    tutor_payload = json.loads(calls[1][3][1]["content"])
    assert tutor_payload["course"] is None and tutor_payload["reference_passages"] == tutor_payload["coursework_references"] == []
    with app.state.database.factory() as db:
        stored = db.get(Record, reply["id"]).value
        assert stored["teacher_context_snapshot"]["authority_course_id"] == teacher["course"]["id"]
        assert stored["teacher_authority_before_release"]["active"]
    login(client, "demo-admin" if bound else "demo-instructor")
    review = client.get(f"/api/courses/{teacher['course']['id']}/homework-help/sessions/{env['session']['id']}/review")
    # A general exchange never becomes an enrolled class interaction merely
    # because that class's teacher persona participated.
    assert review.status_code == 404
    if bound:
        assert len(teacher["hook_calls"]) >= 4


def test_unavailable_core_teacher_falls_back_to_existing_triage_answer_without_tutor_send(client, app, monkeypatch):
    env = environment(client, app, monkeypatch)
    teacher = lesson_environment(client, app, monkeypatch, bound=True)
    remove_enrollments(app)
    calls = teacher_transport(monkeypatch, app, env, teacher)
    monkeypatch.setattr(app.state, "authorize_teacher", lambda *_: {"active": False})
    reply = ask(client, env).json()
    assert reply["status"] == "released", reply
    assert reply["triage"]["responder"] == "school_assistant" and reply["teacher_profile_id"] is None
    assert reply["triage"]["handoff_unavailable"]
    assert [call[2] for call in calls] == ["triage", "safety"]


def test_teacher_revocation_after_handoff_withholds_without_fallback_or_resend(client, app, monkeypatch):
    env = environment(client, app, monkeypatch)
    teacher = lesson_environment(client, app, monkeypatch, bound=True)
    remove_enrollments(app)
    calls = teacher_transport(monkeypatch, app, env, teacher, on_tutor=lambda: setattr(app.state.settings, "allow_agent_teachers", False))
    reply = ask(client, env).json()
    assert reply["status"] == "withheld" and reply["answer"] is None
    assert [call[2] for call in calls] == ["triage", "tutor"]
    assert ask(client, env).json()["status"] == "withheld" and len(calls) == 2


def test_school_can_disable_cross_course_personas_and_assistant_still_helps(client, app, monkeypatch):
    env = environment(client, app, monkeypatch, use_school_teacher_personas=False)
    teacher = lesson_environment(client, app, monkeypatch)
    remove_enrollments(app)
    calls = []
    def classify(*args):
        calls.append(args)
        if args[2] == "triage":
            assert json.loads(args[3][1]["content"])["teachers"] == []
            return result(triage_result())
        return result({"verdict": "allow", "reason_codes": []})
    monkeypatch.setattr(app.state, "classify", classify)
    env["headers"] = teacher["headers"]
    reply = ask(client, env).json()
    assert reply["status"] == "released" and reply["triage"]["responder"] == "school_assistant"
    assert [call[2] for call in calls] == ["triage", "safety"]


def test_no_enrollment_never_authorizes_explicit_or_model_selected_protected_class(client, app, monkeypatch):
    env = environment(client, app, monkeypatch)
    remove_enrollments(app)
    assert ask(client, env, course_id="demo-course").status_code == 403 and not env["calls"]
    monkeypatch.setattr(app.state, "classify", lambda *_: result(triage_result(course_id="demo-course")))
    reply = ask(client, env).json()
    assert reply["status"] == "withheld" and reply["answer"] is None


def test_existing_configuration_without_new_flag_is_readable_but_requires_v2_human_reapproval(client, app, monkeypatch):
    env = environment(client, app, monkeypatch)
    with app.state.database.write() as db:
        pointer = db.get(Record, config_id(app.state.settings.tenant_id, env["school"]))
        original = db.get(Record, pointer.value["revision_id"])
        raw = {key: value for key, value in original.value["configuration"].items() if key != "use_school_teacher_personas"}
        revision_data = {**original.value, "configuration": raw, "configuration_sha256": digest(raw)}
        revision = Record(id=uid(), tenant_id=original.tenant_id, kind="homework_ai_config_revision", parent_id=env["school"], owner_id="demo-admin", data=canonical(revision_data))
        db.add(revision)
        db.flush()
        bind_scope(db, revision, env["school"])
        old_approval = db.get(Record, pointer.value["approval_id"])
        approval = Record(id=uid(), tenant_id=original.tenant_id, kind="homework_ai_approval", parent_id=revision.id, owner_id="demo-admin", data=canonical({**old_approval.value, "boundary_version": VERSION, "configuration_sha256": digest(raw)}))
        db.add(approval)
        db.flush()
        bind_scope(db, approval, env["school"])
        pointer.set({**pointer.value, "revision_id": revision.id, "approval_id": approval.id})
        revision_id = revision.id
    admin = login(client, "demo-admin")
    descriptor = client.get("/api/homework-help/configuration?school_id=" + env["school"]).json()
    assert descriptor["configuration"]["use_school_teacher_personas"] is True
    assert descriptor["configuration_sha256"] == digest(raw)
    assert descriptor["reapproval_required"] and not descriptor["approved"] and not descriptor["available"]
    assert "updated triage" in descriptor["unavailable_reason"]
    approved = client.post("/api/homework-help/configuration/approve?school_id=" + env["school"], json={"revision_id": revision_id}, headers=admin).json()
    assert approved["approved"] and approved["available"] and not approved["reapproval_required"]
    assert approved["boundary_version"] == BOUNDARY_VERSION


def test_approved_base_ai_persona_needs_no_course_or_corpus_to_help(client, app, monkeypatch):
    from test_teacher_onboarding import create, approve
    env = environment(client, app, monkeypatch)
    admin = login(client, "demo-admin")
    profile = approve(client, admin, env["school"], create(client, admin, env["school"], kind="ai", name="Patient Math Coach", expertise=["mathematics"], ai_behavior={"tone": "warm", "instructions": "Use patient, short explanations."}))
    remove_enrollments(app)
    env["headers"] = login(client)
    calls = []
    def classify(*args):
        calls.append(args)
        if args[2] == "triage":
            return result(triage_result(teacher_profile_id=profile["id"], topic="Mathematics"))
        if args[2] == "tutor":
            payload = json.loads(args[3][1]["content"])
            assert payload["assistant"]["behavior"]["instructions"] == "Use patient, short explanations."
            assert payload["course"] is None and payload["reference_passages"] == []
            return result({"mode": "guided_practice", "text": "Let's explore the method using a different example. Which step would you like to practice?", "reference_ids": []})
        return result({"verdict": "allow", "reason_codes": []})
    monkeypatch.setattr(app.state, "classify", classify)
    reply = ask(client, env).json()
    assert reply["status"] == "released", reply
    assert reply["triage"]["teacher_name"] == "Patient Math Coach" and reply["teacher_profile_id"] == profile["id"]
    assert [call[2] for call in calls] == ["triage", "tutor", "safety"]
    with app.state.database.factory() as db:
        assert db.get(Record, reply["id"]).value["teacher_context_snapshot"]["authority_course_id"] is None


def test_followup_using_prior_class_text_retains_only_that_enrollment_dependency(client, app, monkeypatch):
    env = environment(client, app, monkeypatch)
    first = ask(client, env).json()
    assert first["status"] == "released"
    original = env["transport"]
    def general_followup(*args):
        response = original(*args)
        return result(triage_result()) if args[2] == "triage" else response
    monkeypatch.setattr(app.state, "classify", general_followup)
    second = ask(client, env, message="How does that idea apply in general?", idempotency_key="general-followup-scope").json()
    assert second["status"] == "released" and second["triage"]["course_id"] is None
    with app.state.database.factory() as db:
        assert db.get(Record, second["id"]).value["routing_context_course_ids"] == ["demo-course"]
    remove_enrollments(app)
    history = client.get(f"/api/homework-help/ai/sessions/{env['session']['id']}").json()["messages"]
    assert all(item["status"] == "withheld" and item["answer"] is None for item in history)
    new = ask(client, env, message="What is a galaxy?", idempotency_key="new-general-after-class-loss").json()
    assert new["status"] == "released" and new["triage"]["course_id"] is None


def test_operator_closure_during_teacher_handoff_retains_late_result_without_release_or_retry(client, app, monkeypatch):
    from fastapi.testclient import TestClient
    from test_teacher_onboarding import create, approve
    from test_homework_ai import close_body, close_path
    env = environment(client, app, monkeypatch)
    admin = login(client, "demo-admin")
    profile = approve(client, admin, env["school"], create(client, admin, env["school"], kind="ai", name="General AI Teacher"))
    env["headers"] = login(client)
    calls = []
    with TestClient(app, base_url=app.state.settings.app_url) as operator:
        operator_admin = login(operator, "demo-admin")
        def classify(*args):
            calls.append(args)
            if args[2] == "triage":
                return result(triage_result(teacher_profile_id=profile["id"]))
            if args[2] == "tutor":
                review = operator.get(f"/api/homework-help/ai/sessions/{env['session']['id']}/review").json()
                pending = next(item for item in review["messages"] if item["status"] == "pending")
                closed = operator.post(close_path(env, pending["id"]), json=close_body(review, pending["id"]), headers=operator_admin)
                assert closed.status_code == 200
                return result({"mode": "explanation", "text": "This late candidate must never be displayed.", "reference_ids": []})
            pytest.fail("No safety send may follow a human-closed teacher request")
        monkeypatch.setattr(app.state, "classify", classify)
        reply = ask(client, env).json()
        assert reply["status"] == "withheld" and reply["answer"] is None
        assert [call[2] for call in calls] == ["triage", "tutor"]
        review = operator.get(f"/api/homework-help/ai/sessions/{env['session']['id']}/review").json()
        stored = review["messages"][0]
        assert stored["phases"]["tutor"]["response"] is None
        assert stored["late_provider_receipts"][0]["phase"] == "tutor"
        assert ask(client, env).json()["answer"] is None and len(calls) == 2
