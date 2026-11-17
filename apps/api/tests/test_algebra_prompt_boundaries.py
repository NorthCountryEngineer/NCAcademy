"""Graph-step clarification retains independent review and immutable replay."""
import json

import pytest
from sqlalchemy import select

from academy.db import Record
from test_homework_ai import result, triage_result
from test_homework_deidentified_api import create_child, send, setup_public


PARTIAL_GRAPH_STEP = (
    "Subtract 2x from both sides to get 3y = 6 - 2x. "
    "Which number should you divide every term by next to isolate y?"
)
FINAL_GRAPH_FEATURES = (
    "The slope is -2/3 and the y-intercept is 2. "
    "Plot (0, 2) and (3, 0) and draw the line through them."
)


@pytest.mark.parametrize("candidate,verdict,expected_status", [
    pytest.param(PARTIAL_GRAPH_STEP, "allow", "released", id="approved-intermediate-step"),
    pytest.param(PARTIAL_GRAPH_STEP, "withhold", "withheld", id="independent-review-still-controls-release"),
    pytest.param(FINAL_GRAPH_FEATURES, "withhold", "withheld", id="final-features-remain-withheld"),
])
def test_graph_step_still_requires_independent_approval_and_never_retries(client, app, monkeypatch, candidate, verdict, expected_status):
    env = setup_public(client, app, monkeypatch)
    child, headers, session_id, account = create_child(client, app, env)
    original = app.state.classify

    def reviewed_candidate(*args):
        original(*args)  # Records the exact canonical prompt and enforces its contract.
        if args[2] == "triage":
            return result(triage_result(response={"mode": "guided_practice", "text": candidate, "reference_ids": []}))
        return result({"verdict": verdict,
                       "reason_codes": [] if verdict == "allow" else ["assignment_completion"],
                       "input_risk_codes": []})

    monkeypatch.setattr(app.state, "classify", reviewed_candidate)
    question = "Name: PRIVATE_GRAPH_STUDENT\nGraph 2x + 3y = 6"
    try:
        response = send(child, headers, session_id, question, "reviewed-graph-step")
        assert response.status_code == 200 and response.json()["status"] == expected_status, response.text
        assert response.json()["answer"] == (candidate if verdict == "allow" else None)
        assert [call["model"] for call in env["public_calls"]] == ["triage", "safety"]
        task = env["public_calls"][0]["payload"]["task"]
        assert task["kind"] == "algebra" and task["action"] == "study_relation" and task["feature"] == "graph"
        assert env["public_calls"][1]["payload"]["candidate"] == {
            "mode": "guided_practice", "text": candidate, "reference_ids": []}
        for call in env["public_calls"]:
            assert call["payload"]["task"] == task
            wire = json.dumps(call["prompt"])
            for private in ("PRIVATE_GRAPH_STUDENT", account["id"], session_id, env["school"]):
                assert private not in wire
        repeated = send(child, headers, session_id, question, "reviewed-graph-step")
        assert repeated.json() == response.json()
        assert len(env["public_calls"]) == 2
        with app.state.database.factory() as db:
            row = db.get(Record, response.json()["id"])
            assert set(row.value["phases"]) == {"triage", "safety"}
            assert "__academy_pii__" in row.data
            if verdict == "withhold":
                assert row.value["withholding_code"] == "independent_safety_withheld"
            sends = list(db.scalars(select(Record).where(Record.kind == "homework_ai_send", Record.parent_id == row.id)))
            assert len(sends) == 2
    finally:
        child.close()


def test_live_intermediate_graph_example_is_algebraically_equivalent():
    # Independent numerical witnesses for the specific statement seen in UAT.
    for x, y in ((-3, 4), (0, 2), (3, 0), (7, -1)):
        original_residual = 2 * x + 3 * y - 6
        intermediate_residual = 3 * y - (6 - 2 * x)
        assert original_residual == intermediate_residual
