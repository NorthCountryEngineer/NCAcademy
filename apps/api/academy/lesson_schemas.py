"""No student draft, model credentials, tools or custom prompts are accepted."""
from pydantic import BaseModel, ConfigDict, Field


class LessonInput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    teacher_profile_id: str = Field(min_length=1, max_length=64)
    message: str = Field(min_length=1, max_length=10000)
    idempotency_key: str = Field(min_length=8, max_length=100, pattern=r"^[a-zA-Z0-9_-]+$")
