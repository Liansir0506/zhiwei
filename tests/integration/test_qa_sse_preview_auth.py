"""Real MySQL API-key and version-binding gate for SSE STAGED preview."""
import uuid
from unittest.mock import Mock

from fastapi.testclient import TestClient
from sqlalchemy import text

from qa_core.api import auth, route_qa
from qa_core.api.auth import create_api_key, hash_key
from qa_core.config.settings import get_settings
from qa_core.storage.kb_dao import KbDao


def test_staged_preview_requires_real_admin_key_before_stream(monkeypatch):
    settings = get_settings()
    assert settings.is_test and settings.doc_collection == "test_protocol_r1_doc"
    assert settings.faq_collection == "test_protocol_r1_faq"
    dao = KbDao(settings.mysql_dsn)
    assert dao.get_version("v1")["status"] == "ACTIVE"
    assert dao.get_version("v4-protocol-dev-r1")["status"] == "STAGED"
    secure = settings.model_copy(update={"auth_required": True})
    monkeypatch.setattr(auth, "get_settings", lambda: secure)
    monkeypatch.setattr(route_qa, "get_settings", lambda: secure)
    fake = Mock()
    fake.ask_stream.side_effect = lambda *args, **kwargs: iter([
        {"event": "start", "data": {"kb_version": kwargs["kb_version"]}},
        {"event": "end", "data": {"status": "ANSWERED", "answer": "isolated test"}}])
    monkeypatch.setattr(route_qa, "get_usecase", lambda: fake)
    token = uuid.uuid4().hex
    ops_key, admin_key = "sse-preview-ops-" + token, "sse-preview-admin-" + token
    keys = (ops_key, admin_key)
    try:
        create_api_key(ops_key, "sse-preview-ops-" + token, ["ops"])
        create_api_key(admin_key, "sse-preview-admin-" + token, ["admin"])
        from app import create_app
        with TestClient(create_app()) as http:
            body = {"query": "已批准资料预览", "kb_version": "v4-protocol-dev-r1"}
            assert http.post("/qa/sse", json=body).status_code == 401
            ops_versions = http.get("/kb/versions", headers={"X-API-Key": ops_key,
                "X-User-Roles": "admin"})
            assert ops_versions.status_code == 200
            assert ops_versions.json()["can_preview_staged"] is False
            admin_versions = http.get("/kb/versions", headers={"X-API-Key": admin_key})
            assert admin_versions.status_code == 200
            assert admin_versions.json()["can_preview_staged"] is True
            denied = http.post("/qa/sse", json=body, headers={
                "X-API-Key": ops_key, "X-User-Roles": "admin"})
            assert denied.status_code == 404
            assert fake.ask_stream.call_count == 0
            missing = http.post("/qa/sse", json={**body, "kb_version": "missing-preview-" + token},
                headers={"X-API-Key": admin_key})
            assert missing.status_code == 404
            assert fake.ask_stream.call_count == 0
            allowed = http.post("/qa/sse", json=body, headers={"X-API-Key": admin_key})
            assert allowed.status_code == 200, allowed.text
            assert '"kb_version":"v4-protocol-dev-r1"' in allowed.text
            assert fake.ask_stream.call_count == 1
            assert fake.ask_stream.call_args.kwargs["kb_version"] == "v4-protocol-dev-r1"
    finally:
        with dao.engine.begin() as conn:
            for key in keys:
                conn.execute(text("DELETE FROM api_keys WHERE key_hash=:h"), {"h": hash_key(key)})
    assert dao.get_version("v1")["status"] == "ACTIVE"
    assert dao.get_version("v4-protocol-dev-r1")["status"] == "STAGED"
