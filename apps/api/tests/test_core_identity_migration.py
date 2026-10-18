"""Synthetic authority migration preserves coursework and append-only history."""
import json
import time

import pytest
from sqlalchemy import delete, event, select

from academy.core_identity import IdentityConflict, legacy_user_id, resolve_user
from academy.db import Audit, AuditHead, CoreIdentityBinding, Flow, Login, Record, RecordScope, audit, audit_dict, canonical
from academy.migrate_core_issuer import MigrationBlocked, backfill
from academy.permissions import default_school_id, membership_id
from test_core_auth import core, sign_in, begin

OLD = "https://previous-core.example.test"
SUBJECT = "44444444-4444-4444-8444-444444444444"
OTHER = "55555555-5555-4555-8555-555555555555"


def migrate(app, **kwargs):
    return backfill(app.state.database, app.state.settings, old_issuer=OLD,
                    new_issuer=app.state.settings.core_issuer,
                    expected_tenant=app.state.settings.tenant_id, subjects=[SUBJECT], **kwargs)


def complete(core):
    _, client, _ = core
    query = begin(core)
    return client.get("/api/auth/core/callback", params={"code": "synthetic", "state": query["state"][0]}, follow_redirects=False)


def seed(app, issuer=OLD):
    tenant = app.state.settings.tenant_id
    owner = legacy_user_id(issuer, SUBJECT)
    with app.state.database.write() as db:
        db.add(Record(id=owner, tenant_id=tenant, kind="user", data=canonical({"name": "Existing student", "role": "student"})))
        db.add(Record(id="owned-course", tenant_id=tenant, kind="course", data=canonical({"title": "Existing course"})))
        db.add(Record(id="owned-enrollment", tenant_id=tenant, kind="enrollment", parent_id="owned-course", owner_id=owner, data=canonical({"role": "student"})))
        db.add(Record(id="retained-submission", tenant_id=tenant, kind="submission", parent_id="assignment", owner_id=owner, data=canonical({"content": "Student-authored immutable work"})))
        school_id = default_school_id(tenant)
        db.add(Record(id=membership_id(school_id, owner), tenant_id=tenant, kind="school_membership", parent_id=school_id, owner_id=owner, data=canonical({"role": "student", "active": True})))
        for record_id in ("owned-course", "owned-enrollment", "retained-submission"):
            db.add(RecordScope(record_id=record_id, tenant_id=tenant, school_id=school_id))
        db.add(Login(id="old-login", user_id=owner, tenant_id=tenant, csrf="synthetic-csrf", token_cipher="synthetic-encrypted", expires=int(time.time()) + 300))
        db.add(Flow(state_hash="old-flow", cipher="synthetic", expires=int(time.time()) + 300))
        audit(db, tenant, owner, "synthetic.coursework", {"submission_id": "retained-submission"}, "stable-tutor-session")
    return owner


def state(app):
    with app.state.database.factory() as db:
        return {model.__tablename__: [dict(row) for row in db.execute(select(model.__table__)).mappings()]
                for model in (Record, RecordScope, Login, Flow, Audit, AuditHead, CoreIdentityBinding)}


def test_fresh_login_records_exact_authority_binding(core):
    app, _, _ = core
    user = sign_in(core)
    with app.state.database.factory() as db:
        binding = db.get(CoreIdentityBinding, (app.state.settings.tenant_id, app.state.settings.core_issuer, SUBJECT))
        assert binding.user_id == user["id"] == legacy_user_id(app.state.settings.core_issuer, SUBJECT)


def test_same_issuer_legacy_user_is_adopted_without_rekey(core):
    app, _, _ = core
    owner = seed(app, app.state.settings.core_issuer)
    assert sign_in(core)["id"] == owner
    with app.state.database.factory() as db:
        assert db.get(Record, owner).value["name"] == "Existing student"
        assert len(list(db.scalars(select(Record).where(Record.kind == "user")))) == 1


def test_config_only_issuer_change_cannot_create_duplicate_owner(core):
    app, _, _ = core
    owner = seed(app)
    with app.state.database.write() as db:
        db.add(CoreIdentityBinding(tenant_id=app.state.settings.tenant_id, issuer=OLD, subject=SUBJECT, user_id=owner))
    assert complete(core).status_code == 403
    with app.state.database.factory() as db:
        assert list(db.scalars(select(Record.id).where(Record.kind == "user"))) == [owner]
        assert db.get(Record, legacy_user_id(app.state.settings.core_issuer, SUBJECT)) is None


