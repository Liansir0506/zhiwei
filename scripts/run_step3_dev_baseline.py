"""Read-only test snapshot + dense dev diagnosis. Pending labels never become official metrics.

No Milvus insert/create, database registration, cache writes, LLM, or live API calls.
The snapshot adds four authored drafts on disk, using production parsing and local
embedding/reranking. Server BM25 and application terminal status are not measured.
"""
from __future__ import annotations

import argparse
import copy
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from scripts.build_step3_dev_pack import encode, sha, verify
from scripts.calibrate_eval_set import normalize
from qa_core.config.environment import validate_isolated_environment
from qa_core.config.settings import get_settings
from qa_core.retrieval.filters import _to_doc, cap_per_source, dedup_by_parent, fuse, build_filter_expr
from qa_core.security.policy import AccessScope

AS_OF = "2026-10-07T00:00:00+00:00"
COLLECTIONS = {"doc": "test_quality_r1_doc", "faq": "test_quality_r1_faq"}


def diagnostic_allowed(row, scope):
    """Local fixture policy only; this is not the server ABAC implementation."""
    if row.get("tenant_id") != scope.tenant_id or row.get("security_level") not in scope.security_levels:
        return False
    if row.get("status") not in {"active", "draft"}:
        return False
    if row.get("effective_from") and row["effective_from"] > AS_OF:
        return False
    if row.get("effective_to") and row["effective_to"] <= AS_OF:
        return False
    visibility = row.get("visibility")
    if visibility == "role_restricted":
        if not set(scope.roles) & set(row.get("role_scope", []) + row.get("allowed_roles", [])):
            return False
    elif visibility == "department_restricted":
        if row.get("dept_id") not in scope.dept_ids:
            return False
    elif visibility != "public":
        return False
    if scope.device_types and row.get("device_type") not in {"", *scope.device_types}:
        return False
    return True


def snapshot(package, target, embedder, client, sources):
    import numpy as np
    from scripts.validate_enterprise_dataset import _chunks
    from scripts.rebuild_kb import normalize_text, _resanitize
    from qa_core.indexing.chunkers.token_budget import bounded_chunks
    rows, vectors, collection_stats = [], [], {}
    for kind, collection in COLLECTIONS.items():
        before = client.get_collection_stats(collection)["row_count"]
        iterator = client.query_iterator(collection_name=collection, filter='kb_version == "v4-quality-r1"',
                                         output_fields=["*", "dense"], batch_size=256)
        captured = []
        try:
            while batch := iterator.next():
                captured.extend(batch)
        finally:
            iterator.close()
        captured.sort(key=lambda row: row["id"])
        after = client.get_collection_stats(collection)["row_count"]
        if before != after:
            raise ValueError("collection changed during snapshot; retry with a new run")
        for row in captured:
            row = dict(row)
            dense = row.pop("dense")
            row.pop("sparse", None)
            row["collection_kind"] = kind
            row["snapshot_origin"] = "read_only_existing_test_index"
            if kind == "faq" and not row.get("text"):
                row["text"] = f"问：{row.get('question', '')}\n答：{row.get('answer', '')}"
            row["text_sha256"] = sha(row["text"].encode())
            rows.append(row)
            vectors.append(dense)
        collection_stats[collection] = {"before": before, "after": after, "version_rows_snapshotted": len(captured)}
    new_chunks, new_rows = [], []
    for source in sources:
        if not source.get("fixture_key"):
            continue  # Existing twelve SOPs must come from the unchanged real index.
        path = package / "raw" / source["filename"]
        chunks = [_resanitize(chunk) for chunk in _chunks(path)]
        for chunk in chunks:
            chunk.text = normalize_text(chunk.text)
        for number, chunk in enumerate(bounded_chunks(chunks, embedder.model.tokenizer)):
            row = {**chunk.metadata, **source["source_scope"], "id": f"draft-{source['source_id_proposal']}-{number}",
                "text": chunk.text, "doc_type": chunk.doc_type, "source": source["filename"],
                "source_id": source["source_id_proposal"], "source_version": source["source_version"],
                "kb_version": "offline-step3-lab-v1", "collection_kind": "doc",
                "snapshot_origin": "unregistered_draft_on_disk_only", "review_status": "pending",
                "text_sha256": sha(chunk.text.encode())}
            new_chunks.append(chunk.text)
            new_rows.append(row)
    vectors.extend(embedder.embed_passages(new_chunks, batch_size=4))
    rows.extend(new_rows)
    array = np.asarray(vectors, dtype="float32")
    if array.shape != (len(rows), 1024) or not np.isfinite(array).all():
        raise ValueError("invalid dense snapshot")
    if not np.allclose(np.linalg.norm(array, axis=1), 1, atol=0.005):
        raise ValueError("passage vectors are not normalized")
    # Confirm the retained source bodies were actually indexed; do not count a changed
    # original source as if it were the fixed package.
    for source in sources:
        if source.get("fixture_key"):
            continue
        expected = {normalize_text(_resanitize(chunk).text) for chunk in _chunks(package / "raw" / source["filename"])}
        indexed = [row["text"] for row in rows if row["source"] == source["filename"]]
        if not indexed or set(indexed) != expected:
            raise ValueError(f"retained source missing or changed: {source['filename']}")
    (target / "snapshot-rows.json").write_bytes(encode(rows))
    np.save(target / "snapshot-vectors.npy", array, allow_pickle=False)
    return rows, array, {"collections": collection_stats, "draft_chunks_on_disk": len(new_rows),
                         "total_chunks": len(rows), "write_operations": 0}


