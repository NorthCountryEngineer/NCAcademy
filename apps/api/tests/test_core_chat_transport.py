"""Exact reviewed Core Chat wire fixtures; no real Core or paid calls."""
import json
from types import SimpleNamespace

import httpx
import pytest

from academy.core_chat_transport import CoreChatFailure, CoreChatTransport, MAX_PAGE_BYTES


ISSUER = "https://core.example.test"
ORG = "11111111-1111-4111-8111-111111111111"
SERVICE = "22222222-2222-4222-8222-222222222222"
INSTALL = "33333333-3333-4333-8333-333333333333"
ROOM = "44444444-4444-4444-8444-444444444444"
MSG = "55555555-5555-4555-8555-555555555555"
OTHER = "66666666-6666-4666-8666-666666666666"
TOKEN = "cw_" + "synthetic-not-live-credential-" * 2


def message(**changes):
    return {"id": MSG, "cursor": 42, "text": "Consider the evidence.", "actor_kind": "application", "actor_id": INSTALL,
            "origin_issuer": ISSUER, "via_installation_id": INSTALL, "agent_name": "", "sender_name": "Application",
            "created": "2026-10-18T20:00:00+00:00", "reply_to": None, "reply_preview": "", **changes}


def page(**changes):
    return {"organization_id": ORG, "conversation_id": ROOM, "authority_issuer": ISSUER,
            "messages": [message()], "next_cursor": 42, **changes}


def sent(**changes):
    return {"organization_id": ORG, "conversation_id": ROOM, "authority_issuer": ISSUER,
            "message": message(), "created": True, **changes}


def group(**changes):
    return {"id": ROOM, "title": "Class group", "ai_enabled": True, "shared": False, "kind": "group", "topic": "Evidence discussion", **changes}


def integration(**changes):
    return {"version": 1, "issuer": ISSUER, "organization_id": ORG, "service_id": SERVICE,
            "audience": "urn:core:service:" + SERVICE, "environment": "production",
            "core_services_url": ISSUER + "/api/platform/v1", "chat_url": ISSUER + "/api/platform/v1/chat",
            "capabilities": ["chat.read", "chat.send", "chat.manage", "chat.ai"],
            "services": {"core-chat": {"service_id": OTHER, "version": "1.0.0"}}, **changes}


@pytest.fixture
def wire():
    state = {"calls": [], "status": 200, "value": page(), "headers": {}}
    def respond(request):
        state["calls"].append(request)
        if state.get("error"):
            raise httpx.ReadTimeout(TOKEN, request=request)
        if "raw" in state:
            return httpx.Response(state["status"], content=state["raw"], headers={"content-type": "application/json", **state["headers"]})
        return httpx.Response(state["status"], json=state["value"], headers=state["headers"])
    settings = SimpleNamespace(auth_mode="core", core_chat_enabled=True, core_chat_installation_id=INSTALL,
                               core_issuer=ISSUER, core_organization_id=ORG, core_service_id=SERVICE,
                               core_environment="production", core_workload_token=TOKEN)
    with httpx.Client(transport=httpx.MockTransport(respond), follow_redirects=False, trust_env=False) as client:
        adapter = CoreChatTransport(SimpleNamespace(http=client), settings)
        yield adapter, state, settings


def failure(call, code=None, uncertain=None):
    with pytest.raises(CoreChatFailure) as captured:
        call()
    error = captured.value
    assert TOKEN not in str(error) and TOKEN not in repr(error)
    if code:
        assert error.code == code
    if uncertain is not None:
        assert error.uncertain is uncertain
    return error


def test_exact_read_binding_cursor_and_app_credential_only(wire):
    adapter, state, _ = wire
    result = adapter.messages(ROOM, after=9, limit=50)
    assert result == page()
    request = state["calls"][0]
    assert str(request.url) == ISSUER + "/api/platform/v1/chat/conversations/" + ROOM + "/messages/?after=9&limit=50"
    assert request.headers["authorization"] == "Bearer " + TOKEN
    assert "x-core-user-token" not in request.headers and "cookie" not in request.headers
    assert "secret" not in request.url.query.decode() and request.method == "GET"
    # Inaccessible tagged rows can advance the cursor without visible messages.
    state["value"] = page(messages=[], next_cursor=90)
    assert adapter.messages(ROOM, after=42)["next_cursor"] == 90


def test_create_is_fixed_group_app_owned_no_participants_and_no_retries(wire):
    adapter, state, _ = wire
    state.update(status=201, value={"organization_id": ORG, "conversation_id": ROOM})
    assert adapter.create_group(" Class group ", "Evidence discussion")["conversation_id"] == ROOM
    assert json.loads(state["calls"][0].content) == {"title": "Class group", "topic": "Evidence discussion", "kind": "group", "ai_enabled": True}
    assert "participants" not in state["calls"][0].url.path
    state["error"] = True
    failure(lambda: adapter.create_group("Other group"), "unavailable", True)
    assert len(state["calls"]) == 2


