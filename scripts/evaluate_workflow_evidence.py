"""Fixed dev workflow evidence checks. No LLM, publication, or historical relabeling."""
import argparse
import json
import os
import sys
from pathlib import Path
ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from scripts.build_knowledge_candidate import encode, sha
from scripts.ingest_quality_candidate import PROFILES
from scripts.compare_quality_retrieval import NoCache, required_atoms_present
from scripts.calibrate_eval_set import normalize
from qa_core.config.settings import get_settings
from qa_core.config.environment import validate_isolated_environment
from qa_core.retrieval.searcher import Searcher
from qa_core.retrieval.workflow import retrieve_workflow, is_workflow_query
from qa_core.rerank.reranker import get_reranker
from qa_core.generation.context import build_evidence
from qa_core.application.qa_usecase import _citation_answer


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--candidate", type=Path, required=True)
    p.add_argument("--sop-review", type=Path, required=True)
    p.add_argument("--before-report", type=Path, required=True)
    p.add_argument("--report", type=Path, required=True)
    args = p.parse_args()
    protocol_path = args.report.with_suffix(".protocol.json")
    if args.report.exists() or protocol_path.exists(): raise ValueError("refuse overwrite")
    settings = get_settings()
    if errors := validate_isolated_environment(settings, "test"): raise ValueError(errors)
    payload = json.loads(args.sop_review.read_text(encoding="utf-8"))
    old = [c for c in payload["review_cases"] if c["id"] in {"R01","R03","R06","R08","R09","R11","R15","R18","R19","R20"}]
    new = json.loads((args.candidate / "evaluation/review-cases.json").read_text(encoding="utf-8"))["review_cases"]
    if len(old) != 10 or len(new) != 12: raise ValueError("fixed dev ruler mismatch")
    before = json.loads(args.before_report.read_text(encoding="utf-8"))
    result = {"role": "dev_workflow_diagnostic", "independent_frozen": False,
              "formal_metrics_eligible": False, "historical_hit_at_5": 0.827, "llm_used": False,
              "data_changed": False, "workflow_flag_default": False,
              "candidate_sha256": sha((args.candidate / "artifact-hashes.json").read_bytes()),
              "ruler_sha256": sha(args.sop_review.read_bytes()),
              "before_report_sha256": sha(args.before_report.read_bytes()),
              "protocol": "strict SOP recall; same score threshold; uncached scoped full-source read; ordered bounded packet; safety gate"}
    protocol_path.parent.mkdir(parents=True, exist_ok=True)
    protocol_path.write_bytes(encode(result))
    from pymilvus import MilvusClient
    client = MilvusClient(uri=settings.milvus_uri, timeout=30)
    original_names = (settings.doc_collection, settings.faq_collection)
    try:
        reranker = get_reranker(settings.reranker_model_path, settings.rerank_device)
        runs = {}
        for label, (version, doc, faq) in PROFILES.items():
            os.environ.update(DOC_COLLECTION=doc, FAQ_COLLECTION=faq)
            get_settings.cache_clear()
            searcher = Searcher(client=client, settings=get_settings(), cache=NoCache())
            rows = []
            for case in (old if label == "control" else old + new):
                if not is_workflow_query(case["query"]): raise ValueError("ruler has nonworkflow question")
                evidence = retrieve_workflow(searcher, reranker, case["query"], ["ops"], version,
                                             min_score=settings.rerank_min)
                packet = evidence.packet
                row = {"id": case["id"], "ready": evidence.ready, "reason": evidence.reason,
                       "source": packet.source if packet else None,
                       "steps": packet.extra["workflow_steps"] if packet else [],
                       "child_chunk_ids": packet.extra["child_chunk_ids"] if packet else []}
                if case in old:
                    found, total = required_atoms_present(case, [packet.text] if packet else [])
                else:
                    found = sum(normalize(a["quote"]) in normalize(packet.text if packet else "") for a in case["required_evidence"])
                    total = len(case["required_evidence"])
                row.update(atoms_found=found, atoms_required=total)
                if evidence.ready:
                    rendered = build_evidence([packet])
                    citation = _citation_answer([packet])
                    row["full_packet_in_generation_evidence"] = packet.text in rendered
                    row["full_packet_in_citation_answer"] = packet.text in citation
                rows.append(row)
                print(label, case["id"], evidence.reason, f"atoms={found}/{total}", flush=True)
            runs[label] = rows
        result["runs"] = runs
        old_rows = runs["candidate"][:10]; new_rows = runs["candidate"][10:]
        result["summary"] = {
            "old_candidate_all_source_atoms_before": sum(r["candidate"]["literal_required_atoms_present"] == r["candidate"]["required_atoms_total"] for r in before["sop_comparisons"]),
            "old_candidate_all_source_atoms_after": sum(r["atoms_found"] == r["atoms_required"] for r in old_rows),
            "old_candidate_safe_to_answer": sum(r["ready"] for r in old_rows),
            "new_all_atoms_before": before["new_all_literal_atoms_present"],
            "new_all_atoms_after": sum(r["atoms_found"] == r["atoms_required"] for r in new_rows),
            "new_safe_to_answer": sum(r["ready"] for r in new_rows)}
        args.report.write_bytes(encode(result))
        print(json.dumps(result["summary"], ensure_ascii=False, indent=2))
    finally:
        client.close()
        os.environ.update(DOC_COLLECTION=original_names[0], FAQ_COLLECTION=original_names[1])
        get_settings.cache_clear()


if __name__ == "__main__": main()
