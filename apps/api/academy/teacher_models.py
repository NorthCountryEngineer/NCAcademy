"""Separately approved model execution policies for stable AI teacher identities.

Drafts never activate a policy. Model changes never rewrite teacher personality,
class assignments, approved profile revisions or content-addressed class corpora.
Automatic model routing remains preview-only; every send uses the primary model.
"""
import hashlib
import re
from typing import Literal

from fastapi import Depends, HTTPException
from pydantic import BaseModel, ConfigDict, Field, model_validator

from .db import Record, canonical, now, uid
from .permissions import bind_scope, require_school, scope_id

EXECUTION_BOUNDARY = "teacher-models-v1"
TASKS = ("simple", "reasoning", "long_context", "image")
Task = Literal["simple", "reasoning", "long_context", "image"]


class Strict(BaseModel):
    model_config = ConfigDict(extra="forbid")


class ModelRef(Strict):
    connection_id: str = Field(min_length=1, max_length=64)
    model: str = Field(min_length=1, max_length=150, pattern=r"^[a-zA-Z0-9][a-zA-Z0-9._:/-]{0,149}$")


class LessonPolicy(Strict):
    max_turns: int = Field(default=8, ge=1, le=30, strict=True)
    max_input_chars: int = Field(default=3000, ge=100, le=10000, strict=True)
    max_output_tokens: int = Field(default=80, ge=30, le=200, strict=True)
    max_total_tokens: int = Field(default=20000, ge=500, le=1000000, strict=True)
    allow_lesson_curation: bool = Field(default=False, strict=True)
    allow_assignment_images: bool = Field(default=False, strict=True)
    curation_max_output_tokens: int = Field(default=700, ge=200, le=1200, strict=True)

    @model_validator(mode="after")
    def images(self):
        if self.allow_assignment_images and not self.allow_lesson_curation:
            raise ValueError("Assignment pictures require lesson curation")
        return self


class Candidate(ModelRef):
    id: str = Field(min_length=1, max_length=64, pattern=r"^[a-zA-Z0-9_-]{1,64}$")
    label: str = Field(min_length=1, max_length=120)
    tasks: list[Task] = Field(min_length=1, max_length=4)

    @model_validator(mode="after")
    def unique_tasks(self):
        if len(set(self.tasks)) != len(self.tasks) or not self.label.strip():
            raise ValueError("Use a nonblank label and unique candidate tasks")
        return self


class Rule(Strict):
    task: Task
    candidate_id: str = Field(min_length=1, max_length=64)


class Routing(Strict):
    mode: Literal["fixed", "preview"] = "fixed"
    candidates: list[Candidate] = Field(default_factory=list, max_length=8)
    rules: list[Rule] = Field(default_factory=list, max_length=4)
    max_question_chars: int = Field(default=10000, ge=100, le=10000, strict=True)

    @model_validator(mode="after")
    def valid_rules(self):
        candidates = {item.id: item for item in self.candidates}
        if len(candidates) != len(self.candidates) or len({item.task for item in self.rules}) != len(self.rules):
            raise ValueError("Candidate IDs and routing tasks must be unique")
        for rule in self.rules:
            if rule.candidate_id not in candidates or rule.task not in candidates[rule.candidate_id].tasks:
                raise ValueError("Every rule must name a candidate supporting its task")
        return self


class TeacherModelPolicy(Strict):
    enabled: bool = Field(default=True, strict=True)
    primary: ModelRef
    lesson_enabled: bool = Field(default=True, strict=True)
    homework_enabled: bool = Field(default=True, strict=True)
    homework_max_output_tokens: int = Field(default=1500, ge=200, le=3000, strict=True)
    lesson_policy: LessonPolicy = Field(default_factory=LessonPolicy)
    assignment_images_reviewed: bool = Field(default=False, strict=True)
    routing: Routing = Field(default_factory=Routing)

    @model_validator(mode="after")
    def reviewed_images(self):
        if self.lesson_policy.allow_assignment_images and not self.assignment_images_reviewed:
            raise ValueError("Explicitly review this primary model's assignment image support")
        return self