def input_hashes(package, target, settings):
    code = {p.relative_to(ROOT).as_posix(): sha(p.read_bytes())
            for folder in (ROOT / "qa_core", ROOT / "scripts", ROOT / "scenarios")
            for p in sorted(folder.rglob("*")) if p.suffix in {".py", ".yaml"}}
    code["app.py"] = sha((ROOT / "app.py").read_bytes())
    model_hashes = {}
    for path in {Path(settings.embedding_model_path), Path(settings.reranker_model_path)}:
        model_hashes[path.name] = {}
        for name in ("config.json", "tokenizer.json", "tokenizer_config.json", "modules.json"):
            file = path / name
            if file.exists():
                model_hashes[path.name][name] = sha(file.read_bytes())
        for file in sorted(path.glob("*.safetensors")) + sorted(path.glob("pytorch_model*.bin")):
            with file.open("rb") as handle:
                model_hashes[path.name][file.name] = hashlib.file_digest(handle, "sha256").hexdigest()
    return {"package_manifest_sha256": sha((package / "artifact-hashes.json").read_bytes()),
            "cases_sha256": sha((package / "cases.json").read_bytes()),
            "sources_sha256": sha((package / "sources.json").read_bytes()),
            "snapshot_rows_sha256": sha((target / "snapshot-rows.json").read_bytes()),
            "snapshot_vectors_sha256": sha((target / "snapshot-vectors.npy").read_bytes()),
            "code_sha256": sha(encode(code)), "code_files": code,
            "model_artifact_sha256": model_hashes,
            "git_head": subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=ROOT, text=True).strip(),
            "config": {name: getattr(settings, name) for name in (
                "app_env", "embedding_model_path", "embedding_device", "reranker_model_path", "rerank_device",
                "rerank_min", "top_k_dense", "top_k_sparse", "fusion_method", "workflow_evidence_enabled")}}


