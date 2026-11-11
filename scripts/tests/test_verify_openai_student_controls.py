import argparse
from datetime import datetime, timezone
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import stat
import sys

import httpx
import pytest

spec = importlib.util.spec_from_file_location(
    "verify_openai_student_controls", Path(__file__).parents[1] / "verify_openai_student_controls.py")
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)

ADMIN = "sk-admin-synthetic-secret-not-valid"
INFERENCE = "sk-proj-synthetic-secret-not-valid"
NOW = datetime(2026, 11, 11, tzinfo=timezone.utc)


@pytest.fixture
def inputs(tmp_path):
    tmp_path = tmp_path.resolve()
    tmp_path.chmod(0o700)
    for name, content in (("admin", ADMIN + "\n"), ("inference", INFERENCE + "\n"),
                          ("approval", "Synthetic written approval conditions, not real provider approval.\n")):
        (tmp_path / name).write_text(content)
        (tmp_path / name).chmod(0o600)
    return argparse.Namespace(admin_key_file=tmp_path / "admin", inference_key_file=tmp_path / "inference",
        approval_evidence_file=tmp_path / "approval", output=tmp_path / "manifest.json",
        connection_id="synthetic-connection", credential_fingerprint="a" * 64,
        organization_id="synthetic-organization", project_id="proj_synthetic", model=["gpt-6-luna"],
        valid_for_days=7, image_inputs_reviewed=True)


def mocked_provider(monkeypatch, *, project_type="zero_data_retention", organization_type="zero_data_retention",
                    response_organization="synthetic-organization", response_project="proj_synthetic", error=None):
    requests = []

    def handle(request):
        requests.append(request)
        assert request.method == "GET" and request.url.scheme == "https" and request.url.host == "api.openai.com"
        assert not request.content
        assert request.headers["openai-organization"] == "synthetic-organization"
        if error:
            return error(request)
        if request.url.path == "/v1/organization/projects/proj_synthetic/data_retention":
            assert request.headers["authorization"] == "Bearer " + ADMIN
            return httpx.Response(200, json={"object": "project.data_retention", "type": project_type})
        if request.url.path == "/v1/organization/data_retention":
            assert request.headers["authorization"] == "Bearer " + ADMIN
            return httpx.Response(200, json={"object": "organization.data_retention", "type": organization_type})
        assert request.url.path == "/v1/models"
        assert request.headers["authorization"] == "Bearer " + INFERENCE
        assert request.headers["openai-project"] == "proj_synthetic"
        headers = {}
        if response_organization is not None:
            headers["openai-organization"] = response_organization
        if response_project is not None:
            headers["openai-project"] = response_project
        return httpx.Response(200, json={"object": "list", "data": [{"id": "gpt-6-luna"}]}, headers=headers)

    transport = httpx.MockTransport(handle)
    monkeypatch.setattr(module, "http_client", lambda: httpx.Client(
        transport=transport, follow_redirects=False, trust_env=False, timeout=10))
    return requests


@pytest.mark.parametrize("project_type", ["zero_data_retention", "organization_default"])
def test_exact_policy_and_key_identity_create_private_runtime_manifest(inputs, monkeypatch, project_type):
    requests = mocked_provider(monkeypatch, project_type=project_type)
    manifest = module.verify(inputs, now=NOW)
    entry = manifest["approvals"][0]
    assert manifest["version"] == "openai-student-data-v1"
    assert entry["project_retention_type"] == project_type
    assert entry["organization_retention_type"] == ("zero_data_retention" if project_type == "organization_default" else None)
    assert entry["key_project_verified"] is True
    assert entry["inference_key_sha256"] == hashlib.sha256(INFERENCE.encode()).hexdigest()
    assert entry["approval_evidence_sha256"] == hashlib.sha256(inputs.approval_evidence_file.read_bytes()).hexdigest()
    assert entry["verified_at"] == NOW.isoformat() and entry["expires_at"] == "2026-11-18T00:00:00+00:00"
    assert stat.S_IMODE(inputs.output.stat().st_mode) == 0o600
    assert json.loads(inputs.output.read_text()) == manifest
    assert ADMIN not in inputs.output.read_text() and INFERENCE not in inputs.output.read_text()
    assert len(requests) == (3 if project_type == "organization_default" else 2)
    for request in requests:
        assert entry["inference_key_sha256"] not in str(request.headers) + str(request.url) + request.content.decode()
    # Assert compatibility with the actual runtime schema, rather than mirroring
    # its field inventory in this operator test.
    sys.path.insert(0, str(Path(__file__).parents[2] / "apps/api"))
    from academy.openai_student_privacy import parse_approvals
    assert parse_approvals(inputs.output.read_text())[0].connection_id == inputs.connection_id


@pytest.mark.parametrize("retention", ["none", "modified_abuse_monitoring", "enhanced_zero_data_retention",
                                      "enhanced_modified_abuse_monitoring", "unknown", [], None])
def test_non_zdr_and_unsupported_enhanced_policy_never_reach_inference_key(inputs, monkeypatch, retention):
    requests = mocked_provider(monkeypatch, project_type=retention)
    with pytest.raises(module.Refused):
        module.verify(inputs, now=NOW)
    assert len(requests) == 1 and not inputs.output.exists()


@pytest.mark.parametrize("organization_type", ["modified_abuse_monitoring", "enhanced_zero_data_retention", "none"])
def test_inherited_policy_must_resolve_to_supported_zdr(inputs, monkeypatch, organization_type):
    requests = mocked_provider(monkeypatch, project_type="organization_default", organization_type=organization_type)
    with pytest.raises(module.Refused):
        module.verify(inputs)
    assert len(requests) == 2 and not inputs.output.exists()


