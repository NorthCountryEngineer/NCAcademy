"""Private student avatars, single-send moderation and explicit human review.

Every image, student-specific state and review report uses the independent PII
key, including Core-backed Students. Only fixed rules release an admitted image;
provider prose and hidden reasoning never become moderation explanations.
"""
import base64
import hashlib
import json
import math
from urllib.parse import quote

from fastapi import Depends, HTTPException, Query, Request
from fastapi.responses import Response
from sqlalchemy import select

from .avatar_media import MAX_BYTES, MAX_EDGE, normalize_avatar
from .avatar_schemas import AvatarCloseScan, AvatarDecision, AvatarPolicy, AvatarPolicyApproval, AvatarPolicyInput, AvatarReveal, AvatarScan, AvatarUpload, AvatarVersion
from .avatar_scanner import SCANNER_PROMPT_VERSION
from .db import Record, canonical, now, uid
from .lesson_api import _connection_snapshot
from .permissions import bind_scope, membership_id, require_school, scope_id
from .standards import digest

BOUNDARY = "student-avatar-moderation-v1"
CATEGORIES = ("sexual", "violence", "self_harm", "hate", "other_unsafe")
PENDING = {"pending_scan", "manual_review", "quarantined", "flagged_review", "uncertain"}
PRIVATE_KINDS = {"avatar_pointer", "avatar_state", "avatar_upload", "avatar_scan_send", "avatar_scan_result", "avatar_review", "avatar_scan_closure"}
SCAN_LIMITS = {"student_daily": 5, "school_daily": 100, "max_output_tokens": 500, "max_total_tokens": 12000, "period": "utc_calendar_day"}


def pointer_id(tenant, school, student):
    return "student-avatar:" + digest([tenant, school, student])[:48]


def policy_id(tenant, school):
    return "avatar-policy:" + digest([tenant, school])[:48]


def state_id(identifier):
    return "avatar-state:" + digest(identifier)[:48]


def parse_moderation(result):
    """Admit only finite bounded category estimates and normalized token usage."""
    def pairs(items):
        value = {}
        for key, item in items:
            if key in value:
                raise ValueError()
            value[key] = item
        return value
    try:
        if not isinstance(result, dict):
            raise ValueError()
        choices = result["choices"]
        if not isinstance(choices, list) or len(choices) != 1:
            raise ValueError()
        choice = choices[0]
        if not isinstance(choice, dict):
            raise ValueError()
        message = choice["message"]
        if not isinstance(message, dict):
            raise ValueError()
        if choice.get("finish_reason", "stop") != "stop" or message.get("role", "assistant") != "assistant" or any(message.get(key) is not None for key in ("tool_calls", "function_call", "refusal")):
            raise ValueError()
        text = message["content"]
        if not isinstance(text, str) or len(text) > 2000:
            raise ValueError()
        parsed = json.loads(text, object_pairs_hook=pairs, parse_constant=lambda _: (_ for _ in ()).throw(ValueError()))
        if not isinstance(parsed, dict) or set(parsed) != {*CATEGORIES, "uncertain"} or type(parsed["uncertain"]) is not bool:
            raise ValueError()
        if any(type(parsed[key]) not in {int, float} or not math.isfinite(parsed[key]) or not 0 <= parsed[key] <= 1 for key in CATEGORIES):
            raise ValueError()
        usage = result["usage"]
        keys = ("prompt_tokens", "completion_tokens", "total_tokens")
        if not isinstance(usage, dict) or any(type(usage.get(key)) is not int or usage[key] < 0 for key in keys) or usage["total_tokens"] != usage["prompt_tokens"] + usage["completion_tokens"] or usage["completion_tokens"] > 500 or usage["total_tokens"] > 12000:
            raise ValueError()
        return parsed, {key: usage[key] for key in keys}, hashlib.sha256(text.encode()).hexdigest()
    except (KeyError, TypeError, ValueError, OverflowError, RecursionError):
        raise ValueError("Avatar scan response failed the fixed moderation contract") from None


def moderation_rules(scores, configuration):
    categories, explanations, reasons = [], [], []
    for category in CATEGORIES:
        threshold = configuration[category + "_threshold"] if category in {"sexual", "violence"} else configuration["other_threshold"]
        flagged = scores[category] >= threshold
        categories.append({"category": category, "confidence": scores[category], "threshold": threshold, "flagged": flagged})
        if flagged:
            reasons.append(category + "_detected")
            explanations.append(f"The {category.replace('_', ' ')} model risk score {scores[category]:.3f} meets or exceeds the administrator-approved threshold {threshold:.3f}. The image remains hidden pending human review.")
    if scores["uncertain"]:
        reasons.append("scanner_uncertain")
        explanations.append("The scanner reported uncertainty. A human administrator must review this image before it can be displayed.")
    status = "quarantined" if "sexual_detected" in reasons else "flagged_review" if reasons and reasons != ["scanner_uncertain"] else "uncertain" if reasons else "approved"
    if not reasons:
        explanations.append("Every model risk score is below its approved threshold and the scanner reported no uncertainty. Fixed server rules permit this exact image under this policy; the scores are estimates, not calibrated probabilities or a safety guarantee.")
    return status, reasons, {"categories": categories, "uncertain": scores["uncertain"], "rule_explanations": explanations, "score_description": "Model risk scores are estimates, not calibrated probabilities.", "boundary_version": BOUNDARY}


