"""Synthetic pixels and stubbed moderation only: never contact a real provider."""
import base64
from io import BytesIO
import json

import pytest
from fastapi.testclient import TestClient
from PIL import Image, PngImagePlugin
from sqlalchemy import select, text

from academy.avatar_media import normalize_avatar
from academy.db import Audit, Record, student_avatar_schema_id
from academy.permissions import default_school_id, membership_id
from academy.student_avatars import CATEGORIES, PRIVATE_KINDS, parse_moderation, policy_id, state_id
from conftest import login


def school(app):
    return default_school_id(app.state.settings.tenant_id)


def base(app):
    return "/api/schools/" + school(app)


def picture(color="green", size=(32, 32), *, metadata=False):
    output = BytesIO()
    info = PngImagePlugin.PngInfo()
    if metadata:
        info.add_text("private-secret", "Synthetic GPS/private metadata must disappear")
    Image.new("RGB", size, color).save(output, "PNG", pnginfo=info)
    return {"mime_type": "image/png", "data_base64": base64.b64encode(output.getvalue()).decode()}


def response(**scores):
    categories = {key: 0.01 for key in CATEGORIES}
    categories.update({"uncertain": False, **scores})
    return {"choices": [{"message": {"role": "assistant", "content": json.dumps(categories)}, "finish_reason": "stop"}], "usage": {"prompt_tokens": 100, "completion_tokens": 40, "total_tokens": 140}}


def configured(client, app, *, enable_gate=True):
    headers = login(client, "demo-admin")
    created = client.post("/api/admin/connections", json={"name": "Synthetic image scanner", "endpoint": "https://scanner.example/v1", "models": ["vision-safety"], "enabled": True, "api_key": "synthetic-avatar-key"}, headers=headers)
    assert created.status_code == 201, created.text
    connection = created.json()
    configuration = {"enabled": True, "connection_id": connection["id"], "model": "vision-safety", "image_capable": True, "allow_image_processing": True, "sexual_threshold": 0.2, "violence_threshold": 0.35, "other_threshold": 0.35, "expected_version": 0}
    saved = client.put(base(app) + "/avatar-policy", json=configuration, headers=headers)
    assert saved.status_code == 200, saved.text
    approved = client.post(base(app) + "/avatar-policy/approve", json={"revision_id": saved.json()["revision_id"]}, headers=headers)
    assert approved.status_code == 200 and approved.json()["approved"], approved.text
    app.state.scan_avatar = lambda *_args, **_kwargs: response()
    app.state.settings.allow_live_models = enable_gate
    return approved.json(), connection


def upload(client, app, headers=None, *, key="avatar-test-0001", consent=False, image=None, approval=None):
    headers = headers or login(client)
    own = client.get(base(app) + "/me/avatar").json()
    body = {"idempotency_key": key, "expected_version": own["version"], "consent_to_scan": consent, "image": image or picture(), "expected_policy_approval_id": approval if approval is not None else own["scan_policy"]["approval_id"] if consent else None}
    return client.post(base(app) + "/me/avatar", json=body, headers=headers)


def decrypt(app, row):
    return app.state.auth.family.decrypt(row.value["avatar_cipher"], row.tenant_id, row.id, "avatar:" + row.kind + ":" + row.owner_id)


def review(client, app, identifier, decision="approve"):
    headers = login(client, "demo-admin")
    detail = client.get(base(app) + "/avatar-reviews/" + identifier).json()
    reviewed = client.post(base(app) + "/avatar-reviews/" + identifier + "/decision", json={"expected_version": detail["version"], "decision": decision, "reason": "Synthetic human review of the exact normalized avatar."}, headers=headers)
    assert reviewed.status_code == 200, reviewed.text
    return reviewed.json()


