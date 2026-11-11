#!/usr/bin/env python3
"""Read OpenAI retention/credential metadata and create a private review manifest.

Requires existing written approval evidence and separate operator Admin access.
Only GETs official OpenAI endpoints; never sends student content or inference.
The operator must supply the selected Academy connection's exact inference key
and independently inspected encrypted-credential fingerprint. This utility cannot
derive that encrypted-record fingerprint from a plaintext key.
"""
import argparse
from datetime import datetime, timedelta, timezone
import hashlib
import json
import os
from pathlib import Path
import re
import stat
import sys
import tempfile
import time

import httpx

ENDPOINT = "https://api.openai.com/v1"
VERSION = "openai-student-data-v1"
MAX_BODY = 1024 * 1024


class Refused(Exception):
    """A fixed, credential-free operator diagnostic."""


def canonical(value):
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


def _no_symlinks(path):
    try:
        absolute = Path(os.path.abspath(path))
        if any(part.is_symlink() for part in (absolute, *absolute.parents)):
            raise Refused("Symlink paths are not permitted")
        return absolute
    except (OSError, ValueError):
        raise Refused("A private input/output path could not be inspected securely") from None


def private_file(path, label, limit):
    path = _no_symlinks(path)
    descriptor = None
    try:
        descriptor = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
        metadata = os.fstat(descriptor)
        if (not stat.S_ISREG(metadata.st_mode) or metadata.st_uid != os.geteuid()
                or metadata.st_mode & 0o077 or not 0 < metadata.st_size <= limit):
            raise Refused(label + " must be a nonempty owner-only regular file")
        with os.fdopen(descriptor, "rb") as stream:
            descriptor = None
            value = stream.read(limit + 1)
        if not value.strip() or len(value) > limit:
            raise Refused(label + " must be nonempty and within its size limit")
        return value
    except OSError:
        raise Refused(label + " could not be read securely") from None
    finally:
        if descriptor is not None:
            os.close(descriptor)


def _key(path, label, *, admin):
    raw = private_file(path, label, 4096)
    try:
        value = raw.decode("ascii").strip()
    except UnicodeError:
        raise Refused(label + " has an invalid credential format") from None
    if (not 20 <= len(value) <= 2048 or any(ord(char) < 33 or ord(char) > 126 for char in value)
            or not value.startswith("sk-") or value.startswith("sk-admin-") != admin):
        raise Refused(label + " has an invalid credential type or format")
    return value


def output_path(path):
    path = _no_symlinks(path)
    if os.path.lexists(path):
        raise Refused("Output already exists; no overwrite is permitted")
    try:
        metadata = path.parent.stat()
    except OSError:
        raise Refused("Output requires an existing private parent directory") from None
    if (not stat.S_ISDIR(metadata.st_mode) or metadata.st_uid != os.geteuid()
            or metadata.st_mode & 0o077):
        raise Refused("Output requires an owner-only parent directory")
    return path


def http_client():
    return httpx.Client(follow_redirects=False, trust_env=False,
                        timeout=httpx.Timeout(10, connect=5),
                        transport=httpx.HTTPTransport(retries=0, trust_env=False))


