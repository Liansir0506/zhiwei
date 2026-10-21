import copy
import json
from contextlib import contextmanager
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest
import sqlalchemy as sa
from fastapi import HTTPException

from qa_core.api import route_kb
from qa_core.api.auth import AuthContext
from qa_core.quality import publication as pub
from qa_core.quality.gate import GateResult, check_sop_integrity, save_report


def report_payload():
    return {"kb_version": "candidate", "passed": True,
            "checks": [{"check": c, "passed": True} for c in sorted(pub.CHECKS)],
            "snapshot": {"protocol": pub.PROTOCOL, "value": "original"}}


@pytest.fixture
def report(tmp_path):
    settings = SimpleNamespace(reports_dir=str(tmp_path))
    path = tmp_path / "quality_candidate.json"
    path.write_text(json.dumps(report_payload()), encoding="utf-8")
    return settings, path


@pytest.mark.parametrize("change", [
    {"passed": False}, {"passed": "true"}, {"kb_version": "wrong"},
    {"checks": []}, {"snapshot": None}, {"snapshot": {"protocol": "old"}},
    {"checks": [{"check": c, "passed": False} for c in sorted(pub.CHECKS)]},
    {"checks": [{"check": "duplicates", "passed": True}] * 5},
])
def test_invalid_report_rejected(report, change):
    settings, path = report
    payload = report_payload()
    payload.update(change)
    path.write_text(json.dumps(payload), encoding="utf-8")
    with pytest.raises(ValueError):
        pub.load_bound_report(settings, "candidate", str(path))


def test_report_rejects_missing_outside_and_invalid_json(report, tmp_path):
    settings, path = report
    for target in (tmp_path / "missing", tmp_path.parent / "outside"):
        with pytest.raises(ValueError):
            pub.load_bound_report(settings, "candidate", str(target))
    path.write_text("not json", encoding="utf-8")
    with pytest.raises(ValueError):
        pub.load_bound_report(settings, "candidate", str(path))


def test_non_applicable_check_requires_scoped_profile(report):
    settings, path = report
    payload = report_payload()
    payload["checks"][0]["applicable"] = False
    path.write_text(json.dumps(payload), encoding="utf-8")
    with pytest.raises(ValueError, match="范围"):
        pub.load_bound_report(settings, "candidate", str(path))


@pytest.mark.parametrize("version", ["../evil", 'v" or 1==1', "x" * 33, ""])
def test_version_rejects_injection_and_path_traversal(version):
    with pytest.raises(ValueError):
        pub.validate_version(version)


def test_verify_binds_live_data(report, monkeypatch):
    from qa_core.storage import version_manifest
    monkeypatch.setattr(version_manifest, "verify_read_binding", lambda *a: {"binding": "fixture", "snapshot": report_payload()["snapshot"]})
    settings, path = report
    monkeypatch.setattr(pub, "capture_snapshot", lambda *_: report_payload()["snapshot"])
    assert pub.verify_publication(settings, "candidate", str(path))["passed"]
    monkeypatch.setattr(pub, "capture_snapshot", lambda *_: {"protocol": pub.PROTOCOL, "value": "changed"})
    with pytest.raises(ValueError, match="快照"):
        pub.verify_publication(settings, "candidate", str(path))


def test_save_report_carries_binding(tmp_path):
    result = GateResult(snapshot=report_payload()["snapshot"])
    for check in sorted(pub.CHECKS):
        result.add(check, True, {})
    path = save_report(result, "candidate", str(tmp_path))
    assert pub.load_bound_report(SimpleNamespace(reports_dir=str(tmp_path)), "candidate", path)["snapshot"] == result.snapshot


@pytest.mark.parametrize("steps,expected", [
    ([[0], [1, 2], [3]], True), ([[0], [1], [3]], False),
    ([[0]], False), ([[1], [1, 2]], False), ([[], [1]], False),
    ([[True], [1]], False), ([["1"]], False), ([[None]], False),
])
def test_sop_json_steps_cannot_silently_pass(steps, expected):
    client = MagicMock()
    client.query.return_value = [{"id": str(i), "source": "sop", "step_index": s} for i, s in enumerate(steps)]
    assert check_sop_integrity(client, "candidate")[0] is expected