def test_send_app_attribution_exact_body_reply_and_repeat_receipt(wire):
    adapter, state, _ = wire
    state.update(status=201, value=sent(message=message(reply_to=OTHER, reply_preview="Earlier evidence")))
    result = adapter.send(ROOM, "student-command-123", "Consider the evidence.", reply_to=OTHER)
    assert result["message"]["actor_kind"] == "application"
    assert json.loads(state["calls"][0].content) == {"request_id": "student-command-123", "text": "Consider the evidence.", "ai": False, "agent_name": "", "reply_to": OTHER}
    state.update(status=200, value=sent(created=False))
    assert adapter.send(ROOM, "student-command-123", "Consider the evidence.")["created"] is False
    assert all("x-core-user-token" not in request.headers for request in state["calls"])


def test_ai_send_is_application_attributed_with_exact_approved_label(wire):
    adapter, state, _ = wire
    state.update(status=201, value=sent(message=message(actor_kind="ai", agent_name="Approved Earth Science Teacher", sender_name="Approved Earth Science Teacher")))
    assert adapter.send(ROOM, "approved-publication-1", "Consider the evidence.", ai=True,
                        agent_name="Approved Earth Science Teacher")["message"]["actor_id"] == INSTALL


@pytest.mark.parametrize("field,value", [
    ("actor_kind", "user"), ("actor_id", OTHER), ("via_installation_id", OTHER), ("origin_issuer", "https://foreign.example.test"),
    ("text", "Unrequested text"), ("agent_name", "Spoofed teacher"), ("reply_to", OTHER),
    ("cursor", True), ("cursor", 0), ("created", "2026-10-18"), ("reply_preview", "x" * 241),
    ("actor_kind", []), ("text", "\ud800"),
])
def test_send_mismatch_is_uncertain_never_exposes_unverified_receipt(wire, field, value):
    adapter, state, _ = wire
    state.update(status=201, value=sent(message=message(**{field: value})))
    # JSON can represent a lone surrogate; HTTPX json encoder refuses it, so use
    # an escaped raw response for that hostile authority fixture.
    if value == "\ud800":
        state["raw"] = json.dumps(state["value"]).encode()
    failure(lambda: adapter.send(ROOM, "command-1", "Consider the evidence."), "invalid_response", True)
    assert len(state["calls"]) == 1


@pytest.mark.parametrize("changes", [
    {"organization_id": OTHER}, {"conversation_id": OTHER}, {"authority_issuer": "https://foreign.example.test"},
    {"next_cursor": True}, {"next_cursor": 41}, {"messages": [message(cursor=41), message(cursor=40)]},
    {"messages": [message(), message()]}, {"messages": [message(text="x" * 16385)]},
    {"messages": [message(actor_kind={})]}, {"messages": [message(id="not-a-uuid")]},
    {"messages": [message(created="2026-10-18T20:00:00")]}, {"messages": [message(cursor=0)]},
    {"messages": [message(unrecognized="hidden-field")]},
])
def test_page_shape_and_cursor_fail_closed(wire, changes):
    adapter, state, _ = wire
    state["value"] = page(**changes)
    failure(lambda: adapter.messages(ROOM), "invalid_response", False)


def test_list_recovery_validates_every_descriptor_and_fixed_limit(wire):
    adapter, state, _ = wire
    state["value"] = {"organization_id": ORG, "conversations": [group()]}
    assert adapter.list_groups()["conversations"][0]["id"] == ROOM
    state["value"] = {"organization_id": ORG, "conversations": [group(), group()]}
    failure(adapter.list_groups, "invalid_response")
    state["value"] = {"organization_id": ORG, "conversations": [group(kind=[])]}
    failure(adapter.list_groups, "invalid_response")
    state["value"] = {"organization_id": ORG, "conversations": [group(id=f"{i:08x}-1111-4111-8111-111111111111") for i in range(201)]}
    failure(adapter.list_groups, "invalid_response")


def test_discovery_binds_current_service_environment_and_explicit_capabilities(wire):
    adapter, state, _ = wire
    state["value"] = integration()
    result = adapter.discovery(require_ai=True)
    assert result["service_id"] == SERVICE and result["chat_service_id"] == OTHER
    assert "installation_id" not in result  # Core has no such handshake field.
    state["value"]["capabilities"].remove("chat.ai")
    assert "chat.read" in adapter.discovery()["capabilities"]
    failure(lambda: adapter.discovery(require_ai=True), "invalid_response")


