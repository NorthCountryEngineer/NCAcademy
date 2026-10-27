"""Bounded inputs for private, reviewed student avatars."""
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator


class Strict(BaseModel):
    model_config = ConfigDict(extra="forbid")


class AvatarPolicy(Strict):
    enabled: bool = Field(default=False, strict=True)
    connection_id: str | None = Field(default=None, min_length=1, max_length=64)
    model: str | None = Field(default=None, min_length=1, max_length=150, pattern=r"^[a-zA-Z0-9][a-zA-Z0-9._:/-]{0,149}$")
    image_capable: bool = Field(default=False, strict=True)
    allow_image_processing: bool = Field(default=False, strict=True)
    sexual_threshold: float = Field(default=0.2, ge=0.05, le=0.95, strict=True)
    violence_threshold: float = Field(default=0.35, ge=0.05, le=0.95, strict=True)
    other_threshold: float = Field(default=0.35, ge=0.05, le=0.95, strict=True)

    @model_validator(mode="after")
    def configured(self):
        if self.enabled and not (self.connection_id and self.model and self.image_capable and self.allow_image_processing):
            raise ValueError("Enabled avatar scanning requires a model and explicit image-processing capability and approval")
        return self


class AvatarPolicyInput(AvatarPolicy):
    expected_version: int = Field(ge=0, strict=True)


class AvatarPolicyApproval(Strict):
    revision_id: str = Field(min_length=1, max_length=64)


class AvatarVersion(Strict):
    expected_version: int = Field(ge=0, strict=True)


class AvatarImage(Strict):
    mime_type: Literal["image/jpeg", "image/png", "image/webp"]
    data_base64: str = Field(min_length=1, max_length=266668)


class AvatarUpload(AvatarVersion):
    idempotency_key: str = Field(min_length=8, max_length=100, pattern=r"^[A-Za-z0-9_-]+$")
    consent_to_scan: bool = Field(default=False, strict=True)
    expected_policy_approval_id: str | None = Field(default=None, min_length=1, max_length=64)
    image: AvatarImage


class AvatarScan(AvatarVersion):
    consent_to_scan: Literal[True]
    expected_policy_approval_id: str = Field(min_length=1, max_length=64)


class AvatarReveal(AvatarVersion):
    acknowledge_sensitive_content: Literal[True]


class AvatarDecision(AvatarVersion):
    decision: Literal["approve", "reject"]
    reason: str = Field(min_length=10, max_length=1000)

    @model_validator(mode="after")
    def substantive(self):
        if len(self.reason.strip()) < 10 or any(ord(character) < 32 and character not in "\n\t" for character in self.reason):
            raise ValueError("Provide a review explanation of at least ten visible characters")
        return self


class AvatarCloseScan(AvatarVersion):
    reason: str = Field(min_length=10, max_length=1000)

    @model_validator(mode="after")
    def substantive(self):
        if len(self.reason.strip()) < 10 or any(ord(character) < 32 and character not in "\n\t" for character in self.reason):
            raise ValueError("Provide a closure explanation of at least ten visible characters")
        return self