def score_case(case, ranked, recalled):
    sources = [doc.source for doc in ranked]
    target_sources = set(case["expect_hit"])
    ranks = [i for i, source in enumerate(sources, 1) if source in target_sources]
    positive = case["case_kind"] == "answer"
    exact_texts = normalize("\n".join(doc.text for doc in ranked if doc.source in target_sources))
    atoms = case["required_evidence"] if positive else []
    found = sum(normalize(atom["quote"]) in exact_texts for atom in atoms)
    negative_hits = sorted(set(case["hard_negatives"]) & set(sources))
    if not positive:
        failure = "terminal_not_measured" if case["case_kind"] != "permission" else (
            "local_permission_leak" if negative_hits else None)
    elif not ranks:
        failure = "recall_miss" if not any(doc.source in target_sources for doc in recalled) else "rerank_displacement"
    elif found < len(atoms):
        failure = "necessary_evidence_incomplete"
    elif negative_hits:
        failure = "inapplicable_source_in_top5"
    else:
        failure = None
    return {"id": case["id"], "case_kind": case["case_kind"], "expected_status": case["expect_status"],
        "observed_api_status": None, "top5": [{"id": d.id, "source": d.source, "dense_score": d.dense_score,
            "rerank_score": d.rerank_score, "text_sha256": sha(d.text.encode())} for d in ranked],
        "hit_at_5": bool(ranks) if positive else None, "reciprocal_rank_at_5": 1/min(ranks) if ranks else (0 if positive else None),
        "required_atoms_present": found if positive else None, "required_atoms_total": len(atoms) if positive else None,
        "all_necessary_evidence": found == len(atoms) if positive else None,
        "hard_negative_sources_in_top5": negative_hits, "failure_class": failure}


