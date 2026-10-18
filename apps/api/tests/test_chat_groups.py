"""Synthetic Core Chat integration: current school/class ACLs and exact receipts."""
import json
from uuid import uuid4

import pytest
from sqlalchemy import select

from academy.chat_groups import ATTEMPT, COMMAND, GROUP, MEMBER, RECEIPT, ensure_class_group, key
from academy.core_chat_transport import CoreChatFailure
from academy.db import Audit, Record, canonical
from academy.permissions import bind_scope, default_school_id, membership_id
from conftest import login


class FakeCoreChat:
    def __init__(self, app):
        self.settings = app.state.settings
        self.rooms, self.events, self.sends, self.creates = [], {}, [], []
        self.create_error = self.send_error = None
        self.on_messages = self.on_send = None

    def create_group(self, title, topic=''):
        self.creates.append((title, topic))
        identifier = str(uuid4())
        self.rooms.append({'id': identifier, 'title': title, 'topic': topic, 'kind': 'group', 'shared': False, 'ai_enabled': True})
        self.events[identifier] = []
        if self.create_error:
            raise self.create_error
        return {'organization_id': self.settings.core_organization_id, 'conversation_id': identifier}

    def list_groups(self):
        return {'conversations': self.rooms[:200]}

    def send(self, conversation_id, request_id, text, *, ai=False, agent_name='', reply_to=None):
        self.sends.append((conversation_id, request_id, text, ai, agent_name, reply_to))
        if self.on_send:
            self.on_send()
        existing = next((item for item in self.events[conversation_id] if item.get('_key') == request_id), None)
        if existing is None:
            existing = {'id': str(uuid4()), 'cursor': len(self.events[conversation_id]) + 1, 'text': text, 'actor_kind': 'ai' if ai else 'application', 'actor_id': self.settings.core_chat_installation_id, 'origin_issuer': self.settings.core_issuer, 'via_installation_id': self.settings.core_chat_installation_id, 'agent_name': agent_name, 'sender_name': agent_name or 'Application', 'created': '2026-10-18T12:00:00+00:00', 'reply_to': reply_to, 'reply_preview': '', '_key': request_id}
            self.events[conversation_id].append(existing)
        if self.send_error:
            raise self.send_error
        return {'organization_id': self.settings.core_organization_id, 'conversation_id': conversation_id, 'authority_issuer': self.settings.core_issuer, 'message': {name: value for name, value in existing.items() if name != '_key'}, 'created': len(self.sends) == 1}

    def messages(self, conversation_id, after=0, limit=100):
        if self.on_messages:
            self.on_messages()
        rows = [{name: value for name, value in item.items() if name != '_key'} for item in self.events[conversation_id] if item['cursor'] > after][:limit]
        return {'messages': rows, 'next_cursor': rows[-1]['cursor'] if rows else after}


@pytest.fixture
def chat(client, app):
    app.state.settings.core_chat_enabled = True
    app.state.settings.core_issuer = 'https://core.example.test'
    app.state.settings.core_chat_installation_id = '33333333-3333-4333-8333-333333333333'
    app.state.core_chat_transport = FakeCoreChat(app)
    return app.state.core_chat_transport


def sid(app):
    return default_school_id(app.state.settings.tenant_id)


def bootstrap(client, app, headers=None):
    headers = headers or login(client)
    result = client.post('/api/chat/bootstrap', json={'school_id': sid(app)}, headers=headers)
    assert result.status_code == 200, result.text
    return result.json()['groups']


def create(client, app, headers, **changes):
    body = {'school_id': sid(app), 'title': 'Science club', 'description': 'Student discussion', 'visibility': 'school', 'join_policy': 'open', 'idempotency_key': 'create-science-group', **changes}
    result = client.post('/api/chat/groups', json=body, headers=headers)
    assert result.status_code == 201, result.text
    return result.json()


def post(client, headers, group, text='My own question about the class.', key_value='message-logical-key'):
    return client.post('/api/chat/groups/' + group['id'] + '/messages', json={'text': text, 'idempotency_key': key_value}, headers=headers)


def group_url(group, suffix=''):
    return '/api/chat/groups/' + group['id'] + suffix


