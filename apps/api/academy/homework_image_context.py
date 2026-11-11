"""Carry an owned worksheet into a consented, scoped Homework Help follow-up."""

import base64
import hashlib

from fastapi import HTTPException

from .curated_lesson_api import _image_bytes, _image_metadata
from .db import Record, canonical
from .lesson_api import course_for_student
from .permissions import require_school, scope_id


VERSION = "homework-image-context-v1"
_MAX_CONTEXT_DEPTH = 100


def _digest(value):
    return hashlib.sha256(canonical(value).encode()).hexdigest()


def _policy_binding(snapshot):
    return {
        "revision_id": snapshot.get("revision_id"),
        "approval_id": snapshot.get("approval_id"),
        "configuration_sha256": snapshot.get("configuration_sha256"),
        "connections_sha256": _digest(snapshot.get("connections", {})),
    }


def _source_snapshot(row, value):
    return {
        "message_id": row.id,
        "session_id": row.parent_id,
        "tenant_id": row.tenant_id,
        "owner_id": row.owner_id,
        "school_id": value.get("school_id"),
        "course_id": value.get("course_id"),
        "private_mode": value.get("private_mode") is True,
        "status": value.get("status"),
        "images": value.get("images"),
        "images_cipher_sha256": _digest(value.get("images_cipher")),
        "image_context": value.get("image_context"),
    }


def _source(db, identity, settings, session_id, school_id, message_id):
    require_school(db, identity, school_id, settings, {"student"}, human=True)
    school = db.get(Record, school_id)
    conversation = db.get(Record, session_id)
    if (not school or school.value.get("disabled")
            or not conversation or conversation.kind != "homework_ai_session"
            or conversation.tenant_id != identity["tenant_id"] or conversation.owner_id != identity["id"]
            or conversation.parent_id != school_id or scope_id(db, conversation) != school_id
            or conversation.value.get("school_id") != school_id):
        raise HTTPException(404, "Previous homework picture is unavailable")
    row = db.get(Record, message_id)
    if (not row or row.kind != "homework_ai_message"
            or row.tenant_id != identity["tenant_id"] or row.owner_id != identity["id"]
            or row.parent_id != session_id or scope_id(db, row) != school_id):
        raise HTTPException(404, "Previous homework picture is unavailable")
    value = row.value
    if value.get("school_id") != school_id or not value.get("images") or not value.get("images_cipher"):
        raise HTTPException(404, "Previous homework picture is unavailable")
    if value.get("status") not in {"released", "clarify", "blocked", "withheld"}:
        raise HTTPException(409, "Wait for the previous homework picture request to finish")
    source_course = value.get("course_id")
    if source_course:
        _, course_school = course_for_student(db, identity, source_course, settings)
        if course_school != school_id:
            raise HTTPException(403, "Previous homework picture requires its current class enrollment")
    return row, value


def resolve_image_context(db, identity, settings, auth, *, session_id, school_id, course_id,
                          normalized_images, previous_image_message_id, image_use_confirmed,
                          configuration_snapshot):
    """Return normalized images and provenance without sending data externally.

    Every retained-picture import needs a new request's explicit confirmation.
    An earlier local-only picture confirmation never authorizes provider use.
    The caller enforces the current approved model policy and image quota.
    """
    if len(normalized_images) > 2:
        raise HTTPException(422, "Use up to two homework pictures, including retained pictures")
    if not previous_image_message_id:
        return list(normalized_images), None
    if not image_use_confirmed:
        raise HTTPException(403, "Confirm the approved photo destinations before reusing a homework picture")
    row, value = _source(db, identity, settings, session_id, school_id, previous_image_message_id)
    revalidate_image_context(db, identity, settings, value)
    if course_id and value.get("course_id") and course_id != value["course_id"]:
        raise HTTPException(403, "Previous homework picture belongs to a different class context")
    if len(value["images"]) + len(normalized_images) > 2:
        raise HTTPException(422, "Use up to two homework pictures, including retained pictures")
    retained = []
    for metadata, raw in _image_bytes(auth, value):
        retained.append({**metadata, "media_type": "image/jpeg",
                         "data_base64": base64.b64encode(raw).decode("ascii")})
    images = [*retained, *normalized_images]
    images = [{**image, "id": f"image-{index}", "name": f"Homework picture {index}"}
              for index, image in enumerate(images, start=1)]
    context = {
        "version": VERSION,
        "source_message_id": row.id,
        "source_snapshot_sha256": _digest(_source_snapshot(row, value)),
        "source_course_id": value.get("course_id"),
        "source_private_mode": value.get("private_mode") is True,
        "source_images": [{key: item[key] for key in ("id", "sha256", "width", "height")}
                          for item in _image_metadata(retained)],
        "policy_binding": _policy_binding(configuration_snapshot),
    }
    return images, context


def revalidate_image_context(db, identity, settings, data, *, _visited=None):
    """Recheck retained source authority and exact provenance before send/replay."""
    context = data.get("image_context")
    if not context:
        return
    visited = set() if _visited is None else _visited
    source_id = context.get("source_message_id")
    if (context.get("version") != VERSION or not isinstance(source_id, str)
            or source_id in visited or len(visited) >= _MAX_CONTEXT_DEPTH
            or not data.get("image_use_confirmed")
            or context.get("policy_binding") != _policy_binding(data.get("configuration_snapshot", {}))):
        raise HTTPException(409, "Previous homework picture context changed; confirm the picture again")
    visited.add(source_id)
    row, value = _source(db, identity, settings, data["session_id"], data["school_id"], source_id)
    current_images = [{key: item[key] for key in ("id", "sha256", "width", "height")}
                      for item in value["images"]]
    carried_images = [{key: item[key] for key in ("sha256", "width", "height")}
                      for item in data.get("images", [])[:len(current_images)]]
    expected_carried = [{key: item[key] for key in ("sha256", "width", "height")}
                        for item in current_images]
    if (context.get("source_snapshot_sha256") != _digest(_source_snapshot(row, value))
            or context.get("source_course_id") != value.get("course_id")
            or context.get("source_images") != current_images or carried_images != expected_carried):
        raise HTTPException(409, "Previous homework picture changed; confirm the picture again")
    # A follow-up may itself carry a retained photo. Keep every original source
    # dependency subject to current membership, without provider or Core calls.
    revalidate_image_context(db, identity, settings, value, _visited=visited)
