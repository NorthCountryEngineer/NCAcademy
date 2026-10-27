"""Public Game Studio data imported into a fixed local, reviewed class player.

No external iframe, account federation, authoring prompt, score submission,
learner identity or learner content crosses this integration boundary.
"""
from datetime import datetime
import re
from urllib.parse import urlencode

from fastapi import Depends, HTTPException, Query, Request
from sqlalchemy import select

from .db import Record, canonical, now, uid
from .game_spec import GameError, validate_spec, identifier
from .game_studio_schemas import CourseGameInput, GameApproval, GamePlay, GameStudioSettings, GameStudioVersion, GameScoringInput, GameAttemptStart, GameAttemptResult, GameAttemptAward
from .game_scoring import scoring_policy, calculate_points, select_attempt, validate_award
from .game_studio_transport import validate_origin
from .permissions import bind_scope, normalized_role, require_school, scope_id
from .standards import digest


RUNTIME_CONTRACT = {"id": "game-studio-data-v2", "sha256": "8a95fdf521faa262ff8ef2b59d5311dac1c556713e679404621cb2d1324cd176"}
CONFIG_FIELDS = ("enabled", "studio_origin", "api_origin", "host_id")
PUBLIC_GAME_FIELDS = {"id", "spec", "revision", "status", "publishedRevision", "createdAt", "updatedAt", "publishedAt"}
ROUTES = {"games": "/games", "game": "/games/{gameId}", "embed": "/embed/games/{gameId}", "studio": "/game-studio"}


def configuration_id(tenant, school):
    return "game-studio:" + digest([tenant, school])[:48]


def _find(db, tenant, school, key, kind):
    row = db.get(Record, key)
    if not row or row.tenant_id != tenant or row.kind != kind or scope_id(db, row) != school:
        raise HTTPException(404, "Game Studio record not found in this school")
    return row


def _add(db, identity, school, kind, value, parent, key=None):
    row = Record(id=key or uid(), tenant_id=identity["tenant_id"], kind=kind, parent_id=parent, owner_id=identity["id"], data=canonical(value))
    db.add(row); db.flush(); bind_scope(db, row, school)
    return row


def _connection(db, identity, school, enabled=True, require_origins=True):
    row = _find(db, identity["tenant_id"], school, configuration_id(identity["tenant_id"], school), "game_studio_connection")
    value = row.value; config = {key: value[key] for key in CONFIG_FIELDS}
    revision = _find(db, identity["tenant_id"], school, value.get("revision_id", ""), "game_studio_revision")
    if revision.parent_id != row.id or revision.value.get("configuration") != config or revision.value.get("sha256") != digest(config) or revision.value.get("version") != value.get("version"):
        raise HTTPException(409, "Game Studio settings do not match their immutable revision")
    if enabled and not config["enabled"]:
        raise HTTPException(409, "Game Studio is disabled for this school")
    if require_origins and (not config["studio_origin"] or not config["api_origin"]):
        raise HTTPException(409, "Configure the Game Studio public origins first")
    for key in ("studio_origin", "api_origin"):
        if not config[key] and not require_origins:
            continue
        if validate_origin(config[key]) != config[key]:
            raise HTTPException(409, "Game Studio origin integrity check failed")
    return row, {**config, "version": value["version"], "revision_id": revision.id}


def _levels(spec):
    return [level["id"] for level in spec["levels"]] if spec["version"] == 2 else ["level-1"]


def validate_public_game(value):
    """Accept the published data envelope only, never private drafts or code."""
    try:
        if not isinstance(value, dict) or set(value) != PUBLIC_GAME_FIELDS or value["status"] != "Published" or type(value["revision"]) is not int or not 1 <= value["revision"] <= 1000000000 or type(value["publishedRevision"]) is not int or value["publishedRevision"] != value["revision"]:
            raise ValueError()
        identifier(value["id"])
        for key in ("createdAt", "updatedAt", "publishedAt"):
            timestamp = value[key]
            if not isinstance(timestamp, str) or not 1 <= len(timestamp) <= 40 or datetime.fromisoformat(timestamp).tzinfo is None:
                raise ValueError()
        spec = validate_spec(value["spec"])
        if len(canonical(spec).encode()) > 32000:
            raise ValueError()
    except (ValueError, TypeError, KeyError, GameError, OverflowError, RecursionError):
        raise HTTPException(502, "Game Studio returned an unsupported or invalid published game") from None
    return {**value, "spec": spec}


def _game_descriptor(game):
    spec = game["spec"]
    return {"game_id": game["id"], "published_revision": game["publishedRevision"], "title": spec["title"], "description": spec["description"], "genre": spec["genre"], "theme": spec["theme"], "spec_sha256": digest(spec), "level_ids": _levels(spec)}


def _course_context(course):
    return {"id": course.id, **{key: course.value.get(key) for key in ("title", "description", "curriculum", "course_catalog")}}


def _teaching_context(resource):
    result = {key: resource.value.get(key) for key in ("learning_objective", "lesson_label")}
    if resource.value.get("scoring_policy") is not None:
        result.update(scoring_policy=resource.value["scoring_policy"], scoring_revision_id=resource.value.get("scoring_revision_id"))
    return result