def test_unconfigured_upload_is_hidden_until_human_review_and_pixels_require_current_owner(client, app):
    headers = login(client)
    saved = upload(client, app, headers)
    assert saved.status_code == 201, saved.text
    result = saved.json(); identifier = result["latest"]["id"]
    assert result["current"] is None and result["latest"]["status"] == "manual_review"
    path = base(app) + "/avatars/" + identifier + "/image"
    assert client.get(path).status_code == 404
    assert "data_base64" not in saved.text
    reviewed = review(client, app, identifier)
    assert reviewed["status"] == "approved" and reviewed["reviews"][0]["decision"] == "approve"
    login(client)
    own = client.get(base(app) + "/me/avatar").json()
    assert "/school%3A" in own["current"]["image_url"]
    image = client.get(own["current"]["image_url"])
    assert image.status_code == 200 and image.headers["content-type"] == "image/jpeg"
    assert image.headers["cache-control"] == "private, no-store"
    assert image.headers["x-content-type-options"] == "nosniff"
    login(client, "demo-student2")
    assert client.get(path).status_code == 403
    login(client, "demo-instructor")
    assert client.get(path).status_code == 403


@pytest.mark.parametrize("scores,status,code", [
    ({}, "approved", None), ({"sexual": 0.2}, "quarantined", "sexual_detected"),
    ({"violence": 0.35}, "flagged_review", "violence_detected"),
    ({"self_harm": 0.6}, "flagged_review", "self_harm_detected"),
    ({"hate": 0.5}, "flagged_review", "hate_detected"),
    ({"other_unsafe": 0.5}, "flagged_review", "other_unsafe_detected"),
    ({"uncertain": True}, "uncertain", "scanner_uncertain"),
    ({"sexual": 0.9, "violence": 0.9}, "quarantined", "sexual_detected"),
])
def test_fixed_thresholds_determine_hidden_state_and_admin_sees_bounded_rationale(client, app, scores, status, code):
    configured(client, app)
    calls = []
    app.state.scan_avatar = lambda *args, **kwargs: (calls.append((args, kwargs)) or response(**scores))
    saved = upload(client, app, consent=True)
    assert saved.status_code == 201, saved.text
    result = saved.json(); identifier = result["latest"]["id"]
    assert result["latest"]["status"] == status
    assert bool(result["current"]) == (status == "approved")
    assert len(calls) == 1
    with app.state.database.factory() as db:
        sends = list(db.scalars(select(Record).where(Record.kind == "avatar_scan_send")))
        assert len(sends) == 1
        send = decrypt(app, sends[0])
        assert send["policy_snapshot"]["approval_id"] == send["consent_policy_approval_id"]
        assert len(send["prompt_sha256"]) == 64
        assert "data_base64" not in json.dumps(send)
    login(client, "demo-admin")
    report = client.get(base(app) + "/avatar-reviews/" + identifier).json()["moderation"]
    assert len(report["categories"]) == 5 and report["rule_explanations"]
    assert "not calibrated probabilities" in report["score_description"]
    assert "synthetic-avatar-key" not in json.dumps(report)
    if code:
        assert code in report["reason_codes"]


def test_previous_approved_avatar_remains_while_new_upload_is_quarantined(client, app):
    configured(client, app)
    first = upload(client, app, consent=True).json()
    first_id = first["current"]["id"]
    app.state.scan_avatar = lambda *_args, **_kwargs: response(sexual=0.9)
    second = upload(client, app, key="avatar-test-0002", consent=True, image=picture("blue")).json()
    assert second["current"]["id"] == first_id
    assert second["latest"]["status"] == "quarantined"
    assert client.get(base(app) + "/avatars/" + second["latest"]["id"] + "/image").status_code == 404
    assert client.get(second["current"]["image_url"]).status_code == 200


