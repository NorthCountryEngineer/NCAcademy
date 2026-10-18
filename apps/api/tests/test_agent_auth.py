"""Core agent identity is separate from human OAuth and local school approval."""
import json
import time

import httpx
import pytest
from fastapi import HTTPException
from fastapi.testclient import TestClient
from sqlalchemy import select
from starlette.requests import Request

from academy.auth import Auth, COOKIE
from academy.config import Settings
from academy.db import Audit, CoreIdentityBinding, Database, Login, Record, RecordScope, canonical
from academy.main import create_app
from academy.migrate import migrate

AGENT = '44444444-4444-4444-8444-444444444444'
BINDING = '55555555-5555-4555-8555-555555555555'
CONSUMER = '66666666-6666-4666-8666-666666666666'
TOKEN = 'ca_synthetic_agent_token_never_live'
WORKLOAD = 'cw_' + 'synthetic-receiver-only-' * 3
PLACEMENT = 'unit-agent-placement'
SCHOOL = 'unit-agent-school'


def incoming(*, token=TOKEN, placement=PLACEMENT, extra=None):
    headers = {'Authorization': 'Bearer ' + token, 'X-Academy-Agent-Placement': placement}
    headers.update(extra or {})
    return Request({'type': 'http', 'method': 'GET', 'path': '/api/courses', 'headers': [(k.lower().encode(), v.encode()) for k, v in headers.items()]})


@pytest.fixture
def machine(tmp_path):
    settings = Settings('sqlite:///' + str(tmp_path / 'agent.db'), 'core', 'https://academy.example.test', 'synthetic-agent-bootstrap-at-least-thirty-two', False, False,
        core_issuer='https://core.example.test', core_browser_client_id='browser', core_resource_client_id='receiver', core_resource_client_secret='synthetic-oauth-resource-secret',
        core_organization_id='11111111-1111-4111-8111-111111111111', core_service_id='22222222-2222-4222-8222-222222222222', core_deployment_id='33333333-3333-4333-8333-333333333333',
        allow_agent_teachers=True, core_workload_token=WORKLOAD)
    migrate(settings)
    database = Database(settings)
    with database.write() as db:
        db.add(Record(id=SCHOOL, tenant_id=settings.tenant_id, kind='school', data=canonical({'name': 'Synthetic school', 'core_resource_id': 'registered-school-unit'})))
        db.add(Record(id=PLACEMENT, tenant_id=settings.tenant_id, kind='agent_placement', parent_id=SCHOOL, owner_id='agent:' + AGENT, data=canonical({
            'name': 'Synthetic AI teacher', 'school_id': SCHOOL, 'core_agent_id': AGENT, 'core_binding_id': BINDING, 'core_consumer_service_id': CONSUMER,
            'environment': 'production', 'task_id': None, 'core_resource_type': 'academy-school', 'core_resource_id': 'registered-school-unit', 'active': True, 'purpose': 'teaching'})))
    auth = Auth(settings, database)
    state = {'overrides': {}, 'calls': []}
    def core_agent(path, data, purpose=''):
        state['calls'].append((path, data, purpose))
        return {'active': True, 'principal_kind': 'agent', 'iss': settings.core_issuer, 'aud': 'urn:core:service:' + settings.core_service_id,
            'organization_id': settings.tenant_id, 'agent_id': AGENT, 'sub': 'urn:core:agent:' + AGENT, 'binding_id': BINDING, 'consumer_service_id': CONSUMER,
            'environment': 'production', 'task_id': None, 'exp': int(time.time()) + 900, 'roles': ['approved-teacher'], 'permissions': ['academy-use', 'academy-teach', 'academy-audit'], **state['overrides']}
    auth.core_agent = core_agent
    yield auth, state
    auth.http.close()
    database.engine.dispose()


