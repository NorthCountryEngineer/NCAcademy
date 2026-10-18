"""Core contract tests use real RSA signatures and a fully mocked authority."""
import base64
import hashlib
import json
import time
from urllib.parse import parse_qs, urlsplit

import jwt
import pytest
from cryptography.hazmat.primitives.asymmetric import rsa
from fastapi import HTTPException
from fastapi.testclient import TestClient
from sqlalchemy import select

from academy.auth import COOKIE
from academy.config import Settings
from academy.db import Flow, Login, Record
from academy.main import create_app
from academy.migrate import migrate


@pytest.fixture
def core(tmp_path):
    settings = Settings("sqlite:///" + str(tmp_path / "core.db"), "core", "https://academy.example.test", "test-core-bootstrap-must-be-at-least-32", False, False, core_issuer="https://core.example.test", core_browser_client_id="academy-browser", core_resource_client_id="academy-resource", core_resource_client_secret="never-a-live-secret", core_organization_id="11111111-1111-4111-8111-111111111111", core_service_id="22222222-2222-4222-8222-222222222222", core_deployment_id="33333333-3333-4333-8333-333333333333")
    migrate(settings)
    app = create_app(settings)
    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    jwk = jwt.algorithms.RSAAlgorithm.to_jwk(key.public_key(), as_dict=True)
    jwk.update(kid="unit-test-key", alg="RS256", use="sig")
    state = {"permissions": ["academy-use"], "active": True, "outage": False, "nonce": "", "calls": [], "overrides": {}}
    subject = "44444444-4444-4444-8444-444444444444"
    claims = {"iss": settings.core_issuer, "sub": subject, "aud": "urn:core:service:" + settings.core_service_id, "client_id": settings.core_browser_client_id, "organization_id": settings.core_organization_id, "service_id": settings.core_service_id, "deployment_id": settings.core_deployment_id, "environment": "production", "scope": "openid profile authorization", "token_type": "Bearer", "exp": int(time.time()) + 3600}
    def fake(path, data=None, confidential=False):
        state["calls"].append((path, data, confidential))
        current_claims = {**claims, "iss": settings.core_issuer}
        if state["outage"]:
            raise HTTPException(503, "Mock authority unavailable")
        if path == "/.well-known/openid-configuration":
            return {"issuer": settings.core_issuer, "authorization_endpoint": settings.core_issuer + "/oauth/authorize/", "token_endpoint": settings.core_issuer + "/oauth/token/", "jwks_uri": settings.core_issuer + "/oauth/jwks/"}
        if path == "/oauth/token/":
            assert data["grant_type"] == "authorization_code"
            profile = {**current_claims, "aud": settings.core_browser_client_id, "iat": int(time.time()), "nonce": state["nonce"], "name": "Synthetic Core learner"}
            return {"access_token": "synthetic-access-token", "refresh_token": "synthetic-refresh-token", "token_type": "Bearer", "id_token": jwt.encode(profile, key, algorithm="RS256", headers={"kid": "unit-test-key"})}
        if path == "/oauth/jwks/":
            return {"keys": [jwk]}
        if path == "/oauth/introspect/":
            assert confidential is True and data == {"token": "synthetic-access-token"}
            return {**current_claims, "active": state["active"], "permissions": state["permissions"], **state["overrides"]}
        if path == "/oauth/revoke/":
            return {}
        raise AssertionError("Unexpected authority endpoint")
    app.state.auth.core = fake
    with TestClient(app, base_url=settings.app_url) as browser:
        yield app, browser, state


def begin(core):
    app, client, state = core
    response = client.get("/api/auth/core", follow_redirects=False)
    assert response.status_code == 307
    query = parse_qs(urlsplit(response.headers["location"]).query)
    state["nonce"] = query["nonce"][0]
    return query


def sign_in(core):
    app, client, state = core
    query = begin(core)
    response = client.get("/api/auth/core/callback", params={"code": "synthetic-code", "state": query["state"][0]}, follow_redirects=False)
    assert response.status_code == 303, response.text
    return client.get("/api/auth/me").json()


