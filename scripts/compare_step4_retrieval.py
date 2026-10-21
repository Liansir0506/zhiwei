"""Fixed dev diagnostics: real indexed retrieval and separate immutable-snapshot guards.

Pending sources are never registered/indexed/published. No LLM or cache writes.
24 indexed SOP questions share one live ruler; 52 snapshot cases share another.
The two denominators are never merged or described as independent acceptance.
"""
from __future__ import annotations

import argparse
from collections import Counter
import copy
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from scripts.build_step3_dev_pack import encode, sha, verify
from scripts.calibrate_eval_set import normalize
from scripts.run_step3_dev_baseline import AS_OF, COLLECTIONS, diagnostic_allowed, input_hashes, score_case
from qa_core.config.environment import validate_isolated_environment
from qa_core.config.settings import get_settings
from qa_core.retrieval.filters import _to_doc, build_filter_expr, cap_per_source, dedup_by_parent, fuse
from qa_core.retrieval.workflow import is_workflow_query, lab_source_applicable, retrieve_workflow, REQUIRED_WORKFLOW_FIELDS
from qa_core.schemas.retrieval import RetrievalResult
from qa_core.security.policy import AccessScope


def load_fixed(package, baseline):
    import numpy as np
    verify(package)
    protocol = json.loads((baseline / "protocol.json").read_text(encoding="utf-8"))
    files = {"package_manifest_sha256": package / "artifact-hashes.json", "cases_sha256": package / "cases.json",
        "sources_sha256": package / "sources.json", "snapshot_rows_sha256": baseline / "snapshot-rows.json",
        "snapshot_vectors_sha256": baseline / "snapshot-vectors.npy"}
    for field, file in files.items():
        if sha(file.read_bytes()) != protocol["inputs"][field]:
            raise ValueError(f"fixed input changed: {field}")
    summary = json.loads((baseline / "summary.json").read_text(encoding="utf-8"))
    for name, field in (("protocol.json", "protocol_sha256"), ("cases.jsonl", "cases_jsonl_sha256")):
        if sha((baseline / name).read_bytes()) != summary[field]:
            raise ValueError("historical baseline changed")
    rows = json.loads((baseline / "snapshot-rows.json").read_text(encoding="utf-8"))
    vectors = np.load(baseline / "snapshot-vectors.npy", allow_pickle=False)
    cases = json.loads((package / "cases.json").read_text(encoding="utf-8"))["cases"]
    if vectors.shape != (len(rows), 1024) or not np.isfinite(vectors).all():
        raise ValueError("invalid snapshot vector matrix")
    if len({r["id"] for r in rows}) != len(rows):
        raise ValueError("ambiguous cross-collection chunk IDs")
    return rows, vectors, cases, protocol


class RankCache:
    """Reuse exact query/body scores within this run, never reuse changed evidence."""
    def __init__(self, model):
        self.model, self.scores = model, {}

    def rerank(self, query, docs, top_n=5):
        missing = [d for d in docs if (query, d.id, sha(d.text.encode())) not in self.scores]
        if missing:
            self.model.rerank(query, missing, top_n=len(missing))
            for d in missing:
                self.scores[(query, d.id, sha(d.text.encode()))] = d.rerank_score
        for d in docs:
            d.rerank_score = self.scores[(query, d.id, sha(d.text.encode()))]
        return sorted(docs, key=lambda d: d.rerank_score, reverse=True)[:top_n]


