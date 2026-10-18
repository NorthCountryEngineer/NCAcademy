"""Lesson Q&A has full reviewer replay under current class/audit scope."""
from conftest import login
from test_lesson_tutoring import lesson_environment, ask


def test_lesson_session_lists_replays_exports_and_logs_reviewer_access(client,app,monkeypatch):
    env=lesson_environment(client,app,monkeypatch)
    turn=ask(client,env).json()
    assert turn['status']=='released'
    login(client,'demo-instructor')
    sessions=client.get('/api/audit/sessions').json()
    session=next(s for s in sessions if s['session_id']==turn['session_id'])
    assert session['session_kind']=='lesson' and session['assignment_id'] is None
    assert session['assignment_title'].startswith('Lesson Q&A · ')
    replay=client.get('/api/audit/sessions/'+turn['session_id'])
    assert replay.status_code==200,replay.text
    row=replay.json()['turns'][0]
    assert row['teacher_context_snapshot']['teacher_mode']=='managed'
    assert row['evidence_snapshot']['matches'] and row['send_marker']
    assert row['prompt'] and row['response'] and row['citations']==turn['citations']
    assert 'synthetic-lesson-key-never-live' not in replay.text
    assert any(e['event_type']=='audit.session_viewed' for e in replay.json()['events'])
    exported=client.get('/api/audit/sessions/'+turn['session_id']+'/export')
    assert exported.status_code==200 and exported.json()['scope']=='session_partial'
    assert any(e['event_type']=='audit.session_exported' for e in exported.json()['events'])
    login(client,'demo-student2')
    assert client.get('/api/audit/sessions/'+turn['session_id']).status_code==403
    login(client,'demo-admin')
    school_export=client.get('/api/schools/'+env['school']+'/audit/export').json()
    assert any(e['event_type']=='lesson.released' and e['session_id']==turn['session_id'] for e in school_export['events'])
    assert client.get('/api/audit/verify').json()['valid']


def test_current_teacher_and_audit_authority_required_for_lesson_replay(client,app,monkeypatch):
    from academy.db import Record
    env=lesson_environment(client,app,monkeypatch)
    turn=ask(client,env).json()
    with app.state.database.write() as db:
        enrollment=next(r for r in db.query(Record).filter_by(kind='enrollment',parent_id=env['course']['id']).all() if r.owner_id=='demo-instructor')
        db.delete(enrollment)
        course=db.get(Record,env['course']['id']);course.owner_id='demo-admin'
    login(client,'demo-instructor')
    assert client.get('/api/audit/sessions/'+turn['session_id']).status_code==403