def test_agent_has_fixed_teacher_identity_without_human_sessions_or_aliases(machine):
    auth, state = machine
    request = incoming()
    identity = auth.actor(request)
    assert identity['id'] == 'agent:' + AGENT and identity['principal_kind'] == 'agent'
    assert identity['role'] == 'teacher' and identity['school_id'] == SCHOOL and identity['can_audit'] is True
    assert TOKEN not in json.dumps(identity) and WORKLOAD not in json.dumps(identity)
    assert identity['csrf_token'] == '' and identity['login_id'] == ''
    auth.csrf(request, identity)  # Authenticated Bearer machine writes do not borrow browser CSRF.
    assert state['calls'] == [('/api/platform/v1/agent-credentials/introspect/', {'token': TOKEN, 'environment': 'production', 'task_id': None,
        'permission': 'academy-teach', 'resource_type': 'academy-school', 'resource_id': 'registered-school-unit'}, 'teaching')]
    with auth.database.factory() as db:
        assert db.scalar(select(Login)) is None and db.scalar(select(CoreIdentityBinding)) is None


@pytest.mark.parametrize('override', [
    {'iss': 'https://foreign.example.test'}, {'aud': 'urn:foreign'}, {'organization_id': CONSUMER},
    {'principal_kind': 'human'}, {'agent_id': CONSUMER}, {'sub': AGENT}, {'binding_id': CONSUMER},
    {'consumer_service_id': BINDING}, {'environment': 'staging'}, {'task_id': BINDING},
    {'exp': 0}, {'exp': True}, {'exp': int(time.time()) + 7200}, {'roles': 'teacher'}, {'permissions': 'academy-teach'},
    {'permissions': ['academy-use', 'academy-teach', 'admin']}, {'roles': [42]},
])
def test_agent_claim_bindings_fail_closed(machine, override):
    auth, state = machine
    state['overrides'] = override
    with pytest.raises(HTTPException) as caught:auth.actor(incoming())
    assert caught.value.status_code == 401
    assert TOKEN not in str(caught.value.detail)


@pytest.mark.parametrize('override', [{'active': False}, {'permissions': ['academy-use']}, {'permissions': ['academy-teach']}])
def test_current_core_revocation_and_permission_loss_are_not_cached(machine, override):
    auth, state = machine
    assert auth.actor(incoming())['role'] == 'teacher'
    state['overrides'] = override
    with pytest.raises(HTTPException) as caught:auth.actor(incoming())
    assert caught.value.status_code == 403 and len(state['calls']) == 2


@pytest.mark.parametrize('extra', [{'Cookie': COOKIE + '=synthetic-human-session'}, {'Cookie': 'academy_core_flow=synthetic-flow'}])
def test_agent_and_human_cookie_never_fall_back_to_human_authority(machine, extra):
    auth, state = machine
    with pytest.raises(HTTPException) as caught:auth.actor(incoming(extra=extra))
    assert caught.value.status_code == 401 and state['calls'] == []


@pytest.mark.parametrize('header', ['', 'Basic synthetic', 'Bearer ', 'Bearer a b'])
def test_malformed_authorization_cannot_become_a_browser_session(machine, header):
    auth, state = machine
    with pytest.raises(HTTPException) as caught:auth.actor(incoming(extra={'Authorization': header}))
    assert caught.value.status_code == 401 and state['calls'] == []


def test_agent_feature_disabled_by_default_and_no_unregistered_school(machine):
    auth, state = machine
    auth.settings.allow_agent_teachers = False
    with pytest.raises(HTTPException) as caught:auth.actor(incoming())
    assert caught.value.status_code == 403 and state['calls'] == []
    auth.settings.allow_agent_teachers = True
    with auth.database.write() as db:
        row = db.get(Record, SCHOOL); row.set({**row.value, 'core_resource_id': ''})
    with pytest.raises(HTTPException) as caught:auth.actor(incoming())
    assert caught.value.status_code == 403 and state['calls'] == []


def test_local_placement_revocation_during_core_request_denies_admission(machine):
    auth, state = machine
    original = auth.core_agent
    def revoke(*args):
        result = original(*args)
        with auth.database.write() as db:
            row = db.get(Record, PLACEMENT); row.set({**row.value, 'active': False})
        return result
    auth.core_agent = revoke
    with pytest.raises(HTTPException) as caught:auth.actor(incoming())
    assert caught.value.status_code == 403


def test_request_headers_cannot_select_another_task_or_environment(machine):
    auth, state = machine
    auth.actor(incoming(extra={'X-Core-Task': BINDING, 'X-Core-Environment': 'staging', 'X-Core-Purpose': 'admin'}))
    assert state['calls'][0][1]['task_id'] is None
    assert state['calls'][0][1]['environment'] == 'production'
    assert state['calls'][0][2] == 'teaching'


