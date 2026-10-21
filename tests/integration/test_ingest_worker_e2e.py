"""真实轻量 Worker：上传任务最终进入 Milvus，重复提交不重复入库。"""
from __future__ import annotations

import json
import uuid
from pathlib import Path

import sqlalchemy as sa
from fastapi.testclient import TestClient

from app import create_app
from qa_core.config.settings import get_settings
from qa_core.storage.kb_dao import KbDao
from qa_core.storage.milvus_client import get_milvus_client
from scripts.ingest_worker import run_slot
from scripts.migrate_ingest_jobs import apply_migration
from scripts.rebuild_kb import delete_file_chunks


def test_upload_worker_is_persistent_and_idempotent(tmp_path, monkeypatch):
    settings = get_settings()
    apply_migration(settings.mysql_dsn)
    token = uuid.uuid4().hex[:10]
    from pymilvus import MilvusClient
    from scripts.init_collections import build
    from scripts import ingest_worker, rebuild_kb
    from qa_core.api import route_admin
    from qa_core.ingest import write_guard
    from qa_core.ingest.jobs import IngestJobDao
    from types import SimpleNamespace
    from unittest.mock import Mock
    from transformers import AutoTokenizer
    settings = settings.model_copy(update={"data_dir": str(tmp_path),
        "doc_collection": f"test_upload_doc_{token}", "faq_collection": f"test_upload_faq_{token}"})
    for module in (ingest_worker, rebuild_kb, route_admin, write_guard):
        monkeypatch.setattr(module, "get_settings", lambda: settings)
    milvus = MilvusClient(uri=settings.milvus_uri, timeout=15)
    embedder = Mock()
    embedder.embed_passages.side_effect = lambda texts: [[1.0] + [0.0] * 1023 for _ in texts]
    embedder.model = SimpleNamespace(tokenizer=AutoTokenizer.from_pretrained(settings.embedding_model_path, local_files_only=True), max_seq_length=8192)
    tenant = f"worker-test-{token}"
    filename = f"faq_worker_{token}.csv"
    headers = {"X-User-Id": "worker-admin", "X-User-Roles": "admin",
               "X-Tenant-ID": tenant, "X-Filename": filename}
    content = "question,answer\nWorker测试问题？,Worker测试答案\n".encode()
    client = TestClient(create_app())
    engine = sa.create_engine(settings.mysql_dsn)
    staging_path: Path | None = None
    kb_version = f"upload-it-{token}"
    endpoint = f"/admin/documents/upload?kb_version={kb_version}&source_id=source-{token}"
    try:
        build(milvus, settings.doc_collection, False)
        build(milvus, settings.faq_collection, True)
        submitted = client.post(endpoint, headers=headers, content=content)
        assert submitted.status_code == 202
        job_id = submitted.json()["job_id"]
        kb_version = submitted.json()["kb_version"]
        with engine.begin() as conn:
            payload = conn.execute(sa.text(
                "SELECT payload_json FROM ingest_jobs WHERE job_id=:id"),
                {"id": job_id}).scalar()
        payload = json.loads(payload) if isinstance(payload, str) else payload
        staging_path = Path(payload["staging_path"])

        jobs = IngestJobDao(settings.mysql_dsn)
        claimed = jobs.claim_next("upload-test-worker", job_id=job_id)
        stats = ingest_worker.execute_job(claimed, jobs, "upload-test-worker", embedder_override=embedder)
        jobs.finish(job_id, "upload-test-worker", stats)
        finished = client.get(f"/kb/rebuild/{job_id}", headers=headers)
        assert finished.status_code == 200
        assert finished.json()["status"] == "success"
        assert finished.json()["stats_json"]["done"] == 1

        duplicate = client.post(endpoint, headers=headers, content=content)
        assert duplicate.status_code == 202
        assert duplicate.json()["created"] is False
        assert duplicate.json()["job_id"] == job_id
        rows = milvus.query(
            settings.faq_collection,
            filter=f'source == "{filename}" and kb_version == "{kb_version}"',
            output_fields=["id", "tenant_id"], limit=20, consistency_level="Strong")
        assert len(rows) == 1
        assert rows[0]["tenant_id"] == tenant
        assert not (tmp_path / "raw" / filename).exists()
    finally:
        with engine.begin() as conn:
            conn.execute(sa.text("DELETE FROM ingest_jobs WHERE tenant_id=:tenant"),
                         {"tenant": tenant})
            conn.execute(sa.text("DELETE FROM ingest_records WHERE kb_version=:v"), {"v": kb_version})
            conn.execute(sa.text("DELETE FROM kb_versions WHERE version=:v AND status='STAGED'"), {"v": kb_version})
        for coll in (settings.doc_collection, settings.faq_collection):
            if milvus.has_collection(coll):
                milvus.drop_collection(coll)
        milvus.close()
        raw = Path(settings.data_dir) / "raw" / filename
        if raw.is_file():
            raw.unlink()
        if staging_path and staging_path.is_file():
            staging_path.unlink()
            try:
                staging_path.parent.rmdir()
            except OSError:
                pass
