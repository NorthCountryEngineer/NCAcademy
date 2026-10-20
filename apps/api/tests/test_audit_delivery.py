"""Synthetic local evidence; no Core, provider or production student data."""
import json
from pathlib import Path

import pytest
from sqlalchemy import func, select, text

from academy.audit_delivery import AuditDeliveryOutbox, AuditDeliveryState, AuditDeliveryReceipt, delivery_status, enqueue_audit_metadata, validate_metadata
from academy.db import Audit, audit, canonical


def test_audit_outbox_is_same_transaction_and_rolls_back(app):
    database = app.state.database
    with database.factory() as db:
        before_audit = db.scalar(select(func.count()).select_from(Audit))
        before_outbox = db.scalar(select(func.count()).select_from(AuditDeliveryOutbox))
    with pytest.raises(RuntimeError):
        with database.write() as db:
            row = audit(db, "synthetic-tenant", "private-student", "homework.response_recorded", {"question": "Private homework text", "school_id": "school-a", "status": "released"}, "private-session")
            db.flush()
            assert db.scalar(select(AuditDeliveryOutbox).where(AuditDeliveryOutbox.audit_id == row.id))
            raise RuntimeError("synthetic crash before commit")
    with database.factory() as db:
        assert db.scalar(select(func.count()).select_from(Audit)) == before_audit
        assert db.scalar(select(func.count()).select_from(AuditDeliveryOutbox)) == before_outbox


def test_metadata_is_allowlisted_and_raw_private_content_is_absent(app):
    secret = "Never ship student name or question or credential"
    payload = {"question": secret, "reply": secret, "student_name": secret, "api_key": secret, "images": [secret],
        "course_id": "private-course", "school_id": "school-a", "status": "released", "phase": "tutor",
        "policy_snapshot": {"private_instructions": secret, "version": 3},
        "model_snapshot": {"model": "bounded-tutor", "connection_id": "private-connection"},
        "usage": {"prompt_tokens": 90, "completion_tokens": 10, "total_tokens": 100, "api_key": secret, "input_tokens": True}}
    with app.state.database.write() as db:
        row = audit(db, "synthetic-tenant", "private-student", "homework.response_recorded", payload, "private-session")
        entry = enqueue_audit_metadata(db, row, payload)
        db.flush()
        value = json.loads(entry.metadata_json)
        validate_metadata(value)
        assert value["usage"] == {"prompt_tokens": 90, "completion_tokens": 10, "total_tokens": 100}
        assert value["state"] == "released"
        assert value["phase"] == "tutor"
        assert len(value["snapshot_sha256"]["model_snapshot"]) == 64
        assert value["audit_sha256"] == row.hash
        assert value["audit_sequence"] == row.sequence
        for private in (secret, "private-course", "school-a", "private-student", "private-session", "private-connection", "private_instructions"):
            assert private not in entry.metadata_json
        assert "actor_ref" not in value
        assert "student_ref" not in value
        assert db.scalar(select(func.count()).select_from(AuditDeliveryOutbox).where(AuditDeliveryOutbox.audit_id == row.id)) == 1
        assert db.get(AuditDeliveryState, entry.id).status == "pending"


@pytest.mark.parametrize("field,value", [("question", "private"), ("actor_ref", "a" * 64), ("student_id", "private"), ("state", "private student text"), ("usage", {"total_tokens": -1}), ("usage", {"total_tokens": True}), ("snapshot_sha256", {"prompt": "a" * 64})])
def test_metadata_validator_rejects_unlisted_fields(app, field, value):
    with app.state.database.write() as db:
        row = audit(db, "synthetic-tenant", "private-student", "homework.response_recorded", {})
        entry = enqueue_audit_metadata(db, row, {})
        metadata = json.loads(entry.metadata_json)
        metadata[field] = value
        with pytest.raises(ValueError):
            validate_metadata(metadata)


def test_outbox_and_receipts_cannot_be_mutated_through_sql(app):
    database = app.state.database
    with database.write() as db:
        row = audit(db, "synthetic-tenant", "private-student", "homework.response_recorded", {})
        entry = enqueue_audit_metadata(db, row, {})
        db.add(AuditDeliveryReceipt(id="receipt-synthetic", tenant_id="synthetic-tenant", event_id=entry.id,
            attempt=1, outcome="created", http_status=201, core_record_id=1, destination_sha256="a" * 64))
        event_id = entry.id
    for table, predicate in (("audit_delivery_outbox", "id=:id"), ("audit_delivery_receipts", "event_id=:id")):
        for operation in (f"UPDATE {table} SET tenant_id='other' WHERE {predicate}", f"DELETE FROM {table} WHERE {predicate}"):
            with pytest.raises(Exception, match="append-only"):
                with database.engine.begin() as connection:
                    connection.execute(text(operation), {"id": event_id})


def test_school_delivery_status_is_scoped_without_leaking_other_schools(app):
    database = app.state.database
    with database.write() as db:
        audit(db, "synthetic-tenant", "admin", "homework.response_recorded", {"school_id": "school-a"})
        audit(db, "synthetic-tenant", "admin", "homework.response_recorded", {"school_id": "school-b"})
    with database.factory() as db:
        assert delivery_status(db, "synthetic-tenant", "school-a")["pending_events"] == 1
        assert delivery_status(db, "synthetic-tenant", "school-b")["pending_events"] == 1
        assert delivery_status(db, "synthetic-tenant", "unknown")["pending_events"] == 0
        assert delivery_status(db, "synthetic-tenant")["pending_events"] == 2
        assert delivery_status(db, "foreign-tenant")["pending_events"] == 0


def test_deployed_core_dataset_schema_accepts_only_opaque_metadata(app):
    schema = json.loads((Path(__file__).resolve().parents[3] / "deploy/archive/core-metadata-dataset.schema.json").read_text())
    assert schema["additionalProperties"] is False
    assert schema["properties"]["usage"]["additionalProperties"] is False
    assert schema["properties"]["snapshot_sha256"]["additionalProperties"] is False
    with app.state.database.write() as db:
        payload = {"school_id": "school-a", "phase": "safety", "configuration_snapshot": {"version": 1}, "grounding_snapshot": {"corpus": "synthetic"}, "usage": {"input_tokens": 17}}
        row = audit(db, "synthetic-tenant", "admin", "homework.provider_result", payload)
        value = json.loads(enqueue_audit_metadata(db, row, payload).metadata_json)
        validate_metadata(value)
        assert set(schema["required"]) <= set(value) <= set(schema["properties"])
        assert "question" not in schema["properties"]
        assert "actor_ref" not in schema["properties"]
        assert "private" not in schema["properties"]["phase"]["enum"]
        assert set(value["snapshot_sha256"]) <= set(schema["properties"]["snapshot_sha256"]["properties"])