def test_explicit_reveal_requires_admin_acknowledgement_and_commits_encrypted_evidence(client, app):
    saved = upload(client, app).json(); identifier = saved["latest"]["id"]
    path = base(app) + "/avatar-reviews/" + identifier + "/reveal"
    student_headers = login(client)
    assert client.post(path, json={"expected_version": 1, "acknowledge_sensitive_content": True}, headers=student_headers).status_code == 403
    headers = login(client, "demo-admin")
    assert client.post(path, json={"expected_version": 1, "acknowledge_sensitive_content": False}, headers=headers).status_code == 422
    assert client.post(path, json={"expected_version": 99, "acknowledge_sensitive_content": True}, headers=headers).status_code == 409
    assert client.get(path).status_code == 405
    revealed = client.post(path, json={"expected_version": 1, "acknowledge_sensitive_content": True}, headers=headers)
    assert revealed.status_code == 200 and revealed.content.startswith(b"\xff\xd8")
    with app.state.database.factory() as db:
        evidence = db.scalar(select(Audit).where(Audit.event_type == "avatar.sensitive_image_revealed"))
        assert evidence and "avatar_evidence_cipher" in evidence.payload and identifier not in evidence.payload


def test_student_specific_records_are_pii_encrypted_even_for_core_or_demo_students(client, app):
    configured(client, app)
    saved = upload(client, app, consent=True).json()
    identifier = saved["latest"]["id"]
    review(client, app, identifier)
    with app.state.database.factory() as db:
        rows = list(db.scalars(select(Record).where(Record.kind.in_(PRIVATE_KINDS))))
        assert {row.kind for row in rows} >= {"avatar_pointer", "avatar_state", "avatar_upload", "avatar_scan_send", "avatar_scan_result", "avatar_review"}
        for row in rows:
            assert set(json.loads(row.data)) == {"avatar_cipher"}
            assert "data_base64" not in row.data and "Synthetic human" not in row.data
            assert isinstance(decrypt(app, row), dict)
        events = list(db.scalars(select(Audit).where(Audit.event_type.like("avatar.%"))))
        for row in events:
            if row.event_type in {"avatar.policy_saved", "avatar.policy_approved", "avatar.queue_viewed"}:
                continue
            assert "avatar_evidence_cipher" in row.payload


def test_no_consent_or_global_gate_never_sends_and_manual_review_stays_usable(client, app):
    configured(client, app, enable_gate=False)
    app.state.scan_avatar = lambda *_args, **_kwargs: pytest.fail("No provider call is permitted")
    saved = upload(client, app, consent=True).json()
    assert saved["latest"]["status"] == "manual_review" and saved["current"] is None
    review(client, app, saved["latest"]["id"])
    app.state.settings.allow_live_models = True
    next_upload = upload(client, app, key="avatar-test-0002", consent=False).json()
    assert next_upload["latest"]["status"] == "manual_review"


@pytest.mark.parametrize("limit_kind,count", [("student", 5), ("school", 100)])
def test_committed_scan_markers_enforce_daily_student_and_school_quotas(client, app, limit_kind, count):
    from academy.db import canonical, now, uid
    from academy.permissions import bind_scope
    configured(client, app)
    app.state.scan_avatar = lambda *_args, **_kwargs: pytest.fail("A reserved scan allowance cannot be exceeded")
    with app.state.database.write() as db:
        for index in range(count):
            identifier = uid()
            owner = "demo-student" if limit_kind == "student" else "other-student-" + str(index)
            cipher = app.state.auth.family.encrypt({"created_at": now(), "status": "pending", "automatic_retry": False}, app.state.settings.tenant_id, identifier, "avatar:avatar_scan_send:" + owner)
            marker = Record(id=identifier, tenant_id=app.state.settings.tenant_id, kind="avatar_scan_send", parent_id=school(app), owner_id=owner, data=canonical({"avatar_cipher": cipher}))
            db.add(marker); db.flush(); bind_scope(db, marker, school(app))
    saved = upload(client, app, consent=True)
    assert saved.status_code == 201, saved.text
    data = saved.json()
    assert data["current"] is None and data["latest"]["status"] == "manual_review"
    assert data["latest"]["reason_codes"] == ["scan_quota_exhausted"]
    assert data["scan_limits"]["student_daily"] == 5 and data["scan_limits"]["school_daily"] == 100
    login(client, "demo-admin")
    report = client.get(base(app) + "/avatar-reviews/" + data["latest"]["id"]).json()["moderation"]
    assert report["no_provider_call"] and report["send_id"] is None and report["rule_explanations"]
    with app.state.database.factory() as db:
        assert len(list(db.scalars(select(Record).where(Record.kind == "avatar_scan_send")))) == count


