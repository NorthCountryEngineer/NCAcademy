"""Synthetic voice providers only; no real recordings, credentials or charges."""
import base64
import io
import json
import hashlib
import wave
import socket
from datetime import datetime, timezone

import pytest
from fastapi import HTTPException
from sqlalchemy import event, select

from academy import voice_provider
from academy.db import Audit, Record, canonical, uid
from academy.permissions import bind_scope
from academy.voice_api import _json_value, normalized_wav
from conftest import login
from test_lesson_tutoring import ask, lesson_environment


STOCK = "JBFqnCBsd6RMkjVDRZzb"
SECRET = "synthetic-voice-key-never-live"


def wav(seconds=1, rate=16000, channels=1):
    result = io.BytesIO()
    with wave.open(result, "wb") as output:
        output.setnchannels(channels)
        output.setsampwidth(2)
        output.setframerate(rate)
        output.writeframes(b"\0\0" * rate * seconds * channels)
    return base64.b64encode(result.getvalue()).decode()


def environment(client, app, monkeypatch, *, bound=False, **configuration):
    monkeypatch.setattr(voice_provider, "stock_available", lambda: True)
    env = lesson_environment(client, app, monkeypatch, bound=bound)
    admin = login(client, "demo-admin")
    config = {"version": 0, "enabled": True, "voice_ids": [STOCK], "api_key": SECRET, "provider_retention_acknowledged": True, **configuration}
    response = client.put("/api/admin/voice", params={"school_id": env["school"]}, json=config, headers=admin)
    assert response.status_code == 200, response.text
    env["voice_config"] = response.json()
    env["headers"] = login(client)
    selected = client.put(path(env, "voice-preference"), json={"teacher_profile_id": env["profile"]["id"], "voice_id": STOCK, "expected_version": 0}, headers=env["headers"])
    assert selected.status_code == 200, selected.text
    calls = []
    monkeypatch.setattr(app.state, "voice_verify_stock", lambda key, voice_id: {"id": voice_id, "name": "George", "category": "premade", "verification": "synthetic"})
    monkeypatch.setattr(app.state, "voice_transcribe", lambda *args: calls.append(("transcription", args)) or "How does carbon move through an ecosystem?")
    monkeypatch.setattr(app.state, "voice_synthesize", lambda *args: calls.append(("speech", args)) or b"ID3synthetic-audio")
    env["voice_calls"] = calls
    return env


def path(env, suffix="voice"):
    return f"/api/courses/{env['course']['id']}/{suffix}"


def transcription(client, env, key="transcription-synthetic-123", **extra):
    return client.post(path(env, "voice/transcriptions"), json={"teacher_profile_id": env["profile"]["id"], "idempotency_key": key, "consent": True, "audio_base64": wav(), **extra}, headers=env["headers"])


def speech(client, env, turn_id, key="speech-synthetic-123", **extra):
    return client.post(path(env, "voice/speech"), json={"teacher_profile_id": env["profile"]["id"], "idempotency_key": key, "consent": True, "turn_id": turn_id, **extra}, headers=env["headers"])


def test_voice_config_credential_write_only_school_admin_and_stock_only(client, app, monkeypatch):
    env = environment(client, app, monkeypatch)
    assert SECRET not in str(env["voice_config"])
    assert env["voice_config"]["key_configured"] is True
    assert client.get("/api/admin/voice", params={"school_id": env["school"]}).status_code == 403
    admin = login(client, "demo-admin")
    for ids in (["owner-private-clone"], [STOCK, STOCK]):
        result = client.put("/api/admin/voice", params={"school_id": env["school"]}, json={"version": 1, "voice_ids": ids}, headers=admin)
        assert result.status_code == 422
    invalid = client.put("/api/admin/voice", params={"school_id": env["school"]}, json={"version": 1, "enabled": True, "voice_ids": [STOCK]}, headers=admin)
    assert invalid.status_code == 422
    with app.state.database.factory() as db:
        stored = db.scalar(select(Record).where(Record.kind == "voice_connection"))
        assert SECRET not in stored.data
        assert app.state.auth.unseal(stored.value["key_cipher"])["api_key"] == SECRET
        assert SECRET not in "".join(db.scalars(select(Audit.payload)))


