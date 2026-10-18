"""Independent adversarial review of app-only chat scope and recovery."""
import time
from contextlib import contextmanager
from uuid import uuid4

import pytest
from sqlalchemy import select

from academy.chat_groups import ATTEMPT, COMMAND, GROUP, RECEIPT, key, put
from academy.core_chat_transport import CoreChatFailure
from academy.db import Record, now
from academy.permissions import membership_id
from conftest import login
from test_chat_groups import bootstrap, chat, create, group_url, post, sid
from test_school_permissions import teacher_agent


def test_matching_joined_list_is_insufficient_to_adopt_app_only_room(client, app, chat):
    teacher = login(client, "demo-instructor")
    chat.create_error = CoreChatFailure("unavailable", uncertain=True)
    group = create(client, app, teacher)
    assert group["status"] == "uncertain"
    # Joined-list metadata has no owner or sole-participant proof. Even a
    # byte-exact title/topic match must never authorize a new destination.
    response = client.post(group_url(group, "/recover"), json={}, headers=teacher)
    assert response.status_code == 409
    assert client.get(group_url(group)).json()["status"] == "uncertain"
    assert len(chat.creates) == 1 and chat.sends == []


def test_persisted_pending_command_gets_never_resend_but_explicit_retry_uses_exact_original(client, app, chat):
    student = login(client)
    group = bootstrap(client, app, student)[0]
    delivered = post(client, student, group).json()
    with app.state.database.write() as db:
        original = db.get(Record, delivered["id"])
        data = {**original.value, "idempotency_key": "interrupted-command-key", "request_id": "stable-interrupted-wire-id", "created_at": now()}
        command_id = key(COMMAND, app.state.settings.tenant_id, group["id"], "demo-student", data["idempotency_key"])
        put(db, app.state.settings.tenant_id, COMMAND, command_id, group["id"], "demo-student", data, sid(app))
        put(db, app.state.settings.tenant_id, ATTEMPT, "independent-pending-attempt", command_id, "demo-student",
            {"status": "pending", "send_marker": now(), "command_id": command_id, "error": None}, sid(app))
    path = group_url(group, "/commands/" + command_id)
    for _ in range(2):
        receipt = client.get(path)
        assert receipt.status_code == 200 and receipt.json()["status"] == "pending" and receipt.json()["can_retry"]
    assert len(chat.sends) == 1
    response = client.post(path + "/retry", json={}, headers=student)
    assert response.status_code == 200 and response.json()["status"] == "delivered"
    assert len(chat.sends) == 2 and chat.sends[-1][1] == "stable-interrupted-wire-id"
    assert chat.sends[-1][2] == original.value["text"]
    assert client.post(path + "/retry", json={}, headers=student).status_code == 200
    assert len(chat.sends) == 2


def test_membership_revocation_during_read_withholds_history_and_preserves_original_evidence(client, app, chat):
    student = login(client)
    group = bootstrap(client, app, student)[0]
    result = post(client, student, group).json()
    def revoke():
        with app.state.database.write() as db:
            row = db.get(Record, membership_id(sid(app), "demo-student"))
            row.set({**row.value, "active": False})
    chat.on_messages = revoke
    assert client.get(group_url(group, "/messages")).status_code == 403
    assert client.get(group_url(group, "/commands/" + result["id"])).status_code == 403
    with app.state.database.factory() as db:
        assert db.get(Record, key(RECEIPT, result["id"])).value["status"] == "delivered"


def guarded_writes(app, monkeypatch):
    state = {"writing": False}
    original = app.state.database.write
    @contextmanager
    def write():
        with original() as db:
            assert not state["writing"]
            state["writing"] = True
            try:
                yield db
            finally:
                state["writing"] = False
    monkeypatch.setattr(app.state.database, "write", write)
    return state


def test_core_chat_io_occurs_after_local_write_transaction_commits(client, app, chat, monkeypatch):
    state = guarded_writes(app, monkeypatch)
    observed = []
    for name in ("create_group", "send", "messages", "list_groups"):
        original = getattr(chat, name)
        def checked(*args, _method=original, _name=name, **kwargs):
            assert state["writing"] is False
            observed.append(_name)
            return _method(*args, **kwargs)
        monkeypatch.setattr(chat, name, checked)
    student = login(client)
    group = bootstrap(client, app, student)[0]
    response = post(client, student, group)
    assert response.status_code == 200 and response.json()["status"] == "delivered"
    assert client.get(group_url(group, "/messages")).status_code == 200
    assert observed == ["create_group", "send", "messages"]


def test_broader_ambient_school_role_does_not_lend_publication_authority(client, app, chat, monkeypatch):
    teacher = login(client, "demo-instructor")
    group = create(client, app, teacher, course_id="demo-course", visibility="class")
    original = app.state.auth.actor
    def aggregate_role(request):
        identity = original(request)
        if identity["id"] == "demo-instructor":
            identity["role"] = "school"
            identity["permissions"] = ["academy-use", "academy-school", "academy-teach"]
        return identity
    monkeypatch.setattr(app.state.auth, "actor", aggregate_role)
    current = client.get(group_url(group))
    assert current.status_code == 200 and current.json()["can_manage"] and not current.json()["can_review"]
    assert client.get(group_url(group, "/proposals")).status_code == 403


