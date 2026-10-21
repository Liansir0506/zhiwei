"""Own STAGED ledger rows only; no shared candidate or active version mutations."""
import uuid
import pytest
import sqlalchemy as sa
from qa_core.config.settings import get_settings
from qa_core.ingest.write_guard import candidate_write
from qa_core.storage.kb_dao import KbDao


def test_same_content_cannot_silently_skip_foreign_asset():
    settings = get_settings()
    version = "identity-it-" + uuid.uuid4().hex[:10]
    checksum = uuid.uuid4().hex * 2
    dao = KbDao(settings.mysql_dsn)
    try:
        with candidate_write(version, settings=settings):
            dao.upsert_record(checksum, "manual.md", "manual", version, asset_id="owned-test-asset")
            dao.set_status(checksum, version, "done", chunk_count=1)
            assert dao.file_done(checksum, version, "owned-test-asset") is True
            with pytest.raises(ValueError, match="其他或未知资产"):
                dao.file_done(checksum, version, "foreign-test-asset")
            assert dao.get_records_by_filename("manual.md", version)[0]["asset_id"] == "owned-test-asset"
    finally:
        with dao.engine.begin() as conn:
            conn.execute(sa.text("DELETE FROM ingest_records WHERE file_hash=:h AND kb_version=:v"), {"h": checksum, "v": version})
            conn.execute(sa.text("DELETE FROM kb_versions WHERE version=:v AND status='STAGED'"), {"v": version})
        dao.engine.dispose()


def test_replacement_identity_filter_on_real_milvus():
    from pymilvus import MilvusClient
    from qa_core.indexing.chunkers.base import make_chunk
    from qa_core.ingest.source_identity import verify_upload_replacement
    from scripts.init_collections import build
    from scripts.rebuild_kb import to_milvus_row
    token = uuid.uuid4().hex[:10]
    settings = get_settings().model_copy(update={"doc_collection": "test_identity_doc_" + token})
    client = MilvusClient(uri=settings.milvus_uri, timeout=15)
    try:
        build(client, settings.doc_collection, False)
        chunk = make_chunk("source body", "manual", tenant_id="owned", source_system="manual_upload",
                           source_id="stable", asset_id="asset", source_version="rev")
        client.upsert(collection_name=settings.doc_collection,
                      data=[to_milvus_row(chunk, "test-row", [1.] + [0.] * 1023, "identity-test")])
        client.flush(settings.doc_collection)
        records = [{"asset_id": "asset", "doc_type": "manual"}]
        verify_upload_replacement(client, records, settings, "identity-test", "owned", "stable")
        with pytest.raises(ValueError):
            verify_upload_replacement(client, records, settings, "identity-test", "foreign", "stable")
        with pytest.raises(ValueError):
            verify_upload_replacement(client, records, settings, "identity-test", "owned", "renamed")
    finally:
        if client.has_collection(settings.doc_collection): client.drop_collection(settings.doc_collection)
        client.close()
