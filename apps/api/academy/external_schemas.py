"""Bounded, explicit external-material review inputs; never agent credentials."""
from typing import Literal
from urllib.parse import urlsplit

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator


class Strict(BaseModel):
    model_config = ConfigDict(extra="forbid")


class AuditAgentInput(Strict):
    expected_version: int | None = Field(default=None, ge=1, strict=True)
    name: str = Field(min_length=1, max_length=150)
    connection_id: str = Field(min_length=1, max_length=64)
    model: str = Field(min_length=1, max_length=150, pattern=r"^[a-zA-Z0-9][a-zA-Z0-9._:/-]{0,149}$")
    enabled: bool = Field(default=True, strict=True)
    max_input_chars: int = Field(default=30000, ge=1000, le=50000, strict=True)
    max_output_tokens: int = Field(default=700, ge=200, le=1200, strict=True)
    max_daily_reviews: int = Field(default=30, ge=1, le=100, strict=True)
    max_daily_tokens: int = Field(default=1000000, ge=10000, le=5000000, strict=True)
    review_days: int = Field(default=7, ge=1, le=30, strict=True)
    require_human_approval: bool = Field(default=True, strict=True)
    policy_notes: str = Field(default="", max_length=2000)

    @field_validator("name")
    @classmethod
    def named(cls, value):
        if not value.strip():
            raise ValueError("Audit agent name is required")
        return value.strip()


class ExternalInput(Strict):
    kind: Literal["youtube", "external_text"]
    source_url: str = Field(min_length=1, max_length=2000)
    title: str = Field(min_length=1, max_length=200)
    teaching_notes: str = Field(default="", max_length=10000)
    transcript: str = Field(default="", max_length=50000)
    content: str = Field(default="", max_length=50000)
    rights_confirmed: Literal[True]
    start_seconds: int = Field(default=0, ge=0, le=86400, strict=True)
    end_seconds: int | None = Field(default=None, ge=1, le=86400, strict=True)

    @field_validator("rights_confirmed", mode="before")
    @classmethod
    def explicit_rights(cls, value):
        if value is not True:
            raise ValueError("Explicit rights confirmation is required")
        return value

    @field_validator("title")
    @classmethod
    def named(cls, value):
        if not value.strip():
            raise ValueError("Material title is required")
        return value.strip()

    @field_validator("source_url")
    @classmethod
    def https_attribution(cls, value):
        try:
            parsed = urlsplit(value)
            if parsed.scheme != "https" or not parsed.hostname or parsed.username or parsed.password or parsed.port not in (None, 443) or any(ord(c) <= 32 or ord(c) == 127 for c in value):
                raise ValueError()
        except ValueError:
            raise ValueError("Use an HTTPS source URL without credentials") from None
        return value

    @model_validator(mode="after")
    def evidence(self):
        if self.kind == "youtube" and (not self.transcript.strip() or not self.teaching_notes.strip() or self.content):
            raise ValueError("YouTube requires a supplied transcript and original teaching notes, without a content copy")
        if self.kind == "external_text" and (not self.content.strip() or self.transcript or self.start_seconds or self.end_seconds):
            raise ValueError("External text requires supplied content and no video fields")
        if self.end_seconds is not None and self.end_seconds <= self.start_seconds:
            raise ValueError("Video segment end must follow start")
        if len(self.transcript) + len(self.content) + len(self.teaching_notes) > 60000:
            raise ValueError("Submitted evidence exceeds the material limit")
        return self


class ExternalUpdate(ExternalInput):
    expected_version: int = Field(ge=1, strict=True)


class ReviewInput(Strict):
    expected_version: int = Field(ge=1, strict=True)
    idempotency_key: str = Field(min_length=16, max_length=100, pattern=r"^[A-Za-z0-9_-]+$")


class ExternalApprovalInput(Strict):
    expected_version: int = Field(ge=1, strict=True)
    review_id: str = Field(min_length=1, max_length=64)
    snapshot_sha256: str = Field(pattern=r"^[a-f0-9]{64}$")
    watched_video: bool = Field(default=False, strict=True)
    transcript_complete: bool = Field(default=False, strict=True)


class ExternalRevokeInput(Strict):
    expected_version: int = Field(ge=1, strict=True)
    reason: str = Field(min_length=1, max_length=1000)