def test_bootstrap_required_class_group_and_plaintext_messages(client, app, chat):
    headers = login(client)
    group = bootstrap(client, app, headers)[0]
    assert group['required'] and group['visibility'] == 'class' and group['membership'] == 'active'
    assert group['can_send'] and not group['can_leave'] and group['status'] == 'ready'
    assert len(chat.creates) == 1 and bootstrap(client, app, headers)[0]['id'] == group['id'] and len(chat.creates) == 1
    result = post(client, headers, group)
    assert result.status_code == 200, result.text
    message = result.json()
    assert message['status'] == 'delivered' and message['author'] == {'id': 'demo-student', 'name': 'Jordan (student)', 'principal_kind': 'human'}
    visible = client.get(group_url(group, '/messages')).json()
    assert visible['messages'][0]['text'] == 'My own question about the class.' and visible['messages'][0]['author'] == message['author']
    assert visible['next_after'] == 1 and not visible['has_more']
    assert 'email' not in client.get(group_url(group, '/members')).text
    assert post(client, headers, group).json() == message and len(chat.sends) == 1
    assert client.get(group_url(group, '/commands?idempotency_key=message-logical-key')).json() == message
    assert post(client, headers, group, text='Changed').status_code == 409
    assert client.post(group_url(group, '/leave'), json={}, headers=headers).status_code == 403
    with app.state.database.factory() as db:
        receipts = list(db.scalars(select(Record).where(Record.kind == RECEIPT, Record.parent_id == group['id'])))
        assert len(receipts) == 3
        assert any(json.loads(row.payload).get('text') == 'My own question about the class.' for row in db.scalars(select(Audit).where(Audit.event_type == 'chat.message_delivered')))


def test_students_discover_join_and_leave_interest_groups_without_gaining_course_authority(client, app, chat):
    teacher = login(client, 'demo-instructor')
    group = create(client, app, teacher)
    student = login(client, 'demo-student2')
    with app.state.database.write() as db:
        for row in db.scalars(select(Record).where(Record.kind == 'enrollment', Record.owner_id == 'demo-student2')):
            db.delete(row)
    before = client.get('/api/chat/groups', params={'school_id': sid(app)}).json()
    assert group['id'] not in {item['id'] for item in before['groups']}
    found = client.get('/api/chat/groups', params={'school_id': sid(app), 'discover': True}).json()['groups']
    assert found[0]['can_join'] and not found[0]['can_read']
    assert client.get(group_url(group, '/messages')).status_code == 403
    assert client.post(group_url(group, '/join'), json={}, headers=student).json()['can_read']
    assert post(client, student, group).status_code == 200
    assert client.get('/api/courses/demo-course').status_code == 403
    left = client.post(group_url(group, '/leave'), json={}, headers=student)
    assert left.status_code == 200 and left.json()['membership'] == 'none'
    assert client.get(group_url(group, '/messages')).status_code == 403
    assert client.get(group_url(group, '/commands?idempotency_key=message-logical-key')).status_code == 403
    assert post(client, student, group).status_code == 403
    assert len(chat.sends) == 1


def test_private_groups_invitation_acceptance_and_approval_membership(client, app, chat):
    teacher = login(client, 'demo-instructor')
    private = create(client, app, teacher, visibility='private', join_policy='invite')
    student = login(client)
    assert client.get(group_url(private)).status_code == 404
    assert private['id'] not in {row['id'] for row in client.get('/api/chat/groups', params={'school_id': sid(app), 'discover': True}).json()['groups']}
    teacher = login(client, 'demo-instructor')
    invite = client.post(group_url(private, '/members'), json={'principal_id': 'demo-student', 'action': 'invite'}, headers=teacher)
    assert invite.status_code == 200, invite.text
    student = login(client)
    assert client.get(group_url(private)).json()['membership'] == 'invited'
    assert client.post(group_url(private, '/join'), json={}, headers=student).json()['can_read']
    teacher = login(client, 'demo-instructor')
    approved = create(client, app, teacher, join_policy='approval', idempotency_key='second-science-group')
    student = login(client)
    assert client.post(group_url(approved, '/join'), json={}, headers=student).json()['membership'] == 'requested'
    assert client.get(group_url(approved, '/messages')).status_code == 403
    teacher = login(client, 'demo-instructor')
    assert client.post(group_url(approved, '/members'), json={'principal_id': 'demo-student', 'action': 'approve'}, headers=teacher).status_code == 200
    student = login(client)
    assert client.get(group_url(approved)).json()['membership'] == 'active'
    teacher = login(client, 'demo-instructor')
    assert client.post(group_url(approved, '/members'), json={'principal_id': 'demo-student', 'action': 'remove'}, headers=teacher).status_code == 200
    student = login(client)
    assert client.get(group_url(approved, '/messages')).status_code == 403


