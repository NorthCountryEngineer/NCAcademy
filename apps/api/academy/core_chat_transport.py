"""Bounded, application-attributed Core Chat transport.

The application is the only upstream participant. School membership and actual
human/agent attribution belong to the immutable NCAcademy command/receipt layer.
This adapter never accepts a user token, agent token, participant or remote URL.
"""
import json
import re
import time
from datetime import datetime
from urllib.parse import urlsplit
from uuid import UUID

import httpx


CHAT_PATH = "/api/platform/v1/chat"
INTEGRATION_PATH = "/api/platform/v1/integration/"
MAX_TEXT_BYTES = 16384
MAX_PAGE_BYTES = 4 * 1024 * 1024
MAX_RESPONSE_SECONDS = 25
MESSAGE_FIELDS = frozenset({"id", "cursor", "text", "actor_kind", "actor_id", "origin_issuer", "via_installation_id", "agent_name", "sender_name", "created", "reply_to", "reply_preview"})
ACTOR_KINDS = frozenset({"user", "application", "ai", "federated_user", "federated_application", "federated_ai"})


class CoreChatFailure(Exception):
    """Static error metadata only; never retains response bodies or credentials."""

    def __init__(self, code, *, status_code=503, uncertain=False):
        self.code = code
        self.status_code = status_code
        self.uncertain = bool(uncertain)
        super().__init__("Core Chat request could not be admitted or verified")


def _uuid(value):
    try:
        return isinstance(value, str) and str(UUID(value)) == value
    except (ValueError, TypeError, AttributeError):
        return False


def _text(value, maximum, *, blank=False, utf8=False):
    if not isinstance(value, str) or "\0" in value or not blank and not value.strip():
        return False
    try:
        size = len(value.encode("utf-8")) if utf8 else len(value)
        # Reject lone surrogates even in character-bounded metadata.
        if not utf8:
            value.encode("utf-8")
        return size <= maximum
    except UnicodeError:
        return False


def _exact(value, fields):
    return isinstance(value, dict) and set(value) == set(fields)


def _json_pairs(pairs):
    value = {}
    for key, item in pairs:
        if key in value:
            raise ValueError()
        value[key] = item
    return value


