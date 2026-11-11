"""Bounded, single-send classifier transport. Provider prose is never rendered."""
import ipaddress
import hashlib
import hmac
import json
import socket
import time
from urllib.parse import urlsplit

import httpx


class ProviderFailure(Exception):
    pass


# Exact, reviewed OpenAI model IDs only. A compatible endpoint or an unknown
# model name must keep its own existing request contract. See the dated review
# in docs/openai-chat-compatibility.md before extending this list.
OPENAI_REASONING_EFFORT = {
    "gpt-6-luna": "none",
    "gpt-6-sol": "none",
    "gpt-6.1-terra": "none",
    "gpt-6.1-sol": "low",
    "gpt-6-astra": "low",
}


def _chat_payload(parsed, model, prompt, max_output_tokens, processing_tier="standard"):
    if processing_tier not in {"standard", "fast"}:
        raise ValueError("Unsupported provider processing tier")
    payload = {"model": model, "messages": prompt, "temperature": 0,
               "max_tokens": max_output_tokens, "stream": False, "store": False}
    if processing_tier == "fast":
        if parsed.hostname != "api.openai.com" or parsed.path != "/v1":
            raise ValueError("Fast processing requires the official OpenAI endpoint")
        payload["service_tier"] = "fast"
    effort = OPENAI_REASONING_EFFORT.get(model)
    if parsed.hostname == "api.openai.com" and parsed.path == "/v1" and effort is not None:
        # This is the same approved cap, including any reasoning tokens. Never
        # add a separate reasoning allowance or retry a truncated selection.
        payload["max_completion_tokens"] = payload.pop("max_tokens")
        payload["reasoning_effort"] = effort
        if effort != "none":
            payload.pop("temperature")
    return payload


def endpoint(value):
    parsed = urlsplit(value)
    if parsed.scheme != "https" or not parsed.hostname or not parsed.hostname.isascii() or parsed.username or parsed.password or parsed.query or parsed.fragment or parsed.port not in (None, 443) or len(parsed.path) > 200:
        raise ValueError("Connection endpoint must be an HTTPS URL on port 443 without credentials, query or fragment")
    if parsed.hostname in {"localhost", "metadata.google.internal"}:
        raise ValueError("Public provider hostname required")
    try:
        if not ipaddress.ip_address(parsed.hostname).is_global:
            raise ValueError("Public provider hostname required")
    except ValueError as error:
        if "Public provider" in str(error):
            raise
    return value.rstrip("/")


def classify(connection, api_key, model, prompt, max_output_tokens):
    """Resolve once, reject every private answer, pin chosen IP and TLS SNI.

    httpcore uses sni_hostname for both hostname verification and SNI when the
    connection URL contains the pinned address. No redirects, environment proxy,
    model discovery, cookies, tools, retries or streaming response to the learner.
    """
    parsed = urlsplit(endpoint(connection["endpoint"]))
    if "public_homework_contract" in connection:
        # An independent final wire guard: a redacted generic prompt, a raw
        # photo, a child identifier or an extra field can never use this path.
        from .homework_deidentification import build_public_homework_prompt
        try:
            contract = connection["public_homework_contract"]
            expected_keys = {"task", "action", "stage", "phase"}
            if type(contract) is not dict or not expected_keys <= set(contract) <= expected_keys | {"candidate", "include_image"}:
                raise ValueError()
            if (connection["endpoint"] != "https://api.openai.com/v1"
                    or any(name in connection for name in ("openai_organization", "openai_project", "safety_identifier", "openai_inference_key_sha256"))
                    or prompt != build_public_homework_prompt(**contract)):
                raise ValueError()
        except (ValueError, TypeError, KeyError, RecursionError):
            raise ProviderFailure("Public Homework Help request failed its local privacy boundary; nothing was sent") from None
    started = time.monotonic()
    try:
        answers = socket.getaddrinfo(parsed.hostname, 443, type=socket.SOCK_STREAM)
        addresses = sorted({entry[4][0] for entry in answers})
        if not addresses or any(not ipaddress.ip_address(address).is_global for address in addresses):
            raise ProviderFailure("Provider endpoint is not publicly routable")
        target = httpx.URL(connection["endpoint"] + "/chat/completions").copy_with(host=addresses[0])
        with httpx.Client(follow_redirects=False, trust_env=False, timeout=httpx.Timeout(20, connect=5), transport=httpx.HTTPTransport(retries=0, trust_env=False)) as client:
            headers = {"Host": parsed.hostname, "Authorization": "Bearer " + api_key, "Content-Type": "application/json"}
            payload = _chat_payload(parsed, model, prompt, max_output_tokens, connection.get("processing_tier", "standard"))
            if any(name in connection for name in ("openai_organization", "openai_project", "safety_identifier", "openai_inference_key_sha256")):
                import re
                if (parsed.hostname != "api.openai.com" or parsed.path != "/v1"
                        or not re.fullmatch(r"[A-Za-z0-9_.-]{1,160}", connection.get("openai_organization", ""))
                        or not re.fullmatch(r"proj_[A-Za-z0-9_-]{1,160}", connection.get("openai_project", ""))
                        or not re.fullmatch(r"academy_[a-f0-9]{64}", connection.get("safety_identifier", ""))
                        or not re.fullmatch(r"[a-f0-9]{64}", connection.get("openai_inference_key_sha256", ""))
                        or not hmac.compare_digest(connection["openai_inference_key_sha256"], hashlib.sha256(api_key.encode()).hexdigest())):
                    raise ValueError("Invalid OpenAI student request context")
                headers.update({"OpenAI-Organization": connection["openai_organization"], "OpenAI-Project": connection["openai_project"]})
                payload["safety_identifier"] = connection["safety_identifier"]
            with client.stream("POST", target, headers=headers, json=payload, extensions={"sni_hostname": parsed.hostname}) as response:
                raw = bytearray()
                for chunk in response.iter_bytes():
                    if time.monotonic() - started > 30:
                        raise ProviderFailure("Provider response deadline exceeded; outcome may be billable")
                    raw.extend(chunk)
                    if len(raw) > 65536:
                        raise ProviderFailure("Provider response exceeded bound; outcome may be billable")
                if response.status_code != 200:
                    raise ProviderFailure("Provider returned an unsuccessful response; no retry")
                try:
                    result = json.loads(raw)
                except (ValueError, RecursionError):
                    raise ProviderFailure("Provider returned malformed JSON") from None
                return result
    except (httpx.HTTPError, OSError, ValueError):
        raise ProviderFailure("Provider request failed; outcome may be billable; no retry") from None
