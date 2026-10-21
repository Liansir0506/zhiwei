"""Opt-in feature behavior against existing STAGED sources; no fixture writes/LLM."""
import json
from pathlib import Path

from qa_core.application.evidence_service import EvidenceService
from qa_core.config.settings import get_settings
from qa_core.retrieval.searcher import Searcher
from qa_core.rerank.reranker import get_reranker
from qa_core.security.policy import AccessScope
from scripts.build_step3_dev_pack import ROOT
from scripts.calibrate_eval_set import normalize


class ReadOnlyCache:
    def get(self, *args): return None
    def get_with_lock(self, *args): return None
    def set(self, *args): pass
    def set_empty(self, *args): pass


def test_step4_strong_source_read_clarification_and_scope():
    settings = get_settings().model_copy(update={"doc_collection": "test_quality_r1_doc",
        "faq_collection": "test_quality_r1_faq", "workflow_evidence_enabled": True})
    searcher = Searcher(settings=settings, cache=ReadOnlyCache())
    service = EvidenceService(searcher, get_reranker(settings.reranker_model_path, settings.rerank_device), settings)
    cases = json.loads((ROOT / "data/candidates/step3-lab-dev-r1/cases.json").read_text(encoding="utf-8"))["cases"]
    scope = AccessScope.build(tenant_id="default", roles=["ops"], dept_ids=["lab-a"])
    probes = []
    for case in cases[:3]:
        result = service.search(case["query"], "v4-quality-r1", scope, 5)
        expected = "EVIDENCE_FOUND" if case["case_kind"] == "answer" else "NEEDS_CLARIFICATION"
        assert result["status"] == expected, result
        if expected == "EVIDENCE_FOUND":
            text = "\n".join(e["text"] for e in result["evidence"])
            assert all(normalize(a["quote"]) in normalize(text) for a in case["required_evidence"])
            assert {e["source"] for e in result["evidence"]} == set(case["expect_hit"])
            for evidence in result["evidence"]:
                assert service.source(evidence["evidence_id"], scope) is not None
        else:
            assert result["evidence"] == [] and result["question"]
        probes.append({"id": case["id"], "status": result["status"], "chunks": len(result["evidence"])})
    query = cases[0]["query"]
    negatives = [("cross_tenant", query, AccessScope.build(tenant_id="other", roles=["admin"])),
        ("wrong_version", query.replace("Lab-2026.09", "Lab-2026.11"), scope),
        ("production", "真实生产AMF ALM-AMF-REG入口队列增长而鉴权依赖响应稳定，如何恢复SOP？", scope)]
    for label, question, access in negatives:
        result = service.search(question, "v4-quality-r1", access, 5)
        assert result["status"] == "NO_EVIDENCE" and result["evidence"] == [], result
        probes.append({"id": label, "status": result["status"], "chunks": 0})
    print("STEP4_READONLY_PROBES="+json.dumps(probes,ensure_ascii=False))