def test_transcript_is_reviewable_idempotent_audited_without_recording(client, app, monkeypatch):
    env = environment(client, app, monkeypatch)
    result = transcription(client, env)
    assert result.status_code == 200, result.text
    body = result.json()
    assert body["status"] == "released" and body["transcript"].startswith("How does carbon")
    assert transcription(client, env).json() == body
    assert len(env["voice_calls"]) == 1 and len(env["calls"]) == 0
    lookup = client.get(path(env, "voice/operations"), params={"teacher_profile_id": env["profile"]["id"], "idempotency_key": "transcription-synthetic-123"})
    assert lookup.json() == body
    assert transcription(client, env, audio_base64=wav(2)).status_code == 409
    with app.state.database.factory() as db:
        stored = db.get(Record, body["id"]).value
        assert stored["send_marker"] and stored["recording"]["duration_ms"] == 1000
        assert stored["snapshot"]["learning_preferences"]["effective_weights"]["audio"] == 50
        assert "audio_base64" not in str(stored) and SECRET not in str(stored)
        audit = "".join(db.scalars(select(Audit.payload)))
        assert body["transcript"] in audit and wav() not in audit and SECRET not in audit


@pytest.mark.parametrize("recording", ["A" * 100, wav(31), wav(channels=2), wav(rate=4000)])
def test_recording_bounds_fail_before_send(client, app, monkeypatch, recording):
    env = environment(client, app, monkeypatch)
    assert transcription(client, env, audio_base64=recording).status_code == 422
    assert env["voice_calls"] == []


def test_mic_consent_intake_and_live_gates_fail_before_send(client, app, monkeypatch):
    env = environment(client, app, monkeypatch)
    assert transcription(client, env, consent=False).status_code == 422
    app.state.settings.allow_live_models = False
    assert transcription(client, env).status_code == 403
    app.state.settings.allow_live_models = True
    reset = client.post("/api/me/learning-preferences/reset", json={"expected_version": 1}, headers=env["headers"])
    assert reset.status_code == 200
    assert transcription(client, env).status_code == 409
    assert env["voice_calls"] == []


def test_voice_selection_personal_and_optimistically_versioned(client, app, monkeypatch):
    env = environment(client, app, monkeypatch)
    params = {"teacher_profile_id": env["profile"]["id"]}
    assert client.get(path(env), params=params).json()["selected_voice_id"] == STOCK
    conflict = client.put(path(env, "voice-preference"), json={"teacher_profile_id": env["profile"]["id"], "voice_id": None, "expected_version": 0}, headers=env["headers"])
    assert conflict.status_code == 409
    assert client.put(path(env, "voice-preference"), json={"teacher_profile_id": env["profile"]["id"], "voice_id": "owner-private-clone", "expected_version": 1}, headers=env["headers"]).status_code == 422
    cleared = client.put(path(env, "voice-preference"), json={"teacher_profile_id": env["profile"]["id"], "voice_id": None, "expected_version": 1}, headers=env["headers"])
    assert cleared.status_code == 200 and cleared.json()["selected_voice_id"] is None


def test_speech_uses_exact_released_text_and_protected_audio(client, app, monkeypatch):
    env = environment(client, app, monkeypatch)
    turn = ask(client, env).json()
    result = speech(client, env, turn["id"])
    assert result.status_code == 200, result.text
    receipt = result.json()
    assert receipt["status"] == "released"
    assert env["voice_calls"] == [("speech", (SECRET, STOCK, turn["approved_text"], False))]
    audio = client.get(receipt["audio_url"])
    assert audio.status_code == 200 and audio.content == b"ID3synthetic-audio"
    assert audio.headers["cache-control"] == "no-store"
    assert speech(client, env, turn["id"]).json() == receipt and len(env["voice_calls"]) == 1
    assert speech(client, env, turn["id"], text="Write my essay").status_code == 422
    with app.state.database.factory() as db:
        stored = db.get(Record, receipt["id"]).value
        assert "ID3synthetic-audio" not in str(stored)
        assert "audio_cipher" in stored
        assert "audio_cipher" not in "".join(db.scalars(select(Audit.payload)))
    login(client, "demo-student2")
    assert client.get(receipt["audio_url"]).status_code in {403, 404}