class SnapshotSearcher:
    def __init__(self, rows, vectors, embedder, top_k=50):
        self.rows, self.vectors, self.embedder = rows, vectors, embedder
        self.top_k, self.query_vectors = top_k, {}

    def vector(self, query):
        import numpy as np
        if query not in self.query_vectors:
            self.query_vectors[query] = np.asarray(self.embedder.embed_query(query), dtype="float32")
        return self.query_vectors[query]

    def dense(self, query, scope, doc_types=None, all_collections=True):
        import numpy as np
        indices = np.asarray([i for i, row in enumerate(self.rows)
            if diagnostic_allowed(row, scope) and (all_collections or row["collection_kind"] == "doc")
            and (not doc_types or row["doc_type"] in doc_types)], dtype=int)
        if not len(indices):
            return []
        scores = self.vectors[indices] @ self.vector(query)
        order = np.argsort(-scores, kind="stable")[:self.top_k]
        return [{"id": self.rows[indices[i]]["id"], "entity": self.rows[indices[i]], "distance": float(scores[i])} for i in order]

    def search_docs(self, variants, roles, version, *, scope=None, tenant_id="default", doc_types=None, **kwargs):
        scope = scope or AccessScope.build(tenant_id=tenant_id, roles=roles)
        hits = self.dense(variants[0], scope, doc_types, all_collections=False)
        return RetrievalResult(variants, cap_per_source(dedup_by_parent(fuse(hits, [], 1, 0))))

    def fetch_sop_source(self, seed, roles, version, *, scope=None, tenant_id="default"):
        scope = scope or AccessScope.build(tenant_id=tenant_id, roles=roles)
        # Re-read only the fixed authorized indexed rows. Never open raw corpus files
        # or select a source using case labels. Live strong reads are tested separately.
        selected = [row for row in self.rows if row["source"] == seed.source
            and row.get("asset_id") == seed.asset_id and row["kb_version"] == seed.kb_version
            and row["source_version"] == seed.source_version and row["doc_type"] == "sop"
            and diagnostic_allowed(row, scope)]
        if not selected or any(not REQUIRED_WORKFLOW_FIELDS <= row.keys() for row in selected):
            raise ValueError("incomplete source schema or denied source")
        return [_to_doc({"id": row["id"], "entity": row}) for row in selected]


def live_fingerprint(client, rows):
    expected = {r["id"]: r for r in rows if r["snapshot_origin"] == "read_only_existing_test_index"}
    found = {}
    for kind, collection in COLLECTIONS.items():
        iterator = client.query_iterator(collection_name=collection, filter='kb_version == "v4-quality-r1"',
            output_fields=["*"], batch_size=256)
        try:
            while batch := iterator.next():
                for row in batch:
                    row.pop("dense", None)
                    row.pop("sparse", None)
                    original = expected.get(row["id"])
                    if original is None:
                        raise ValueError("live index added a chunk since the fixed snapshot")
                    for key, value in row.items():
                        if original.get(key) != value:
                            raise ValueError(f"live index changed: {row['id']} {key}")
                    found[row["id"]] = row
        finally:
            iterator.close()
    if set(found) != set(expected):
        raise ValueError("live index removed a chunk since the fixed snapshot")
    return sha(encode(found))


def live_profiles(client, snapshot, case, settings, ranker):
    scope = AccessScope.build(**case["request_context"])
    hits = {}
    for kind, collection in COLLECTIONS.items():
        fields = {f["name"] for f in client.describe_collection(collection)["fields"]}
        expr = build_filter_expr(list(scope.roles), "v4-quality-r1", tenant_id=scope.tenant_id,
            dept_ids=scope.dept_ids, security_levels=scope.security_levels, as_of=AS_OF, available_fields=fields)
        output = sorted(fields - {"dense", "sparse"})
        hits[kind] = client.search(collection_name=collection, data=[snapshot.vector(case["query"]).tolist()],
            anns_field="dense", limit=settings.top_k_dense if kind == "doc" else 5, filter=expr,
            output_fields=output, search_params={"params": {"ef": settings.dense_ef}})[0]
        if kind == "doc":
            hits["sparse"] = client.search(collection_name=collection, data=[case["query"]], anns_field="sparse",
                limit=settings.top_k_sparse, filter=expr, output_fields=output)[0]
    faq = fuse(hits["faq"], [], 1, 0)
    dense = cap_per_source(dedup_by_parent(fuse(hits["doc"], [], 1, 0)))
    weighted = cap_per_source(dedup_by_parent(fuse(hits["doc"], hits["sparse"], .6, .4)))
    rrf = cap_per_source(dedup_by_parent(fuse(hits["doc"], hits["sparse"], method="rrf")))

    def merge(docs):
        result, seen = list(docs[:10]), {d.id for d in docs[:10]}
        result.extend(d for d in faq if d.id not in seen)
        return result

    profiles = {"dense": (dense[:5], dense), "hybrid_weighted": (weighted[:5], weighted),
        "hybrid_weighted_rerank": (ranker.rerank(case["query"], merge(copy.deepcopy(weighted))), weighted),
        "hybrid_rrf_rerank": (ranker.rerank(case["query"], merge(copy.deepcopy(rrf))), rrf)}
    return {name: score_case(case, ranked, recalled) for name, (ranked, recalled) in profiles.items()}


