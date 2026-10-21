"""Paired, retrieval-only dev stress test; never registers or publishes sources."""
from __future__ import annotations

import argparse
import copy
import hashlib
import json
import uuid
from pathlib import Path

from pymilvus import MilvusClient

from qa_core.config.environment import validate_isolated_environment
from qa_core.config.settings import get_settings
from qa_core.quality.eval_review import validate_export
from qa_core.retrieval.searcher import Searcher
from qa_core.rerank.reranker import get_reranker
from scripts.calibrate_eval_set import normalize
from scripts.init_collections import build


APPROVED_COLLECTION = "test_protocol_r1_doc"
DISTRACTOR_COLLECTION = "test_quality_r1_doc"
APPROVED_VERSION = "v4-protocol-dev-r1"
DISTRACTOR_VERSION = "v4-quality-r1"
OVERLAP_TERMS = ("DNS", "HTTP", "TLS", "503", "429", "缓存", "重放", "限流")


class NoCache:
    def get(self, *args): return None
    def get_with_lock(self, *args): return None
    def set(self, *args): pass


def select_distractors(rows: list[dict]) -> list[dict]:
    """Fixed selection made before observing any eight-case retrieval result."""
    eligible = [r for r in rows if r.get("tenant_id") == "default" and
                r.get("status") == "active" and r.get("text") and r.get("dense")]
    eligible.sort(key=lambda r: hashlib.sha256(str(r["id"]).encode()).hexdigest())
    overlap = [r for r in eligible if any(t.casefold() in
               (str(r.get("source", "")) + " " + r["text"]).casefold() for t in OVERLAP_TERMS)]
    chosen = {r["id"]: r for r in overlap[:50]}
    for row in eligible:
        if len(chosen) >= 250: break
        chosen.setdefault(row["id"], row)
    return list(chosen.values())


def canonical_sha(value) -> str:
    return hashlib.sha256(json.dumps(value, ensure_ascii=False, sort_keys=True,
                                separators=(",", ":")).encode()).hexdigest()


def copy_row(row: dict, version: str, *, distractor: bool = False) -> dict:
    copied = copy.deepcopy(row)
    copied.pop("sparse", None)  # generated again by Milvus BM25 function
    copied["kb_version"] = version
    if distractor:
        copied["id"] = hashlib.sha256(("diagnostic:" + row["id"]).encode()).hexdigest()
        # Preserve original source_version/review status: this is not an approved
        # versioned source and must never be mistaken for a valid citation.
    return copied