def test_stock_metadata_failure_never_sends_teaching_text(client, app, monkeypatch):
    env = environment(client, app, monkeypatch)
    turn = ask(client, env).json()
    monkeypatch.setattr(app.state, "voice_verify_stock", lambda *_: {"id": STOCK, "category": "cloned"})
    body = speech(client, env, turn["id"]).json()
    assert body["status"] == "withheld" and env["voice_calls"] == []
    with app.state.database.factory() as db:
        assert db.get(Record, body["id"]).value["send_marker"] is None


def test_uncertain_provider_outcome_is_sanitized_and_never_resent(client, app, monkeypatch):
    env = environment(client, app, monkeypatch)
    calls = []
    def uncertain(*_):
        calls.append(1)
        raise RuntimeError("synthetic private vendor body " + SECRET)
    monkeypatch.setattr(app.state, "voice_transcribe", uncertain)
    first = transcription(client, env).json()
    assert first["status"] == "withheld" and SECRET not in str(first)
    assert transcription(client, env).json() == first and calls == [1]
    with app.state.database.factory() as db:
        assert db.get(Record, first["id"]).value["uncertain"] is True
        assert SECRET not in "".join(db.scalars(select(Audit.payload)))


def test_preference_change_during_transcription_withholds_result(client, app, monkeypatch):
    env = environment(client, app, monkeypatch)
    def concurrent(*_):
        result = client.put(path(env, "voice-preference"), json={"teacher_profile_id": env["profile"]["id"], "voice_id": None, "expected_version": 1}, headers=env["headers"])
        assert result.status_code == 200
        return "A transcript that must be withheld"
    monkeypatch.setattr(app.state, "voice_transcribe", concurrent)
    result = transcription(client, env).json()
    assert result["status"] == "withheld" and result["transcript"] is None


def test_revoked_teacher_blocks_receipt_and_playback(client, app, monkeypatch):
    env = environment(client, app, monkeypatch, bound=True)
    turn = ask(client, env).json()
    result = speech(client, env, turn["id"]).json()
    assert result["status"] == "released"
    monkeypatch.setattr(app.state, "authorize_teacher", lambda *_: {"active": False})
    assert client.get(path(env, "voice/operations/" + result["id"])).json()["status"] == "withheld"
    assert client.get(result["audio_url"]).status_code == 403


def test_voice_quota_counts_both_directions_across_teacher_operations(client, app, monkeypatch):
    env = environment(client, app, monkeypatch, max_daily_operations=1)
    assert transcription(client, env).json()["status"] == "released"
    turn = ask(client, env).json()
    assert speech(client, env, turn["id"]).status_code == 403
    assert len(env["voice_calls"]) == 1


def test_exact_stock_metadata_never_uses_account_list_or_private_id(monkeypatch):
    monkeypatch.setattr(voice_provider, "stock_available", lambda: True)
    calls = []
    monkeypatch.setattr(voice_provider, "request", lambda *args, **kwargs: calls.append((args, kwargs)) or json.dumps({"voice_id": STOCK, "category": "premade"}).encode())
    assert voice_provider.verify_stock(SECRET, STOCK)["category"] == "premade"
    assert calls == [(("GET", "/v1/voices/" + STOCK), {"key": SECRET})]
    for value in ({"voice_id": STOCK, "category": "cloned"}, {"voice_id": STOCK, "category": "premade", "is_owner": True}, {"voice_id": STOCK, "category": "premade", "is_mixed": True}, {"voice_id": "foreign", "category": "premade"}):
        monkeypatch.setattr(voice_provider, "request", lambda *args, **kwargs: json.dumps(value).encode())
        with pytest.raises(voice_provider.ProviderFailure):
            voice_provider.verify_stock(SECRET, STOCK)
    with pytest.raises(voice_provider.ProviderFailure):
        voice_provider.verify_stock(SECRET, "owner-private-clone")


