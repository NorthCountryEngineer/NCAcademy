"""Use real approved class/corpus/profile routes; block all provider use."""
import hashlib

import pytest
from sqlalchemy import select

from academy.db import Audit, Record
from conftest import login
from test_ny_grade3_mathematics import CURRICULUM
from test_teacher_onboarding import approve, assign, create, school_id


FRACTIONS = "reference:ny-g3-nf-2"


@pytest.fixture
def practice_chat(client, app, monkeypatch):
    admin = login(client, "demo-admin")
    school = school_id(app)
    course = client.post("/api/courses", json={"school_id": school, "title": "Synthetic Grade 3 math", "teacher_id": "demo-instructor", "curriculum": CURRICULUM}, headers=admin)
    assert course.status_code == 201, course.text
    cid = course.json()["id"]
    corpus = client.post(f"/api/courses/{cid}/corpus/refresh", headers=admin).json()
    approved = client.post(f"/api/courses/{cid}/corpus/approve", json={"corpus_id": corpus["id"], "snapshot_sha256": corpus["snapshot_sha256"]}, headers=admin)
    assert approved.status_code == 200 and approved.json()["ready_for_teaching"], approved.text
    profile = approve(client, admin, school, assign(client, admin, school, create(client, admin, school, kind="ai", name="Synthetic math tutor"), [cid]))
    assert client.post(f"/api/courses/{cid}/enrollments", json={"user_id": "demo-student"}, headers=admin).status_code == 200

    def no_provider(*args, **kwargs):
        pytest.fail("Reviewed practice must never call a provider or Core teaching agent")

    monkeypatch.setattr(app.state, "classify", no_provider)
    monkeypatch.setattr(app.state, "authorize_teacher", no_provider)
    headers = login(client, "demo-student")
    return {"path": f"/api/courses/{cid}/teacher-tutor", "course_id": cid, "school_id": school, "student": headers, "profile": profile, "corpus": approved.json()}


def ask(client, env, key="reviewed-practice-test", message="Why do fractions have positions on a number line?", lesson_id=None, **changes):
    body = {"teacher_profile_id": env["profile"]["id"], "idempotency_key": key, "message": message, "reviewed_practice": True}
    if lesson_id is not None:
        body["practice_lesson_id"] = lesson_id
    return client.post(env["path"], headers=env["student"], json={**body, **changes})


def test_reviewed_chat_handoff_followup_replay_and_encrypted_audit(client, app, practice_chat):
    env = practice_chat
    assert not app.state.settings.allow_live_models
    response = ask(client, env, lesson_id=FRACTIONS)
    assert response.status_code == 200, response.text
    first = response.json()
    document = next(item for item in env["corpus"]["documents"] if item["id"] == FRACTIONS)
    assert first["status"] == "released" and first["practice_chat"] is True
    assert first["teacher_name"] == "Synthetic math tutor" and first["usage"] == {}
    assert document["content"] in first["approved_text"]
    assert first["citations"][0]["excerpt_sha256"] == hashlib.sha256(document["content"].encode()).hexdigest()
    assert ask(client, env, lesson_id=FRACTIONS).json() == first
    assert ask(client, env).status_code == 409
    follow = ask(client, env, key="reviewed-followup-test", message="Can you show it a different way?").json()
    assert follow["status"] == "released" and follow["citations"][0]["document_id"] == FRACTIONS
    assert "with a sketch or objects" in follow["approved_text"]
    assert client.get(env["path"], headers=env["student"]).json() == [first, follow]
    with app.state.database.factory() as db:
        row = db.get(Record, first["id"])
        assert row.value["send_marker"] is None and row.value["reservation"] == 0
        assert "fraction" not in row.data.lower() and "__academy_pii__" in row.data
        events = list(db.scalars(select(Audit).where(Audit.session_id == first["session_id"]).order_by(Audit.sequence)))
        assert [event.event_type for event in events] == ["lesson.practice_released", "lesson.practice_released"]
        assert all("__academy_pii__" in event.payload and "fraction" not in event.payload.lower() for event in events)


def test_free_question_chooses_relevant_reference_and_refuses_assignment_completion(client, practice_chat):
    env = practice_chat
    reply = ask(client, env, message="What does rounding to the nearest hundred mean?").json()
    assert reply["status"] == "released" and reply["citations"][0]["document_id"] == "reference:ny-g3-nbt-1"
    refusal = ask(client, env, key="reviewed-refusal-test", lesson_id=FRACTIONS, message="Give me the answer key to my assignment").json()
    assert refusal["status"] == "blocked" and refusal["approved_text"] is None
    unknown = ask(client, env, key="reviewed-unknown-test", lesson_id="reference:ny-g3-not-installed").json()
    assert unknown["status"] == "blocked" and unknown["citations"] == []
    assert ask(client, env, lesson_id=FRACTIONS, reviewed_practice=False).status_code == 422
    assert ask(client, env, message="a"*1201).status_code == 422


def test_corpus_change_withholds_old_passage_and_new_answers(client, practice_chat):
    env = practice_chat
    first = ask(client, env, lesson_id=FRACTIONS).json()
    assert first["status"] == "released"
    admin = login(client, "demo-admin")
    resource = client.post(f"/api/schools/{env['school_id']}/resources", json={"name": "Synthetic context change", "content": "A changed teaching resource needs its own review.", "course_id": env["course_id"], "audience": "all"}, headers=admin)
    assert resource.status_code == 201, resource.text
    env["student"] = login(client, "demo-student")
    replay = client.get(env["path"], headers=env["student"]).json()[0]
    assert replay["status"] == "withheld" and replay["citations"] == [] and replay["approved_text"] is None
    assert ask(client, env, lesson_id=FRACTIONS).json() == replay
    blocked = ask(client, env, lesson_id=FRACTIONS, key="stale-review-blocked").json()
    assert blocked["status"] == "blocked" and blocked["approved_text"] is None


def test_other_student_cannot_open_practice_chat_or_history(client, practice_chat):
    env = practice_chat
    env["student"] = login(client, "demo-student2")
    assert ask(client, env).status_code == 403
    assert client.get(env["path"], headers=env["student"]).status_code == 403