def _scoring(db, identity, school, resource, spec):
    policy = scoring_policy(resource.value.get("scoring_policy"), spec)
    if policy is not None:
        revision = _find(db, identity["tenant_id"], school, resource.value.get("scoring_revision_id", ""), "game_scoring_revision")
        if revision.parent_id != resource.id or revision.value.get("scoring_policy") != policy or revision.value.get("sha256") != digest(policy):
            raise HTTPException(409, "Assignment scoring does not match its immutable policy revision")
    return policy


def _package(db, identity, school, resource):
    row = _find(db, identity["tenant_id"], school, resource.value.get("package_id", ""), "game_package")
    value = row.value; material = {key: item for key, item in value.items() if key != "package_sha256"}
    if row.parent_id != resource.id or value.get("package_sha256") != digest(material):
        raise HTTPException(409, "Imported game package integrity check failed")
    game = validate_public_game(value["game"])
    if value.get("spec_sha256") != digest(game["spec"]):
        raise HTTPException(409, "Imported game definition integrity check failed")
    return row


def _approval(db, identity, school, course, resource, package, config):
    value = resource.value
    if value.get("revoked") or value.get("source_stale") or not config or not config["enabled"]:
        return None
    key = value.get("approval_id")
    if not key:
        return None
    try:
        approval = _find(db, identity["tenant_id"], school, key, "game_publication")
        proof = approval.value
        if approval.parent_id != resource.id or proof.get("resource_version") != value["version"] or proof.get("package_id") != package.id or proof.get("package_sha256") != package.value["package_sha256"] or proof.get("connection_revision_id") != config["revision_id"] or proof.get("course_context") != _course_context(course) or proof.get("teaching_context") != _teaching_context(resource) or proof.get("runtime_contract") != RUNTIME_CONTRACT or proof.get("reviewed_level_ids") != _levels(package.value["game"]["spec"]) or proof.get("reviewed") is not True:
            return None
        if any(package.value.get(key) != config[key] for key in ("studio_origin", "api_origin")):
            return None
        return approval
    except HTTPException:
        return None