@pytest.mark.parametrize("identity", [("different-organization", "proj_synthetic"),
                                     ("synthetic-organization", "proj_different"),
                                     (None, "proj_synthetic"), ("synthetic-organization", None)])
def test_exact_response_identity_is_required_without_guessing(inputs, monkeypatch, identity):
    requests = mocked_provider(monkeypatch, response_organization=identity[0], response_project=identity[1])
    with pytest.raises(module.Refused, match="exactly match"):
        module.verify(inputs)
    assert len(requests) == 2 and not inputs.output.exists()


@pytest.mark.parametrize("failure", ["http", "redirect", "network", "malformed", "large"])
def test_failures_are_bounded_never_echo_keys_or_raw_provider_text_and_never_retry(inputs, monkeypatch, capsys, failure):
    sentinel = "PRIVATE_PROVIDER_BODY " + ADMIN + " " + INFERENCE

    def error(request):
        if failure == "network":
            raise httpx.ConnectError(sentinel, request=request)
        if failure == "redirect":
            return httpx.Response(302, headers={"location": "https://other.example/"}, text=sentinel)
        if failure == "malformed":
            return httpx.Response(200, text=sentinel)
        if failure == "large":
            return httpx.Response(200, content=b"x" * (module.MAX_BODY + 1))
        return httpx.Response(403, text=sentinel)

    requests = mocked_provider(monkeypatch, error=error)
    assert module.main(arguments(inputs)) == 1
    output = capsys.readouterr()
    assert "Verification refused" in output.err and output.out == ""
    assert all(value not in output.err for value in (ADMIN, INFERENCE, "PRIVATE_PROVIDER_BODY"))
    assert len(requests) == 1 and not inputs.output.exists()


def arguments(args):
    argv = []
    for key, value in vars(args).items():
        option = "--" + key.replace("_", "-")
        if key == "image_inputs_reviewed":
            if value:
                argv.append(option)
        elif key == "model":
            for item in value:
                argv.extend([option, item])
        else:
            argv.extend([option, str(value)])
    return argv


@pytest.mark.parametrize("target", ["admin_key_file", "inference_key_file", "approval_evidence_file"])
@pytest.mark.parametrize("problem", ["permissions", "symlink", "empty", "wrong_owner"])
def test_insecure_or_empty_inputs_refuse_before_network(inputs, monkeypatch, target, problem):
    path = getattr(inputs, target)
    if problem == "permissions":
        path.chmod(0o644)
    elif problem == "symlink":
        actual = path.with_suffix(".actual")
        path.rename(actual)
        path.symlink_to(actual)
    elif problem == "empty":
        path.write_bytes(b"")
    else:
        monkeypatch.setattr(module.os, "geteuid", lambda: os.stat(path).st_uid + 1)
    requests = mocked_provider(monkeypatch)
    with pytest.raises(module.Refused):
        module.verify(inputs)
    assert requests == [] and not inputs.output.exists()


@pytest.mark.parametrize("problem", ["exists", "symlink", "public_parent", "expiry", "no_image_review", "missing_model"])
def test_no_manifest_overwrite_and_invalid_review_inputs(inputs, monkeypatch, problem):
    if problem == "exists":
        inputs.output.write_text("KEEP EXISTING RECEIPT")
    elif problem == "symlink":
        inputs.output.symlink_to(inputs.approval_evidence_file)
    elif problem == "public_parent":
        inputs.output.parent.chmod(0o755)
    elif problem == "expiry":
        inputs.valid_for_days = 31
    elif problem == "no_image_review":
        inputs.image_inputs_reviewed = False
    else:
        inputs.model = ["unavailable-model"]
    requests = mocked_provider(monkeypatch)
    with pytest.raises(module.Refused):
        module.verify(inputs)
    assert len(requests) == (2 if problem == "missing_model" else 0)
    if problem == "exists":
        assert inputs.output.read_text() == "KEEP EXISTING RECEIPT"


def test_success_summary_excludes_private_key_fingerprints(inputs, monkeypatch, capsys):
    mocked_provider(monkeypatch)
    assert module.main(arguments(inputs)) == 0
    output = capsys.readouterr()
    assert output.err == ""
    assert json.loads(output.out) == {"status": "PASS", "manifest_created": True,
        "inference_calls": 0, "provider_approval_granted_by_tool": False}
    assert hashlib.sha256(INFERENCE.encode()).hexdigest() not in output.out


def test_concurrent_output_creation_is_never_replaced(inputs, monkeypatch):
    mocked_provider(monkeypatch)
    write_new = module._write_new

    def race(path, manifest):
        path.write_text("CONCURRENT RECEIPT")
        return write_new(path, manifest)

    monkeypatch.setattr(module, "_write_new", race)
    with pytest.raises(module.Refused, match="not replaced"):
        module.verify(inputs)
    assert inputs.output.read_text() == "CONCURRENT RECEIPT"
    assert not list(inputs.output.parent.glob(".openai-student-controls-*"))


def test_real_http_client_disables_redirects_proxy_and_retries(monkeypatch):
    calls = {}
    monkeypatch.setattr(module.httpx, "Client", lambda **kwargs: calls.update(client=kwargs))
    monkeypatch.setattr(module.httpx, "HTTPTransport", lambda **kwargs: calls.update(transport=kwargs))
    module.http_client()
    assert calls["transport"] == {"retries": 0, "trust_env": False}
    assert calls["client"]["follow_redirects"] is False and calls["client"]["trust_env"] is False
