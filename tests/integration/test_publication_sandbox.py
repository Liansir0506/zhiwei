"""Real publication in an ephemeral database and collections; never switch shared ACTIVE."""
import json
import re
import subprocess
import uuid
from pathlib import Path

import pytest
import sqlalchemy as sa
from fastapi import HTTPException
from pymilvus import MilvusClient

from qa_core.api import route_kb
from qa_core.api.auth import AuthContext
from qa_core.config.settings import get_settings
from qa_core.indexing.chunkers.base import make_chunk
from qa_core.indexing.embedder.embedder import get_embedder
from qa_core.quality.gate import run_gate, save_report
from qa_core.storage.kb_dao import KbDao
from scripts.init_collections import build
from scripts.rebuild_kb import to_milvus_row


def root_sql(sql):
    # Password stays inside the test container; never read or print its value.
    result = subprocess.run(["docker", "exec", "-i", "telecom-ops-rag-test-mysql-1", "sh", "-c",
                             'MYSQL_PWD="$MYSQL_ROOT_PASSWORD" exec mysql -uroot'],
                            input=sql, text=True, capture_output=True)
    if result.returncode:
        raise RuntimeError("isolated sandbox database operation failed")


@pytest.fixture
def sandbox(monkeypatch, tmp_path):
    base = get_settings()
    url = sa.engine.make_url(base.mysql_dsn)
    if url.database != "rag_kb_test" or url.port != 13306 or url.username != "rag":
        pytest.fail("sandbox requires the verified local test MySQL profile")
    shared_engine = sa.create_engine(base.mysql_dsn)
    with shared_engine.connect() as conn:
        shared_before = conn.execute(sa.text("SELECT version,status FROM kb_versions ORDER BY version")).all()
    token = uuid.uuid4().hex[:12]
    database = "test_publication_" + token
    assert re.fullmatch(r"test_publication_[a-f0-9]{12}", database)
    root_sql(f"CREATE DATABASE `{database}` CHARACTER SET utf8mb4; GRANT ALL ON `{database}`.* TO 'rag'@'%';")
    settings = base.model_copy(update={"mysql_dsn": url.set(database=database).render_as_string(hide_password=False),
        "doc_collection": "test_publication_doc_" + token, "faq_collection": "test_publication_faq_" + token,
        "reports_dir": str(tmp_path / "reports")})
    engine = sa.create_engine(settings.mysql_dsn)
    client = MilvusClient(uri=settings.milvus_uri, timeout=60)
    try:
        with engine.begin() as conn:
            for filename in ("01_schema.sql", "02_enterprise_domain.sql", "03_security_governance.sql", "04_ingest_jobs.sql", "05_version_manifests.sql", "06_source_withdrawals.sql"):
                sql = Path("docker/mysql/init", filename).read_text(encoding="utf-8")
                sql = re.sub(r"--[^\n]*", "", sql)
                for statement in sql.split(";"):
                    if statement.strip(): conn.exec_driver_sql(statement)
        build(client, settings.doc_collection, False)
        build(client, settings.faq_collection, True)
        monkeypatch.setattr(route_kb, "get_settings", lambda: settings)
        # Real verifier/gate/cache remain intact. Versions contain a unique namespace.
        yield settings, engine, client, token
    finally:
        try:
            client = MilvusClient(uri=settings.milvus_uri, timeout=60)
            for name in (settings.doc_collection, settings.faq_collection):
                if client.has_collection(name): client.drop_collection(name)
            client.close()
        finally:
            from qa_core.security.withdrawals import _ledger_engine
            _ledger_engine(settings.mysql_dsn).dispose()
            engine.dispose()
            root_sql(f"DROP DATABASE `{database}`; REVOKE ALL ON `{database}`.* FROM 'rag'@'%';")
            with shared_engine.connect() as conn:
                assert conn.execute(sa.text("SELECT version,status FROM kb_versions ORDER BY version")).all() == shared_before
            shared_engine.dispose()


def prepare(settings, engine, client, version, source_id):
    embedder = get_embedder(settings.embedding_model_path, settings.embedding_device)
    text = "沙箱独立告警案例：检查接口连通状态，仅用于发布事务验收。"
    client = MilvusClient(uri=settings.milvus_uri, timeout=60)
    chunk = make_chunk(text, "fault_case", doc_type="fault_case", source=version + ".md", tenant_id="default", source_system="sandbox",
        source_id=source_id, source_version="r1", asset_id="asset-" + version,
        equipment_type="UPF", alarm_code="SBX-001", status="active", visibility="public")
    row = to_milvus_row(chunk, "row-" + version, embedder.embed_passages([text])[0], version)
    dao = KbDao(settings.mysql_dsn)
    try:
        dao.ensure_version(version)
        dao.upsert_record("hash-" + version, version + ".md", "fault_case", version, asset_id="asset-" + version)
        dao.set_status("hash-" + version, version, "done", chunk_count=1)
        client.upsert(collection_name=settings.doc_collection, data=[row]); client.flush(settings.doc_collection)
        result = run_gate(settings, version)
        assert result.passed and len(result.checks) == 5
        report = save_report(result, version, settings.reports_dir)
        dao.set_quality_report(version, report)
    finally: dao.engine.dispose()


def active(engine):
    with engine.connect() as conn:
        return conn.execute(sa.text("SELECT version FROM kb_versions WHERE status='ACTIVE'")).scalars().all()


