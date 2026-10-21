"""Own tenant, two STAGED versions, real ledger/API/Milvus; never withdraw business data."""
import uuid
from unittest.mock import Mock

import sqlalchemy as sa
from pymilvus import MilvusClient
from fastapi.testclient import TestClient

from app import create_app
from qa_core.api import route_kb, route_evidence
from qa_core.application.evidence_service import EvidenceService
from qa_core.application.qa_usecase import QaUseCase
from qa_core.config.settings import get_settings
from qa_core.indexing.chunkers.base import make_chunk
from qa_core.retrieval.filters import _to_doc
from qa_core.retrieval.searcher import Searcher
from qa_core.schemas.evidence import evidence_of
from qa_core.schemas.intent import IntentDecision
from qa_core.security.policy import AccessScope
from qa_core.security.withdrawals import assert_publishable_sources
from qa_core.storage.kb_dao import KbDao
from scripts.init_collections import build
from scripts.rebuild_kb import to_milvus_row


def test_withdrawal_hides_old_and_new_copies_cached_evidence_and_stream(monkeypatch):
    token = uuid.uuid4().hex[:10]
    tenant = "withdraw-it-" + token
    versions = ["withdraw-a-" + token, "withdraw-b-" + token]
    settings = get_settings().model_copy(update={"doc_collection": "test_withdraw_doc_" + token,
        "faq_collection": "test_withdraw_faq_" + token})
    client = MilvusClient(uri=settings.milvus_uri, timeout=15)
    dao = KbDao(settings.mysql_dsn)
    headers = {"X-User-Id": "withdraw-test", "X-User-Roles": "admin", "X-Tenant-ID": tenant}
    scope = AccessScope.build(tenant_id=tenant, roles=["admin"])
    cache = Mock(); cache.get_with_lock.return_value = None
    searcher = Searcher(client=client, embedder=Mock(), cache=cache, settings=settings)
    service = EvidenceService(searcher, Mock(), settings)
    monkeypatch.setattr(route_kb, "get_settings", lambda: settings)
    monkeypatch.setattr(route_evidence, "get_settings", lambda: settings)
    monkeypatch.setattr(route_evidence, "get_evidence_service", lambda: service)
    api = TestClient(create_app())
    try:
        build(client, settings.doc_collection, False); build(client, settings.faq_collection, True)
        for v in versions: dao.ensure_version(v)
        rows, docs = [], []
        for index, (version, source_id) in enumerate([(versions[0], "removed"), (versions[1], "removed"),
                                                     (versions[0], "good"), (versions[0], ""), (versions[0], "live")]):
            chunk = make_chunk("packet evidence", "manual", tenant_id=tenant, source_system="wiki", source_id=source_id,
                asset_id=f"withdraw-asset-{token}-{index}", source_version=version, security_level="internal", visibility="public", status="active")
            row = to_milvus_row(chunk, f"withdraw-{token}-{index}", [1.] + [0.] * 1023, version)
            rows.append(row); docs.append(_to_doc({"id": row["id"], "entity": row}))
        client.upsert(collection_name=settings.doc_collection, data=rows); client.flush(settings.doc_collection)
        urls = [evidence_of(d)["source_url"] for d in docs[:2]]
        assert all(api.get(url, headers=headers).status_code == 200 for url in urls)
        assert api.post("/kb/withdrawals", headers={**headers, "X-Tenant-ID": "other"},
                        json={"source_system": "wiki", "source_id": "removed", "reason": "fixture"}).status_code == 404
        body = {"source_system": "wiki", "source_id": "removed", "reason": "fixture"}
        assert api.post("/kb/withdrawals", headers=headers, json=body).json()["created"] is True
        assert api.post("/kb/withdrawals", headers=headers, json=body).json()["created"] is False
        assert all(api.get(url, headers=headers).status_code == 404 for url in urls)
        # Cached candidates are not deleted: guard must work even with stale cache intact.
        cache.get_with_lock.return_value = [d.to_dict() for d in (docs[0], docs[2], docs[3])]
        result = searcher.search_docs(["packet"], ["admin"], versions[0], tenant_id=tenant, scope=scope)
        assert result.cache_hit and [d.source_id for d in result.docs] == ["good"]
        assert service.source(evidence_of(docs[2])["evidence_id"], scope)
        import pytest
        with pytest.raises(ValueError):
            assert_publishable_sources(settings, [(tenant, "wiki", "removed")])
        # Withdraw during an in-flight simulated generation. Previously sent text
        # cannot be recalled, but the next token and final sources must be suppressed.
        def pieces():
            yield "before-withdraw"
            response = api.post("/kb/withdrawals", headers=headers, json={"source_system": "wiki", "source_id": "live", "reason": "stream fixture"})
            assert response.status_code == 200
            yield "after-withdraw"
        uc = QaUseCase(searcher=Mock(), llm=Mock()); uc.settings = settings
        uc.searcher.search_faq.return_value = []
        uc._decide = lambda *a, **kw: IntentDecision("knowledge", "retrieval", "rule", .9, "test")
        uc._stream_retrieval = lambda *a, **kw: (pieces(), [docs[4]], QaUseCase._sources_of([docs[4]]), False)
        events = list(uc.ask_stream("packet", tenant_id=tenant, roles=["admin"], kb_version=versions[0], persist=False))
        deltas = [e["data"]["text"] for e in events if e["event"] == "delta"]
        assert "before-withdraw" in deltas and "after-withdraw" not in deltas
        end = next(e["data"] for e in events if e["event"] == "end")
        assert end["status"] == "NO_EVIDENCE"
        assert next(e["data"]["sources"] for e in events if e["event"] == "sources") == []
    finally:
        with dao.engine.begin() as conn:
            conn.execute(sa.text("DELETE FROM knowledge_source_withdrawals WHERE tenant_id=:t"), {"t": tenant})
            conn.execute(sa.text("DELETE FROM audit_log WHERE actor='withdraw-test' AND action='withdraw_source' "
                                 "AND JSON_UNQUOTE(JSON_EXTRACT(payload_json,'$.tenant_id'))=:t"), {"t": tenant})
            for v in versions:
                conn.execute(sa.text("DELETE FROM kb_versions WHERE version=:v AND status='STAGED'"), {"v": v})
        for name in (settings.doc_collection, settings.faq_collection):
            if client.has_collection(name): client.drop_collection(name)
        client.close(); dao.engine.dispose()