def run(package, target):
    import numpy as np
    from pymilvus import MilvusClient
    from qa_core.indexing.embedder.embedder import get_embedder
    from qa_core.rerank.reranker import get_reranker
    if target.exists():
        raise ValueError("refuse overwrite; report directory must be new")
    validation = verify(package)
    settings = get_settings()
    if errors := validate_isolated_environment(settings, "test"):
        raise ValueError(errors)
    sources = json.loads((package / "sources.json").read_text(encoding="utf-8"))["sources"]
    cases = json.loads((package / "cases.json").read_text(encoding="utf-8"))["cases"]
    target.mkdir(parents=True)
    print("Loading local embedding model; no network generation", flush=True)
    embedder = get_embedder(settings.embedding_model_path, settings.embedding_device)
    client = MilvusClient(uri=settings.milvus_uri, timeout=60)
    try:
        rows, vectors, snapshot_info = snapshot(package, target, embedder, client, sources)
        # Compare the mirror's access masks against real production filter expressions
        # on the already indexed rows, without writing fixtures into Milvus.
        scopes = {encode(case["request_context"]): case["request_context"] for case in cases}
        policy_checks = []
        for context in scopes.values():
            scope = AccessScope.build(**context)
            for kind, collection in COLLECTIONS.items():
                fields = {f["name"] for f in client.describe_collection(collection)["fields"]}
                expr = build_filter_expr(list(scope.roles), "v4-quality-r1", tenant_id=scope.tenant_id,
                    dept_ids=scope.dept_ids, security_levels=scope.security_levels, as_of=AS_OF, available_fields=fields)
                iterator = client.query_iterator(collection_name=collection, filter=expr, output_fields=["id"], batch_size=256)
                actual = set()
                try:
                    while batch := iterator.next():
                        actual.update(row["id"] for row in batch)
                finally:
                    iterator.close()
                mirrored = {row["id"] for row in rows if row["collection_kind"] == kind
                    and row["snapshot_origin"] == "read_only_existing_test_index" and diagnostic_allowed(row, scope)}
                policy_checks.append({"context": context, "collection": collection, "count": len(actual),
                                      "passed": actual == mirrored})
        if not all(check["passed"] for check in policy_checks):
            (target / "policy-mismatch.json").write_bytes(encode(policy_checks))
            raise ValueError("local mask disagrees with production filter; no baseline permitted")
    finally:
        client.close()
    protocol = {"status": "unapproved_local_dev_diagnostic", "metrics_eligible": False,
        "independent_frozen": False, "llm_calls": 0, "validation": validation, "snapshot": snapshot_info,
        "inputs": input_hashes(package, target, settings), "as_of": AS_OF,
        "case_count": len(cases), "answer_denominator": sum(c["case_kind"] == "answer" for c in cases),
        "retrieval": {"dense": "exact cosine over fixed read-only snapshot", "recall_k": settings.top_k_dense,
            "faq": "included as dense distractors, not FAQ exact routing", "rerank_candidates": 10, "top_k": 5,
            "sparse_bm25": "not_measured", "fusion_comparison": "not_measured", "query_rewrite": False,
            "route_classification": False, "cache": False, "score_threshold_filter": False},
        "evidence_matching": "Exact source name; NFKC and whitespace normalization for literal quote coverage, not semantic support.",
        "historical_embedding_checkpoint": "Not recorded in historical index; existing vectors are preserved and hashed, not attributed to the current checkpoint.",
        "terminal_status": "not_measured; do not infer clarification or refusal from absence/presence of retrieved documents",
        "policy_checks": policy_checks, "draft_policy": "local proposed scope fixtures; not production authorization",
        "created_at": datetime.now(timezone.utc).isoformat()}
    # Inputs, denominators, and settings are fixed before seeing any case outcome.
    (target / "protocol.json").write_bytes(encode(protocol))
    print(f"Fixed snapshot: {len(rows)} chunks; {len(cases)} cases", flush=True)
    reranker = get_reranker(settings.reranker_model_path, settings.rerank_device)
    results = []
    for case in cases:
        scope = AccessScope.build(**case["request_context"])
        mask = np.asarray([diagnostic_allowed(row, scope) for row in rows])
        if mask.any():
            query_vector = np.asarray(embedder.embed_query(case["query"]), dtype="float32")
            indices = np.flatnonzero(mask)
            scores = vectors[indices] @ query_vector
            order = np.argsort(-scores, kind="stable")[:settings.top_k_dense]
            hits = [{"id": rows[indices[i]]["id"], "entity": rows[indices[i]], "distance": float(scores[i])} for i in order]
            recalled = cap_per_source(dedup_by_parent(fuse(hits, [], w_dense=1, w_sparse=0)))
            ranked = reranker.rerank(case["query"], copy.deepcopy(recalled[:10]), top_n=5)
        else:
            recalled, ranked = [], []
        row = score_case(case, ranked, recalled)
        results.append(row)
        with (target / "cases.jsonl").open("ab") as handle:
            handle.write(json.dumps(row, ensure_ascii=False, sort_keys=True).encode() + b"\n")
        print(case["id"], row["failure_class"] or "diagnostic_pass", flush=True)
    answers = [r for r in results if r["case_kind"] == "answer"]
    permissions = [r for r in results if r["case_kind"] == "permission"]
    summary = {"status": "completed_dev_diagnostic_not_official_metrics", "cases": len(results),
        "answer_denominator": len(answers), "hit_at_5_numerator": sum(r["hit_at_5"] for r in answers),
        "dev_diagnostic_hit_at_5": sum(r["hit_at_5"] for r in answers)/len(answers),
        "dev_diagnostic_mrr_at_5": sum(r["reciprocal_rank_at_5"] for r in answers)/len(answers),
        "all_necessary_evidence_numerator": sum(r["all_necessary_evidence"] for r in answers),
        "necessary_atoms_present": sum(r["required_atoms_present"] for r in answers),
        "necessary_atoms_denominator": sum(r["required_atoms_total"] for r in answers),
        "local_permission_denominator": len(permissions),
        "local_permission_no_target_leak": sum(not r["hard_negative_sources_in_top5"] for r in permissions),
        "clarification_terminal": "not_measured (12 expected cases)", "version_refusal_terminal": "not_measured (4 expected cases)",
        "generation_support": "not_measured", "independent_frozen_cases": 0, "llm_calls": 0,
        "failure_cases": [{"id": r["id"], "class": r["failure_class"]} for r in results if r["failure_class"]],
        "protocol_sha256": sha((target / "protocol.json").read_bytes()),
        "cases_jsonl_sha256": sha((target / "cases.jsonl").read_bytes()),
        "inputs_unchanged": verify(package) == validation}
    (target / "summary.json").write_bytes(encode(summary))
    print(json.dumps(summary, ensure_ascii=False, indent=2))


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--package", type=Path, default=ROOT / "data/candidates/step3-lab-dev-r1")
    p.add_argument("--report-dir", type=Path, required=True)
    args = p.parse_args()
    run(args.package, args.report_dir)


if __name__ == "__main__":
    main()