def test_wav_rewrite_strips_metadata_and_rejects_malformed_length():
    encoded = wav()
    clean, metadata = normalized_wav(encoded)
    assert metadata["bytes"] == len(clean) and metadata["duration_ms"] == 1000
    with pytest.raises(HTTPException):
        normalized_wav(base64.b64encode(base64.b64decode(encoded) + b"hidden metadata").decode())


def test_provider_wire_contract_has_fixed_models_retention_and_no_transcript_edit(monkeypatch):
    calls = []
    def request(*args, **kwargs):
        calls.append((args, kwargs))
        return b'{"text":"A question to review"}' if args[1] == "/v1/speech-to-text" else b"ID3synthetic-audio"
    monkeypatch.setattr(voice_provider, "request", request)
    assert voice_provider.transcribe(SECRET, b"bounded synthetic WAV", True) == "A question to review"
    assert calls[0] == (("POST", "/v1/speech-to-text"), {"key": SECRET, "params": {"enable_logging": "false"}, "files": {"file": ("question.wav", b"bounded synthetic WAV", "audio/wav")}, "data": {"model_id": "scribe_v2", "diarize": "false", "tag_audio_events": "false", "timestamps_granularity": "none"}})
    assert voice_provider.synthesize(SECRET, STOCK, "Exact approved course passage", False) == b"ID3synthetic-audio"
    args, kwargs = calls[1]
    assert args == ("POST", "/v1/text-to-speech/" + STOCK)
    assert kwargs["params"] == {"enable_logging": "true", "output_format": "mp3_44100_128"}
    assert kwargs["payload"]["text"] == "Exact approved course passage"
    assert kwargs["payload"]["model_id"] == "eleven_multilingual_v2"
    assert "previous_text" not in kwargs["payload"] and "pronunciation_dictionary_locators" not in kwargs["payload"]


def test_provider_transcript_never_echoes_credential_or_unbounded_text(monkeypatch):
    for text in (SECRET, "x" * 4001, "", None):
        monkeypatch.setattr(voice_provider, "request", lambda *args, **kwargs: json.dumps({"text": text}).encode())
        with pytest.raises(voice_provider.ProviderFailure):
            voice_provider.transcribe(SECRET, b"synthetic")


def test_stock_retirement_stops_selection_synthesis_and_playback(client, app, monkeypatch):
    env = environment(client, app, monkeypatch)
    turn = ask(client, env).json()
    result = speech(client, env, turn["id"]).json()
    assert result["status"] == "released"
    monkeypatch.setattr(voice_provider, "stock_available", lambda: False)
    state = client.get(path(env), params={"teacher_profile_id": env["profile"]["id"]}).json()
    assert state["voices"] == [] and state["selected_voice_id"] is None
    assert speech(client, env, turn["id"], key="retired-voice-new-request").status_code == 409
    assert client.get(result["audio_url"]).status_code == 409
    assert client.get(path(env, "voice/operations/" + result["id"])).json()["status"] == "withheld"
    assert len(env["voice_calls"]) == 1


def test_enrollment_revocation_blocks_operation_lookup_and_recording(client, app, monkeypatch):
    env = environment(client, app, monkeypatch)
    result = transcription(client, env).json()
    with app.state.database.write() as db:
        enrollment = db.scalar(select(Record).where(Record.kind == "enrollment", Record.parent_id == env["course"]["id"], Record.owner_id == "demo-student"))
        enrollment.set({"role": "teacher"})
    assert client.get(path(env, "voice/operations/" + result["id"])).status_code == 403
    assert transcription(client, env, key="revoked-enrollment-new-request").status_code == 403


def test_voice_connection_rotation_withholds_inflight_and_never_retries(client, app, monkeypatch):
    env = environment(client, app, monkeypatch)
    def rotate(*_):
        with app.state.database.write() as db:
            row = db.scalar(select(Record).where(Record.kind == "voice_connection"))
            row.set({**row.value, "version": row.value["version"] + 1})
        return "Must not be released after rotation"
    monkeypatch.setattr(app.state, "voice_transcribe", rotate)
    result = transcription(client, env).json()
    assert result["status"] == "withheld" and result["transcript"] is None
    assert transcription(client, env).json() == result