def test_current_enrollment_revokes_read_send_receipts_and_discovery_immediately(client, app, chat):
    headers = login(client)
    group = bootstrap(client, app, headers)[0]
    receipt = post(client, headers, group).json()
    with app.state.database.write() as db:
        enrolled = db.scalar(select(Record).where(Record.kind == 'enrollment', Record.parent_id == 'demo-course', Record.owner_id == 'demo-student'))
        db.delete(enrolled)
    assert client.get(group_url(group)).status_code == 403
    assert client.get(group_url(group, '/messages')).status_code == 403
    assert client.get(group_url(group, '/commands/' + receipt['id'])).status_code == 403
    assert client.post(group_url(group, '/join'), json={}, headers=headers).status_code == 403
    assert post(client, headers, group).status_code == 403
    assert group['id'] not in {item['id'] for item in client.get('/api/chat/groups', params={'school_id': sid(app), 'discover': True}).json()['groups']}
    assert len(chat.sends) == 1


def test_school_membership_revocation_revokes_joined_optional_group(client, app, chat):
    admin = login(client, 'demo-admin')
    group = create(client, app, admin)
    student = login(client)
    assert client.post(group_url(group, '/join'), json={}, headers=student).status_code == 200
    with app.state.database.write() as db:
        member = db.get(Record, membership_id(sid(app), 'demo-student'))
        member.set({**member.value, 'active': False})
    assert client.get(group_url(group)).status_code == 403
    assert client.get(group_url(group, '/messages')).status_code == 403
    assert post(client, student, group).status_code == 403


def test_unassigned_teacher_cannot_create_or_manage_class_groups(client, app, chat):
    teacher = login(client, 'demo-instructor')
    group = create(client, app, teacher, course_id='demo-course', visibility='class')
    assert group['can_manage'] and not group['can_review']
    with app.state.database.write() as db:
        course = db.get(Record, 'demo-course')
        course.owner_id = 'demo-admin'
        enrolled = db.scalar(select(Record).where(Record.kind == 'enrollment', Record.parent_id == 'demo-course', Record.owner_id == 'demo-instructor'))
        if enrolled:
            db.delete(enrolled)
    assert client.get(group_url(group)).status_code == 403
    assert client.post('/api/chat/groups', json={'school_id': sid(app), 'title': 'Unauthorized', 'course_id': 'demo-course', 'visibility': 'class', 'idempotency_key': 'other-class-group'}, headers=teacher).status_code == 403


def test_uncertain_create_is_not_repeated_and_exact_recovery_only(client, app, chat):
    teacher = login(client, 'demo-instructor')
    chat.create_error = CoreChatFailure('unavailable', uncertain=True)
    group = create(client, app, teacher)
    assert group['status'] == 'uncertain' and group['can_recover'] and not group['can_read']
    assert create(client, app, teacher)['id'] == group['id'] and len(chat.creates) == 1
    recovered = client.post(group_url(group, '/recover'), json={}, headers=teacher)
    assert recovered.status_code == 409 and 'sole-participant' in recovered.text
    assert len(chat.creates) == 1
    chat.create_error = CoreChatFailure('unavailable', uncertain=True)
    other = create(client, app, teacher, idempotency_key='uncertain-other-group')
    chat.rooms[-1]['topic'] = 'attacker-topic'
    assert client.post(group_url(other, '/recover'), json={}, headers=teacher).status_code == 409
    assert len(chat.creates) == 2


def test_uncertain_send_explicit_exact_wire_retry_without_duplicate_message(client, app, chat):
    headers = login(client)
    group = bootstrap(client, app, headers)[0]
    chat.send_error = CoreChatFailure('unavailable', uncertain=True)
    result = post(client, headers, group)
    assert result.status_code == 200 and result.json()['status'] == 'uncertain' and result.json()['can_retry']
    assert post(client, headers, group).json()['status'] == 'uncertain' and len(chat.sends) == 1
    assert client.get(group_url(group, '/messages')).json()['messages'] == []
    chat.send_error = None
    retried = client.post(group_url(group, '/commands/' + result.json()['id'] + '/retry'), json={}, headers=headers)
    assert retried.status_code == 200, retried.text
    assert retried.json()['status'] == 'delivered' and len(chat.sends) == 2
    assert chat.sends[0] == chat.sends[1] and len(client.get(group_url(group, '/messages')).json()['messages']) == 1