def register_student_avatar_api(app, database, auth, settings, actor, audit_actor):
    def private(row):
        try:
            return auth.family.decrypt(row.value["avatar_cipher"], tenant_id=row.tenant_id, owner_id=row.id, purpose="avatar:" + row.kind + ":" + row.owner_id)
        except (KeyError, TypeError):
            raise HTTPException(503, "Protected avatar evidence is unavailable") from None

    def save(row, value):
        row.set({"avatar_cipher": auth.family.encrypt(value, tenant_id=row.tenant_id, owner_id=row.id, purpose="avatar:" + row.kind + ":" + row.owner_id)})

    def add(db, identity, school, kind, value, student, identifier=None):
        row = Record(id=identifier or uid(), tenant_id=identity["tenant_id"], kind=kind, parent_id=school, owner_id=student)
        if kind in PRIVATE_KINDS:
            save(row, value)
        else:
            row.set(value)
        db.add(row); db.flush(); bind_scope(db, row, school)
        return row

    def event(db, identity, school, student, name, value):
        # Deliberately independent of the local-family-only audit auto-wrapper.
        protected = auth.family.encrypt(value, tenant_id=identity["tenant_id"], owner_id=student, purpose="avatar-audit:" + name)
        audit_actor(db, identity, "avatar." + name, {"school_id": school, "avatar_evidence_cipher": protected})

    def fresh(request, identity):
        current = auth.actor(request)
        if any(current.get(key) != identity.get(key) for key in ("id", "tenant_id", "login_id", "principal_kind")):
            raise HTTPException(403, "Your current session changed; reload before continuing")
        return current

    def student(db, identity, school, identifier=None):
        identifier = identifier or identity["id"]
        school_row = db.get(Record, school)
        user = db.get(Record, identifier)
        member = db.get(Record, membership_id(school, identifier))
        if not school_row or school_row.kind != "school" or school_row.tenant_id != identity["tenant_id"] or school_row.value.get("disabled") or not user or user.kind != "user" or user.tenant_id != identity["tenant_id"] or user.value.get("role") != "student" or user.value.get("disabled") or user.value.get("family_status", "active") != "active" or not member or member.tenant_id != identity["tenant_id"] or member.kind != "school_membership" or member.parent_id != school or member.owner_id != identifier or not member.value.get("active") or member.value.get("role") != "student":
            raise HTTPException(403, "A currently enrolled active Student in this school is required")
        if identifier == identity["id"]:
            require_school(db, identity, school, settings, {"student"}, human=True)
        return user

    def manager(db, identity, school):
        school_row = db.get(Record, school)
        if not school_row or school_row.kind != "school" or school_row.tenant_id != identity["tenant_id"] or school_row.value.get("disabled"):
            raise HTTPException(403, "An active school is required")
        require_school(db, identity, school, settings, {"admin", "school"}, human=True)

    def find(db, identity, school, identifier, kind):
        row = db.get(Record, identifier)
        if not row or row.tenant_id != identity["tenant_id"] or row.kind != kind or row.parent_id != school or scope_id(db, row) != school:
            raise HTTPException(404, "Avatar record not found in this school")
        return row

    def pointer(db, identity, school, identifier=None):
        row = db.get(Record, pointer_id(identity["tenant_id"], school, identifier or identity["id"]))
        if not row:
            return None, {"version": 0, "latest_id": None, "current_id": None}
        if row.kind != "avatar_pointer" or row.tenant_id != identity["tenant_id"] or row.parent_id != school or row.owner_id != (identifier or identity["id"]) or scope_id(db, row) != school:
            raise HTTPException(409, "Avatar profile integrity check failed")
        return row, private(row)

    def policy_snapshot(db, identity, school, live=False):
        row = find(db, identity, school, policy_id(identity["tenant_id"], school), "avatar_policy")
        data = row.value
        revision = find(db, identity, school, data.get("revision_id", ""), "avatar_policy_revision")
        value = revision.value
        if value.get("version") != data.get("version") or value.get("configuration_sha256") != digest(value.get("configuration")) or value.get("boundary_version") != BOUNDARY:
            raise HTTPException(409, "Avatar policy integrity check failed")
        configuration = AvatarPolicy.model_validate(value["configuration"]).model_dump()
        connection, key = None, None
        if configuration["connection_id"]:
            connection_row = db.get(Record, configuration["connection_id"])
            if not connection_row or connection_row.kind != "connection" or connection_row.tenant_id != identity["tenant_id"] or scope_id(db, connection_row) != school or not connection_row.value.get("enabled") or not connection_row.value.get("key_cipher") or configuration["model"] not in connection_row.value.get("models", []):
                raise HTTPException(409, "The avatar scanner model connection is unavailable")
            connection = _connection_snapshot(connection_row, school)
            key = connection_row.value["key_cipher"]
        approval = db.get(Record, data.get("approval_id") or "")
        snapshot = {"revision_id": revision.id, "configuration": configuration, "configuration_sha256": value["configuration_sha256"], "model_snapshot": connection, "boundary_version": BOUNDARY, "scanner_prompt_version": SCANNER_PROMPT_VERSION}
        approved = bool(approval and approval.tenant_id == identity["tenant_id"] and approval.kind == "avatar_policy_approval" and approval.parent_id == school and scope_id(db, approval) == school and approval.value.get("snapshot") == snapshot)
        snapshot["approval_id"] = approval.id if approved else None
        if live and (not approved or not configuration["enabled"] or not settings.allow_live_models):
            raise HTTPException(409, "Avatar scanning requires the deployment model gate and a current human-approved image-processing policy")
        return snapshot, key

    def policy_descriptor(db, identity, school, can_manage=False):
        row = db.get(Record, policy_id(identity["tenant_id"], school))
        base = {"school_id": school, "version": 0, "revision_id": None, "approved": False, "approval_id": None, "available": False, "live_models_enabled": settings.allow_live_models, "can_manage": can_manage, "configuration": AvatarPolicy().model_dump(), "unavailable_reason": "An administrator has not configured avatar scanning; uploads require manual review", "connection_name": None, "model": None, "scan_limits": SCAN_LIMITS}
        if row and row.tenant_id == identity["tenant_id"] and row.kind == "avatar_policy":
            revision = db.get(Record, row.value.get("revision_id", ""))
            if revision and revision.kind == "avatar_policy_revision" and revision.tenant_id == identity["tenant_id"]:
                base.update(version=row.value["version"], revision_id=revision.id, configuration=revision.value["configuration"])
            try:
                snapshot, _ = policy_snapshot(db, identity, school)
                approved = bool(snapshot["approval_id"])
                enabled = snapshot["configuration"]["enabled"]
                base.update(approved=approved, approval_id=snapshot["approval_id"], available=bool(approved and enabled and settings.allow_live_models), unavailable_reason=None if approved and enabled and settings.allow_live_models else "Human policy approval is required" if not approved else "Avatar scanning is disabled for this school" if not enabled else "Live models are disabled for this deployment", connection_name=(snapshot["model_snapshot"] or {}).get("name"), model=snapshot["configuration"]["model"])
            except HTTPException as error:
                base["unavailable_reason"] = error.detail
        return base

    def state(db, identity, school, identifier):
        row = find(db, identity, school, state_id(identifier), "avatar_state")
        data = private(row)
        upload = find(db, identity, school, identifier, "avatar_upload")
        if row.owner_id != upload.owner_id or data.get("upload_id") != identifier:
            raise HTTPException(409, "Avatar image integrity check failed")
        return row, data, upload

    def approved(db, identity, school, data):
        if data["status"] != "approved":
            return False
        if data.get("approval_source") == "human":
            proof = db.get(Record, data.get("review_id", ""))
            return bool(proof and proof.kind == "avatar_review" and proof.tenant_id == identity["tenant_id"] and proof.parent_id == school and scope_id(db, proof) == school and private(proof).get("decision") == "approve" and private(proof).get("upload_id") == data["upload_id"])
        try:
            snapshot, _ = policy_snapshot(db, identity, school)
            return bool(snapshot["approval_id"] and snapshot["configuration"]["enabled"] and snapshot == data.get("approved_policy_snapshot"))
        except HTTPException:
            return False

    def item_descriptor(db, identity, school, row, value):
        effective_status = value["status"]
        reasons = value["reason_codes"]
        if effective_status == "approved" and not approved(db, identity, school, value):
            effective_status, reasons = "manual_review", ["policy_changed"]
        return {"id": value["upload_id"], "student_id": row.owner_id, "version": value["version"], "status": effective_status, "created_at": value["created_at"], "pending_review": effective_status in PENDING, "reason_codes": reasons, "can_scan": not value.get("scan_send_id") and effective_status == "manual_review"}

    def descriptor(db, identity, school):
        _row, value = pointer(db, identity, school)
        current = latest = None
        if value["latest_id"]:
            latest_row, latest_value, _ = state(db, identity, school, value["latest_id"])
            latest = item_descriptor(db, identity, school, latest_row, latest_value)
        if value["current_id"]:
            _current_row, current_value, _ = state(db, identity, school, value["current_id"])
            if approved(db, identity, school, current_value):
                current = {"id": value["current_id"], "status": "approved", "image_url": f"/api/schools/{quote(school, safe='')}/avatars/{quote(value['current_id'], safe='')}/image"}
        policy = policy_descriptor(db, identity, school)
        return {"school_id": school, "version": value["version"], "current": current, "latest": latest, "upload_limits": {"max_bytes": MAX_BYTES, "max_dimension": MAX_EDGE, "daily_uploads": 5}, "scan_policy": {key: policy[key] for key in ("available", "unavailable_reason", "connection_name", "model", "approval_id")}, "scan_limits": SCAN_LIMITS, "consent_required": True}

    def detail(db, identity, school, identifier):
        row, value, upload = state(db, identity, school, identifier)
        user = student(db, identity, school, row.owner_id)
        metadata = private(upload)
        result = None
        if value.get("result_id"):
            result = private(find(db, identity, school, value["result_id"], "avatar_scan_result"))
        reports = [private(report) for report in db.scalars(select(Record).where(Record.tenant_id == identity["tenant_id"], Record.kind == "avatar_scan_result", Record.parent_id == school, Record.owner_id == row.owner_id).order_by(Record.created_at)) if private(report).get("upload_id") == identifier]
        if result is None and reports:
            result = reports[-1]
        closures = [private(closure) for closure in db.scalars(select(Record).where(Record.tenant_id == identity["tenant_id"], Record.kind == "avatar_scan_closure", Record.parent_id == school, Record.owner_id == row.owner_id).order_by(Record.created_at)) if private(closure).get("upload_id") == identifier]
        reviews = [private(review) for review in db.scalars(select(Record).where(Record.tenant_id == identity["tenant_id"], Record.kind == "avatar_review", Record.parent_id == school, Record.owner_id == row.owner_id).order_by(Record.created_at)) if private(review).get("upload_id") == identifier]
        name = user.value.get("name", "Student")
        if user.value.get("auth_provider") == "academy":
            from .family_accounts import _account, _profile
            profile = _profile(auth, _account(db, user))
            name = profile.get("preferred_name") or profile["name"]
        return {**item_descriptor(db, identity, school, row, value), "student_name": name, "image": {key: metadata["image"][key] for key in ("mime_type", "sha256", "width", "height", "byte_length")}, "consent_to_scan": metadata["consent_to_scan"], "admission": metadata.get("admission"), "moderation": result, "scan_reports": reports, "scan_closures": closures, "reviews": reviews, "can_review": value["status"] != "pending_scan", "can_reveal": True}

    def run_scan(request, identity, school, identifier, expected_version, expected_policy_approval_id):
        current = fresh(request, identity)
        with database.write() as db:
            student(db, current, school)
            row, data, upload = state(db, current, school, identifier)
            pointer_row, pointer_value = pointer(db, current, school)
            if row.owner_id != current["id"] or data["version"] != expected_version or pointer_value["latest_id"] != identifier or data.get("scan_send_id") or data["status"] != "manual_review":
                raise HTTPException(409, "This avatar cannot be scanned again; uncertain requests are never retried")
            snapshot, key = policy_snapshot(db, current, school, live=True)
            if snapshot["approval_id"] != expected_policy_approval_id:
                raise HTTPException(409, "The named avatar scanner policy changed; review its current notice and consent again")
            image = private(upload)["image"]
            today = now()[:10]
            day_sends = list(db.scalars(select(Record).where(Record.tenant_id == current["tenant_id"], Record.kind == "avatar_scan_send", Record.created_at.startswith(today))))
            if sum(send.owner_id == current["id"] for send in day_sends) >= SCAN_LIMITS["student_daily"] or sum(send.parent_id == school for send in day_sends) >= SCAN_LIMITS["school_daily"]:
                report = {"upload_id": identifier, "image_sha256": image["sha256"], "send_id": None, "categories": [], "uncertain": True, "rule_explanations": ["The fixed UTC-day scan allowance has been reached. Pending and uncertain sends count toward this limit. No provider call was made; the image remains hidden for human review."], "policy_snapshot": snapshot, "model_snapshot": snapshot["model_snapshot"], "boundary_version": BOUNDARY, "usage": {}, "received_at": now(), "failure_code": "scan_quota_exhausted", "status": "manual_review", "reason_codes": ["scan_quota_exhausted"], "automatic_retry": False, "no_provider_call": True, "scan_limits": SCAN_LIMITS}
                receipt = add(db, current, school, "avatar_scan_result", report, row.owner_id)
                data.update(reason_codes=["scan_quota_exhausted"], result_id=receipt.id, version=data["version"] + 1)
                save(row, data)
                event(db, current, school, row.owner_id, "scan_quota_exhausted", {"upload_id": identifier, "result_id": receipt.id, "scan_limits": SCAN_LIMITS, "no_provider_call": True})
                return descriptor(db, current, school)
            from .avatar_scanner import build_avatar_prompt
            prompt_hash = digest(build_avatar_prompt(image))
            send_id = uid()
            send = {"upload_id": identifier, "image_sha256": image["sha256"], "policy_snapshot": snapshot, "model_snapshot": snapshot["model_snapshot"], "prompt_sha256": prompt_hash, "created_at": now(), "consent_to_scan": True, "consent_policy_approval_id": expected_policy_approval_id, "max_output_tokens": 500, "automatic_retry": False}
            add(db, current, school, "avatar_scan_send", send, current["id"], send_id)
            data.update(status="pending_scan", version=data["version"] + 1, scan_send_id=send_id, reason_codes=["scan_pending"])
            save(row, data)
            event(db, current, school, current["id"], "scan_started", {"upload_id": identifier, "send_id": send_id, "image_sha256": image["sha256"], "policy_revision_id": snapshot["revision_id"]})
        failure, parsed, usage, response_hash = None, None, {}, None
        try:
            # The committed marker and all current scope/configuration checks
            # precede exactly one provider send. No transport replay is offered.
            current = fresh(request, identity)
            with database.factory() as db:
                student(db, current, school)
                latest_snapshot, _ = policy_snapshot(db, current, school, live=True)
                _state_row, latest_state, _ = state(db, current, school, identifier)
                _, latest_pointer = pointer(db, current, school)
                if latest_snapshot != snapshot or latest_state["scan_send_id"] != send_id or latest_state["status"] != "pending_scan" or latest_pointer["latest_id"] != identifier:
                    raise HTTPException(409, "Avatar admission changed before scanning")
            result = app.state.scan_avatar(snapshot["model_snapshot"], auth.unseal(key)["api_key"], snapshot["configuration"]["model"], image, max_output_tokens=500)
            parsed, usage, response_hash = parse_moderation(result)
        except Exception:
            failure = "scanner_unavailable_or_invalid"
        try:
            current = fresh(request, identity)
            with database.factory() as db:
                student(db, current, school)
                latest_snapshot, _ = policy_snapshot(db, current, school, live=True)
                if latest_snapshot != snapshot:
                    failure = "policy_changed"
        except Exception:
            failure = "authority_or_policy_changed"
        with database.write() as db:
            # Retain the bounded receipt even after revocation, but never use a
            # late result to restore access or replace a newer avatar.
            row, data, _ = state(db, identity, school, identifier)
            pointer_row, pointer_value = pointer(db, identity, school)
            closed = data.get("scan_send_id") != send_id or data["status"] != "pending_scan"
            if closed:
                failure = "operator_closed_scan"
            if pointer_value["latest_id"] != identifier:
                failure = "avatar_replaced"
            if not failure:
                try:
                    current = fresh(request, identity)
                    student(db, current, school)
                    latest_snapshot, _ = policy_snapshot(db, current, school, live=True)
                    if latest_snapshot != snapshot:
                        failure = "policy_changed"
                except Exception:
                    failure = "authority_or_policy_changed"
            negative_status = None
            if parsed is None:
                status, reasons, evidence = "uncertain", [failure or "scanner_uncertain"], {"categories": [], "uncertain": True, "rule_explanations": ["The scanner did not return a complete valid result. The image remains hidden for human review; this request will not be automatically retried."], "boundary_version": BOUNDARY}
            else:
                status, reasons, evidence = moderation_rules(parsed, snapshot["configuration"])
                negative_status = status if status in {"quarantined", "flagged_review"} else None
                if failure:
                    status, reasons = "uncertain", [*reasons, failure]
                    evidence["rule_explanations"].append("Current authority, policy or avatar state changed during scanning; the result cannot authorize display.")
            late_negative_hold = bool(closed and negative_status and data["status"] == "approved" and pointer_value["current_id"] == identifier)
            if late_negative_hold:
                status = negative_status
                reasons = [*reasons, "late_negative_scan"]
                evidence["rule_explanations"].append("A valid negative result arrived after the interrupted scan was closed and an earlier human approval. The previous approval is preserved, but this exact current avatar is hidden for a new human review of this report.")
            report = {**evidence, "upload_id": identifier, "image_sha256": image["sha256"], "send_id": send_id, "policy_snapshot": snapshot, "model_snapshot": snapshot["model_snapshot"], "response_sha256": response_hash, "usage": usage, "received_at": now(), "failure_code": failure, "status": status, "reason_codes": reasons, "automatic_retry": False}
            receipt = add(db, identity, school, "avatar_scan_result", report, row.owner_id)
            if not closed:
                data.update(status=status, reason_codes=reasons, result_id=receipt.id, version=data["version"] + 1, approval_source="scan" if status == "approved" else None, approved_policy_snapshot=snapshot if status == "approved" else None)
                save(row, data)
            elif late_negative_hold:
                # A human may have closed an interrupted call and approved its
                # image before a late negative result arrived. Preserve that
                # decision, then withhold this exact image for a new review.
                # This cannot replace another image or grant model authority.
                negative_reasons = [category + "_detected" for category in CATEGORIES if any(item["category"] == category and item["flagged"] for item in evidence["categories"])]
                data.update(status=negative_status, reason_codes=[*negative_reasons, "late_negative_scan"], result_id=receipt.id, version=data["version"] + 1, approval_source=None, approved_policy_snapshot=None)
                save(row, data)
                pointer_value.update(current_id=None, version=pointer_value["version"] + 1)
                save(pointer_row, pointer_value)
                event(db, identity, school, row.owner_id, "late_negative_withheld", {"upload_id": identifier, "result_id": receipt.id, "status": negative_status, "prior_review_id": data.get("review_id")})
            if status == "approved" and not closed:
                pointer_value.update(current_id=identifier, version=pointer_value["version"] + 1)
                save(pointer_row, pointer_value)
            event(db, identity, school, row.owner_id, "scan_completed", {"upload_id": identifier, "result_id": receipt.id, "status": status, "reason_codes": reasons, "usage": usage, "late_after_operator_closure": closed})
        current = fresh(request, identity)
        with database.factory() as db:
            student(db, current, school)
            return descriptor(db, current, school)

    @app.get("/api/schools/{school_id}/avatar-policy")
    def get_policy(school_id: str, identity=Depends(actor)):
        with database.factory() as db:
            manager(db, identity, school_id)
            return policy_descriptor(db, identity, school_id, True)

    @app.put("/api/schools/{school_id}/avatar-policy")
    def save_policy(school_id: str, body: AvatarPolicyInput, request: Request, identity=Depends(actor)):
        identity = fresh(request, identity)
        with database.write() as db:
            manager(db, identity, school_id)
            row = db.get(Record, policy_id(identity["tenant_id"], school_id))
            version = row.value["version"] if row else 0
            if body.expected_version != version:
                raise HTTPException(409, "Avatar policy changed; reload before saving")
            configuration = body.model_dump(exclude={"expected_version"})
            revision = add(db, identity, school_id, "avatar_policy_revision", {"version": version + 1, "configuration": configuration, "configuration_sha256": digest(configuration), "boundary_version": BOUNDARY, "created_at": now(), "actor_id": identity["id"]}, identity["id"])
            value = {"version": version + 1, "revision_id": revision.id, "approval_id": None}
            if row:
                row.set(value)
            else:
                add(db, identity, school_id, "avatar_policy", value, identity["id"], policy_id(identity["tenant_id"], school_id))
            audit_actor(db, identity, "avatar.policy_saved", {"school_id": school_id, "revision_id": revision.id, "version": version + 1})
            return policy_descriptor(db, identity, school_id, True)

    @app.post("/api/schools/{school_id}/avatar-policy/approve")
    def approve_policy(school_id: str, body: AvatarPolicyApproval, request: Request, identity=Depends(actor)):
        identity = fresh(request, identity)
        with database.write() as db:
            manager(db, identity, school_id)
            snapshot, _ = policy_snapshot(db, identity, school_id)
            if snapshot["revision_id"] != body.revision_id:
                raise HTTPException(409, "Avatar policy changed; review its current revision")
            snapshot.pop("approval_id")
            proof = add(db, identity, school_id, "avatar_policy_approval", {"snapshot": snapshot, "created_at": now(), "actor_id": identity["id"]}, identity["id"])
            row = find(db, identity, school_id, policy_id(identity["tenant_id"], school_id), "avatar_policy")
            row.set({**row.value, "approval_id": proof.id})
            audit_actor(db, identity, "avatar.policy_approved", {"school_id": school_id, "revision_id": body.revision_id, "approval_id": proof.id})
            return policy_descriptor(db, identity, school_id, True)

    @app.get("/api/schools/{school_id}/me/avatar")
    def own_avatar(school_id: str, identity=Depends(actor)):
        with database.factory() as db:
            student(db, identity, school_id)
            return descriptor(db, identity, school_id)

    @app.post("/api/schools/{school_id}/me/avatar", status_code=201)
    def upload_avatar(school_id: str, body: AvatarUpload, request: Request, identity=Depends(actor)):
        identity = fresh(request, identity)
        with database.factory() as db:
            student(db, identity, school_id)
        try:
            image = normalize_avatar(body.image.model_dump())
        except ValueError as error:
            raise HTTPException(422, str(error)) from None
        fingerprint = digest({"image_sha256": image["sha256"], "consent_to_scan": body.consent_to_scan, "expected_policy_approval_id": body.expected_policy_approval_id, "school_id": school_id})
        with database.write() as db:
            identity = fresh(request, identity)
            student(db, identity, school_id)
            pointer_row, value = pointer(db, identity, school_id)
            uploads = list(db.scalars(select(Record).where(Record.tenant_id == identity["tenant_id"], Record.kind == "avatar_upload", Record.parent_id == school_id, Record.owner_id == identity["id"])))
            for previous in uploads:
                prior = private(previous)
                if prior["idempotency_key"] == body.idempotency_key:
                    if prior["request_sha256"] != fingerprint:
                        raise HTTPException(409, "Avatar request key was already used with different content")
                    return descriptor(db, identity, school_id)
            if value["version"] != body.expected_version:
                raise HTTPException(409, "Avatar profile changed; reload before uploading")
            if value["latest_id"]:
                _, previous_state, _ = state(db, identity, school_id, value["latest_id"])
                if previous_state["status"] == "pending_scan":
                    raise HTTPException(409, "An avatar scan is already in progress; an administrator can close an interrupted scan without retrying it")
            if sum(row.created_at[:10] == now()[:10] for row in uploads) >= 5:
                raise HTTPException(429, "The daily limit of five avatar uploads has been reached")
            created = now()
            identifier = uid()
            scan_policy = policy_descriptor(db, identity, school_id)
            admission_code = "student_consent_not_provided" if not body.consent_to_scan else "scanner_unconfigured" if not scan_policy["version"] else "policy_not_approved_or_connection_changed" if not scan_policy["approved"] else "scanner_disabled" if not scan_policy["configuration"]["enabled"] else "deployment_model_gate_disabled" if not settings.allow_live_models else "consent_policy_changed" if not body.expected_policy_approval_id or body.expected_policy_approval_id != scan_policy["approval_id"] else "ready_to_scan"
            explanations = {"student_consent_not_provided": "The student did not consent to sending this image to the named scanner. The image remains hidden for human review.", "scanner_unconfigured": "No avatar scanner policy was configured at upload. The image remains hidden for human review.", "policy_not_approved_or_connection_changed": "The scanner policy or its model connection did not have current human approval at upload. No provider call was admitted.", "scanner_disabled": "School avatar scanning was disabled at upload. The image remains hidden for human review.", "deployment_model_gate_disabled": "The deployment model gate was disabled at upload. No provider call was admitted.", "consent_policy_changed": "The current approved scanner differs from the policy named in the student's consent. No image was sent; the student must review the current notice before consenting again.", "ready_to_scan": "The exact approved scanner policy matched the student's consent and current deployment gate. The image remains hidden until a complete admitted scan or human decision."}
            admission = {"reason_code": admission_code, "explanation": explanations[admission_code], "policy_at_upload": {key: scan_policy[key] for key in ("version", "revision_id", "approval_id", "approved", "available", "live_models_enabled", "configuration", "connection_name", "model")}, "expected_policy_approval_id": body.expected_policy_approval_id, "created_at": created, "boundary_version": BOUNDARY}
            add(db, identity, school_id, "avatar_upload", {"image": image, "consent_to_scan": body.consent_to_scan, "consent_policy_approval_id": body.expected_policy_approval_id, "admission": admission, "idempotency_key": body.idempotency_key, "request_sha256": fingerprint, "created_at": created}, identity["id"], identifier)
            data = {"upload_id": identifier, "version": 1, "status": "manual_review", "reason_codes": [admission_code], "created_at": created, "scan_send_id": None, "result_id": None, "review_id": None, "approval_source": None}
            add(db, identity, school_id, "avatar_state", data, identity["id"], state_id(identifier))
            value.update(version=value["version"] + 1, latest_id=identifier)
            if pointer_row:
                save(pointer_row, value)
            else:
                add(db, identity, school_id, "avatar_pointer", value, identity["id"], pointer_id(identity["tenant_id"], school_id, identity["id"]))
            event(db, identity, school_id, identity["id"], "uploaded", {"upload_id": identifier, "image_sha256": image["sha256"], "consent_to_scan": body.consent_to_scan, "status": "manual_review"})
            can_scan = admission_code == "ready_to_scan"
        if can_scan:
            return run_scan(request, identity, school_id, identifier, 1, body.expected_policy_approval_id)
        with database.factory() as db:
            return descriptor(db, identity, school_id)

    @app.post("/api/schools/{school_id}/me/avatar/{avatar_id}/scan")
    def scan_avatar(school_id: str, avatar_id: str, body: AvatarScan, request: Request, identity=Depends(actor)):
        return run_scan(request, identity, school_id, avatar_id, body.expected_version, body.expected_policy_approval_id)

    @app.delete("/api/schools/{school_id}/me/avatar")
    def remove_avatar(school_id: str, body: AvatarVersion, request: Request, identity=Depends(actor)):
        identity = fresh(request, identity)
        with database.write() as db:
            student(db, identity, school_id)
            row, value = pointer(db, identity, school_id)
            if not row or value["version"] != body.expected_version:
                raise HTTPException(409, "Avatar profile changed; reload before removing")
            value.update(version=value["version"] + 1, current_id=None, latest_id=None)
            save(row, value)
            event(db, identity, school_id, identity["id"], "removed", {"version": value["version"]})
            return descriptor(db, identity, school_id)

    @app.get("/api/schools/{school_id}/avatars/{avatar_id}/image")
    def avatar_image(school_id: str, avatar_id: str, request: Request, identity=Depends(actor)):
        identity = fresh(request, identity)
        with database.write() as db:
            row, data, upload = state(db, identity, school_id, avatar_id)
            student(db, identity, school_id, row.owner_id)
            if identity["id"] != row.owner_id:
                manager(db, identity, school_id)
            else:
                student(db, identity, school_id)
            _, value = pointer(db, identity, school_id, row.owner_id)
            if value["current_id"] != avatar_id or not approved(db, identity, school_id, data):
                raise HTTPException(404, "No approved current avatar is available")
            event(db, identity, school_id, row.owner_id, "image_viewed", {"upload_id": avatar_id})
            raw = base64.b64decode(private(upload)["image"]["data_base64"], validate=True)
        return Response(raw, media_type="image/jpeg", headers={"Cache-Control": "private, no-store", "Content-Disposition": "inline; filename=avatar.jpg"})

    @app.get("/api/schools/{school_id}/avatar-reviews")
    def review_queue(school_id: str, status: str = Query(default="pending", pattern="^(pending|all)$"), identity=Depends(actor)):
        with database.write() as db:
            manager(db, identity, school_id)
            items = []
            for row in db.scalars(select(Record).where(Record.tenant_id == identity["tenant_id"], Record.kind == "avatar_state", Record.parent_id == school_id).order_by(Record.created_at.desc())):
                try:
                    student(db, identity, school_id, row.owner_id)
                except HTTPException:
                    continue
                value = private(row)
                item = item_descriptor(db, identity, school_id, row, value)
                if status == "all" or item["pending_review"]:
                    detailed = detail(db, identity, school_id, value["upload_id"])
                    items.append({**item, "student_name": detailed["student_name"], "can_review": detailed["can_review"]})
            audit_actor(db, identity, "avatar.queue_viewed", {"school_id": school_id, "status": status, "item_count": len(items)})
            return {"items": items, "can_manage": True}

    @app.get("/api/schools/{school_id}/avatar-reviews/{avatar_id}")
    def review_detail(school_id: str, avatar_id: str, request: Request, identity=Depends(actor)):
        identity = fresh(request, identity)
        with database.write() as db:
            manager(db, identity, school_id)
            result = detail(db, identity, school_id, avatar_id)
            event(db, identity, school_id, result["student_id"], "evidence_viewed", {"upload_id": avatar_id})
            return result

    @app.post("/api/schools/{school_id}/avatar-reviews/{avatar_id}/reveal")
    def reveal(school_id: str, avatar_id: str, body: AvatarReveal, request: Request, identity=Depends(actor)):
        identity = fresh(request, identity)
        with database.write() as db:
            manager(db, identity, school_id)
            row, value, upload = state(db, identity, school_id, avatar_id)
            student(db, identity, school_id, row.owner_id)
            if value["version"] != body.expected_version:
                raise HTTPException(409, "Avatar review changed; reload before revealing")
            event(db, identity, school_id, row.owner_id, "sensitive_image_revealed", {"upload_id": avatar_id, "version": value["version"], "acknowledge_sensitive_content": True})
            raw = base64.b64decode(private(upload)["image"]["data_base64"], validate=True)
        return Response(raw, media_type="image/jpeg", headers={"Cache-Control": "private, no-store", "Content-Disposition": "inline; filename=private-review.jpg"})

    @app.post("/api/schools/{school_id}/avatar-reviews/{avatar_id}/decision")
    def decision(school_id: str, avatar_id: str, body: AvatarDecision, request: Request, identity=Depends(actor)):
        identity = fresh(request, identity)
        with database.write() as db:
            manager(db, identity, school_id)
            row, value, upload = state(db, identity, school_id, avatar_id)
            student(db, identity, school_id, row.owner_id)
            if value["version"] != body.expected_version or value["status"] == "pending_scan":
                raise HTTPException(409, "Avatar review changed or its scan is in progress; reload before deciding")
            proof_value = {"upload_id": avatar_id, "image_sha256": private(upload)["image"]["sha256"], "state_version": value["version"], "decision": body.decision, "reason": body.reason.strip(), "actor_id": identity["id"], "created_at": now(), "result_id": value.get("result_id")}
            proof = add(db, identity, school_id, "avatar_review", proof_value, row.owner_id)
            value.update(version=value["version"] + 1, status="approved" if body.decision == "approve" else "rejected", reason_codes=["human_approved" if body.decision == "approve" else "human_rejected"], review_id=proof.id, approval_source="human" if body.decision == "approve" else None)
            save(row, value)
            pointer_row, pointer_value = pointer(db, identity, school_id, row.owner_id)
            if body.decision == "approve":
                if pointer_value["latest_id"] != avatar_id:
                    raise HTTPException(409, "Only the student's latest avatar can replace the current image")
                pointer_value.update(current_id=avatar_id, version=pointer_value["version"] + 1)
                save(pointer_row, pointer_value)
            elif pointer_value["current_id"] == avatar_id:
                pointer_value.update(current_id=None, version=pointer_value["version"] + 1)
                save(pointer_row, pointer_value)
            event(db, identity, school_id, row.owner_id, "reviewed", {"upload_id": avatar_id, "review_id": proof.id, "decision": body.decision})
            return detail(db, identity, school_id, avatar_id)

    @app.post("/api/schools/{school_id}/avatar-reviews/{avatar_id}/close-scan")
    def close_scan(school_id: str, avatar_id: str, body: AvatarCloseScan, request: Request, identity=Depends(actor)):
        identity = fresh(request, identity)
        with database.write() as db:
            manager(db, identity, school_id)
            row, value, _ = state(db, identity, school_id, avatar_id)
            student(db, identity, school_id, row.owner_id)
            if value["version"] != body.expected_version or value["status"] != "pending_scan" or not value.get("scan_send_id"):
                raise HTTPException(409, "This avatar has no current pending scan to close")
            closure = add(db, identity, school_id, "avatar_scan_closure", {"upload_id": avatar_id, "send_id": value["scan_send_id"], "state_version": value["version"], "reason": body.reason.strip(), "actor_id": identity["id"], "created_at": now(), "automatic_retry": False}, row.owner_id)
            value.update(version=value["version"] + 1, status="uncertain", reason_codes=["operator_closed_scan"], closure_id=closure.id)
            save(row, value)
            event(db, identity, school_id, row.owner_id, "scan_closed", {"upload_id": avatar_id, "closure_id": closure.id, "send_id": value["scan_send_id"]})
            return detail(db, identity, school_id, avatar_id)
