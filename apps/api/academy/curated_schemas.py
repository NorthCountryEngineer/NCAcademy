"""Private lesson inputs are task context, never executable tutor instructions."""
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator


class Strict(BaseModel):
    model_config = ConfigDict(extra="forbid")


class CuratedImage(Strict):
    name: str = Field(min_length=1, max_length=120)
    data_url: str = Field(min_length=1, max_length=175000)

    @field_validator("name")
    @classmethod
    def safe_name(cls, value):
        value = value.strip()
        if not value or any(ord(char) < 32 or ord(char) == 127 for char in value) or "/" in value or "\\" in value:
            raise ValueError("Use a short image filename without paths or control characters")
        return value


class CuratedPreferences(Strict):
    pace: Literal["gentle", "standard"] = "standard"
    formats: list[Literal["read", "visual", "listen"]] = Field(default_factory=lambda: ["read", "visual"], min_length=1, max_length=3)

    @field_validator("formats")
    @classmethod
    def unique_formats(cls, value):
        if len(value) != len(set(value)) or "read" not in value:
            raise ValueError("Include readable lesson text and choose each format once")
        # Stable request fingerprints regardless of checkbox order.
        return [item for item in ("read", "visual", "listen") if item in value]


class CuratedLessonInput(Strict):
    teacher_profile_id: str = Field(min_length=1, max_length=64)
    message: str = Field(default="", max_length=10000)
    images: list[CuratedImage] = Field(default_factory=list, max_length=2)
    image_use_confirmed: bool = Field(default=False, strict=True)
    preferences: CuratedPreferences = Field(default_factory=CuratedPreferences)
    idempotency_key: str = Field(min_length=8, max_length=100, pattern=r"^[a-zA-Z0-9_-]+$")

    @model_validator(mode="after")
    def actual_context(self):
        if not self.message.strip() and not self.images:
            raise ValueError("Explain the topic or attach an assignment picture")
        return self


class CuratedProgress(Strict):
    section_id: str = Field(min_length=1, max_length=40)
    event: Literal["viewed", "read_aloud", "reflection"]
    response: str | None = Field(default=None, max_length=2000)

    @model_validator(mode="after")
    def response_event(self):
        if self.response is not None and self.event != "reflection":
            raise ValueError("Only a reflection event accepts a student response")
        if self.event == "reflection" and (self.response is None or not self.response.strip()):
            raise ValueError("A reflection requires your own nonblank response")
        return self
