"""Use real approved class/corpus/profile routes; block all provider use."""
import hashlib

import pytest
from sqlalchemy import select

from academy.db import Audit, Record
from academy.practice_lesson import EXHAUSTED_PRACTICE_TEXT, next_question, select_practice_excerpt
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
    assert document["content"] not in follow["approved_text"]
    citation = follow["citations"][0]
    excerpt = document["content"][citation["excerpt_start"]:citation["excerpt_end"]]
    assert 0 < len(excerpt) <= 420
    assert excerpt in follow["approved_text"]
    assert citation["excerpt_sha256"] == hashlib.sha256(excerpt.encode()).hexdigest()
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


def test_followups_progress_through_exact_sections_then_explain_the_limit(client, app, practice_chat):
    env = practice_chat
    first = ask(client, env, lesson_id=FRACTIONS).json()
    document = next(item for item in env["corpus"]["documents"] if item["id"] == FRACTIONS)
    spans, replies = set(), [first]
    for index in range(8):
        response = ask(client, env, key=f"section-followup-{index}", lesson_id=FRACTIONS,
                       message="Can you show another example?").json()
        assert response["status"] == "released", response
        replies.append(response)
        citation = response["citations"][0]
        start, end = citation["excerpt_start"], citation["excerpt_end"]
        excerpt = document["content"][start:end]
        assert citation["excerpt_sha256"] == hashlib.sha256(excerpt.encode()).hexdigest()
        if not excerpt:
            assert response["approved_text"] == EXHAUSTED_PRACTICE_TEXT
            break
        assert (start, end) not in spans
        spans.add((start, end))
        assert len(excerpt) <= 420 and excerpt in response["approved_text"]
        assert document["content"] not in response["approved_text"]
    else:
        pytest.fail("A finite reviewed lesson must give an honest exhaustion response")
    assert len(spans) >= 3
    assert client.get(env["path"], headers=env["student"]).json() == replies
    with app.state.database.factory() as db:
        values = [db.get(Record, reply["id"]).value for reply in replies]
        assert values[0]["practice_excerpt_mode"] == "overview"
        assert values[-1]["practice_excerpt_mode"] == "exhausted"
        assert all(value["send_marker"] is None and value["usage"] == {} for value in values)


def test_section_selection_ranks_the_question_and_keeps_exact_unicode_offsets():
    content = "A rectangle covers an area. Count its squares. Fractions have equal parts. A shaded part can be ½."
    overview = select_practice_excerpt(content, "Help with fractions", started=False)
    assert content[overview["start"]:overview["end"]] == content
    first = select_practice_excerpt(content, "Help with shaded fractions", started=True)
    assert content[first["start"]:first["end"]] == "Fractions have equal parts. A shaded part can be ½."
    second = select_practice_excerpt(content, "Another example", started=True,
                                     previous_spans=[(first["start"], first["end"])])
    assert content[second["start"]:second["end"]] == "A rectangle covers an area. Count its squares."
    assert select_practice_excerpt(content, "Another example", started=True,
                                   previous_spans=[(first["start"], first["end"]), (second["start"], second["end"])]) is None
    repeat = select_practice_excerpt(content, "Repeat the shaded fractions part", started=True,
                                     previous_spans=[(first["start"], first["end"]), (second["start"], second["end"])])
    assert repeat == first


def test_one_sentence_has_no_fake_alternative_and_fixed_coaching_varies():
    assert select_practice_excerpt("This is the only approved explanation.", "Another example", started=True) is None
    prompts = {next_question("Show it with a picture", True, index) for index in range(3)}
    assert len(prompts) == 3
    assert "Which part" in next_question("Help me check this", True, 1)
