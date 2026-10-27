"""Explicit Game Studio connection, review and local-practice commands."""
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator


class Strict(BaseModel):
    model_config = ConfigDict(extra="forbid")


class GameStudioSettings(Strict):
    expected_version: int = Field(default=0, ge=0, strict=True)
    enabled: bool = Field(default=False, strict=True)
    studio_origin: str = Field(default="", max_length=500)
    api_origin: str = Field(default="", max_length=500)
    host_id: str = Field(default="academy", min_length=1, max_length=64, pattern=r"^[a-z][a-z0-9_-]{0,63}$")

    @field_validator("studio_origin", "api_origin")
    @classmethod
    def public_origin(cls, value):
        if not value:
            return value
        from .game_studio_transport import validate_origin
        return validate_origin(value)

    @model_validator(mode="after")
    def enabled_origins(self):
        if self.enabled and (not self.studio_origin or not self.api_origin):
            raise ValueError("Enabled Game Studio requires both public HTTPS origins")
        return self


class GameStudioVersion(Strict):
    expected_version: int = Field(ge=1, strict=True)


class CourseGameInput(Strict):
    game_id: str = Field(pattern=r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$")
    published_revision: int = Field(ge=1, le=1000000000, strict=True)
    spec_sha256: str = Field(pattern=r"^[a-f0-9]{64}$")
    learning_objective: str = Field(min_length=1, max_length=2000)
    lesson_label: str = Field(default="Practice game", min_length=1, max_length=150)
    idempotency_key: str = Field(min_length=16, max_length=100, pattern=r"^[A-Za-z0-9_-]+$")

    @field_validator("learning_objective", "lesson_label")
    @classmethod
    def visible_text(cls, value):
        if not value.strip() or any(ord(char) < 32 and char not in "\n\t" for char in value):
            raise ValueError("Use a nonblank learning objective and lesson label")
        return value.strip()


class GameApproval(GameStudioVersion):
    reviewed_level_ids: list[str] = Field(min_length=1, max_length=8)
    reviewed: Literal[True]

    @field_validator("reviewed", mode="before")
    @classmethod
    def human_attestation(cls, value):
        if value is not True:
            raise ValueError("Explicit human playtest and content review is required")
        return value

    @field_validator("reviewed_level_ids")
    @classmethod
    def unique_levels(cls, value):
        if len(set(value)) != len(value) or any(item not in {f"level-{index}" for index in range(1, 9)} for item in value):
            raise ValueError("Supply the exact distinct reviewed level IDs")
        return value


class GamePlay(Strict):
    pass
