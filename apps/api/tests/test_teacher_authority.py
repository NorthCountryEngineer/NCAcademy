"""Selected-teacher Core read authorization: no tokens, grants or impersonation."""
import time
from copy import deepcopy
from uuid import uuid4

import pytest
from fastapi import HTTPException
from sqlalchemy import select

from academy.db import Audit, Record, canonical
from academy.permissions import default_school_id
from academy.teacher_authority import PATH, authorize_teacher
from conftest import login


@pytest.fixture
def selected_teacher(app, client, monkeypatch):
    headers = login(client, "demo-admin")
    school = default_school_id(app.state.settings.tenant_id)
    assert client.put(f"/api/schools/{school}", json={"name": "Synthetic school", "core_resource_id": "registered-school"}, headers=headers).status_code == 200
    agent_id, binding_id, service_id, task_id = (str(uuid4()) for _ in range(4))
    response = client.post(f"/api/schools/{school}/agents", json={"name": "Synthetic teacher", "core_agent_id": agent_id, "core_binding_id": binding_id, "core_consumer_service_id": service_id, "environment": "production", "task_id": task_id, "core_resource_id": "registered-school", "purpose": "approved-teaching"}, headers=headers)
    assert response.status_code == 201, response.text
    placement = response.json()
    response = client.post("/api/courses", json={"title": "Environmental science", "school_id": school, "teacher_id": "agent:" + agent_id, "curriculum": {"state": "US-NY", "subject": "environmental_science", "grades": [10], "academic_year": "2026-2027"}}, headers=headers)
    assert response.status_code == 201, response.text
    course = response.json()
    corpus = client.get(f"/api/courses/{course['id']}/corpus").json()
    assert client.post(f"/api/courses/{course['id']}/corpus/approve", json={"corpus_id": corpus["id"], "snapshot_sha256": corpus["snapshot_sha256"]}, headers=headers).status_code == 200
    response = client.post(f"/api/schools/{school}/teachers", json={"kind": "ai", "name": "River", "agent_placement_id": placement["id"]}, headers=headers)
    assert response.status_code == 201, response.text
    profile = response.json()
    response = client.put(f"/api/schools/{school}/teachers/{profile['id']}/courses", json={"expected_version": profile["version"], "course_ids": [course["id"]]}, headers=headers)
    assert response.status_code == 200, response.text
    profile = response.json()
    response = client.post(f"/api/schools/{school}/teachers/{profile['id']}/approve", json={"expected_version": profile["version"]}, headers=headers)
    assert response.status_code == 200, response.text
    profile = response.json()
    # Synthetic local records retain their tenant while exercising the Core-only
    # adapter. No production settings, credentials, issuer or Core calls are used.
    settings = app.state.settings
    tenant = settings.tenant_id
    settings.core_organization_id = tenant
    settings.auth_mode = "core"
    settings.core_issuer = "https://core.synthetic.test"
    settings.core_service_id = service_id
    settings.core_workload_token = "synthetic-workload-token-never-live"
    settings.allow_agent_teachers = True
    clock = int(time.time())
    claims = {"active": True, "iss": settings.core_issuer, "aud": "urn:core:service:" + service_id,
              "sub": "urn:core:agent:" + agent_id, "principal_kind": "agent", "agent_id": agent_id,
              "organization_id": tenant, "consumer_service_id": service_id, "binding_id": binding_id,
              "environment": "production", "task_id": task_id, "permission": "academy-teach",
              "resource_type": "academy-school", "resource_id": "registered-school",
              "iat": clock, "exp": clock + 45, "roles": ["teacher"], "permissions": ["academy-use", "academy-teach"]}
    calls = []
    def remote(path, payload, purpose):
        calls.append((path, deepcopy(payload), purpose))
        return deepcopy(claims)
    monkeypatch.setattr(app.state.auth, "core_agent", remote)
    return {"auth": app.state.auth, "school": school, "profile": profile, "course": course, "placement": placement, "claims": claims, "calls": calls, "remote": remote}


def check(data):
    return authorize_teacher(data["auth"], data["profile"]["id"], data["school"], data["course"]["id"])


def test_selected_teacher_reads_own_exact_context_without_minting_or_disclosing_credentials(selected_teacher):
    data = selected_teacher
    database = data["auth"].database
    with database.factory() as db:
        before = [(row.id, row.data) for row in db.scalars(select(Record).order_by(Record.id))]
        audit_count = len(list(db.scalars(select(Audit))))
    evidence = check(data)
    assert evidence["active"] is True and evidence["binding_id"] == data["claims"]["binding_id"]
    assert evidence["authorization_method"] == "core-binding-read-only-v1"
    assert evidence["approved_hash"] == data["profile"]["approved_hash"]
    assert data["calls"] == [(PATH, {"binding_id": data["claims"]["binding_id"], "environment": "production", "task_id": data["claims"]["task_id"], "permission": "academy-teach", "resource_type": "academy-school", "resource_id": "registered-school"}, "approved-teaching")]
    assert "token" not in canonical(evidence) and "biography" not in evidence and "corpus" not in evidence
    with database.factory() as db:
        assert [(row.id, row.data) for row in db.scalars(select(Record).order_by(Record.id))] == before
        assert len(list(db.scalars(select(Audit)))) == audit_count


