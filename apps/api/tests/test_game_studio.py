"""Game Studio public data, human release and platform-local learner boundaries."""
from copy import deepcopy
import json
from pathlib import Path

import pytest
from sqlalchemy import select, text
from sqlalchemy.exc import DatabaseError

from academy.db import Audit, Record, canonical, game_studio_schema_id
from academy.game_studio import RUNTIME_CONTRACT, ROUTES, validate_public_game
from academy.game_studio_transport import GameStudioFailure
from academy.migrate import migrate
from academy.permissions import bind_scope, default_school_id
from academy.standards import digest
from conftest import login
from test_teacher_onboarding import new_school


STUDIO = "https://studio.example.test"
API = "https://games.example.test"
GAME_ID = "11111111-2222-4333-8444-555555555555"
SPEC = {"version": 1, "title": "Practice collection", "description": "Practice planning and careful movement.", "genre": "collect", "theme": "pond", "player": {"name": "Learner", "color": "#88aaff", "speed": 5, "jump": 10}, "rules": {"goal": 3, "timeLimit": 60, "lives": 3, "difficulty": 1}, "objects": {"collectible": "star", "hazard": "rock"}, "instructions": "Collect three stars and avoid rocks."}


def published(spec=None):
    return {"id": GAME_ID, "spec": deepcopy(spec or SPEC), "revision": 2, "publishedRevision": 2, "status": "Published", "createdAt": "2026-10-20T00:00:00+00:00", "updatedAt": "2026-10-26T00:00:00+00:00", "publishedAt": "2026-10-26T00:00:00+00:00"}


def base(app, school=None):
    return f"/api/schools/{school or default_school_id(app.state.settings.tenant_id)}/game-studio"


def course_path(course="demo-course", resource=None, action=None):
    return f"/api/courses/{course}/games" + ("/" + resource if resource else "") + ("/" + action if action else "")


def setup(client, app, monkeypatch, *, hosts=None, spec=None):
    headers = login(client, "demo-admin")
    response = client.put(base(app), json={"expected_version": 0, "enabled": True, "studio_origin": STUDIO, "api_origin": API, "host_id": "academy"}, headers=headers)
    assert response.status_code == 200, response.text
    game = published(spec); calls = []
    def fetch(origin, path):
        calls.append((origin, path))
        assert origin == API
        if path == "/integration": return {"version": 1, "revision": 2, "branding": {"appName": "Game Studio"}, "routes": ROUTES, "hosts": hosts or []}
        if path.startswith("/games/published?") or path == "/games/published": return {"games": [deepcopy(game)]}
        assert path == "/games/published/" + GAME_ID
        return deepcopy(game)
    monkeypatch.setattr(app.state, "game_studio_fetch", fetch)
    return headers, game, calls


def import_game(client, app, headers, game=None, course="demo-course", key="synthetic-game-import-0001"):
    game = game or published()
    response = client.post(course_path(course), json={"game_id": game["id"], "published_revision": game["publishedRevision"], "spec_sha256": digest(game["spec"]), "learning_objective": "Practice planning a safe route and explain a strategy afterwards.", "lesson_label": "Planning practice", "idempotency_key": key}, headers=headers)
    assert response.status_code == 201, response.text
    return response.json()


def approve(client, headers, item):
    response = client.post(course_path(item["course_id"], item["id"], "approve"), json={"expected_version": item["version"], "reviewed_level_ids": item["level_ids"], "reviewed": True}, headers=headers)
    assert response.status_code == 200, response.text
    return response.json()


def records(app, kinds=None):
    with app.state.database.factory() as db:
        query = select(Record).order_by(Record.id)
        if kinds: query = query.where(Record.kind.in_(kinds))
        return [(row.id, row.kind, row.data) for row in db.scalars(query)]


def test_default_disabled_settings_and_blank_saved_configuration_are_readable_without_network(client, app, monkeypatch):
    headers = login(client, "demo-admin")
    monkeypatch.setattr(app.state, "game_studio_fetch", lambda *args: pytest.fail("Settings must not contact Game Studio"))
    value = client.get(base(app)).json(); assert value["version"] == 0 and not value["enabled"] and value["creator_url"] is None
    saved = client.put(base(app), json={"expected_version": 0}, headers=headers)
    assert saved.status_code == 200, saved.text
    assert client.get(base(app)).json()["version"] == 1
    assert client.get(base(app) + "/catalog").status_code == 409
    assert client.post(base(app) + "/verify", json={"expected_version": 1}, headers=headers).status_code == 409


