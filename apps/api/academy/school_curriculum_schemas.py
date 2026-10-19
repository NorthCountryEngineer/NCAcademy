"""School curriculum intent and explicit, bounded onboarding commands."""
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, StrictInt, field_validator, model_validator


class Strict(BaseModel):
    model_config = ConfigDict(extra="forbid")


class SchoolMetadata(Strict):
    key: str = Field(min_length=1, max_length=80)
    value: str = Field(max_length=1000)

    @field_validator("key")
    @classmethod
    def meaningful_key(cls, value):
        value = value.strip()
        if not value or any(ord(char) < 32 for char in value):
            raise ValueError("Metadata keys require visible text")
        return value


class SchoolCurriculumInput(Strict):
    mode: Literal["state", "custom", "none"] = "none"
    state: str = Field(default="US-NY", pattern=r"^US-[A-Z]{2}$")
    academic_year: str = Field(default="2026-2027", pattern=r"^20[0-9]{2}-20[0-9]{2}$")
    grades: list[Annotated[StrictInt, Field(ge=-1, le=12)]] = Field(default_factory=list, max_length=14)
    subjects: list[str] = Field(default_factory=list, max_length=80)
    custom_name: str = Field(default="", max_length=200)
    custom_description: str = Field(default="", max_length=10000)

    @field_validator("grades")
    @classmethod
    def unique_grades(cls, value):
        if len(value) != len(set(value)):
            raise ValueError("Choose unique grade years")
        return sorted(value)

    @field_validator("subjects")
    @classmethod
    def unique_subjects(cls, value):
        import re
        if len(value) != len(set(value)) or any(not re.fullmatch(r"[a-z][a-z0-9_]{0,79}", item) for item in value):
            raise ValueError("Choose unique explicit subject adapter codes")
        return sorted(value)

    @model_validator(mode="after")
    def valid_configuration(self):
        start, end = map(int, self.academic_year.split("-"))
        if end != start + 1:
            raise ValueError("Academic year must span consecutive years")
        if len(self.grades) * len(self.subjects) > 1120:
            raise ValueError("Select at most 1120 grade and subject combinations")
        if self.mode == "state" and (not self.grades or not self.subjects):
            raise ValueError("State curriculum requires grade years and subjects")
        if self.mode == "custom" and not self.custom_name.strip():
            raise ValueError("Name the planned custom curriculum")
        return self


class CurriculumRunInput(Strict):
    idempotency_key: str = Field(min_length=8, max_length=100, pattern=r"^[a-zA-Z0-9_-]+$")
    expected_school_version: int = Field(ge=1, strict=True)


class CurriculumPublishInput(CurriculumRunInput):
    run_id: str = Field(min_length=1, max_length=64)
    plan_sha256: str = Field(pattern=r"^[a-f0-9]{64}$")
    course_keys: list[str] = Field(min_length=1, max_length=100)

    @field_validator("course_keys")
    @classmethod
    def unique_course_keys(cls, value):
        import re
        if len(value) != len(set(value)) or any(not re.fullmatch(r"[a-f0-9]{32}", key) for key in value):
            raise ValueError("Choose unique exact planned course keys")
        return sorted(value)