@pytest.mark.parametrize("key,value", [("active", False), ("principal_kind", "human"), ("iss", "https://foreign.test"), ("aud", "urn:core:service:foreign"), ("organization_id", "foreign"), ("consumer_service_id", "foreign"), ("agent_id", str(uuid4())), ("sub", "urn:core:agent:foreign"), ("binding_id", str(uuid4())), ("environment", "staging"), ("task_id", None), ("permission", "academy-audit"), ("resource_type", "other"), ("resource_id", "other-school"), ("exp", True), ("iat", True), ("exp", 1), ("roles", "teacher"), ("permissions", ["academy-use", "academy-teach", "admin"]), ("permissions", ["academy-teach"])])
def test_authority_claim_mismatches_fail_closed(selected_teacher, key, value):
    selected_teacher["claims"][key] = value
    with pytest.raises(HTTPException):
        check(selected_teacher)


def test_claim_lifetime_is_at_most_sixty_seconds_and_no_tokens_pass_through(selected_teacher):
    data = selected_teacher
    data["claims"]["exp"] = int(time.time()) + 61
    with pytest.raises(HTTPException):
        check(data)
    data["claims"]["exp"] = int(time.time()) + 30
    data["claims"].update(access_token="synthetic-unexpected-token", workload_token="synthetic-unexpected-key")
    evidence = check(data)
    assert "synthetic-unexpected" not in canonical(evidence)


@pytest.mark.parametrize("changed", ["profile", "placement", "course", "materials"])
def test_local_approval_or_class_changes_during_check_block(selected_teacher, monkeypatch, changed):
    data = selected_teacher
    def remote(*args):
        with data["auth"].database.write() as db:
            if changed == "profile":
                row = db.get(Record, data["profile"]["id"])
                row.set({**row.value, "status": "draft", "version": row.value["version"] + 1, "approved_revision_id": None})
            elif changed == "placement":
                row = db.get(Record, data["placement"]["id"])
                row.set({**row.value, "active": False})
            elif changed == "course":
                row = db.get(Record, data["course"]["id"])
                row.set({**row.value, "description": "Changed course purpose"})
            else:
                from academy.permissions import bind_scope
                row = Record(id="new-teaching-material", tenant_id=data["auth"].settings.tenant_id, kind="resource", parent_id=data["school"], owner_id="demo-admin", data=canonical({"kind": "text", "course_id": data["course"]["id"], "audience": "teachers", "name": "New lesson", "content": "New climate explanation"}))
                db.add(row)
                db.flush()
                bind_scope(db, row, data["school"])
        return deepcopy(data["claims"])
    monkeypatch.setattr(data["auth"], "core_agent", remote)
    with pytest.raises(HTTPException):
        check(data)


@pytest.mark.parametrize("change", ["gate", "credential", "unknown_profile", "foreign_course", "consumer"])
def test_unapproved_or_unavailable_local_context_never_calls_core(selected_teacher, change):
    data = selected_teacher
    if change == "gate":
        data["auth"].settings.allow_agent_teachers = False
    elif change == "credential":
        data["auth"].settings.core_workload_token = ""
    elif change == "unknown_profile":
        data["profile"]["id"] = "unknown"
    elif change == "foreign_course":
        data["course"]["id"] = "unknown"
    else:
        data["auth"].settings.core_service_id = str(uuid4())
    with pytest.raises(HTTPException):
        check(data)
    assert not data["calls"]


def test_missing_core_supplement_transport_denial_is_never_fallback_success(selected_teacher, monkeypatch):
    def unavailable(*_):
        raise HTTPException(503, "Core endpoint unavailable")
    monkeypatch.setattr(selected_teacher["auth"], "core_agent", unavailable)
    with pytest.raises(HTTPException) as denied:
        check(selected_teacher)
    assert denied.value.status_code == 503


def test_real_adapter_uses_workload_transport_without_teacher_or_browser_tokens(selected_teacher, monkeypatch):
    import json
    import httpx
    from academy.auth import Auth, NoIdentityCookies
    from http.cookiejar import CookieJar
    data = selected_teacher
    requests = []
    def respond(request):
        requests.append(request)
        assert str(request.url) == data["auth"].settings.core_issuer + PATH
        assert request.headers["authorization"] == "Bearer synthetic-workload-token-never-live"
        assert request.headers["x-core-purpose"] == "approved-teaching"
        assert "cookie" not in request.headers
        assert set(json.loads(request.content)) == {"binding_id", "environment", "task_id", "permission", "resource_type", "resource_id"}
        return httpx.Response(200, json=data["claims"], headers={"Set-Cookie": "unwanted=never-forward"})
    with httpx.Client(transport=httpx.MockTransport(respond), trust_env=False, cookies=CookieJar(policy=NoIdentityCookies())) as transport:
        monkeypatch.setattr(data["auth"], "http", transport)
        monkeypatch.setattr(data["auth"], "core_agent", Auth.core_agent.__get__(data["auth"], Auth))
        check(data)
        check(data)
        assert len(requests) == 2 and not list(transport.cookies.jar)