def test_verify_supports_bare_creator_and_exact_host_return_without_tokens(client, app, monkeypatch):
    headers, _game, calls = setup(client, app, monkeypatch)
    assert client.get(base(app)).json()["creator_url"] is None
    bare = client.post(base(app) + "/verify", json={"expected_version": 1}, headers=headers)
    assert bare.status_code == 200, bare.text
    assert bare.json()["creator_url"] == STUDIO + "/game-studio" and not bare.json()["host_configured"] and bare.json()["creator_ready"]
    assert client.get(base(app)).json()["creator_url"] == STUDIO + "/game-studio"
    monkeypatch.setattr(app.state, "game_studio_fetch", lambda *_: {"version": 1, "routes": ROUTES, "hosts": [{"id": "academy", "name": "Academy", "origin": app.state.settings.app_url, "returnPath": "/"}]})
    mapped = client.post(base(app) + "/verify", json={"expected_version": 1}, headers=headers).json()
    assert mapped["host_configured"] and mapped["creator_url"] == STUDIO + "/game-studio?host=academy" and mapped["return_path"] == "/"
    assert calls == [(API, "/integration")]
    login(client, "demo-instructor")
    assert client.get(base(app)).json()["creator_url"] is None
    assert client.post(base(app) + "/verify", json={"expected_version": 1}, headers=login(client, "demo-instructor")).status_code == 403


def test_catalog_and_teacher_import_are_explicit_public_reads_without_class_profile_mutation(client, app, monkeypatch):
    headers, game, calls = setup(client, app, monkeypatch)
    before = records(app, ["course", "teacher_profile", "teacher_profile_revision", "teacher_model_config", "course_corpus", "corpus_approval", "enrollment", "school_membership"])
    headers = login(client, "demo-instructor")
    assert calls == []
    catalog = client.get(base(app) + "/catalog").json()
    assert len(catalog["games"]) == 1 and "spec" not in catalog["games"][0]
    assert catalog["games"][0]["level_ids"] == ["level-1"]
    item = import_game(client, app, headers, game)
    assert item["status"] == "draft" and item["can_manage"] and not item["can_approve"] and "spec" not in item
    assert client.get(course_path(resource=item["id"])).json()["spec"] == SPEC
    assert import_game(client, app, headers, game) == item and len(calls) == 2
    assert records(app, ["course", "teacher_profile", "teacher_profile_revision", "teacher_model_config", "course_corpus", "corpus_approval", "enrollment", "school_membership"]) == before
    assert client.post(course_path(resource=item["id"], action="approve"), json={"expected_version": 1, "reviewed_level_ids": ["level-1"], "reviewed": True}, headers=headers).status_code == 403
    login(client)
    assert client.get(course_path()).json()["games"] == []


def test_human_approved_game_plays_locally_after_fresh_publication_check_and_revocation_withdraws(client, app, monkeypatch):
    headers, game, calls = setup(client, app, monkeypatch)
    item = approve(client, headers, import_game(client, app, headers, game))
    assert item["status"] == "approved" and item["spec"] == SPEC
    student = login(client)
    listing = client.get(course_path()).json()["games"]
    assert len(listing) == 1 and "spec" not in listing[0] and not listing[0]["can_manage"]
    assert client.get(course_path(resource=item["id"])).status_code == 403
    played = client.post(course_path(resource=item["id"], action="play"), json={}, headers=student)
    assert played.status_code == 200, played.text
    assert played.json()["spec"] == SPEC and played.json()["runtime_contract"] == RUNTIME_CONTRACT and played.json()["preview"] is False
    assert calls[-1] == (API, "/games/published/" + GAME_ID)
    assert not any(word in canonical(played.json()) for word in ["iframe", "studio_origin", "api_origin", "leaderboard", "token"])
    headers = login(client, "demo-admin")
    revoked = client.post(course_path(resource=item["id"], action="revoke"), json={"expected_version": item["version"]}, headers=headers)
    assert revoked.status_code == 200 and revoked.json()["status"] == "revoked"
    student = login(client)
    assert client.get(course_path()).json()["games"] == []
    count = len(calls)
    assert client.post(course_path(resource=item["id"], action="play"), json={}, headers=student).status_code == 403
    assert len(calls) == count and len(records(app, ["game_publication"])) == 1


