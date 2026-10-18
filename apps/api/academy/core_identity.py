"""Explicit Core authority bindings preserve application ownership across issuers."""
import hashlib
from uuid import UUID

from sqlalchemy import select

from .db import CoreIdentityBinding, Record, canonical


class IdentityConflict(ValueError):
    pass


def canonical_subject(subject):
    try:
        return isinstance(subject, str) and str(UUID(subject)) == subject
    except (ValueError, TypeError, AttributeError):
        return False


def legacy_user_id(issuer, subject):
    return hashlib.sha256((issuer + "#" + subject).encode()).hexdigest()


def checked_user(db, tenant, user_id, *, allow_disabled=False):
    user = db.get(Record, user_id)
    if not user or user.tenant_id != tenant or user.kind != "user":
        raise IdentityConflict("identity_target_unavailable")
    if not allow_disabled and user.value.get("disabled"):
        raise IdentityConflict("identity_target_disabled")
    return user


def subject_owner(db, tenant, issuer, subject):
    if not canonical_subject(subject):
        raise IdentityConflict("invalid_core_subject")
    binding = db.get(CoreIdentityBinding, (tenant, issuer, subject))
    if not binding:
        raise IdentityConflict("identity_binding_unavailable")
    return checked_user(db, tenant, binding.user_id)


def resolve_user(db, tenant, issuer, subject, name):
    """Resolve only after validated Core signature, nonce, tenant and live claims.

    Same-issuer legacy hashes are the sole implicit compatibility lookup.
    A different issuer requires an operator binding, never email/name linking.
    The caller holds Database.write's cross-process transaction lock.
    """
    if not canonical_subject(subject):
        raise IdentityConflict("invalid_core_subject")
    binding = db.get(CoreIdentityBinding, (tenant, issuer, subject))
    if binding:
        return checked_user(db, tenant, binding.user_id)
    user_id = legacy_user_id(issuer, subject)
    user = db.get(Record, user_id)
    if user:
        user = checked_user(db, tenant, user_id)
    else:
        existing_users = set(db.scalars(select(Record.id).where(
            Record.tenant_id == tenant, Record.kind == "user")))
        current_owners = set(db.scalars(select(CoreIdentityBinding.user_id).where(
            CoreIdentityBinding.tenant_id == tenant, CoreIdentityBinding.issuer == issuer)))
        if not existing_users <= current_owners:
            raise IdentityConflict("current_authority_backfill_required")
        user = Record(id=user_id, tenant_id=tenant, kind="user", data=canonical({
            "name": str(name or "Core member")[:200], "role": "student", "teaching": False}))
        db.add(user)
    competing = db.scalar(select(CoreIdentityBinding).where(
        CoreIdentityBinding.tenant_id == tenant, CoreIdentityBinding.issuer == issuer,
        CoreIdentityBinding.user_id == user_id))
    if competing:
        raise IdentityConflict("competing_core_subject")
    db.add(CoreIdentityBinding(tenant_id=tenant, issuer=issuer, subject=subject, user_id=user_id))
    return user
