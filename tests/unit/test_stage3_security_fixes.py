from unittest.mock import Mock
from contextlib import nullcontext
import pytest
from fastapi import HTTPException
from qa_core.api.auth import AuthContext
from qa_core.api import route_admin, route_ingest
from qa_core.storage import milvus_client as storage
from pymilvus.exceptions import ConnectionNotExistException


@pytest.fixture(autouse=True)
def no_audit_database(monkeypatch):
    monkeypatch.setattr("qa_core.security.audit.audit_access", Mock())


@pytest.mark.parametrize("tenant,roles", [("other", ["admin"]), ("default", ["ops"])])
def test_global_delete_denied_before_storage(monkeypatch, tenant, roles):
    delete = Mock()
    monkeypatch.setattr("scripts.rebuild_kb.delete_file_chunks", delete)
    with pytest.raises(HTTPException) as exc:
        route_admin.admin_delete_document("manual_foreign.md", "candidate", AuthContext("u", roles, "test", tenant_id=tenant))
    assert exc.value.status_code == 403
    delete.assert_not_called()


def test_global_admin_delete_remains_available(monkeypatch):
    delete = Mock(return_value={"chunks_deleted": 1, "records_deleted": 1})
    monkeypatch.setattr("scripts.rebuild_kb.delete_file_chunks", delete)
    monkeypatch.setattr("qa_core.storage.kb_dao.KbDao", Mock())
    monkeypatch.setattr(storage, "get_milvus_client", Mock())
    monkeypatch.setattr("qa_core.cache.redis_cache.CacheLayer", Mock())
    result = route_admin.admin_delete_document("manual_own.md", "candidate", AuthContext("u", ["admin"], "test"))
    assert result["chunks_deleted"] == 1
    delete.assert_called_once()


def test_tenant_admin_cannot_submit_global_rebuild(monkeypatch):
    raw = Mock()
    monkeypatch.setattr(route_ingest, "_safe_raw_dir", raw)
    with pytest.raises(HTTPException) as exc:
        route_ingest.create_rebuild(route_ingest.RebuildRequest(kb_version="candidate"), AuthContext("u", ["admin"], "test", tenant_id="other"))
    assert exc.value.status_code == 403
    raw.assert_not_called()


def test_tenant_cannot_retry_legacy_global_rebuild(monkeypatch):
    dao = Mock()
    dao.get.return_value = {"job_type": "rebuild", "status": "failed", "kb_version": "candidate"}
    monkeypatch.setattr(route_ingest, "_dao", lambda: dao)
    with pytest.raises(HTTPException) as exc:
        route_ingest.retry_job("job", AuthContext("u", ["admin"], "test", tenant_id="other"))
    assert exc.value.status_code == 403
    dao.retry.assert_not_called()


@pytest.mark.parametrize("operation", ["query", "describe_collection"])
def test_disconnected_read_reconnects_once(monkeypatch, operation):
    old, new = Mock(), Mock()
    getattr(old, operation).side_effect = ConnectionNotExistException(message="closed")
    getattr(new, operation).return_value = {"ok": True}
    clients = iter([old, new])
    monkeypatch.setattr(storage, "_clients", {"uri": old})
    monkeypatch.setattr(storage, "get_milvus_client", lambda *a: next(clients))
    assert storage.read_with_retry("uri", operation, collection_name="own") == {"ok": True}
    assert "uri" not in storage._clients
    old.close.assert_not_called()
    assert getattr(old, operation).call_count == getattr(new, operation).call_count == 1


def test_repeated_disconnect_stops_after_two_attempts(monkeypatch):
    client = Mock()
    client.query.side_effect = ConnectionNotExistException(message="closed")
    monkeypatch.setattr(storage, "get_milvus_client", lambda *a: client)
    monkeypatch.setattr(storage, "_clients", {})
    with pytest.raises(ConnectionNotExistException): storage.read_with_retry("uri", "query")
    assert client.query.call_count == 2


def test_other_storage_errors_are_not_retried_or_hidden(monkeypatch):
    client = Mock()
    client.query.side_effect = RuntimeError("service unavailable")
    monkeypatch.setattr(storage, "get_milvus_client", lambda *a: client)
    with pytest.raises(RuntimeError): storage.read_with_retry("uri", "query")
    assert client.query.call_count == 1
    with pytest.raises(ValueError): storage.read_with_retry("uri", "delete")


def test_reconnect_does_not_evict_another_callers_replacement(monkeypatch):
    old, new = Mock(), Mock()
    monkeypatch.setattr(storage, "_clients", {"uri": old})
    def missing(**kwargs):
        storage._clients["uri"] = new
        raise ConnectionNotExistException(message="closed")
    old.query.side_effect = missing
    new.query.return_value = []
    monkeypatch.setattr(storage, "get_milvus_client", lambda uri: storage._clients[uri])
    assert storage.read_with_retry("uri", "query") == []
    assert storage._clients["uri"] is new
