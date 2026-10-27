"""Synthetic-only assignment policies, attempt authority and encrypted evidence."""
from copy import deepcopy
from concurrent.futures import ThreadPoolExecutor
import json
import threading

import pytest
from fastapi import HTTPException
from fastapi.testclient import TestClient
from pydantic import ValidationError
from sqlalchemy import select, text
from sqlalchemy.exc import DatabaseError

from academy.db import Audit, Record, canonical, game_scoring_schema_id
from academy.game_scoring import calculate_points, validate_result
from academy.game_studio_schemas import GameScoringPolicy
from academy.migrate import migrate
from academy.permissions import bind_scope, default_school_id
from academy.standards import digest
from conftest import login
from test_game_studio import SPEC, approve, course_path, import_game, records, setup


POLICY = {"version": 1, "max_points": 25, "method": "proportional", "rounding": "hundredth", "attempt_policy": "best", "max_attempts": 3, "reveal_policy": "zero"}
LOST = {"outcome": "lost", "elapsed": 10, "score": 1, "reviewedLevelIds": ["level-1"]}
WON = {"outcome": "won", "elapsed": 20, "score": 3, "reviewedLevelIds": ["level-1"]}


def configure(client, headers, item, policy=None):
    response = client.put(course_path(resource=item["id"], action="scoring"), json={"expected_version": item["version"], "scoring_policy": policy or POLICY}, headers=headers)
    assert response.status_code == 200, response.text
    return response.json()


def assignment(client, app, monkeypatch, policy=None, spec=None):
    headers, game, calls = setup(client, app, monkeypatch, spec=spec)
    item = approve(client, headers, configure(client, headers, import_game(client, app, headers, game), policy))
    return item, headers, calls


def start(client, headers, item, key="synthetic-attempt-key-0001"):
    response = client.post(course_path(resource=item["id"], action="attempts"), json={"idempotency_key": key}, headers=headers)
    assert response.status_code == 200, response.text
    return response.json()


def submit(client, headers, item, attempt, result=LOST):
    response = client.post(course_path(resource=item["id"], action=f"attempts/{attempt['id']}/result"), json={"result": result}, headers=headers)
    assert response.status_code == 200, response.text
    return response.json()


def test_same_raw_observation_uses_host_25_or_100_scale_and_full_multilevel_denominator():
    assert calculate_points(SPEC, LOST, POLICY)["earned_points"] == 8.33
    assert calculate_points(SPEC, LOST, {**POLICY, "max_points": 100})["earned_points"] == 33.33
    assert calculate_points(SPEC, LOST, {**POLICY, "rounding": "whole"})["earned_points"] == 8
    spec = {**deepcopy(SPEC), "version": 2, "levels": [{"id": "level-1", "rules": {"goal": 3, "timeLimit": 60}}, {"id": "level-2", "rules": {"goal": 7, "timeLimit": 60}}]}
    partial = {**LOST, "score": 3}
    assert calculate_points(spec, partial, POLICY) == {"earned_points": 7.5, "max_points": 25, "numerator": 3, "denominator": 10, "method": "proportional", "provisional": True}
    assert calculate_points(SPEC, LOST, {**POLICY, "method": "completion"})["earned_points"] == 0
    assert calculate_points(SPEC, WON, {**POLICY, "method": "completion"})["earned_points"] == 25
    # 1/2 of a single point rounds half upward, without binary float error.
    assert calculate_points({**SPEC, "rules": {**SPEC["rules"], "goal": 2}}, LOST, {**POLICY, "max_points": 1, "rounding": "whole"})["earned_points"] == 1


def test_arcade_target_caps_independently_of_objective_completion():
    spec = {**deepcopy(SPEC), "genre": "dodge", "arcade": {"rotation": True, "shooting": True, "splitAsteroids": True, "scoring": True, "highScores": False}}
    policy = {**POLICY, "method": "arcade_target", "arcade_target": 100}
    assert calculate_points(spec, {**LOST, "points": 50}, policy)["earned_points"] == 12.5
    assert calculate_points(spec, {**LOST, "points": 300}, policy)["earned_points"] == 25
    with pytest.raises(HTTPException): calculate_points(SPEC, LOST, policy)


@pytest.mark.parametrize("change", [{"version": True}, {"max_points": True}, {"max_points": 0}, {"max_points": 10001}, {"max_attempts": 101}, {"arcade_target": None}, {"method": "arcade_target"}, {"rounding": "bankers"}, {"extra": True}])
def test_policy_rejects_ambiguous_unbounded_and_extra_configuration(change):
    with pytest.raises(ValidationError): GameScoringPolicy.model_validate({**POLICY, **change})


