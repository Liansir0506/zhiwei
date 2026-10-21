"""SSE through real API-key auth and test MySQL, without invoking DeepSeek."""
import uuid

from fastapi.testclient import TestClient
from sqlalchemy import text

from qa_core.api import auth, route_qa
from qa_core.api.auth import create_api_key, hash_key
from qa_core.config.settings import get_settings
from qa_core.storage.kb_dao import KbDao


def test_sse_real_key_auth_and_direct_answer(monkeypatch):
    settings = get_settings()
    assert settings.is_test and settings.doc_collection == "test_protocol_r1_doc"
    assert KbDao(settings.mysql_dsn).get_version("v1")["status"] == "ACTIVE"
    secure = settings.model_copy(update={"auth_required": True})
    monkeypatch.setattr(auth, "get_settings", lambda: secure)
    monkeypatch.setattr(route_qa, "get_settings", lambda: secure)
    key = "sse-test-" + uuid.uuid4().hex
    create_api_key(key, "sse-user-" + uuid.uuid4().hex, ["ops"])
    from app import create_app
    try:
        with TestClient(create_app()) as http:
            assert http.post("/qa/sse", json={"query": "你好"}).status_code == 401
            result = http.post("/qa/sse", json={"query": "你好"},
                               headers={"X-API-Key": key, "X-User-Roles": "admin"})
            assert result.status_code == 200, result.text
            assert "event: meta\n" in result.text
            assert "event: sources\n" in result.text
            assert "event: done\n" in result.text
            assert "event: token\n" not in result.text  # direct reply, no model call
            assert '"status":"ANSWERED"' in result.text
    finally:
        with KbDao(settings.mysql_dsn).engine.begin() as conn:
            conn.execute(text("DELETE FROM api_keys WHERE key_hash=:h"), {"h": hash_key(key)})
