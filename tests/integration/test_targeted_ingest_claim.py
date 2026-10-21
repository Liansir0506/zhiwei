"""Claiming a requested candidate job must leave older unrelated jobs pending."""
import uuid

from sqlalchemy import text
from qa_core.config.settings import get_settings
from qa_core.ingest.jobs import IngestJobDao


def test_selected_job_does_not_drain_unrelated_queue():
    dao = IngestJobDao(get_settings().mysql_dsn)
    identity = uuid.uuid4().hex
    tenant = f"targeted-{identity}"
    version = "targeted-" + identity[:12]
    ids = []
    try:
        for index in range(2):
            job, _ = dao.create(job_type="rebuild", tenant_id=tenant, actor="unit-isolated",
                               kb_version=version, idempotency_key=f"{identity}-{index}")
            ids.append(job["job_id"])
        claimed = dao.claim_next("selected-test-worker", job_id=ids[1])
        assert claimed["job_id"] == ids[1]
        assert dao.get(ids[0], tenant)["status"] == "pending"
        assert dao.claim_next("different-worker", job_id=ids[1]) is None
    finally:
        with dao.engine.begin() as conn:
            conn.execute(text("DELETE FROM ingest_jobs WHERE tenant_id=:tenant"), {"tenant": tenant})
            conn.execute(text("DELETE FROM kb_versions WHERE version=:v AND status='STAGED'"), {"v": version})