def coverage(case, docs):
    target = set(case["expect_hit"])
    text = normalize("\n".join(d.text for d in docs if d.source in target))
    return [atom for atom in case["required_evidence"] if normalize(atom["quote"]) not in text]


def guarded(snapshot, ranker, case, settings):
    from qa_core.generation.context import build_messages, context_budget_ok
    from qa_core.application.qa_usecase import _citation_answer
    scope = AccessScope.build(**case["request_context"])
    if is_workflow_query(case["query"]):
        workflow = retrieve_workflow(snapshot, ranker, case["query"], list(scope.roles), "v4-quality-r1",
            tenant_id=scope.tenant_id, scope=scope, min_score=settings.rerank_min)
        docs = [workflow.packet] if workflow.ready else []
        status = "EVIDENCE_FOUND" if docs else "NEEDS_CLARIFICATION" if workflow.question else "NO_EVIDENCE"
        reason = workflow.reason
    else:
        hits = snapshot.dense(case["query"], scope)
        recalled = cap_per_source(dedup_by_parent(fuse(hits, [], 1, 0)))
        candidates = [d for d in recalled if lab_source_applicable(case["query"], d.text)]
        docs = ranker.rerank(case["query"], candidates[:10])
        docs = [d for d in docs if d.rerank_score is not None and d.rerank_score >= settings.rerank_min]
        for doc in docs:
            doc.extra["lab_scope_verified"] = True
        status, reason = ("EVIDENCE_FOUND", "lab_scope_compatible") if docs else ("NO_EVIDENCE", "no_authorized_compatible_source")
    result = score_case(case, docs, docs)
    expected = "EVIDENCE_FOUND" if case["expect_status"] == "ANSWERED" else case["expect_status"]
    result.update(local_evidence_status=status, expected_local_evidence_status=expected,
        local_contract_passed=status == expected, guard_reason=reason,
        child_chunk_ids=[i for d in docs for i in d.extra.get("child_chunk_ids", [])])
    if docs:
        messages = build_messages(case["query"], docs)
        result["prompt_within_budget"] = context_budget_ok(messages)
        result["all_evidence_in_prompt"] = all(d.text in messages[0]["content"] for d in docs)
        result["citation_answer_literal_missing"] = [a for a in case["required_evidence"]
            if normalize(a["quote"]) not in normalize(_citation_answer(docs))]
    return result


def summarize(rows):
    answers = [r for r in rows if r["case_kind"] == "answer"]
    return {"answer_denominator": len(answers), "hit_at_5": sum(r["hit_at_5"] for r in answers),
        "mrr_at_5": sum(r["reciprocal_rank_at_5"] for r in answers)/len(answers),
        "all_necessary_evidence": sum(r["all_necessary_evidence"] for r in answers),
        "atoms_present": sum(r["required_atoms_present"] for r in answers),
        "atoms_denominator": sum(r["required_atoms_total"] for r in answers),
        "inapplicable_source_in_top5": sum(bool(r["hard_negative_sources_in_top5"]) for r in answers),
        "failure_classes": dict(Counter(r["failure_class"] for r in answers if r["failure_class"]))}