def _unique(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("duplicate field")
        result[key] = value
    return result


def get_metadata(client, path, key, organization, project=None):
    headers = {"Authorization": "Bearer " + key, "OpenAI-Organization": organization,
               "Accept": "application/json"}
    if project:
        headers["OpenAI-Project"] = project
    started = time.monotonic()
    try:
        with client.stream("GET", ENDPOINT + path, headers=headers) as response:
            if response.status_code != 200:
                raise Refused("OpenAI metadata read failed; check Admin access, project binding and approval")
            raw = bytearray()
            for chunk in response.iter_bytes():
                raw.extend(chunk)
                if len(raw) > MAX_BODY or time.monotonic() - started > 30:
                    raise Refused("OpenAI metadata response exceeded its bound")
            result = json.loads(raw, object_pairs_hook=_unique,
                                parse_constant=lambda _: (_ for _ in ()).throw(ValueError()))
            if not isinstance(result, dict):
                raise ValueError()
            identity = {name: response.headers.get("openai-" + name)
                        for name in ("organization", "project")}
            return result, identity
    except Refused:
        raise
    except (httpx.HTTPError, OSError, ValueError, TypeError, RecursionError):
        raise Refused("OpenAI metadata read failed or returned invalid metadata; no retry was made") from None


def _retention(value, expected_object):
    if (set(value) != {"object", "type"} or value["object"] != expected_object
            or not isinstance(value["type"], str)):
        raise Refused("OpenAI retention response has an unexpected schema")
    return value["type"]


def _require_zdr(value):
    if value in {"enhanced_zero_data_retention", "enhanced_modified_abuse_monitoring"}:
        raise Refused("Enhanced retention requires a separate supported-policy/PSP review; no manifest was created")
    if value != "zero_data_retention":
        raise Refused("Effective OpenAI policy is not explicit Zero Data Retention")


def _write_new(path, value):
    temporary = None
    try:
        descriptor, temporary = tempfile.mkstemp(prefix=".openai-student-controls-", dir=path.parent)
        with os.fdopen(descriptor, "wb") as stream:
            stream.write((canonical(value) + "\n").encode())
            stream.flush()
            os.fsync(stream.fileno())
        # Atomic creation with no replacement, including a concurrent new file.
        os.link(temporary, path, follow_symlinks=False)
    except OSError:
        raise Refused("Could not create a new private manifest; existing output was not replaced") from None
    finally:
        if temporary is not None:
            os.unlink(temporary)


def verify(args, *, now=None):
    if (not re.fullmatch(r"[A-Za-z0-9_.-]{1,160}", args.organization_id)
            or not re.fullmatch(r"proj_[A-Za-z0-9_-]{1,160}", args.project_id)
            or not 1 <= len(args.connection_id) <= 64
            or not re.fullmatch(r"[a-f0-9]{64}", args.credential_fingerprint)
            or not 1 <= args.valid_for_days <= 30
            or not args.image_inputs_reviewed
            or not 1 <= len(args.model) <= 100 or len(set(args.model)) != len(args.model)
            or any(not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._:/-]{0,149}", model) for model in args.model)):
        raise Refused("Invalid expected identity, models, fingerprint, review confirmation or expiry")
    destination = output_path(args.output)
    admin_key = _key(args.admin_key_file, "Admin key file", admin=True)
    inference_key = _key(args.inference_key_file, "Inference key file", admin=False)
    approval = private_file(args.approval_evidence_file, "Approval evidence file", MAX_BODY)
    with http_client() as client:
        project, _ = get_metadata(client, "/organization/projects/" + args.project_id + "/data_retention",
                                  admin_key, args.organization_id)
        project_type = _retention(project, "project.data_retention")
        organization = None
        if project_type == "organization_default":
            organization, _ = get_metadata(client, "/organization/data_retention", admin_key, args.organization_id)
            effective = _retention(organization, "organization.data_retention")
        else:
            effective = project_type
        _require_zdr(effective)
        models, identity = get_metadata(client, "/models", inference_key, args.organization_id, args.project_id)
        if identity != {"organization": args.organization_id, "project": args.project_id}:
            raise Refused("Inference key response organization/project does not exactly match the reviewed identity")
        data = models.get("data")
        if models.get("object") != "list" or not isinstance(data, list) or len(data) > 10000:
            raise Refused("OpenAI model metadata has an unexpected schema")
        available = {item.get("id") for item in data if isinstance(item, dict) and isinstance(item.get("id"), str)}
        if not set(args.model) <= available:
            raise Refused("A reviewed model is unavailable to the exact inference credential")
    verified = datetime.now(timezone.utc) if now is None else now
    if verified.tzinfo is None:
        raise Refused("Verification requires an aware timestamp")
    evidence = {"project_id": args.project_id, "organization_id": args.organization_id,
                "project_retention": project, "organization_retention": organization,
                "inference_identity": identity, "reviewed_models": sorted(args.model)}
    manifest = {"version": VERSION, "approvals": [{
        "connection_id": args.connection_id, "credential_fingerprint": args.credential_fingerprint,
        "inference_key_sha256": hashlib.sha256(inference_key.encode()).hexdigest(),
        "endpoint": ENDPOINT, "organization_id": args.organization_id, "project_id": args.project_id,
        "project_retention_type": project_type,
        "organization_retention_type": effective if organization is not None else None,
        "image_inputs_reviewed": True, "reviewed_models": sorted(args.model),
        "approval_evidence_sha256": hashlib.sha256(approval).hexdigest(),
        "evidence_sha256": hashlib.sha256(canonical(evidence).encode()).hexdigest(),
        "key_project_verified": True, "source": "openai-admin-api-readback",
        "verified_at": verified.isoformat(), "expires_at": (verified + timedelta(days=args.valid_for_days)).isoformat(),
    }]}
    _write_new(destination, manifest)
    return manifest


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    for name in ("admin-key-file", "inference-key-file", "approval-evidence-file", "output"):
        parser.add_argument("--" + name, type=Path, required=True)
    for name in ("connection-id", "credential-fingerprint", "organization-id", "project-id"):
        parser.add_argument("--" + name, required=True)
    parser.add_argument("--model", action="append", required=True)
    parser.add_argument("--image-inputs-reviewed", action="store_true", required=True)
    parser.add_argument("--valid-for-days", type=int, default=7)
    args = parser.parse_args(argv)
    try:
        verify(args)
    except Refused as error:
        print("Verification refused: " + str(error), file=sys.stderr)
        return 1
    except (OSError, ValueError, TypeError):
        print("Verification refused: private metadata verification could not complete safely", file=sys.stderr)
        return 1
    print(json.dumps({"status": "PASS", "manifest_created": True, "inference_calls": 0,
                      "provider_approval_granted_by_tool": False}, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
