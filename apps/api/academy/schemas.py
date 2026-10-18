from typing import Literal
from pydantic import BaseModel, ConfigDict, Field, field_validator


class Strict(BaseModel):
    model_config = ConfigDict(extra="forbid")


class ApprovedText(Strict):
    id: str = Field(min_length=1, max_length=80, pattern=r"^[a-zA-Z0-9_-]+$")
    text: str = Field(min_length=1, max_length=1500)


class TutorPolicy(Strict):
    mode: Literal["off", "curated", "socratic"] = "off"
    connection_id: str | None = None
    model: str | None = Field(default=None, max_length=150)
    max_turns: int = Field(default=8, ge=1, le=30)
    max_input_chars: int = Field(default=3000, ge=100, le=10000)
    max_output_tokens: int = Field(default=80, ge=30, le=200)
    max_total_tokens: int = Field(default=20000, ge=500, le=200000)
    require_attempt_chars: int = Field(default=40, ge=20, le=2000)
    hints: list[ApprovedText] = Field(default_factory=list, max_length=20)
    questions: list[ApprovedText] = Field(default_factory=list, max_length=20)

    @field_validator("questions")
    @classmethod
    def unique_ids(cls, value, info):
        ids = [item.id for item in info.data.get("hints", []) + value]
        if len(ids) != len(set(ids)):
            raise ValueError("Approved text IDs must be unique")
        return value


class CourseInput(Strict):
    title: str = Field(min_length=1, max_length=200)
    description: str = Field(default="", max_length=10000)


class AssignmentInput(Strict):
    title: str = Field(min_length=1, max_length=200)
    module: str = Field(default="General", max_length=200)
    instructions: str = Field(min_length=1, max_length=20000)
    tutor_policy: TutorPolicy = Field(default_factory=TutorPolicy)


class EnrollmentInput(Strict):
    user_id: str = Field(min_length=1, max_length=64)
    role: Literal["student", "instructor"] = "student"


class ContentInput(Strict):
    content: str = Field(max_length=100000)


class ReviewInput(Strict):
    feedback: str = Field(min_length=1, max_length=10000)


class TutorInput(Strict):
    message: str = Field(min_length=1, max_length=10000)
    idempotency_key: str = Field(min_length=8, max_length=100, pattern=r"^[a-zA-Z0-9_-]+$")


class ConnectionInput(Strict):
    name: str = Field(min_length=1, max_length=100)
    protocol: Literal["openai_compatible"] = "openai_compatible"
    endpoint: str = Field(min_length=1, max_length=500)
    models: list[str] = Field(min_length=1, max_length=30)
    enabled: bool = False
    api_key: str | None = Field(default=None, min_length=1, max_length=4096)
    clear_key: bool = False
    version: int | None = None

    @field_validator("models")
    @classmethod
    def valid_models(cls, value):
        import re
        if len(value) != len(set(value)) or any(not re.fullmatch(r"[a-zA-Z0-9][a-zA-Z0-9._:/-]{0,149}", item) for item in value):
            raise ValueError("Unique explicit model IDs required")
        return value
