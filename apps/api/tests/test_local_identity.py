"""Synthetic platform-only accounts: no external identity or provider access."""
import base64
import json
import time

import pytest
from fastapi import HTTPException
from fastapi.testclient import TestClient
from sqlalchemy import select

from academy.auth import COOKIE, Auth
from academy.config import Settings
from academy.db import Audit, Database, Login, Record, canonical
from academy.local_identity import LocalIdentity, LOGIN_IP_LIMIT, LOGIN_USERNAME_LIMIT, SCRYPT_N, decode_pii_key
from academy.main import create_app
from academy.migrate import migrate

PASSWORD = "a synthetic long password"
REPLACEMENT = "a different synthetic password"
USERNAME = "synthetic.learner"
USER_ID = "local-synthetic-student"
KEY = base64.urlsafe_b64encode(bytes(range(32))).decode()


def account(app, *, must_change=False):
    family, tenant = app.state.auth.family, app.state.settings.tenant_id
    with app.state.database.write() as db:
        db.add(Record(id=USER_ID, tenant_id=tenant, kind="user", data=canonical({"name": "Student tudent", "role": "student", "auth_provider": "academy", "disabled": False, "credential_version": 1, "must_change_password": must_change})))
        db.add(Record(id="local-account:" + USER_ID, tenant_id=tenant, kind="local_account", owner_id=USER_ID, data=canonical({"profile_cipher": family.encrypt({"username": USERNAME, "name": "Synthetic Sensitive Name", "preferred_name": "Synthetic Preferred", "date_of_birth": "2010-01-02", "email": "synthetic-minor@example.test", "notes": "Synthetic sensitive note"}, tenant, USER_ID, "profile"), "password_cipher": family.encrypt(family.password_hash(PASSWORD), tenant, USER_ID, "password"), "version": 1})))
        db.add(Record(id="local-username:" + family.username_key(tenant, USERNAME), tenant_id=tenant, kind="local_username", owner_id=USER_ID, data="{}"))


def sign_in(client, username=USERNAME, password=PASSWORD, *, origin="http://localhost:8090", follow_redirects=True):
    return client.post("/api/auth/password", json={"username": username, "password": password}, headers={"Origin": origin}, follow_redirects=follow_redirects)


def headers(response, origin="http://localhost:8090"):
    return {"Origin": origin, "X-CSRF-Token": response.json()["csrf_token"]}


def test_profile_and_verifier_encryption_random_nonce_bound_context(app):
    family, tenant = app.state.auth.family, app.state.settings.tenant_id
    payload = {"name": "Synthetic Sensitive Name"}
    first = family.encrypt(payload, tenant, USER_ID, "profile")
    second = family.encrypt(payload, tenant, USER_ID, "profile")
    assert first != second
    assert first["alg"] == "AES-256-GCM"
    assert family.decrypt(first, tenant, USER_ID, "profile") == payload
    for context in [(tenant + "-other", USER_ID, "profile"), (tenant, USER_ID + "-other", "profile"), (tenant, USER_ID, "password")]:
        with pytest.raises(HTTPException) as denied:
            family.decrypt(first, *context)
        assert denied.value.status_code == 503
    altered = {**first, "ciphertext": first["ciphertext"][:-5] + "AAAA="}
    with pytest.raises(HTTPException):
        family.decrypt(altered, tenant, USER_ID, "profile")


def test_envelope_accepts_bounded_nested_audio_scaffolding_but_rejects_oversize(app):
    from academy.local_identity import MAX_ENCRYPTED_PLAINTEXT
    family, tenant = app.state.auth.family, app.state.settings.tenant_id
    # Existing maximum 2 MiB audio can occupy about 3.73 MiB after its
    # base64/vault envelope. No provider is contacted to exercise that boundary.
    payload = {"audio_cipher": "A" * (4 * 1024 * 1024)}
    envelope = family.encrypt(payload, tenant, USER_ID, "record:synthetic-voice")
    assert family.decrypt(envelope, tenant, USER_ID, "record:synthetic-voice") == payload
    with pytest.raises(HTTPException) as denied:
        family.encrypt({"oversize": "A" * MAX_ENCRYPTED_PLAINTEXT}, tenant, USER_ID, "record:synthetic-voice")
    assert denied.value.status_code == 422


