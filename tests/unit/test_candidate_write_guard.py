from contextlib import nullcontext
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest
from fastapi import HTTPException

from qa_core.ingest import write_guard as guard
from scripts import rebuild_kb, ingest_worker


@pytest.fixture
def guarded_db(monkeypatch):
    settings = SimpleNamespace(mysql_dsn="mysql://fixture", scenario="telecom_ops")
    engine, conn = MagicMock(), MagicMock()
    engine.connect.return_value.__enter__.return_value = conn
    state = {"status": "STAGED", "lock": 1}
    def execute(sql, params=None):
        result = MagicMock()
        text = str(sql)
        result.scalar.return_value = state["lock"] if "GET_LOCK" in text else state["status"]
        return result
    conn.execute.side_effect = execute
    monkeypatch.setattr(guard.sa, "create_engine", lambda *a, **kw: engine)
    return settings, engine, conn, state


@pytest.mark.parametrize("status", ["ACTIVE", "ARCHIVED", None])
def test_non_candidate_denied_before_mutation(guarded_db, status):
    settings, engine, conn, state = guarded_db
    state["status"] = status
    with pytest.raises(HTTPException) as exc:
        with guard.candidate_write("v-test", settings=settings):
            pytest.fail("must not enter mutation body")
    assert exc.value.status_code == 409
    assert not any("quality_report_path=NULL" in str(c.args[0]) for c in conn.execute.call_args_list)
    assert any("RELEASE_LOCK" in str(c.args[0]) for c in conn.execute.call_args_list)
    engine.dispose.assert_called_once()


def test_guard_invalidates_report_and_is_reentrant(guarded_db):
    settings, _, conn, _ = guarded_db
    with guard.candidate_write("v-test", settings=settings):
        with guard.candidate_write("v-test", settings=settings):
            pass
        with pytest.raises(HTTPException):
            with guard.candidate_write("v-other", settings=settings):
                pytest.fail("cross-version nested write")
    statements = [str(c.args[0]) for c in conn.execute.call_args_list]
    assert sum("GET_LOCK" in s for s in statements) == 1
    assert sum("quality_report_path=NULL" in s for s in statements) == 1
    assert guard._held.get() is None


def test_busy_guard_does_not_create_version_or_run_body(guarded_db):
    settings, _, conn, state = guarded_db
    state["lock"] = 0
    with pytest.raises(HTTPException) as exc:
        with guard.candidate_write("v-test", settings=settings):
            pytest.fail("busy guard must reject")
    assert exc.value.status_code == 409
    assert not any("INSERT" in str(c.args[0]) for c in conn.execute.call_args_list)


def test_body_failure_releases_guard(guarded_db):
    settings, _, conn, _ = guarded_db
    with pytest.raises(RuntimeError):
        with guard.candidate_write("v-test", settings=settings):
            raise RuntimeError("worker failed")
    assert guard._held.get() is None
    assert any("RELEASE_LOCK" in str(c.args[0]) for c in conn.execute.call_args_list)


def test_new_chunk_identity_is_version_tenant_scoped(tmp_path, monkeypatch):
    monkeypatch.setattr(guard, "candidate_write", lambda *a, **kw: nullcontext())
    monkeypatch.setattr(rebuild_kb, "_collection_field_names", lambda *a: None)
    path = tmp_path / "manual_identity.md"
    path.write_text("# heading\nsame content", encoding="utf-8")
    identities, assets = [], []
    for version, tenant in (("v-a", "default"), ("v-b", "default"), ("v-a", "other")):
        dao, client, embedder = MagicMock(), MagicMock(), MagicMock()
        dao.file_done.return_value = False
        embedder.embed_passages.side_effect = lambda texts: [[1., 0.] for _ in texts]
        stats = dict(done=0, failed=0, skipped=0, duplicates=0)
        rebuild_kb.process_file(client, dao, embedder, path, version, set(), stats,
                                extra_meta={"tenant_id": tenant}, strict_metadata=True)
        assert stats["failed"] == 0
        rows = client.upsert.call_args.kwargs["data"]
        identities.append(rows[0]["id"])
        assets.append(rows[0]["asset_id"])
    assert len(set(identities)) == 3 and len(set(assets)) == 3


def test_worker_active_rejected_before_file_copy(monkeypatch):
    from contextlib import contextmanager
    @contextmanager
    def deny(*a, **kw):
        raise HTTPException(409, "not candidate")
        yield
    monkeypatch.setattr(ingest_worker, "candidate_write", deny)
    monkeypatch.setattr(ingest_worker, "_execute_job", lambda *a, **kw: pytest.fail("worker body must not start"))
    with pytest.raises(HTTPException):
        from qa_core.ingest.binding import execution_binding
        payload = {"execution_binding": execution_binding(ingest_worker.get_settings(), "rebuild", {})}
        ingest_worker.execute_job({"kb_version": "v1", "job_type": "rebuild", "payload_json": payload}, MagicMock(), "test")


def test_upload_and_delete_no_longer_fall_back_to_active():
    import asyncio
    from qa_core.api.route_admin import admin_upload_document, admin_delete_document
    from qa_core.api.auth import AuthContext
    ctx = AuthContext("admin", ["admin"], "header")
    request = MagicMock()
    async def body(): return b"# content"
    request.body = body
    with pytest.raises(HTTPException) as upload:
        asyncio.run(admin_upload_document(request, "manual.md", "", ctx))
    with pytest.raises(HTTPException) as delete:
        admin_delete_document("manual.md", "", ctx)
    assert upload.value.status_code == delete.value.status_code == 400