def test_unknown_or_mutated_core_messages_are_withheld_and_cursor_advances(client, app, chat):
    headers = login(client)
    group = bootstrap(client, app, headers)[0]
    assert post(client, headers, group).status_code == 200
    room = chat.sends[0][0]
    source = chat.events[room][0]
    chat.events[room].append({**source, 'id': str(uuid4()), 'cursor': 2, 'text': 'Unknown Core message', '_key': 'unknown'})
    visible = client.get(group_url(group, '/messages')).json()
    assert len(visible['messages']) == 1 and visible['next_after'] == 2
    source['text'] = 'Mutated after delivery'
    assert client.get(group_url(group, '/messages')).json()['messages'] == []
    with app.state.database.factory() as db:
        assert json.loads(list(db.scalars(select(Audit).where(Audit.event_type == 'chat.messages_read')))[-1].payload)['withheld'] == 2


def test_revocation_during_core_read_or_send_withholds_result(client, app, chat):
    headers = login(client)
    group = bootstrap(client, app, headers)[0]
    def revoke():
        with app.state.database.write() as db:
            db.get(Record, membership_id(sid(app), 'demo-student')).set({'active': False, 'role': 'student'})
    chat.on_send = revoke
    assert post(client, headers, group).status_code == 403
    with app.state.database.factory() as db:
        outcomes = list(db.scalars(select(Record).where(Record.kind == RECEIPT, Record.parent_id == group['id'])))
        assert any(row.value.get('status') == 'delivered' for row in outcomes)
    assert len(chat.sends) == 1


@pytest.mark.parametrize('after,limit', [(-1, 100), (0, 101), (0, 0), (2**63, 100)])
def test_read_limits(client, app, chat, after, limit):
    headers = login(client)
    group = bootstrap(client, app, headers)[0]
    assert client.get(group_url(group, '/messages'), params={'after': after, 'limit': limit}).status_code == 422


def test_cross_school_and_tenant_registry_access_denied(client, app, chat):
    admin = login(client, 'demo-admin')
    other = client.post('/api/schools', json={'name': 'Other school', 'description': ''}, headers=admin).json()
    group = create(client, app, admin, school_id=other['id'])
    student = login(client)
    assert client.get(group_url(group)).status_code == 403
    assert client.get('/api/chat/groups', params={'school_id': other['id'], 'discover': True}).status_code == 403
    assert post(client, student, group).status_code == 403
    with app.state.database.write() as db:
        row = db.get(Record, group['id'])
        row.tenant_id = 'foreign-tenant'
    login(client, 'demo-admin')
    assert client.get(group_url(group)).status_code == 404


def test_reply_ids_must_be_locally_admitted_and_same_group(client, app, chat):
    headers = login(client)
    group = bootstrap(client, app, headers)[0]
    first = post(client, headers, group).json()
    result = client.post(group_url(group, '/messages'), json={'text': 'Follow up', 'idempotency_key': 'reply-original-key', 'reply_to': first['message_id']}, headers=headers)
    assert result.status_code == 200
    bad = client.post(group_url(group, '/messages'), json={'text': 'Forged reply', 'idempotency_key': 'reply-forged-key', 'reply_to': str(uuid4())}, headers=headers)
    assert bad.status_code == 409 and len(chat.sends) == 2


def test_group_and_message_inputs_reject_upstream_identity_urls_and_unknown_fields(client, app, chat):
    admin = login(client, 'demo-admin')
    group = create(client, app, admin)
    assert client.post(group_url(group, '/messages'), json={'text': 'Hello', 'idempotency_key': 'malicious-input', 'actor_id': 'someone-else'}, headers=admin).status_code == 422
    assert client.post('/api/chat/groups', json={'school_id': sid(app), 'title': 'Hello', 'idempotency_key': 'malicious-group', 'core_room_id': str(uuid4())}, headers=admin).status_code == 422
    student = login(client)
    assert client.post('/api/chat/groups', json={'school_id': sid(app), 'title': 'Student group', 'idempotency_key': 'student-group-key'}, headers=student).status_code == 403


def test_teacher_cannot_review_agent_publication(client, app, chat):
    teacher = login(client, 'demo-instructor')
    group = create(client, app, teacher, course_id='demo-course', visibility='class')
    assert client.get(group_url(group, '/proposals')).status_code == 403