class CoreChatTransport:
    def __init__(self, auth, settings):
        self.auth = auth
        self.settings = settings

    def _configuration(self):
        settings = self.settings
        issuer = getattr(settings, "core_issuer", "")
        try:
            parsed = urlsplit(issuer) if isinstance(issuer, str) else None
        except ValueError:
            raise CoreChatFailure("disabled", status_code=503) from None
        token = getattr(settings, "core_workload_token", "")
        if (getattr(settings, "auth_mode", None) != "core" or getattr(settings, "core_chat_enabled", False) is not True
                or not parsed or parsed.scheme != "https" or not parsed.hostname or parsed.path or parsed.query or parsed.fragment
                or parsed.username is not None or parsed.password is not None
                or not _uuid(getattr(settings, "core_organization_id", None))
                or not _uuid(getattr(settings, "core_service_id", None))
                or not _uuid(getattr(settings, "core_chat_installation_id", None))
                or not isinstance(getattr(settings, "core_environment", None), str)
                or settings.core_environment not in {"sandbox", "development", "staging", "uat", "production"}
                or not isinstance(token, str) or not re.fullmatch(r"cw_[A-Za-z0-9_-]{37,253}", token)):
            raise CoreChatFailure("disabled", status_code=503)
        # Force malformed ports to fail before HTTPX can format an exception.
        try:
            parsed.port
        except ValueError:
            raise CoreChatFailure("disabled", status_code=503) from None
        return issuer, token

    def _request(self, method, path, *, payload=None, params=None, maximum=65536):
        issuer, token = self._configuration()
        allowed = (path == INTEGRATION_PATH and method == "GET"
                   or path == CHAT_PATH + "/conversations/" and method == "GET"
                   or path == CHAT_PATH + "/conversations/create/" and method == "POST"
                   or re.fullmatch(re.escape(CHAT_PATH) + r"/conversations/[0-9a-f-]{36}/messages/", path) and method == "GET"
                   or re.fullmatch(re.escape(CHAT_PATH) + r"/conversations/[0-9a-f-]{36}/send/", path) and method == "POST")
        if not allowed:
            raise CoreChatFailure("invalid_request", status_code=422)
        mutation = method == "POST"
        started = time.monotonic()
        try:
            # Auth.http is the existing no-cookie, trust_env=False, nonredirecting
            # identity client. Explicit auth prevents inherited Basic credentials.
            with self.auth.http.stream(method, issuer + path,
                    headers={"Authorization": "Bearer " + token, "Accept": "application/json"},
                    auth=None, json=payload, params=params, follow_redirects=False,
                    timeout=httpx.Timeout(15, connect=3, pool=1)) as response:
                accepted = {200, 201} if path.endswith("/send/") else {201} if mutation else {200}
                if response.status_code not in accepted:
                    status = response.status_code
                    if status in {400, 401, 403, 404, 405, 409, 413, 415, 422, 429}:
                        code = "idempotency_conflict" if status == 409 else "denied" if status in {401, 403, 404} else "invalid_request" if status in {400, 405, 413, 415, 422} else "unavailable"
                        raise CoreChatFailure(code, status_code=status if status in {400, 401, 403, 404, 409, 422} else 503)
                    raise CoreChatFailure("unavailable", uncertain=mutation)
                content_type = response.headers.get("content-type", "").split(";", 1)[0].strip().lower()
                declared = response.headers.get("content-length")
                if content_type != "application/json" or declared is not None and (not declared.isdigit() or int(declared) > maximum):
                    raise CoreChatFailure("invalid_response", uncertain=mutation)
                body = bytearray()
                for chunk in response.iter_bytes():
                    body.extend(chunk)
                    if len(body) > maximum or time.monotonic() - started > MAX_RESPONSE_SECONDS:
                        raise CoreChatFailure("invalid_response", uncertain=mutation)
                value = json.loads(bytes(body).decode("utf-8"), object_pairs_hook=_json_pairs,
                                   parse_constant=lambda _: (_ for _ in ()).throw(ValueError()))
                if not isinstance(value, dict):
                    raise ValueError()
                return value
        except CoreChatFailure:
            raise
        except (httpx.HTTPError, OSError, ValueError, UnicodeError, RecursionError):
            pass
        # Raise after leaving the exception handler: even __context__ must not
        # retain an HTTP exception containing a credential or response payload.
        raise CoreChatFailure("unavailable", uncertain=mutation) from None

    def _invalid_response(self, mutation=False):
        raise CoreChatFailure("invalid_response", uncertain=mutation)

    def _room(self, value):
        if not _uuid(value):
            raise CoreChatFailure("invalid_request", status_code=422)
        return value

    def discovery(self, *, require_ai=False):
        if type(require_ai) is not bool:
            raise CoreChatFailure("invalid_request", status_code=422)
        value = self._request("GET", INTEGRATION_PATH)
        settings = self.settings
        required = {"chat.read", "chat.send", "chat.manage"} | ({"chat.ai"} if require_ai else set())
        capabilities = value.get("capabilities")
        services = value.get("services")
        chat_service = services.get("core-chat") if isinstance(services, dict) else None
        if (value.get("version") != 1 or type(value.get("version")) is not int
                or value.get("issuer") != settings.core_issuer
                or value.get("organization_id") != settings.core_organization_id
                or value.get("service_id") != settings.core_service_id
                or value.get("audience") != "urn:core:service:" + settings.core_service_id
                or value.get("environment") != settings.core_environment
                or value.get("core_services_url") != settings.core_issuer + "/api/platform/v1"
                or value.get("chat_url") != settings.core_issuer + CHAT_PATH
                or not isinstance(capabilities, list) or len(capabilities) > 128
                or any(not _text(item, 100) for item in capabilities)
                or len(set(capabilities)) != len(capabilities) or not required <= set(capabilities)
                or not isinstance(services, dict) or len(services) > 100
                or not isinstance(chat_service, dict) or not _uuid(chat_service.get("service_id"))
                or not _text(chat_service.get("version"), 100)):
            self._invalid_response()
        # Deliberately project only validated public contract fields. Core's
        # discovery has no installation_id; send receipts prove that separately.
        return {"version": 1, "issuer": settings.core_issuer, "organization_id": settings.core_organization_id,
                "service_id": settings.core_service_id, "environment": settings.core_environment,
                "capabilities": capabilities, "chat_service_id": chat_service["service_id"]}

    def create_group(self, title, topic=""):
        if not _text(title, 160) or not _text(topic, 500, blank=True):
            raise CoreChatFailure("invalid_request", status_code=422)
        value = self._request("POST", CHAT_PATH + "/conversations/create/",
                              payload={"title": title.strip(), "topic": topic, "kind": "group", "ai_enabled": True})
        if (not _exact(value, {"organization_id", "conversation_id"})
                or value["organization_id"] != self.settings.core_organization_id
                or not _uuid(value["conversation_id"])):
            self._invalid_response(True)
        return value

    def list_groups(self):
        value = self._request("GET", CHAT_PATH + "/conversations/", maximum=1048576)
        rows = value.get("conversations")
        if (not _exact(value, {"organization_id", "conversations"})
                or value["organization_id"] != self.settings.core_organization_id
                or not isinstance(rows, list) or len(rows) > 200):
            self._invalid_response()
        seen = set()
        for row in rows:
            if (not _exact(row, {"id", "title", "ai_enabled", "shared", "kind", "topic"}) or not _uuid(row["id"])
                    or row["id"] in seen or not _text(row["title"], 160) or not _text(row["topic"], 500, blank=True)
                    or type(row["ai_enabled"]) is not bool or type(row["shared"]) is not bool
                    or not isinstance(row["kind"], str) or row["kind"] not in {"channel", "direct", "group"}):
                self._invalid_response()
            seen.add(row["id"])
        return value

    def _message(self, value):
        if (not _exact(value, MESSAGE_FIELDS) or not _uuid(value["id"])
                or type(value["cursor"]) is not int or not 1 <= value["cursor"] <= 9223372036854775807
                or not _text(value["text"], MAX_TEXT_BYTES, utf8=True)
                or not isinstance(value["actor_kind"], str) or value["actor_kind"] not in ACTOR_KINDS or not _text(value["actor_id"], 160)
                or not re.fullmatch(r"[-a-zA-Z0-9_:./]{1,160}", value["actor_id"])
                or value["origin_issuer"] != self.settings.core_issuer
                or value["via_installation_id"] is not None and not _uuid(value["via_installation_id"])
                or not _text(value["agent_name"], 160, blank=True) or not _text(value["sender_name"], 500)
                or value["reply_to"] is not None and not _uuid(value["reply_to"])
                or not _text(value["reply_preview"], 240, blank=True)
                or not _text(value["created"], 64)):
            return False
        try:
            return datetime.fromisoformat(value["created"]).utcoffset() is not None
        except (ValueError, OverflowError):
            return False

    def messages(self, conversation_id, after=0, limit=100):
        conversation_id = self._room(conversation_id)
        if (type(after) is not int or not 0 <= after <= 9223372036854775807
                or type(limit) is not int or not 1 <= limit <= 200):
            raise CoreChatFailure("invalid_request", status_code=422)
        value = self._request("GET", CHAT_PATH + f"/conversations/{conversation_id}/messages/",
                              params={"after": after, "limit": limit}, maximum=MAX_PAGE_BYTES)
        rows, cursor = value.get("messages"), value.get("next_cursor")
        if (not _exact(value, {"organization_id", "conversation_id", "authority_issuer", "messages", "next_cursor"})
                or value["organization_id"] != self.settings.core_organization_id or value["conversation_id"] != conversation_id
                or value["authority_issuer"] != self.settings.core_issuer
                or type(cursor) is not int or not after <= cursor <= 9223372036854775807
                or not isinstance(rows, list) or len(rows) > limit):
            self._invalid_response()
        previous, seen = after, set()
        for row in rows:
            if not self._message(row) or not previous < row["cursor"] <= cursor or row["id"] in seen:
                self._invalid_response()
            previous = row["cursor"]
            seen.add(row["id"])
        return value

    def send(self, conversation_id, request_id, text, *, ai=False, agent_name="", reply_to=None):
        conversation_id = self._room(conversation_id)
        if (not isinstance(request_id, str) or not re.fullmatch(r"[-a-zA-Z0-9_:./]{1,160}", request_id)
                or not _text(text, MAX_TEXT_BYTES, utf8=True) or type(ai) is not bool
                or not _text(agent_name, 160, blank=True) or ai and not agent_name.strip() or not ai and agent_name
                or reply_to is not None and not _uuid(reply_to)):
            raise CoreChatFailure("invalid_request", status_code=422)
        payload = {"request_id": request_id, "text": text, "ai": ai, "agent_name": agent_name}
        if reply_to is not None:
            payload["reply_to"] = reply_to
        value = self._request("POST", CHAT_PATH + f"/conversations/{conversation_id}/send/", payload=payload, maximum=131072)
        row = value.get("message")
        if (not _exact(value, {"organization_id", "conversation_id", "authority_issuer", "message", "created"})
                or value["organization_id"] != self.settings.core_organization_id or value["conversation_id"] != conversation_id
                or value["authority_issuer"] != self.settings.core_issuer or type(value["created"]) is not bool
                or not self._message(row) or row["actor_kind"] != ("ai" if ai else "application")
                or row["actor_id"] != self.settings.core_chat_installation_id
                or row["via_installation_id"] != self.settings.core_chat_installation_id
                or row["text"] != text or row["agent_name"] != agent_name or row["reply_to"] != reply_to):
            self._invalid_response(True)
        return value
