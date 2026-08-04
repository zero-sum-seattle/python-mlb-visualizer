"""Tests for the web application foundation."""

from fastapi.testclient import TestClient

from app.main import app

client = TestClient(app)


def test_index_returns_ok() -> None:
    response = client.get("/")
    assert response.status_code == 200


def test_index_returns_html() -> None:
    response = client.get("/")
    assert "text/html" in response.headers["content-type"]


def test_index_contains_project_name_and_message() -> None:
    response = client.get("/")
    body = response.text
    assert "mlb-stats-visualizer" in body
    assert "The application foundation is working." in body
    assert "python-mlb-statsapi" in body


def test_health_returns_ok() -> None:
    response = client.get("/health")
    assert response.status_code == 200


def test_health_returns_expected_json() -> None:
    response = client.get("/health")
    assert response.json() == {
        "status": "ok",
        "app": "mlb-stats-visualizer",
    }
