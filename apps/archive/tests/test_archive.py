"""Synthetic evidence only; transport, crashes and tamper detection are exercised."""
import base64
from dataclasses import replace
import hashlib
import io
import json
from pathlib import Path
import sys
from types import SimpleNamespace

import pytest
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from sqlalchemy import create_engine, text

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from academy_archive import (Archive, ArchiveConfig, ArchiveError, DatabaseSource, S3Objects,
    ZERO, canonical, check_events, export_once, main, sha, safe_payload)


@pytest.mark.parametrize('field', ['audio_base64', 'audio_cipher'])
def test_voice_binary_fields_are_refused_while_visible_evidence_is_allowed(field):
    safe_payload({'transcript': 'Why does this happen?', 'spoken_text': 'Reviewed explanation', 'recording': {'sha256': 'a' * 64, 'duration_ms': 1000}, 'audio_sha256': 'b' * 64, 'audio_bytes': 2048})
    with pytest.raises(ArchiveError, match='prohibited'):
        safe_payload({'voice': {field: 'synthetic-private-audio'}})


def events(count=3, tenant="synthetic-tenant"):
    result, previous = [], ZERO
    for sequence in range(1, count + 1):
        event = {"id": str(sequence), "tenant_id": tenant, "sequence": sequence, "session_id": "session-a", "actor_id": "student-a", "event_type": "lesson.completed", "payload": {"student_question": "Why does this happen?", "student_attempt": "My own explanation", "approved_text": "Reviewed teaching excerpt", "model_policy": {"name": "bounded-selection"}, "learning_preferences_snapshot": {"revision": "v1", "weights": {"warmth": 0.6}}, "feedback": {"accuracy": "incorrect", "tone": "more_encouraging"}}, "previous_hash": previous, "created_at": "2026-10-18T12:00:00+00:00"}
        event["hash"] = sha(canonical(event))
        previous = event["hash"]
        result.append(event)
    return result


class MemoryObjects:
    def __init__(self):
        self.values, self.creates, self.fail_on = {}, [], None

    def get(self, key):
        return self.values.get(key)

    def create(self, key, body):
        self.creates.append(key)
        if self.fail_on and self.fail_on in key:
            raise OSError("synthetic failure")
        return self.values.setdefault(key, body)


@pytest.fixture
def config(tmp_path):
    ca = tmp_path / "ca.pem"
    ca.write_text("synthetic CA; no network")
    return ArchiveConfig("synthetic-tenant", "https://archive.internal:8334", ("https://archive.internal:8334",), "academy-audit", "ncacademy/" + sha(b"synthetic-tenant"), str(ca), batch_size=2)


@pytest.fixture
def archive(config):
    signing = Ed25519PrivateKey.generate()
    return Archive(config, MemoryObjects(), b"e" * 32, signing.public_key().public_bytes_raw(), signing.private_bytes_raw())


def source(tmp_path, values):
    url = "sqlite:///" + str(tmp_path / "source.db")
    engine = create_engine(url)
    with engine.begin() as connection:
        connection.execute(text("CREATE TABLE audit_heads (tenant_id TEXT PRIMARY KEY, sequence INTEGER, digest TEXT)"))
        connection.execute(text("CREATE TABLE audit_events (id TEXT, tenant_id TEXT, sequence INTEGER, session_id TEXT, actor_id TEXT, event_type TEXT, payload TEXT, previous_hash TEXT, created_at TEXT, hash TEXT)"))
        for event in values:
            connection.execute(text("INSERT INTO audit_events VALUES (:id,:tenant_id,:sequence,:session_id,:actor_id,:event_type,:payload,:previous_hash,:created_at,:hash)"), {**event, "payload": canonical(event["payload"]).decode()})
        connection.execute(text("INSERT INTO audit_heads VALUES (:tenant,:sequence,:digest)"), {"tenant": "synthetic-tenant", "sequence": len(values), "digest": values[-1]["hash"] if values else ZERO})
    engine.dispose()
    return DatabaseSource(url, "synthetic-tenant", local_test=True)