def snapshot_fixture(tmp_path):
    settings = SimpleNamespace(mysql_dsn=f"sqlite:///{tmp_path / 'ledger.sqlite'}",
                               milvus_uri="http://test:19530", doc_collection="docs", faq_collection="faqs")
    engine = sa.create_engine(settings.mysql_dsn)
    with engine.begin() as conn:
        conn.execute(sa.text("CREATE TABLE ingest_records (file_hash TEXT, filename TEXT, asset_id TEXT, doc_type TEXT, status TEXT, chunk_count INTEGER, kb_version TEXT)"))
        conn.execute(sa.text("INSERT INTO ingest_records VALUES ('hash', 'file', 'asset', 'manual', 'done', 2, 'candidate')"))
    engine.dispose()
    fields = ["id", "kb_version", "asset_id", "source_version", "tenant_id", "dept_id",
              "visibility", "allowed_roles", "role_scope", "security_level", "device_type",
              "status", "effective_from", "effective_to", "text", "dense"]
    rows = [{"id": "a", "text": "original", "dense": [0.1, 0.2], "status": "active"},
            {"id": "b", "text": "tail", "dense": [0.3, 0.4], "status": "active"}]
    client = MagicMock()
    client.describe_collection.return_value = {"fields": [{"name": f} for f in fields]}

    def iterator(**kw):
        assert kw["consistency_level"] == "Strong"
        assert "dense" in kw["output_fields"]
        result = MagicMock()
        # More than one page: ensure the tail is included, not first-page sampling.
        result.next.side_effect = [[copy.deepcopy(r)] for r in rows] + [[]] if kw["collection_name"] == "docs" else [[]]
        return result
    client.query_iterator.side_effect = iterator
    return settings, client, rows


@pytest.mark.parametrize("field,value", [("text", "changed"), ("dense", [0.9, 0.1]),
                                         ("status", "retired"), ("tenant_id", "other")])
def test_snapshot_detects_tail_text_vectors_withdrawal_and_acl(tmp_path, field, value):
    settings, client, rows = snapshot_fixture(tmp_path)
    before = pub.capture_snapshot(settings, "candidate", client)
    assert before["collections"]["doc"]["count"] == 2
    rows[-1][field] = value
    assert pub.capture_snapshot(settings, "candidate", client) != before


def test_snapshot_rejects_incomplete_ledger_and_missing_schema(tmp_path):
    settings, client, rows = snapshot_fixture(tmp_path)
    engine = sa.create_engine(settings.mysql_dsn)
    with engine.begin() as conn:
        conn.execute(sa.text("UPDATE ingest_records SET status='loaded'"))
    engine.dispose()
    with pytest.raises(ValueError, match="未完成"):
        pub.capture_snapshot(settings, "candidate", client)
    client.describe_collection.return_value = {"fields": [{"name": "id"}]}
    with pytest.raises(ValueError, match="生命周期"):
        pub.capture_snapshot(settings, "candidate", client)


def test_bm25_derived_output_not_read_but_function_definition_bound(tmp_path):
    settings, client, _ = snapshot_fixture(tmp_path)
    schema = copy.deepcopy(client.describe_collection.return_value)
    schema["fields"].append({"name": "sparse", "is_function_output": True})
    schema["functions"] = [{"type": 1, "input_field_names": ["text"], "output_field_names": ["sparse"]}]
    client.describe_collection.return_value = schema
    before = pub.capture_snapshot(settings, "candidate", client)
    assert all("sparse" not in c.kwargs["output_fields"] for c in client.query_iterator.call_args_list)
    schema["functions"][0]["params"] = {"changed": "true"}
    assert pub.capture_snapshot(settings, "candidate", client) != before


