"""No student draft, model credentials, tools or custom prompts are accepted."""
from pydantic import BaseModel, ConfigDict, Field, model_validator


class LessonInput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    teacher_profile_id: str = Field(min_length=1, max_length=64)
    message: str = Field(min_length=1, max_length=10000)
    idempotency_key: str = Field(min_length=8, max_length=100, pattern=r"^[a-zA-Z0-9_-]+$")
    reviewed_practice: bool = Field(default=False, strict=True)
    practice_lesson_id: str | None = Field(default=None, max_length=80, pattern=r"^reference:ny-g3-[a-z0-9-]+$")

    @model_validator(mode="after")
    def practice_context_only(self):
        if self.practice_lesson_id is not None and not self.reviewed_practice:
            raise ValueError("A practice lesson belongs to reviewed practice chat")
        if self.reviewed_practice and len(self.message) > 1200:
            raise ValueError("Practice questions are limited to 1200 characters")
        return self
