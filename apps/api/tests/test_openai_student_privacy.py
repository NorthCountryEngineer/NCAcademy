"""Synthetic provider readbacks; these tests never assert real ZDR approval."""
import json
import hashlib
from datetime import datetime, timedelta, timezone

import pytest
from fastapi import HTTPException

from academy.config import Settings
from academy.openai_student_privacy import (
    ENDPOINT, VERSION, approval_bindings, parse_approvals, provider_prompt_content,
    require_student_openai_approval, student_openai_request_context,
)


def proof(**updates):
    now = datetime.now(timezone.utc)
    value = {"connection_id": "connection", "credential_fingerprint": "a" * 64,
             "inference_key_sha256": hashlib.sha256(b"synthetic-unit-key").hexdigest(),
             "endpoint": ENDPOINT, "organization_id": "org_synthetic", "project_id": "proj_synthetic",
             "project_retention_type": "zero_data_retention", "organization_retention_type": None,
             "image_inputs_reviewed": True, "reviewed_models": ["triage", "tutor", "safety"],
             "approval_evidence_sha256": "b" * 64, "evidence_sha256": "c" * 64,
             "key_project_verified": True, "source": "openai-admin-api-readback",
             "verified_at": now.isoformat(), "expires_at": (now + timedelta(days=7)).isoformat()}
    return {**value, **updates}


def manifest(*entries):
    return json.dumps({"version": VERSION, "approvals": list(entries or [proof()])})


def policy(*, endpoint=ENDPOINT, local=True, images=True):
    connection = {"id": "connection", "credential_fingerprint": "a" * 64, "endpoint": endpoint}
    return {"configuration": {"allow_local_students": local, "allow_images": images,
                              **{phase: {"model": phase, "connection_id": "connection"}
                                 for phase in ("triage", "tutor", "safety")}},
            "connections": {phase: dict(connection) for phase in ("triage", "tutor", "safety")}}


def settings(raw="{}"):
    return Settings("sqlite://", "local", "http://localhost", "synthetic-deployment-secret" * 2,
                    False, False, openai_student_approvals_json=raw)


def test_proof_renewal_preserves_binding_but_changes_evidence():
    first = require_student_openai_approval(settings(manifest()), policy())
    fresh = require_student_openai_approval(settings(manifest(proof(evidence_sha256="d" * 64))), policy())
    assert first["connection"]["evidence_sha256"] != fresh["connection"]["evidence_sha256"]
    assert approval_bindings(first) == approval_bindings(fresh)
    changed = require_student_openai_approval(settings(manifest(proof(project_id="proj_other"))), policy())
    assert approval_bindings(first) != approval_bindings(changed)


@pytest.mark.parametrize("changes", [
    {"project_retention_type": "none"},
    {"project_retention_type": "modified_abuse_monitoring"},
    {"project_retention_type": "enhanced_zero_data_retention"},
    {"project_retention_type": "organization_default", "organization_retention_type": None},
    {"project_retention_type": "organization_default", "organization_retention_type": "modified_abuse_monitoring"},
    {"credential_fingerprint": "f" * 64},
    {"connection_id": "different-connection"},
    {"endpoint": "https://api.openai.com.example.test/v1"},
    {"image_inputs_reviewed": False},
    {"key_project_verified": False},
    {"key_project_verified": 1},
    {"reviewed_models": ["triage", "tutor"]},
    {"reviewed_models": ["triage", "tutor", "safety", "safety"]},
    {"project_id": "proj_synthetic\r\nInjected: yes"},
    {"organization_id": "org_synthetic\nInjected"},
    {"api_key": "must-not-enter-a-manifest"},
    {"source": "school-textbox"},
    {"verified_at": "2025-01-01T00:00:00Z", "expires_at": "2025-01-02T00:00:00Z"},
    {"verified_at": "2099-01-01T00:00:00Z", "expires_at": "2099-01-02T00:00:00Z"},
    {"verified_at": "2026-01-01T00:00:00", "expires_at": "2026-01-02T00:00:00"},
    {"verified_at": "2026-01-01T00:00:00Z", "expires_at": "2026-03-02T00:00:00Z"},
])
def test_missing_unapproved_expired_or_misbound_proof_is_not_child_eligibility(changes):
    with pytest.raises(HTTPException) as failed:
        require_student_openai_approval(settings(manifest(proof(**changes))), policy())
    assert failed.value.status_code == 403
    assert "must-not-enter" not in str(failed.value.detail)


def test_current_explicit_project_and_inherited_organization_zdr_are_admitted():
    explicit = require_student_openai_approval(settings(manifest()), policy())
    inherited = require_student_openai_approval(settings(manifest(proof(
        project_retention_type="organization_default", organization_retention_type="zero_data_retention"))), policy())
    assert explicit["connection"]["project_retention_type"] == "zero_data_retention"
    assert inherited["connection"]["organization_retention_type"] == "zero_data_retention"