def test_complete_event_bytes_encrypted_signed_restored_and_offline_verifiable(archive, tmp_path):
    original = events()
    cursor_path = tmp_path / "cursor.json"
    result = export_once(archive, source(tmp_path, original), cursor_path)
    assert result["lag_events"] == 0 and result["archived_sequence"] == 3
    restored, cursor = archive.export(json.loads(cursor_path.read_bytes()))
    assert restored["events"] == original
    assert check_events(restored["events"], restored["tenant_id"]) == (3, original[-1]["hash"])
    for key, raw in archive.objects.values.items():
        if "/segments/" in key:
            assert b"student_question" not in raw and b"Reviewed teaching excerpt" not in raw
    assert cursor["digest"] == original[-1]["hash"]
    assert cursor_path.stat().st_mode & 0o777 == 0o600


def test_retries_and_lost_cursor_do_not_duplicate_or_overwrite(archive, tmp_path):
    original = events(5)
    database = source(tmp_path, original)
    cursor_path = tmp_path / "cursor.json"
    export_once(archive, database, cursor_path)
    initial_objects = dict(archive.objects.values)
    assert export_once(archive, database, cursor_path)["archived_sequence"] == 5
    cursor_path.unlink()
    assert export_once(archive, database, cursor_path)["archived_sequence"] == 5
    assert archive.objects.values == initial_objects


def test_upload_then_process_crash_recovers_orphan_encrypted_segment(archive, tmp_path):
    original = events()
    database = source(tmp_path, original)
    archive.objects.fail_on = "/commits/"
    cursor_path = tmp_path / "cursor.json"
    with pytest.raises(OSError):
        export_once(archive, database, cursor_path)
    assert not cursor_path.exists()
    orphan = {key: raw for key, raw in archive.objects.values.items() if "/segments/" in key}
    assert len(orphan) == 1
    archive.objects.fail_on = None
    export_once(archive, database, cursor_path)
    assert all(archive.objects.values[key] == raw for key, raw in orphan.items())
    assert archive.export()[0]["events"] == original


def test_committed_remote_tail_recovers_after_cursor_write_crash(archive, tmp_path, monkeypatch):
    import academy_archive
    database = source(tmp_path, events())
    cursor_path = tmp_path / "cursor.json"
    real_write = academy_archive.atomic_private
    monkeypatch.setattr(academy_archive, "atomic_private", lambda *_: (_ for _ in ()).throw(OSError("crash")))
    with pytest.raises(OSError):
        export_once(archive, database, cursor_path)
    assert not cursor_path.exists()
    monkeypatch.setattr(academy_archive, "atomic_private", real_write)
    assert export_once(archive, database, cursor_path)["archived_sequence"] == 3


@pytest.mark.parametrize("retained_sequence", (0, 2))
def test_committed_remote_tail_exposes_database_rollback_after_lost_cursor_write(archive, tmp_path, retained_sequence):
    original = events(3)
    database = source(tmp_path, original[:retained_sequence])
    path = tmp_path / "cursor.json"
    cursor = export_once(archive, database, path)["checkpoint"]
    # Model the successful remote commit followed by a cursor-write crash and
    # a complete, internally consistent database restore to the old cursor.
    archive.append(cursor, original[retained_sequence:retained_sequence + 1])
    before_cursor = path.read_bytes() if path.exists() else None
    before_objects = dict(archive.objects.values)
    with pytest.raises(ArchiveError, match="beyond the verified database snapshot"):
        export_once(archive, database, path)
    assert (path.read_bytes() if path.exists() else None) == before_cursor
    assert archive.objects.values == before_objects