def test_disabled_chat_keeps_class_registry_pending_without_transport_io(client, app, chat):
    app.state.settings.core_chat_enabled = False
    headers = login(client)
    group = bootstrap(client, app, headers)[0]
    assert group['status'] == 'pending' and not group['can_read'] and not chat.creates
    with app.state.database.factory() as db:
        assert db.get(Record, group['id']).value['provision_started'] is None
    app.state.settings.core_chat_enabled = True
    assert bootstrap(client, app, headers)[0]['status'] == 'ready' and len(chat.creates) == 1


def test_known_rejected_creation_can_explicitly_connect_after_configuration_repair(client, app, chat):
    teacher = login(client, 'demo-instructor')
    # A real Core known rejection creates no room. This synthetic transport also
    # records its attempted call, while deliberately not pretending uncertainty.
    def denied(title, topic=''):
        chat.creates.append((title, topic))
        raise CoreChatFailure('denied', status_code=403, uncertain=False)
    normal = chat.create_group
    chat.create_group = denied
    group = create(client, app, teacher)
    assert group['status'] == 'unavailable' and len(chat.creates) == 1
    assert create(client, app, teacher)['status'] == 'unavailable' and len(chat.creates) == 1
    chat.create_group = normal
    connected = client.post(group_url(group, '/recover'), json={}, headers=teacher)
    assert connected.status_code == 200 and connected.json()['status'] == 'ready' and len(chat.creates) == 2


@pytest.mark.parametrize('field,value', [('text', '\ud800'), ('text', 'bad\x00text'), ('title', '\ud800'), ('description', '\udfff')])
def test_invalid_utf8_and_control_characters_are_typed_validation_errors(client, app, chat, field, value):
    admin = login(client, 'demo-admin')
    group = create(client, app, admin)
    if field == 'text':
        result = client.post(group_url(group, '/messages'), content=json.dumps({'text': value, 'idempotency_key': 'invalid-utf8-key'}), headers={**admin, 'Content-Type': 'application/json'})
    else:
        result = client.post('/api/chat/groups', content=json.dumps({'school_id': sid(app), 'title': 'A group', 'description': '', 'idempotency_key': 'invalid-group-key', field: value}), headers={**admin, 'Content-Type': 'application/json'})
    assert result.status_code == 422, result.text
    assert not chat.sends


def test_interrupted_admitted_message_can_explicitly_resume_exact_original_wire_request(client, app, chat):
    from academy.chat_api import _authority, _hash, _scope
    from academy.chat_groups import author, put
    from academy.db import now
    headers = login(client)
    group = bootstrap(client, app, headers)[0]
    identity = {"id": "demo-student", "name": "Jordan (student)", "role": "student", "principal_kind": "human", "tenant_id": app.state.settings.tenant_id}
    identifier = key(COMMAND, identity["tenant_id"], group["id"], identity["id"], "message-logical-key")
    original_request_id = "synthetic-interrupted-wire-key"
    with app.state.database.write() as db:
        row = db.get(Record, group["id"])
        data = {"operation": "message", "idempotency_key": "message-logical-key", "request_id": original_request_id, "school_id": sid(app), "course_id": row.value["course_id"], "group_scope": _scope(row), "text": "My own question about the class.", "text_sha256": _hash("My own question about the class."), "reply_to": None, "author": author(identity), "authority": _authority(identity), "ai": False, "created_at": now()}
        put(db, identity["tenant_id"], COMMAND, identifier, group["id"], identity["id"], data, sid(app))
        put(db, identity["tenant_id"], ATTEMPT, "interrupted-attempt", identifier, identity["id"], {"status": "pending", "send_marker": now(), "error": None, "command_id": identifier}, sid(app))
    lookup = client.get(group_url(group, '/commands?idempotency_key=message-logical-key')).json()
    assert lookup['status'] == 'pending' and lookup['can_retry']
    assert post(client, headers, group).json()['status'] == 'pending' and not chat.sends
    resumed = client.post(group_url(group, '/commands/' + lookup['id'] + '/retry'), json={}, headers=headers)
    assert resumed.status_code == 200 and resumed.json()['status'] == 'delivered' and len(chat.sends) == 1
    assert chat.sends[0][1] == original_request_id


