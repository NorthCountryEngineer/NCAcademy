from typing import Literal
from pydantic import BaseModel, ConfigDict, Field, field_validator
from .standards_schemas import CurriculumInput
from .school_curriculum_schemas import SchoolCurriculumInput, SchoolMetadata
from .teacher_schemas import TeacherPicture


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


class CourseCatalogSelectionInput(Strict):
    state: str = Field(pattern=r"^US-[A-Z]{2}$")
    academic_year: str = Field(pattern=r"^20[0-9]{2}-20[0-9]{2}$")
    course_code: str = Field(min_length=1, max_length=20, pattern=r"^[A-Za-z0-9]+$")
    catalog_sha256: str = Field(pattern=r"^[a-f0-9]{64}$")


class CourseInput(Strict):
    title: str = Field(min_length=1, max_length=200)
    description: str = Field(default="", max_length=10000)
    school_id: str | None = Field(default=None, min_length=1, max_length=64)
    teacher_id: str | None = Field(default=None, min_length=1, max_length=64)
    curriculum: CurriculumInput | None = None
    course_catalog: CourseCatalogSelectionInput | None = None


class AssignmentInput(Strict):
    title: str = Field(min_length=1, max_length=200)
    module: str = Field(default="General", max_length=200)
    instructions: str = Field(min_length=1, max_length=20000)
    tutor_policy: TutorPolicy = Field(default_factory=TutorPolicy)


class EnrollmentInput(Strict):
    user_id: str = Field(min_length=1, max_length=64)
    role: Literal["student", "teacher", "instructor"] = "student"


class ContentInput(Strict):
    content: str = Field(max_length=100000)


class ReviewInput(Strict):
    feedback: str = Field(min_length=1, max_length=10000)


class TutorInput(Strict):
    message: str = Field(min_length=1, max_length=10000)
    idempotency_key: str = Field(min_length=8, max_length=100, pattern=r"^[a-zA-Z0-9_-]+$")
    teacher_profile_id: str | None = Field(default=None, min_length=1, max_length=64)


class ConnectionInput(Strict):
    school_id: str | None = Field(default=None, min_length=1, max_length=64)
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


class SchoolInput(Strict):
    name: str = Field(min_length=1, max_length=200)
    description: str = Field(default="", max_length=10000)
    core_resource_id: str | None = Field(default=None, min_length=1, max_length=200)
    custom_metadata: list[SchoolMetadata] = Field(default_factory=list, max_length=30)
    logo: TeacherPicture | None = None
    curriculum: SchoolCurriculumInput = Field(default_factory=SchoolCurriculumInput)
    expected_version: int | None = Field(default=None, ge=1, strict=True)

    @field_validator("custom_metadata")
    @classmethod
    def unique_metadata(cls, value):
        keys = [item.key.casefold() for item in value]
        if len(keys) != len(set(keys)):
            raise ValueError("School metadata keys must be unique")
        return value


class MembershipInput(Strict):
    role: Literal["school", "teacher", "student"]
    active: bool = True


class AgentPlacementInput(Strict):
    name: str = Field(min_length=1, max_length=200)
    core_agent_id: str
    core_binding_id: str
    core_consumer_service_id: str
    environment: str = Field(min_length=1, max_length=64, pattern=r"^[a-zA-Z0-9_-]+$")
    task_id: str | None = None
    active: bool = True
    purpose: str = Field(default="", max_length=80, pattern=r"^[-a-zA-Z0-9_]*$")
    core_resource_id: str = Field(min_length=1, max_length=200)

    @field_validator("core_agent_id", "core_binding_id", "core_consumer_service_id", "task_id")
    @classmethod
    def uuid_value(cls, value):
        from uuid import UUID
        if value is not None and str(UUID(value)) != value:
            raise ValueError("Canonical UUID required")
        return value


class ResourceInput(Strict):
    name: str = Field(min_length=1, max_length=200)
    kind: Literal["text", "core"] = "text"
    content: str = Field(default="", max_length=20000)
    core_resource_type: str | None = Field(default=None, min_length=1, max_length=100, pattern=r"^[a-zA-Z0-9_-]+$")
    core_resource_id: str | None = Field(default=None, min_length=1, max_length=200)
    permission: str = Field(default="academy-use", min_length=1, max_length=100, pattern=r"^[a-zA-Z0-9_-]+$")
    audience: Literal["all", "teachers"] = "all"
    course_id: str | None = Field(default=None, min_length=1, max_length=64)