def test_competing_shorter_valid_commit_wins_without_overwrite(archive):
    first_events, first_cursor = archive.append(archive.empty(), events(1))
    second_events, second_cursor = archive.append(archive.empty(), events(2))
    assert first_events == second_events and first_cursor == second_cursor
    assert second_cursor["sequence"] == 1


def test_competing_different_valid_evidence_is_rejected(archive):
    archive.append(archive.empty(), events(1))
    different = events(1)
    different[0]["payload"]["approved_text"] = "different evidence"
    different[0]["hash"] = sha(canonical({key: value for key, value in different[0].items() if key != "hash"}))
    with pytest.raises(ArchiveError, match="disagrees"):
        archive.append(archive.empty(), different)


def test_owner_restore_uses_independent_snapshot_even_when_newer_commits_exist(archive):
    _, accepted = archive.append(archive.empty(), events(2))
    archive.append(accepted, events(3)[2:])
    restored, checkpoint = archive.export(accepted)
    assert restored["events"] == events(2)
    assert checkpoint == accepted


@pytest.mark.parametrize("tamper", ("ciphertext", "signature", "missing_segment", "missing_tail"))
def test_tamper_and_deleted_tail_detected_against_independent_head(archive, tmp_path, tamper):
    result = export_once(archive, source(tmp_path, events()), tmp_path / "cursor.json")
    checkpoint = result["checkpoint"]
    if tamper == "ciphertext":
        key = next(key for key in archive.objects.values if "/segments/" in key)
        archive.objects.values[key] = b'{"format":1,"nonce":"AA==","ciphertext":"AA=="}'
    elif tamper == "signature":
        key = archive.commit_key(1)
        value = json.loads(archive.objects.values[key])
        value["body"]["created_at"] = "modified"
        archive.objects.values[key] = canonical(value)
    elif tamper == "missing_segment":
        del archive.objects.values[next(key for key in archive.objects.values if "/segments/" in key)]
    else:
        del archive.objects.values[archive.commit_key(3)]
    with pytest.raises(ArchiveError):
        archive.export(checkpoint)


def test_persisted_cursor_detects_remote_tail_deletion_before_further_writes(archive, tmp_path):
    database = source(tmp_path, events())
    path = tmp_path / "cursor.json"
    export_once(archive, database, path)
    del archive.objects.values[archive.commit_key(3)]
    with pytest.raises(ArchiveError, match="tail"):
        export_once(archive, database, path)


def test_database_tamper_or_chain_truncation_refuses_archive_advance(archive, tmp_path):
    database = source(tmp_path, events())
    path = tmp_path / "cursor.json"
    export_once(archive, database, path)
    with database.engine.begin() as connection:
        connection.execute(text("DELETE FROM audit_events WHERE sequence=2"))
    before = path.read_bytes()
    with pytest.raises(ArchiveError):
        export_once(archive, database, path)
    assert path.read_bytes() == before


@pytest.mark.parametrize("damage", ("missing", "truncated"))
def test_missing_or_truncated_database_head_cannot_hide_unarchived_events(archive, tmp_path, damage):
    values = events()
    database = source(tmp_path, values)
    with database.engine.begin() as connection:
        if damage == "missing":
            connection.execute(text("DELETE FROM audit_heads"))
        else:
            connection.execute(text("UPDATE audit_heads SET sequence=1,digest=:digest"), {"digest": values[0]["hash"]})
    with pytest.raises(ArchiveError, match="inventory"):
        export_once(archive, database, tmp_path / "cursor.json")
    assert not archive.objects.values


@pytest.mark.parametrize("key", ("images_cipher", "api_key", "client_secret", "access_token", "data_base64"))
def test_structural_secret_or_photo_evidence_fails_without_redaction(archive, key):
    value = events(1)
    value[0]["payload"]["nested"] = {key: "never upload"}
    with pytest.raises(ArchiveError, match="prohibited"):
        archive.append(archive.empty(), value)
    assert not archive.objects.values