def test_voice_preferences_and_completed_operations_are_immutable(client, app, monkeypatch):
    env = environment(client, app, monkeypatch)
    result = transcription(client, env).json()
    for kind in ("student_voice_preference", "voice_operation"):
        with pytest.raises(ValueError):
            with app.state.database.write() as db:
                row = db.scalar(select(Record).where(Record.kind == kind))
                row.set({**row.value, "changed": True})


def test_default_catalog_has_a_hard_utc_retirement_boundary(monkeypatch):
    class Frozen(datetime):
        value = datetime(2026, 12, 30, 23, 59, tzinfo=timezone.utc)
        @classmethod
        def now(cls, tz=None):
            return cls.value
    monkeypatch.setattr(voice_provider, "datetime", Frozen)
    assert voice_provider.stock_available() is True
    Frozen.value = datetime(2026, 12, 31, tzinfo=timezone.utc)
    assert voice_provider.stock_available() is False


def test_metadata_paths_are_static_and_unknown_paths_cannot_become_sql():
    from types import SimpleNamespace
    from sqlalchemy.dialects import postgresql, sqlite
    for dialect in (postgresql.dialect(), sqlite.dialect()):
        db = SimpleNamespace(get_bind=lambda: SimpleNamespace(dialect=dialect))
        query = select(_json_value(db, "idempotency_key"))
        assert query.compile(dialect=dialect).params == {}
        assert "'idempotency_key'" in str(query.compile(dialect=dialect)) or "'$.idempotency_key'" in str(query.compile(dialect=dialect))
        with pytest.raises(ValueError, match="Unknown voice metadata path"):
            _json_value(db, "idempotency_key'); DROP TABLE records; --")


def test_voice_transport_rejects_any_private_dns_answer(monkeypatch):
    monkeypatch.setattr(socket, "getaddrinfo", lambda *_args, **_kwargs: [(socket.AF_INET, socket.SOCK_STREAM, 6, "", (address, 443)) for address in ("8.8.8.8", "127.0.0.1")])
    with pytest.raises(voice_provider.ProviderFailure):
        voice_provider.request("POST", "/v1/speech-to-text", key=SECRET)


@pytest.mark.parametrize("status,body,maximum", [(200, b"bounded result", 20), (302, b"redirect", 20), (429, b"private vendor message", 100), (200, b"oversized", 2)])
def test_voice_transport_pins_tls_and_never_redirects_retries_or_exposes_vendor_error(monkeypatch, status, body, maximum):
    monkeypatch.setattr(socket, "getaddrinfo", lambda *_args, **_kwargs: [(socket.AF_INET, socket.SOCK_STREAM, 6, "", ("8.8.8.8", 443))])
    captured = []
    class Response:
        status_code = status
        def __enter__(self): return self
        def __exit__(self, *_): pass
        def iter_bytes(self): yield body
    class Client:
        def __init__(self, **kwargs):
            assert kwargs["trust_env"] is False and kwargs["follow_redirects"] is False
        def __enter__(self): return self
        def __exit__(self, *_): pass
        def stream(self, method, url, **kwargs):
            assert method == "POST" and url.host == "8.8.8.8"
            assert kwargs["headers"] == {"Host": "api.elevenlabs.io", "xi-api-key": SECRET}
            assert kwargs["extensions"]["sni_hostname"] == "api.elevenlabs.io"
            captured.append(kwargs)
            return Response()
    monkeypatch.setattr(voice_provider.httpx, "Client", Client)
    if status == 200 and len(body) <= maximum:
        assert voice_provider.request("POST", "/v1/speech-to-text", key=SECRET, maximum=maximum) == body
    else:
        with pytest.raises(voice_provider.ProviderFailure) as failure:
            voice_provider.request("POST", "/v1/speech-to-text", key=SECRET, maximum=maximum)
        assert SECRET not in str(failure.value) and body.decode() not in str(failure.value)
    assert len(captured) == 1