def test_strong_salted_password_verifier_and_no_composition_rules(app):
    family = app.state.auth.family
    first = family.password_hash("my learning journal")
    second = family.password_hash("my learning journal")
    assert first["n"] == SCRYPT_N == 131072
    assert (first["r"], first["p"]) == (8, 1)
    assert first["salt"] != second["salt"] and first["digest"] != second["digest"]
    assert family.password_verify("my learning journal", first)
    assert not family.password_verify("x" * 15, first)
    assert not family.password_verify("my learning journal", {**first, "n": 2})
    for invalid in ["x" * 14, "x" * 129, "bad\ud800password-long", " " * 15, "x" * 128, "123456789012345", "passwordpassword"]:
        with pytest.raises(HTTPException) as denied:
            family.password_hash(invalid)
        assert denied.value.status_code == 422


def test_username_index_tenant_bound_normalized_and_key_separated(app):
    family = app.state.auth.family
    key = family.username_key("tenant-a", "Synthetic.Learner")
    assert key == family.username_key("tenant-a", "synthetic.learner")
    assert key != family.username_key("tenant-b", "synthetic.learner")
    assert len(key) == 48 and "synthetic" not in key
    for invalid in ["ab", "space name", "a@b.test", "../", "x" * 65]:
        with pytest.raises(HTTPException):
            family.username_key("tenant-a", invalid)


def test_kdf_admission_is_bounded_without_reducing_crypto_cost(app):
    from academy.local_identity import _KDF_SLOTS
    assert _KDF_SLOTS.acquire(blocking=False)
    assert _KDF_SLOTS.acquire(blocking=False)
    try:
        with pytest.raises(HTTPException) as denied:
            app.state.auth.family.password_hash(PASSWORD)
        assert denied.value.status_code == 429
        assert denied.value.headers["Retry-After"] == "2"
        # Unknown accounts use the same bounded expensive path as real ones.
        with pytest.raises(HTTPException) as denied:
            app.state.auth.family.authenticate("absent.learner", PASSWORD, "192.0.2.2")
        assert denied.value.status_code == 429
    finally:
        _KDF_SLOTS.release()
        _KDF_SLOTS.release()
    verifier = app.state.auth.family.password_hash(PASSWORD)
    assert verifier["n"] == 131072
    assert app.state.auth.family.password_verify(PASSWORD, verifier)


def test_password_login_own_identity_and_at_rest_pii(app, client):
    account(app)
    response = sign_in(client, username=USERNAME.upper())
    assert response.status_code == 200, response.text
    me = response.json()
    assert me["name"] == "Synthetic Preferred"
    assert me["role"] == "student" and me["auth_provider"] == "academy"
    assert me["permissions"] == [] and me["can_audit"] is False
    with app.state.database.factory() as db:
        storage = " ".join(row.data for row in db.scalars(select(Record)))
        audit = " ".join(row.payload for row in db.scalars(select(Audit)))
        assert USERNAME not in storage and PASSWORD not in storage
        for raw in ["Synthetic Sensitive Name", "Synthetic Preferred", "2010-01-02", "synthetic-minor@example.test", "Synthetic sensitive note", PASSWORD]:
            assert raw not in storage and raw not in audit
        login = db.scalar(select(Login).where(Login.user_id == USER_ID))
        assert app.state.auth.unseal(login.token_cipher) == {"auth_provider": "academy", "credential_version": 1}
    assert response.headers["cache-control"] == "no-store"