def test_agent_resource_reads_require_an_additional_live_resource_decision(machine):
    auth, state = machine
    request = incoming(); identity = auth.actor(request)
    auth.authorize_resource(request, identity, 'academy-use', 'reference', 'registered-resource-unit')
    assert len(state['calls']) == 2
    assert state['calls'][-1][1]['resource_id'] == 'registered-resource-unit'
    state['overrides'] = {'active': False}
    with pytest.raises(HTTPException) as caught:auth.authorize_resource(request, identity, 'academy-use', 'reference', 'registered-resource-unit')
    assert caught.value.status_code == 403


def test_agent_transport_uses_workload_bearer_json_and_never_identity_cookies(machine):
    auth, _ = machine
    calls = []
    def transport(request):
        calls.append(request)
        assert request.headers['Authorization'] == 'Bearer ' + WORKLOAD
        assert request.headers['Content-Type'] == 'application/json'
        assert request.headers['X-Core-Purpose'] == 'teaching'
        assert 'cookie' not in request.headers
        assert json.loads(request.content)['token'] == TOKEN
        return httpx.Response(200, json={'active': False}, headers={'Set-Cookie': 'upstream=untrusted; Path=/'})
    auth.http.close()
    auth.http = httpx.Client(transport=httpx.MockTransport(transport), cookies=auth.http.cookies, follow_redirects=False, trust_env=False)
    Auth.core_agent(auth, '/api/platform/v1/agent-credentials/introspect/', {'token': TOKEN}, 'teaching')
    Auth.core_agent(auth, '/api/platform/v1/agent-credentials/introspect/', {'token': TOKEN}, 'teaching')
    assert len(calls) == 2 and len(auth.http.cookies) == 0


@pytest.mark.parametrize('response', [httpx.Response(302, headers={'Location': 'https://other.example.test'}), httpx.Response(500, text='synthetic-private-diagnostic'), httpx.Response(200, content=b'x' * 262145)])
def test_agent_transport_refuses_redirect_errors_and_oversized_bodies_without_leaking(machine, response):
    auth, _ = machine
    calls=[]
    def transport(request):calls.append(request);return response
    auth.http.close();auth.http=httpx.Client(transport=httpx.MockTransport(transport),follow_redirects=False,trust_env=False)
    with pytest.raises(HTTPException) as caught:Auth.core_agent(auth, '/api/platform/v1/agent-credentials/introspect/', {'token': TOKEN})
    assert caught.value.status_code == 503 and len(calls) == 1
    assert 'synthetic-private-diagnostic' not in str(caught.value.detail)


def test_real_agent_http_path_scopes_classes_stages_writes_and_rechecks_revocation(machine):
    auth, state = machine
    with auth.database.write() as db:
        for course_id, owner in [('assigned-class', 'agent:' + AGENT), ('other-class', 'other-human')]:
            db.add(Record(id=course_id, tenant_id=auth.settings.tenant_id, kind='course', owner_id=owner, data=canonical({'title': course_id})))
            db.add(RecordScope(record_id=course_id, tenant_id=auth.settings.tenant_id, school_id=SCHOOL))
    app = create_app(auth.settings)
    app.state.auth.core_agent = auth.core_agent
    headers = {'Authorization': 'Bearer ' + TOKEN, 'X-Academy-Agent-Placement': PLACEMENT}
    with TestClient(app, base_url=auth.settings.app_url) as client:
        me = client.get('/api/auth/me', headers=headers)
        assert me.status_code == 200 and me.json()['principal_kind'] == 'agent'
        schools = client.get('/api/schools', headers=headers)
        assert schools.status_code == 200 and [s['id'] for s in schools.json()] == [SCHOOL]
        courses = client.get('/api/courses', params={'school_id': SCHOOL}, headers=headers)
        assert courses.status_code == 200 and [c['id'] for c in courses.json()] == ['assigned-class']
        assert client.get('/api/courses/other-class', headers=headers).status_code in {403, 404}
        proposal = client.post('/api/courses', json={'title': 'Agent proposal', 'school_id': SCHOOL}, headers=headers)
        assert proposal.status_code == 202 and proposal.json()['publication'] == 'human_approval_required'
        assert client.get('/api/admin/settings', headers=headers).status_code == 403
        assert client.get('/api/admin/audit/export', headers=headers).status_code == 403
        state['overrides'] = {'active': False}
        assert client.get('/api/courses', params={'school_id': SCHOOL}, headers=headers).status_code == 403
    with auth.database.factory() as db:
        records = list(db.scalars(select(Record)))
        assert len([r for r in records if r.kind == 'course']) == 2
        assert len([r for r in records if r.kind == 'change_request']) == 1
        evidence = json.dumps([r.value for r in records]) + json.dumps([r.payload for r in db.scalars(select(Audit))])
        assert TOKEN not in evidence and WORKLOAD not in evidence
        assert db.scalar(select(Login)) is None and db.scalar(select(CoreIdentityBinding)) is None