def test_old_encrypted_audio_is_never_bulk_loaded_for_new_operation_or_key_lookup(client, app, monkeypatch):
    env = environment(client, app, monkeypatch)
    old_id = uid()
    with app.state.database.write() as db:
        old = Record(id=old_id, tenant_id=app.state.settings.tenant_id, kind="voice_operation", parent_id=env["course"]["id"], owner_id="demo-student", data=canonical({"status": "released", "kind": "speech", "teacher_profile_id": env["profile"]["id"], "idempotency_key": "old-encrypted-recording-123", "budget_day": "2025-01-01", "created_at": "2025-01-01T00:00:00+00:00", "recording": {}, "text": "Old teaching text", "audio_cipher": "old-encrypted-audio" * 100000}), created_at="2025-01-01T00:00:00+00:00")
        db.add(old)
        db.flush()
        bind_scope(db, old, env["school"])
    loaded_ids = []
    def loaded(target, context):
        loaded_ids.append(target.id)
    event.listen(Record, "load", loaded)
    try:
        result = transcription(client, env)
        assert result.status_code == 200 and result.json()["status"] == "released"
        assert transcription(client, env).json() == result.json()
        lookup = client.get(path(env, "voice/operations"), params={"teacher_profile_id": env["profile"]["id"], "idempotency_key": "transcription-synthetic-123"})
        assert lookup.json() == result.json()
        assert old_id not in loaded_ids
    finally:
        event.remove(Record, "load", loaded)


def test_scalar_recording_budget_counts_all_voices_and_exact_replay_is_free(client, app, monkeypatch):
    env = environment(client, app, monkeypatch, max_daily_recording_seconds=30)
    first = transcription(client, env, audio_base64=wav(30))
    assert first.status_code == 200 and first.json()["status"] == "released"
    assert transcription(client, env, audio_base64=wav(30)).json() == first.json()
    assert transcription(client, env, key="recording-budget-second-question").status_code == 403
    assert len(env["voice_calls"]) == 1


def test_session_replacement_before_voice_send_withholds_without_provider_call(client, app, monkeypatch):
    env = environment(client, app, monkeypatch)
    original = app.state.auth.actor
    calls = []
    def changed(request):
        result = original(request)
        calls.append(1)
        return {**result, "login_id": "replaced-session"} if len(calls) > 1 else result
    monkeypatch.setattr(app.state.auth, "actor", changed)
    result = transcription(client, env).json()
    assert result["status"] == "withheld" and env["voice_calls"] == []


@pytest.mark.parametrize("corruption", ["hash", "length", "cipher", "base64", "format"])
def test_retained_audio_integrity_is_checked_before_release_or_playback_audit(client, app, monkeypatch, corruption):
    env = environment(client, app, monkeypatch)
    turn = ask(client, env).json()
    result = speech(client, env, turn["id"]).json()
    broken_id = uid()
    with app.state.database.write() as db:
        data = db.get(Record, result["id"]).value
        if corruption == "hash":
            data["audio_sha256"] = "0" * 64
        elif corruption == "length":
            data["audio_bytes"] += 1
        elif corruption == "cipher":
            data["audio_cipher"] = "corrupted encrypted clip"
        elif corruption == "base64":
            data["audio_cipher"] = app.state.auth.seal({"audio_base64": "not valid base64"})
        else:
            invalid = b"unexpected returned content"
            data.update(audio_cipher=app.state.auth.seal({"audio_base64": base64.b64encode(invalid).decode()}), audio_bytes=len(invalid), audio_sha256=hashlib.sha256(invalid).hexdigest())
        broken = Record(id=broken_id, tenant_id=app.state.settings.tenant_id, kind="voice_operation", parent_id=env["course"]["id"], owner_id="demo-student", data=canonical(data))
        db.add(broken)
        db.flush()
        bind_scope(db, broken, env["school"])
    response = client.get(path(env, "voice/operations/" + broken_id + "/audio"))
    assert response.status_code == 409 and response.json()["detail"] == "Stored teacher audio failed integrity verification"
    with app.state.database.factory() as db:
        assert not [item for item in db.scalars(select(Audit).where(Audit.event_type == "voice.playback_accessed")) if json.loads(item.payload)["operation_id"] == broken_id]
