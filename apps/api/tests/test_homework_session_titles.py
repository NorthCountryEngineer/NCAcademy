"""Conversation display titles survive history reads without record rewrites."""
from types import SimpleNamespace

import pytest
from sqlalchemy import select

from academy.db import Audit, Record
from academy.homework_ai_api import session_descriptor
from test_homework_deidentified_api import BASE, create_child, send, setup_public


@pytest.mark.parametrize(("first", "expected"), [
    (None, "New conversation"),
    ({}, "New conversation"),
    ({"message": None, "answer": "WITHHELD_ANSWER"}, "New conversation"),
    ({"message": {"ciphertext": "PRIVATE_CIPHER"}}, "New conversation"),
    ({"message": " \n\t ", "images": [{"id": "picture"}]}, "Homework picture"),
    ({"message": "  My\n question\t here  "}, "My question here"),
    ({"message": "x" * 200}, "x" * 160),
])
def test_titles_use_only_bounded_available_question_text(first, expected):
    session = SimpleNamespace(id="session", value={"school_id": "school", "created_at": "2026-11-17", "idempotency_key": "key"})
    assert session_descriptor(session, first)["title"] == expected


def test_first_question_title_survives_index_detail_replay_and_followup(client, app, monkeypatch):
    env = setup_public(client, app, monkeypatch)
    child, headers, session_id, account = create_child(client, app, env)
    first_question = "How are photosynthesis and cellular respiration related?"
    try:
        first = send(child, headers, session_id, first_question, "title-first-question").json()
        assert first["status"] == "released"
        with app.state.database.factory() as db:
            stored = {row.id: row.data for row in db.scalars(select(Record).where(
                (Record.id == session_id) | (Record.parent_id == session_id)))}
            audit_before = [(row.id, row.payload) for row in db.scalars(select(Audit).where(Audit.session_id == session_id))]
        call_count = len(env["public_calls"])
        index = child.get(BASE, params={"school_id": env["school"]}).json()
        detail = child.get(f"{BASE}/sessions/{session_id}").json()
        title = next(item["title"] for item in index["sessions"] if item["id"] == session_id)
        assert title == detail["session"]["title"] == first_question
        assert detail["messages"][0]["message"] == first_question
        replay = child.post(f"{BASE}/sessions", headers=headers, json={
            "school_id": env["school"], "idempotency_key": detail["session"]["idempotency_key"]})
        assert replay.status_code == 200 and replay.json()["title"] == first_question
        assert len(env["public_calls"]) == call_count
        with app.state.database.factory() as db:
            assert {row.id: row.data for row in db.scalars(select(Record).where(
                (Record.id == session_id) | (Record.parent_id == session_id)))} == stored
            assert [(row.id, row.payload) for row in db.scalars(select(Audit).where(Audit.session_id == session_id))] == audit_before
        followup = send(child, headers, session_id, "Another way please", "title-followup-question")
        assert followup.status_code == 200
        index_after = child.get(BASE, params={"school_id": env["school"]}).json()
        detail_after = child.get(f"{BASE}/sessions/{session_id}").json()
        assert next(item["title"] for item in index_after["sessions"] if item["id"] == session_id) == first_question
        assert detail_after["session"]["title"] == first_question
        assert [item["message"] for item in detail_after["messages"]] == [first_question, "Another way please"]
    finally:
        child.close()
