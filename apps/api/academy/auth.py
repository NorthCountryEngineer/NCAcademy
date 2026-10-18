"""Independent Core adapter with server-side sessions and fresh authority checks."""
import base64
import hashlib
import hmac
import json
import secrets
import time
from http.cookiejar import CookieJar, DefaultCookiePolicy
from urllib.parse import urlencode
from uuid import UUID

import httpx
import jwt
from cryptography.fernet import Fernet, InvalidToken
from fastapi import HTTPException, Request
from fastapi.responses import RedirectResponse
from sqlalchemy import select

from .db import Flow, Login, Record, audit, canonical
from .core_identity import IdentityConflict, resolve_user, subject_owner

COOKIE = "academy_session"
FLOW_COOKIE = "academy_core_flow"
CALLBACK = "/api/auth/core/callback"


class NoIdentityCookies(DefaultCookiePolicy):
    def set_ok(self, cookie, request):
        return False

    def return_ok(self, cookie, request):
        return False


class Auth:
    def __init__(self, settings, database):
        self.settings, self.database = settings, database
        self.cipher = Fernet(base64.urlsafe_b64encode(hashlib.sha256((settings.session_secret + ":academy-vault-v1").encode()).digest()))
        self.http = httpx.Client(cookies=CookieJar(policy=NoIdentityCookies()), follow_redirects=False, trust_env=False, timeout=httpx.Timeout(10, connect=3, pool=1), limits=httpx.Limits(max_connections=16, max_keepalive_connections=8))

    def seal(self, value):
        return self.cipher.encrypt(canonical(value).encode()).decode()

    def unseal(self, value):
        try:
            result = json.loads(self.cipher.decrypt(value.encode()))
            if not isinstance(result, dict):
                raise ValueError()
            return result
        except (ValueError, InvalidToken):
            raise HTTPException(401, "Session unavailable; sign in again") from None

    def core(self, path, data=None, confidential=False):
        kwargs = {"data": data} if data is not None else {}
        if confidential:
            from urllib.parse import quote
            kwargs["auth"] = (quote(self.settings.core_resource_client_id, safe=""), quote(self.settings.core_resource_client_secret, safe=""))
        return self._core_document("POST" if data is not None else "GET", path, kwargs)

    def core_agent(self, path, data, purpose=""):
        # A receiving service's workload credential is separate from browser
        # OAuth and resource-client Basic authentication. Never forward cookies.
        if not self.settings.allow_agent_teachers or not self.settings.core_workload_token:
            raise HTTPException(403, "Agent teacher access is not enabled")
        headers = {"Authorization": "Bearer " + self.settings.core_workload_token}
        if purpose:
            headers["X-Core-Purpose"] = purpose
        return self._core_document("POST", path, {"json": data, "headers": headers})

    def _core_document(self, method, path, kwargs):
        try:
            # Identity transport never accepts, retains or sends upstream cookies.
            self.http.cookies.clear()
            started = time.monotonic()
            with self.http.stream(method, self.settings.core_issuer + path, **kwargs) as response:
                body = bytearray()
                for chunk in response.iter_bytes():
                    body.extend(chunk)
                    if len(body) > 262144 or time.monotonic() - started > 20:
                        raise ValueError()
                self.http.cookies.clear()
                if response.status_code != 200:
                    raise ValueError()
                value = json.loads(body) if body else {}
                if not isinstance(value, dict):
                    raise ValueError()
                return value
        except (httpx.HTTPError, ValueError):
            raise HTTPException(503, "Core identity service unavailable") from None

    def discovery(self):
        value = self.core("/.well-known/openid-configuration")
        for key, suffix in {"issuer": "", "authorization_endpoint": "/oauth/authorize/", "token_endpoint": "/oauth/token/", "jwks_uri": "/oauth/jwks/"}.items():
            if value.get(key) != self.settings.core_issuer + suffix:
                raise HTTPException(503, "Core discovery differs from trusted registration")

    def binding(self, claims):
        for key, value in {"organization_id": self.settings.core_organization_id, "service_id": self.settings.core_service_id, "deployment_id": self.settings.core_deployment_id, "environment": self.settings.core_environment}.items():
            if claims.get(key) != value:
                raise HTTPException(401, "Foreign Core authority")
        try:
            if str(UUID(claims.get("sub"))) != claims["sub"]:
                raise ValueError()
        except (ValueError, TypeError, AttributeError):
            raise HTTPException(401, "Invalid Core identity") from None

    def introspect(self, token, subject):
        claims = self.core("/oauth/introspect/", {"token": token}, confidential=True)
        if claims.get("active") is not True:
            raise HTTPException(401, "Core session expired or revoked; sign in again")
        self.binding(claims)
        if claims.get("iss") != self.settings.core_issuer or claims.get("aud") != "urn:core:service:" + self.settings.core_service_id or claims.get("client_id") != self.settings.core_browser_client_id or claims.get("token_type") != "Bearer" or claims.get("sub") != subject or type(claims.get("exp")) is not int or claims["exp"] <= time.time() or not isinstance(claims.get("scope"), str) or "authorization" not in claims["scope"].split():
            raise HTTPException(401, "Core authorization binding invalid")
        if not isinstance(claims.get("permissions"), list) or any(not isinstance(item, str) for item in claims["permissions"]):
            raise HTTPException(503, "Malformed Core authorization")
        if "academy-use" not in claims["permissions"]:
            raise HTTPException(403, "Core academy-use permission required")
        return claims

    def id_token(self, raw, nonce):
        try:
            if not isinstance(raw, str) or len(raw) > 32768:
                raise ValueError()
            header = jwt.get_unverified_header(raw)
            if header.get("alg") != "RS256":
                raise ValueError()
            keys = self.core("/oauth/jwks/").get("keys")
            if not isinstance(keys, list) or len(keys) > 32:
                raise ValueError()
            keys = [k for k in keys if isinstance(k, dict) and k.get("kid") == header.get("kid") and k.get("kty") == "RSA" and k.get("use", "sig") == "sig" and k.get("alg", "RS256") == "RS256"]
            if len(keys) != 1:
                raise ValueError()
            claims = jwt.decode(raw, jwt.PyJWK.from_dict(keys[0], algorithm="RS256").key, algorithms=["RS256"], audience=self.settings.core_browser_client_id, issuer=self.settings.core_issuer, options={"require": ["exp", "iat", "iss", "aud", "sub", "nonce"]})
            if claims.get("aud") != self.settings.core_browser_client_id or claims.get("azp", self.settings.core_browser_client_id) != self.settings.core_browser_client_id or not hmac.compare_digest(claims["nonce"], nonce):
                raise ValueError()
            self.binding(claims)
            return claims
        except (jwt.PyJWTError, ValueError, TypeError, KeyError):
            raise HTTPException(401, "Invalid Core identity token") from None

    def actor(self, request: Request):
        # An invalid machine credential must never fall back to a human cookie.
        if request.headers.get("authorization") is not None or request.headers.get("x-academy-agent-placement") is not None:
            if request.cookies.get(COOKIE) or request.cookies.get(FLOW_COOKIE):
                raise HTTPException(401, "Use one authenticated identity per request")
            from .agent_auth import actor
            return actor(self, request)
        return self.human_actor(request)

    def human_actor(self, request: Request):
        sid = request.cookies.get(COOKIE, "")
        with self.database.factory() as db:
            login = db.get(Login, sid)
            if not login or login.expires <= time.time():
                raise HTTPException(401, "Sign in required")
            snapshot = (login.user_id, login.tenant_id, login.token_cipher, login.csrf)
        role, permissions = None, []
        if self.settings.auth_mode == "core":
            tokens = self.unseal(snapshot[2])
            if tokens.get("expires", 0) <= time.time():
                raise HTTPException(401, "Session expired; sign in with Core again")
            claims = self.introspect(tokens["access_token"], tokens["subject"])
            role = "admin" if "admin" in claims["permissions"] else None
            permissions = claims["permissions"]
        with self.database.factory() as db:
            current = db.get(Login, sid)
            user = db.get(Record, snapshot[0])
            if not current or (current.user_id, current.tenant_id, current.token_cipher, current.csrf) != snapshot or current.expires <= time.time() or current.tenant_id != self.settings.tenant_id or not user or user.tenant_id != current.tenant_id or user.kind != "user":
                raise HTTPException(401, "Session changed; sign in again")
            if self.settings.auth_mode == "core":
                try:
                    bound = subject_owner(db, self.settings.tenant_id, self.settings.core_issuer, tokens["subject"])
                    if bound.id != user.id:
                        raise IdentityConflict("session_identity_binding_changed")
                except IdentityConflict:
                    raise HTTPException(401, "Sign in with Core again.") from None
            data = user.value
            if data.get("disabled"):
                raise HTTPException(403, "Membership disabled")
            # Core admin is live authority. Teaching assignments are separately
            # administered app enrollment, never inferred from a role claim.
            if self.settings.auth_mode == "core":
                role = role or ("school" if "academy-school" in permissions else "teacher" if "academy-teach" in permissions else "student")
            else:
                role = "teacher" if data["role"] == "instructor" else data["role"]
            return {"id": user.id, "name": data["name"], "role": role, "principal_kind": "human", "tenant_id": current.tenant_id, "csrf_token": current.csrf, "login_id": sid, "permissions": permissions, "can_audit": role in {"admin", "school"} or (self.settings.auth_mode == "local" and role == "teacher") or "academy-audit" in permissions}

    def authorize_resource(self, request, identity, permission, resource_type, resource_id):
        """A school's local resource assignment never substitutes for Core ACLs."""
        if identity.get("principal_kind") == "agent":
            from .agent_auth import authorize_resource
            return authorize_resource(self, request, identity, permission, resource_type, resource_id)
        if self.settings.auth_mode != "core":
            raise HTTPException(403, "Core resources are unavailable in the local demonstration")
        with self.database.factory() as db:
            login = db.get(Login, identity.get("login_id", ""))
            if not login or login.user_id != identity["id"] or login.tenant_id != self.settings.tenant_id or login.expires <= time.time():
                raise HTTPException(401, "Sign in required")
            tokens = self.unseal(login.token_cipher)
        claims = self.introspect(tokens["access_token"], tokens["subject"])
        if permission not in claims["permissions"]:
            raise HTTPException(403, "Core resource permission required")
        value = self.core("/api/v1/authorize/", {"token": tokens["access_token"], "permission": permission, "organization_id": self.settings.core_organization_id, "resource_type": resource_type, "resource_id": resource_id}, confidential=True)
        if value.get("allowed") is not True or value.get("subject") != tokens["subject"]:
            raise HTTPException(403, "Core resource access denied")
        return value

    def register(self, app):
        @app.get("/api/auth/config")
        def config():
            users = []
            if self.settings.auth_mode == "local":
                with self.database.factory() as db:
                    users = [{"id": r.id, "name": r.value["name"], "role": r.value["role"]} for r in db.scalars(select(Record).where(Record.tenant_id == self.settings.tenant_id, Record.kind == "user"))]
            return {"mode": self.settings.auth_mode, "demo_users": users, "login_url": "/api/auth/core"}

        @app.post("/api/auth/local")
        def local(request: Request, body: dict):
            if self.settings.auth_mode != "local":
                raise HTTPException(404)
            self.check_origin(request)
            with self.database.write() as db:
                user = db.get(Record, body.get("user_id", ""))
                if not user or user.kind != "user" or user.tenant_id != self.settings.tenant_id:
                    raise HTTPException(403, "Unknown local demo user")
                old = db.get(Login, request.cookies.get(COOKIE, ""))
                if old:
                    db.delete(old)
                sid = secrets.token_urlsafe(48)
                db.add(Login(id=sid, user_id=user.id, tenant_id=user.tenant_id, csrf=secrets.token_urlsafe(32), expires=int(time.time()) + 28800))
                audit(db, user.tenant_id, user.id, "auth.local_login", {})
            response = RedirectResponse("/api/auth/me", status_code=303)
            response.set_cookie(COOKIE, sid, httponly=True, secure=False, samesite="lax", max_age=28800)
            return response

        @app.get("/api/auth/me")
        def me(request: Request):
            return {k: v for k, v in self.actor(request).items() if k != "login_id"}

        @app.post("/api/auth/logout")
        def logout(request: Request):
            # Local logout remains available during Core outage/revocation.
            if request.headers.get("authorization") is not None or request.headers.get("x-academy-agent-placement") is not None:
                raise HTTPException(401, "Agent credentials do not create browser sessions")
            with self.database.factory() as db:
                login = db.get(Login, request.cookies.get(COOKIE, ""))
                if not login:
                    raise HTTPException(401, "Sign in required")
                actor = {"id": login.user_id, "tenant_id": login.tenant_id, "login_id": login.id, "csrf_token": login.csrf}
            self.csrf(request, actor)
            tokens = None
            with self.database.write() as db:
                login = db.get(Login, actor["login_id"])
                if login.token_cipher:
                    tokens = self.unseal(login.token_cipher)
                db.delete(login)
                audit(db, actor["tenant_id"], actor["id"], "auth.logout", {})
            if tokens:
                try:
                    self.core("/oauth/revoke/", {"token": tokens["refresh_token"], "client_id": self.settings.core_browser_client_id})
                except HTTPException:
                    pass
            response = RedirectResponse("/api/auth/config", status_code=303)
            response.delete_cookie(COOKIE)
            return response

        @app.get("/api/auth/core")
        def begin():
            if self.settings.auth_mode != "core":
                raise HTTPException(404)
            self.discovery()
            state, nonce, verifier = (secrets.token_urlsafe(32) for _ in range(3))
            with self.database.write() as db:
                db.add(Flow(state_hash=hashlib.sha256(state.encode()).hexdigest(), cipher=self.seal({"nonce": nonce, "verifier": verifier}), expires=int(time.time()) + 600))
            challenge = base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest()).decode().rstrip("=")
            response = RedirectResponse(self.settings.core_issuer + "/oauth/authorize/?" + urlencode({"response_type": "code", "client_id": self.settings.core_browser_client_id, "redirect_uri": self.settings.app_url + CALLBACK, "scope": "openid profile email authorization", "state": state, "nonce": nonce, "code_challenge": challenge, "code_challenge_method": "S256"}))
            response.set_cookie(FLOW_COOKIE, state, secure=True, httponly=True, samesite="lax", max_age=600, path="/api/auth/core")
            return response

        @app.get(CALLBACK)
        def callback(request: Request, code: str = "", state: str = ""):
            if self.settings.auth_mode != "core":
                raise HTTPException(404)
            expected = request.cookies.get(FLOW_COOKIE, "")
            if not code or len(code) > 4096 or not state or len(state) > 200 or not hmac.compare_digest(state, expected):
                raise HTTPException(403, "Core sign-in state mismatch")
            with self.database.write() as db:
                flow = db.get(Flow, hashlib.sha256(state.encode()).hexdigest())
                if not flow or flow.expires <= time.time():
                    raise HTTPException(403, "Core sign-in expired or already consumed")
                decoded = self.unseal(flow.cipher)
                db.delete(flow)
            tokens = self.core("/oauth/token/", {"grant_type": "authorization_code", "client_id": self.settings.core_browser_client_id, "code": code, "redirect_uri": self.settings.app_url + CALLBACK, "code_verifier": decoded["verifier"]})
            profile = self.id_token(tokens.get("id_token"), decoded["nonce"])
            if not isinstance(tokens.get("access_token"), str) or not isinstance(tokens.get("refresh_token"), str) or tokens.get("token_type", "").lower() != "bearer":
                raise HTTPException(401, "Incomplete Core tokens")
            claims = self.introspect(tokens["access_token"], profile["sub"])
            sid = secrets.token_urlsafe(48)
            with self.database.write() as db:
                try:
                    user = resolve_user(db, self.settings.tenant_id, self.settings.core_issuer, claims["sub"], profile.get("name"))
                except IdentityConflict:
                    raise HTTPException(403, "Core sign-in is unavailable. Contact an administrator.") from None
                user_id = user.id
                db.add(Login(id=sid, user_id=user_id, tenant_id=self.settings.tenant_id, csrf=secrets.token_urlsafe(32), token_cipher=self.seal({"access_token": tokens["access_token"], "refresh_token": tokens["refresh_token"], "subject": claims["sub"], "expires": claims["exp"]}), expires=min(int(time.time()) + 28800, claims["exp"])))
                audit(db, self.settings.tenant_id, user_id, "auth.core_login", {})
            response = RedirectResponse(self.settings.app_url + "/", status_code=303)
            response.set_cookie(COOKIE, sid, secure=True, httponly=True, samesite="lax", max_age=min(28800, claims["exp"] - int(time.time())))
            response.delete_cookie(FLOW_COOKIE, path="/api/auth/core")
            return response

    def check_origin(self, request):
        if request.headers.get("origin") != self.settings.app_url:
            raise HTTPException(403, "Application Origin required")

    def csrf(self, request, actor):
        if actor.get("principal_kind") == "agent":
            # Only an already-authenticated machine actor can take this path.
            if not getattr(request.state, "academy_agent_token", None) or request.cookies.get(COOKIE):
                raise HTTPException(401, "Agent authorization required")
            return
        self.check_origin(request)
        if not hmac.compare_digest(request.headers.get("x-csrf-token", ""), actor["csrf_token"]):
            raise HTTPException(403, "CSRF token required")
