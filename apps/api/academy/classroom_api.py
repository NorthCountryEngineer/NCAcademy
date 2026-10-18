"""A personal, read-only Google Classroom companion; no delegated school authority."""
import base64
import hashlib
import hmac
import secrets
import time
from urllib.parse import quote

from fastapi import Depends, HTTPException, Request, Response
from fastapi.responses import RedirectResponse
from sqlalchemy import select

from .classroom import ACCOUNT, CALLBACK, FLOW_COOKIE, LINK, OPERATION, account, clear_links, configuration_binding, context, descriptor, event, key, link, new_operation, own_record, revalidate, same_actor
from .classroom_schemas import ClassroomConnectInput, ClassroomEmptyInput, ClassroomLinkInput
from .db import Flow, Record, canonical, now, uid
from .google_classroom_transport import GoogleClassroomFailure
from .lesson_api import course_for_student
from .permissions import bind_scope, scope_id


def register_classroom_api(app, database, auth, settings, actor, audit_actor):
    def transport():
        if not getattr(settings, "google_classroom_enabled", False):
            raise HTTPException(503, "Google Classroom is not enabled for this deployment")
        return app.state.google_classroom_transport

    def fresh(request, identity):
        return same_actor(identity, auth.actor(request))

    def request_fingerprint(material):
        # Keyed and domain-separated: even low-entropy Google identifiers cannot
        # be recovered by an offline dictionary attack on immutable evidence.
        return hmac.new(settings.session_secret.encode(), ("academy-classroom-request-v1:" + canonical(material)).encode(), hashlib.sha256).hexdigest()

    def finish(db, identity, row, status, **metadata):
        value = row.value
        if value["status"] != "pending":
            raise HTTPException(409, "Classroom operation is already complete")
        value.update(status=status, completed_at=now(), **metadata)
        row.set(value)
        audit_actor(db, identity, "classroom.operation_" + status, {"operation_id": row.id, "operation": value["operation"], "course_id": value.get("course_id"), "school_id": value.get("school_id"), "status": status, **metadata})

    def marker(db, identity, row, step):
        value = row.value
        if value["status"] != "pending" or len(value["markers"]) >= 32:
            raise HTTPException(409, "Classroom operation cannot send another request")
        value["markers"].append({"step": step, "created_at": now()})
        row.set(value)
        audit_actor(db, identity, "classroom.send_marked", {"operation_id": row.id, "course_id": value.get("course_id"), "school_id": value.get("school_id"), "operation": value["operation"], "step": step, "ordinal": len(value["markers"])})

    def unseal_account(row):
        value = auth.unseal(row.value["cipher"])
        if not all(isinstance(value.get(name), str) and value[name] for name in ("access_token", "refresh_token", "subject")):
            raise HTTPException(409, "Reconnect your Google account")
        return value

    def enrollment(access_token, remote_id, call):
        page_token, seen = None, set()
        for _ in range(10):
            result = call("courses", lambda: transport().courses(access_token, page_token))
            for item in result["courses"]:
                if item["id"] in seen:
                    raise GoogleClassroomFailure("invalid_response")
                seen.add(item["id"])
                if item["id"] == remote_id:
                    return item
            page_token = result.get("next_page_token")
            if not page_token:
                raise HTTPException(403, "Current student enrollment in that Google Classroom course is required")
        raise HTTPException(503, "Google course enrollment could not be verified within the request limit")

    def execute(request, identity, course_id, operation_name, task, *, linked=False, expected_version=None, identifier=None, idempotency_key=None, request_material=None, publish=None):
        accepted = fresh(request, identity)
        with database.write() as db:
            snapshot, row = context(db, accepted, course_id, settings, linked=linked, expected_version=expected_version)
            if row.value.get("refresh_operation_id"):
                raise HTTPException(409, "A Google credential refresh is unresolved; reconnect before another request")
            prior = own_record(db, accepted, identifier, OPERATION, course_id, snapshot["school_id"]) if identifier else None
            if prior:
                if not hmac.compare_digest(prior.value.get("request_fingerprint", ""), request_fingerprint(request_material)):
                    raise HTTPException(409, "Classroom request key was used for a different action")
                if prior.value["status"] == "released" and prior.value.get("link_version") == (link(db, accepted, course_id, snapshot["school_id"]).value.get("version") if link(db, accepted, course_id, snapshot["school_id"]) else None):
                    return descriptor(db, accepted, course_id, settings)
                raise HTTPException(409, "This Classroom request is already recorded; start a new explicit request")
            operation = new_operation(db, accepted, course_id, snapshot["school_id"], operation_name, identifier=identifier, idempotency_key=idempotency_key)
            operation_id = operation.id
            if request_material is not None:
                operation.set({**operation.value, "request_fingerprint": request_fingerprint(request_material)})
            credentials = unseal_account(row)
            expires = row.value["expires"]
            remote_id = auth.unseal(link(db, accepted, course_id, snapshot["school_id"]).value["cipher"])["google_course_id"] if linked else None
            if expires <= time.time() + 30:
                row.set({**row.value, "refresh_operation_id": operation_id})

        def call(step, action):
            current = fresh(request, identity)
            with database.write() as db:
                revalidate(db, current, course_id, settings, snapshot)
                marker(db, current, db.get(Record, operation_id), step)
            return action()

        result, failure, reconnect, uncertain, refreshed_credentials = None, None, False, False, False
        try:
            if expires <= time.time() + 30:
                refreshed = call("refresh", lambda: transport().refresh(credentials["refresh_token"]))
                profile = call("identity", lambda: transport().userinfo(refreshed["access_token"]))
                if profile["sub"] != credentials["subject"]:
                    raise GoogleClassroomFailure("identity_changed", reconnect=True)
                current = fresh(request, identity)
                with database.write() as db:
                    row = revalidate(db, current, course_id, settings, snapshot)
                    credentials = {"access_token": refreshed["access_token"], "refresh_token": refreshed.get("refresh_token") or credentials["refresh_token"], "subject": credentials["subject"]}
                    value = row.value
                    value.pop("refresh_operation_id", None)
                    value.update(cipher=auth.seal(credentials), expires=int(time.time()) + refreshed["expires_in"], token_revision=uid())
                    row.set(value)
                    snapshot["token_revision"] = value["token_revision"]
                    audit_actor(db, current, "classroom.credentials_refreshed", {"operation_id": operation_id, "course_id": course_id})
                    refreshed_credentials = True
            result = task(credentials["access_token"], remote_id, call)
            accepted = fresh(request, identity)
        except GoogleClassroomFailure as exc:
            failure = HTTPException(409 if exc.reconnect else exc.status_code, "Reconnect your Google account." if exc.reconnect else "Google Classroom is unavailable. Request a fresh read when you are ready.")
            reconnect, uncertain = exc.reconnect, exc.uncertain
        except HTTPException as exc:
            failure = exc
        except Exception:
            failure = HTTPException(503, "Google Classroom result could not be verified")
            uncertain = True
        with database.write() as db:
            operation = db.get(Record, operation_id)
            if not failure:
                try:
                    revalidate(db, accepted, course_id, settings, snapshot)
                except HTTPException as exc:
                    failure = exc
            if failure:
                row = account(db, identity, snapshot["school_id"])
                # A refresh can rotate a credential even when its response is
                # lost. A fresh user connection is required; never repeat it.
                reconnect = reconnect or bool(not refreshed_credentials and row and row.value.get("refresh_operation_id") == operation_id)
                if reconnect and row and row.value.get("version") == snapshot["account_version"] and row.value.get("token_revision") == snapshot["token_revision"]:
                    row.set({"version": uid(), "token_revision": uid(), "status": "reauth", "cipher": "", "expires": 0, "revoked": False, "revocation_uncertain": True})
                    clear_links(db, identity, snapshot["school_id"])
                finish(db, identity, operation, "withheld", uncertain=uncertain, reconnect=reconnect)
            else:
                metadata = publish(db, accepted, snapshot, result) if publish else {"count": len(result.get("courses", result.get("assignments", [])))}
                finish(db, accepted, operation, "released", **metadata)
                if publish:
                    result = descriptor(db, accepted, course_id, settings)
        credentials = None
        if failure:
            raise failure
        return result

    @app.get("/api/courses/{course_id}/classroom")
    def status(course_id: str, identity=Depends(actor)):
        with database.factory() as db:
            return descriptor(db, identity, course_id, settings)

    @app.post("/api/courses/{course_id}/classroom/connect")
    def connect(course_id: str, body: ClassroomConnectInput, request: Request, response: Response, identity=Depends(actor)):
        transport()
        current = fresh(request, identity)
        identifier = key(OPERATION, identity["tenant_id"], identity["id"], course_id, "connect", body.idempotency_key)
        with database.write() as db:
            _, school_id = course_for_student(db, current, course_id, settings)
            row = account(db, current, school_id, create=True)
            if row.value.get("revoke_operation_id"):
                raise HTTPException(409, "Google disconnection is still in progress; check its outcome before reconnecting")
            existing = own_record(db, current, identifier, OPERATION, course_id, school_id)
            flow = db.get(Flow, existing.value.get("flow_hash", "")) if existing else None
            if existing:
                if existing.value["status"] != "pending" or not flow or flow.expires <= time.time():
                    raise HTTPException(409, "This Google connection request is complete or expired; start a new request")
                data = auth.unseal(flow.cipher)
                if data["login_id"] != identity["login_id"] or data["account_version"] != row.value["version"] or data.get("configuration") != configuration_binding(settings):
                    raise HTTPException(409, "Google connection request changed; start a new request")
            else:
                state, verifier = secrets.token_urlsafe(32), secrets.token_urlsafe(48)
                operation = new_operation(db, current, course_id, school_id, "connect", identifier=identifier, idempotency_key=body.idempotency_key)
                flow_hash = hashlib.sha256(state.encode()).hexdigest()
                data = {"provider": "google_classroom", "state": state, "verifier": verifier, "tenant_id": identity["tenant_id"], "user_id": identity["id"], "login_id": identity["login_id"], "school_id": school_id, "course_id": course_id, "account_version": row.value["version"], "configuration": configuration_binding(settings), "operation_id": operation.id}
                db.add(Flow(state_hash=flow_hash, cipher=auth.seal(data), expires=int(time.time()) + 600))
                operation.set({**operation.value, "flow_hash": flow_hash})
                audit_actor(db, current, "classroom.connect_requested", {"operation_id": operation.id, "course_id": course_id, "school_id": school_id})
        challenge = base64.urlsafe_b64encode(hashlib.sha256(data["verifier"].encode()).digest()).decode().rstrip("=")
        authorization_url = transport().authorize_url(data["state"], challenge)
        response.set_cookie(FLOW_COOKIE, data["state"], secure=settings.app_url.startswith("https://"), httponly=True, samesite="lax", max_age=600, path=CALLBACK)
        return {"authorization_url": authorization_url}

    @app.get(CALLBACK)
    def callback(request: Request, code: str = "", state: str = "", error: str = ""):
        data, operation_id, result, outcome = None, None, None, "error"
        try:
            expected = request.cookies.get(FLOW_COOKIE, "")
            if not state or len(state) > 200 or not expected or not hmac.compare_digest(state, expected) or len(code) > 4096:
                raise HTTPException(403, "Google sign-in state mismatch")
            identity = auth.actor(request)
            if identity.get("principal_kind", "human") != "human" or identity.get("auth_provider") == "academy":
                raise HTTPException(403)
            cancelled = False
            with database.write() as db:
                flow = db.get(Flow, hashlib.sha256(state.encode()).hexdigest())
                if not flow or flow.expires <= time.time():
                    raise HTTPException(403, "Google sign-in expired or already consumed")
                data = auth.unseal(flow.cipher)
                if data.get("provider") != "google_classroom" or data.get("configuration") != configuration_binding(settings) or any(data.get(name) != identity.get(field) for name, field in (("tenant_id", "tenant_id"), ("user_id", "id"), ("login_id", "login_id"))):
                    raise HTTPException(403, "Google sign-in identity changed")
                _, school_id = course_for_student(db, identity, data["course_id"], settings)
                row = account(db, identity, school_id)
                if school_id != data["school_id"] or not row or row.value["version"] != data["account_version"]:
                    raise HTTPException(403, "Google connection changed")
                operation_id = data["operation_id"]
                operation = own_record(db, identity, operation_id, OPERATION, data["course_id"], school_id)
                if not operation or operation.value["status"] != "pending":
                    raise HTTPException(409)
                db.delete(flow)
                if error or not code:
                    finish(db, identity, operation, "withheld", uncertain=False, reconnect=False)
                    cancelled = True
                else:
                    marker(db, identity, operation, "exchange")
            if cancelled:
                raise HTTPException(403, "Google sign-in cancelled")
            tokens = transport().exchange(code, data["verifier"])
            current = fresh(request, identity)
            with database.write() as db:
                if not getattr(settings, "google_classroom_enabled", False) or data.get("configuration") != configuration_binding(settings):
                    raise HTTPException(503)
                _, school_id = course_for_student(db, current, data["course_id"], settings)
                row = account(db, current, school_id)
                if not row or row.value["version"] != data["account_version"]:
                    raise HTTPException(403)
                marker(db, current, db.get(Record, operation_id), "identity")
            profile = transport().userinfo(tokens["access_token"])
            if not tokens.get("refresh_token") or not profile.get("sub"):
                raise HTTPException(409, "Google offline authorization was incomplete")
            current = fresh(request, identity)
            with database.write() as db:
                if not getattr(settings, "google_classroom_enabled", False) or data.get("configuration") != configuration_binding(settings):
                    raise HTTPException(503)
                _, school_id = course_for_student(db, current, data["course_id"], settings)
                row = account(db, current, school_id)
                if not row or row.value["version"] != data["account_version"]:
                    raise HTTPException(403)
                row.set({"version": uid(), "token_revision": uid(), "status": "connected", "cipher": auth.seal({"access_token": tokens["access_token"], "refresh_token": tokens["refresh_token"], "subject": profile["sub"]}), "expires": int(time.time()) + tokens["expires_in"], "configuration": data["configuration"]})
                clear_links(db, current, school_id)
                finish(db, current, db.get(Record, operation_id), "released", count=1)
                event(db, current, data["course_id"], school_id, "connected", audit_actor, account_id=row.id)
            outcome = "connected"
        except Exception:
            if operation_id:
                with database.write() as db:
                    operation = db.get(Record, operation_id)
                    if operation and operation.value["status"] == "pending":
                        finish(db, identity, operation, "withheld", uncertain=bool(operation.value["markers"]), reconnect=False)
        finally:
            result = None
        destination = settings.app_url + "/?classroom_status=" + outcome
        if data and isinstance(data.get("course_id"), str):
            destination += "&classroom_course=" + quote(data["course_id"], safe="")
        response = RedirectResponse(destination, status_code=303)
        response.delete_cookie(FLOW_COOKIE, path=CALLBACK)
        return response

    @app.get("/api/courses/{course_id}/classroom/google-courses")
    def google_courses(course_id: str, request: Request, page_token: str | None = None, identity=Depends(actor)):
        return execute(request, identity, course_id, "courses_read", lambda token, _remote, call: call("courses", lambda: transport().courses(token, page_token)))

    @app.post("/api/courses/{course_id}/classroom/link")
    def link_course(course_id: str, body: ClassroomLinkInput, request: Request, identity=Depends(actor)):
        def publish(db, current, snapshot, _result):
            row = link(db, current, course_id, snapshot["school_id"])
            if not row:
                row = Record(id=key(LINK, current["tenant_id"], current["id"], course_id), tenant_id=current["tenant_id"], kind=LINK, parent_id=course_id, owner_id=current["id"])
                db.add(row)
                db.flush()
                bind_scope(db, row, snapshot["school_id"])
            row.set({"version": uid(), "account_version": snapshot["account_version"], "cipher": auth.seal({"google_course_id": body.google_course_id})})
            event(db, current, course_id, snapshot["school_id"], "linked", audit_actor, link_id=row.id, link_version=row.value["version"])
            return {"link_version": row.value["version"], "count": 1}
        return execute(request, identity, course_id, "link", lambda token, _remote, call: enrollment(token, body.google_course_id, call), expected_version=body.expected_connection_version, identifier=key(OPERATION, identity["tenant_id"], identity["id"], course_id, "link", body.idempotency_key), idempotency_key=body.idempotency_key, request_material=body.model_dump(), publish=publish)

    @app.post("/api/courses/{course_id}/classroom/unlink")
    def unlink_course(course_id: str, body: ClassroomEmptyInput, identity=Depends(actor)):
        with database.write() as db:
            _, school_id = course_for_student(db, identity, course_id, settings)
            row = link(db, identity, course_id, school_id)
            if row and row.value.get("cipher"):
                row.set({"version": uid(), "account_version": "", "cipher": ""})
                event(db, identity, course_id, school_id, "unlinked", audit_actor, link_id=row.id)
            return descriptor(db, identity, course_id, settings)

    @app.get("/api/courses/{course_id}/classroom/coursework")
    def coursework(course_id: str, request: Request, page_token: str | None = None, identity=Depends(actor)):
        def read(token, remote, call):
            course = enrollment(token, remote, call)
            result = call("coursework", lambda: transport().coursework(token, remote, page_token))
            return {**result, "course": course}
        return execute(request, identity, course_id, "coursework_read", read, linked=True)

    def connection_descriptor(row):
        value = row.value
        changed = bool(value.get("cipher")) and value.get("configuration") != configuration_binding(settings)
        return {"id": row.id, "school_id": row.parent_id, "connected": value.get("status") == "connected" and bool(value.get("cipher")) and not changed, "requires_reauth": value.get("status") == "reauth" or changed, "revoked": value.get("revoked", False), "uncertain": value.get("revocation_uncertain", False)}

    def own_account(db, identity, account_id):
        if identity.get("principal_kind", "human") != "human":
            raise HTTPException(403, "Human account ownership required")
        row = db.get(Record, account_id)
        if not row or row.kind != ACCOUNT or row.tenant_id != identity["tenant_id"] or row.owner_id != identity["id"] or scope_id(db, row) != row.parent_id:
            raise HTTPException(404, "Your Google connection was not found")
        return row

    def disconnect_account(account_id, request, identity, course_id=None):
        current = fresh(request, identity)
        token, operation_id = None, None
        with database.write() as db:
            row = own_account(db, current, account_id)
            school_id = row.parent_id
            previous = row.value
            if previous.get("revoke_operation_id"):
                result = descriptor(db, current, course_id, settings) if course_id else connection_descriptor(row)
                return {**result, "revoked": previous.get("revoked", False), "uncertain": True}
            had_credentials = bool(row.value.get("cipher"))
            if had_credentials and previous.get("configuration") == configuration_binding(settings):
                try:
                    token = unseal_account(row)["refresh_token"]
                except HTTPException:
                    # Corrupted/unavailable ciphertext must never trap a
                    # person's own local credentials or course mappings.
                    token = None
            revoked = False if had_credentials else previous.get("revoked", previous.get("status") == "disconnected")
            uncertain = True if had_credentials else previous.get("revocation_uncertain", False)
            value = {"version": uid(), "token_revision": uid(), "status": "disconnected", "cipher": "", "expires": 0, "revoked": revoked, "revocation_uncertain": uncertain}
            row.set(value)
            clear_links(db, current, school_id)
            event(db, current, course_id, school_id, "disconnected", audit_actor, account_id=row.id)
            if token and getattr(settings, "google_classroom_enabled", False):
                operation = new_operation(db, current, course_id, school_id, "revoke")
                operation_id = operation.id
                row.set({**row.value, "revoke_operation_id": operation_id})
                marker(db, current, operation, "revoke")
            account_version = row.value["version"]
        if token and operation_id:
            try:
                revoked = transport().revoke(token) is True
                uncertain = not revoked
            except Exception:
                uncertain = True
            with database.write() as db:
                finish(db, current, db.get(Record, operation_id), "released" if revoked else "withheld", uncertain=uncertain, count=int(revoked))
                row = own_account(db, current, account_id)
                if row.value["version"] == account_version:
                    value = row.value
                    value.pop("revoke_operation_id", None)
                    row.set({**value, "revoked": revoked, "revocation_uncertain": uncertain})
        current = fresh(request, identity)
        with database.factory() as db:
            row = own_account(db, current, account_id)
            result = descriptor(db, current, course_id, settings) if course_id else connection_descriptor(row)
        return {**result, "revoked": revoked, "uncertain": uncertain}

    @app.post("/api/courses/{course_id}/classroom/disconnect")
    def disconnect(course_id: str, body: ClassroomEmptyInput, request: Request, identity=Depends(actor)):
        with database.write() as db:
            _, school_id = course_for_student(db, identity, course_id, settings)
            row = account(db, identity, school_id, create=True)
            account_id = row.id
        return disconnect_account(account_id, request, identity, course_id)

    @app.get("/api/me/google-classroom/connections")
    def own_connections(identity=Depends(actor)):
        if identity.get("principal_kind", "human") != "human":
            raise HTTPException(403, "Human account ownership required")
        with database.factory() as db:
            rows = db.scalars(select(Record).where(Record.tenant_id == identity["tenant_id"], Record.kind == ACCOUNT, Record.owner_id == identity["id"]).order_by(Record.created_at).limit(200))
            return {"connections": [connection_descriptor(own_account(db, identity, row.id)) for row in rows]}

    @app.post("/api/me/google-classroom/connections/{account_id}/disconnect")
    def disconnect_owned(account_id: str, body: ClassroomEmptyInput, request: Request, identity=Depends(actor)):
        return disconnect_account(account_id, request, identity)
