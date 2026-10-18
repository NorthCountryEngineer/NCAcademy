import pytest
from fastapi.testclient import TestClient
from academy.config import Settings
from academy.main import create_app
from academy.migrate import migrate


@pytest.fixture(autouse=True)
def no_outbound_network(monkeypatch):
    import socket
    def denied(*args, **kwargs):
        raise AssertionError("Tests may not contact real networks; install an explicit mock")
    monkeypatch.setattr(socket, "getaddrinfo", denied)
    monkeypatch.setattr(socket.socket, "connect", denied)


@pytest.fixture
def app(tmp_path):
    settings = Settings("sqlite:///" + str(tmp_path / "academy.db"), "local", "http://localhost:8090", "test-bootstrap-secret-long-enough-for-local", False, True)
    migrate(settings)
    return create_app(settings)


@pytest.fixture
def client(app):
    with TestClient(app, base_url=app.state.settings.app_url) as browser:
        yield browser


def login(client, user="demo-student", *, complete_intake=True):
    response = client.post("/api/auth/local", json={"user_id": user}, headers={"Origin": "http://localhost:8090"})
    assert response.status_code == 200, response.text
    headers = {"Origin": "http://localhost:8090", "X-CSRF-Token": response.json()["csrf_token"]}
    if complete_intake and response.json().get("learning_preferences", {}).get("status") == "required":
        from academy.learning_preferences import NEUTRAL
        saved = client.put("/api/me/learning-preferences", json={"answers": NEUTRAL, "expected_version": response.json()["learning_preferences"]["version"]}, headers=headers)
        assert saved.status_code == 200, saved.text
    return headers


def draft(client, headers):
    response = client.put("/api/assignments/demo-assignment/draft", json={"content": "My own attempt explains a claim, identifies evidence, and considers an alternative explanation."}, headers=headers)
    assert response.status_code == 200, response.text


def model_assignment(client, app):
    headers = login(client, "demo-admin")
    response = client.post("/api/admin/connections", json={"name": "Mock classifier", "endpoint": "https://models.example.test/v1", "models": ["classify-only"], "enabled": True, "api_key": "unit-test-key-never-live"}, headers=headers)
    assert response.status_code == 201, response.text
    connection = response.json()
    headers = login(client, "demo-instructor")
    assignment = client.get("/api/assignments/demo-assignment").json()
    policy = assignment["tutor_policy"]
    policy.update(mode="socratic", connection_id=connection["id"], model="classify-only", max_total_tokens=100000)
    response = client.put("/api/assignments/demo-assignment", json={"title": assignment["title"], "module": assignment["module"], "instructions": assignment["instructions"], "tutor_policy": policy}, headers=headers)
    assert response.status_code == 200, response.text
    headers = login(client)
    draft(client, headers)
    # Tests substitute the transport before explicitly enabling the server gate.
    app.state.classify = lambda *_: {"id": "mock", "choices": [{"message": {"role": "assistant", "content": '{"selection_id":"uncertainty"}'}}], "usage": {"prompt_tokens": 90, "completion_tokens": 10, "total_tokens": 100}}
    app.state.settings.allow_live_models = True
    return headers, connection
