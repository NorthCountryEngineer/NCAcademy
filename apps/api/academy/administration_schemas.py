"""Declarative administrative reviews cannot grant authority or register tools."""
from datetime import date
from decimal import Decimal
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator


class Strict(BaseModel):
    model_config = ConfigDict(extra="forbid")


Action = Literal["readiness.inspect", "curriculum.plan", "model.review"]


class SkillInput(Strict):
    expected_version: int = Field(default=0, ge=0, strict=True)
    name: str = Field(min_length=1, max_length=150)
    description: str = Field(default="", max_length=2000)
    base_action: Action = "readiness.inspect"
    instructions: str = Field(default="", max_length=4000)
    enabled: bool = Field(default=True, strict=True)
    archived: bool = Field(default=False, strict=True)

    @field_validator("name")
    @classmethod
    def named(cls, value):
        if not value.strip():
            raise ValueError("A name is required")
        return value.strip()


class ModelRef(Strict):
    connection_id: str = Field(min_length=1, max_length=64)
    model: str = Field(min_length=1, max_length=150, pattern=r"^[a-zA-Z0-9][a-zA-Z0-9._:/-]{0,149}$")


class Pricing(Strict):
    input_per_million: Decimal = Field(ge=0, le=1000, max_digits=12, decimal_places=6)
    output_per_million: Decimal = Field(ge=0, le=1000, max_digits=12, decimal_places=6)
    verified_on: date
    confirmed: Literal[True]

    @field_validator("confirmed", mode="before")
    @classmethod
    def explicit_confirmation(cls, value):
        if value is not True:
            raise ValueError("Explicit pricing confirmation is required")
        return value


class AgentInput(Strict):
    expected_version: int = Field(default=0, ge=0, strict=True)
    name: str = Field(min_length=1, max_length=150)
    description: str = Field(default="", max_length=2000)
    purpose: Literal["school_readiness", "curriculum_planning", "administrative_review"] = "school_readiness"
    instructions: str = Field(default="", max_length=4000)
    context: str = Field(default="", max_length=4000)
    skill_ids: list[str] = Field(default_factory=lambda: ["readiness.inspect"], min_length=1, max_length=10)
    enabled: bool = Field(default=True, strict=True)
    archived: bool = Field(default=False, strict=True)
    execution_mode: Literal["local_review", "model_review"] = "local_review"
    primary: ModelRef | None = None
    max_input_chars: int = Field(default=12000, ge=1000, le=20000, strict=True)
    max_output_tokens: int = Field(default=700, ge=200, le=1500, strict=True)
    max_daily_runs: int = Field(default=10, ge=1, le=30, strict=True)
    max_daily_tokens: int = Field(default=200000, ge=10000, le=1000000, strict=True)
    pricing: Pricing | None = None
    per_run_budget_usd: Decimal | None = Field(default=None, gt=0, le=10, max_digits=12, decimal_places=6)

    _named = field_validator("name")(SkillInput.named.__func__)

    @field_validator("skill_ids")
    @classmethod
    def unique_skills(cls, value):
        if len(set(value)) != len(value) or any(not item or len(item) > 64 for item in value):
            raise ValueError("Select unique registered skills")
        return value

    @model_validator(mode="after")
    def model_configuration(self):
        if self.execution_mode == "model_review" and (not self.primary or not self.pricing or self.per_run_budget_usd is None):
            raise ValueError("Model review requires an integrated model, verified pricing and a run budget")
        return self


class VersionInput(Strict):
    expected_version: int = Field(ge=1, strict=True)


class RunInput(VersionInput):
    idempotency_key: str = Field(min_length=16, max_length=100, pattern=r"^[A-Za-z0-9_-]+$")


class Finding(Strict):
    severity: Literal["info", "warning"]
    code: str = Field(min_length=1, max_length=80, pattern=r"^[a-zA-Z0-9_.-]+$")
    title: str = Field(min_length=1, max_length=200)
    detail: str = Field(default="", max_length=1000)


class AdvisoryReport(Strict):
    summary: str = Field(min_length=1, max_length=2000)
    findings: list[Finding] = Field(default_factory=list, max_length=20)
    recommendations: list[str] = Field(default_factory=list, max_length=20)

    @field_validator("recommendations")
    @classmethod
    def bounded_recommendations(cls, value):
        if any(not item.strip() or len(item) > 1000 for item in value):
            raise ValueError("Recommendations must be bounded text")
        return value