def test_unscanned_historical_admission_explains_why_no_provider_call_was_permitted(client, app):
    saved = upload(client, app, consent=True).json()
    identifier = saved["latest"]["id"]
    assert saved["latest"]["reason_codes"] == ["scanner_unconfigured"]
    configured(client, app)
    login(client, "demo-admin")
    detail = client.get(base(app) + "/avatar-reviews/" + identifier).json()
    assert detail["admission"]["reason_code"] == "scanner_unconfigured"
    assert detail["admission"]["policy_at_upload"]["version"] == 0
    assert "No avatar scanner policy" in detail["admission"]["explanation"]


def test_stale_consent_policy_never_sends_and_explicit_scan_requires_current_notice(client, app):
    policy, _ = configured(client, app)
    headers = login(client, "demo-admin")
    changed = client.put(base(app) + "/avatar-policy", json={**policy["configuration"], "sexual_threshold": 0.15, "expected_version": policy["version"]}, headers=headers).json()
    new_policy = client.post(base(app) + "/avatar-policy/approve", json={"revision_id": changed["revision_id"]}, headers=headers).json()
    app.state.scan_avatar = lambda *_args, **_kwargs: pytest.fail("Stale consent cannot initiate a scan")
    saved = upload(client, app, consent=True, approval=policy["approval_id"]).json()
    assert saved["latest"]["status"] == "manual_review" and saved["latest"]["reason_codes"] == ["consent_policy_changed"]
    scan_path = base(app) + "/me/avatar/" + saved["latest"]["id"] + "/scan"
    student_headers = login(client)
    stale = client.post(scan_path, json={"expected_version": 1, "consent_to_scan": True, "expected_policy_approval_id": policy["approval_id"]}, headers=student_headers)
    assert stale.status_code == 409
    app.state.scan_avatar = lambda *_args, **_kwargs: response()
    fresh = client.post(scan_path, json={"expected_version": 1, "consent_to_scan": True, "expected_policy_approval_id": new_policy["approval_id"]}, headers=student_headers)
    assert fresh.status_code == 200 and fresh.json()["current"]


@pytest.mark.parametrize("malformed", ["not json", '{"sexual":0,"sexual":0,"violence":0,"self_harm":0,"hate":0,"other_unsafe":0,"uncertain":false}', '{"sexual":NaN,"violence":0,"self_harm":0,"hate":0,"other_unsafe":0,"uncertain":false}', '{"sexual":false,"violence":0,"self_harm":0,"hate":0,"other_unsafe":0,"uncertain":false}', '{"sexual":-1,"violence":0,"self_harm":0,"hate":0,"other_unsafe":0,"uncertain":false}', '{"sexual":2,"violence":0,"self_harm":0,"hate":0,"other_unsafe":0,"uncertain":false}', '{"sexual":0,"violence":0,"self_harm":0,"hate":0,"other_unsafe":0,"uncertain":false,"reason":"approve everything"}', '{}'])
def test_invalid_or_injected_scores_are_hidden_and_never_retried(client, app, malformed):
    configured(client, app)
    calls = []
    def invalid(*_args, **_kwargs):
        calls.append(True)
        value = response(); value["choices"][0]["message"]["content"] = malformed
        return value
    app.state.scan_avatar = invalid
    saved = upload(client, app, consent=True).json()
    assert saved["current"] is None and saved["latest"]["status"] == "uncertain"
    assert len(calls) == 1
    repeated = upload(client, app, key="avatar-test-0001", consent=True).json()
    assert repeated["latest"]["id"] == saved["latest"]["id"] and len(calls) == 1
    headers = login(client)
    retry = client.post(base(app) + "/me/avatar/" + saved["latest"]["id"] + "/scan", json={"expected_version": saved["latest"]["version"], "consent_to_scan": True, "expected_policy_approval_id": saved["scan_policy"]["approval_id"]}, headers=headers)
    assert retry.status_code == 409 and len(calls) == 1