def test_snapshot_is_json_roundtrip_stable_and_ignores_only_operational_timestamp(tmp_path):
    settings, client, _ = snapshot_fixture(tmp_path)
    schema = client.describe_collection.return_value
    schema["update_timestamp"] = 1
    schema["collection_id"] = 100
    before = pub.capture_snapshot(settings, "candidate", client)
    assert json.loads(json.dumps(before)) == before
    schema["update_timestamp"] = 2
    assert pub.capture_snapshot(settings, "candidate", client) == before
    schema["collection_id"] = 101
    assert pub.capture_snapshot(settings, "candidate", client) != before


class FakeConnection:
    def __init__(self, status="STAGED", path="report", active=None, lock=1):
        self.status, self.path = status, path
        self.active = active or []
        self.lock = lock
        self.calls, self.writes = [], []
        self.committed = False

    def __enter__(self):
        return self

    def __exit__(self, *_):
        pass

    def commit(self):
        pass

    @contextmanager
    def begin(self):
        try:
            yield self
        except Exception:
            self.writes.clear()
            raise
        else:
            self.committed = True

    def execute(self, statement, params=None):
        sql = str(statement)
        self.calls.append((sql, params))
        result = MagicMock()
        if "GET_LOCK" in sql:
            result.scalar.return_value = self.lock
        elif "SELECT COUNT(*) FROM ingest_jobs" in sql:
            result.scalar.return_value = 0
        elif "SELECT status" in sql:
            result.first.return_value = (self.status, self.path) if self.status else None
        elif "SELECT version" in sql:
            result.scalars.return_value.all.return_value = self.active
        elif sql.startswith(("UPDATE", "INSERT")):
            self.writes.append((sql, params))
        return result


def setup_publisher(monkeypatch, connection):
    engine = MagicMock()
    engine.connect.return_value = connection
    monkeypatch.setattr(route_kb, "_engine", lambda: engine)
    monkeypatch.setattr(route_kb, "get_settings", lambda: SimpleNamespace(redis_url="unused"))
    monkeypatch.setattr(route_kb, "verify_publication", lambda *_: report_payload())
    cache = MagicMock()
    cache.invalidate_version.return_value = 2
    monkeypatch.setattr(route_kb, "CacheLayer", lambda *_: cache)
    return engine, cache


@pytest.mark.parametrize("status,path,active,lock,action,code", [
    (None, "report", [], 1, "activate", 404),
    ("ACTIVE", "report", [], 1, "activate", 400),
    ("ARCHIVED", "report", [], 1, "activate", 400),
    ("STAGED", None, [], 1, "activate", 400),
    ("STAGED", "report", ["a", "b"], 1, "activate", 409),
    ("STAGED", "report", [], 0, "activate", 409),
    ("ARCHIVED", "report", [], 1, "rollback", 409),
    ("STAGED", "report", ["old"], 1, "rollback", 400),
    ("ARCHIVED", None, ["old"], 1, "rollback", 400),
    ("ARCHIVED", "report", ["a", "b"], 1, "rollback", 409),
])
def test_publication_rejections_never_write(monkeypatch, status, path, active, lock, action, code):
    conn = FakeConnection(status, path, active, lock)
    engine, _ = setup_publisher(monkeypatch, conn)
    with pytest.raises(HTTPException) as exc:
        route_kb._activate("candidate", "verified", action=action)
    assert exc.value.status_code == code
    assert not conn.writes
    engine.dispose.assert_called_once()


def test_normal_publication_audit_and_cache_both_versions(monkeypatch):
    conn = FakeConnection(active=["old"])
    _, cache = setup_publisher(monkeypatch, conn)
    result = route_kb._activate("candidate", "verified")
    assert conn.committed and len(conn.writes) == 3
    assert conn.writes[-1][1]["act"] == "activate"
    assert conn.writes[-1][1]["a"] == "verified"
    assert {c.args[0] for c in cache.invalidate_version.call_args_list} == {"old", "candidate"}
    assert result["cache_invalidated"] == 4
    assert any("RELEASE_LOCK" in sql for sql, _ in conn.calls)