def test_another_tenant_or_archive_key_cannot_be_mixed(archive, config):
    with pytest.raises(ArchiveError, match="tenant"):
        archive.append(archive.empty(), events(1, "foreign-tenant"))
    second = Archive(config, archive.objects, b"x" * 32, archive.public.public_bytes_raw())
    archive.append(archive.empty(), events(1))
    with pytest.raises(ArchiveError):
        second.export()


@pytest.mark.parametrize("endpoint", ("http://archive.internal", "https://user:password@archive.internal", "https://metadata.internal", "https://archive.internal/path", "https://archive.internal?token=x"))
def test_endpoint_must_match_operator_allowlist_and_exact_tls_origin(config, endpoint):
    with pytest.raises(ArchiveError):
        replace(config, endpoint=endpoint)


def test_s3_request_guard_blocks_redirects_foreign_prefix_and_write_for_reader(config):
    objects = object.__new__(S3Objects)
    objects.config, objects.writable = config, False
    good = config.endpoint + "/" + config.bucket + "/" + config.prefix + "/commits/00000000000000000001.json"
    objects._guard_request(SimpleNamespace(url=good, method="GET"))
    for url, method in ((good.replace("archive.internal", "attacker.example"), "GET"), (good.replace(config.prefix, "other-tenant"), "GET"), (good, "PUT"), (good.replace("https:", "http:"), "GET")):
        with pytest.raises(ArchiveError):
            objects._guard_request(SimpleNamespace(url=url, method=method))


def test_production_database_rejects_owner_style_or_nonverified_connection():
    for url in ("sqlite:///memory.db", "postgresql+psycopg://readonly:secret@db/academy?sslmode=require", "postgresql+psycopg://readonly:secret@db/academy?sslmode=verify-full"):
        with pytest.raises(ArchiveError):
            DatabaseSource(url, "synthetic-tenant")


def test_public_failure_output_never_echoes_sensitive_exception(tmp_path, capsys):
    result = main(["export", "--config", str(tmp_path / "missing"), "--credentials", "/private/secret", "--encryption-key", "/private/key", "--public-key", "/private/public"])
    captured = capsys.readouterr()
    assert result == 1 and not captured.out
    assert json.loads(captured.err)["status"] == "archive_failed"
    assert "/private/secret" not in captured.err


def s3_objects(config, tmp_path):
    credentials = tmp_path / "credentials.json"
    credentials.write_text(json.dumps({"access_key_id": "synthetic-archive", "secret_access_key": "synthetic-archive-secret"}))
    return S3Objects(config, credentials)


def test_actual_sdk_uses_single_conditional_create_readback_and_default_retention(config, tmp_path):
    from botocore.stub import Stubber
    from datetime import datetime, timezone, timedelta
    objects = s3_objects(config, tmp_path)
    key = config.prefix + "/segments/test"
    payload = b"encrypted-synthetic-evidence"
    created = datetime.now(timezone.utc)
    with Stubber(objects.client) as stub:
        stub.add_response("put_object", {"ETag": '"test"'}, {"Bucket": config.bucket, "Key": key, "Body": payload, "ContentType": "application/octet-stream", "IfNoneMatch": "*", "ContentMD5": base64.b64encode(hashlib.md5(payload, usedforsecurity=False).digest()).decode()})
        stub.add_response("get_object", {"Body": io.BytesIO(payload), "ContentLength": len(payload), "VersionId": "version-1", "LastModified": created}, {"Bucket": config.bucket, "Key": key})
        stub.add_response("get_object_retention", {"Retention": {"Mode": "COMPLIANCE", "RetainUntilDate": created + timedelta(days=365)}}, {"Bucket": config.bucket, "Key": key, "VersionId": "version-1"})
        assert objects.create(key, payload) == payload
        stub.assert_no_pending_responses()
    assert objects.client.meta.config.retries["total_max_attempts"] == 1
    assert objects.client.meta.config.proxies == {}