class PolicyInput(Strict):
    expected_version: int = Field(ge=0, strict=True)
    policy: TeacherModelPolicy


class ApprovalInput(Strict):
    expected_version: int = Field(ge=1, strict=True)
    revision_id: str = Field(min_length=1, max_length=64)


class PreviewInput(Strict):
    text: str = Field(default="", max_length=10000)
    has_images: bool = Field(default=False, strict=True)


def digest(value):
    return hashlib.sha256(canonical(value).encode()).hexdigest()


def pointer_id(tenant_id, profile_id):
    return digest([EXECUTION_BOUNDARY, tenant_id, profile_id])


def _profile(db, identity, school_id, profile_id, *, manage=False, settings=None):
    if manage:
        require_school(db, identity, school_id, settings, {"admin", "school"}, human=True)
    row = db.get(Record, profile_id)
    if not row or row.kind != "teacher_profile" or row.tenant_id != identity["tenant_id"] or row.parent_id != school_id or scope_id(db, row) != school_id:
        raise HTTPException(404, "Teacher not found in this school")
    if row.value.get("kind") != "ai":
        raise HTTPException(422, "Model execution policies require an AI teacher")
    if row.value.get("status") == "archived":
        raise HTTPException(409, "Restore this teacher before managing or using its models")
    return row


def _pointer(db, identity, profile, school_id):
    row = db.get(Record, pointer_id(identity["tenant_id"], profile.id))
    if row and (row.kind != "teacher_model_config" or row.tenant_id != identity["tenant_id"] or row.parent_id != profile.id or scope_id(db, row) != school_id):
        raise HTTPException(409, "Teacher model policy scope check failed")
    return row


def _revision(db, identity, profile, school_id, revision_id):
    row = db.get(Record, revision_id)
    if not row or row.kind != "teacher_model_revision" or row.tenant_id != identity["tenant_id"] or row.parent_id != profile.id or scope_id(db, row) != school_id:
        raise HTTPException(409, "Teacher model revision scope check failed")
    value = row.value
    if value.get("sha256") != digest(value.get("policy")) or value.get("execution_boundary") != EXECUTION_BOUNDARY:
        raise HTTPException(409, "Teacher model policy integrity check failed")
    # Hash exact historical bytes before parsing newly introduced defaults.
    return row, TeacherModelPolicy.model_validate(value["policy"]).model_dump()


def _connections(db, identity, school_id, policy):
    from .lesson_api import _connection_snapshot
    refs = [policy["primary"], *policy["routing"]["candidates"]] if policy["enabled"] else []
    result = {}
    for ref in refs:
        row = db.get(Record, ref["connection_id"])
        if not row or row.kind != "connection" or row.tenant_id != identity["tenant_id"] or scope_id(db, row) != school_id:
            raise HTTPException(403, "Teacher models must use connections assigned to this school")
        value = row.value
        if not value.get("enabled") or not value.get("key_cipher") or ref["model"] not in value.get("models", []):
            raise HTTPException(403, "A teacher model connection is disabled, unconfigured or no longer allows this model")
        result[row.id] = _connection_snapshot(row, school_id)
    return {key: result[key] for key in sorted(result)}


def _active(db, identity, profile, school_id):
    pointer = _pointer(db, identity, profile, school_id)
    if not pointer or not pointer.value.get("active_revision_id"):
        return None
    revision, policy = _revision(db, identity, profile, school_id, pointer.value["active_revision_id"])
    approval = db.get(Record, pointer.value.get("active_approval_id", ""))
    if not approval or approval.kind != "teacher_model_approval" or approval.tenant_id != identity["tenant_id"] or approval.parent_id != revision.id or scope_id(db, approval) != school_id:
        raise HTTPException(409, "The active teacher model policy needs human approval")
    value = approval.value
    if value.get("policy_sha256") != revision.value["sha256"] or value.get("execution_boundary") != EXECUTION_BOUNDARY or digest(value.get("connections")) != value.get("connections_sha256"):
        raise HTTPException(409, "Teacher model approval integrity check failed")
    return revision, approval, policy