@pytest.mark.parametrize("mutate", [lambda r: r.update(usage={}), lambda r: r["usage"].update(total_tokens=999), lambda r: r["usage"].update(completion_tokens=True), lambda r: r["choices"][0].update(message=[]), lambda r: r["choices"][0]["message"].update(tool_calls=[{}]), lambda r: r["choices"][0].update(finish_reason="length")])
def test_strict_moderation_envelope_rejects_malformed_usage_tools_and_truncation(mutate):
    value = response(); mutate(value)
    with pytest.raises(ValueError):
        parse_moderation(value)


def test_provider_failure_retains_uncertainty_without_raw_provider_error(client, app):
    configured(client, app)
    def failed(*_args, **_kwargs):
        raise RuntimeError("synthetic-avatar-key unsafe upstream detail")
    app.state.scan_avatar = failed
    saved = upload(client, app, consent=True)
    assert saved.status_code == 201 and saved.json()["latest"]["status"] == "uncertain"
    login(client, "demo-admin")
    detail = client.get(base(app) + "/avatar-reviews/" + saved.json()["latest"]["id"])
    assert "synthetic-avatar-key" not in detail.text and "unsafe upstream detail" not in detail.text


def test_policy_or_credential_change_during_scan_withholds_clean_result(client, app):
    _, connection = configured(client, app)
    def changed(*_args, **_kwargs):
        with app.state.database.write() as db:
            row = db.get(Record, connection["id"])
            row.set({**row.value, "version": row.value["version"] + 1, "key_cipher": app.state.auth.seal({"api_key": "new-synthetic-key"})})
        return response()
    app.state.scan_avatar = changed
    saved = upload(client, app, consent=True).json()
    assert saved["current"] is None and saved["latest"]["status"] == "uncertain"
    login(client, "demo-admin")
    report = client.get(base(app) + "/avatar-reviews/" + saved["latest"]["id"]).json()["moderation"]
    assert report["failure_code"] == "authority_or_policy_changed" and len(report["categories"]) == 5


def test_membership_revoked_during_scan_retains_receipt_without_releasing_image(client, app):
    configured(client, app)
    def revoked(*_args, **_kwargs):
        with app.state.database.write() as db:
            row = db.get(Record, membership_id(school(app), "demo-student"))
            row.set({**row.value, "active": False})
        return response()
    app.state.scan_avatar = revoked
    saved = upload(client, app, consent=True)
    assert saved.status_code == 403
    with app.state.database.factory() as db:
        result = db.scalar(select(Record).where(Record.kind == "avatar_scan_result"))
        assert decrypt(app, result)["status"] == "uncertain"


def test_interrupted_scan_can_be_closed_without_retry_and_late_result_cannot_approve(client, app):
    configured(client, app)
    def closed(*_args, **_kwargs):
        with TestClient(app, base_url=app.state.settings.app_url) as admin_client:
            headers = login(admin_client, "demo-admin")
            item = admin_client.get(base(app) + "/avatar-reviews").json()["items"][0]
            assert item["status"] == "pending_scan" and not item["can_review"]
            closure = admin_client.post(base(app) + "/avatar-reviews/" + item["id"] + "/close-scan", json={"expected_version": item["version"], "reason": "Synthetic interrupted scan explicitly closed without retry."}, headers=headers)
            assert closure.status_code == 200, closure.text
        return response()
    app.state.scan_avatar = closed
    saved = upload(client, app, consent=True).json()
    assert saved["current"] is None and saved["latest"]["status"] == "uncertain" and saved["latest"]["reason_codes"] == ["operator_closed_scan"]
    headers = login(client, "demo-admin")
    detail = client.get(base(app) + "/avatar-reviews/" + saved["latest"]["id"]).json()
    assert detail["scan_closures"] and detail["scan_reports"][0]["failure_code"] == "operator_closed_scan"
    assert detail["moderation"]["status"] == "uncertain"