def test_safe_rollback_reverifies_and_audits(monkeypatch):
    conn = FakeConnection(status="ARCHIVED", active=["old"])
    _, cache = setup_publisher(monkeypatch, conn)
    verify = MagicMock(return_value=report_payload())
    monkeypatch.setattr(route_kb, "verify_publication", verify)
    result = route_kb._activate("candidate", "verified", action="rollback")
    verify.assert_called_once()
    assert conn.writes[-1][1]["act"] == "rollback"
    assert result["activated"] == "candidate" and result["archived"] == "old"
    assert {c.args[0] for c in cache.invalidate_version.call_args_list} == {"old", "candidate"}


def test_report_failure_rolls_back_and_releases_lock(monkeypatch):
    conn = FakeConnection(active=["old"])
    setup_publisher(monkeypatch, conn)
    def fail(*_):
        raise ValueError("binding changed")
    monkeypatch.setattr(route_kb, "verify_publication", fail)
    with pytest.raises(HTTPException) as exc:
        route_kb._activate("candidate", "verified")
    assert exc.value.status_code == 400 and not conn.writes
    assert any("RELEASE_LOCK" in sql for sql, _ in conn.calls)


def test_cache_failure_does_not_claim_database_rollback(monkeypatch):
    conn = FakeConnection()
    _, cache = setup_publisher(monkeypatch, conn)
    cache.invalidate_version.side_effect = RuntimeError("redis unavailable")
    result = route_kb._activate("candidate", "verified")
    assert conn.committed and result["cache_invalidation_ok"] is False


def test_actor_comes_from_authentication_and_tenant_admin_denied(monkeypatch):
    publish = MagicMock(return_value={})
    monkeypatch.setattr(route_kb, "_activate", publish)
    ctx = AuthContext("verified", ["admin"], "api_key")
    route_kb.activate(route_kb.ActivateRequest(version="candidate", actor="spoofed"), ctx)
    publish.assert_called_once_with("candidate", "verified")
    ctx.tenant_id = "other"
    with pytest.raises(HTTPException) as exc:
        route_kb.activate(route_kb.ActivateRequest(version="candidate"), ctx)
    assert exc.value.status_code == 403


@pytest.mark.parametrize("action,status", [("activate", "STAGED"), ("rollback", "ARCHIVED")])
def test_audit_failure_rolls_back_pointer_writes(monkeypatch, action, status):
    conn = FakeConnection(status=status, active=["old"])
    setup_publisher(monkeypatch, conn)
    original = conn.execute
    def execute(statement, params=None):
        if str(statement).startswith("INSERT INTO audit_log"):
            raise RuntimeError("audit database failure")
        return original(statement, params)
    conn.execute = execute
    with pytest.raises(HTTPException) as exc:
        route_kb._activate("candidate", "verified", action=action)
    assert exc.value.status_code == 503
    assert not conn.writes and not conn.committed


@pytest.mark.parametrize("bypass", [True, False])
def test_strict_cache_invalidation_does_not_silently_claim_success(bypass):
    from qa_core.cache.redis_cache import CacheLayer
    cache = object.__new__(CacheLayer)
    cache.bypass, cache.scenario = bypass, "sandbox"
    cache.client = MagicMock()
    cache.client.scan.side_effect = RuntimeError("unavailable")
    assert cache.invalidate_version("test") == 0  # Worker compatibility.
    with pytest.raises(RuntimeError):
        cache.invalidate_version("test", strict=True)


def test_pending_ingestion_prevents_publication(monkeypatch):
    conn = FakeConnection()
    setup_publisher(monkeypatch, conn)
    original = conn.execute
    def execute(statement, params=None):
        result = original(statement, params)
        if "SELECT COUNT(*) FROM ingest_jobs" in str(statement):
            result.scalar.return_value = 1
        return result
    conn.execute = execute
    with pytest.raises(HTTPException) as exc:
        route_kb._activate("candidate", "verified")
    assert exc.value.status_code == 409 and not conn.writes
