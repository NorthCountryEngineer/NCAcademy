#!/usr/bin/env python3
"""Verify school setup on an isolated loopback local installation.

Creates labeled synthetic schools, courses and teacher profiles. Refuses Core,
live models and non-loopback origins. Leaves its synthetic evidence for review.
"""
import argparse
import base64
import json
import struct
import sys
import zlib
from urllib.parse import urlsplit
from uuid import uuid4

from smoke_local import Client
from verify_audit import verify


def fixture_logo():
    def chunk(kind, value):
        return struct.pack('>I', len(value)) + kind + value + struct.pack('>I', zlib.crc32(kind + value) & 0xffffffff)
    raw = b'\x89PNG\r\n\x1a\n' + chunk(b'IHDR', struct.pack('>IIBBBBB', 1, 1, 8, 2, 0, 0, 0)) + chunk(b'IDAT', zlib.compress(b'\x00\x14\x78\x96')) + chunk(b'IEND', b'')
    return {'media_type':'image/png', 'data_base64':base64.b64encode(raw).decode()}


def run(origin):
    parsed = urlsplit(origin)
    if parsed.scheme != 'http' or parsed.hostname not in {'localhost','127.0.0.1','::1'} or parsed.path or parsed.query or parsed.fragment or parsed.username or parsed.password:
        raise ValueError('School smoke requires an exact loopback HTTP origin')
    admin = Client(origin)
    config = admin.call('/auth/config')
    if config['mode'] != 'local':
        raise ValueError('Refusing a production/Core installation')
    admin.login(next(item['id'] for item in config['demo_users'] if item['role'] == 'admin'))
    settings = admin.call('/admin/settings')
    assert settings['live_models_enabled'] is False and settings['schema_version'] >= 7
    catalog = admin.call('/school-curriculum/catalog')
    assert catalog['custom_ingestion_supported'] is False
    assert not catalog['planner']['live_model_used'] and not catalog['planner']['network_fetch']
    subjects = next(item for item in catalog['states'] if item['code'] == 'US-NY')['subjects']
    assert {'earth_space_science','environmental_science'} <= {item['code'] for item in subjects}
    label = 'Synthetic school setup ' + uuid4().hex[:8]
    intent = {'mode':'state','state':'US-NY','academic_year':'2026-2027','grades':[9,10],'subjects':['earth_space_science']}
    school = admin.call('/schools', {'name':label,'description':'Synthetic onboarding acceptance only.','logo':fixture_logo(),'custom_metadata':[{'key':'Fixture','value':'Synthetic only'}],'curriculum':intent}, 'POST', 201)
    sid = school['id']
    assert school['version'] == 1 and school['logo']['sha256'] and school['custom_metadata'][0]['value'] == 'Synthetic only'
    path = '/schools/' + sid
    command = {'idempotency_key':uuid4().hex, 'expected_school_version':school['version']}
    planned = admin.call(path + '/curriculum/runs', command, 'POST', 201)
    assert planned['status'] == 'ready' and len(planned['plan']['courses']) == 2
    assert planned['current'] and not planned['agent']['live_model_used']
    assert admin.call(path + '/curriculum/runs', command, 'POST', 201)['id'] == planned['id']
    assert admin.call('/courses?school_id=' + sid) == []
    publication_command = {**command, 'idempotency_key':uuid4().hex, 'run_id':planned['id'], 'plan_sha256':planned['plan_sha256'], 'course_keys':[item['key'] for item in planned['plan']['courses']]}
    published = admin.call(path + '/curriculum/publish', publication_command, 'POST', 201)
    assert admin.call(path + '/curriculum/publish', publication_command, 'POST', 201)['id'] == published['id']
    assert len(published['courses']) == len(admin.call('/courses?school_id=' + sid)) == 2
    course_ids = [item['course_id'] for item in published['courses']]
    for item in published['courses']:
        corpus = admin.call('/courses/' + item['course_id'] + '/corpus')
        assert corpus['instructional_material_count'] == 5 and not corpus['ready_for_teaching']
        assert corpus['approval']['status'] == 'pending'
        course = admin.call('/courses/' + item['course_id'])
        assert course['school_curriculum']['curriculum_revision'] == school['curriculum_revision']
        assert course['curriculum']['grades'] == [item['grade']]
    teacher = admin.call(path + '/teachers', {'kind':'ai','name':'Synthetic earth mentor','biography':'A synthetic school-setup fixture.','ai_behavior':{'tone':'warm','instructions':'Use reviewed course evidence and ask students to explain their observations.'}}, 'POST', 201)
    assert teacher['course_ids'] == [] and teacher['authority_granted'] is False
    assert teacher['ai_behavior']['instructions'].startswith('Use reviewed')
    assigned = admin.call(path + '/teachers/' + teacher['id'] + '/courses', {'expected_version':teacher['version'],'course_ids':course_ids}, 'PUT')
    receipt = assigned['onboarding_run']
    assert receipt['status'] == 'needs_review' and len(receipt['courses']) == 2
    assert all(item['corpus']['id'] for item in receipt['courses']) and receipt['authority_granted'] is False
    human = admin.call(path + '/teachers', {'kind':'human','name':'Synthetic human teacher','biography':'Awaiting normal identity onboarding.'}, 'POST', 201)
    assert human['principal_id'] is None and human['authority_granted'] is False
    workflows = admin.call(path + '/onboarding/runs')
    assert receipt['id'] in {item['id'] for item in workflows}
    # Change curriculum intent without rewriting the already created classes.
    changed = admin.call(path, {'name':label,'description':school['description'],'expected_version':school['version'],'curriculum':{'mode':'none'}}, 'PUT')
    assert changed['logo']['sha256'] == school['logo']['sha256']
    assert changed['custom_metadata'] == school['custom_metadata']
    before = admin.call(path + '/curriculum')
    assert next(item for item in before['runs'] if item['id'] == planned['id'])['current'] is False
    admin.call(path + '/curriculum/publish', {**publication_command,'idempotency_key':uuid4().hex,'expected_school_version':changed['version']}, 'POST', 409)
    no_curriculum = admin.call(path + '/curriculum/runs', {'idempotency_key':uuid4().hex,'expected_school_version':changed['version']}, 'POST', 201)
    assert no_curriculum['plan']['courses'] == [] and len(admin.call('/courses?school_id=' + sid)) == 2
    changed = admin.call(path, {'name':label,'description':school['description'],'expected_version':changed['version'],'curriculum':{'mode':'custom','custom_name':'Synthetic future program'}}, 'PUT')
    custom = admin.call(path + '/curriculum/runs', {'idempotency_key':uuid4().hex,'expected_school_version':changed['version']}, 'POST', 201)
    assert custom['status'] == 'unavailable' and not custom['plan']['ingestion_supported']
    student = Client(origin)
    student.login(next(item['id'] for item in config['demo_users'] if item['role'] == 'student'))
    student.call(path + '/onboarding/runs', expect=403)
    student.call('/school-curriculum/catalog', expect=403)
    export = admin.call('/admin/audit/export')
    verified = verify(export, {'tenant_id':export['tenant_id'],**export['head']})
    return {'status':'passed','synthetic_only':True,'schema':settings['schema_version'],'school_id':sid,'classes':2,'teachers':2,'source_documents_per_class':5,'audit_events_verified':verified['sequence'],'live_model_calls':0,'checks':['school logo and metadata','NY source catalog','grade-year draft plan','explicit class creation','idempotent retries','automatic pending corpora','base AI instructions','multi-course teacher assignment receipt','human profile onboarding','configuration invalidation','no-curriculum mode','deferred custom ingestion','student isolation','complete audit chain']}


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--origin', required=True)
    args = parser.parse_args()
    try:
        print(json.dumps(run(args.origin.rstrip('/')), indent=2))
    except (AssertionError,ValueError,KeyError,StopIteration,OSError) as error:
        print('School setup verification failed: ' + str(error), file=sys.stderr)
        raise SystemExit(1)