@pytest.mark.parametrize("change", [{"outcome": "level-complete"}, {"outcome": []}, {"outcome": {}}, {"score": True}, {"score": 4}, {"elapsed": float("nan")}, {"elapsed": 10**400}, {"elapsed": 61}, {"reviewedLevelIds": ["level-2"]}, {"reviewedLevelIds": ["level-1", "level-1"]}, {"outcome": "won"}, {"points": 30}, {"student_id": "other"}])
def test_observation_rejects_forged_structure_and_nonterminal_gameplay(change):
    with pytest.raises(HTTPException): validate_result({**LOST, **change}, SPEC)


def test_default_practice_preserved_and_scoring_requires_explicit_human_reapproval(client, app, monkeypatch):
    headers, game, calls = setup(client, app, monkeypatch)
    item = approve(client, headers, import_game(client, app, headers, game))
    assert item["scoring_policy"] is None
    student = login(client)
    assert client.post(course_path(resource=item["id"], action="attempts"), json={"idempotency_key": "synthetic-no-scoring-key"}, headers=student).status_code == 409
    teacher = login(client, "demo-instructor")
    assert client.put(course_path(resource=item["id"], action="scoring"), json={"expected_version": item["version"], "scoring_policy": POLICY}, headers=teacher).status_code == 403
    headers = login(client, "demo-admin")
    changed = configure(client, headers, item)
    assert changed["status"] == "draft" and changed["approval_id"] is None
    student = login(client)
    assert client.get(course_path()).json()["games"] == []
    assert client.post(course_path(resource=item["id"], action="attempts"), json={"idempotency_key": "synthetic-unapproved-key"}, headers=student).status_code == 403


@pytest.mark.parametrize("mode,expected", [("first", 8.33), ("latest", 16.67), ("best", 25)])
def test_repeat_policy_uses_server_ordinals_preserves_replay_and_separates_reviewed_credit(client, app, monkeypatch, mode, expected):
    item, _admin, _calls = assignment(client, app, monkeypatch, {**POLICY, "attempt_policy": mode})
    student = login(client)
    attempts = []
    for index, result in enumerate([LOST, WON, {**LOST, "score": 2}]):
        play = start(client, student, item, f"synthetic-attempt-{index:04d}")
        assert play["preview"] is False and play["spec"] == SPEC
        observed = submit(client, student, item, play["attempt"], result)
        assert submit(client, student, item, play["attempt"], result) == observed
        attempts.append(observed)
    history = client.get(course_path(resource=item["id"], action="attempts")).json()
    assert history["summary"]["suggested_points"] == expected and history["summary"]["remaining_attempts"] == 0
    assert not history["can_award"] and history["summaries"] == []
    assert all(attempt["award"] is None for attempt in history["attempts"])
    replay = start(client, student, item, "synthetic-attempt-0000")
    assert replay["attempt"] == attempts[0]
    count = len(_calls)
    assert client.post(course_path(resource=item["id"], action="attempts"), json={"idempotency_key": "synthetic-fourth-attempt"}, headers=student).status_code == 409
    assert len(_calls) == count
    changed = client.post(course_path(resource=item["id"], action=f"attempts/{attempts[0]['id']}/result"), json={"result": WON}, headers=student)
    assert changed.status_code == 409
    route = course_path(resource=item["id"], action=f"attempts/{attempts[0]['id']}/award")
    assert client.post(route, json={"points": 12, "reason": "Human assessment"}, headers=student).status_code == 403
    teacher = login(client, "demo-instructor")
    awarded = client.post(route, json={"points": 12, "reason": "Reviewed strategy and explanation separately."}, headers=teacher)
    assert awarded.status_code == 200, awarded.text
    assert awarded.json()["award"]["points"] == 12
    assert awarded.json()["calculation"]["earned_points"] == 8.33
    assert client.post(route, json={"points": 12, "reason": "Reviewed strategy and explanation separately."}, headers=teacher).json() == awarded.json()
    assert client.post(route, json={"points": 13, "reason": "Changed"}, headers=teacher).status_code == 409
    history = client.get(course_path(resource=item["id"], action="attempts")).json()
    assert history["summary"] is None and history["summaries"][0]["suggested_points"] == expected


