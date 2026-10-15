"""CORS headers the browser frontend depends on."""

from __future__ import annotations

from fastapi.testclient import TestClient


def test_cors_exposes_retry_after_to_the_frontend(client: TestClient) -> None:
    response = client.get("/healthz", headers={"Origin": "http://localhost:5173"})
    exposed = response.headers["access-control-expose-headers"].lower()
    assert "retry-after" in exposed