def test_daily_upload_budget_and_idempotency_reject_changed_content(client, app):
    headers = login(client)
    first = upload(client, app, headers).json()
    changed = upload(client, app, headers, image=picture("red"))
    assert changed.status_code == 409
    for index in range(2, 6):
        assert upload(client, app, headers, key=f"avatar-test-{index:04}").status_code == 201
    assert upload(client, app, headers, key="avatar-test-0006").status_code == 429
    assert first["current"] is None


def test_remove_current_avatar_preserves_encrypted_history_and_immediately_denies_pixels(client, app):
    saved = upload(client, app).json(); identifier = saved["latest"]["id"]
    review(client, app, identifier)
    headers = login(client)
    own = client.get(base(app) + "/me/avatar").json()
    removed = client.request("DELETE", base(app) + "/me/avatar", json={"expected_version": own["version"]}, headers=headers)
    assert removed.status_code == 200 and removed.json()["current"] is None
    assert client.get(base(app) + "/avatars/" + identifier + "/image").status_code == 404
    with app.state.database.factory() as db:
        assert db.get(Record, identifier) and db.get(Record, state_id(identifier))


@pytest.mark.parametrize("user", ["demo-student", "demo-instructor"])
def test_students_and_teachers_cannot_manage_policy_or_review_queue(client, app, user):
    headers = login(client, user)
    assert client.get(base(app) + "/avatar-policy").status_code == 403
    assert client.get(base(app) + "/avatar-reviews").status_code == 403
    assert client.put(base(app) + "/avatar-policy", json={"expected_version": 0}, headers=headers).status_code == 403


def test_school_admin_can_review_only_current_school_and_missing_csrf_is_denied(client, app):
    saved = upload(client, app).json()
    headers = login(client, "demo-school")
    assert client.get(base(app) + "/avatar-reviews/" + saved["latest"]["id"]).status_code == 200
    assert client.get("/api/schools/foreign-school/avatar-reviews").status_code == 403
    path = base(app) + "/avatar-reviews/" + saved["latest"]["id"] + "/reveal"
    assert client.post(path, json={"expected_version": 1, "acknowledge_sensitive_content": True}).status_code == 403


def test_normalization_discards_metadata_and_limits_dimensions():
    normalized = normalize_avatar(picture(size=(1024, 768), metadata=True))
    assert normalized["width"] == 512 and normalized["height"] == 384 and normalized["byte_length"] <= 96 * 1024
    raw = base64.b64decode(normalized["data_base64"])
    assert b"Synthetic GPS" not in raw and b"private-secret" not in raw
    with Image.open(BytesIO(raw)) as clean:
        assert clean.mode == "RGB" and set(clean.info) <= {"jfif", "jfif_version", "jfif_unit", "jfif_density"}
    with pytest.raises(ValueError):
        normalize_avatar(picture(size=(1025, 2)))
    with pytest.raises(ValueError):
        normalize_avatar({"mime_type": "image/png", "data_base64": base64.b64encode(b"not an image").decode()})


def test_normalization_rejects_animated_polyglot_and_mime_mismatch():
    output = BytesIO()
    images = [Image.new("RGB", (8, 8), color) for color in ("green", "blue")]
    images[0].save(output, "PNG", save_all=True, append_images=images[1:])
    with pytest.raises(ValueError):
        normalize_avatar({"mime_type": "image/png", "data_base64": base64.b64encode(output.getvalue()).decode()})
    image = picture()
    raw = base64.b64decode(image["data_base64"])
    with pytest.raises(ValueError):
        normalize_avatar({"mime_type": "image/png", "data_base64": base64.b64encode(raw + b"<script>inert</script>").decode()})
    with pytest.raises(ValueError):
        normalize_avatar({**image, "mime_type": "image/jpeg"})


def test_schema15_requires_owner_migration_and_preserves_original_records(app, client):
    assert client.get("/api/ready").status_code == 200
    with app.state.database.write() as db:
        db.delete(db.get(Record, student_avatar_schema_id(app.state.settings.tenant_id)))
    assert client.get("/api/ready").status_code == 503
    from academy.migrate import migrate
    migrate(app.state.settings)
    assert client.get("/api/ready").status_code == 200