@pytest.mark.parametrize("change", ["republish", "content", "unpublished", "unavailable"])
def test_remote_game_changes_or_unavailability_fail_closed_without_returning_local_spec(client, app, monkeypatch, change):
    headers, game, _calls = setup(client, app, monkeypatch)
    item = approve(client, headers, import_game(client, app, headers, game))
    if change == "republish": game["revision"] = game["publishedRevision"] = 3
    if change == "content": game["spec"]["title"] = "Changed edition"
    if change == "unpublished": game["status"] = "Draft"
    def fetch(*_):
        if change == "unavailable": raise GameStudioFailure("upstream-secret-error")
        return game
    monkeypatch.setattr(app.state, "game_studio_fetch", fetch)
    student = login(client)
    response = client.post(course_path(resource=item["id"], action="play"), json={}, headers=student)
    assert response.status_code in {409, 502} and "spec" not in response.json()
    assert "upstream-secret-error" not in response.text
    if change in {"republish", "content"}: assert client.get(course_path()).json()["games"] == []


@pytest.mark.parametrize("change", ["course", "connection", "membership", "session", "approval"])
def test_inflight_revalidation_blocks_stale_play_and_keeps_approval_history(client, app, monkeypatch, change):
    headers, game, _calls = setup(client, app, monkeypatch)
    item = approve(client, headers, import_game(client, app, headers, game)); student = login(client)
    def fetch(*_):
        with app.state.database.write() as db:
            if change == "course":
                target = db.get(Record, "demo-course"); target.set({**target.value, "title": "Changed curriculum context"})
            elif change == "connection":
                from academy.game_studio import configuration_id
                target = db.get(Record, configuration_id(app.state.settings.tenant_id, default_school_id(app.state.settings.tenant_id))); target.set({**target.value, "enabled": False})
            elif change == "membership":
                target = db.scalar(select(Record).where(Record.kind == "enrollment", Record.parent_id == "demo-course", Record.owner_id == "demo-student")); db.delete(target)
            elif change == "session":
                from academy.db import Login
                target = db.scalar(select(Login).where(Login.user_id == "demo-student")); target.expires = 0
            else:
                target = db.get(Record, item["id"]); target.set({**target.value, "revoked": True})
        return deepcopy(game)
    monkeypatch.setattr(app.state, "game_studio_fetch", fetch)
    result = client.post(course_path(resource=item["id"], action="play"), json={}, headers=student)
    assert result.status_code in {401, 403, 409} and "spec" not in result.json()
    assert len(records(app, ["game_publication"])) == 1


def test_exact_all_level_human_review_and_optimistic_approval_are_required(client, app, monkeypatch):
    spec = {**deepcopy(SPEC), "version": 2, "progression": "Practice then apply.", "levels": [{"id": f"level-{index}", "title": f"Practice {index}", "brief": "Collect stars carefully.", "theme": "pond", "rules": deepcopy(SPEC["rules"]), "seed": index} for index in (1, 2)]}
    headers, game, _calls = setup(client, app, monkeypatch, spec=spec); item = import_game(client, app, headers, game)
    path = course_path(resource=item["id"], action="approve")
    assert client.post(path, json={"expected_version": 1, "reviewed_level_ids": ["level-1"], "reviewed": True}, headers=headers).status_code == 409
    assert client.post(path, json={"expected_version": 1, "reviewed_level_ids": item["level_ids"], "reviewed": False}, headers=headers).status_code == 422
    approved = approve(client, headers, item)
    assert approved["version"] == 2
    assert client.post(path, json={"expected_version": 1, "reviewed_level_ids": item["level_ids"], "reviewed": True}, headers=headers).status_code == 409


