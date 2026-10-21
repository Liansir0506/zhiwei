"""Real original-content samples remain unreadable in an isolated pending preview.

Reads original vectors only; writes three quarantined copies to new test-only
collections, never promotes approval or changes shared ACTIVE. No LLM/models.
"""
import hashlib
import json
import os
from pathlib import Path
import secrets
import uuid
from unittest.mock import Mock

from dotenv import dotenv_values
from fastapi.testclient import TestClient
from pymilvus import MilvusClient
import pytest
import sqlalchemy as sa

from qa_core.api import auth, route_evidence
from qa_core.api.auth import create_api_key, hash_key
from qa_core.application.evidence_service import EvidenceService
from qa_core.config.settings import get_settings
from qa_core.retrieval.filters import _to_doc
from qa_core.retrieval.searcher import Searcher
from qa_core.schemas.evidence import evidence_of
from qa_core.storage.kb_dao import KbDao
from qa_core.storage.location_dao import LocationDao
from qa_core.storage.version_manifest import bind_candidate
from scripts.build_legacy_identity_review import content_key, validate_package
from scripts.init_collections import build
from scripts.migrate_legacy_collections import adapt


def test_actual_legacy_faq_ticket_sop_remain_denied_when_pending(monkeypatch):
    root = Path(__file__).resolve().parents[2]
    package = root / "data/candidates/legacy-identity-review-r2"
    if not package.exists():
        pytest.skip("content-bound legacy review package required")
    assert validate_package(package)["approved"] == 0
    settings = get_settings()
    assert settings.is_test and sa.engine.make_url(settings.mysql_dsn).database == "rag_kb_test"
    suffix = uuid.uuid4().hex[:12]
    cfg = settings.model_copy(update={"doc_collection": "test_legacy_pending_" + suffix + "_doc",
        "faq_collection": "test_legacy_pending_" + suffix + "_faq", "auth_required": True})
    version = "legacy-pending-" + suffix
    before = KbDao(settings.mysql_dsn).get_version("v1")
    traces = json.loads((package / "row-trace.json").read_text(encoding="utf-8"))["rows"]
    entries = {e["filename"]: e for e in json.loads((package / "source-proposals.json").read_text(encoding="utf-8"))["entries"]}
    selected = [next(t for t in traces if t["status"] == "exact_trace" and t.get("filename", "").startswith(prefix))
                for prefix in ("faq_", "fault_case_", "sop_")]
    original = dotenv_values(root / ".env.runtime")
    source_client = MilvusClient(uri=original["MILVUS_URI"], timeout=30)
    rows, docs = [], []
    try:
        for trace in selected:
            name = original["FAQ_COLLECTION"] if trace["collection_kind"] == "faq" else original["DOC_COLLECTION"]
            source = source_client.query(collection_name=name, filter='id == ' + json.dumps(trace["chunk_id"]) + ' and kb_version == "v4-foundation-r3"',
                output_fields=["*"], limit=2, consistency_level="Strong")
            assert len(source) == 1
            assert hashlib.sha256(content_key(source[0]).encode()).hexdigest() == trace["content_sha256"]
            entry = entries[trace["filename"]]
            row = adapt(source[0])
            for field, value in entry["original_scope_metadata"].items():
                if field in row and field not in {"allowed_roles", "source", "doc_type", "equipment_type", "vendor", "network_tech"}:
                    row[field] = value
            row.update(kb_version=version, asset_id=entry["proposed_asset_id"], source_id=entry["proposed_source_id"],
                       source_version=entry["existing_source_version"], status="draft", review_status="pending")
            rows.append(row)
            docs.append(_to_doc({"id": row["id"], "entity": row}))
    finally:
        source_client.close()
    client = MilvusClient(uri=cfg.milvus_uri, timeout=30)
    dao = KbDao(cfg.mysql_dsn)
    location = LocationDao(dao.engine)
    keys = [(secrets.token_urlsafe(24), ["admin"]), (secrets.token_urlsafe(24), ["ops"])]
    created = []
    try:
        for name, faq in ((cfg.doc_collection, False), (cfg.faq_collection, True)):
            assert name.startswith("test_legacy_pending_") and not client.has_collection(name)
            build(client, name, faq); created.append(name)
        dao.ensure_version(version); bind_candidate(cfg, version)
        location.ensure_schema()
        for trace, row, doc in zip(selected, rows, docs):
            collection = cfg.faq_collection if trace["collection_kind"] == "faq" else cfg.doc_collection
            client.insert(collection_name=collection, data=[row])
            location.save(doc, trace["location"])
        for name in created:
            client.flush(name)
        embedder, reranker = Mock(), Mock()
        cache = Mock(); cache.get_with_lock.return_value = None
        searcher = Searcher(client=client, settings=cfg, embedder=embedder, cache=cache)
        service = EvidenceService(searcher, reranker, cfg)
        monkeypatch.setattr(auth, "get_settings", lambda: cfg)
        monkeypatch.setattr(route_evidence, "get_settings", lambda: cfg)
        monkeypatch.setattr(route_evidence, "get_evidence_service", lambda: service)
        for key, roles in keys:
            create_api_key(key, "legacy-pending-test-" + suffix, roles)
        from app import create_app
        checks = []
        with TestClient(create_app()) as http:
            for trace, doc in zip(selected, docs):
                url = evidence_of(doc, trace["collection_kind"])["source_url"]
                codes = [http.get(url).status_code,
                    http.get(url, headers={"X-API-Key": keys[0][0]}).status_code,
                    http.get(url, headers={"X-API-Key": keys[1][0], "X-User-Roles": "admin"}).status_code]
                assert codes == [401, 404, 404]
                checks.append({"filename": trace["filename"], "status_codes": codes, "original_location": trace["location"]})
        embedder.embed_query.assert_not_called(); reranker.rerank.assert_not_called()
        assert dao.get_version(version)["status"] == "STAGED" and dao.get_version("v1") == before
        assert all(r["status"] == "draft" and r["review_status"] == "pending" for r in rows)
        if os.getenv("LEGACY_IDENTITY_REPORT_DIR"):
            path = Path(os.environ["LEGACY_IDENTITY_REPORT_DIR"]) / "pending-preview.json"
            path.write_text(json.dumps({"passed": True, "checks": checks, "paid_llm_calls": 0,
                "original_vector_writes": 0, "candidate_promoted": False, "test_active_unchanged": True}, ensure_ascii=False, indent=2), encoding="utf-8")
    finally:
        with dao.engine.begin() as conn:
            for doc in docs:
                conn.execute(sa.text("DELETE FROM evidence_locations WHERE identity_hash=:id"), {"id": location.identity(doc)})
            for key, _ in keys:
                conn.execute(sa.text("DELETE FROM api_keys WHERE key_hash=:h"), {"h": hash_key(key)})
            conn.execute(sa.text("DELETE FROM kb_version_manifests WHERE version=:v"), {"v": version})
            conn.execute(sa.text("DELETE FROM kb_versions WHERE version=:v AND status='STAGED'"), {"v": version})
        for name in created:
            assert name.startswith("test_legacy_pending_" + suffix)
            client.drop_collection(name)
        client.close(); dao.engine.dispose()
