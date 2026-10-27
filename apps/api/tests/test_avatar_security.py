"""Independent avatar boundary checks using benign pixels and isolated records."""
import json

from fastapi import HTTPException
from fastapi.testclient import TestClient
import pytest
from sqlalchemy import select

from academy.db import Audit, Record
from academy.permissions import membership_id
from academy.student_avatars import PRIVATE_KINDS
from conftest import login
from test_student_avatars import base, configured, decrypt, picture, response, review, school, upload


def test_credential_revocation_after_committed_marker_prevents_the_actual_send(client, app, monkeypatch):
    _, connection = configured(client, app)
    headers = login(client)
    original_actor = app.state.auth.actor
    changed = []
    calls = []

    def revoke_at_durable_boundary(request):
        identity = original_actor(request)
        if not changed:
            with app.state.database.factory() as db:
                marker = db.scalar(select(Record).where(Record.kind == "avatar_scan_send"))
                marker_id = marker.id if marker else None
            if marker_id:
                # A separate transaction can already read the committed
                # marker. Change the exact admitted credential before I/O.
                changed.append(marker_id)
                with app.state.database.write() as db:
                    row = db.get(Record, connection["id"])
                    row.set({**row.value, "key_cipher": app.state.auth.seal({"api_key": "replacement-synthetic-key"}), "version": row.value["version"] + 1})
        return identity

    monkeypatch.setattr(app.state.auth, "actor", revoke_at_durable_boundary)
    app.state.scan_avatar = lambda *_args, **_kwargs: calls.append(True) or response()
    saved = upload(client, app, headers, consent=True)
    assert saved.status_code == 201, saved.text
    assert changed and calls == []
    assert saved.json()["current"] is None and saved.json()["latest"]["status"] == "uncertain"
    with app.state.database.factory() as db:
        results = list(db.scalars(select(Record).where(Record.kind == "avatar_scan_result")))
        assert len(results) == 1
        assert decrypt(app, results[0])["automatic_retry"] is False


def test_parallel_upload_cannot_replace_a_pending_scan(client, app):
    configured(client, app)
    concurrent = []

    def attempt_replacement(*_args, **_kwargs):
        with TestClient(app, base_url=app.state.settings.app_url) as other_client:
            other_headers = login(other_client)
            concurrent.append(upload(other_client, app, other_headers, key="parallel-avatar-0002", image=picture("blue")))
        return response()

    app.state.scan_avatar = attempt_replacement
    saved = upload(client, app, consent=True)
    assert saved.status_code == 201, saved.text
    assert len(concurrent) == 1 and concurrent[0].status_code == 409
    assert saved.json()["current"]["id"] == saved.json()["latest"]["id"]
    with app.state.database.factory() as db:
        assert len(list(db.scalars(select(Record).where(Record.kind == "avatar_upload")))) == 1


def test_student_removal_during_provider_io_cannot_be_undone_by_a_late_clean_scan(client, app):
    configured(client, app)

    def remove_while_awaiting_result(*_args, **_kwargs):
        with TestClient(app, base_url=app.state.settings.app_url) as other_client:
            headers = login(other_client)
            own = other_client.get(base(app) + "/me/avatar").json()
            assert own["latest"]["status"] == "pending_scan"
            removed = other_client.request("DELETE", base(app) + "/me/avatar", json={"expected_version": own["version"]}, headers=headers)
            assert removed.status_code == 200 and removed.json()["current"] is None
        return response()

    app.state.scan_avatar = remove_while_awaiting_result
    saved = upload(client, app, consent=True)
    assert saved.status_code == 201, saved.text
    assert saved.json()["current"] is None and saved.json()["latest"] is None
    with app.state.database.factory() as db:
        report = decrypt(app, db.scalar(select(Record).where(Record.kind == "avatar_scan_result")))
        assert report["status"] == "uncertain" and report["failure_code"] == "avatar_replaced"


def test_disabled_school_during_scan_retains_private_evidence_without_display(client, app):
    configured(client, app)

    def deactivate_school(*_args, **_kwargs):
        with app.state.database.write() as db:
            row = db.get(Record, school(app))
            row.set({**row.value, "disabled": True})
        return response()

    app.state.scan_avatar = deactivate_school
    saved = upload(client, app, consent=True)
    assert saved.status_code == 403
    with app.state.database.factory() as db:
        report = decrypt(app, db.scalar(select(Record).where(Record.kind == "avatar_scan_result")))
        assert report["status"] == "uncertain" and report["failure_code"] == "authority_or_policy_changed"