def model_policy_context(db, identity, profile, school_id, purpose):
    """Resolve a separately approved binding, or None for explicit legacy mode.

    No credential is returned. Callers separately retain all teacher, class,
    source, consent, budget and authority checks before each provider send.
    """
    if purpose not in {"lesson", "homework"}:
        raise ValueError("Unknown teacher model purpose")
    profile = _profile(db, identity, school_id, profile.id if isinstance(profile, Record) else profile)
    from .teacher_api import _approved
    _approved(db, profile, school_id)
    active = _active(db, identity, profile, school_id)
    if not active:
        return None
    revision, approval, policy = active
    if not policy["enabled"]:
        return None
    if not policy[purpose + "_enabled"]:
        raise HTTPException(403, "The teacher's approved model policy disables this activity")
    connections = _connections(db, identity, school_id, policy)
    if canonical(connections) != canonical(approval.value["connections"]):
        raise HTTPException(409, "A teacher model connection changed; review and approve a new model policy")
    connection = connections[policy["primary"]["connection_id"]]
    return {"execution_boundary": EXECUTION_BOUNDARY, "teacher_profile_id": profile.id, "purpose": purpose, "revision_id": revision.id, "approval_id": approval.id, "policy_sha256": revision.value["sha256"], "connections_sha256": approval.value["connections_sha256"], "policy": policy, "connection_snapshot": connection, "model": policy["primary"]["model"], "selection_reason": "approved_primary", "automatic_routing_enabled": False}


def require_model_policy_snapshot(db, identity, profile, school_id, purpose, snapshot, *, current=True):
    """Current admission for sends; immutable retained admission for history.

    A policy-only model swap keeps historical responses readable. Revoking or
    changing a connection used by that historical policy still withholds them.
    """
    if current:
        resolved = model_policy_context(db, identity, profile, school_id, purpose)
    elif snapshot is None:
        return None
    else:
        profile = _profile(db, identity, school_id, profile.id if isinstance(profile, Record) else profile)
        from .teacher_api import _approved
        _approved(db, profile, school_id)
        revision, policy = _revision(db, identity, profile, school_id, snapshot.get("revision_id", ""))
        approval = db.get(Record, snapshot.get("approval_id", ""))
        if not approval or approval.kind != "teacher_model_approval" or approval.tenant_id != identity["tenant_id"] or approval.parent_id != revision.id or scope_id(db, approval) != school_id:
            raise HTTPException(409, "Historical teacher model approval is unavailable")
        value = approval.value
        if value.get("policy_sha256") != revision.value["sha256"] or value.get("execution_boundary") != EXECUTION_BOUNDARY or digest(value.get("connections")) != value.get("connections_sha256") or not policy["enabled"] or not policy.get(purpose + "_enabled"):
            raise HTTPException(409, "Historical teacher model policy integrity check failed")
        # Only the primary was executed. A preview-only candidate cannot
        # retroactively revoke a response sent to a different connection.
        selected_id = policy["primary"]["connection_id"]
        connections = _connections(db, identity, school_id, {**policy, "routing": {**policy["routing"], "candidates": []}})
        if canonical(connections[selected_id]) != canonical(value["connections"].get(selected_id)):
            raise HTTPException(409, "A historical teacher model connection changed or was revoked")
        resolved = {"execution_boundary": EXECUTION_BOUNDARY, "teacher_profile_id": profile.id, "purpose": purpose, "revision_id": revision.id, "approval_id": approval.id, "policy_sha256": revision.value["sha256"], "connections_sha256": value["connections_sha256"], "policy": policy, "connection_snapshot": connections[selected_id], "model": policy["primary"]["model"], "selection_reason": "approved_primary", "automatic_routing_enabled": False}
    if canonical(resolved) != canonical(snapshot):
        raise HTTPException(409, "The approved teacher execution policy or model connection changed")
    return resolved