@pytest.mark.parametrize("field,value", [
    ("actor_id", "66666666-6666-4666-8666-666666666666"), ("actor_kind", "user"),
    ("origin_issuer", "https://foreign.example.test"), ("via_installation_id", None),
    ("agent_name", "Spoofed teacher"), ("reply_preview", "Unadmitted private quote"),
])
def test_mutating_upstream_attribution_or_quote_never_changes_student_receipt(client, app, chat, field, value):
    student = login(client)
    group = bootstrap(client, app, student)[0]
    original = post(client, student, group).json()
    room = chat.sends[0][0]
    chat.events[room][0][field] = value
    response = client.get(group_url(group, "/messages"))
    assert response.status_code == 200 and response.json()["messages"] == []
    assert "Unadmitted private quote" not in response.text
    assert client.get(group_url(group, "/commands/" + original["id"])).json()["text"] == "My own question about the class."


def agent_group(client, app, chat, teacher_agent):
    data = teacher_agent
    # The local authentication fixture supplies an explicit synthetic binding;
    # bind its consumer to this adapter's configured receiving service as Core
    # would require in production, so approval tests reach live-claim checks.
    app.state.settings.core_service_id = data["identity"]["consumer_service_id"]
    group = create(client, app, data["headers"], course_id=data["course"]["id"], visibility="class",
                   idempotency_key="agent-class-group")
    proposed = post(client, data["headers"], group, text="Teacher evidence proposed for human review.", key_value="agent-proposal-key")
    assert proposed.status_code == 202, proposed.text
    assert proposed.json()["status"] == "pending_review" and chat.sends == []
    return group, proposed.json()


def binding_claims(app, data, payload, **changes):
    identity = data["identity"]
    clock = int(time.time())
    return {"active": True, "principal_kind": "agent", "iss": app.state.settings.core_issuer,
            "aud": "urn:core:service:" + app.state.settings.core_service_id,
            "organization_id": app.state.settings.core_organization_id,
            "agent_id": identity["account_id"], "sub": "urn:core:agent:" + identity["account_id"],
            "binding_id": identity["binding_id"], "consumer_service_id": identity["consumer_service_id"],
            "environment": identity["environment"], "task_id": identity["task_id"],
            "iat": clock, "exp": clock + 50, "permissions": ["academy-use", "academy-teach"],
            **{name: payload[name] for name in ("permission", "resource_type", "resource_id")}, **changes}


def test_bound_agent_cannot_publish_until_human_approval_and_current_binding(client, app, chat, teacher_agent, monkeypatch):
    group, proposed = agent_group(client, app, chat, teacher_agent)
    path = group_url(group, "/proposals/" + proposed["proposal_id"] + "/review")
    assert client.post(path, json={"decision": "approve"}, headers=teacher_agent["headers"]).status_code == 403
    transaction = guarded_writes(app, monkeypatch)
    def authorize(path, payload, purpose=""):
        assert transaction["writing"] is False
        assert path == "/api/platform/v1/agent-bindings/authorize/"
        assert "token" not in payload
        return binding_claims(app, teacher_agent, payload)
    monkeypatch.setattr(app.state.auth, "core_agent", authorize)
    admin = login(client, "demo-admin")
    response = client.post(path, json={"decision": "approve"}, headers=admin)
    assert response.status_code == 200, response.text
    assert response.json()["status"] == "delivered"
    assert chat.sends[-1][3:] == (True, "Synthetic Teacher", None)
    with app.state.database.factory() as db:
        receipt = db.get(Record, key(RECEIPT, app.state.settings.tenant_id, group["id"], response.json()["message_id"]))
        assert receipt.value["human_approval"] and receipt.value["reviewer"]["id"] == "demo-admin"
        assert receipt.value["author"]["principal_kind"] == "agent"
    assert client.get("/api/audit/verify").json()["valid"] is True


def test_revoked_agent_placement_blocks_approval_without_upstream_send(client, app, chat, teacher_agent, monkeypatch):
    group, proposed = agent_group(client, app, chat, teacher_agent)
    with app.state.database.write() as db:
        row = db.get(Record, teacher_agent["placement"]["id"])
        row.set({**row.value, "active": False})
    monkeypatch.setattr(app.state.auth, "core_agent", lambda *args: pytest.fail("Revoked placement must fail before Core"))
    admin = login(client, "demo-admin")
    response = client.post(group_url(group, "/proposals/" + proposed["proposal_id"] + "/review"),
                           json={"decision": "approve"}, headers=admin)
    assert response.status_code in {403, 409} and chat.sends == []


@pytest.mark.parametrize("claim", [
    {"active": False}, {"active": 1}, {"binding_id": str(uuid4())}, {"permissions": ["academy-use"]},
    {"permissions": ["academy-use", "academy-teach", "admin"]}, {"exp": 0},
    {"permissions": [{"unexpected": "nested"}]},
])
def test_original_agent_live_authority_is_required_even_with_admin_reviewer(client, app, chat, teacher_agent, monkeypatch, claim):
    group, proposed = agent_group(client, app, chat, teacher_agent)
    calls = []
    def authorize(path, payload, purpose=""):
        calls.append(path)
        return binding_claims(app, teacher_agent, payload, **claim)
    monkeypatch.setattr(app.state.auth, "core_agent", authorize)
    admin = login(client, "demo-admin")
    response = client.post(group_url(group, "/proposals/" + proposed["proposal_id"] + "/review"),
                           json={"decision": "approve"}, headers=admin)
    assert response.status_code in {403, 409} and chat.sends == []
    assert calls == ["/api/platform/v1/agent-bindings/authorize/"]