@pytest.mark.parametrize("operation", ["verify", "catalog", "import", "play"])
def test_fresh_auth_before_public_read_marker_prevents_any_request_after_session_revocation(client, app, monkeypatch, operation):
    headers, game, calls = setup(client, app, monkeypatch)
    item = approve(client, headers, import_game(client, app, headers, game)) if operation == "play" else None
    count = len(calls); actual_actor = app.state.auth.actor; auth_checks = 0
    def revoked_before_marker(request):
        nonlocal auth_checks
        auth_checks += 1
        if auth_checks > 1:
            from fastapi import HTTPException
            raise HTTPException(401, "Synthetic expired session")
        return actual_actor(request)
    monkeypatch.setattr(app.state.auth, "actor", revoked_before_marker)
    if operation == "verify": response = client.post(base(app) + "/verify", json={"expected_version": 1}, headers=headers)
    elif operation == "catalog": response = client.get(base(app) + "/catalog")
    elif operation == "play": response = client.post(course_path(resource=item["id"], action="play"), json={}, headers=headers)
    else: response = client.post(course_path(), json={"game_id": GAME_ID, "published_revision": 2, "spec_sha256": digest(SPEC), "learning_objective": "Practice a careful strategy", "idempotency_key": "synthetic-revoked-import-key"}, headers=headers)
    assert response.status_code == 401 and len(calls) == count


@pytest.mark.parametrize("field", ["learning_objective", "lesson_label", "curriculum", "connection"])
def test_current_teaching_metadata_course_and_connection_revision_are_bound_to_human_approval(client, app, monkeypatch, field):
    headers, game, calls = setup(client, app, monkeypatch)
    item = approve(client, headers, import_game(client, app, headers, game))
    with app.state.database.write() as db:
        if field in {"learning_objective", "lesson_label"}:
            row = db.get(Record, item["id"]); row.set({**row.value, field: "Changed after the human review"})
        elif field == "curriculum":
            row = db.get(Record, "demo-course"); row.set({**row.value, "curriculum": {"grade": "4", "subject": "mathematics"}})
    if field == "connection":
        response = client.put(base(app), json={"expected_version": 1, "enabled": True, "studio_origin": STUDIO, "api_origin": API, "host_id": "academy"}, headers=headers)
        assert response.status_code == 200
    student = login(client); count = len(calls)
    assert client.get(course_path()).json()["games"] == []
    assert client.post(course_path(resource=item["id"], action="play"), json={}, headers=student).status_code == 403
    assert len(calls) == count and len(records(app, ["game_publication"])) == 1


def test_revoked_student_cannot_mark_a_source_stale_after_remote_result_changes(client, app, monkeypatch):
    headers, game, _calls = setup(client, app, monkeypatch)
    item = approve(client, headers, import_game(client, app, headers, game)); student = login(client)
    def changed_and_revoked(*_):
        with app.state.database.write() as db:
            enrollment = db.scalar(select(Record).where(Record.kind == "enrollment", Record.parent_id == "demo-course", Record.owner_id == "demo-student"))
            db.delete(enrollment)
        remote_game = deepcopy(game); remote_game["revision"] = remote_game["publishedRevision"] = 3
        return remote_game
    monkeypatch.setattr(app.state, "game_studio_fetch", changed_and_revoked)
    response = client.post(course_path(resource=item["id"], action="play"), json={}, headers=student)
    assert response.status_code == 403
    with app.state.database.factory() as db: assert db.get(Record, item["id"]).value["source_stale"] is False


def test_import_rejects_stale_game_or_inflight_class_change_without_partial_packages(client, app, monkeypatch):
    headers, game, _calls = setup(client, app, monkeypatch)
    before = records(app, ["game_package", "course_game"])
    game["revision"] = game["publishedRevision"] = 3
    body = {"game_id": GAME_ID, "published_revision": 2, "spec_sha256": digest(SPEC), "learning_objective": "Practice a careful strategy", "idempotency_key": "synthetic-stale-import-key"}
    assert client.post(course_path(), json=body, headers=headers).status_code == 409
    assert records(app, ["game_package", "course_game"]) == before
    def changed(*_):
        with app.state.database.write() as db:
            course = db.get(Record, "demo-course"); course.set({**course.value, "title": "Changed while loading public game"})
        return published()
    monkeypatch.setattr(app.state, "game_studio_fetch", changed)
    assert client.post(course_path(), json=body, headers=headers).status_code == 409
    assert records(app, ["game_package", "course_game"]) == before


