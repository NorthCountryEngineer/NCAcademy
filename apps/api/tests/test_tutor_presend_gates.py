"""Revocation after durable admission must prevent the actual model send."""
from copy import deepcopy

import pytest
from fastapi import HTTPException

from academy import main
from academy.db import Record, RecordScope
from conftest import model_assignment


def revoke_during_reauthentication(app, monkeypatch, mutation):
    original = app.state.auth.actor
    calls = []

    def actor(request):
        identity = original(request)
        calls.append(request.url.path)
        if len(calls) == 2:
            # The first call admits the student request. The second is the
            # fresh Core/session check after its transaction commits.
            mutation()
        return identity

    monkeypatch.setattr(app.state.auth, "actor", actor)
    return calls


def request_turn(client, headers, profile=None):
    return client.post("/api/assignments/demo-assignment/tutor", json={"message": "How should I compare ecosystem evidence?", "idempotency_key": "pre-send-revocation-12345", "teacher_profile_id": profile}, headers=headers)


@pytest.mark.parametrize("change", ["live_flag", "assignment_off", "connection_disabled", "connection_key_replaced", "connection_school"])
def test_model_send_rechecks_current_global_assignment_and_connection_authority(app, client, monkeypatch, change):
    headers, connection = model_assignment(client, app)
    provider_calls = []

    def mutation():
        if change == "live_flag":
            app.state.settings.allow_live_models = False
            return
        with app.state.database.write() as db:
            if change == "assignment_off":
                task = db.get(Record, "demo-assignment")
                value = task.value
                value["tutor_policy"]["mode"] = "off"
                task.set({**value, "version": value["version"] + 1})
            elif change == "connection_school":
                # The immutable scope invariant must be checked even if corrupt
                # sidecar metadata changes without the connection version.
                db.get(RecordScope, connection["id"]).school_id = "another-school"
            else:
                row = db.get(Record, connection["id"])
                value = row.value
                if change == "connection_disabled":
                    value["enabled"] = False
                else:
                    value["key_cipher"] = app.state.auth.seal({"api_key": "synthetic-replacement-key-never-live"})
                # Compare actual enabled/key state as well as the normal API's
                # version, so a stale credential cannot be sent accidentally.
                row.set(value)

    reauth_calls = revoke_during_reauthentication(app, monkeypatch, mutation)

    def classify(*args):
        provider_calls.append(args)
        raise AssertionError("A revoked model request must never reach transport")

    app.state.classify = classify
    response = request_turn(client, headers)
    assert response.status_code == 200, response.text
    assert response.json()["status"] in {"blocked", "withheld"}
    assert response.json()["approved_text"] is None and len(reauth_calls) >= 2
    assert provider_calls == []
    assert request_turn(client, headers).json() == response.json()
    assert provider_calls == []


@pytest.mark.parametrize("change", ["teacher_admission", "teacher_hook", "profile_revision", "corpus_snapshot", "corpus_approval"])
def test_named_teacher_send_rechecks_current_admission_and_approved_context(app, client, monkeypatch, change):
    headers, _ = model_assignment(client, app)
    app.state.settings.allow_agent_teachers = True
    app.state.authorize_teacher = lambda *_: {"active": True, "authorization_method": "synthetic-read-only"}
    context = {"profile_id": "synthetic-ai", "name": "Evidence Coach", "biography": "Synthetic approved teacher", "ai_behavior": {"tone": "warm"}, "approved_version": 3, "approved_revision_id": "revision-3", "approved_hash": "a" * 64, "corpus": {"id": "corpus-3", "snapshot_sha256": "b" * 64, "approval": {"id": "approval-3", "approval_sha256": "c" * 64}}}
    monkeypatch.setattr(main, "resolve_teacher_context", lambda *_: deepcopy(context))
    monkeypatch.setattr(main, "retrieve_corpus", lambda *_: {"matches": [{"id": "standard:HS-LS2-6", "excerpt": "Synthetic ecosystem evidence"}], "corpus_id": "corpus-3"})
    provider_calls = []

    def mutation():
        if change == "teacher_admission":
            app.state.settings.allow_agent_teachers = False
        elif change == "teacher_hook":
            def denied(*_):
                raise HTTPException(403, "Synthetic teacher revocation")
            app.state.authorize_teacher = denied
        elif change == "profile_revision":
            context["approved_revision_id"] = "revision-4"
            context["approved_hash"] = "d" * 64
        elif change == "corpus_snapshot":
            context["corpus"]["snapshot_sha256"] = "e" * 64
        else:
            context["corpus"]["approval"] = {"id": "approval-4", "approval_sha256": "f" * 64}

    revoke_during_reauthentication(app, monkeypatch, mutation)

    def classify(*args):
        provider_calls.append(args)
        raise AssertionError("A stale named teacher context must never reach transport")

    app.state.classify = classify
    response = request_turn(client, headers, profile="synthetic-ai")
    assert response.status_code == 200, response.text
    assert response.json()["status"] in {"blocked", "withheld"}
    assert response.json()["approved_text"] is None and provider_calls == []
    assert request_turn(client, headers, profile="synthetic-ai").json() == response.json()
    assert provider_calls == []
