"""UI preview hint must match authenticated role, not caller-controlled headers."""
from fastapi import FastAPI
from fastapi.testclient import TestClient

from qa_core.api import route_kb
from qa_core.api.auth import AuthContext, get_auth_context


def test_versions_preview_hint_is_admin_only(monkeypatch):
    monkeypatch.setattr(route_kb, "_list_versions", lambda: [
        {"version": "v1", "status": "ACTIVE"},
        {"version": "candidate", "status": "STAGED"}])
    app = FastAPI()
    app.include_router(route_kb.router)
    app.dependency_overrides[get_auth_context] = lambda: AuthContext("user", ["ops"], "api_key")
    with TestClient(app) as http:
        ops = http.get("/kb/versions", headers={"X-User-Roles": "admin"})
        assert ops.status_code == 200
        assert ops.json()["can_preview_staged"] is False
        app.dependency_overrides[get_auth_context] = lambda: AuthContext("admin", ["admin"], "api_key")
        admin = http.get("/kb/versions")
        assert admin.status_code == 200
        assert admin.json()["can_preview_staged"] is True