def preview_model_policy(policy, text, has_images=False):
    """Deterministic advisory routing. It never executes a candidate or a model."""
    if len(text) > policy["routing"]["max_question_chars"]:
        raise HTTPException(422, "Question exceeds this routing preview's character limit")
    task = "image" if has_images else "long_context" if len(text) > 2000 else "reasoning" if re.search(r"\b(why|prove|compare|analy[sz]e|reason|derive|evaluate)\b", text, re.I) else "simple"
    candidate_id = next((rule["candidate_id"] for rule in policy["routing"]["rules"] if rule["task"] == task), None)
    candidate = next((item for item in policy["routing"]["candidates"] if item["id"] == candidate_id), None)
    suggested = {key: candidate[key] for key in ("connection_id", "model", "label")} | {"candidate_id": candidate["id"]} if candidate and policy["routing"]["mode"] == "preview" else None
    return {"task": task, "reason_code": "image_input" if has_images else "long_question" if task == "long_context" else "reasoning_keyword" if task == "reasoning" else "default_simple", "executed": {**policy["primary"], "label": policy["primary"]["model"], "selection_reason": "approved_primary"}, "suggested": suggested, "automatic_routing_enabled": False, "no_provider_calls": True, "pricing_status": "unknown"}


def descriptor(db, identity, profile, school_id):
    pointer = _pointer(db, identity, profile, school_id)
    result = {"school_id": school_id, "teacher_profile_id": profile.id, "version": pointer.value["version"] if pointer else 0, "draft": None, "active": None, "draft_pending": False, "can_manage": True, "active_available": False, "unavailable_reason": None, "automatic_routing_enabled": False, "homework_images": "school_models_only", "pricing_status": "unknown"}
    if not pointer:
        return result
    if pointer.value.get("draft_revision_id"):
        revision, policy = _revision(db, identity, profile, school_id, pointer.value["draft_revision_id"])
        result["draft"] = {"revision_id": revision.id, "sha256": revision.value["sha256"], "policy": policy}
    active = _active(db, identity, profile, school_id)
    if active:
        revision, approval, policy = active
        connections = approval.value["connections"]
        result["active"] = {"revision_id": revision.id, "approval_id": approval.id, "sha256": revision.value["sha256"], "policy": policy, "approved_at": approval.value["approved_at"], "connections": [{key: value.get(key) for key in ("id", "name", "models", "enabled", "version")} for value in connections.values()]}
        try:
            current = _connections(db, identity, school_id, policy)
            if canonical(current) != canonical(connections):
                raise HTTPException(409, "A teacher model connection changed; approve a new policy")
            result["active_available"] = True
        except HTTPException as error:
            result["unavailable_reason"] = error.detail
    result["draft_pending"] = bool(result["draft"] and (not result["active"] or result["draft"]["revision_id"] != result["active"]["revision_id"]))
    return result


