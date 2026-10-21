import uuid
import pytest
import sqlalchemy as sa
from qa_core.config.settings import get_settings
from qa_core.ingest.jobs import IngestJobDao
from qa_core.ingest.write_guard import candidate_write
from qa_core.storage.version_manifest import VersionManifestDao, bind_candidate, verify_read_binding


def test_candidate_manifest_is_persistent_and_cannot_rebind():
    settings = get_settings()
    version = "manifest-it-" + uuid.uuid4().hex[:10]
    dao = VersionManifestDao(settings.mysql_dsn)
    jobs = IngestJobDao(settings.mysql_dsn)
    try:
        job, _ = jobs.create(job_type="rebuild", tenant_id="default", actor="manifest-test", kb_version=version,
                             idempotency_key=uuid.uuid4().hex, payload={"run_quality_gate": False})
        assert job["payload_json"]["execution_binding"]["version_binding"] == dao.get(version)["binding"]
        with candidate_write(version, settings=settings):
            with pytest.raises(ValueError, match="绑定不一致"):
                bind_candidate(settings.model_copy(update={"doc_collection": "other"}), version)
            dao.record_snapshot(version, {"files": [{"filename": "fixture.md", "chunk_count": 2}]})
        assert dao.get(version)["snapshot"]["files"][0]["chunk_count"] == 2
        assert verify_read_binding(settings, version)
        with pytest.raises(ValueError, match="不匹配"):
            verify_read_binding(settings.model_copy(update={"faq_collection": "other"}), version)
    finally:
        with dao.engine.begin() as conn:
            conn.execute(sa.text("DELETE FROM ingest_jobs WHERE kb_version=:v"), {"v": version})
            conn.execute(sa.text("DELETE FROM kb_versions WHERE version=:v AND status='STAGED'"), {"v": version})
        dao.close()