def test_actual_sdk_uncertain_write_reconciles_without_overwrite(config, tmp_path):
    from botocore.stub import Stubber
    from datetime import datetime, timezone, timedelta
    objects = s3_objects(config, tmp_path)
    key, payload = config.prefix + "/segments/test", b"winner-evidence"
    created = datetime.now(timezone.utc) - timedelta(hours=8)
    with Stubber(objects.client) as stub:
        stub.add_client_error("put_object", "ServiceUnavailable", http_status_code=503)
        stub.add_response("get_object", {"Body": io.BytesIO(payload), "ContentLength": len(payload), "VersionId": "retained-old-version", "LastModified": created}, {"Bucket": config.bucket, "Key": key})
        stub.add_response("get_object_retention", {"Retention": {"Mode": "COMPLIANCE", "RetainUntilDate": created + timedelta(days=365)}}, {"Bucket": config.bucket, "Key": key, "VersionId": "retained-old-version"})
        assert objects.create(key, b"attempted-body") == payload
        stub.assert_no_pending_responses()


def test_actual_sdk_missing_compliance_retention_refuses_checkpoint(config, tmp_path):
    from botocore.stub import Stubber
    from datetime import datetime, timezone
    objects = s3_objects(config, tmp_path)
    key, payload = config.prefix + "/segments/test", b"ciphertext"
    with Stubber(objects.client) as stub:
        stub.add_response("put_object", {})
        stub.add_response("get_object", {"Body": io.BytesIO(payload), "ContentLength": len(payload), "VersionId": "version-1", "LastModified": datetime.now(timezone.utc)})
        stub.add_response("get_object_retention", {})
        with pytest.raises(ArchiveError, match="retention"):
            objects.create(key, payload)


@pytest.mark.parametrize("age_days,hold_days", ((0, 1), (400, 365)))
def test_actual_sdk_short_default_or_expired_old_orphan_cannot_be_checkpointed(config, tmp_path, age_days, hold_days):
    from botocore.stub import Stubber
    from datetime import datetime, timezone, timedelta
    objects = s3_objects(config, tmp_path)
    key, payload = config.prefix + "/segments/test", b"ciphertext"
    created = datetime.now(timezone.utc) - timedelta(days=age_days)
    with Stubber(objects.client) as stub:
        stub.add_response("put_object", {})
        stub.add_response("get_object", {"Body": io.BytesIO(payload), "ContentLength": len(payload), "VersionId": "version-1", "LastModified": created})
        stub.add_response("get_object_retention", {"Retention": {"Mode": "COMPLIANCE", "RetainUntilDate": created + timedelta(days=hold_days)}}, {"Bucket": config.bucket, "Key": key, "VersionId": "version-1"})
        with pytest.raises(ArchiveError, match="retention"):
            objects.create(key, payload)


def test_static_store_credentials_use_exact_separate_policies_without_admin_or_anonymous(config):
    sys.path.insert(0, str(Path(__file__).resolve().parents[3] / "scripts"))
    from prepare_archive_storage_config import render
    writer = {"access_key_id": "synthetic-writer", "secret_access_key": "synthetic-writer-secret"}
    reader = {"access_key_id": "synthetic-reader", "secret_access_key": "synthetic-reader-secret"}
    result = render(config, writer, reader)
    assert len(result["identities"]) == 2
    assert all("actions" not in identity for identity in result["identities"])
    assert all(identity["name"] != "anonymous" and identity["policyNames"] for identity in result["identities"])
    for item in result["policies"]:
        policy = json.loads(item["content"])
        for statement in policy["Statement"]:
            assert all(resource.startswith("arn:aws:s3:::" + config.bucket) for resource in statement["Resource"])
    allowed_reader = json.loads(result["policies"][1]["content"])["Statement"][0]
    assert allowed_reader["Action"] == ["s3:GetObject"]
    with pytest.raises(ArchiveError, match="distinct"):
        render(config, writer, writer)
