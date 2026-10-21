"""第4步：HTTP/WS 一致鉴权、越权反馈和审计记录。"""
from __future__ import annotations

import sqlalchemy as sa
import pytest
from fastapi.testclient import TestClient
from starlette.websockets import WebSocketDisconnect

from app import create_app
from qa_core.api.auth import create_api_key, hash_key
from qa_core.config.settings import get_settings
from scripts.migrate_security_model import apply_migration


@pytest.fixture(scope="module")
def client():
    settings = get_settings()
    apply_migration(settings.mysql_dsn)
    return TestClient(create_app())


def test_http_and_ws_share_api_key_requirement(client, monkeypatch):
    settings = get_settings()
    raw = "security-ws-key-123"
    create_api_key(raw, "security-ws-user", ["ops"], security_levels=["public", "internal"])
    monkeypatch.setattr(settings, "auth_required", True)
    try:
        assert client.post("/qa", json={"query": "你好"}).status_code == 401
        with pytest.raises(WebSocketDisconnect) as exc:
            with client.websocket_connect("/qa/ws"):
                pass
        assert exc.value.code == 1008
        assert client.post("/qa", json={"query": "你好"}, headers={"X-API-Key": raw}).status_code == 200
        with client.websocket_connect("/qa/ws", headers={"X-API-Key": raw}) as ws:
            ws.send_json({"query": "你好"})
            assert ws.receive_json()["event"] == "start"
    finally:
        monkeypatch.setattr(settings, "auth_required", False)
        with sa.create_engine(settings.mysql_dsn).begin() as conn:
            conn.execute(sa.text("DELETE FROM api_keys WHERE key_hash=:h"), {"h": hash_key(raw)})


def test_feedback_owner_and_denial_are_audited(client):
    owner = {"X-User-Id": "feedback-owner", "X-User-Roles": "ops", "X-Tenant-Id": "default"}
    other = {"X-User-Id": "feedback-other", "X-User-Roles": "ops", "X-Tenant-Id": "default"}
    result = client.post("/qa", json={"query": "你好", "session_id": "feedback-security"}, headers=owner)
    message_id = result.json()["message_id"]
    assert client.post("/feedback", json={"message_id": message_id, "rating": "up"}, headers=other).status_code == 403
    audit = client.get("/admin/audit/access?decision=deny", headers={"X-User-Roles": "auditor"})
    assert audit.status_code == 200
    assert any(event["reason"] == "message_owner_mismatch" for event in audit.json()["events"])
