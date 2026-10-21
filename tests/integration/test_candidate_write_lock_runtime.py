"""Real shared MySQL lock, own STAGED fixture; never change the global ACTIVE."""
import uuid
from concurrent.futures import ThreadPoolExecutor

import pytest
import sqlalchemy as sa
from fastapi import HTTPException

from qa_core.config.settings import get_settings
from qa_core.ingest.write_guard import candidate_write
from qa_core.api.route_kb import _activate


def test_real_write_lock_blocks_publication_and_other_writer_and_rechecks_status():
    settings = get_settings()
    version = "guard-it-" + uuid.uuid4().hex[:12]
    engine = sa.create_engine(settings.mysql_dsn)
    def read_active():
        with engine.connect() as conn:
            return list(conn.execute(sa.text("SELECT version,quality_report_path,updated_at FROM kb_versions WHERE status='ACTIVE' ORDER BY version")))
    before = read_active()
    try:
        with candidate_write(version, settings=settings):
            with engine.begin() as conn:
                conn.execute(sa.text("UPDATE kb_versions SET quality_report_path='fixture' WHERE version=:v"), {"v": version})
            def conflicting_writer():
                with candidate_write(version, settings=settings):
                    pytest.fail("concurrent writer entered")
            with ThreadPoolExecutor(max_workers=1) as pool:
                with pytest.raises(HTTPException) as exc:
                    pool.submit(conflicting_writer).result(timeout=10)
                assert exc.value.status_code == 409
            with pytest.raises(HTTPException) as publishing:
                _activate(version, "test-fixture")
            assert publishing.value.status_code == 409
        with candidate_write(version, settings=settings):
            with engine.connect() as conn:
                assert conn.execute(sa.text("SELECT quality_report_path FROM kb_versions WHERE version=:v"), {"v": version}).scalar() is None
        with engine.begin() as conn:
            conn.execute(sa.text("UPDATE kb_versions SET status='ARCHIVED' WHERE version=:v"), {"v": version})
        with pytest.raises(HTTPException) as archived:
            with candidate_write(version, settings=settings):
                pytest.fail("archived candidate entered")
        assert archived.value.status_code == 409
        assert read_active() == before
    finally:
        with engine.begin() as conn:
            conn.execute(sa.text("DELETE FROM kb_versions WHERE version=:v AND status!='ACTIVE'"), {"v": version})
        engine.dispose()
