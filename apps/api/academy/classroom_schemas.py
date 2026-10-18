"""Personal, read-only Classroom commands. Google authority never grants local access."""
from pydantic import BaseModel, ConfigDict, Field


class ClassroomEmptyInput(BaseModel):
    model_config = ConfigDict(extra="forbid")


class ClassroomConnectInput(ClassroomEmptyInput):
    idempotency_key: str = Field(min_length=8, max_length=100, pattern=r"^[A-Za-z0-9_-]+$")


class ClassroomLinkInput(ClassroomConnectInput):
    google_course_id: str = Field(min_length=1, max_length=200, pattern=r"^[A-Za-z0-9_-]+$")
    expected_connection_version: str = Field(min_length=1, max_length=100, pattern=r"^[A-Za-z0-9_-]+$")