def register_game_studio_api(app, database, auth, settings, actor, audit_actor):
    def school_access(db, identity, school, manager=False):
        return require_school(db, identity, school, settings, {"admin", "school"} if manager else {"admin", "school", "teacher"}, human=True)

    def course_access(db, identity, course_id, staff=False):
        course = db.get(Record, course_id)
        if not course or course.kind != "course" or course.tenant_id != identity["tenant_id"]:
            raise HTTPException(404, "Course not found")
        school = scope_id(db, course)
        role = require_school(db, identity, school, settings, {"admin", "school", "teacher"} if staff else {"admin", "school", "teacher", "student"}, human=True)
        if role in {"teacher", "student"}:
            enrolled = db.scalar(select(Record).where(Record.tenant_id == identity["tenant_id"], Record.kind == "enrollment", Record.parent_id == course.id, Record.owner_id == identity["id"]))
            if not (role == "teacher" and course.owner_id == identity["id"] or enrolled and normalized_role(enrolled.value.get("role")) == role):
                raise HTTPException(403, "Current course membership is required")
        return course, school, role

    def fresh(request, identity):
        current = auth.actor(request)
        if any(current.get(key) != identity.get(key) for key in ("id", "tenant_id", "login_id", "principal_kind")):
            raise HTTPException(403, "Your session changed; reload before continuing")
        return current

    def remote(request, identity, school, config, path, operation, course_id=None, resource_id=None, context=None, resource_version=None):
        payload = {"school_id": school, "course_id": course_id, "resource_id": resource_id, "connection_revision_id": config["revision_id"], "operation": operation, "public_data_only": True}
        current = fresh(request, identity)
        with database.write() as db:
            # Recheck under the same write admission that commits the external
            # read marker. A stale screen/session cannot initiate a new request.
            if course_id:
                course, current_school, role = course_access(db, current, course_id, staff=operation == "import")
                if current_school != school or _course_context(course) != context:
                    raise HTTPException(409, "Class context changed before the public Game Studio request")
                if resource_id:
                    row, package = resource(db, current, course, school, resource_id)
                    if row.value["version"] != resource_version or role == "student" and not _approval(db, current, school, course, row, package, config):
                        raise HTTPException(409, "Practice game approval changed before the public Game Studio request")
            else:
                school_access(db, current, school, manager=operation == "verify")
            _row, latest = _connection(db, current, school, enabled=operation != "verify")
            if latest != config:
                raise HTTPException(409, "Game Studio settings changed before the public request")
            audit_actor(db, current, "game_studio.public_read_started", payload)
        try:
            result = app.state.game_studio_fetch(config["api_origin"], path)
        except Exception:
            with database.write() as db:
                audit_actor(db, identity, "game_studio.public_read_failed", payload)
            raise HTTPException(502, "Game Studio public data is unavailable; no automatic retry was made") from None
        with database.write() as db:
            audit_actor(db, identity, "game_studio.public_read_completed", payload)
        return result

    def settings_descriptor(db, identity, school, role):
        row = db.get(Record, configuration_id(identity["tenant_id"], school))
        manager = role in {"admin", "school"}
        result = {"school_id": school, "version": 0, "enabled": False, "studio_origin": "", "api_origin": "", "host_id": "academy", "revision_id": None, "can_manage": manager, "can_browse": True, "creator_url": None, "host_configured": False, "creator_ready": False, "runtime_contract": RUNTIME_CONTRACT}
        if row:
            _found, config = _connection(db, identity, school, enabled=False, require_origins=False)
            result.update(config)
            verified = row.value.get("verification") or {}
            if config["enabled"] and manager and verified.get("connection_revision_id") == config["revision_id"]:
                result.update(creator_ready=True, host_configured=bool(verified.get("host_configured")))
                result["creator_url"] = config["studio_origin"] + "/game-studio" + ("?" + urlencode({"host": config["host_id"]}) if result["host_configured"] else "")
        return result

    def resource(db, identity, course, school, identifier):
        row = _find(db, identity["tenant_id"], school, identifier, "course_game")
        if row.parent_id != course.id:
            raise HTTPException(404, "Practice game not found in this course")
        return row, _package(db, identity, school, row)

    def describe(db, identity, course, school, role, row, package, full=False):
        config = None
        try:
            _record, config = _connection(db, identity, school)
        except HTTPException:
            pass
        publication = _approval(db, identity, school, course, row, package, config)
        manager = role in {"admin", "school"}; staff = role != "student"
        status = "revoked" if row.value.get("revoked") else "approved" if publication else "stale" if row.value.get("approval_id") or row.value.get("source_stale") else "draft"
        if role == "student" and not publication:
            raise HTTPException(404, "Approved practice game not found")
        result = {"id": row.id, "course_id": course.id, "school_id": school, "version": row.value["version"], "status": status, **_game_descriptor(package.value["game"]), "learning_objective": row.value["learning_objective"], "lesson_label": row.value["lesson_label"], "approval_id": publication.id if publication else None, "can_manage": staff, "can_approve": bool(manager and config), "can_revoke": manager and bool(row.value.get("approval_id")) and not row.value.get("revoked"), "can_play": bool(config and (staff or publication)), "runtime_contract": RUNTIME_CONTRACT}
        result.update(scoring_policy=_scoring(db, identity, school, row, package.value["game"]["spec"]), can_configure_scoring=manager)
        if full and staff:
            result["spec"] = package.value["game"]["spec"]
        return result

    @app.get("/api/schools/{school_id}/game-studio")
    def get_settings(school_id: str, identity=Depends(actor)):
        with database.factory() as db:
            role = school_access(db, identity, school_id)
            return settings_descriptor(db, identity, school_id, role)

    @app.put("/api/schools/{school_id}/game-studio")
    def put_settings(school_id: str, body: GameStudioSettings, identity=Depends(actor)):
        with database.write() as db:
            role = school_access(db, identity, school_id, True)
            row = db.get(Record, configuration_id(identity["tenant_id"], school_id))
            if row:
                row = _find(db, identity["tenant_id"], school_id, row.id, "game_studio_connection")
            version = row.value["version"] if row else 0
            if body.expected_version != version:
                raise HTTPException(409, "Game Studio settings changed; reload before saving")
            config = body.model_dump(exclude={"expected_version"})
            if row is None:
                row = _add(db, identity, school_id, "game_studio_connection", {}, school_id, configuration_id(identity["tenant_id"], school_id))
            revision = _add(db, identity, school_id, "game_studio_revision", {"configuration": config, "version": version + 1, "sha256": digest(config), "saved_at": now(), "saved_by": identity["id"]}, row.id)
            row.set({**config, "version": version + 1, "revision_id": revision.id, "verification": None})
            audit_actor(db, identity, "game_studio.connection_saved", {"school_id": school_id, "revision_id": revision.id, "version": version + 1, "enabled": config["enabled"]})
            # Disabled blank configuration is a valid saved starting point.
            if not config["studio_origin"] or not config["api_origin"]:
                return {"school_id": school_id, **row.value, "can_manage": True, "can_browse": True, "creator_url": None, "host_configured": False, "creator_ready": False, "runtime_contract": RUNTIME_CONTRACT}
            return settings_descriptor(db, identity, school_id, role)

    @app.post("/api/schools/{school_id}/game-studio/verify")
    def verify(school_id: str, body: GameStudioVersion, request: Request, identity=Depends(actor)):
        with database.factory() as db:
            school_access(db, identity, school_id, True); _row, config = _connection(db, identity, school_id, False)
            if body.expected_version != config["version"]:
                raise HTTPException(409, "Game Studio settings changed; reload before verifying")
        raw = remote(request, identity, school_id, config, "/integration", "verify")
        if not isinstance(raw, dict) or set(raw) - {"version", "revision", "branding", "hosts", "routes"} or type(raw.get("version")) is not int or raw["version"] != 1 or raw.get("routes") != ROUTES or not isinstance(raw.get("hosts"), list) or len(raw["hosts"]) > 20:
            raise HTTPException(502, "Game Studio returned an unsupported integration contract")
        matches = [host for host in raw["hosts"] if isinstance(host, dict) and host.get("id") == config["host_id"]]
        mapped = len(matches) == 1 and set(matches[0]) == {"id", "name", "origin", "returnPath"} and matches[0]["origin"] == settings.app_url and matches[0]["returnPath"] == "/"
        current = fresh(request, identity)
        with database.write() as db:
            school_access(db, current, school_id, True); row, latest = _connection(db, current, school_id, False)
            if latest != config:
                raise HTTPException(409, "Game Studio settings changed during verification")
            row.set({**row.value, "verification": {"connection_revision_id": config["revision_id"], "host_configured": mapped, "verified_at": now(), "integration_sha256": digest(raw)}})
            audit_actor(db, current, "game_studio.connection_verified", {"school_id": school_id, "revision_id": config["revision_id"], "host_configured": mapped, "integration_sha256": digest(raw)})
        creator = config["studio_origin"] + "/game-studio" + ("?" + urlencode({"host": config["host_id"]}) if mapped else "")
        return {"verified": True, "creator_url": creator, "creator_ready": True, "public_catalog_ready": True, "host_configured": mapped, "return_path": "/" if mapped else None, "connection_version": config["version"], "detail": "Game Studio creator and Academy return are configured." if mapped else "The public catalog is available. Open Game Studio separately, then return to Academy and import the published game manually; the Academy return host is not configured."}

    @app.get("/api/schools/{school_id}/game-studio/catalog")
    def catalog(school_id: str, request: Request, cursor: str | None = Query(default=None, max_length=1000, pattern=r"^[A-Za-z0-9_=-]+$"), identity=Depends(actor)):
        with database.factory() as db:
            school_access(db, identity, school_id); _row, config = _connection(db, identity, school_id)
        raw = remote(request, identity, school_id, config, "/games/published" + ("?" + urlencode({"cursor": cursor}) if cursor else ""), "catalog")
        if not isinstance(raw, dict) or set(raw) - {"games", "cursor"} or not isinstance(raw.get("games"), list) or len(raw["games"]) > 50 or raw.get("cursor") is not None and (not isinstance(raw["cursor"], str) or not 1 <= len(raw["cursor"]) <= 1000 or not re.fullmatch(r"[A-Za-z0-9_=-]+", raw["cursor"])):
            raise HTTPException(502, "Game Studio returned an invalid catalog page")
        games = [_game_descriptor(validate_public_game(game)) for game in raw["games"]]
        current = fresh(request, identity)
        with database.factory() as db:
            school_access(db, current, school_id); _row, latest = _connection(db, current, school_id)
            if config != latest:
                raise HTTPException(409, "Game Studio settings changed during catalog loading")
        return {"games": games, "cursor": raw.get("cursor"), "connection_version": config["version"]}

    @app.get("/api/courses/{course_id}/games")
    def list_games(course_id: str, identity=Depends(actor)):
        with database.factory() as db:
            course, school, role = course_access(db, identity, course_id)
            games = []
            for row in db.scalars(select(Record).where(Record.tenant_id == identity["tenant_id"], Record.kind == "course_game", Record.parent_id == course.id).order_by(Record.created_at, Record.id)):
                if scope_id(db, row) != school:
                    continue
                try:
                    games.append(describe(db, identity, course, school, role, row, _package(db, identity, school, row)))
                except HTTPException as error:
                    if role != "student" or error.status_code != 404:
                        raise
            return {"games": games, "can_prepare": role != "student", "can_approve": role in {"admin", "school"}}

    @app.post("/api/courses/{course_id}/games", status_code=201)
    def import_game(course_id: str, body: CourseGameInput, request: Request, identity=Depends(actor)):
        key = "game-resource:" + digest([identity["tenant_id"], course_id, body.idempotency_key])[:48]
        request_material = body.model_dump(exclude={"scoring_policy"})
        if body.scoring_policy is not None:
            request_material["scoring_policy"] = body.scoring_policy.snapshot()
        request_hash = digest(request_material)
        with database.factory() as db:
            course, school, role = course_access(db, identity, course_id, True)
            existing = db.get(Record, key)
            if existing:
                row, package = resource(db, identity, course, school, key)
                if row.value.get("request_sha256") != request_hash:
                    raise HTTPException(409, "Game import key belongs to different input")
                return describe(db, identity, course, school, role, row, package)
            _row, config = _connection(db, identity, school)
            context = _course_context(course)
        game = validate_public_game(remote(request, identity, school, config, "/games/published/" + body.game_id, "import", course_id, context=context))
        if game["id"] != body.game_id or game["publishedRevision"] != body.published_revision or digest(game["spec"]) != body.spec_sha256:
            raise HTTPException(409, "The published game changed; reload the catalog before importing")
        current = fresh(request, identity)
        with database.write() as db:
            course, school, role = course_access(db, current, course_id, True); _row, latest = _connection(db, current, school)
            if latest != config or _course_context(course) != context:
                raise HTTPException(409, "Class or Game Studio settings changed during import")
            existing = db.get(Record, key)
            if existing:
                row, package = resource(db, current, course, school, key)
                if row.value.get("request_sha256") != request_hash:
                    raise HTTPException(409, "Game import key belongs to different input")
                return describe(db, current, course, school, role, row, package)
            policy = scoring_policy(body.scoring_policy.snapshot() if body.scoring_policy else None, game["spec"])
            row = _add(db, current, school, "course_game", {"version": 1, "learning_objective": body.learning_objective, "lesson_label": body.lesson_label, "request_sha256": request_hash, "approval_id": None, "revoked": False, "source_stale": False, "scoring_policy": policy}, course.id, key)
            if policy:
                revision = _add(db, current, school, "game_scoring_revision", {"scoring_policy": policy, "sha256": digest(policy), "saved_by": current["id"], "saved_at": now()}, row.id)
                row.set({**row.value, "scoring_revision_id": revision.id})
            material = {"game": game, "spec_sha256": body.spec_sha256, "studio_origin": config["studio_origin"], "api_origin": config["api_origin"], "connection_revision_id": config["revision_id"], "imported_at": now(), "source_commit": "f09860b9973b0359ea36684aad66618bf7a67bbf"}
            package = _add(db, current, school, "game_package", {**material, "package_sha256": digest(material)}, row.id)
            row.set({**row.value, "package_id": package.id})
            audit_actor(db, current, "game_studio.game_imported", {"school_id": school, "course_id": course.id, "resource_id": row.id, "package_id": package.id, "spec_sha256": body.spec_sha256, "game_id": body.game_id, "published_revision": body.published_revision})
            return describe(db, current, course, school, role, row, package)

    @app.get("/api/courses/{course_id}/games/{resource_id}")
    def detail(course_id: str, resource_id: str, identity=Depends(actor)):
        with database.factory() as db:
            course, school, role = course_access(db, identity, course_id, True)
            row, package = resource(db, identity, course, school, resource_id)
            return describe(db, identity, course, school, role, row, package, True)

    @app.post("/api/courses/{course_id}/games/{resource_id}/approve")
    def approve(course_id: str, resource_id: str, body: GameApproval, identity=Depends(actor)):
        with database.write() as db:
            course, school, role = course_access(db, identity, course_id, True); school_access(db, identity, school, True)
            row, package = resource(db, identity, course, school, resource_id); _record, config = _connection(db, identity, school)
            _scoring(db, identity, school, row, package.value["game"]["spec"])
            if row.value["version"] != body.expected_version:
                raise HTTPException(409, "Practice game changed; reload before approval")
            if set(body.reviewed_level_ids) != set(_levels(package.value["game"]["spec"])) or any(package.value[key] != config[key] for key in ("studio_origin", "api_origin")) or row.value.get("source_stale"):
                raise HTTPException(409, "Playtest every exact imported level from the current Game Studio connection before approval")
            version = row.value["version"] + 1
            publication = _add(db, identity, school, "game_publication", {"resource_version": version, "package_id": package.id, "package_sha256": package.value["package_sha256"], "connection_revision_id": config["revision_id"], "course_context": _course_context(course), "teaching_context": _teaching_context(row), "runtime_contract": RUNTIME_CONTRACT, "reviewed": True, "reviewed_level_ids": _levels(package.value["game"]["spec"]), "approved_by": identity["id"], "approved_at": now()}, row.id)
            row.set({**row.value, "version": version, "approval_id": publication.id, "revoked": False})
            audit_actor(db, identity, "game_studio.game_approved", {"school_id": school, "course_id": course.id, "resource_id": row.id, "publication_id": publication.id, "package_id": package.id, "spec_sha256": package.value["spec_sha256"], "runtime_contract": RUNTIME_CONTRACT})
            return describe(db, identity, course, school, role, row, package, True)

    @app.post("/api/courses/{course_id}/games/{resource_id}/revoke")
    def revoke(course_id: str, resource_id: str, body: GameStudioVersion, identity=Depends(actor)):
        with database.write() as db:
            course, school, role = course_access(db, identity, course_id, True); school_access(db, identity, school, True)
            row, package = resource(db, identity, course, school, resource_id)
            if row.value["version"] != body.expected_version:
                raise HTTPException(409, "Practice game changed; reload before revocation")
            row.set({**row.value, "version": row.value["version"] + 1, "revoked": True})
            audit_actor(db, identity, "game_studio.game_revoked", {"school_id": school, "course_id": course.id, "resource_id": row.id, "publication_id": row.value.get("approval_id")})
            return describe(db, identity, course, school, role, row, package)

    def play_admission(course_id, resource_id, request, identity, start=None):
        with database.factory() as db:
            course, school, role = course_access(db, identity, course_id)
            row, package = resource(db, identity, course, school, resource_id); _record, config = _connection(db, identity, school)
            publication = _approval(db, identity, school, course, row, package, config)
            if start is not None:
                if role != "student":
                    raise HTTPException(403, "Only enrolled students may start assignment attempts; staff use preview")
                policy = _scoring(db, identity, school, row, package.value["game"]["spec"])
                if policy is None:
                    raise HTTPException(409, "This is an unscored practice game")
                attempt_key = "game-attempt:" + digest([identity["tenant_id"], identity["id"], row.id, start.idempotency_key])[:48]
                series = row.value.get("scoring_revision_id")
                existing = db.get(Record, attempt_key)
                attempts = list(db.scalars(select(Record).where(Record.tenant_id == identity["tenant_id"], Record.kind == "game_attempt", Record.parent_id == resource_id, Record.owner_id == identity["id"])))
                if existing and (existing.value.get("scoring_revision_id") != series or existing.value.get("resource_version") != row.value["version"]):
                    raise HTTPException(409, "Attempt key belongs to an earlier assignment publication")
                if not existing and sum(item.value.get("scoring_revision_id") == series for item in attempts) >= policy["max_attempts"]:
                    raise HTTPException(409, "This assignment's attempt limit has been reached")
            if role == "student" and not publication:
                raise HTTPException(403, "A current human-approved practice game is required")
            wanted = package.value; version = row.value["version"]; context = _course_context(course)
            if any(wanted[key] != config[key] for key in ("studio_origin", "api_origin")):
                raise HTTPException(409, "This imported game belongs to another Game Studio connection")
        remote_game = validate_public_game(remote(request, identity, school, config, "/games/published/" + wanted["game"]["id"], "play", course_id, resource_id, context, version))
        if remote_game["id"] != wanted["game"]["id"] or remote_game["publishedRevision"] != wanted["game"]["publishedRevision"] or digest(remote_game["spec"]) != wanted["spec_sha256"]:
            current = fresh(request, identity)
            with database.write() as db:
                course, school, _role = course_access(db, current, course_id)
                target, _package_row = resource(db, current, course, school, resource_id)
                _record, latest = _connection(db, current, school)
                if latest != config or target.value["version"] != version or _course_context(course) != context:
                    raise HTTPException(409, "Class or Game Studio settings changed during the public check")
                target.set({**target.value, "source_stale": True})
                audit_actor(db, current, "game_studio.source_changed", {"school_id": school, "course_id": course_id, "resource_id": resource_id})
            raise HTTPException(409, "The published game changed. A teacher must import and review its new edition")
        current = fresh(request, identity)
        with database.write() as db:
            course, school, role = course_access(db, current, course_id); row, package = resource(db, current, course, school, resource_id); _record, latest = _connection(db, current, school)
            approval = _approval(db, current, school, course, row, package, latest)
            if latest != config or row.value["version"] != version or _course_context(course) != context or role == "student" and (not approval or approval.id != publication.id):
                raise HTTPException(409, "The practice game, approval, class or connection changed before play")
            audit_actor(db, current, "game_studio.game_play_admitted", {"school_id": school, "course_id": course.id, "resource_id": row.id, "student_id": current["id"] if role == "student" else None, "package_id": package.id, "publication_id": approval.id if approval else None, "spec_sha256": wanted["spec_sha256"], "runtime_contract": RUNTIME_CONTRACT, "preview": role != "student"})
            result = {"spec": package.value["game"]["spec"], "game_id": wanted["game"]["id"], "published_revision": wanted["game"]["publishedRevision"], "spec_sha256": wanted["spec_sha256"], "runtime_contract": RUNTIME_CONTRACT, "resource_id": row.id, "course_id": course.id, "preview": role != "student"}
            if start is not None:
                # Course row lock plus Database.write's transaction admission
                # serialize the per-student ordinal and finite attempt budget.
                db.scalar(select(Record).where(Record.id == course.id).with_for_update())
                policy = _scoring(db, current, school, row, package.value["game"]["spec"])
                existing = db.get(Record, attempt_key)
                attempts = list(db.scalars(select(Record).where(Record.tenant_id == current["tenant_id"], Record.kind == "game_attempt", Record.parent_id == row.id, Record.owner_id == current["id"])))
                same_series = [item for item in attempts if item.value.get("scoring_revision_id") == series]
                if existing:
                    if existing.tenant_id != current["tenant_id"] or existing.kind != "game_attempt" or existing.owner_id != current["id"] or existing.parent_id != row.id or existing.value.get("scoring_revision_id") != series or existing.value.get("resource_version") != version:
                        raise HTTPException(409, "Attempt key belongs to different assignment input")
                    attempt = existing
                else:
                    if len(same_series) >= policy["max_attempts"]:
                        raise HTTPException(409, "This assignment's attempt limit has been reached")
                    material = {"idempotency_key": start.idempotency_key, "course_id": course.id, "resource_id": row.id, "resource_version": version, "publication_id": approval.id, "package_id": package.id, "connection_revision_id": latest["revision_id"], "course_context": context, "game": package.value["game"], "spec_sha256": wanted["spec_sha256"], "runtime_contract": RUNTIME_CONTRACT, "scoring_policy": policy, "scoring_policy_sha256": digest(policy), "scoring_revision_id": series, "ordinal": len(same_series) + 1, "started_at": now()}
                    attempt = _add(db, current, school, "game_attempt", {**material, "snapshot_sha256": digest(material)}, row.id, attempt_key)
                    audit_actor(db, current, "game_studio.attempt_started", {"school_id": school, "course_id": course.id, "resource_id": row.id, "student_id": current["id"], "attempt_id": attempt.id, "snapshot_sha256": digest(material), "scoring_policy_sha256": digest(policy)})
                result["attempt"] = attempt_descriptor(db, current, school, attempt)
        return result

    @app.post("/api/courses/{course_id}/games/{resource_id}/play")
    def play(course_id: str, resource_id: str, body: GamePlay, request: Request, identity=Depends(actor)):
        return play_admission(course_id, resource_id, request, identity)

    @app.put("/api/courses/{course_id}/games/{resource_id}/scoring")
    def configure_scoring(course_id: str, resource_id: str, body: GameScoringInput, identity=Depends(actor)):
        with database.write() as db:
            course, school, role = course_access(db, identity, course_id, True); school_access(db, identity, school, True)
            row, package = resource(db, identity, course, school, resource_id)
            if row.value["version"] != body.expected_version:
                raise HTTPException(409, "Game changed; reload before changing assignment points")
            policy = scoring_policy(body.scoring_policy.snapshot() if body.scoring_policy else None, package.value["game"]["spec"])
            revision = _add(db, identity, school, "game_scoring_revision", {"scoring_policy": policy, "sha256": digest(policy), "saved_by": identity["id"], "saved_at": now()}, row.id)
            row.set({**row.value, "version": body.expected_version + 1, "scoring_policy": policy, "scoring_revision_id": revision.id, "approval_id": None})
            audit_actor(db, identity, "game_studio.scoring_configured", {"school_id": school, "course_id": course.id, "resource_id": row.id, "scoring_revision_id": revision.id, "scoring_policy_sha256": digest(policy), "human_reapproval_required": True})
            return describe(db, identity, course, school, role, row, package, True)

    def attempt_row(db, identity, school, resource_id, attempt_id):
        attempt = _find(db, identity["tenant_id"], school, attempt_id, "game_attempt")
        value = attempt.value
        if attempt.parent_id != resource_id or value.get("resource_id") != resource_id or value.get("snapshot_sha256") != digest({key: item for key, item in value.items() if key != "snapshot_sha256"}):
            raise HTTPException(409, "Assignment attempt snapshot integrity check failed")
        if value.get("spec_sha256") != digest(value.get("game", {}).get("spec")) or value.get("scoring_policy_sha256") != digest(value.get("scoring_policy")):
            raise HTTPException(409, "Assignment attempt game or policy integrity check failed")
        return attempt

    def evidence_key(kind, identity, attempt):
        return kind + ":" + digest([identity["tenant_id"], attempt.id])[:48]

    def attempt_descriptor(db, identity, school, attempt):
        value = attempt.value
        result_row = db.get(Record, evidence_key("game-result", identity, attempt))
        award_row = db.get(Record, evidence_key("game-award", identity, attempt))
        for evidence, kind in ((result_row, "game_attempt_result"), (award_row, "game_attempt_award")):
            if evidence and (evidence.tenant_id != identity["tenant_id"] or evidence.kind != kind or evidence.parent_id != attempt.id or evidence.owner_id != attempt.owner_id or scope_id(db, evidence) != school):
                raise HTTPException(409, "Assignment evidence integrity check failed")
        return {"id": attempt.id, "student_id": attempt.owner_id, "ordinal": value["ordinal"], "status": "observed" if result_row else "started", "resource_version": value["resource_version"], "scoring_revision_id": value["scoring_revision_id"], "scoring_policy": value["scoring_policy"], "scoring_policy_sha256": value["scoring_policy_sha256"], "started_at": value["started_at"], "result": result_row.value["result"] if result_row else None, "calculation": result_row.value["calculation"] if result_row else None, "award": award_row.value["award"] if award_row else None}

    @app.post("/api/courses/{course_id}/games/{resource_id}/attempts")
    def start_attempt(course_id: str, resource_id: str, body: GameAttemptStart, request: Request, identity=Depends(actor)):
        return play_admission(course_id, resource_id, request, identity, body)

    @app.post("/api/courses/{course_id}/games/{resource_id}/attempts/{attempt_id}/result")
    def save_result(course_id: str, resource_id: str, attempt_id: str, body: GameAttemptResult, request: Request, identity=Depends(actor)):
        identity = fresh(request, identity)
        with database.write() as db:
            course, school, role = course_access(db, identity, course_id)
            if role != "student":
                raise HTTPException(403, "Students submit only their own assignment observations")
            row, package = resource(db, identity, course, school, resource_id)
            attempt = attempt_row(db, identity, school, resource_id, attempt_id)
            if attempt.owner_id != identity["id"]:
                raise HTTPException(404, "Assignment attempt not found")
            value = attempt.value
            calculation = calculate_points(value["game"]["spec"], body.result, value["scoring_policy"])
            key = evidence_key("game-result", identity, attempt)
            existing = db.get(Record, key)
            if existing:
                if existing.value.get("result") != body.result:
                    raise HTTPException(409, "This attempt already has a different terminal observation")
                return attempt_descriptor(db, identity, school, attempt)
            _config_row, config = _connection(db, identity, school)
            publication = _approval(db, identity, school, course, row, package, config)
            if not publication or publication.id != value["publication_id"] or row.value["version"] != value["resource_version"] or _course_context(course) != value["course_context"] or config["revision_id"] != value["connection_revision_id"] or value["runtime_contract"] != RUNTIME_CONTRACT:
                raise HTTPException(409, "Assignment approval, policy, class or connection changed during play")
            result = _add(db, identity, school, "game_attempt_result", {"attempt_id": attempt.id, "snapshot_sha256": value["snapshot_sha256"], "result": body.result, "calculation": calculation, "observed_at": now(), "client_reported": True}, attempt.id, key)
            audit_actor(db, identity, "game_studio.attempt_observed", {"school_id": school, "course_id": course.id, "resource_id": row.id, "student_id": identity["id"], "attempt_id": attempt.id, "result_id": result.id, "result": body.result, "calculation": calculation, "automatic_grade": False})
            return attempt_descriptor(db, identity, school, attempt)

    @app.get("/api/courses/{course_id}/games/{resource_id}/attempts")
    def list_attempts(course_id: str, resource_id: str, identity=Depends(actor)):
        with database.write() as db:
            course, school, role = course_access(db, identity, course_id)
            row, package = resource(db, identity, course, school, resource_id)
            policy = _scoring(db, identity, school, row, package.value["game"]["spec"])
            query = select(Record).where(Record.tenant_id == identity["tenant_id"], Record.kind == "game_attempt", Record.parent_id == row.id)
            if role == "student":
                query = query.where(Record.owner_id == identity["id"])
            attempts = [attempt_descriptor(db, identity, school, attempt_row(db, identity, school, row.id, item.id)) for item in db.scalars(query)]
            attempts.sort(key=lambda item: (item["student_id"], item["started_at"], item["ordinal"]))
            def summarize(student):
                series = [item for item in attempts if item["student_id"] == student and item["scoring_revision_id"] == row.value.get("scoring_revision_id")]
                selected = select_attempt(series, policy) if policy else None
                return {"suggested_points": selected["calculation"]["earned_points"] if selected else None, "max_points": policy["max_points"] if policy else None, "selected_attempt_id": selected["id"] if selected else None, "attempt_count": len(series), "remaining_attempts": max(0, policy["max_attempts"] - len(series)) if policy else 0}
            audit_actor(db, identity, "game_studio.attempts_reviewed", {"school_id": school, "course_id": course.id, "resource_id": row.id, "student_id": identity["id"] if role == "student" else None, "attempt_ids": [item["id"] for item in attempts], "private_evidence": True})
            return {"attempts": attempts, "summary": summarize(identity["id"]) if role == "student" else None, "summaries": [] if role == "student" else [{"student_id": student, **summarize(student)} for student in sorted({item["student_id"] for item in attempts})], "can_award": role in {"admin", "school", "teacher"}}

    @app.post("/api/courses/{course_id}/games/{resource_id}/attempts/{attempt_id}/award")
    def award_attempt(course_id: str, resource_id: str, attempt_id: str, body: GameAttemptAward, identity=Depends(actor)):
        with database.write() as db:
            course, school, role = course_access(db, identity, course_id, True)
            row, _package_row = resource(db, identity, course, school, resource_id)
            attempt = attempt_row(db, identity, school, resource_id, attempt_id)
            if not db.get(Record, evidence_key("game-result", identity, attempt)):
                raise HTTPException(409, "A terminal gameplay observation is required before reviewing credit")
            points = validate_award(body.points, attempt.value["scoring_policy"]["max_points"])
            key = evidence_key("game-award", identity, attempt)
            existing = db.get(Record, key)
            if existing:
                award = existing.value["award"]
                if award["points"] != points or award["reason"] != body.reason:
                    raise HTTPException(409, "This attempt already has a different immutable credit review")
                return attempt_descriptor(db, identity, school, attempt)
            award = {"points": points, "reason": body.reason, "awarded_by": identity["id"], "awarded_at": now()}
            reviewer = {**identity, "id": attempt.owner_id}
            receipt = _add(db, reviewer, school, "game_attempt_award", {"attempt_id": attempt.id, "snapshot_sha256": attempt.value["snapshot_sha256"], "award": award}, attempt.id, key)
            audit_actor(db, identity, "game_studio.award_reviewed", {"school_id": school, "course_id": course.id, "resource_id": row.id, "student_id": attempt.owner_id, "attempt_id": attempt.id, "award_id": receipt.id, "award": award, "human_review": True})
            return attempt_descriptor(db, identity, school, attempt)