def test_student_chat_budget_spans_groups_and_counts_ambiguous_sends(client, app, chat, monkeypatch):
    from academy import chat_api
    monkeypatch.setattr(chat_api, 'MAX_DAILY_MESSAGES', 1)
    headers = login(client)
    group = bootstrap(client, app, headers)[0]
    chat.send_error = CoreChatFailure('unavailable', uncertain=True)
    assert post(client, headers, group).json()['status'] == 'uncertain'
    assert post(client, headers, group, key_value='second-daily-message').status_code == 403
    assert len(chat.sends) == 1


def test_members_do_not_see_nonmember_roster_or_pending_join_requests(client, app, chat):
    admin = login(client, 'demo-admin')
    group = create(client, app, admin, join_policy='approval')
    student = login(client)
    assert client.post(group_url(group, '/join'), json={}, headers=student).json()['membership'] == 'requested'
    admin = login(client, 'demo-admin')
    assert client.post(group_url(group, '/members'), json={'principal_id': 'demo-student', 'action': 'approve'}, headers=admin).status_code == 200
    other = login(client, 'demo-student2')
    assert client.post(group_url(group, '/join'), json={}, headers=other).json()['membership'] == 'requested'
    student = login(client)
    roster = client.get(group_url(group, '/members')).json()
    assert roster['candidates'] == [] and not roster['can_manage']
    assert 'demo-student2' not in {member['id'] for member in roster['members']}


from test_school_permissions import teacher_agent


def agent_chat_setup(client, app, chat, teacher_agent, monkeypatch):
    env = teacher_agent
    app.state.settings.core_service_id = env['identity']['consumer_service_id']
    group = create(client, app, env['headers'], course_id=env['course']['id'], visibility='class', idempotency_key='agent-class-chat')
    calls = []
    def core_agent(path, payload, purpose=''):
        calls.append((path, payload, purpose))
        return {'active': True, 'principal_kind': 'agent', 'iss': app.state.settings.core_issuer, 'aud': 'urn:core:service:' + app.state.settings.core_service_id, 'organization_id': app.state.settings.core_organization_id, 'agent_id': env['identity']['account_id'], 'sub': 'urn:core:agent:' + env['identity']['account_id'], 'binding_id': env['identity']['binding_id'], 'consumer_service_id': env['identity']['consumer_service_id'], 'environment': env['identity']['environment'], 'task_id': env['identity']['task_id'], 'permission': 'academy-teach', 'resource_type': 'academy-school', 'resource_id': 'school-registered', 'iat': int(__import__('time').time()), 'exp': int(__import__('time').time()) + 40, 'permissions': ['academy-use', 'academy-teach']}
    monkeypatch.setattr(app.state.auth, 'core_agent', core_agent)
    return group, calls


def test_bound_agent_can_create_assigned_class_group_but_message_requires_school_admin_review(client, app, chat, teacher_agent, monkeypatch):
    group, checks = agent_chat_setup(client, app, chat, teacher_agent, monkeypatch)
    assert group['can_manage'] and not group['can_review']
    proposed = post(client, teacher_agent['headers'], group, text='Which evidence supports your claim?')
    assert proposed.status_code == 202 and proposed.json()['status'] == 'pending_review' and not chat.sends
    assert client.get(group_url(group, '/proposals'), headers=teacher_agent['headers']).status_code == 403
    admin = login(client, 'demo-admin')
    pending = client.get(group_url(group, '/proposals')).json()['proposals']
    assert pending[0]['text'] == proposed.json()['text'] and pending[0]['author']['principal_kind'] == 'agent'
    approved = client.post(group_url(group, '/proposals/' + proposed.json()['proposal_id'] + '/review'), json={'decision': 'approve'}, headers=admin)
    assert approved.status_code == 200, approved.text
    assert approved.json()['status'] == 'delivered' and len(chat.sends) == 1 and chat.sends[0][3] is True and chat.sends[0][4] == 'Synthetic Teacher'
    assert checks and all(call[0] == '/api/platform/v1/agent-bindings/authorize/' for call in checks)
    assert client.post(group_url(group, '/proposals/' + proposed.json()['proposal_id'] + '/review'), json={'decision': 'approve'}, headers=admin).json()['status'] == 'delivered' and len(chat.sends) == 1
    visible = client.get(group_url(group, '/messages')).json()['messages']
    assert visible[0]['ai'] and visible[0]['author']['id'] == teacher_agent['identity']['id']


