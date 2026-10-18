"""Operator-only, reviewed Core issuer aliases; no application startup or network."""
import argparse
from contextlib import contextmanager
import hashlib
import json
from pathlib import Path
import re
from urllib.parse import urlsplit

from sqlalchemy import delete, inspect, select, text

from .core_identity import canonical_subject, checked_user, legacy_user_id
from .db import Audit, AuditHead, CoreIdentityBinding, Database, Flow, Login, Record, audit, canonical

EVENT = "auth.core_issuer_migrated"


class MigrationBlocked(ValueError):
    pass


def require(condition, code):
    if not condition:
        raise MigrationBlocked(code)


def origin(value):
    parsed = urlsplit(value)
    require(parsed.scheme == "https" and parsed.hostname and not parsed.path and
            not parsed.query and not parsed.fragment and not parsed.username and
            not parsed.password and value == "https://" + parsed.netloc and
            len(value) <= 500 and not any(c.isspace() or ord(c) < 32 for c in value),
            "invalid_issuer_origin")
    return value


@contextmanager
def read_transaction(database):
    with database.factory() as db, db.begin():
        if database.engine.dialect.name == "postgresql":
            db.execute(text("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ READ ONLY"))
            db.execute(text("SET LOCAL search_path = public"))
            db.execute(text("SET LOCAL statement_timeout = '30s'"))
        yield db


def snapshot(db, tenant, old_issuer, new_issuer, subjects):
    bindings = list(db.scalars(select(CoreIdentityBinding).where(CoreIdentityBinding.tenant_id == tenant)))
    require(all(b.issuer in {old_issuer, new_issuer} and b.subject in subjects for b in bindings),
            "unreviewed_existing_authority")
    by_key = {(b.issuer, b.subject): b for b in bindings}
    mappings, missing = [], []
    for subject in subjects:
        old = by_key.get((old_issuer, subject))
        new = by_key.get((new_issuer, subject))
        legacy_id = legacy_user_id(old_issuer, subject)
        legacy = db.get(Record, legacy_id)
        if legacy is not None:
            checked_user(db, tenant, legacy_id, allow_disabled=True)
        if not old and not legacy:
            require(new is None, "new_authority_user_without_old_identity")
            continue  # The Core inventory can include members who never used this app.
        user_id = old.user_id if old else legacy_id
        checked_user(db, tenant, user_id, allow_disabled=True)
        require(legacy is None or legacy.id == user_id, "competing_legacy_identity")
        new_candidate = db.get(Record, legacy_user_id(new_issuer, subject))
        require(new_candidate is None or new_candidate.id == user_id, "duplicate_new_authority_user")
        require(new is None or new.user_id == user_id, "competing_new_authority_binding")
        for issuer, existing in ((old_issuer, old), (new_issuer, new)):
            if existing:
                checked_user(db, tenant, existing.user_id, allow_disabled=True)
            else:
                missing.append((issuer, subject, user_id))
        mappings.append({"subject": subject, "user_id": user_id})
    user_ids = {m["user_id"] for m in mappings}
    require(len(user_ids) == len(mappings), "competing_subject_owner")
    existing_users = set(db.scalars(select(Record.id).where(Record.tenant_id == tenant, Record.kind == "user")))
    require(user_ids == existing_users, "unmapped_local_user")
    return mappings, missing


