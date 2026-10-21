"""Same-ruler retrieval-only dev diagnosis, NOT the historical end-to-end Hit@5."""
from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from scripts.build_knowledge_candidate import encode, sha
from scripts.calibrate_eval_set import normalize
from scripts.ingest_quality_candidate import PROFILES
from qa_core.config.settings import get_settings
from qa_core.config.environment import validate_isolated_environment
from qa_core.retrieval.searcher import Searcher
from qa_core.rerank.reranker import get_reranker
from qa_core.security.policy import AccessScope


class NoCache:
    def get(self, *args): return None
    def get_with_lock(self, *args): return None
    def set(self, *args): pass


def required_atoms_present(case, texts):
    normalized = normalize("\n".join(texts))
    required = [a for a in case["evidence_requirements"] if a.get("required", True)]
    return sum(any(normalize(ref["quote"]) in normalized for ref in atom["any_of"]) for atom in required), len(required)


def acl_probe(client, settings):
    from scripts.init_collections import build
    from qa_core.retrieval.filters import build_filter_expr
    name = "test_quality_acl_probe"
    build(client, name, False)
    base = client.query(collection_name=PROFILES["candidate"][1], filter='kb_version == "v4-quality-r1"',
                        output_fields=["*", "dense"], limit=1)[0]
    schema_fields = {f["name"] for f in client.describe_collection(collection_name=name)["fields"]}
    base = {k: v for k, v in base.items() if k in schema_fields and k != "sparse"}
    rows = []
    changes = {
        "public": {},
        "role": {"visibility": "role_restricted", "role_scope": ["ops"], "allowed_roles": ["ops"]},
        "dept": {"visibility": "department_restricted", "dept_id": "dept-a"},
        "tenant": {"tenant_id": "other"},
        "restricted": {"security_level": "restricted"},
        "retired": {"status": "retired"},
        "expired": {"effective_to": "2026-01-01T00:00:00+00:00"},
        "future": {"effective_from": "2027-01-01T00:00:00+00:00"},
    }
    for key, delta in changes.items():
        rows.append({**base, "id": key, "source": key, "kb_version": "quality-acl-probe",
                     "effective_from": "2026-01-01T00:00:00+00:00",
                     "effective_to": "2028-01-01T00:00:00+00:00", **delta})
    client.upsert(collection_name=name, data=rows)
    client.flush(name)
    checks = []
    cases = [("ops-dept-a", ["ops"], "default", ["dept-a"], {"public", "role", "dept"}),
             ("ops-dept-b", ["ops"], "default", ["dept-b"], {"public", "role"}),
             ("guest", ["guest"], "default", [], set()),
             ("other-tenant", ["ops"], "other", [], {"tenant"}),
             ("admin", ["admin"], "default", [], {"public", "restricted"})]
    for label, roles, tenant, departments, expected in cases:
        scope = AccessScope.build(tenant_id=tenant, roles=roles, dept_ids=departments)
        expr = build_filter_expr(list(scope.roles), "quality-acl-probe", tenant_id=tenant,
                 dept_ids=scope.dept_ids, security_levels=scope.security_levels,
                 as_of="2026-09-27T00:00:00+00:00", available_fields=schema_fields)
        found = {r["source"] for r in client.query(collection_name=name, filter=expr, output_fields=["source"], limit=100)}
        checks.append({"case": label, "expected": sorted(expected), "found": sorted(found), "passed": found == expected})
    return {"collection": name, "checks": checks, "passed": all(c["passed"] for c in checks),
            "note": "Synthetic fixtures, retrieval prefilter only; not full security certification."}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--candidate", type=Path, required=True)
    parser.add_argument("--sop-review", type=Path, required=True)
    parser.add_argument("--report", type=Path, required=True)
    args = parser.parse_args()
    if args.report.exists():
        raise ValueError("refuse overwrite")
    settings = get_settings()
    if errors := validate_isolated_environment(settings, "test"):
        raise ValueError(errors)
    from pymilvus import MilvusClient
    client = MilvusClient(uri=settings.milvus_uri, timeout=30)
    review = json.loads(args.sop_review.read_text(encoding="utf-8"))
    old = [c for c in review["review_cases"] if c["id"] in {"R01", "R03", "R06", "R08", "R09", "R11", "R15", "R18", "R19", "R20"}]
    new = json.loads((args.candidate / "evaluation/review-cases.json").read_text(encoding="utf-8"))["review_cases"]
    if len(old) != 10 or len(new) != 12:
        raise ValueError("comparison ruler requires exactly 10 SOP and 12 new dev probes")
    # Old SOP families are diagnostic-only equivalence, NEVER ordinary approved source aliases.
    families = {m["retained"]: {m["retained"], *m["removed_from_candidate_only"]}
                for m in json.loads((args.candidate / "manifests/dedup-map.json").read_text(encoding="utf-8"))}
    comparisons, new_results = [], []
    original_names = (settings.doc_collection, settings.faq_collection)
    result = {"status": "retrieval_only_dev_diagnostic", "metrics_eligible": False,
              "historical_hit_at_5_unchanged": 0.827, "independent_frozen": False,
              "algorithm_changed": False, "answer_guard_or_generation_tested": False,
              "ruler": "10 previously reviewed SOP questions; exact-body diagnostic families, not approved aliases",
              "candidate_sha256": sha((args.candidate / "artifact-hashes.json").read_bytes()),
              "sop_review_sha256": sha(args.sop_review.read_bytes())}
    result["protocol"] = {"query_rewrite": False, "route_classification": False, "cache": False,
                          "dense_sparse_doc_candidates": 10, "faq_candidates": 5, "final_chunks": 5,
                          "config": {k: getattr(settings, k) for k in ("fusion_method", "dense_ef", "top_k_dense", "top_k_sparse", "rerank_min")}}
    # Save the protocol/ruler before model inference; never edit it after seeing outcomes.
    protocol_path = args.report.with_suffix(".protocol.json")
    if protocol_path.exists():
        raise ValueError("protocol already exists; choose a new run report")
    protocol_path.parent.mkdir(parents=True, exist_ok=True)
    protocol_path.write_bytes(encode(result))
    try:
        result["acl_probe"] = acl_probe(client, settings)
        if not result["acl_probe"]["passed"]:
            raise RuntimeError("ACL probe failed")
        reranker = get_reranker(settings.reranker_model_path, settings.rerank_device)
        runs = {}
        for profile, (version, doc, faq) in PROFILES.items():
            os.environ.update(DOC_COLLECTION=doc, FAQ_COLLECTION=faq)
            get_settings.cache_clear()
            searcher = Searcher(client=client, settings=get_settings(), cache=NoCache())
            runs[profile] = {}
            cases = old if profile == "control" else old + new
            for case in cases:
                query = case["query"]
                recalled = searcher.search_docs([query], ["ops"], version).docs[:10]
                recalled += searcher.search_faq(query, ["ops"], version, top_k=5)
                top = reranker.rerank(query, recalled, top_n=5)
                sources = [d.source for d in top]
                row = {"id": case["id"], "sources": sources,
                       "top5": [{"source": d.source, "text": d.text, "rerank_score": d.rerank_score} for d in top]}
                if case in old:
                    target = case["legacy_labels"]["expect_hit"]
                    target = target[0] if isinstance(target, list) else target
                    family = next(v for v in families.values() if target in v)
                    row["diagnostic_family_present"] = any(source in family for source in sources)
                    present, total = required_atoms_present(case, [d.text for d in top if d.source in family])
                    row.update(literal_required_atoms_present=present, required_atoms_total=total)
                    row["probe_note"] = "Family presence does not guarantee complete ordered steps."
                    runs[profile][case["id"]] = row
                else:
                    label = case["proposed_labels"]
                    row["source_present"] = label["expect_hit"] in sources
                    row["inapplicable_branch_present"] = any(s in label["hard_negatives"] for s in sources)
                    texts = "\n".join(d.text for d in top if d.source == label["expect_hit"])
                    row["literal_required_atoms_present"] = sum(normalize(a["quote"]) in normalize(texts) for a in case["required_evidence"])
                    row["required_atoms_total"] = len(case["required_evidence"])
                    new_results.append(row)
                print(f"{profile} {case['id']} {sources}", flush=True)
        for case in old:
            comparisons.append({"id": case["id"], "control": runs["control"][case["id"]],
                                "candidate": runs["candidate"][case["id"]]})
        result.update(sop_comparisons=comparisons, new_dev_probes=new_results,
            control_family_present=sum(r["control"]["diagnostic_family_present"] for r in comparisons),
            candidate_family_present=sum(r["candidate"]["diagnostic_family_present"] for r in comparisons),
            sop_cases=len(comparisons), new_source_present=sum(r["source_present"] for r in new_results),
            new_inapplicable_branch_present=sum(r["inapplicable_branch_present"] for r in new_results),
            new_all_literal_atoms_present=sum(r["literal_required_atoms_present"] == r["required_atoms_total"] for r in new_results))
        args.report.parent.mkdir(parents=True, exist_ok=True)
        args.report.write_bytes(encode(result))
        print(json.dumps({k:v for k,v in result.items() if k not in {"sop_comparisons", "new_dev_probes", "acl_probe"}}, ensure_ascii=False, indent=2))
    finally:
        client.close()
        os.environ.update(DOC_COLLECTION=original_names[0], FAQ_COLLECTION=original_names[1])
        get_settings.cache_clear()


if __name__ == "__main__":
    main()