def run(package, baseline, target, reuse_cache=None):
    import numpy as np
    from pymilvus import MilvusClient
    from qa_core.indexing.embedder.embedder import get_embedder
    from qa_core.rerank.reranker import get_reranker
    rows, vectors, cases, old_protocol = load_fixed(package, baseline)
    settings = get_settings()
    if errors := validate_isolated_environment(settings, "test"):
        raise ValueError(errors)
    if target.exists():
        raise ValueError("refuse overwrite")
    target.mkdir(parents=True)
    inputs = input_hashes(package, baseline, settings)
    if inputs["model_artifact_sha256"] != old_protocol["inputs"]["model_artifact_sha256"]:
        raise ValueError("model artifacts changed; do not mix this comparison with the historical baseline")
    indexed_cases = [c for c in cases if c["case_kind"] == "answer" and int(c["id"].split('-')[1]) <= 12]
    client = MilvusClient(uri=settings.milvus_uri, timeout=60)
    try:
        initial_fingerprint = live_fingerprint(client, rows)
        protocol = {"role": "pending_dev_comparison_only", "metrics_eligible": False, "independent_frozen": False,
            "inputs": inputs, "baseline_protocol_sha256": sha((baseline / "protocol.json").read_bytes()),
            "live_corpus_sha256": initial_fingerprint, "server": client.get_server_version(), "as_of": AS_OF,
            "live_ruler": {"cases": [c["id"] for c in indexed_cases], "answer_denominator": len(indexed_cases),
                "version": "v4-quality-r1", "doc_chunks": 2380, "faq_chunks": 1000,
                "dense_k": settings.top_k_dense, "sparse_k": settings.top_k_sparse, "rerank_doc_k": 10, "faq_extra_k": 5,
                "weighted": [.6, .4], "rrf_k": 60, "selection_split": "dev_only"},
            "snapshot_ruler": {"cases": len(cases), "answer_denominator": 32, "chunks": len(rows),
                "sparse": "not used; four unregistered notes stay on disk", "workflow": "scoped source assembly and existing score threshold",
                "observed_api_status": "not measured; local evidence guard only"},
            "llm_calls": 0, "generation": "prompt/citation construction only; DeepSeek generation and semantic support not measured",
            "production_flag": settings.workflow_evidence_enabled, "shared_configuration_changed": False}
        if reuse_cache:
            previous = json.loads((reuse_cache / "protocol.json").read_text(encoding="utf-8"))
            for key in ("model_artifact_sha256", "cases_sha256", "snapshot_rows_sha256", "snapshot_vectors_sha256"):
                if inputs[key] != previous["inputs"][key]:
                    raise ValueError("cache belongs to different fixed inputs/model")
            protocol["exact_pair_cache_reused"] = {name: sha((reuse_cache / name).read_bytes())
                for name in ("protocol.json", "query-vectors.json", "rank-cache.json")}
        (target / "protocol.json").write_bytes(encode(protocol))
        print("Fixed inputs; loading unchanged local models", flush=True)
        embedder = get_embedder(settings.embedding_model_path, settings.embedding_device)
        ranker = RankCache(get_reranker(settings.reranker_model_path, settings.rerank_device))
        snapshot = SnapshotSearcher(rows, vectors, embedder, settings.top_k_dense)
        if reuse_cache:
            for query, values in json.loads((reuse_cache / "query-vectors.json").read_text(encoding="utf-8")).items():
                vector = np.asarray(values, dtype="float32")
                if vector.shape != (1024,) or not np.isfinite(vector).all() or not np.isclose(np.linalg.norm(vector), 1, atol=.005):
                    raise ValueError("invalid cached query vector")
                snapshot.query_vectors[query] = vector
            for record in json.loads((reuse_cache / "rank-cache.json").read_text(encoding="utf-8")):
                score = record["score"]
                if not isinstance(score, (int,float)) or not 0 <= score <= 1:
                    raise ValueError("invalid cached model score")
                ranker.scores[(record["query"], record["id"], record["text_sha256"])] = score
        live, fixed, diagnosis = {}, [], []
        indexed_ids = {c["id"] for c in indexed_cases}
        for case in cases:
            if case["id"] in indexed_ids:
                result = live_profiles(client, snapshot, case, settings, ranker)
                for name, row in result.items():
                    live.setdefault(name, []).append(row)
                with (target / "live-cases.jsonl").open("ab") as handle:
                    handle.write(json.dumps({"id": case["id"], "profiles": result}, ensure_ascii=False).encode()+b"\n")
            scope = AccessScope.build(**case["request_context"])
            if case["case_kind"] == "answer":
                recalled = cap_per_source(dedup_by_parent(fuse(snapshot.dense(case["query"], scope), [], 1, 0)))
                ranked = ranker.rerank(case["query"], copy.deepcopy(recalled[:10]))
                missing = coverage(case, ranked)
                diagnosis.append({"id": case["id"], "missing_top5": missing,
                    "missing_recall50": coverage(case, recalled), "missing_rerank_input10": coverage(case, recalled[:10]),
                    "bottleneck": "not_applicable" if not missing else "recall" if coverage(case, recalled)
                        else "candidate_cutoff" if coverage(case, recalled[:10]) else "rerank_top5_cutoff"})
            row = guarded(snapshot, ranker, case, settings)
            fixed.append(row)
            with (target / "snapshot-cases.jsonl").open("ab") as handle:
                handle.write(json.dumps(row, ensure_ascii=False).encode()+b"\n")
            print(case["id"], row["local_evidence_status"], row["all_necessary_evidence"], flush=True)
        if live_fingerprint(client, rows) != initial_fingerprint:
            raise ValueError("live corpus changed during comparison; results are invalid")
        load_fixed(package, baseline)  # Revalidate actual hashes, not just case counts.
        old_rows = [json.loads(line) for line in (baseline / "cases.jsonl").read_text(encoding="utf-8").splitlines()]
        summary = {"status": "completed_dev_diagnostic", "metrics_eligible": False, "independent_frozen_cases": 0,
            "live_comparison_24": {name: summarize(results) for name, results in live.items()},
            "snapshot_before_32": summarize(old_rows), "snapshot_guarded_32": summarize(fixed),
            "snapshot_local_status_by_kind": {kind: {"passed": sum(r["local_contract_passed"] for r in fixed if r["case_kind"] == kind),
                "denominator": sum(r["case_kind"] == kind for r in fixed)} for kind in {r["case_kind"] for r in fixed}},
            "diagnosis": dict(Counter(r["bottleneck"] for r in diagnosis)),
            "all_prompts_contain_returned_evidence": all(r.get("all_evidence_in_prompt", True) for r in fixed),
            "prompt_budget_failures": [r["id"] for r in fixed if r.get("prompt_within_budget") is False],
            "citation_truncation_cases": [r["id"] for r in fixed if r.get("citation_answer_literal_missing")],
            "llm_calls": 0, "live_index_unchanged": True, "fixed_inputs_unchanged": True,
            "shared_configuration_changed": False, "production_workflow_enabled": settings.workflow_evidence_enabled,
            "protocol_sha256": sha((target / "protocol.json").read_bytes()),
            "artifact_hashes": {name: sha((target / name).read_bytes()) for name in ("live-cases.jsonl", "snapshot-cases.jsonl")}}
        (target / "diagnosis.json").write_bytes(encode(diagnosis))
        (target / "rank-cache.json").write_bytes(encode([{ "query": key[0], "id": key[1], "text_sha256": key[2], "score": score}
            for key, score in ranker.scores.items()]))
        (target / "query-vectors.json").write_bytes(encode({query: value.tolist() for query,value in snapshot.query_vectors.items()}))
        (target / "summary.json").write_bytes(encode(summary))
        print(json.dumps(summary, ensure_ascii=False, indent=2))
    finally:
        client.close()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--package", type=Path, default=ROOT / "data/candidates/step3-lab-dev-r1")
    parser.add_argument("--baseline", type=Path, default=ROOT / ".runtime/test/reports/step3-20261007-112438/baseline-r2")
    parser.add_argument("--report-dir", type=Path, required=True)
    parser.add_argument("--reuse-cache", type=Path)
    args = parser.parse_args()
    run(args.package, args.baseline, args.report_dir, args.reuse_cache)


if __name__ == "__main__":
    main()