def backfill(database, settings, *, old_issuer, new_issuer, expected_tenant,
             subjects, apply=False, plan_sha256=None):
    """Map explicitly verified same Core subjects to their unchanged app owners.

    Call only with an authoritative organization subject inventory obtained by
    the deployment operator. Display names and email are never matching inputs.
    """
    origin(old_issuer), origin(new_issuer)
    require(old_issuer != new_issuer, "distinct_issuers_required")
    require(settings.auth_mode == "core" and canonical_subject(expected_tenant) and
            settings.tenant_id == expected_tenant, "foreign_tenant_or_auth_mode")
    require(settings.core_issuer in {old_issuer, new_issuer}, "unexpected_runtime_issuer")
    require(isinstance(subjects, list) and len(subjects) <= 10000 and
            all(canonical_subject(s) for s in subjects) and len(subjects) == len(set(subjects)),
            "invalid_authoritative_subject_inventory")
    subjects = sorted(subjects)
    if apply:
        require(isinstance(plan_sha256, str) and re.fullmatch(r"[a-f0-9]{64}", plan_sha256),
                "reviewed_plan_sha256_required")
    transaction = database.write(maintenance=True) if apply else read_transaction(database)
    with transaction as db:
        models = (Record, Login, Flow, CoreIdentityBinding, AuditHead, Audit)
        require(all(inspect(db.connection()).has_table(m.__tablename__) for m in models), "schema_migration_required")
        if apply and database.engine.dialect.name == "postgresql":
            names = ", ".join("public." + m.__tablename__ for m in models)
            db.execute(text("LOCK TABLE " + names + " IN SHARE ROW EXCLUSIVE MODE"))
        # Flows predate authority bindings and have no tenant/issuer column.
        # This operator procedure supports one Core organization per database.
        for model in (Record, Login, CoreIdentityBinding, AuditHead, Audit):
            require(db.scalar(select(model.tenant_id).where(model.tenant_id != expected_tenant).limit(1)) is None,
                    "foreign_deployment_tenant")
        require(db.get(AuditHead, expected_tenant) is not None, "tenant_audit_head_missing")
        mappings, missing = snapshot(db, expected_tenant, old_issuer, new_issuer, subjects)
        plan = {"version": 1, "tenant_id": expected_tenant, "old_issuer": old_issuer,
                "new_issuer": new_issuer, "authoritative_subjects": subjects, "mappings": mappings}
        digest = hashlib.sha256(canonical(plan).encode()).hexdigest()
        if apply:
            require(plan_sha256 == digest, "reviewed_plan_changed")
        receipts = []
        for event in db.scalars(select(Audit).where(Audit.tenant_id == expected_tenant, Audit.event_type == EVENT)):
            payload = json.loads(event.payload)
            if payload.get("old_issuer") == old_issuer and payload.get("new_issuer") == new_issuer:
                receipts.append(payload)
        require(len(receipts) <= 1, "competing_migration_receipts")
        if receipts:
            require(receipts[0].get("plan_sha256") == digest and not missing, "migration_receipt_mismatch")
        result = {"status": "already_migrated" if receipts else "applied" if apply else "dry_run",
                  "plan_sha256": digest, "tenant_id": expected_tenant, "mappings": mappings,
                  "bindings_to_add": len(missing) if not receipts else 0,
                  "sessions_cleared": 0, "oauth_flows_cleared": 0}
        if apply and not receipts:
            for issuer, subject, user_id in missing:
                db.add(CoreIdentityBinding(tenant_id=expected_tenant, issuer=issuer, subject=subject, user_id=user_id))
            db.flush()
            result["sessions_cleared"] = db.execute(delete(Login).where(Login.tenant_id == expected_tenant)).rowcount
            # Flows predate authority bindings and contain no tenant/issuer column.
            result["oauth_flows_cleared"] = db.execute(delete(Flow)).rowcount
            audit(db, expected_tenant, "deployment-migration", EVENT, {
                "old_issuer": old_issuer, "new_issuer": new_issuer,
                "plan_sha256": digest, "identity_count": len(mappings)})
            after, remaining = snapshot(db, expected_tenant, old_issuer, new_issuer, subjects)
            require(after == mappings and not remaining, "post_migration_mapping_changed")
        return result


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--old-issuer", required=True)
    parser.add_argument("--new-issuer", required=True)
    parser.add_argument("--expected-tenant-id", required=True)
    parser.add_argument("--subjects-file", required=True)
    parser.add_argument("--apply", action="store_true")
    parser.add_argument("--plan-sha256")
    args = parser.parse_args(argv)
    database = None
    try:
        from .config import Settings
        settings = Settings.load()
        path = Path(args.subjects_file)
        require(path.stat().st_size <= 500000, "subject_inventory_too_large")
        inventory = json.loads(path.read_text())
        require(isinstance(inventory, dict) and set(inventory) == {"tenant_id", "subjects"} and
                inventory["tenant_id"] == args.expected_tenant_id, "foreign_subject_inventory")
        database = Database(settings)
        require(database.engine.dialect.name == "postgresql", "operator_postgresql_required")
        result = backfill(database, settings, old_issuer=args.old_issuer, new_issuer=args.new_issuer,
                          expected_tenant=args.expected_tenant_id, subjects=inventory["subjects"],
                          apply=args.apply, plan_sha256=args.plan_sha256)
        print(json.dumps(result, sort_keys=True))
        return 0
    except Exception as error:
        # Driver/config exceptions can include DSNs or secret-bearing statements.
        code = str(error) if isinstance(error, MigrationBlocked) else "runtime_or_database_failure"
        print(json.dumps({"status": "blocked", "code": code}))
        return 2
    finally:
        if database is not None:
            database.engine.dispose()


if __name__ == "__main__":
    raise SystemExit(main())