def test_agent_has_no_schoolwide_group_creation_and_revoked_placement_blocks_draft_approval(client, app, chat, teacher_agent, monkeypatch):
    group, checks = agent_chat_setup(client, app, chat, teacher_agent, monkeypatch)
    forbidden = client.post('/api/chat/groups', json={'school_id': sid(app), 'title': 'School chat', 'idempotency_key': 'agent-school-chat'}, headers=teacher_agent['headers'])
    assert forbidden.status_code == 403
    proposed = post(client, teacher_agent['headers'], group)
    assert proposed.status_code == 202
    admin = login(client, 'demo-admin')
    with app.state.database.write() as db:
        row = db.get(Record, teacher_agent['placement']['id'])
        row.set({**row.value, 'active': False})
    denied = client.post(group_url(group, '/proposals/' + proposed.json()['proposal_id'] + '/review'), json={'decision': 'approve'}, headers=admin)
    assert denied.status_code in {403, 409} and not chat.sends


def test_agent_original_course_assignment_revocation_blocks_human_publication(client, app, chat, teacher_agent, monkeypatch):
    group, checks = agent_chat_setup(client, app, chat, teacher_agent, monkeypatch)
    proposed = post(client, teacher_agent['headers'], group).json()
    admin = login(client, 'demo-admin')
    with app.state.database.write() as db:
        course = db.get(Record, teacher_agent['course']['id'])
        course.owner_id = 'demo-admin'
        for row in db.scalars(select(Record).where(Record.kind == 'enrollment', Record.parent_id == course.id, Record.owner_id == teacher_agent['identity']['id'])):
            db.delete(row)
    result = client.post(group_url(group, '/proposals/' + proposed['proposal_id'] + '/review'), json={'decision': 'approve'}, headers=admin)
    assert result.status_code == 403 and not chat.sends


def test_rejected_agent_draft_is_immutable_terminal_and_never_sent(client, app, chat, teacher_agent, monkeypatch):
    group, checks = agent_chat_setup(client, app, chat, teacher_agent, monkeypatch)
    proposed = post(client, teacher_agent['headers'], group).json()
    admin = login(client, 'demo-admin')
    result = client.post(group_url(group, '/proposals/' + proposed['proposal_id'] + '/review'), json={'decision': 'reject'}, headers=admin)
    assert result.status_code == 200 and result.json()['status'] == 'rejected' and not chat.sends
    assert client.post(group_url(group, '/proposals/' + proposed['proposal_id'] + '/review'), json={'decision': 'approve'}, headers=admin).json()['status'] == 'rejected' and not chat.sends


def test_inaccessible_older_groups_cannot_displace_required_enrolled_class_group(client, app, chat):
    # Synthetic registry rows deliberately exceed the administrative create cap
    # to represent required class groups accumulated over a large school.
    with app.state.database.write() as db:
        for index in range(110):
            identifier = key('older-private-group', index)
            row = Record(id=identifier, tenant_id=app.state.settings.tenant_id, kind=GROUP, parent_id=sid(app), owner_id='demo-instructor', created_at='2020-01-01T00:00:00+00:00', data=canonical({'title': 'Private staff ' + str(index), 'description': '', 'school_id': sid(app), 'course_id': None, 'visibility': 'private', 'join_policy': 'invite', 'required': False, 'status': 'pending', 'error': None, 'topic': 'private', 'room_id': None, 'provision_started': None}))
            db.add(row)
            db.flush()
            bind_scope(db, row, sid(app))
    student = login(client)
    required = bootstrap(client, app, student)[0]
    listing = client.get('/api/chat/groups', params={'school_id': sid(app), 'discover': True}).json()
    assert [row['id'] for row in listing['groups']] == [required['id']] and not listing['truncated']
    login(client, 'demo-admin')
    listing = client.get('/api/chat/groups', params={'school_id': sid(app), 'discover': True}).json()
    assert listing['groups'][0]['required'] and listing['truncated']


def test_agent_proposal_pins_exact_placement_metadata_as_well_as_live_binding(client, app, chat, teacher_agent, monkeypatch):
    group, checks = agent_chat_setup(client, app, chat, teacher_agent, monkeypatch)
    proposed = post(client, teacher_agent['headers'], group).json()
    admin = login(client, 'demo-admin')
    with app.state.database.write() as db:
        placement = db.get(Record, teacher_agent['placement']['id'])
        placement.set({**placement.value, 'purpose': 'changed-approved-purpose'})
    result = client.post(group_url(group, '/proposals/' + proposed['proposal_id'] + '/review'), json={'decision': 'approve'}, headers=admin)
    assert result.status_code == 409 and not chat.sends