def test_managed_accounts_cannot_use_demo_bypass_or_discovery(app, client):
    account(app)
    config = client.get("/api/auth/config").json()
    assert config["password_login"] is True
    assert USER_ID not in {item["id"] for item in config["demo_users"]}
    assert client.post("/api/auth/local", json={"user_id": USER_ID}, headers={"Origin": app.state.settings.app_url}).status_code == 403


def test_login_generic_failures_and_origin_checks(app, client):
    account(app)
    assert sign_in(client, origin="https://foreign.example.test").status_code == 403
    assert client.post("/api/auth/password", json={"username": USERNAME, "password": PASSWORD}).status_code == 403
    known = sign_in(client, password="incorrect password").json()
    absent = sign_in(client, username="absent.learner").json()
    malformed = sign_in(client, username="a@b.test").json()
    assert known == absent == malformed == {"detail": "Invalid username or password"}


def test_password_required_before_other_routes_and_rotation_revokes_others(app, client):
    account(app, must_change=True)
    original = sign_in(client)
    assert original.json()["must_change_password"] is True
    assert client.get("/api/courses").status_code == 403
    old_sid, old_csrf = client.cookies.get(COOKIE), original.json()["csrf_token"]
    second = TestClient(app, base_url=app.state.settings.app_url)
    assert sign_in(second).status_code == 200
    assert client.put("/api/me/password", json={"current_password": PASSWORD, "new_password": REPLACEMENT}, headers={"Origin": app.state.settings.app_url}).status_code == 403
    changed = client.put("/api/me/password", json={"current_password": PASSWORD, "new_password": REPLACEMENT}, headers=headers(original))
    assert changed.status_code == 200, changed.text
    assert changed.json()["must_change_password"] is False
    assert changed.json()["csrf_token"] != old_csrf and client.cookies.get(COOKIE) != old_sid
    assert second.get("/api/auth/me").status_code == 401
    assert sign_in(second, password=REPLACEMENT).status_code == 200
    assert sign_in(second, password=PASSWORD).status_code == 401


def test_credential_version_disable_and_expiry_checked_every_request(app, client):
    account(app)
    assert sign_in(client).status_code == 200
    sid = client.cookies.get(COOKIE)
    with app.state.database.write() as db:
        user = db.get(Record, USER_ID)
        user.set({**user.value, "credential_version": 2})
    assert client.get("/api/auth/me").status_code == 401
    assert sign_in(client).status_code == 200
    with app.state.database.write() as db:
        user = db.get(Record, USER_ID)
        user.set({**user.value, "disabled": True})
    assert client.get("/api/auth/me").status_code == 401
    with app.state.database.write() as db:
        user = db.get(Record, USER_ID)
        user.set({**user.value, "disabled": False})
    assert sign_in(client).status_code == 200
    with app.state.database.write() as db:
        db.get(Login, client.cookies.get(COOKIE)).expires = int(time.time()) - 1
    assert client.get("/api/auth/me").status_code == 401


def test_cross_instance_rate_limits_store_no_identifiers(app):
    first = app.state.auth.family
    second_db = Database(app.state.settings)
    second = LocalIdentity(app.state.settings, second_db)
    try:
        for number in range(LOGIN_USERNAME_LIMIT):
            (first if number % 2 else second).reserve_login("synthetic-private-username", "192.0.2.56")
        with pytest.raises(HTTPException) as denied:
            second.reserve_login("synthetic-private-username", "192.0.2.99")
        assert denied.value.status_code == 429
        for number in range(LOGIN_IP_LIMIT):
            first.reserve_login("another-private-name-" + str(number), "192.0.2.88")
        with pytest.raises(HTTPException) as denied:
            second.reserve_login("fresh-private-name", "192.0.2.88")
        assert denied.value.status_code == 429
        with app.state.database.factory() as db:
            records = list(db.scalars(select(Record).where(Record.kind == "local_login_limit")))
            contents = canonical([{key: getattr(row, key) for key in ("id", "data", "owner_id", "parent_id")} for row in records])
            assert "192.0.2." not in contents and "private-name" not in contents and "private-username" not in contents
    finally:
        second_db.engine.dispose()