def test_idempotency_key_cannot_overwrite_snapshot_or_send_secret_fields(client, app, monkeypatch):
    headers, game, calls = setup(client, app, monkeypatch); item = import_game(client, app, headers, game)
    body = {"game_id": GAME_ID, "published_revision": 2, "spec_sha256": digest(SPEC), "learning_objective": "A different objective", "idempotency_key": "synthetic-game-import-0001"}
    count = len(calls); evidence = records(app, ["game_package", "course_game"])
    assert client.post(course_path(), json=body, headers=headers).status_code == 409
    body["student_name"] = "Private learner information"
    assert client.post(course_path(), json=body, headers=headers).status_code == 422
    assert client.post(course_path(resource=item["id"], action="play"), json={"token": "Do not forward this"}, headers=headers).status_code == 422
    assert client.put(base(app), json={"expected_version": 1, "api_key": "No keys are supported"}, headers=headers).status_code == 422
    assert len(calls) == count and records(app, ["game_package", "course_game"]) == evidence


@pytest.mark.parametrize("origin", ["http://games.example.test", "https://127.0.0.1", "https://games.example.test/path", "https://user:password@games.example.test", "https://games.example.test?token=secret"])
def test_api_settings_reject_non_public_origins_without_network(client, app, monkeypatch, origin):
    headers = login(client, "demo-admin")
    monkeypatch.setattr(app.state, "game_studio_fetch", lambda *_: pytest.fail("Invalid settings cannot initiate public reads"))
    response = client.put(base(app), json={"expected_version": 0, "enabled": True, "api_origin": origin, "studio_origin": STUDIO}, headers=headers)
    assert response.status_code == 422
    assert client.get(base(app)).json()["version"] == 0


def test_catalog_cursor_and_untrusted_return_mapping_are_fail_closed(client, app, monkeypatch):
    headers, game, calls = setup(client, app, monkeypatch)
    assert client.get(base(app) + "/catalog", params={"cursor": "https://private.test/?credential=x"}).status_code == 422
    assert calls == []
    monkeypatch.setattr(app.state, "game_studio_fetch", lambda *_: {"games": [game], "cursor": "https://private.test/?credential=x"})
    assert client.get(base(app) + "/catalog").status_code == 502
    monkeypatch.setattr(app.state, "game_studio_fetch", lambda *_: {"version": 1, "routes": ROUTES, "hosts": [{"id": "academy", "name": "Untrusted return", "origin": "https://untrusted.example.test", "returnPath": "/"}]})
    result = client.post(base(app) + "/verify", json={"expected_version": 1}, headers=headers)
    assert result.status_code == 200 and result.json()["creator_url"] == STUDIO + "/game-studio" and not result.json()["host_configured"]


@pytest.mark.parametrize("malformed", ["extra", "private", "html", "url", "bool_integer", "arcade", "unicode", "duplicate_levels"])
def test_strict_public_game_validation_rejects_code_private_data_and_invalid_mechanics(malformed):
    game = published()
    if malformed == "extra": game["spec"]["code"] = "eval('bad')"
    if malformed == "private": game["messages"] = [{"text": "private authoring conversation"}]
    if malformed == "html": game["spec"]["title"] = "<script>bad</script>"
    if malformed == "url": game["spec"]["instructions"] = "Open https://example.test"
    if malformed == "bool_integer": game["spec"]["rules"]["goal"] = True
    if malformed == "arcade": game["spec"]["arcade"] = {"rotation": True}
    if malformed == "unicode": game["spec"]["title"] = "\ud800"
    if malformed == "duplicate_levels": game["spec"].update(version=2, progression="Progress", levels=[{"id": "level-1", "title": "One", "brief": "Practice", "theme": "pond", "rules": SPEC["rules"], "seed": 1}] * 2)
    with pytest.raises(Exception) as caught: validate_public_game(game)
    assert getattr(caught.value, "status_code", None) == 502