def test_pkce_signature_cookie_binding_and_encrypted_session(core):
    app, client, state = core
    query = begin(core)
    assert query["code_challenge_method"] == ["S256"]
    assert query["redirect_uri"] == [app.state.settings.app_url + "/api/auth/core/callback"]
    with app.state.database.factory() as db:
        flow = db.get(Flow, hashlib.sha256(query["state"][0].encode()).hexdigest())
        stored = app.state.auth.unseal(flow.cipher)
        assert query["code_challenge"][0] == base64.urlsafe_b64encode(hashlib.sha256(stored["verifier"].encode()).digest()).decode().rstrip("=")
        assert stored["verifier"] not in flow.cipher
    callback = {"code": "synthetic-code", "state": query["state"][0]}
    response = client.get("/api/auth/core/callback", params=callback, follow_redirects=False)
    assert response.status_code == 303
    assert "HttpOnly" in response.headers["set-cookie"] and "Secure" in response.headers["set-cookie"]
    me = client.get("/api/auth/me").json()
    assert me["role"] == "student" and me["tenant_id"] == app.state.settings.core_organization_id
    with app.state.database.factory() as db:
        login = db.scalar(select(Login))
        assert "synthetic-access-token" not in login.token_cipher
        assert "synthetic-refresh-token" not in login.token_cipher
        assert login.user_id != "44444444-4444-4444-8444-444444444444"
    assert client.get("/api/auth/core/callback", params=callback).status_code == 403


def test_fresh_introspection_permissions_revocation_and_outage(core):
    app, client, state = core
    sign_in(core)
    before = sum(path == "/oauth/introspect/" for path, *_ in state["calls"])
    assert client.get("/api/courses").status_code == 200
    assert client.get("/api/courses").status_code == 200
    after = sum(path == "/oauth/introspect/" for path, *_ in state["calls"])
    assert after - before == 2
    state["permissions"] = ["academy-use", "academy-teach", "academy-audit"]
    me = client.get("/api/auth/me").json()
    assert me["role"] == "instructor" and me["can_audit"] is True
    assert client.get("/api/admin/settings").status_code == 403
    state["permissions"].append("admin")
    assert client.get("/api/admin/settings").status_code == 200
    state["permissions"] = ["academy-use"]
    assert client.get("/api/admin/settings").status_code == 403
    state["active"] = False
    assert client.get("/api/courses").status_code == 401
    state["active"], state["outage"] = True, True
    assert client.get("/api/courses").status_code == 503


@pytest.mark.parametrize("overrides", [
    {"organization_id": "55555555-5555-4555-8555-555555555555"},
    {"service_id": "55555555-5555-4555-8555-555555555555"},
    {"deployment_id": "55555555-5555-4555-8555-555555555555"},
    {"environment": "staging"}, {"aud": "urn:foreign"}, {"client_id": "foreign"},
    {"sub": "55555555-5555-4555-8555-555555555555"}, {"exp": 1}, {"scope": "openid"},
])
def test_foreign_or_expired_core_claims_fail_closed(core, overrides):
    app, client, state = core
    sign_in(core)
    state["overrides"] = overrides
    assert client.get("/api/courses").status_code == 401


def test_missing_permission_denied_and_local_login_disabled(core):
    app, client, state = core
    sign_in(core)
    state["permissions"] = ["admin"]
    assert client.get("/api/courses").status_code == 403
    assert client.post("/api/auth/local", json={"user_id": "demo-admin"}).status_code == 404
    assert client.get("/api/auth/config").json()["demo_users"] == []


def test_state_nonce_and_single_use_callback(core):
    app, client, state = core
    query = begin(core)
    assert client.get("/api/auth/core/callback", params={"code": "x", "state": "wrong"}).status_code == 403
    state["nonce"] = "incorrect-nonce"
    params = {"code": "x", "state": query["state"][0]}
    assert client.get("/api/auth/core/callback", params=params).status_code == 401
    assert client.get("/api/auth/core/callback", params=params).status_code == 403


def test_logout_during_core_outage_still_deletes_local_session(core):
    app, client, state = core
    me = sign_in(core)
    state["outage"] = True
    response = client.post("/api/auth/logout", headers={"Origin": app.state.settings.app_url, "X-CSRF-Token": me["csrf_token"]}, follow_redirects=False)
    assert response.status_code == 303
    assert client.get("/api/auth/me").status_code == 401
    with app.state.database.factory() as db:
        assert db.scalar(select(Login)) is None


def test_configuration_rejects_network_demo_and_live_demo(monkeypatch):
    monkeypatch.setenv("AUTH_MODE", "local")
    monkeypatch.setenv("APP_URL", "https://public.example.test")
    with pytest.raises(ValueError):
        Settings.load()
    monkeypatch.setenv("APP_URL", "http://localhost:8090")
    monkeypatch.setenv("ALLOW_LIVE_MODELS", "true")
    with pytest.raises(ValueError):
        Settings.load()
