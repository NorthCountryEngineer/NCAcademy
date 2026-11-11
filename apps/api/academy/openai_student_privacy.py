"""Operator-verified OpenAI controls, separate from a school's tutoring approval.

This trusted deployment manifest records provider readback; a school textbox is
not provider approval. It contains no API key and makes no network requests.
"""
import hashlib
import hmac
import json
from datetime import datetime, timedelta, timezone
from typing import Literal

from fastapi import HTTPException
from pydantic import BaseModel, ConfigDict, Field, model_validator

from .db import canonical

ENDPOINT = "https://api.openai.com/v1"
VERSION = "openai-student-data-v1"
ERROR = "OpenAI student data controls require current operator-verified project Zero Data Retention approval"


class Approval(BaseModel):
    model_config = ConfigDict(extra="forbid")
    connection_id: str = Field(min_length=1, max_length=64, strict=True)
    credential_fingerprint: str = Field(pattern=r"^[a-f0-9]{64}$", strict=True)
    inference_key_sha256: str = Field(pattern=r"^[a-f0-9]{64}$", strict=True)
    endpoint: Literal[ENDPOINT]
    organization_id: str = Field(pattern=r"^[A-Za-z0-9_.-]{1,160}$", strict=True)
    project_id: str = Field(pattern=r"^proj_[A-Za-z0-9_-]{1,160}$", strict=True)
    project_retention_type: Literal["organization_default", "zero_data_retention"]
    organization_retention_type: Literal["zero_data_retention"] | None
    image_inputs_reviewed: bool = Field(strict=True)
    reviewed_models: list[str] = Field(min_length=1, max_length=100)
    approval_evidence_sha256: str = Field(pattern=r"^[a-f0-9]{64}$", strict=True)
    evidence_sha256: str = Field(pattern=r"^[a-f0-9]{64}$", strict=True)
    key_project_verified: Literal[True]
    source: Literal["openai-admin-api-readback"]
    verified_at: datetime
    expires_at: datetime

    @model_validator(mode="before")
    @classmethod
    def explicit_key_verification(cls, value):
        if not isinstance(value, dict) or value.get("key_project_verified") is not True:
            raise ValueError("Explicit credential project verification required")
        return value

    @model_validator(mode="after")
    def valid(self):
        import re
        if (any(not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._:/-]{0,149}", item) for item in self.reviewed_models)
                or len(set(self.reviewed_models)) != len(self.reviewed_models)
                or self.verified_at.tzinfo is None or self.expires_at.tzinfo is None
                or not self.verified_at < self.expires_at <= self.verified_at + timedelta(days=30)
                or self.project_retention_type == "organization_default" and self.organization_retention_type != "zero_data_retention"):
            raise ValueError("Invalid OpenAI student approval")
        return self


class Manifest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    version: Literal[VERSION]
    approvals: list[Approval] = Field(max_length=100)


def _unique_object(pairs):
    value = {}
    for key, item in pairs:
        if key in value:
            raise ValueError("Duplicate approval key")
        value[key] = item
    return value


def parse_approvals(raw):
    if raw in ("", "{}"):
        return []
    if not isinstance(raw, str) or len(raw.encode()) > 131072:
        raise ValueError("Invalid OpenAI student approval manifest")
    value = Manifest.model_validate(json.loads(raw, object_pairs_hook=_unique_object))
    if len({item.connection_id for item in value.approvals}) != len(value.approvals):
        raise ValueError("Duplicate OpenAI student connection approval")
    return value.approvals


def require_student_openai_approval(settings, snapshot):
    """Return current nonsecret proofs for the exact school connection snapshots.

    Local-family opt-in requires OpenAI for every phase. Legacy non-OpenAI
    configurations remain outside this OpenAI-specific contract. An OpenAI
    phase still requires proof for a Core student of unknown age.
    """
    configuration, connections = snapshot["configuration"], snapshot["connections"]
    require_all = configuration.get("allow_local_students") is True
    selected = {connection["id"]: connection for connection in connections.values()
                if connection["endpoint"] == ENDPOINT}
    if require_all and any(connection["endpoint"] != ENDPOINT for connection in connections.values()):
        raise HTTPException(403, ERROR)
    if not selected:
        return {}
    try:
        entries = {item.connection_id: item for item in parse_approvals(settings.openai_student_approvals_json)}
        current = datetime.now(timezone.utc)
        proofs = {}
        for identifier, connection in selected.items():
            entry = entries[identifier]
            models = {configuration[phase]["model"] for phase, target in connections.items() if target["id"] == identifier}
            if (entry.credential_fingerprint != connection["credential_fingerprint"]
                    or entry.endpoint != connection["endpoint"]
                    or not entry.verified_at <= current + timedelta(minutes=5)
                    or not current < entry.expires_at
                    or not models <= set(entry.reviewed_models)
                    or configuration.get("allow_images") and not entry.image_inputs_reviewed):
                raise ValueError("OpenAI approval changed or expired")
            proof = entry.model_dump(mode="json")
            binding = {key: proof[key] for key in ("connection_id", "credential_fingerprint", "inference_key_sha256", "endpoint", "organization_id", "project_id", "project_retention_type", "organization_retention_type", "image_inputs_reviewed")}
            binding["reviewed_models"] = sorted(proof["reviewed_models"])
            proof["binding_sha256"] = hashlib.sha256(canonical(binding).encode()).hexdigest()
            proofs[identifier] = proof
        return proofs
    except (ValueError, KeyError, TypeError, AttributeError, RecursionError):
        raise HTTPException(403, ERROR) from None


def approval_bindings(approvals):
    return {identifier: item["binding_sha256"] for identifier, item in approvals.items()}


def student_openai_request_context(settings, identity, connection, approvals, *, model, api_key):
    if connection["endpoint"] != ENDPOINT:
        return {}
    try:
        proof = approvals[connection["id"]]
        if (proof["credential_fingerprint"] != connection["credential_fingerprint"]
                or model not in proof["reviewed_models"]
                or not hmac.compare_digest(proof["inference_key_sha256"], hashlib.sha256(api_key.encode()).hexdigest())):
            raise ValueError()
        secret = settings.pii_encryption_key or settings.session_secret
        identity_bytes = canonical(["ncacademy-openai-safety-v1", identity["tenant_id"], identity["id"]]).encode()
        opaque = hmac.new(secret.encode(), identity_bytes, hashlib.sha256).hexdigest()
        return {"openai_organization": proof["organization_id"], "openai_project": proof["project_id"],
                "safety_identifier": "academy_" + opaque,
                "openai_inference_key_sha256": proof["inference_key_sha256"]}
    except (KeyError, ValueError, TypeError, AttributeError):
        raise HTTPException(403, ERROR) from None


def provider_prompt_content(content):
    """Minimize metadata only. Arbitrary text and image pixels still need ZDR."""
    if isinstance(content, list):
        return [provider_prompt_content(item) for item in content]
    if not isinstance(content, dict):
        return content
    value = {}
    for key, item in content.items():
        if key in {"message_id", "session_id", "student_id", "owner_id", "tenant_id", "school_id", "created_at"}:
            continue
        if key in {"images", "image_references"} and isinstance(item, list):
            value[key] = [{"label": f"Homework picture {index}", **{name: image[name] for name in ("width", "height") if name in image}}
                          for index, image in enumerate(item, start=1) if isinstance(image, dict)]
        else:
            value[key] = provider_prompt_content(item)
    return value
