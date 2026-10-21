"""HTTP 层测试：/health 可通过 TestClient 访问（不连真实服务）。"""
from fastapi.testclient import TestClient

from app import create_app


def test_health_endpoint_200():
    client = TestClient(create_app())
    resp = client.get("/health")
    assert resp.status_code == 200
    body = resp.json()
    assert body["status"] in {"ok", "degraded"}
    assert body["environment"] in {"dev", "test", "demo", "prod"}
    assert "components" in body