def test_current_membership_ownership_and_human_only_review_are_enforced(client, app, monkeypatch):
    item, _admin, calls = assignment(client, app, monkeypatch)
    student = login(client); attempt = start(client, student, item)["attempt"]
    other = login(client, "demo-student2")
    assert client.get(course_path(resource=item["id"], action="attempts")).json()["attempts"] == []
    route = course_path(resource=item["id"], action=f"attempts/{attempt['id']}/result")
    assert client.post(route, json={"result": LOST}, headers=other).status_code == 404
    student = login(client)
    with app.state.database.write() as db:
        db.delete(db.get(Record, "enrollment-demo-student"))
    assert client.get(course_path(resource=item["id"], action="attempts")).status_code == 403
    assert client.post(route, json={"result": LOST}, headers=student).status_code == 403
    identity = {"id": "agent:synthetic", "role": "admin", "tenant_id": app.state.settings.tenant_id, "principal_kind": "agent", "school_id": default_school_id(app.state.settings.tenant_id), "permissions": []}
    monkeypatch.setattr(app.state.auth, "actor", lambda _: dict(identity))
    assert client.get(course_path(resource=item["id"], action="attempts")).status_code == 403
    assert client.post(course_path(resource=item["id"], action=f"attempts/{attempt['id']}/award"), json={"points": 5, "reason": "Agent must not award"}, headers=student).status_code == 403


@pytest.mark.parametrize("change", ["course", "policy", "approval", "connection", "membership", "session"])
def test_attempt_creation_revalidates_after_public_io_without_partial_attempts(client, app, monkeypatch, change):
    item, _admin, _calls = assignment(client, app, monkeypatch)
    student = login(client)
    original = app.state.game_studio_fetch
    def changed(*args):
        with app.state.database.write() as db:
            if change == "course":
                row = db.get(Record, "demo-course"); row.set({**row.value, "title": "New course context"})
            elif change in {"policy", "approval"}:
                row = db.get(Record, item["id"]); row.set({**row.value, "version": row.value["version"] + 1})
            elif change == "connection":
                from academy.game_studio import configuration_id
                row = db.get(Record, configuration_id(app.state.settings.tenant_id, default_school_id(app.state.settings.tenant_id))); row.set({**row.value, "enabled": False})
            elif change == "membership": db.delete(db.get(Record, "enrollment-demo-student"))
            else:
                from academy.db import Login
                row = db.scalar(select(Login).where(Login.user_id == "demo-student")); row.expires = 0
        return original(*args)
    monkeypatch.setattr(app.state, "game_studio_fetch", changed)
    response = client.post(course_path(resource=item["id"], action="attempts"), json={"idempotency_key": "synthetic-inflight-start"}, headers=student)
    assert response.status_code in {401, 403, 409}
    assert records(app, ["game_attempt"]) == []


def test_policy_revision_retains_historical_snapshot_and_blocks_late_result(client, app, monkeypatch):
    item, _admin, _calls = assignment(client, app, monkeypatch)
    student = login(client); first = start(client, student, item)["attempt"]
    admin = login(client, "demo-admin")
    changed = approve(client, admin, configure(client, admin, item, {**POLICY, "max_points": 100}))
    student = login(client)
    assert client.post(course_path(resource=item["id"], action=f"attempts/{first['id']}/result"), json={"result": LOST}, headers=student).status_code == 409
    second = start(client, student, changed, "synthetic-new-policy-attempt")["attempt"]
    assert second["ordinal"] == 1 and second["scoring_policy"]["max_points"] == 100
    history = client.get(course_path(resource=item["id"], action="attempts")).json()
    assert len(history["attempts"]) == 2 and history["attempts"][0]["scoring_policy"]["max_points"] == 25
    assert history["summary"]["attempt_count"] == 1


def test_all_student_evidence_and_review_reasons_are_encrypted_and_immutable(client, app, monkeypatch):
    item, _admin, calls = assignment(client, app, monkeypatch)
    student = login(client); attempt = start(client, student, item)["attempt"]
    submit(client, student, item, attempt)
    teacher = login(client, "demo-instructor")
    reason = "Private instructor assessment of synthetic strategy."
    response = client.post(course_path(resource=item["id"], action=f"attempts/{attempt['id']}/award"), json={"points": 7.25, "reason": reason}, headers=teacher)
    assert response.status_code == 200, response.text
    kinds = ["game_attempt", "game_attempt_result", "game_attempt_award"]
    evidence = records(app, kinds)
    assert len(evidence) == 3 and all("__academy_pii__" in json.loads(data) for _, _, data in evidence)
    assert reason not in canonical(evidence) and "reviewedLevelIds" not in canonical(evidence)
    with app.state.database.factory() as db:
        events = list(db.scalars(select(Audit).where(Audit.event_type.in_(["game_studio.attempt_started", "game_studio.attempt_observed", "game_studio.award_reviewed"]))))
        assert len(events) == 3 and all("__academy_pii__" in json.loads(event.payload) for event in events)
    for key, _kind, _data in evidence:
        for command in ("UPDATE records SET data='{}' WHERE id=:id", "UPDATE records SET owner_id='different' WHERE id=:id", "DELETE FROM records WHERE id=:id"):
            with pytest.raises(DatabaseError), app.state.database.write() as db:
                db.execute(text(command), {"id": key})
    assert "demo-student" not in canonical(calls) and reason not in canonical(calls)


