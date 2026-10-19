"""Curriculum describes an intended alignment, never a self-issued verification."""
from typing import Annotated
from pydantic import BaseModel, ConfigDict, Field, StrictInt, field_validator, model_validator


class Strict(BaseModel):
    model_config = ConfigDict(extra="forbid")


class CurriculumInput(Strict):
    state: str = Field(min_length=5, max_length=12, pattern=r"^US-[A-Z]{2}$")
    subject: str = Field(min_length=1, max_length=80, pattern=r"^[a-z][a-z0-9_]*$")
    grades: list[Annotated[StrictInt, Field(ge=-1, le=12)]] = Field(min_length=1, max_length=14)
    academic_year: str = Field(pattern=r"^20[0-9]{2}-20[0-9]{2}$")
    standards_version: str | None = Field(default=None, min_length=1, max_length=100)

    @field_validator("grades")
    @classmethod
    def valid_grades(cls, value):
        if len(value) != len(set(value)) or any(type(grade) is not int or not -1 <= grade <= 12 for grade in value):
            raise ValueError("Choose unique grades from prekindergarten (-1) and kindergarten (0) through grade 12")
        return sorted(value)

    @model_validator(mode="after")
    def consecutive_years(self):
        start, end = map(int, self.academic_year.split("-"))
        if end != start + 1:
            raise ValueError("Academic year must span consecutive years")
        return self


class CorpusApprovalInput(Strict):
    corpus_id: str = Field(min_length=1, max_length=64)
    snapshot_sha256: str = Field(pattern=r"^[a-f0-9]{64}$")


class CorpusRetrievalInput(Strict):
    query: str = Field(min_length=1, max_length=1000)
    top_k: int = Field(default=5, ge=1, le=8)

    @field_validator("query")
    @classmethod
    def meaningful_query(cls, value):
        value = value.strip()
        if not value:
            raise ValueError("A retrieval question is required")
        return value