def test_core_mode_local_password_sessions_never_call_core(tmp_path):
    settings = Settings("sqlite:///" + str(tmp_path / "core-local.db"), "core", "https://academy.example.test", "synthetic-core-secret-long-enough-12345", False, False, core_organization_id="synthetic-tenant", pii_encryption_key=KEY)
    migrate(settings)
    app = create_app(settings)
    def denied(*args, **kwargs):
        raise AssertionError("Managed local family sessions must not use Core")
    app.state.auth.core = denied
    account(app)
    with TestClient(app, base_url=settings.app_url) as client:
        response = sign_in(client, origin=settings.app_url, follow_redirects=False)
        assert response.status_code == 303
        assert "Secure" in response.headers["set-cookie"] and "HttpOnly" in response.headers["set-cookie"] and "SameSite=lax" in response.headers["set-cookie"]
        me = client.get("/api/auth/me")
        assert me.status_code == 200 and me.json()["auth_provider"] == "academy"
        with pytest.raises(HTTPException) as denied_resource:
            app.state.auth.authorize_resource(None, me.json(), "read", "document", "synthetic-resource")
        assert denied_resource.value.status_code == 403
        result = client.post("/api/auth/logout", headers=headers(me, settings.app_url))
        assert result.status_code == 200
        assert client.get("/api/auth/me").status_code == 401


def test_missing_production_pii_key_does_not_break_staff_but_blocks_family(tmp_path):
    settings = Settings("sqlite:///" + str(tmp_path / "missing-key.db"), "core", "https://academy.example.test", "synthetic-core-secret-long-enough-12345", False, False, core_organization_id="synthetic-tenant")
    migrate(settings)
    app = create_app(settings)
    with TestClient(app, base_url=settings.app_url) as client:
        assert client.get("/api/auth/config").json()["password_login"] is False
        result = sign_in(client, origin=settings.app_url)
        assert result.status_code == 503
    with pytest.raises(HTTPException) as denied:
        app.state.auth.family.encrypt({}, settings.tenant_id, USER_ID, "profile")
    assert denied.value.status_code == 503


def test_session_and_pii_keys_are_independent(app):
    app.state.settings.pii_encryption_key = KEY
    family = app.state.auth.family
    envelope = family.encrypt({"name": "Synthetic Private Name"}, "tenant", USER_ID, "profile")
    app.state.settings.session_secret = "different-session-secret-long-enough-1234"
    assert family.decrypt(envelope, "tenant", USER_ID, "profile") == {"name": "Synthetic Private Name"}
    app.state.settings.pii_encryption_key = base64.urlsafe_b64encode(b"x" * 32).decode()
    with pytest.raises(HTTPException):
        family.decrypt(envelope, "tenant", USER_ID, "profile")


def test_key_validation_and_write_only_configuration(monkeypatch, tmp_path):
    assert len(decode_pii_key(KEY)) == 32
    for invalid in ["x", base64.urlsafe_b64encode(b"x" * 31).decode(), "!" * 44, "A" * 44]:
        with pytest.raises(ValueError):
            decode_pii_key(invalid)
    path = tmp_path / "pii-key"
    path.write_text(KEY + "\n")
    monkeypatch.setenv("AUTH_MODE", "local")
    monkeypatch.setenv("APP_URL", "http://localhost:8090")
    monkeypatch.delenv("PII_ENCRYPTION_KEY", raising=False)
    monkeypatch.setenv("PII_ENCRYPTION_KEY_FILE", str(path))
    settings = Settings.load()
    assert settings.pii_encryption_key == KEY and KEY not in repr(settings)
    monkeypatch.setenv("PII_ENCRYPTION_KEY", KEY)
    with pytest.raises(ValueError, match="one platform"):
        Settings.load()
