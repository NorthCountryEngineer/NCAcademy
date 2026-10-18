#!/usr/bin/env python3
"""Exercise the real loopback Compose API with synthetic coursework only.

Refuses Core/production authentication and any non-loopback target. Never calls a
model or reads configuration/secrets. Leaves labeled synthetic records for review.
"""
import argparse
import http.cookiejar
import json
import sys
from datetime import datetime, timezone
from urllib.error import HTTPError
from urllib.parse import urlsplit
from urllib.request import HTTPCookieProcessor, Request, build_opener
from uuid import uuid4

from verify_audit import verify


class Client:
    def __init__(self, origin):
        self.origin = origin
        self.opener = build_opener(HTTPCookieProcessor(http.cookiejar.CookieJar()))
        self.csrf = ''

    def call(self, path, body=None, method='GET', expect=200):
        data = json.dumps(body).encode() if body is not None else None
        headers = {'Origin': self.origin, 'X-CSRF-Token': self.csrf}
        if data is not None:
            headers['Content-Type'] = 'application/json'
        request = Request(self.origin + '/api' + path, data=data, method=method, headers=headers)
        try:
            with self.opener.open(request, timeout=30) as response:
                status = response.status
                raw = response.read()
        except HTTPError as error:
            status, raw = error.code, error.read()
        if status != expect:
            raise AssertionError(f'{method} {path}: expected HTTP {expect}, received {status}')
        return json.loads(raw) if raw else None

    def login(self, user_id):
        self.call('/auth/local', {'user_id':user_id}, 'POST')
        self.csrf = self.call('/auth/me')['csrf_token']


def run(origin):
    parsed = urlsplit(origin)
    if parsed.scheme != 'http' or parsed.hostname not in {'localhost', '127.0.0.1', '::1'} or parsed.path or parsed.query or parsed.fragment:
        raise ValueError('Smoke test requires an exact loopback HTTP origin')
    anonymous = Client(origin)
    config = anonymous.call('/auth/config')
    if config['mode'] != 'local':
        raise ValueError('Refusing to test a production/Core installation')
    users = config['demo_users']
    instructor_id = next(u['id'] for u in users if u['role'] in {'teacher', 'instructor'})
    student_ids = [u['id'] for u in users if u['role'] == 'student']
    admin_id = next(u['id'] for u in users if u['role'] == 'admin')
    instructor, student, admin = (Client(origin) for _ in range(3))
    instructor.login(instructor_id)
    student.login(student_ids[0])
    admin.login(admin_id)
    assert admin.call('/admin/settings')['live_models_enabled'] is False
    label = 'Local verification ' + datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M:%S')
    course = instructor.call('/courses', {'title':label, 'description':'Synthetic local workflow verification.'}, 'POST', 201)
    cid = course['id']
    student.call('/courses/' + cid, expect=403)
    instructor.call('/courses/' + cid + '/enrollments', {'user_id':student_ids[0], 'role':'student'}, 'POST')
    hint = 'Compare the evidence for each explanation. Which assumption could you test first?'
    assignment = instructor.call('/courses/' + cid + '/assignments', {
        'title':'Explain your method', 'module':'Reasoning',
        'instructions':'Write your own explanation and describe a test for your assumption.',
        'tutor_policy': {'mode':'curated', 'max_turns':1, 'hints':[{'id':'h1','text':hint}], 'questions':[]}
    }, 'POST', 201)
    aid = assignment['id']
    task = student.call('/assignments/' + aid)
    assert hint not in json.dumps(task), 'Unreleased hint bank leaked to student'
    assert hint not in json.dumps(student.call('/courses/' + cid)), 'Course detail leaked unreleased hints'
    assert hint not in json.dumps(student.call('/courses/' + cid + '/assignments')), 'Assignment list leaked unreleased hints'
    blocked = student.call('/assignments/' + aid + '/tutor', {'message':'I am stuck.', 'idempotency_key':uuid4().hex}, 'POST')
    assert blocked['status'] == 'blocked'
    work = 'My initial explanation compares two assumptions. I would collect more observations to test which assumption fits.'
    student.call('/assignments/' + aid + '/draft', {'content':work}, 'PUT')
    key = uuid4().hex
    payload = {'message':'How can I examine my assumption?', 'idempotency_key':key}
    released = student.call('/assignments/' + aid + '/tutor', payload, 'POST')
    assert released['status'] == 'released' and released['approved_text'] == hint
    again = student.call('/assignments/' + aid + '/tutor', payload, 'POST')
    assert again['id'] == released['id'], 'Idempotent request created another turn'
    limited = student.call('/assignments/' + aid + '/tutor', {'message':'Another hint?', 'idempotency_key':uuid4().hex}, 'POST')
    assert limited['status'] == 'blocked'
    submitted = student.call('/assignments/' + aid + '/submit', {'content':work}, 'POST', 201)
    student.call('/assignments/' + aid + '/draft', {'content':work + ' Later draft change.'}, 'PUT')
    snapshots = student.call('/assignments/' + aid)['submissions']
    assert any(s['id'] == submitted['id'] and s['content'] == work for s in snapshots)
    student.call('/admin/settings', expect=403)
    student.call('/courses', {'title':'Forbidden'}, 'POST', 403)
    student.call('/assignments/' + aid + '/submissions', expect=403)
    if len(student_ids) > 1:
        outsider = Client(origin)
        outsider.login(student_ids[1])
        outsider.call('/assignments/' + aid, expect=403)
    feedback = 'Describe the evidence that would distinguish your assumptions.'
    instructor.call('/submissions/' + submitted['id'] + '/review', {'feedback':feedback}, 'POST')
    assert student.call('/assignments/' + aid)['submissions'][0]['feedback'] == feedback
    sessions = instructor.call('/audit/sessions')
    session = next(s for s in sessions if s['assignment_id'] == aid and s['student_id'] == student_ids[0])
    replay = instructor.call('/audit/sessions/' + session['session_id'])
    assert any(t.get('draft_snapshot') == work for t in replay['turns'])
    assert any(e['event_type'] == 'student.submitted' for e in replay['events'])
    assert any(e['event_type'] == 'audit.session_viewed' for e in replay['events'])
    student.call('/audit/sessions/' + session['session_id'], expect=403)
    export = admin.call('/admin/audit/export')
    checkpoint = {'tenant_id':export['tenant_id'], **export['head']}
    verified = verify(export, checkpoint)
    assert verified['independently_anchored']
    return {'status':'passed', 'storage':'real Compose PostgreSQL', 'course_id':cid, 'assignment_id':aid,
            'audit_events_verified':verified['sequence'], 'live_model_calls':0,
            'checks':['enrollment isolation','unreleased hint privacy','attempt gate','approved hint release','idempotency','turn limit','submission snapshots','role boundaries','instructor feedback','session evidence','full audit chain']}


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--origin', default='http://localhost:8090')
    args = parser.parse_args()
    try:
        print(json.dumps(run(args.origin.rstrip('/')), indent=2))
    except (AssertionError, ValueError, KeyError, StopIteration, OSError) as error:
        print('Local smoke verification failed: ' + str(error), file=sys.stderr)
        raise SystemExit(1)