def assess(cases: list[dict], searcher: Searcher, reranker, version: str) -> list[dict]:
    results = []
    for case in cases:
        docs = searcher.search_docs([case["query"]], ["ops"], version).docs[:10]
        ranked = reranker.rerank(case["query"], docs, top_n=5)
        names = [d.source for d in ranked]
        expected = case["expect_hit"]
        rank = names.index(expected) + 1 if expected in names else None
        quotes = [e["quote"] for e in case["evidence_units"]]
        target_text = normalize("\n".join(d.text for d in ranked if d.source == expected))
        results.append({"id": case["id"], "expected": expected,
                        "sources": names, "target_rank": rank,
                        "target_quote_count": sum(normalize(q) in target_text for q in quotes),
                        "required_quote_count": len(quotes),
                        "top5": [{"source": d.source, "score": d.rerank_score,
                                  "text_sha256": hashlib.sha256(d.text.encode()).hexdigest()}
                                 for d in ranked]})
    return results


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dataset", type=Path, required=True)
    parser.add_argument("--report", type=Path, required=True)
    args = parser.parse_args(argv)
    if args.report.exists() or args.report.with_suffix(".protocol.json").exists():
        raise ValueError("refuse to overwrite comparison or preregistered protocol")
    settings = get_settings()
    errors = validate_isolated_environment(settings, "test")
    if errors or (settings.doc_collection, settings.faq_collection) != (
            APPROVED_COLLECTION, "test_protocol_r1_faq"):
        raise ValueError("requires exact isolated protocol profile: " + str(errors))
    payload = json.loads(args.dataset.read_text(encoding="utf-8"))
    validate_export(payload)
    cases = payload["cases"]
    if payload["kb_version"] != APPROVED_VERSION or len(cases) != 8 or any(
            c["split"] != "dev" or c["expect_route"] != "retrieval" for c in cases):
        raise ValueError("requires unchanged approved eight-case dev ruler")
    client = MilvusClient(uri=settings.milvus_uri, timeout=30)
    token = uuid.uuid4().hex[:10]
    version = "proto-diag-" + token
    names = ["test_proto_base_" + token, "test_proto_mix_" + token]
    created = []
    try:
        approved = client.query(collection_name=APPROVED_COLLECTION,
            filter=f'kb_version == "{APPROVED_VERSION}"', output_fields=["*", "dense"],
            limit=100, consistency_level="Strong")
        candidate = client.query(collection_name=DISTRACTOR_COLLECTION,
            filter=f'kb_version == "{DISTRACTOR_VERSION}"', output_fields=["*", "dense"],
            limit=5000, consistency_level="Strong")
        expected_sources = {s["filename"] for s in payload["sources"]}
        if len(approved) != 4 or {r["source"] for r in approved} != expected_sources:
            raise ValueError("approved storage differs from fixed source roster")
        distractors = select_distractors(candidate)
        if len(distractors) < 50:
            raise ValueError("insufficient diagnostic distractor rows")
        if any(r["source"] in expected_sources for r in distractors):
            raise ValueError("distractor source name collides with approved roster")
        protocol = {"purpose": "retrieval_only_dev_stress_not_formal_metric",
                    "dataset_sha256": hashlib.sha256(args.dataset.read_bytes()).hexdigest(),
                    "approved_version": APPROVED_VERSION,
                    "candidate_version": DISTRACTOR_VERSION,
                    "approved_roster": sorted(expected_sources),
                    "approved_rows_sha256": canonical_sha(sorted(
                        [(r["id"], hashlib.sha256(r["text"].encode()).hexdigest()) for r in approved])),
                    "distractor_rows_sha256": canonical_sha(sorted(
                        [(r["id"], hashlib.sha256(r["text"].encode()).hexdigest()) for r in distractors])),
                    "distractor_count": len(distractors),
                    "selection": "up to 50 lexical overlap then SHA-256(id) ordered fill to 250, before inference",
                    "retrieval": "same BGE-M3 dense+BM25, fusion, first 10 then CrossEncoder top 5; no FAQ/route/answer/LLM",
                    "config": {k: getattr(settings, k) for k in
                               ("fusion_method", "top_k_dense", "top_k_sparse", "rerank_min", "top_n_final")},
                    "temporary_version": version, "temporary_collections": names,
                    "source_governance": "candidate rows unreviewed; copied only to disposable test corpus; no approval or publication"}
        args.report.parent.mkdir(parents=True, exist_ok=True)
        args.report.with_suffix(".protocol.json").write_text(
            json.dumps(protocol, ensure_ascii=False, indent=2), encoding="utf-8")
        base_rows = [copy_row(r, version) for r in approved]
        mix_rows = base_rows + [copy_row(r, version, distractor=True) for r in distractors]
        for name, rows in zip(names, (base_rows, mix_rows)):
            if client.has_collection(name): raise ValueError("random diagnostic collection already exists")
            build(client, name, False)
            created.append(name)
            client.upsert(collection_name=name, data=rows)
            client.flush(name)
            count = client.query(collection_name=name, filter=f'kb_version == "{version}"',
                                 output_fields=["count(*)"], consistency_level="Strong")[0]["count(*)"]
            if count != len(rows): raise ValueError("temporary corpus count mismatch")
        reranker = get_reranker(settings.reranker_model_path, settings.rerank_device)
        runs = {}
        for label, name in zip(("base", "mixed"), names):
            own = settings.model_copy(update={"doc_collection": name})
            searcher = Searcher(client=client, settings=own, cache=NoCache())
            runs[label] = assess(cases, searcher, reranker, version)
        paired = []
        for base, mixed in zip(runs["base"], runs["mixed"]):
            paired.append({"id": base["id"], "base": base, "mixed": mixed,
                           "rank_delta": (mixed["target_rank"] - base["target_rank"]
                                          if base["target_rank"] and mixed["target_rank"] else None)})
        summary = {label: {"source_hit_at_5": sum(r["target_rank"] is not None for r in rows),
                           "source_hit_at_1": sum(r["target_rank"] == 1 for r in rows),
                           "all_required_quotes": sum(r["target_quote_count"] == r["required_quote_count"]
                                                      for r in rows)} for label, rows in runs.items()}
        report = {"protocol_sha256": canonical_sha(protocol), "protocol": protocol,
                  "metrics_eligible": False, "independent_frozen": False,
                  "generation_assessed": False, "thresholds_changed": False,
                  "summary": summary, "paired": paired}
        args.report.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
        print(json.dumps(summary, ensure_ascii=False))
    finally:
        # Drop only the two random names created in this invocation. Never touch
        # the approved or candidate source collections.
        for name in reversed(created):
            if client.has_collection(name): client.drop_collection(name)
        client.close()


if __name__ == "__main__":
    main()