def test_new_subject_allowed_after_current_authority_covers_existing_owners(core):
    app, _, _ = core
    sign_in(core)
    with app.state.database.write() as db:
        user = resolve_user(db, app.state.settings.tenant_id, app.state.settings.core_issuer, OTHER, "Another learner")
        assert user.id == legacy_user_id(app.state.settings.core_issuer, OTHER)


def test_migrated_login_preserves_enrollment_submissions_and_audit(core):
    app, client, _ = core
    owner = seed(app)
    before = state(app)
    plan = migrate(app)
    assert state(app) == before  # A plan has no DML, including no audit append.
    report = migrate(app, apply=True, plan_sha256=plan["plan_sha256"])
    assert report["status"] == "applied" and report["sessions_cleared"] == 1 and report["oauth_flows_cleared"] == 1
    after = state(app)
    assert after["records"] == before["records"]
    assert after["audit_events"][:len(before["audit_events"])] == before["audit_events"]
    assert len(after["audit_events"]) == len(before["audit_events"]) + 1
    assert after["logins"] == []
    assert sign_in(core)["id"] == owner
    assert [c["id"] for c in client.get("/api/courses").json()] == ["owned-course"]
    with app.state.database.write() as db:
        assert resolve_user(db, app.state.settings.tenant_id, OLD, SUBJECT, "Ignored").id == owner
    canonical_issuer = app.state.settings.core_issuer
    app.state.settings.core_issuer = OLD
    assert sign_in(core)["id"] == owner  # Configuration rollback uses the retained old alias.
    app.state.settings.core_issuer = canonical_issuer
    # Original audit bytes/owner/session IDs remain valid as the chain advances.
    from importlib.util import module_from_spec, spec_from_file_location
    from pathlib import Path
    spec = spec_from_file_location("audit_verifier", Path(__file__).resolve().parents[3] / "scripts/verify_audit.py")
    verifier = module_from_spec(spec)
    spec.loader.exec_module(verifier)
    with app.state.database.factory() as db:
        events = [audit_dict(row) for row in db.scalars(select(Audit).order_by(Audit.sequence))]
        head = db.get(AuditHead, app.state.settings.tenant_id)
        verifier.verify({"tenant_id": head.tenant_id, "head": {"sequence": head.sequence, "digest": head.digest}, "events": events})


def test_apply_retry_preserves_new_login_flows_and_chain(core):
    app, _, _ = core
    seed(app)
    digest = migrate(app)["plan_sha256"]
    migrate(app, apply=True, plan_sha256=digest)
    sign_in(core)
    with app.state.database.write() as db:
        db.add(Flow(state_hash="new-flow", cipher="synthetic", expires=int(time.time()) + 100))
    before = state(app)
    result = migrate(app, apply=True, plan_sha256=digest)
    assert result["status"] == "already_migrated" and result["sessions_cleared"] == result["oauth_flows_cleared"] == 0
    assert state(app) == before


def test_empty_tenant_receipt_also_prevents_repeat_cleanup(core):
    app, _, _ = core
    digest = migrate(app)["plan_sha256"]
    migrate(app, apply=True, plan_sha256=digest)
    with app.state.database.write() as db:
        db.add(Flow(state_hash="new-flow", cipher="synthetic", expires=int(time.time()) + 100))
    before = state(app)
    assert migrate(app, apply=True, plan_sha256=digest)["status"] == "already_migrated"
    assert state(app) == before


@pytest.mark.parametrize("kind", ["new_user", "new_binding", "foreign_tenant", "wrong_kind", "unknown_subject"])
def test_conflicts_abort_without_rewriting_any_data(core, kind):
    app, _, _ = core
    owner = seed(app)
    tenant = app.state.settings.tenant_id
    with app.state.database.write() as db:
        if kind == "new_user":
            db.add(Record(id=legacy_user_id(app.state.settings.core_issuer, SUBJECT), tenant_id=tenant, kind="user"))
        elif kind == "new_binding":
            db.add(Record(id="competing-user", tenant_id=tenant, kind="user"))
            db.add(CoreIdentityBinding(tenant_id=tenant, issuer=app.state.settings.core_issuer, subject=SUBJECT, user_id="competing-user"))
        elif kind in {"foreign_tenant", "wrong_kind"}:
            row = db.get(Record, owner)
            row.tenant_id = OTHER if kind == "foreign_tenant" else tenant
            row.kind = "user" if kind == "foreign_tenant" else "course"
        else:
            db.add(CoreIdentityBinding(tenant_id=tenant, issuer=OLD, subject=OTHER, user_id=owner))
    before = state(app)
    with pytest.raises((MigrationBlocked, IdentityConflict)):
        migrate(app, apply=True, plan_sha256="0" * 64)
    assert state(app) == before