def test_schema16_is_additive_idempotent_and_required_for_readiness(client, app):
    before = records(app); migrate(app.state.settings)
    assert records(app) == before
    with app.state.database.write() as db:
        db.delete(db.get(Record, game_scoring_schema_id(app.state.settings.tenant_id)))
    assert client.get("/api/ready").status_code == 503
    migrate(app.state.settings)
    assert client.get("/api/ready").status_code == 200
    assert client.get("/api/admin/settings", headers=login(client, "demo-admin")).json()["schema_version"] == 16


def test_two_concurrent_starts_cannot_exceed_a_single_attempt_budget(client, app, monkeypatch):
    item, _admin, _calls = assignment(client, app, monkeypatch, {**POLICY, "max_attempts": 1})
    browsers = [TestClient(app, base_url=app.state.settings.app_url) for _ in range(2)]
    headers = [login(browser) for browser in browsers]
    barrier = threading.Barrier(2)
    original = app.state.game_studio_fetch
    def concurrent_public_read(*args):
        barrier.wait(timeout=10)
        return original(*args)
    monkeypatch.setattr(app.state, "game_studio_fetch", concurrent_public_read)
    def request(index):
        return browsers[index].post(course_path(resource=item["id"], action="attempts"), json={"idempotency_key": f"synthetic-concurrent-{index}"}, headers=headers[index])
    try:
        with ThreadPoolExecutor(max_workers=2) as workers:
            responses = list(workers.map(request, range(2)))
        assert sorted(response.status_code for response in responses) == [200, 409]
        assert len(records(app, ["game_attempt"])) == 1
        winner = next(response.json() for response in responses if response.status_code == 200)
        assert winner["attempt"]["ordinal"] == 1
    finally:
        for browser in browsers:
            browser.close()


def test_preparation_accepts_policy_without_changing_public_game_or_granting_publication(client, app, monkeypatch):
    headers, game, calls = setup(client, app, monkeypatch)
    teacher = login(client, "demo-instructor")
    response = client.post(course_path(), json={"game_id": game["id"], "published_revision": game["publishedRevision"], "spec_sha256": digest(game["spec"]), "learning_objective": "Practice and then explain the strategy.", "idempotency_key": "synthetic-policy-prepare", "scoring_policy": POLICY}, headers=teacher)
    assert response.status_code == 201, response.text
    item = response.json()
    assert item["scoring_policy"] == POLICY and item["status"] == "draft"
    assert not item["can_approve"] and not item["can_configure_scoring"]
    assert len(records(app, ["game_scoring_revision"])) == 1
    assert calls and all("scoring" not in canonical(call) for call in calls)


@pytest.mark.parametrize("points", [-1, 25.01, 0.001])
def test_reviewed_credit_rejects_out_of_scale_or_sub_cent_values(client, app, monkeypatch, points):
    item, _admin, _calls = assignment(client, app, monkeypatch)
    student = login(client); attempt = start(client, student, item)["attempt"]
    submit(client, student, item, attempt)
    teacher = login(client, "demo-instructor")
    response = client.post(course_path(resource=item["id"], action=f"attempts/{attempt['id']}/award"), json={"points": points, "reason": "Review"}, headers=teacher)
    assert response.status_code == 422 and records(app, ["game_attempt_award"]) == []


def test_unassigned_teacher_cannot_read_or_award_class_attempts(client, app, monkeypatch):
    item, _admin, _calls = assignment(client, app, monkeypatch)
    student = login(client); attempt = start(client, student, item)["attempt"]
    submit(client, student, item, attempt)
    teacher = login(client, "demo-instructor")
    with app.state.database.write() as db:
        db.delete(db.get(Record, "enrollment-demo-instructor"))
    assert client.get(course_path(resource=item["id"], action="attempts")).status_code == 403
    assert client.post(course_path(resource=item["id"], action=f"attempts/{attempt['id']}/award"), json={"points": 5, "reason": "No longer assigned"}, headers=teacher).status_code == 403


@pytest.mark.parametrize("change", [{"outcome": []}, {"outcome": {}}, {"elapsed": 10**400}])
def test_malformed_json_observations_are_422_without_persisting_partial_results(client, app, monkeypatch, change):
    item, _admin, _calls = assignment(client, app, monkeypatch)
    student = login(client); attempt = start(client, student, item)["attempt"]
    response = client.post(course_path(resource=item["id"], action=f"attempts/{attempt['id']}/result"), json={"result": {**LOST, **change}}, headers=student)
    assert response.status_code == 422 and records(app, ["game_attempt_result"]) == []
