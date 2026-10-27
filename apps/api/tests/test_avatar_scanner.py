"""Benign synthetic pixels only; no real student or unsafe image is used."""
import base64
from copy import deepcopy
import hashlib
from io import BytesIO
import json

from PIL import Image
import pytest

from academy import avatar_scanner, provider


def image(**options):
    output = BytesIO()
    Image.new("RGB", (32, 24), "green").save(output, format="JPEG", **options)
    raw = output.getvalue()
    return {"mime_type": "image/jpeg", "data_base64": base64.b64encode(raw).decode(),
            "sha256": hashlib.sha256(raw).hexdigest(), "width": 32, "height": 24,
            "byte_length": len(raw)}


def connection():
    return {"id": "synthetic-connection", "protocol": "openai_compatible", "enabled": True,
            "endpoint": "https://scanner.example/v1", "models": ["synthetic-vision"]}


def test_exact_prompt_contains_only_fixed_rubric_and_normalized_image():
    avatar = image()
    prompt = avatar_scanner.build_avatar_prompt(avatar)
    assert prompt == avatar_scanner.build_avatar_prompt(deepcopy(avatar))
    assert prompt[0] == {"role": "system", "content": avatar_scanner.SYSTEM_PROMPT}
    assert prompt[1]["content"][1] == {"type": "image_url", "image_url": {
        "url": "data:image/jpeg;base64," + avatar["data_base64"], "detail": "high"}}
    assert set(prompt[1]) == {"role", "content"}
    assert all(key not in json.dumps(prompt) for key in ("student_id", "filename", "school_id", "biography", "api_key"))
    assert "untrusted content" in prompt[0]["content"]
    assert "Do not infer identity" in prompt[0]["content"]
    assert "Do not describe people" in prompt[0]["content"]
    assert "uncertain to true" in prompt[0]["content"]


def test_scan_delegates_once_to_existing_protected_transport(monkeypatch):
    calls = []
    result = {"choices": [{"message": {"content": "synthetic private result"}}]}
    monkeypatch.setattr(provider, "classify", lambda *args: calls.append(args) or result)
    avatar = image()
    assert avatar_scanner.scan_avatar(connection(), "synthetic-key-only", "synthetic-vision", avatar) is result
    assert len(calls) == 1
    assert calls[0] == (connection(), "synthetic-key-only", "synthetic-vision", avatar_scanner.build_avatar_prompt(avatar), 500)
    assert "synthetic-key-only" not in json.dumps(calls[0][3])


def test_uncertain_provider_failure_is_never_retried(monkeypatch):
    calls = []
    def fail(*args):
        calls.append(args)
        raise provider.ProviderFailure("Synthetic uncertainty")
    monkeypatch.setattr(provider, "classify", fail)
    with pytest.raises(provider.ProviderFailure):
        avatar_scanner.scan_avatar(connection(), "synthetic-key-only", "synthetic-vision", image())
    assert len(calls) == 1


@pytest.mark.parametrize("field,value", [
    ("mime_type", "image/png"), ("mime_type", "https://example.test/avatar.jpg"),
    ("width", 0), ("width", 513), ("width", True), ("width", 12),
    ("height", -1), ("height", 513), ("height", 24.0),
    ("byte_length", 0), ("byte_length", True), ("byte_length", 100000), ("byte_length", 4),
    ("sha256", "0" * 64), ("sha256", "not-a-hash"), ("sha256", None),
    ("data_base64", "not valid base64"), ("data_base64", None),
    ("data_base64", "A" * (128 * 1024 + 4)),
])
def test_rejects_unadmitted_or_changed_image_metadata_before_transport(monkeypatch, field, value):
    calls = []
    monkeypatch.setattr(provider, "classify", lambda *args: calls.append(args))
    avatar = image(); avatar[field] = value
    with pytest.raises(ValueError, match="intact normalized private JPEG"):
        avatar_scanner.scan_avatar(connection(), "synthetic-key-only", "synthetic-vision", avatar)
    assert calls == []


@pytest.mark.parametrize("mutate", [
    lambda value: value.update(filename="PRIVATE_STUDENT_FILENAME"),
    lambda value: value.update(student_id="PRIVATE_STUDENT_ID"),
    lambda value: value.pop("sha256"),
])
def test_rejects_extra_private_metadata_or_incomplete_image(mutate):
    avatar = image(); mutate(avatar)
    with pytest.raises(ValueError):
        avatar_scanner.build_avatar_prompt(avatar)


@pytest.mark.parametrize("options", [
    {"exif": b"Exif\x00\x00"}, {"icc_profile": b"PRIVATE_COLOR_PROFILE"},
    {"comment": b"PRIVATE_METADATA"},
])
def test_rejects_non_normalized_metadata(options):
    avatar = image(**options)
    with pytest.raises(ValueError):
        avatar_scanner.build_avatar_prompt(avatar)


def test_rejects_corrupt_container_even_with_matching_hash():
    avatar = image()
    raw = b"\xff\xd8\xff" + b"synthetic-invalid-image" + b"\xff\xd9"
    avatar.update(data_base64=base64.b64encode(raw).decode(), byte_length=len(raw), sha256=hashlib.sha256(raw).hexdigest())
    with pytest.raises(ValueError):
        avatar_scanner.build_avatar_prompt(avatar)


def test_incomplete_decoder_error_is_redacted_before_transport(monkeypatch):
    avatar = image()
    def incomplete(*args, **kwargs):
        raise EOFError("PRIVATE_DECODER_DETAIL")
    monkeypatch.setattr(avatar_scanner.Image, "open", incomplete)
    with pytest.raises(ValueError, match="intact normalized private JPEG") as failure:
        avatar_scanner.build_avatar_prompt(avatar)
    assert "PRIVATE_DECODER_DETAIL" not in str(failure.value)


@pytest.mark.parametrize("change", [
    {"protocol": "unsupported"}, {"enabled": False}, {"enabled": 1}, {"models": []},
])
def test_requires_exact_enabled_model_connection(monkeypatch, change):
    calls = []
    monkeypatch.setattr(provider, "classify", lambda *args: calls.append(args))
    configured = connection(); configured.update(change)
    with pytest.raises(ValueError):
        avatar_scanner.scan_avatar(configured, "synthetic-key-only", "synthetic-vision", image())
    assert calls == []


@pytest.mark.parametrize("model,key,limit", [
    ("unknown", "synthetic-key-only", 500),
    ("synthetic-vision", "", 500),
    ("synthetic-vision", "synthetic\nkey", 500),
    ("synthetic-vision", "synthetic-key-only", True),
    ("synthetic-vision", "synthetic-key-only", 127),
    ("synthetic-vision", "synthetic-key-only", 1001),
])
def test_rejects_invalid_execution_inputs_before_call(monkeypatch, model, key, limit):
    calls = []
    monkeypatch.setattr(provider, "classify", lambda *args: calls.append(args))
    with pytest.raises(ValueError):
        avatar_scanner.scan_avatar(connection(), key, model, image(), limit)
    assert calls == []
