"""Reviewer replay exposes exact private lessons and protected retained photos."""
import hashlib

from conftest import login
from test_curated_lessons import curate, environment, photo, stored


def test_reviewer_replay_and_exports_reconstruct_the_lesson_without_embedded_photos(client, app, monkeypatch):
    env = environment(client, app, monkeypatch)
    turn = curate(client, env, images=[{"name": "synthetic-task.png", "data_url": photo()}], image_use_confirmed=True).json()
    assert turn["status"] == "released"
    progress = f"/api/courses/{env['course']['id']}/curated-lessons/{turn['id']}/progress"
    reflection = "I understand that photosynthesis transfers carbon into plants."
    assert client.post(progress, json={"section_id": "section-1", "event": "reflection", "response": reflection}, headers=env["headers"]).status_code == 200
    original = stored(app, turn)
    assert original["images_cipher"]
    login(client, "demo-instructor")
    endpoint = f"/api/audit/sessions/{turn['session_id']}"
    replay = client.get(endpoint)
    assert replay.status_code == 200
    evidence = next(item for item in replay.json()["turns"] if item["id"] == turn["id"])
    assert evidence["activity"] == "curated_lesson" and evidence["lesson"] == turn["lesson"]
    assert evidence["images"][0]["url"] == turn["images"][0]["url"]
    assert evidence["provider_request_sha256"] == original["provider_request_sha256"]
    assert any(event["event_type"] == "curated_lesson.progress" and event["payload"]["response"] == reflection for event in replay.json()["events"])
    image = client.get(evidence["images"][0]["url"])
    assert image.status_code == 200 and image.headers["cache-control"] == "private, no-store"
    assert hashlib.sha256(image.content).hexdigest() == evidence["images"][0]["sha256"]
    exported = client.get(endpoint + "/export")
    assert exported.status_code == 200 and exported.json()["complete_tenant_chain"] is False
    assert original["images_cipher"] not in replay.text + exported.text
    assert "images_cipher" not in replay.text + exported.text and "data:image/" not in replay.text + exported.text
    assert "synthetic-lesson-key-never-live" not in replay.text + exported.text
    assert stored(app, turn) == original
    login(client, "demo-student2")
    assert client.get(endpoint).status_code == 403
    assert client.get(turn["images"][0]["url"]).status_code == 403
    login(client, "demo-admin")
    assert client.get("/api/audit/verify").json()["valid"] is True
