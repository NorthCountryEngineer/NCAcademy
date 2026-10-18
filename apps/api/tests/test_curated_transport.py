"""Worksheet photo uploads widen only their exact bounded mutation route."""
import asyncio

import pytest

from academy.main import RequestBodyLimit


@pytest.mark.parametrize("method,path,size,expected", [
    ("POST", "/api/courses/class-a/curated-lessons", 400000, 204),
    ("POST", "/api/courses/class-a/curated-lessons", 400001, 413),
    ("POST", "/api/courses/class-a/teacher-tutor", 150001, 413),
    ("PUT", "/api/courses/class-a/curated-lessons", 150001, 413),
    ("POST", "/api/courses/class-a/curated-lessons/extra", 150001, 413),
    ("POST", "/api/courses/" + "x" * 65 + "/curated-lessons", 150001, 413),
    ("POST", "/api/homework-help/sessions/session-a/messages", 400000, 204),
    ("POST", "/api/homework-help/sessions/session-a/messages", 400001, 413),
    ("PUT", "/api/homework-help/sessions/session-a/messages", 150001, 413),
    ("POST", "/api/homework-help/sessions/session-a/messages/extra", 150001, 413),
    ("POST", "/api/courses/class-a/voice/transcriptions", 4300000, 204),
    ("POST", "/api/courses/class-a/voice/transcriptions", 4300001, 413),
    ("PUT", "/api/courses/class-a/voice/transcriptions", 150001, 413),
    ("POST", "/api/courses/class-a/voice/speech", 150001, 413),
    ("POST", "/api/courses/class-a/voice/transcriptions/extra", 150001, 413),
])
def test_chunked_photo_boundary_is_exact_and_applies_to_actual_bytes(method, path, size, expected):
    messages = []
    chunks = iter([{"type": "http.request", "body": b"x" * (size // 2), "more_body": True},
                   {"type": "http.request", "body": b"x" * (size - size // 2), "more_body": False}])

    async def receive():
        return next(chunks)

    async def send(message):
        messages.append(message)

    async def accepted(scope, receive, send):
        request = await receive()
        assert len(request["body"]) == size and request["more_body"] is False
        await send({"type": "http.response.start", "status": 204, "headers": []})
        await send({"type": "http.response.body", "body": b""})

    asyncio.run(RequestBodyLimit(accepted)({"type": "http", "method": method, "path": path}, receive, send))
    assert next(item["status"] for item in messages if item["type"] == "http.response.start") == expected


def test_content_length_cannot_expand_ordinary_or_photo_requests(client):
    for path, size in (("/api/courses/demo-course/teacher-tutor", 150001),
                       ("/api/courses/demo-course/curated-lessons", 400001)):
        response = client.post(path, content=b"x" * size, headers={"Content-Type": "application/json"})
        assert response.status_code == 413
        assert "x" * 10 not in response.text
