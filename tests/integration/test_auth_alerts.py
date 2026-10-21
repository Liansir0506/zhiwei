"""阶段10 集成测试：API Key 认证、告警 webhook、/metrics。"""
import pytest
from fastapi.testclient import TestClient

from app import create_app
from qa_core.api.auth import create_api_key
from qa_core.config.settings import get_settings


@pytest.fixture(scope="module")
def client():
    return TestClient(create_app())


@pytest.mark.integration
def test_metrics_endpoint(client):
    r = client.get("/metrics")
    assert r.status_code == 200
    assert "qa_requests_total" in r.text


@pytest.mark.integration
def test_alarm_webhook_known_alarm(client):
    r = client.post("/alerts/webhook", json={
        "alarm_code": "ALM-1201", "source": "网管", "severity": "critical"},
        headers={"X-User-Roles": "ops"})
    assert r.status_code == 200
    body = r.json()
    assert body["alarm_code"] == "ALM-1201"
    assert body["advice"] and len(body["advice"]) > 20
    assert any("TT-2026-001" in (c.get("source") or "") for c in body["similar_cases"])


@pytest.mark.integration
def test_alarm_webhook_unknown_alarm_degrades(client):
    r = client.post("/alerts/webhook", json={
        "alarm_code": "ALM-9999", "source": "网管"}, headers={"X-User-Roles": "ops"})
    assert r.status_code == 200
    body = r.json()
    assert body["advice"]  # 降级语义检索或转人工文案


@pytest.mark.integration
def test_api_key_auth_flow(client, monkeypatch):
    """AUTH_REQUIRED=true 时：无 key 401、有效 key 200、无效 key 401。"""
    from qa_core.api.auth import hash_key
    import sqlalchemy as sa

    raw = "test-key-unit-123"
    create_api_key(raw, "tester", ["ops"])
    engine = sa.create_engine(get_settings().mysql_dsn)
    settings = get_settings()
    monkeypatch.setattr(settings, "auth_required", True)
    try:
        assert client.post("/qa", json={"query": "你好"}).status_code == 401
        assert client.post("/qa", json={"query": "你好"},
                           headers={"X-API-Key": "wrong-key"}).status_code == 401
        r = client.post("/qa", json={"query": "你好"}, headers={"X-API-Key": raw})
        assert r.status_code == 200
        assert r.json()["route"] == "direct_answer"
    finally:
        with engine.begin() as conn:
            conn.execute(sa.text("DELETE FROM api_keys WHERE key_hash=:h"),
                         {"h": hash_key(raw)})
        monkeypatch.setattr(settings, "auth_required", False)


@pytest.mark.integration
def test_disabled_key_rejected(client, monkeypatch):
    import sqlalchemy as sa
    from qa_core.api.auth import hash_key
    from qa_core.config.settings import get_settings

    raw = "disabled-key-test"
    create_api_key(raw, "tester", ["ops"])
    engine = sa.create_engine(get_settings().mysql_dsn)
    settings = get_settings()
    monkeypatch.setattr(settings, "auth_required", True)
    try:
        with engine.begin() as conn:
            conn.execute(sa.text("UPDATE api_keys SET enabled=0 WHERE key_hash=:h"),
                         {"h": hash_key(raw)})
        r = client.post("/qa", json={"query": "你好"}, headers={"X-API-Key": raw})
        assert r.status_code == 401
    finally:
        with engine.begin() as conn:
            conn.execute(sa.text("DELETE FROM api_keys WHERE key_hash=:h"),
                         {"h": hash_key(raw)})