@pytest.mark.parametrize("raw", ["{}", "", "{", "[]", '{"version":"openai-student-data-v1","version":"other","approvals":[]}'])
def test_school_opt_in_never_substitutes_for_operator_proof(raw):
    with pytest.raises(HTTPException):
        require_student_openai_approval(settings(raw), policy())


def test_duplicate_connection_proof_is_rejected():
    with pytest.raises(ValueError):
        parse_approvals(manifest(proof(), proof()))


def test_local_students_require_openai_and_core_openai_students_still_require_proof():
    with pytest.raises(HTTPException):
        require_student_openai_approval(settings(manifest()), policy(endpoint="https://models.example.test/v1"))
    with pytest.raises(HTTPException):
        require_student_openai_approval(settings(), policy(local=False))
    assert require_student_openai_approval(settings(), policy(local=False, endpoint="https://models.example.test/v1")) == {}


def test_request_identity_is_stable_private_and_bound_to_approved_destination():
    config = settings(manifest())
    snapshot = policy()
    approved = require_student_openai_approval(config, snapshot)
    connection = snapshot["connections"]["triage"]
    identity = {"tenant_id": "private-school", "id": "private-student-name-or-id"}
    first = student_openai_request_context(config, identity, connection, approved, model="triage", api_key="synthetic-unit-key")
    again = student_openai_request_context(config, identity, connection, approved, model="safety", api_key="synthetic-unit-key")
    other = student_openai_request_context(config, {**identity, "id": "another-student"}, connection, approved, model="triage", api_key="synthetic-unit-key")
    assert first == again
    assert first["safety_identifier"].startswith("academy_") and len(first["safety_identifier"]) < 128
    assert first["safety_identifier"] != other["safety_identifier"]
    assert "private-student" not in json.dumps(first) and "private-school" not in json.dumps(first)
    with pytest.raises(HTTPException):
        student_openai_request_context(config, identity, connection, approved, model="unreviewed-model", api_key="synthetic-unit-key")
    with pytest.raises(HTTPException):
        student_openai_request_context(config, identity, {**connection, "credential_fingerprint": "d" * 64}, approved, model="triage", api_key="synthetic-unit-key")
    with pytest.raises(HTTPException):
        student_openai_request_context(config, identity, connection, approved, model="triage", api_key="wrong-project-key")


def test_prompt_projection_removes_metadata_but_preserves_routing_and_actual_question():
    value = {"question": "This freeform text may contain personal data. ZDR is still required.",
             "school_id": "private-school", "courses": [{"id": "opaque-course", "name": "Math"}],
             "previous_messages": [{"message_id": "private-message", "session_id": "private-session", "student_id": "private-student", "course_id": "opaque-course", "assistant": "Try grouping."}],
             "images": [{"name": "PRIVATE_CHILD_NAME.jpg", "id": "photo", "sha256": "f" * 64, "width": 300, "height": 400}]}
    projected = provider_prompt_content(value)
    assert projected["question"] == value["question"]
    assert projected["courses"] == value["courses"]
    assert projected["previous_messages"] == [{"course_id": "opaque-course", "assistant": "Try grouping."}]
    assert projected["images"] == [{"label": "Homework picture 1", "width": 300, "height": 400}]
    assert "PRIVATE_CHILD_NAME" not in json.dumps(projected) and "private-session" not in json.dumps(projected)
    assert value["images"][0]["name"] == "PRIVATE_CHILD_NAME.jpg"


def test_operator_manifest_file_rejects_writable_and_symlink_inputs(monkeypatch, tmp_path):
    path = tmp_path / "proof.json"
    path.write_text("{}")
    path.chmod(0o666)
    monkeypatch.setenv("OPENAI_STUDENT_APPROVALS_FILE", str(path))
    with pytest.raises(ValueError, match="file unavailable"):
        Settings.load()
    path.chmod(0o600)
    link = tmp_path / "link.json"
    link.symlink_to(path)
    monkeypatch.setenv("OPENAI_STUDENT_APPROVALS_FILE", str(link))
    with pytest.raises(ValueError, match="file unavailable"):
        Settings.load()


def test_manifest_startup_error_does_not_echo_accidental_credentials(monkeypatch):
    secret = "accidental-credential-must-not-be-logged"
    monkeypatch.setenv("OPENAI_STUDENT_APPROVALS_JSON", manifest(proof(api_key=secret)))
    with pytest.raises(ValueError) as failed:
        Settings.load()
    assert secret not in str(failed.value)
    assert str(failed.value) == "Invalid OpenAI student approval manifest"