def test_current_school_role_is_required_for_every_known_image_and_review_route(client, app):
    saved = upload(client, app).json()
    identifier = saved["latest"]["id"]
    review(client, app, identifier)
    admin = login(client, "demo-admin")
    created = client.post("/api/schools", json={"name": "Synthetic separate school"}, headers=admin)
    assert created.status_code == 201, created.text
    second_school = created.json()["id"]
    assert client.put(f"/api/schools/{second_school}/members/demo-school", json={"role": "school", "active": True}, headers=admin).status_code == 200
    assert client.delete(base(app) + "/members/demo-school", headers=admin).status_code == 200
    school_headers = login(client, "demo-school")
    for suffix in ("/avatar-reviews", "/avatar-reviews/" + identifier, "/avatars/" + identifier + "/image"):
        assert client.get(base(app) + suffix).status_code == 403
    revealed = client.post(base(app) + "/avatar-reviews/" + identifier + "/reveal", json={"expected_version": 2, "acknowledge_sensitive_content": True}, headers=school_headers)
    assert revealed.status_code == 403
    # Actual authority over a different school cannot relabel a known record.
    other = "/api/schools/" + second_school
    assert client.get(other + "/avatar-reviews/" + identifier).status_code == 404
    assert client.get(other + "/avatars/" + identifier + "/image").status_code == 404


def test_avatar_ciphertext_cannot_be_transplanted_to_another_record_context(client, app):
    first = upload(client, app).json()["latest"]["id"]
    second = upload(client, app, key="avatar-swap-0002", image=picture("blue")).json()["latest"]["id"]
    with app.state.database.factory() as db:
        source = db.get(Record, first)
        target = db.get(Record, second)
        assert source.kind == target.kind == "avatar_upload"
        assert set(json.loads(source.data)) == {"avatar_cipher"}
        with pytest.raises(HTTPException) as failure:
            app.state.auth.family.decrypt(source.value["avatar_cipher"], target.tenant_id, target.id, "avatar:" + target.kind + ":" + target.owner_id)
        assert failure.value.status_code == 503


def test_rejected_private_request_fields_never_echo_or_persist_their_values(client, app):
    headers = login(client)
    private_name = "SYNTHETIC_PRIVATE_STUDENT_FILENAME"
    raw_image = picture()
    failed = client.post(base(app) + "/me/avatar", json={"expected_version": 0, "idempotency_key": "bad-image-metadata", "consent_to_scan": False, "image": {**raw_image, "filename": private_name}}, headers=headers)
    assert failed.status_code == 422
    assert private_name not in failed.text and raw_image["data_base64"] not in failed.text
    with app.state.database.factory() as db:
        assert not list(db.scalars(select(Record).where(Record.kind.in_(PRIVATE_KINDS))))
        assert not list(db.scalars(select(Audit).where(Audit.event_type == "avatar.uploaded")))


def test_reviewer_membership_revocation_immediately_denies_sensitive_reveal(client, app):
    identifier = upload(client, app).json()["latest"]["id"]
    school_headers = login(client, "demo-school")
    assert client.get(base(app) + "/avatar-reviews/" + identifier).status_code == 200
    with app.state.database.write() as db:
        member = db.get(Record, membership_id(school(app), "demo-school"))
        member.set({**member.value, "active": False})
    result = client.post(base(app) + "/avatar-reviews/" + identifier + "/reveal", json={"expected_version": 1, "acknowledge_sensitive_content": True}, headers=school_headers)
    assert result.status_code == 403
    with app.state.database.factory() as db:
        assert not list(db.scalars(select(Audit).where(Audit.event_type == "avatar.sensitive_image_revealed")))