def test_school_tenant_student_and_agent_boundaries_do_not_grant_upstream_accounts(client, app, monkeypatch):
    headers, game, calls = setup(client, app, monkeypatch); item = import_game(client, app, headers, game); other = new_school(client, headers)
    foreign_course = client.post("/api/courses", json={"school_id": other, "title": "Other school class"}, headers=headers).json()
    assert client.get(course_path(foreign_course["id"], item["id"])).status_code == 404
    login(client, "demo-school")
    assert client.get(base(app, other)).status_code == 403
    student = login(client, "demo-student2")
    for suffix in ["", "/catalog"]: assert client.get(base(app) + suffix).status_code == 403
    assert client.post(course_path(), json={"game_id": GAME_ID}, headers=student).status_code == 422
    identity = {"id": "agent:synthetic", "role": "admin", "tenant_id": app.state.settings.tenant_id, "principal_kind": "agent", "school_id": default_school_id(app.state.settings.tenant_id), "permissions": []}
    monkeypatch.setattr(app.state.auth, "actor", lambda request: dict(identity))
    assert client.get(base(app)).status_code == 403 and client.get(course_path()).status_code == 403
    assert len(calls) == 1


def test_platform_local_student_can_play_without_external_identity_and_audit_is_encrypted(client, app, monkeypatch):
    headers, game, calls = setup(client, app, monkeypatch); item = approve(client, headers, import_game(client, app, headers, game))
    family = client.post("/api/admin/family-accounts", json={"kind": "student", "username": "game-learner", "password": "Synthetic game family password!", "name": "Protected Game Learner", "must_change_password": False, "school_id": default_school_id(app.state.settings.tenant_id), "academic_year": "2026-2027", "grade": "3", "course_ids": ["demo-course"]}, headers=headers)
    assert family.status_code == 201, family.text
    signed = client.post("/api/auth/password", json={"username": "game-learner", "password": "Synthetic game family password!"}, headers={"Origin": app.state.settings.app_url})
    assert signed.status_code == 200, signed.text
    student = {"Origin": app.state.settings.app_url, "X-CSRF-Token": signed.json()["csrf_token"]}
    response = client.post(course_path(resource=item["id"], action="play"), json={}, headers=student)
    assert response.status_code == 200, response.text
    assert calls[-1] == (API, "/games/published/" + GAME_ID)
    with app.state.database.factory() as db:
        events = list(db.scalars(select(Audit).where(Audit.actor_id == family.json()["id"], Audit.event_type.like("game_studio.%"))))
        assert len(events) >= 3 and all("__academy_pii__" in json.loads(event.payload) for event in events)
        assert "Protected Game Learner" not in canonical([(row.data,) for row in db.scalars(select(Record))])
    assert "Protected Game Learner" not in canonical(calls) and family.json()["id"] not in canonical(calls)


def test_schema14_preserves_evidence_requires_marker_and_protects_every_new_immutable_kind(client, app):
    before = records(app); migrate(app.state.settings); assert records(app) == before
    with app.state.database.write() as db:
        db.delete(db.get(Record, game_studio_schema_id(app.state.settings.tenant_id)))
    assert client.get("/api/ready").status_code == 503
    migrate(app.state.settings); assert client.get("/api/ready").status_code == 200
    for kind in ("game_studio_revision", "game_package", "game_publication"):
        key = "synthetic-game-protected-" + kind
        with app.state.database.write() as db:
            db.add(Record(id=key, tenant_id=app.state.settings.tenant_id, kind=kind, data="{}"))
        for command in ("UPDATE records SET data='{}' WHERE id=:id", "UPDATE records SET kind='course' WHERE id=:id", "DELETE FROM records WHERE id=:id"):
            with pytest.raises(DatabaseError), app.state.database.write() as db: db.execute(text(command), {"id": key})
    runtime = Path(__file__).resolve().parents[2] / "web/src/game-studio-runtime/runtime-build.ts"
    assert RUNTIME_CONTRACT["sha256"] in runtime.read_text()
