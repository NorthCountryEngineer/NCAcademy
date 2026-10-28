"""Minimal human-staff projections for class enrollment, never identity authority.

The caller must first authorize the current human course manager. Private names
and current school placement are decrypted only here; the family administration
descriptor (contacts, birth date, notes, parents and history) is never returned.
"""
import re
from typing import Literal

from fastapi import HTTPException
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import select

from .db import Record
from .family_accounts import ACCOUNT, ENROLLMENT, _profile, _enrollment_value
from .permissions import membership_id, normalized_role


class EnrollmentCandidateSearch(BaseModel):
    model_config = ConfigDict(extra="forbid")

    grade: Literal["course", "all", "unplaced", "-1", "0", "1", "2", "3", "4", "5", "6", "7", "8", "9", "10", "11", "12"] = "course"
    search: str = Field(default="", max_length=100)
    offset: int = Field(default=0, ge=0, le=100000)
    limit: int = Field(default=50, ge=1, le=100)


def grade_level(value):
    """Recognize explicit school grades without inferring age or subject."""
    text = str(value or "").strip().casefold()
    if text == "-1":
        return -1
    text = re.sub(r"[\s_-]+", " ", text)
    if text in {"pk", "prek", "pre k", "prekindergarten", "pre kindergarten", "-1"}:
        return -1
    if text in {"k", "kg", "kindergarten", "grade k", "0"}:
        return 0
    match = re.fullmatch(r"(?:grade )?(1[0-2]|[1-9])(?:st|nd|rd|th)?(?: grade)?", text)
    return int(match[1]) if match else None


def course_grade_levels(course):
    curriculum = course.value.get("curriculum") or {}
    return sorted({grade for grade in curriculum.get("grades", []) if type(grade) is int and -1 <= grade <= 12})


def _current_school_member(db, user, school_id):
    member = db.get(Record, membership_id(school_id, user.id))
    return bool(member and member.tenant_id == user.tenant_id and member.kind == "school_membership"
                and member.parent_id == school_id and member.owner_id == user.id
                and member.value.get("active") and normalized_role(member.value.get("role")) == "student")


def student_projection(db, auth, user, school_id):
    """Current school-only name/placement. Call only after staff authorization."""
    if user.value.get("auth_provider") != "academy":
        name = user.value.get("name") or "Unnamed student"
        return {"id": user.id, "name": name, "display_name": name, "preferred_name": "", "username": "",
                "grade": "", "grade_level": None, "academic_year": "", "principal_kind": "human", "role": "student"}
    account = db.get(Record, "local-account:" + user.id)
    if not account or account.tenant_id != user.tenant_id or account.kind != ACCOUNT or account.owner_id != user.id:
        raise HTTPException(409, "Local student identity configuration is unavailable")
    profile = _profile(auth, account)
    current = []
    for row in db.scalars(select(Record).where(Record.tenant_id == user.tenant_id, Record.kind == ENROLLMENT,
                                             Record.owner_id == user.id, Record.parent_id == school_id)):
        value = _enrollment_value(auth, row)
        if value.get("active"):
            current.append(value)
    if len(current) > 1:
        raise HTTPException(409, "Current student grade placement is ambiguous")
    placement = current[0] if current and _current_school_member(db, user, school_id) else {}
    name = profile["name"]
    preferred = profile.get("preferred_name", "").strip()
    display_name = f"{preferred} ({name})" if preferred and preferred.casefold() != name.casefold() else name
    grade = placement.get("grade", "")
    return {"id": user.id, "name": name, "display_name": display_name, "preferred_name": preferred,
            "username": profile.get("username", ""), "grade": grade, "grade_level": grade_level(grade),
            "academic_year": placement.get("academic_year", ""), "principal_kind": "human", "role": "student"}


def enrollment_candidates(db, auth, identity, course, school_id, body):
    """Caller supplies an already authorized course and school, never client scope."""
    grades = course_grade_levels(course)
    selected = grades if body.grade == "course" and grades else None
    if body.grade == "unplaced":
        selected = []
    elif body.grade not in {"course", "all"}:
        selected = [int(body.grade)]
    search = body.search.strip().casefold()
    enrolled = {row.owner_id for row in db.scalars(select(Record).where(Record.tenant_id == identity["tenant_id"],
                Record.kind == "enrollment", Record.parent_id == course.id))}
    candidates = []
    for member in db.scalars(select(Record).where(Record.tenant_id == identity["tenant_id"], Record.kind == "school_membership",
                                                Record.parent_id == school_id)):
        if member.id != membership_id(school_id, member.owner_id) or not member.value.get("active") or normalized_role(member.value.get("role")) != "student":
            continue
        user = db.get(Record, member.owner_id)
        if not user or user.tenant_id != identity["tenant_id"] or user.kind != "user" or user.id in enrolled:
            continue
        if user.value.get("principal_kind", "human") != "human" or normalized_role(user.value.get("role")) != "student" or user.value.get("disabled") or user.value.get("family_status", "active") != "active":
            continue
        if not _current_school_member(db, user, school_id):
            continue
        candidate = student_projection(db, auth, user, school_id)
        if selected == [] and candidate["grade"].strip():
            continue
        if selected and candidate["grade_level"] not in selected:
            continue
        if search and not any(search in candidate[key].casefold() for key in ("name", "preferred_name", "username")):
            continue
        candidates.append(candidate)
    candidates.sort(key=lambda item: (item["display_name"].casefold(), item["username"].casefold(), item["id"]))
    return {"course_id": course.id, "school_id": school_id, "course_grade_levels": grades,
            "selected_grade_levels": selected, "grade_filter": body.grade, "total": len(candidates),
            "offset": body.offset, "limit": body.limit, "candidates": candidates[body.offset:body.offset + body.limit]}