def close_and_review_pending(app, decision):
    """Use the real administrator routes while the synthetic provider waits."""
    with TestClient(app, base_url=app.state.settings.app_url) as admin_client:
        headers = login(admin_client, "demo-admin")
        item = admin_client.get(base(app) + "/avatar-reviews").json()["items"][0]
        closed = admin_client.post(base(app) + "/avatar-reviews/" + item["id"] + "/close-scan", json={"expected_version": item["version"], "reason": "Synthetic interrupted scan closed for independent human review."}, headers=headers)
        assert closed.status_code == 200, closed.text
        detail = closed.json()
        revealed = admin_client.post(base(app) + "/avatar-reviews/" + item["id"] + "/reveal", json={"expected_version": detail["version"], "acknowledge_sensitive_content": True}, headers=headers)
        assert revealed.status_code == 200
        decided = admin_client.post(base(app) + "/avatar-reviews/" + item["id"] + "/decision", json={"expected_version": detail["version"], "decision": decision, "reason": "Synthetic exact-image human decision before late scan receipt."}, headers=headers)
        assert decided.status_code == 200, decided.text
        return decided.json()


@pytest.mark.parametrize("category,status", [("sexual", "quarantined"), ("violence", "flagged_review")])
def test_valid_late_negative_rehides_prior_human_approval_and_requires_new_review(client, app, category, status):
    configured(client, app)
    prior_decision = []

    def approve_before_late_negative(*_args, **_kwargs):
        prior_decision.append(close_and_review_pending(app, "approve"))
        return response(**{category: 0.99})

    app.state.scan_avatar = approve_before_late_negative
    saved = upload(client, app, consent=True)
    assert saved.status_code == 201, saved.text
    own = saved.json()
    assert own["current"] is None and own["latest"]["status"] == status
    assert category + "_detected" in own["latest"]["reason_codes"]
    assert "late_negative_scan" in own["latest"]["reason_codes"]
    identifier = own["latest"]["id"]
    assert client.get(base(app) + "/avatars/" + identifier + "/image").status_code == 404
    login(client, "demo-admin")
    detail = client.get(base(app) + "/avatar-reviews/" + identifier).json()
    assert detail["reviews"] == prior_decision[0]["reviews"]
    assert detail["moderation"]["status"] == status
    assert any("previous approval is preserved" in value for value in detail["moderation"]["rule_explanations"])
    # An administrator can inspect the newly negative report and explicitly
    # override a false positive without changing the historical approval.
    reviewed = review(client, app, identifier)
    assert len(reviewed["reviews"]) == 2 and reviewed["status"] == "approved"


@pytest.mark.parametrize("valid", [True, False])
def test_late_clean_or_invalid_scan_cannot_undo_a_human_rejection(client, app, valid):
    configured(client, app)

    def reject_before_late_result(*_args, **_kwargs):
        close_and_review_pending(app, "reject")
        return response() if valid else {"choices": []}

    app.state.scan_avatar = reject_before_late_result
    saved = upload(client, app, consent=True)
    assert saved.status_code == 201, saved.text
    own = saved.json()
    assert own["current"] is None and own["latest"]["status"] == "rejected"
    assert own["latest"]["reason_codes"] == ["human_rejected"]
    login(client, "demo-admin")
    detail = client.get(base(app) + "/avatar-reviews/" + own["latest"]["id"]).json()
    assert len(detail["reviews"]) == 1 and detail["reviews"][0]["decision"] == "reject"
    assert detail["scan_reports"] and detail["moderation"]["status"] == "uncertain"


def test_late_negative_for_old_candidate_cannot_remove_a_different_current_avatar(client, app):
    configured(client, app)
    replacement = []

    def replace_before_late_negative(*_args, **_kwargs):
        close_and_review_pending(app, "approve")
        with TestClient(app, base_url=app.state.settings.app_url) as other_client:
            student_headers = login(other_client)
            created = upload(other_client, app, student_headers, key="new-candidate-0002", image=picture("blue"))
            assert created.status_code == 201, created.text
            identifier = created.json()["latest"]["id"]
            review(other_client, app, identifier)
            replacement.append(identifier)
        return response(sexual=0.99)

    app.state.scan_avatar = replace_before_late_negative
    saved = upload(client, app, consent=True)
    assert saved.status_code == 201, saved.text
    own = saved.json()
    assert own["current"]["id"] == replacement[0] == own["latest"]["id"]
    assert own["latest"]["status"] == "approved"
    assert client.get(own["current"]["image_url"]).status_code == 200