def test_real_gate_publish_withdraw_and_rollback_refusal(sandbox):
    settings, engine, client, token = sandbox
    first, second, third = "pub-a-" + token, "pub-b-" + token, "pub-c-" + token
    ctx = AuthContext("sandbox-admin", ["admin"], "api_key")
    prepare(settings, engine, client, first, "retired")
    prepare(settings, engine, client, second, "retained")
    prepare(settings, engine, client, third, "new-retained")
    from qa_core.cache.redis_cache import CacheLayer
    cache = CacheLayer(settings.redis_url)
    assert not cache.bypass
    cache.set("ret", first, "sandbox", token, [{"text": "old"}])
    assert route_kb.activate(route_kb.ActivateRequest(version=first), ctx)["activated"] == first
    assert cache.get("ret", first, "sandbox", token) is None
    cache.set("ret", first, "sandbox", token, [{"text": "old"}])
    cache.set("ret", second, "sandbox", token, [{"text": "new"}])
    result = route_kb.activate(route_kb.ActivateRequest(version=second), ctx)
    assert result["archived"] == first and result["cache_invalidation_ok"]
    assert cache.get("ret", first, "sandbox", token) is None
    assert cache.get("ret", second, "sandbox", token) is None
    assert active(engine) == [second]
    # Deterministically recreate the combined-suite stale-singleton failure.
    from qa_core.storage.milvus_client import get_milvus_client
    get_milvus_client(settings.milvus_uri).close()
    assert route_kb.withdraw_source(route_kb.WithdrawRequest(source_system="sandbox", source_id="retired", reason="sandbox"), ctx)["created"]
    from qa_core.quality.publication import verify_publication
    with engine.connect() as conn:
        report = conn.execute(sa.text("SELECT quality_report_path FROM kb_versions WHERE version=:v"), {"v": first}).scalar()
    with pytest.raises(ValueError, match="撤回"):
        verify_publication(settings, first, report)
    with pytest.raises(HTTPException) as exc:
        route_kb.rollback(route_kb.ActivateRequest(version=first), ctx)
    assert exc.value.status_code == 400 and "撤回" in str(exc.value.detail)
    assert active(engine) == [second]
    route_kb.activate(route_kb.ActivateRequest(version=third), ctx)
    cache.set("ret", second, "sandbox", token, [{"text": "archived"}])
    cache.set("ret", third, "sandbox", token, [{"text": "current"}])
    result = route_kb.rollback(route_kb.ActivateRequest(version=second, actor="spoofed"), ctx)
    assert result["activated"] == second and result["archived"] == third
    assert result["cache_invalidation_ok"]
    assert cache.get("ret", second, "sandbox", token) is None
    assert cache.get("ret", third, "sandbox", token) is None
    assert active(engine) == [second]
    old_settings = route_kb.get_settings
    try:
        route_kb.get_settings = lambda: settings.model_copy(update={"doc_collection": "other"})
        with pytest.raises(HTTPException) as drift:
            route_kb.rollback(route_kb.ActivateRequest(version=third), ctx)
        assert drift.value.status_code == 400 and "绑定" in str(drift.value.detail)
    finally:
        route_kb.get_settings = old_settings
    # Real database audit failure must roll back both pointer updates atomically.
    database = sa.engine.make_url(settings.mysql_dsn).database
    assert re.fullmatch(r"test_publication_[a-f0-9]{12}", database)
    # Binlogging requires the test container's root to create triggers. Do not
    # elevate the app account or alter global log_bin_trust_function_creators.
    root_sql(f"USE `{database}`; CREATE TRIGGER sandbox_deny_rollback BEFORE INSERT ON audit_log "
             "FOR EACH ROW SIGNAL SQLSTATE '45000' SET MESSAGE_TEXT='sandbox audit failure';")
    try:
        with pytest.raises(HTTPException) as audit_failure:
            route_kb.rollback(route_kb.ActivateRequest(version=third), ctx)
        assert audit_failure.value.status_code == 503
        assert active(engine) == [second]
        with engine.connect() as conn:
            assert conn.execute(sa.text("SELECT status FROM kb_versions WHERE version=:v"), {"v": third}).scalar() == "ARCHIVED"
    finally:
        root_sql(f"USE `{database}`; DROP TRIGGER sandbox_deny_rollback;")
    client = MilvusClient(uri=settings.milvus_uri, timeout=60)
    schema = client.describe_collection(collection_name=settings.doc_collection)
    fields = [f["name"] for f in schema["fields"] if f["name"] != "sparse"]
    rows = client.query(collection_name=settings.doc_collection, filter=f'kb_version == "{third}"',
                        output_fields=fields, consistency_level="Strong")
    rows[0]["text"] = "modified after publication"
    client.upsert(collection_name=settings.doc_collection, data=rows); client.flush(settings.doc_collection)
    with pytest.raises(HTTPException) as changed:
        route_kb.rollback(route_kb.ActivateRequest(version=third), ctx)
    assert changed.value.status_code == 400 and "快照" in str(changed.value.detail)
    assert active(engine) == [second]
    with engine.connect() as conn:
        audit = conn.execute(sa.text("SELECT action,actor,payload_json FROM audit_log ORDER BY id")).all()
    assert [r[0] for r in audit] == ["activate", "activate", "withdraw_source", "activate", "rollback"]
    assert all(r[1] == "sandbox-admin" for r in audit)
