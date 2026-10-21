"""入库任务 API：快速返回、租户查询、幂等提交和取消。"""
from __future__ import annotations

import uuid

import sqlalchemy as sa
from fastapi.testclient import TestClient

from app import create_app
from qa_core.config.settings import get_settings
from scripts.migrate_ingest_jobs import apply_migration


def test_rebuild_api_returns_persistent_job_without_running_pipeline():
    settings = get_settings()
    apply_migration(settings.mysql_dsn)
    tenant = f"api-job-{uuid.uuid4().hex}"
    headers = {"X-User-Id": "job-admin", "X-User-Roles": "admin",
               "X-Tenant-ID": "default"}
    client = TestClient(create_app())
    body = {"mode": "incremental", "kb_version": f"v-{uuid.uuid4().hex[:8]}",
            "run_quality_gate": False}
    try:
        first = client.post("/kb/rebuild", json=body, headers=headers)
        assert first.status_code == 202
        assert first.json()["created"] is True
        job_id = first.json()["job"]["job_id"]
        assert first.json()["job"]["status"] == "pending"

        duplicate = client.post("/kb/rebuild", json=body, headers=headers)
        assert duplicate.status_code == 202
        assert duplicate.json()["created"] is False
        assert duplicate.json()["job"]["job_id"] == job_id

        fetched = client.get(f"/kb/rebuild/{job_id}", headers=headers)
        assert fetched.status_code == 200
        assert fetched.json()["tenant_id"] == "default"
        assert client.get(f"/kb/rebuild/{job_id}", headers={**headers, "X-Tenant-ID": tenant}).status_code == 404
        cancelled = client.post(f"/kb/rebuild/{job_id}/cancel", headers=headers)
        assert cancelled.status_code == 200
        assert cancelled.json()["status"] == "cancelled"
    finally:
        engine = sa.create_engine(settings.mysql_dsn)
        with engine.begin() as conn:
            conn.execute(sa.text("DELETE FROM ingest_jobs WHERE kb_version=:v"),
                         {"v": body["kb_version"]})
            conn.execute(sa.text("DELETE FROM kb_versions WHERE version=:v AND status='STAGED'"),
                         {"v": body["kb_version"]})


def test_tenant_admin_global_mutations_denied_before_write(monkeypatch):
    from unittest.mock import Mock
    from qa_core.api import route_ingest
    delete = Mock()
    monkeypatch.setattr("scripts.rebuild_kb.delete_file_chunks", delete)
    def forbidden_guard(*args, **kwargs):
        raise AssertionError("unauthorized request reached candidate mutation")
    monkeypatch.setattr(route_ingest, "candidate_write", forbidden_guard)
    client = TestClient(create_app())
    headers = {"X-User-Id": "foreign-admin", "X-User-Roles": "admin", "X-Tenant-ID": "foreign"}
    assert client.delete("/admin/documents", params={"source": "manual.md", "kb_version": "own-test-only"}, headers=headers).status_code == 403
    assert client.post("/kb/rebuild", json={"kb_version": "own-test-only"}, headers=headers).status_code == 403
    delete.assert_not_called()