def test_disabling_ready_chat_blocks_new_io_and_hides_send_capabilities(client, app, chat):
    headers = login(client)
    group = bootstrap(client, app, headers)[0]
    app.state.settings.core_chat_enabled = False
    descriptor = client.get(group_url(group)).json()
    assert not descriptor['can_read'] and not descriptor['can_send']
    assert client.get(group_url(group, '/messages')).status_code == 503
    assert post(client, headers, group).status_code == 503
    assert not chat.sends


def test_audit_archive_is_self_contained_for_exact_core_wire_and_delivery_proof(client, app, chat):
    headers = login(client)
    group = bootstrap(client, app, headers)[0]
    sent = post(client, headers, group, text='My recorded class question.').json()
    with app.state.database.factory() as db:
        events = [json.loads(row.payload) for row in db.scalars(select(Audit).where(Audit.session_id == group['id']).order_by(Audit.sequence))]
    creation = next(event for event in events if event.get('operation') == 'chat.group_create_send_marked')
    connected = next(event for event in events if event.get('operation') == 'chat.group_provisioned')
    assert creation['wire_request'] == {'title': group['title'], 'topic': chat.creates[0][1], 'kind': 'group', 'ai_enabled': True}
    assert connected['creation_receipt']['conversation_id'] == chat.sends[0][0]
    admission = next(event for event in events if event.get('operation') == 'chat.message_admitted')
    marker = next(event for event in events if event.get('operation') == 'chat.message_send_marked')
    delivered = next(event for event in events if event.get('operation') == 'chat.message_delivered')
    wire = {'conversation_id': chat.sends[0][0], 'request_id': chat.sends[0][1], 'text': 'My recorded class question.', 'ai': False, 'agent_name': '', 'reply_to': None}
    assert admission['command_snapshot']['wire_request'] == marker['command_snapshot']['wire_request'] == delivered['command_snapshot']['wire_request'] == wire
    assert delivered['command_snapshot']['author'] == sent['author']
    actual = {name: value for name, value in chat.events[chat.sends[0][0]][0].items() if name != '_key'}
    assert delivered['core_delivery']['message'] == actual
    assert delivered['core_delivery']['conversation_id'] == wire['conversation_id']
    assert actual['id'] == sent['message_id'] and actual['actor_id'] == app.state.settings.core_chat_installation_id
    assert actual['cursor'] == 1 and actual['origin_issuer'] == app.state.settings.core_issuer and actual['created']
    assert 'token' not in canonical(delivered) and 'api_key' not in canonical(delivered)


def test_bootstrap_filters_current_school_and_enrollment_before_course_limit(client, app, chat):
    with app.state.database.write() as db:
        other_school = Record(id='other-bootstrap-school', tenant_id=app.state.settings.tenant_id, kind='school', data=canonical({'name': 'Other school'}))
        db.add(other_school)
        db.flush()
        # Earlier course IDs deliberately fill the old tenant-wide scan page.
        for number in range(501):
            row = Record(id=f'aaa-other-course-{number:04d}', tenant_id=app.state.settings.tenant_id, kind='course', owner_id='demo-instructor', data=canonical({'title': 'Other class'}))
            db.add(row)
            db.flush()
            bind_scope(db, row, other_school.id)
    headers = login(client)
    result = client.post('/api/chat/bootstrap', json={'school_id': sid(app)}, headers=headers)
    assert result.status_code == 200, result.text
    assert [row['course_id'] for row in result.json()['groups']] == ['demo-course']
    assert not result.json()['bootstrap_truncated'] and len(chat.creates) == 1


def test_long_authenticated_agent_name_is_preserved_locally_with_bounded_core_label(client, app, chat, teacher_agent, monkeypatch):
    teacher_agent['identity']['name'] = 'Synthetic Teacher ' + 'A' * 180
    group, checks = agent_chat_setup(client, app, chat, teacher_agent, monkeypatch)
    proposed = post(client, teacher_agent['headers'], group).json()
    assert len(proposed['author']['name']) > 160
    admin = login(client, 'demo-admin')
    result = client.post(group_url(group, '/proposals/' + proposed['proposal_id'] + '/review'), json={'decision': 'approve'}, headers=admin)
    assert result.status_code == 200 and result.json()['status'] == 'delivered'
    assert chat.sends[0][4] == proposed['author']['name'][:160]
    assert client.get(group_url(group, '/messages')).json()['messages'][0]['author']['name'] == proposed['author']['name']