def register_teacher_model_api(app, database, settings, actor, audit_actor):
    def manage(db, identity, school_id, profile_id):
        return _profile(db, identity, school_id, profile_id, manage=True, settings=settings)

    def add(db, identity, school_id, kind, parent, value):
        row = Record(id=uid(), tenant_id=identity["tenant_id"], kind=kind, parent_id=parent, owner_id=identity["id"], data=canonical(value))
        db.add(row)
        db.flush()
        bind_scope(db, row, school_id)
        return row

    @app.get("/api/schools/{school_id}/teachers/{profile_id}/models")
    def get_policy(school_id: str, profile_id: str, identity=Depends(actor)):
        with database.factory() as db:
            profile = manage(db, identity, school_id, profile_id)
            return descriptor(db, identity, profile, school_id)

    @app.put("/api/schools/{school_id}/teachers/{profile_id}/models")
    def save_policy(school_id: str, profile_id: str, body: PolicyInput, identity=Depends(actor)):
        with database.write() as db:
            profile = manage(db, identity, school_id, profile_id)
            pointer = _pointer(db, identity, profile, school_id)
            old = pointer.value if pointer else {"version": 0, "active_revision_id": None, "active_approval_id": None}
            if old["version"] != body.expected_version:
                raise HTTPException(409, "Teacher model settings changed; reload before saving")
            policy = body.policy.model_dump()
            _connections(db, identity, school_id, policy)
            value = {"execution_boundary": EXECUTION_BOUNDARY, "version": old["version"] + 1, "policy": policy, "sha256": digest(policy), "created_at": now(), "created_by": identity["id"]}
            revision = add(db, identity, school_id, "teacher_model_revision", profile.id, value)
            pointer_value = {**old, "version": value["version"], "draft_revision_id": revision.id}
            if pointer:
                pointer.set(pointer_value)
            else:
                pointer = Record(id=pointer_id(identity["tenant_id"], profile.id), tenant_id=identity["tenant_id"], kind="teacher_model_config", parent_id=profile.id, owner_id=identity["id"], data=canonical(pointer_value))
                db.add(pointer)
                db.flush()
                bind_scope(db, pointer, school_id)
            db.flush()
            audit_actor(db, identity, "teacher_model.draft_saved", {"school_id": school_id, "teacher_profile_id": profile.id, "revision_id": revision.id, "policy_sha256": value["sha256"], "version": value["version"], "personality_and_corpus_unchanged": True})
            return descriptor(db, identity, profile, school_id)

    @app.post("/api/schools/{school_id}/teachers/{profile_id}/models/approve")
    def approve_policy(school_id: str, profile_id: str, body: ApprovalInput, identity=Depends(actor)):
        with database.write() as db:
            profile = manage(db, identity, school_id, profile_id)
            pointer = _pointer(db, identity, profile, school_id)
            if not pointer or pointer.value["version"] != body.expected_version or pointer.value.get("draft_revision_id") != body.revision_id:
                raise HTTPException(409, "Approve only the current saved model draft")
            if pointer.value.get("active_revision_id") == body.revision_id:
                return descriptor(db, identity, profile, school_id)
            revision, policy = _revision(db, identity, profile, school_id, body.revision_id)
            connections = _connections(db, identity, school_id, policy)
            value = {"execution_boundary": EXECUTION_BOUNDARY, "policy_sha256": revision.value["sha256"], "connections": connections, "connections_sha256": digest(connections), "approved_by": identity["id"], "approved_at": now()}
            approval = add(db, identity, school_id, "teacher_model_approval", revision.id, value)
            pointer.set({**pointer.value, "active_revision_id": revision.id, "active_approval_id": approval.id})
            db.flush()
            audit_actor(db, identity, "teacher_model.policy_approved", {"school_id": school_id, "teacher_profile_id": profile.id, "revision_id": revision.id, "approval_id": approval.id, "policy_sha256": revision.value["sha256"], "connections_sha256": value["connections_sha256"], "personality_and_corpus_unchanged": True})
            return descriptor(db, identity, profile, school_id)

    @app.post("/api/schools/{school_id}/teachers/{profile_id}/models/preview")
    def preview(school_id: str, profile_id: str, body: PreviewInput, identity=Depends(actor)):
        with database.factory() as db:
            profile = manage(db, identity, school_id, profile_id)
            pointer = _pointer(db, identity, profile, school_id)
            if not pointer or not pointer.value.get("draft_revision_id"):
                raise HTTPException(409, "Save a model policy before previewing its routing")
            _, policy = _revision(db, identity, profile, school_id, pointer.value["draft_revision_id"])
            _connections(db, identity, school_id, policy)
            return preview_model_policy(policy, body.text, body.has_images)
