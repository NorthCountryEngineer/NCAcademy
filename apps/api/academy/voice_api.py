"""Optional student-controlled speech around already approved teaching text."""
import base64
import binascii
import hashlib
import io
import wave
from datetime import datetime, timezone

from fastapi import Depends, HTTPException, Request
from fastapi.responses import Response
from pydantic import BaseModel, ConfigDict, Field, StrictBool, model_validator
from sqlalchemy import JSON, cast, func, literal_column, select

from . import voice_provider
from .db import Record, RecordScope, canonical, now, uid
from .learning_preferences import preference_snapshot
from .lesson_api import authorize_lesson_teacher, course_for_student, lesson_session_id, protect_released_lesson, resolve_lesson_runtime
from .permissions import bind_scope, default_school_id, require_school
from .teacher_api import resolve_lesson_context


MAX_AUDIO_BYTES = 3 * 1024 * 1024
MAX_AUDIO_BASE64 = 4 * ((MAX_AUDIO_BYTES + 2) // 3)
MAX_SECONDS = 30
VOICE_OPERATION_KIND = literal_column("'voice_operation'")
_JSON_PATHS = {
    ("text_characters",): ("'$.text_characters'", ("'text_characters'",)),
    ("idempotency_key",): ("'$.idempotency_key'", ("'idempotency_key'",)),
    ("teacher_profile_id",): ("'$.teacher_profile_id'", ("'teacher_profile_id'",)),
    ("status",): ("'$.status'", ("'status'",)),
    ("budget_day",): ("'$.budget_day'", ("'budget_day'",)),
    ("text",): ("'$.text'", ("'text'",)),
    ("recording", "duration_ms"): ("'$.recording.duration_ms'", ("'recording'", "'duration_ms'")),
}


class VoiceConfigurationInput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    version: int = Field(ge=0, strict=True)
    enabled: StrictBool = False
    voice_ids: list[str] = Field(default_factory=list, max_length=2)
    api_key: str | None = Field(default=None, min_length=12, max_length=512)
    clear_key: StrictBool = False
    zero_retention: StrictBool = False
    provider_retention_acknowledged: StrictBool = False
    max_daily_operations: int = Field(default=20, ge=1, le=50, strict=True)
    max_daily_recording_seconds: int = Field(default=300, ge=30, le=900, strict=True)
    max_daily_speech_characters: int = Field(default=12000, ge=1000, le=30000, strict=True)

    @model_validator(mode="after")
    def valid(self):
        if len(set(self.voice_ids)) != len(self.voice_ids) or set(self.voice_ids) - set(voice_provider.STOCK_VOICES):
            raise ValueError("Select only the documented stock voices")
        if self.api_key and (self.clear_key or any(character.isspace() for character in self.api_key)):
            raise ValueError("Use one credential replacement or clear action")
        if self.enabled and (not self.voice_ids or not self.zero_retention and not self.provider_retention_acknowledged):
            raise ValueError("Enabling voice requires stock voices and an explicit provider-retention choice")
        return self


class VoicePreferenceInput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    teacher_profile_id: str = Field(min_length=1, max_length=64)
    voice_id: str | None = Field(max_length=32)
    expected_version: int = Field(ge=0, strict=True)


class VoiceOperationInput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    teacher_profile_id: str = Field(min_length=1, max_length=64)
    idempotency_key: str = Field(min_length=12, max_length=100, pattern=r"^[A-Za-z0-9_-]+$")
    consent: StrictBool


class TranscriptionInput(VoiceOperationInput):
    audio_base64: str = Field(min_length=60, max_length=MAX_AUDIO_BASE64)


class SpeechInput(VoiceOperationInput):
    turn_id: str = Field(min_length=1, max_length=64)


def normalized_wav(encoded):
    """Accept bounded PCM WAV only; never store the student's raw recording."""
    try:
        recording = base64.b64decode(encoded, validate=True)
        if len(recording) > MAX_AUDIO_BYTES or recording[:4] != b"RIFF" or recording[8:12] != b"WAVE" or int.from_bytes(recording[4:8], "little") + 8 != len(recording):
            raise ValueError()
        with wave.open(io.BytesIO(recording), "rb") as source:
            rate, frames = source.getframerate(), source.getnframes()
            if source.getnchannels() != 1 or source.getsampwidth() != 2 or source.getcomptype() != "NONE" or not 8000 <= rate <= 48000 or not rate // 10 <= frames <= rate * MAX_SECONDS:
                raise ValueError()
            samples = source.readframes(frames + 1)
            if len(samples) != frames * 2:
                raise ValueError()
        # Rewrite only PCM frames, dropping optional source metadata chunks.
        target = io.BytesIO()
        with wave.open(target, "wb") as output:
            output.setnchannels(1)
            output.setsampwidth(2)
            output.setframerate(rate)
            output.writeframes(samples)
        clean = target.getvalue()
        return clean, {"sha256": hashlib.sha256(clean).hexdigest(), "bytes": len(clean), "duration_ms": (frames * 1000 + rate - 1) // rate, "sample_rate": rate, "mime_type": "audio/wav"}
    except (ValueError, binascii.Error, wave.Error, EOFError, OverflowError):
        raise HTTPException(422, "Use a mono PCM16 WAV recording between 0.1 and 30 seconds, at 8–48 kHz") from None


def configuration_id(tenant_id, school_id):
    return "voice:" + hashlib.sha256(canonical([tenant_id, school_id]).encode()).hexdigest()[:48]


def configuration_descriptor(row):
    defaults = VoiceConfigurationInput(version=0).model_dump(exclude={"api_key", "clear_key"})
    return {**defaults, **({key: value for key, value in row.value.items() if key != "key_cipher"} if row else {}), "key_configured": bool(row and row.value.get("key_cipher")), "provider": "elevenlabs", "catalog_version": "documented-defaults-2026-10", "catalog": [{"id": key, "name": name, "category": "premade", "retirement_date": voice_provider.STOCK_RETIREMENT.isoformat()} for key, name in voice_provider.STOCK_VOICES.items()]}


def voice_preference(db, identity, profile_id):
    rows = db.scalars(select(Record).where(Record.tenant_id == identity["tenant_id"], Record.kind == "student_voice_preference", Record.owner_id == identity["id"], Record.parent_id == profile_id))
    return max(rows, key=lambda row: row.value["version"], default=None)


def _json_value(db, *path, integer=False):
    # SQLite CAST(text AS JSON) yields a number, unlike PostgreSQL's JSON cast.
    # Keep these scalar metadata lookups on the actual JSON document.
    if path not in _JSON_PATHS:
        raise ValueError("Unknown voice metadata path")
    sqlite_path, postgres_path = _JSON_PATHS[path]
    if db.get_bind().dialect.name == "sqlite":
        return func.json_extract(Record.data, literal_column(sqlite_path))
    value = cast(Record.data, JSON)
    for key in postgres_path:
        value = value[literal_column(key)]
    return value.as_integer() if integer else value.as_string()


def register_voice_api(app, database, auth, settings, actor, audit_actor):
    app.state.voice_verify_stock = voice_provider.verify_stock
    app.state.voice_transcribe = voice_provider.transcribe
    app.state.voice_synthesize = voice_provider.synthesize

    def config(db, identity, school_id):
        return db.get(Record, configuration_id(identity["tenant_id"], school_id))

    def resolve(db, identity, course_id, profile_id, *, live=False):
        if live:
            course, school_id, context, policy, model_connection, _ = resolve_lesson_runtime(db, identity, course_id, profile_id, settings)
        else:
            course, school_id = course_for_student(db, identity, course_id, settings)
            context = resolve_lesson_context(db, identity, course, school_id, settings, profile_id)
            model_connection = None
        learning = preference_snapshot(db, identity, profile_id, course_id)
        row = config(db, identity, school_id)
        data = configuration_descriptor(row)
        selected = voice_preference(db, identity, profile_id)
        preference = {"revision_id": selected.id if selected else None, "version": selected.value["version"] if selected else 0, "voice_id": selected.value["voice_id"] if selected else None}
        if live and (not row or not data["enabled"] or not data["key_configured"]):
            raise HTTPException(403, "Your school has not enabled voice")
        snapshot = {"teacher_context": context, "learning_preferences": learning, "voice_configuration": {key: value for key, value in data.items() if key != "catalog"}, "voice_preference": preference, "model_connection": model_connection}
        if row:
            snapshot["voice_configuration"]["credential_fingerprint"] = hashlib.sha256(row.value.get("key_cipher", "").encode()).hexdigest()
        return course, school_id, context, row, snapshot

    def student_descriptor(db, identity, course_id, profile_id):
        _, _, context, row, snapshot = resolve(db, identity, course_id, profile_id)
        configured = configuration_descriptor(row)
        selected = snapshot["voice_preference"]
        enabled = bool(configured["enabled"] and configured["key_configured"])
        available = bool(enabled and settings.allow_live_models and (context["teacher_mode"] != "core_agent" or settings.allow_agent_teachers))
        return {"enabled": enabled, "available": available, "voices": [{"id": key, "name": voice_provider.STOCK_VOICES[key]} for key in configured["voice_ids"] if key in voice_provider.STOCK_VOICES] if enabled and voice_provider.stock_available() else [], "selected_voice_id": selected["voice_id"] if selected["voice_id"] in configured["voice_ids"] and voice_provider.stock_available() else None, "preference_version": selected["version"], "max_recording_seconds": MAX_SECONDS, "audio_mime_type": "audio/wav", "provider": "ElevenLabs", "provider_zero_retention_requested": configured["zero_retention"], "stock_voice_retirement": voice_provider.STOCK_RETIREMENT.isoformat()}

    @app.get("/api/admin/voice")
    def get_configuration(school_id: str | None = None, identity=Depends(actor)):
        with database.factory() as db:
            sid = school_id or default_school_id(identity["tenant_id"])
            require_school(db, identity, sid, settings, {"admin", "school"}, human=True)
            return {"school_id": sid, **configuration_descriptor(config(db, identity, sid))}

    @app.put("/api/admin/voice")
    def put_configuration(body: VoiceConfigurationInput, school_id: str | None = None, identity=Depends(actor)):
        with database.write() as db:
            sid = school_id or default_school_id(identity["tenant_id"])
            require_school(db, identity, sid, settings, {"admin", "school"}, human=True)
            row = config(db, identity, sid)
            previous = configuration_descriptor(row)
            if previous["version"] != body.version:
                raise HTTPException(409, "Voice configuration changed; reload before saving")
            cipher = auth.seal({"api_key": body.api_key}) if body.api_key else "" if body.clear_key else row.value.get("key_cipher", "") if row else ""
            if body.enabled and not cipher:
                raise HTTPException(422, "An encrypted voice credential is required")
            value = {**body.model_dump(exclude={"api_key", "clear_key"}), "version": body.version + 1, "school_id": sid, "key_cipher": cipher}
            if row:
                row.set(value)
            else:
                row = Record(id=configuration_id(identity["tenant_id"], sid), tenant_id=identity["tenant_id"], kind="voice_connection", parent_id=sid, data=canonical(value))
                db.add(row)
                db.flush()
                bind_scope(db, row, sid)
            result = configuration_descriptor(row)
            audit_actor(db, identity, "voice.configuration_updated", {**result, "credential_action": "replaced" if body.api_key else "cleared" if body.clear_key else "unchanged"})
            return result

    @app.get("/api/courses/{course_id}/voice")
    def get_voice(course_id: str, teacher_profile_id: str, identity=Depends(actor)):
        with database.factory() as db:
            return student_descriptor(db, identity, course_id, teacher_profile_id)

    @app.put("/api/courses/{course_id}/voice-preference")
    def put_preference(course_id: str, body: VoicePreferenceInput, identity=Depends(actor)):
        with database.write() as db:
            _, school_id, _, row, snapshot = resolve(db, identity, course_id, body.teacher_profile_id)
            if body.voice_id is not None and (body.voice_id not in configuration_descriptor(row)["voice_ids"] or not voice_provider.stock_available()):
                raise HTTPException(422, "Select a school-approved stock voice")
            if body.expected_version != snapshot["voice_preference"]["version"]:
                raise HTTPException(409, "Your teacher voice changed; reload before saving")
            value = {"version": body.expected_version + 1, "voice_id": body.voice_id, "created_at": now()}
            record = Record(id=uid(), tenant_id=identity["tenant_id"], kind="student_voice_preference", parent_id=body.teacher_profile_id, owner_id=identity["id"], data=canonical(value))
            db.add(record)
            db.flush()
            bind_scope(db, record, school_id)
            audit_actor(db, identity, "voice.preference_saved", {"revision_id": record.id, "teacher_profile_id": body.teacher_profile_id, "school_id": school_id, **value})
            return student_descriptor(db, identity, course_id, body.teacher_profile_id)

    def speech_text(db, identity, course, school_id, profile_id, turn_id, context):
        row = db.get(Record, turn_id)
        if not row or row.tenant_id != identity["tenant_id"] or row.kind != "lesson_turn" or row.parent_id != course.id or row.owner_id != identity["id"] or row.value.get("teacher_profile_id") != profile_id:
            raise HTTPException(404, "Your released teacher response was not found")
        data = row.value
        if data.get("status") != "released" or protect_released_lesson(db, course, school_id, data).get("status") != "released":
            raise HTTPException(409, "This teaching response is no longer available for playback")
        stored_context = data.get("teacher_context_snapshot", {})
        if any(canonical(stored_context.get(key)) != canonical(context.get(key)) for key in ("profile_id", "approved_revision_id", "approved_hash", "corpus")):
            raise HTTPException(409, "The approved teacher or course corpus changed")
        text = data.get("lesson", {}).get("narration_text") if data.get("activity") == "curated_lesson" else data.get("approved_text")
        if not isinstance(text, str) or not text.strip() or len(text) > 4000:
            raise HTTPException(409, "This response has no bounded read-aloud text")
        return text

    def owned_operation(db, identity, course_id, operation_id=None, key=None, profile_id=None):
        course_for_student(db, identity, course_id, settings)
        if operation_id:
            row = db.get(Record, operation_id)
            if not row or row.tenant_id != identity["tenant_id"] or row.kind != "voice_operation" or row.owner_id != identity["id"] or row.parent_id != course_id:
                raise HTTPException(404, "Voice operation not found")
            return row
        query = select(Record).where(Record.tenant_id == identity["tenant_id"], Record.kind == VOICE_OPERATION_KIND, Record.owner_id == identity["id"], Record.parent_id == course_id)
        if identity.get("auth_provider") == "academy":
            from .pii_storage import lookup_key
            key = lookup_key(db, identity["tenant_id"], identity["id"], key)
        query = query.where(_json_value(db, "idempotency_key") == key, _json_value(db, "teacher_profile_id") == profile_id)
        matches = list(db.scalars(query.limit(2)))
        if not matches:
            raise HTTPException(404, "Voice operation not found")
        if len(matches) > 1:
            raise HTTPException(409, "Voice operation receipt is ambiguous")
        return matches[0]

    def revalidate(db, identity, course_id, data):
        course, school_id, context, row, snapshot = resolve(db, identity, course_id, data["teacher_profile_id"], live=True)
        if canonical(snapshot) != canonical(data["snapshot"]):
            raise HTTPException(409, "Teacher, preferences or voice configuration changed")
        if data["kind"] == "speech" and not voice_provider.stock_available():
            raise HTTPException(409, "The stock voice catalog requires a reviewed update")
        if data["kind"] == "speech" and speech_text(db, identity, course, school_id, data["teacher_profile_id"], data["turn_id"], context) != data["text"]:
            raise HTTPException(409, "The teaching response changed")
        return course, school_id, context, row

    def receipt(db, identity, course_id, row):
        data = row.value
        result = {"id": row.id, "kind": data["kind"], "status": data["status"], "created_at": data["created_at"], "transcript": None, "audio_url": None, "error": data.get("error")}
        if data["status"] == "released":
            try:
                _, school_id, context, _ = revalidate(db, identity, course_id, data)
                authorize_lesson_teacher(app, data["teacher_profile_id"], school_id, course_id, context)
            except HTTPException:
                return {**result, "status": "withheld", "error": "Voice access changed. Ask your teacher for current help."}
            if data["kind"] == "transcription":
                result["transcript"] = data["transcript"]
            else:
                result["audio_url"] = f"/api/courses/{course_id}/voice/operations/{row.id}/audio"
        return result

    @app.get("/api/courses/{course_id}/voice/operations")
    def lookup_operation(course_id: str, teacher_profile_id: str, idempotency_key: str, identity=Depends(actor)):
        with database.factory() as db:
            return receipt(db, identity, course_id, owned_operation(db, identity, course_id, key=idempotency_key, profile_id=teacher_profile_id))

    @app.get("/api/courses/{course_id}/voice/operations/{operation_id}")
    def get_operation(course_id: str, operation_id: str, identity=Depends(actor)):
        with database.factory() as db:
            return receipt(db, identity, course_id, owned_operation(db, identity, course_id, operation_id))

    @app.get("/api/courses/{course_id}/voice/operations/{operation_id}/audio")
    def get_audio(course_id: str, operation_id: str, identity=Depends(actor)):
        with database.write() as db:
            row = owned_operation(db, identity, course_id, operation_id)
            data = row.value
            if data["kind"] != "speech" or data["status"] != "released":
                raise HTTPException(404, "Released audio not found")
            _, school_id, context, _ = revalidate(db, identity, course_id, data)
            authority = authorize_lesson_teacher(app, data["teacher_profile_id"], school_id, course_id, context)
            try:
                audio = base64.b64decode(auth.unseal(data["audio_cipher"])["audio_base64"], validate=True)
                if not audio or len(audio) > 2 * 1024 * 1024 or len(audio) != data["audio_bytes"] or hashlib.sha256(audio).hexdigest() != data["audio_sha256"] or not (audio.startswith(b"ID3") or len(audio) >= 2 and audio[0] == 255 and audio[1] & 224 == 224):
                    raise ValueError()
            except (HTTPException, ValueError, KeyError, TypeError, binascii.Error):
                raise HTTPException(409, "Stored teacher audio failed integrity verification") from None
            audit_actor(db, identity, "voice.playback_accessed", {"operation_id": row.id, "audio_sha256": data["audio_sha256"], "teacher_authority": authority}, data["session_id"])
            return Response(audio, media_type="audio/mpeg", headers={"Cache-Control": "no-store", "X-Content-Type-Options": "nosniff", "Content-Disposition": "inline; filename=teacher.mp3"})

    def perform(course_id, body, request, identity, kind):
        if not body.consent:
            raise HTTPException(422, "Choose whether to send audio or teaching text to ElevenLabs")
        recording, metadata = normalized_wav(body.audio_base64) if kind == "transcription" else (None, None)
        request_material = {"kind": kind, "teacher_profile_id": body.teacher_profile_id, "recording": metadata, "turn_id": body.turn_id if kind == "speech" else None, "consent": True}
        request_hash = hashlib.sha256(canonical(request_material).encode()).hexdigest()
        with database.write() as db:
            course, school_id = course_for_student(db, identity, course_id, settings)
            own = (Record.tenant_id == identity["tenant_id"], Record.kind == VOICE_OPERATION_KIND, Record.owner_id == identity["id"])
            key = body.idempotency_key
            if identity.get("auth_provider") == "academy":
                from .pii_storage import lookup_key
                key = lookup_key(db, identity["tenant_id"], identity["id"], key)
            matching = list(db.scalars(select(Record).where(*own, _json_value(db, "idempotency_key") == key).limit(2)))
            if len(matching) > 1:
                raise HTTPException(409, "Voice operation receipt is ambiguous")
            prior = matching[0] if matching else None
            if prior:
                if prior.parent_id != course_id or prior.value["request_sha256"] != request_hash:
                    raise HTTPException(409, "Idempotency key was used for another voice request")
                return receipt(db, identity, course_id, prior)
            course, school_id, context, connection, snapshot = resolve(db, identity, course_id, body.teacher_profile_id, live=True)
            descriptor = configuration_descriptor(connection)
            day = datetime.now(timezone.utc).date().isoformat()
            scoped = (*own, RecordScope.tenant_id == identity["tenant_id"], RecordScope.school_id == school_id)
            pending = db.scalar(select(func.count()).select_from(Record).join(RecordScope, RecordScope.record_id == Record.id).where(*scoped, _json_value(db, "status") == "pending"))
            if pending:
                raise HTTPException(409, "A previous voice operation is unresolved; check its receipt before continuing")
            # Database-side scalar projections never transfer or parse old
            # encrypted MP3 payloads in the API process. Each daily budget spans
            # this student's school, courses, teachers and both voice directions.
            text_size = func.coalesce(_json_value(db, "text_characters", integer=True), func.length(_json_value(db, "text")), 0)
            count, milliseconds, characters = db.execute(select(func.count(), func.coalesce(func.sum(_json_value(db, "recording", "duration_ms", integer=True)), 0), func.coalesce(func.sum(text_size), 0)).select_from(Record).join(RecordScope, RecordScope.record_id == Record.id).where(*scoped, _json_value(db, "budget_day") == day)).one()
            text = speech_text(db, identity, course, school_id, body.teacher_profile_id, body.turn_id, context) if kind == "speech" else None
            if kind == "speech" and (snapshot["voice_preference"]["voice_id"] not in descriptor["voice_ids"] or not voice_provider.stock_available()):
                raise HTTPException(409, "Choose a stock voice for this teacher first")
            if count >= descriptor["max_daily_operations"] or milliseconds + (metadata or {}).get("duration_ms", 0) > descriptor["max_daily_recording_seconds"] * 1000 or characters + len(text or "") > descriptor["max_daily_speech_characters"]:
                raise HTTPException(403, "Daily school voice allowance reached; typed help remains available")
            operation_id = uid()
            data = {"kind": kind, "status": "pending", "teacher_profile_id": body.teacher_profile_id, "idempotency_key": body.idempotency_key, "request_sha256": request_hash, "created_at": now(), "budget_day": day, "snapshot": snapshot, "consent": True, "recording": metadata or {}, "text": text, "turn_id": body.turn_id if kind == "speech" else None, "session_id": lesson_session_id(identity["tenant_id"], course_id, identity["id"]), "transcript": None, "error": None, "send_marker": None}
            row = Record(id=operation_id, tenant_id=identity["tenant_id"], kind="voice_operation", parent_id=course_id, owner_id=identity["id"], data=canonical(data))
            db.add(row)
            db.flush()
            bind_scope(db, row, school_id)
            audit_actor(db, identity, "voice.admitted", {"operation_id": row.id, **data}, data["session_id"])
        key, result, failure, verified = None, None, None, None
        try:
            accepted = auth.actor(request)
            if (accepted["id"], accepted["tenant_id"], accepted["login_id"]) != (identity["id"], identity["tenant_id"], identity["login_id"]):
                raise HTTPException(403, "Student identity changed")
            with database.write() as db:
                _, school_id, context, connection = revalidate(db, accepted, course_id, data)
                authority = authorize_lesson_teacher(app, body.teacher_profile_id, school_id, course_id, context)
                key_cipher = connection.value["key_cipher"]
                data["teacher_authority_before_send"] = authority
            key = auth.unseal(key_cipher)["api_key"]
            if kind == "speech":
                verified = app.state.voice_verify_stock(key, data["snapshot"]["voice_preference"]["voice_id"])
                if not isinstance(verified, dict) or verified.get("id") != data["snapshot"]["voice_preference"]["voice_id"] or verified.get("category") != "premade":
                    raise ValueError()
            # Recheck after public stock metadata lookup, immediately before the
            # durable marker. A metadata failure never sends student text/audio.
            accepted = auth.actor(request)
            if (accepted["id"], accepted["tenant_id"], accepted["login_id"]) != (identity["id"], identity["tenant_id"], identity["login_id"]):
                raise HTTPException(403, "Student identity changed")
            with database.write() as db:
                _, school_id, context, connection = revalidate(db, accepted, course_id, data)
                data["teacher_authority_before_send"] = authorize_lesson_teacher(app, body.teacher_profile_id, school_id, course_id, context)
                data["send_marker"] = now()
                data["verified_stock_voice"] = verified
                db.get(Record, operation_id).set(data)
                audit_actor(db, identity, "voice.send_marked", {"operation_id": operation_id, "kind": kind, "send_marker": data["send_marker"], "stock_voice": verified, "teacher_authority": data["teacher_authority_before_send"]}, data["session_id"])
            zero_retention = data["snapshot"]["voice_configuration"]["zero_retention"]
            result = app.state.voice_transcribe(key, recording, zero_retention) if kind == "transcription" else app.state.voice_synthesize(key, verified["id"], data["text"], zero_retention)
            if kind == "transcription" and (not isinstance(result, str) or not result.strip() or len(result) > 4000 or key in result):
                raise ValueError()
            if kind == "speech" and (not isinstance(result, bytes) or not result or len(result) > 2 * 1024 * 1024 or not (result.startswith(b"ID3") or len(result) >= 2 and result[0] == 255 and result[1] & 224 == 224)):
                raise ValueError()
            accepted = auth.actor(request)
            if (accepted["id"], accepted["tenant_id"], accepted["login_id"]) != (identity["id"], identity["tenant_id"], identity["login_id"]):
                raise HTTPException(403, "Student identity changed")
            with database.factory() as db:
                _, school_id, context, _ = revalidate(db, accepted, course_id, data)
                data["teacher_authority_before_release"] = authorize_lesson_teacher(app, body.teacher_profile_id, school_id, course_id, context)
        except HTTPException:
            failure = "Teacher, student access or voice settings changed; result withheld"
        except Exception:
            # Transport, vendor body, SDK errors and credential values are never
            # returned or logged; the committed marker retains uncertainty.
            failure = "Voice result unavailable; check the receipt. It will not be retried automatically."
        finally:
            recording, key = None, None
        with database.write() as db:
            if not failure:
                try:
                    revalidate(db, accepted, course_id, data)
                except HTTPException:
                    failure = "Teacher, student access or voice settings changed; result withheld"
            data["status"] = "withheld" if failure else "released"
            data["error"] = failure
            data["uncertain"] = bool(failure and data["send_marker"])
            if not failure:
                if kind == "transcription":
                    data["transcript"] = result.strip()
                else:
                    data["audio_sha256"] = hashlib.sha256(result).hexdigest()
                    data["audio_bytes"] = len(result)
                    data["audio_cipher"] = auth.seal({"audio_base64": base64.b64encode(result).decode()})
            db.get(Record, operation_id).set(data)
            audit_actor(db, identity, "voice." + data["status"], {"operation_id": operation_id, **{key: value for key, value in data.items() if key != "audio_cipher"}}, data["session_id"])
            return receipt(db, identity, course_id, db.get(Record, operation_id))

    @app.post("/api/courses/{course_id}/voice/transcriptions")
    def transcribe(course_id: str, body: TranscriptionInput, request: Request, identity=Depends(actor)):
        return perform(course_id, body, request, identity, "transcription")

    @app.post("/api/courses/{course_id}/voice/speech")
    def speech(course_id: str, body: SpeechInput, request: Request, identity=Depends(actor)):
        return perform(course_id, body, request, identity, "speech")