@pytest.fixture
def core_environment(monkeypatch):
    values = {'AUTH_MODE': 'core', 'APP_URL': 'https://academy.example.test',
        'DATABASE_URL': 'postgresql+psycopg://synthetic:synthetic@localhost/academy',
        'SESSION_SECRET': 'synthetic-config-secret-at-least-thirty-two',
        'CORE_ISSUER': 'https://core.example.test', 'CORE_BROWSER_CLIENT_ID': 'browser',
        'CORE_RESOURCE_CLIENT_ID': 'receiver', 'CORE_RESOURCE_CLIENT_SECRET': 'synthetic-oauth-secret',
        'CORE_ORGANIZATION_ID': '11111111-1111-4111-8111-111111111111',
        'CORE_SERVICE_ID': '22222222-2222-4222-8222-222222222222',
        'CORE_DEPLOYMENT_ID': '33333333-3333-4333-8333-333333333333'}
    for key in ['CORE_RESOURCE_CREDENTIALS_JSON', 'CORE_WORKLOAD_TOKEN', 'CORE_WORKLOAD_TOKEN_FILE', 'ALLOW_AGENT_TEACHERS', 'ALLOW_LIVE_MODELS']:
        monkeypatch.delenv(key, raising=False)
    for key, value in values.items():monkeypatch.setenv(key, value)


def test_agent_config_is_opt_in_and_disabled_mode_never_reads_workload_file(core_environment, monkeypatch):
    monkeypatch.setenv('CORE_WORKLOAD_TOKEN_FILE', '/does/not/exist/synthetic-secret')
    settings = Settings.load()
    assert settings.allow_agent_teachers is False and settings.core_workload_token == ''
    monkeypatch.setenv('ALLOW_AGENT_TEACHERS', 'true')
    with pytest.raises(ValueError, match='credential file unavailable'):Settings.load()


def test_agent_config_uses_separate_hidden_workload_secret(core_environment, monkeypatch, tmp_path):
    monkeypatch.setenv('ALLOW_AGENT_TEACHERS', 'true')
    with pytest.raises(ValueError, match='separate Core workload'):Settings.load()
    secret_path = tmp_path / 'synthetic-workload-token'
    secret_path.write_text(WORKLOAD + '\n')
    monkeypatch.setenv('CORE_WORKLOAD_TOKEN_FILE', str(secret_path))
    settings = Settings.load()
    assert settings.core_workload_token == WORKLOAD and settings.core_resource_client_secret == 'synthetic-oauth-secret'
    assert WORKLOAD not in repr(settings)
    monkeypatch.setenv('CORE_WORKLOAD_TOKEN', WORKLOAD)
    with pytest.raises(ValueError, match='one Core workload credential'):Settings.load()


@pytest.mark.parametrize('flag', ['enabled', '1', ' true'])
def test_agent_config_rejects_ambiguous_flags(core_environment, monkeypatch, flag):
    monkeypatch.setenv('ALLOW_AGENT_TEACHERS', flag)
    with pytest.raises(ValueError, match='must be true or false'):Settings.load()


def test_local_mode_refuses_agent_credentials(core_environment, monkeypatch):
    monkeypatch.setenv('AUTH_MODE', 'local');monkeypatch.setenv('APP_URL', 'http://localhost:8090')
    monkeypatch.setenv('ALLOW_AGENT_TEACHERS', 'true');monkeypatch.setenv('CORE_WORKLOAD_TOKEN', WORKLOAD)
    with pytest.raises(ValueError, match='disabled live models/agent credentials'):Settings.load()