@pytest.mark.parametrize("changes", [
    {"organization_id": OTHER}, {"service_id": OTHER}, {"issuer": "https://foreign.example.test"},
    {"audience": "urn:foreign"}, {"environment": "staging"}, {"chat_url": ISSUER + "/unreviewed"},
    {"core_services_url": "https://foreign.example.test/api/platform/v1"}, {"capabilities": ["chat.read", "chat.send"]},
    {"services": {}}, {"version": True}, {"capabilities": [{"secret": TOKEN}]},
])
def test_discovery_mismatch_and_missing_grants(wire, changes):
    adapter, state, _ = wire
    state["value"] = integration(**changes)
    failure(adapter.discovery, "invalid_response")


@pytest.mark.parametrize("field,value", [
    ("auth_mode", "local"), ("core_chat_enabled", False), ("core_chat_enabled", "true"),
    ("core_chat_installation_id", "local-owner-hash"), ("core_workload_token", ""),
    ("core_workload_token", "ca_" + "a" * 50), ("core_issuer", "http://localhost:9000"),
    ("core_issuer", "https://core.example.test/path"), ("core_issuer", "https://user:password@core.example.test"),
    ("core_issuer", "https://[invalid"), ("core_issuer", "https://core.example.test:notaport"),
    ("core_environment", []), ("core_organization_id", OTHER.upper().replace("666", "ABC", 1)),
])
def test_disabled_or_invalid_config_makes_no_network_call(wire, field, value):
    adapter, state, settings = wire
    setattr(settings, field, value)
    failure(lambda: adapter.messages(ROOM), "disabled", False)
    assert state["calls"] == []


@pytest.mark.parametrize("call", [
    lambda a: a.messages("../../oauth/token"), lambda a: a.messages(ROOM, after=True),
    lambda a: a.messages(ROOM, limit=201), lambda a: a.messages(ROOM, after=-1),
    lambda a: a.send(ROOM, "request with spaces", "hello"), lambda a: a.send(ROOM, "id", "x" * 16385),
    lambda a: a.send(ROOM, "id", "🦎" * 4097), lambda a: a.send(ROOM, "id", "\ud800"),
    lambda a: a.send(ROOM, "id", "hello", ai="true"), lambda a: a.send(ROOM, "id", "hello", ai=True),
    lambda a: a.send(ROOM, "id", "hello", agent_name="fake human"),
    lambda a: a.send(ROOM, "id", "hello", reply_to="local-record-id"),
    lambda a: a.create_group(" "), lambda a: a.create_group("class", "x" * 501),
    lambda a: a._request("POST", "/api/platform/v1/chat/conversations/" + ROOM + "/participants/", payload={"user_id": OTHER}),
])
def test_invalid_inputs_never_send(wire, call):
    adapter, state, _ = wire
    failure(lambda: call(adapter), "invalid_request", False)
    assert state["calls"] == []


@pytest.mark.parametrize("status,uncertain", [(301, True), (302, True), (307, True), (400, False), (401, False),
                                                   (403, False), (404, False), (409, False), (429, False), (500, True), (503, True)])
def test_errors_are_static_no_redirect_retry_or_credential_detail(wire, status, uncertain):
    adapter, state, _ = wire
    state.update(status=status, value={"error": TOKEN}, headers={"location": "https://untrusted.example.test/receive"})
    error = failure(lambda: adapter.create_group("Class"), uncertain=uncertain)
    assert error.code == ("idempotency_conflict" if status == 409 else error.code)
    assert len(state["calls"]) == 1


@pytest.mark.parametrize("raw", [b"[]", b"{bad", b'{"organization_id":"first","organization_id":"second"}', b'{"invalid":NaN}', b"\xff"])
def test_malformed_json_is_bounded_sanitized_and_creation_uncertain(wire, raw):
    adapter, state, _ = wire
    state.update(status=201, raw=raw)
    failure(lambda: adapter.create_group("Class"), uncertain=True)
    assert len(state["calls"]) == 1


def test_response_bytes_content_type_and_total_time_bounds(wire, monkeypatch):
    adapter, state, _ = wire
    state["raw"] = b"x" * (MAX_PAGE_BYTES + 1)
    failure(lambda: adapter.messages(ROOM), "invalid_response")
    state.pop("raw")
    state["headers"] = {"content-type": "text/html"}
    failure(lambda: adapter.messages(ROOM), "invalid_response")
    state["headers"] = {}
    clock = iter([1, 28])
    monkeypatch.setattr("academy.core_chat_transport.time", SimpleNamespace(monotonic=lambda: next(clock)))
    failure(lambda: adapter.messages(ROOM), "invalid_response")


def test_timeout_has_no_retry_and_does_not_retain_sensitive_upstream_exception(wire):
    adapter, state, _ = wire
    state["error"] = True
    error = failure(lambda: adapter.send(ROOM, "command", "Consider the evidence."), "unavailable", True)
    assert error.__suppress_context__ is True and error.__context__ is None and len(state["calls"]) == 1
