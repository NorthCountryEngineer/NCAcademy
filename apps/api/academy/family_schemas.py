"""Bounded administration of application-owned family identities."""
import re
from datetime import date
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, SecretStr, field_validator, model_validator


class Strict(BaseModel):
    model_config = ConfigDict(extra="forbid")


class Profile(Strict):
    username: str = Field(min_length=3, max_length=64)
    name: str = Field(min_length=1, max_length=160)
    preferred_name: str = Field(default="", max_length=160)
    date_of_birth: date | None = None
    email: str = Field(default="", max_length=254)
    phone: str = Field(default="", max_length=80)
    notes: str = Field(default="", max_length=4000)

    @field_validator("name")
    @classmethod
    def name_present(cls, value):
        if not value.strip():
            raise ValueError("A name is required")
        return value.strip()

    @field_validator("date_of_birth")
    @classmethod
    def past_birth(cls, value):
        if value is not None and value > date.today():
            raise ValueError("Date of birth cannot be in the future")
        return value


class FamilyCreate(Profile):
    kind: Literal["student", "parent"]
    password: SecretStr
    must_change_password: bool = True
    school_id: str | None = Field(default=None, min_length=1, max_length=64)
    academic_year: str | None = Field(default=None, min_length=1, max_length=40)
    grade: str = Field(default="", max_length=80)
    course_ids: list[str] = Field(default_factory=list, max_length=100)
    student_ids: list[str] = Field(default_factory=list, max_length=100)

    @model_validator(mode="after")
    def enrollment_shape(self):
        validate_ids(self.course_ids)
        validate_ids(self.student_ids)
        if self.kind == "parent" and (self.school_id or self.course_ids or self.academic_year or self.grade):
            raise ValueError("Parents are linked to students, not enrolled in classes")
        if self.kind == "student" and self.student_ids:
            raise ValueError("Only parents link to children")
        if self.school_id and not self.academic_year:
            raise ValueError("An academic year is required for a school enrollment")
        if not self.school_id and (self.academic_year or self.course_ids or self.grade):
            raise ValueError("A school is required for enrollment details")
        return self


class FamilyPatch(Strict):
    expected_version: int = Field(ge=1)
    username: str | None = Field(default=None, min_length=3, max_length=64)
    name: str | None = Field(default=None, min_length=1, max_length=160)
    preferred_name: str | None = Field(default=None, max_length=160)
    date_of_birth: date | None = None
    email: str | None = Field(default=None, max_length=254)
    phone: str | None = Field(default=None, max_length=80)
    notes: str | None = Field(default=None, max_length=4000)

    @model_validator(mode="after")
    def valid_patch(self):
        for field in self.model_fields_set - {"expected_version", "date_of_birth"}:
            if getattr(self, field) is None:
                raise ValueError("Only date of birth may be cleared with null")
        if self.name is not None and not self.name.strip():
            raise ValueError("A name is required")
        if self.date_of_birth is not None and self.date_of_birth > date.today():
            raise ValueError("Date of birth cannot be in the future")
        return self


class FamilyPassword(Strict):
    expected_version: int = Field(ge=1)
    new_password: SecretStr
    must_change_password: bool = True


def validate_ids(values):
    if len(set(values)) != len(values) or any(not isinstance(v, str) or not re.fullmatch(r"[A-Za-z0-9:_-]{1,64}", v) for v in values):
        raise ValueError("Identifiers must be distinct bounded record identifiers")
    return values


class FamilyEnrollment(Strict):
    expected_version: int = Field(ge=1)
    school_id: str = Field(min_length=1, max_length=64)
    academic_year: str = Field(min_length=1, max_length=40)
    grade: str = Field(default="", max_length=80)
    course_ids: list[str] = Field(default_factory=list, max_length=100)

    @field_validator("course_ids")
    @classmethod
    def distinct_courses(cls, value):
        return validate_ids(value)


class FamilyLifecycle(Strict):
    expected_version: int = Field(ge=1)
    status: Literal["active", "withdrawn", "graduated", "archived", "suspended"]
    academic_year: str = Field(min_length=1, max_length=40)
    grade: str | None = Field(default=None, max_length=80)
    school_id: str | None = Field(default=None, min_length=1, max_length=64)
    note: str = Field(default="", max_length=4000)


class ParentStudents(Strict):
    expected_version: int = Field(ge=1)
    student_ids: list[str] = Field(min_length=1, max_length=100)

    @field_validator("student_ids")
    @classmethod
    def distinct_students(cls, value):
        return validate_ids(value)
