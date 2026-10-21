"""Real isolated source expansion: permission, lifecycle and missing-tail denial."""
import uuid
import copy
from unittest.mock import Mock
import pytest
from sqlalchemy import text
from pymilvus import MilvusClient
from qa_core.config.settings import get_settings
from qa_core.retrieval.searcher import Searcher
from qa_core.retrieval.filters import _to_doc
from qa_core.security.policy import AccessScope
from qa_core.storage.kb_dao import KbDao
from scripts.init_collections import build
from scripts.rebuild_kb import to_milvus_row
from qa_core.indexing.chunkers.base import make_chunk


def test_live_source_expansion_does_not_bypass_permissions_or_missing_tail():
    s = get_settings(); token = uuid.uuid4().hex[:12]
    name = f"test_workflow_{token}"; version = f"workflow-it-{token}"
    client = MilvusClient(uri=s.milvus_uri, timeout=15); dao = KbDao(s.mysql_dsn)
    rows, seeds = [], {}
    changes = {"allowed": {}, "role": {"visibility": "role_restricted", "role_scope": ["admin"], "allowed_roles": ["admin"]},
               "dept": {"visibility": "department_restricted", "dept_id": "dept-a"},
               "retired": {"status": "retired"}, "expired": {"effective_to": "2026-01-01T00:00:00+00:00"},
               "future": {"effective_from": "2027-01-01T00:00:00+00:00"}, "tail": {}}
    try:
        build(client, name, False); dao.ensure_version(version)
        for label, delta in changes.items():
            asset = f"asset-{token}-{label}"; source = f"sop_{label}.md"
            texts = ["# AMF 流程\n适用 AMF", "步骤 1：只读检查，停止并转人工。",
                     "步骤 2：人工审批，保存快照，异常回滚。", "步骤 3：恢复验证。"]
            for i, body in enumerate(texts):
                chunk = make_chunk(body, "sop", doc_type="sop", source=source, asset_id=asset,
                    step_index=[i], source_version="source-revision-r7", tenant_id="default", dept_id="public",
                    security_level="internal", visibility="public", effective_from="2026-01-01T00:00:00+00:00",
                    effective_to="2028-01-01T00:00:00+00:00", status="active", **({}))
                chunk.metadata.update(delta)
                row = to_milvus_row(chunk, f"{label}-{i}", [1.0] + [0.0] * 1023, version)
                rows.append(row)
                if i == 1: seeds[label] = _to_doc({"id": row["id"], "entity": row})
            digest = (token + label).encode().hex().ljust(64, "0")[:64]
            dao.upsert_record(digest, source, "sop", version, asset_id=asset)
            dao.set_status(digest, version, "done", chunk_count=4)
        # Same chunk body under other identities must never join this source.
        anchor = next(row for row in rows if row["id"] == "allowed-1")
        for field, value in (("kb_version", "another-release"), ("source_version", "source-revision-r8"),
                             ("tenant_id", "other"), ("asset_id", "other-asset"), ("source", "other-file.md")):
            rows.append({**anchor, "id": "decoy-" + field, field: value})
        client.upsert(collection_name=name, data=rows); client.flush(name)
        searcher = Searcher(client=client, embedder=Mock(), cache=Mock(),
                            settings=s.model_copy(update={"doc_collection": name}))
        expanded = searcher.fetch_sop_source(seeds["allowed"], ["ops"], version)
        assert {doc.id for doc in expanded} == {f"allowed-{i}" for i in range(4)}
        assert all(doc.source_version == "source-revision-r7" and doc.kb_version == version for doc in expanded)
        with pytest.raises(ValueError, match="identity mismatch"):
            searcher.fetch_sop_source(seeds["allowed"], ["ops"], "another-release")
        missing_revision = copy.deepcopy(seeds["allowed"]); missing_revision.source_version = ""
        with pytest.raises(ValueError, match="identity mismatch"):
            searcher.fetch_sop_source(missing_revision, ["ops"], version)
        for field, value in (("source_version", "source-revision-r8"),
                             ("source", "other-file.md"), ("asset_id", "other-asset")):
            altered_seed = copy.deepcopy(seeds["allowed"]); setattr(altered_seed, field, value)
            with pytest.raises(ValueError, match="incomplete"):
                searcher.fetch_sop_source(altered_seed, ["ops"], version)
        with dao.engine.begin() as conn:
            conn.execute(text("UPDATE ingest_records SET chunk_count=65 WHERE kb_version=:v AND asset_id=:a"),
                         {"v": version, "a": seeds["allowed"].asset_id})
        with pytest.raises(ValueError, match="too large"):
            searcher.fetch_sop_source(seeds["allowed"], ["ops"], version)
        with dao.engine.begin() as conn:
            conn.execute(text("UPDATE ingest_records SET chunk_count=4 WHERE kb_version=:v AND asset_id=:a"),
                         {"v": version, "a": seeds["allowed"].asset_id})
        from qa_core.application.evidence_service import EvidenceService
        from qa_core.schemas.evidence import evidence_of
        service = EvidenceService(searcher, Mock(), searcher.settings)
        ops = AccessScope.build(roles=["ops"])
        ref = evidence_of(seeds["allowed"])["evidence_id"]
        assert service.source(ref, ops)["text"] == seeds["allowed"].text
        assert service.source(ref, AccessScope.build(roles=["guest"])) is None
        assert service.source(ref, AccessScope.build(tenant_id="other", roles=["admin"])) is None
        for label in ("role", "dept", "retired", "expired", "future"):
            assert service.source(evidence_of(seeds[label])["evidence_id"], ops) is None
        assert service.source(evidence_of(seeds["dept"])["evidence_id"],
            AccessScope.build(roles=["ops"], dept_ids=["dept-a"])) is not None
        for label in ("role", "dept", "retired", "expired", "future"):
            with pytest.raises(ValueError): searcher.fetch_sop_source(seeds[label], ["ops"], version)
        with pytest.raises(ValueError): searcher.fetch_sop_source(seeds["allowed"], ["guest"], version)
        with pytest.raises(ValueError): searcher.fetch_sop_source(seeds["allowed"], ["ops"], version, tenant_id="other")
        access = AccessScope.build(roles=["ops"], dept_ids=["dept-a"])
        assert len(searcher.fetch_sop_source(seeds["dept"], ["ops"], version, scope=access)) == 4
        client.delete(collection_name=name, ids=["tail-3"]); client.flush(name)
        with pytest.raises(ValueError, match="incomplete"):
            searcher.fetch_sop_source(seeds["tail"], ["ops"], version)
        # Same reference must not survive an in-place withdrawal or text revision.
        allowed_row = next(r for r in rows if r["id"] == seeds["allowed"].id)
        client.upsert(collection_name=name, data=[{**allowed_row, "text": "修订后的新原文"}]); client.flush(name)
        assert service.source(ref, ops) is None
        new_ref = evidence_of(searcher.fetch_evidence_chunk(allowed_row["id"], version, scope=ops))["evidence_id"]
        client.upsert(collection_name=name, data=[{**allowed_row, "text": "修订后的新原文", "status": "retired"}]); client.flush(name)
        assert service.source(new_ref, ops) is None
    finally:
        # Only this test's random collection and its exact unique version records.
        if client.has_collection(name): client.drop_collection(name)
        client.close()
        with dao.engine.begin() as conn:
            conn.execute(text("DELETE FROM ingest_records WHERE kb_version=:v"), {"v": version})
            conn.execute(text("DELETE FROM kb_versions WHERE version=:v"), {"v": version})