def test_missing_inventory_foreign_scope_and_changed_plan_fail_closed(core):
    app, _, _ = core
    seed(app)
    before = state(app)
    for tenant, subjects in ((OTHER, [SUBJECT]), (app.state.settings.tenant_id, []),
                             (app.state.settings.tenant_id, ["not-a-uuid"]),
                             (app.state.settings.tenant_id, [SUBJECT, SUBJECT])):
        with pytest.raises(MigrationBlocked):
            backfill(app.state.database, app.state.settings, old_issuer=OLD,
                     new_issuer=app.state.settings.core_issuer, expected_tenant=tenant, subjects=subjects)
    with pytest.raises(MigrationBlocked, match="reviewed_plan_changed"):
        migrate(app, apply=True, plan_sha256="0" * 64)
    assert state(app) == before


@pytest.mark.parametrize("kind", ["record", "login", "binding", "audit_head"])
def test_foreign_deployment_data_blocks_global_flow_cleanup(core, kind):
    app, _, _ = core
    seed(app)
    with app.state.database.write() as db:
        if kind == "record":
            db.add(Record(id="foreign-row", tenant_id=OTHER, kind="course"))
        elif kind == "login":
            db.add(Login(id="foreign-login", user_id="foreign", tenant_id=OTHER, csrf="synthetic", expires=1))
        elif kind == "binding":
            db.add(CoreIdentityBinding(tenant_id=OTHER, issuer=OLD, subject=OTHER, user_id="foreign"))
        else:
            db.add(AuditHead(tenant_id=OTHER))
    before = state(app)
    with pytest.raises(MigrationBlocked, match="foreign_deployment_tenant"):
        migrate(app, apply=True, plan_sha256="0" * 64)
    assert state(app) == before


def test_failure_after_binding_insert_and_session_clear_rolls_back(core):
    app, _, _ = core
    seed(app)
    digest = migrate(app)["plan_sha256"]
    before = state(app)
    def fail_audit(connection, cursor, statement, parameters, context, executemany):
        if statement.startswith("INSERT INTO audit_events"):
            raise RuntimeError("Synthetic post-cleanup failure")
    event.listen(app.state.database.engine, "before_cursor_execute", fail_audit)
    try:
        with pytest.raises(RuntimeError):
            migrate(app, apply=True, plan_sha256=digest)
    finally:
        event.remove(app.state.database.engine, "before_cursor_execute", fail_audit)
    assert state(app) == before


@pytest.mark.parametrize("kind", ["foreign_tenant", "wrong_kind", "disabled"])
def test_login_rejects_invalid_binding_target(core, kind):
    app, _, _ = core
    with app.state.database.write() as db:
        db.add(Record(id="bound-user", tenant_id=OTHER if kind == "foreign_tenant" else app.state.settings.tenant_id,
                      kind="course" if kind == "wrong_kind" else "user", data=canonical({"disabled": kind == "disabled"})))
        db.add(CoreIdentityBinding(tenant_id=app.state.settings.tenant_id, issuer=app.state.settings.core_issuer, subject=SUBJECT, user_id="bound-user"))
    assert complete(core).status_code == 403


def test_live_session_checks_binding_and_never_links_by_name(core):
    app, client, _ = core
    me = sign_in(core)
    with app.state.database.write() as db:
        db.execute(delete(CoreIdentityBinding))
        db.add(Record(id="other-owner", tenant_id=app.state.settings.tenant_id, kind="user", data=canonical({"name": "Synthetic Core learner"})))
        db.add(CoreIdentityBinding(tenant_id=app.state.settings.tenant_id, issuer=app.state.settings.core_issuer,
                                   subject=SUBJECT, user_id="other-owner"))
    assert client.get("/api/auth/me").status_code == 401
    assert me["id"] != "other-owner"


@pytest.mark.parametrize("subject", ["not-a-uuid", "AAAAAAAA-AAAA-4AAA-8AAA-AAAAAAAAAAAA", None])
def test_runtime_binding_requires_canonical_subject(core, subject):
    app, _, _ = core
    with pytest.raises(IdentityConflict):
        with app.state.database.write() as db:
            resolve_user(db, app.state.settings.tenant_id, app.state.settings.core_issuer, subject, "No name link")
