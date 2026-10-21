"""Offline source/evaluation review package; no approvals, ingestion or model calls."""
import argparse
import hashlib
import json
from collections import Counter, defaultdict
from pathlib import Path


def digest(data):
    return hashlib.sha256(data).hexdigest()


def contained_file(root, name):
    path = (root / name).resolve()
    if not path.is_relative_to(root.resolve()) or not path.is_file():
        raise ValueError("missing or escaping review input")
    return path


def prepare(candidate):
    candidate = Path(candidate).resolve()
    hashes = json.loads((candidate / "artifact-hashes.json").read_text(encoding="utf-8"))
    for name, expected in hashes.items():
        if digest(contained_file(candidate, name).read_bytes()) != expected:
            raise ValueError("candidate artifact changed; review cannot continue")
    raw = candidate / "raw"
    metadata = json.loads((raw / "meta.json").read_text(encoding="utf-8"))
    sources, file_hashes = [], {}
    for name, meta in sorted(metadata.items()):
        if Path(name).name != name or "/" in name or "\\" in name:
            raise ValueError("source must be an exact filename")
        checksum = digest(contained_file(raw, name).read_bytes())
        file_hashes[name] = checksum
        sources.append({"filename": name, "sha256": checksum, "tenant_id": meta.get("tenant_id", ""),
            "source_system": meta.get("source_system", ""), "source_id": meta.get("source_id", ""),
            "origin_type": meta.get("origin_type", ""), "synthetic": meta.get("synthetic"),
            "license_status": meta.get("license_status", ""), "evidence_refs": meta.get("evidence_refs", []),
            "external_reference_support_verified": False,
            "approval": {"status": "pending", "proposer": "", "reviewer": "", "evidence": ""}})
    cases = json.loads((candidate / "evaluation/review-cases.json").read_text(encoding="utf-8"))["review_cases"]
    evidence, groups, ids = [], defaultdict(list), set()
    for case in cases:
        if case["id"] in ids or not case.get("group_id"):
            raise ValueError("duplicate case ID or missing semantic group")
        ids.add(case["id"])
        groups[case["group_id"]].append(case["id"])
        for atom in case["required_evidence"]:
            name, line, quote = atom["source"], atom["line"], atom["quote"]
            if name not in file_hashes or atom["file_sha256"] != file_hashes[name]:
                raise ValueError("evidence source hash mismatch")
            lines = contained_file(raw, name).read_text(encoding="utf-8").splitlines()
            if type(line) is not int or not 1 <= line <= len(lines) or not quote or quote not in lines[line - 1]:
                raise ValueError("evidence position or quotation mismatch")
            evidence.append({**atom, "case_id": case["id"], "group_id": case["group_id"],
                             "split": "dev", "approval_status": "pending",
                             "validation": "exact_quote_and_line_only"})
    registry = {"protocol": "reviewed-source-map-v1", "status": "pending_review_not_ingestible",
                "entries": sources, "candidate_input_sha256": digest((candidate / "artifact-hashes.json").read_bytes())}
    plan = {"protocol": "stage4-eval-plan-v1", "status": "planning_not_executable_eval",
        "input_sha256": digest((candidate / "evaluation/review-cases.json").read_bytes()),
        "existing_exposed_groups": dict(groups), "existing_cases_are": "dev_only_already_used",
        "evidence_atoms": evidence, "independent_frozen_cases": 0, "approved_cases": 0,
        "required_strata": ["faq", "alarm_exact", "rag_evidence", "workflow", "clarification", "refusal", "permissions"],
        "grouping_rules": ["link source revisions, equivalent bodies, topics and question templates before splitting",
                           "all paraphrases and linked variants remain in one split",
                           "already exposed cases/groups cannot be relabeled as independent frozen",
                           "new labels bind original positions, never tune labels to retrieved chunks"],
        "metrics": {"hit_at_5": "pending", "evidence_recall_at_5": "pending", "mrr": "pending",
                    "refusal_and_clarification": "pending", "citation_support": "pending"},
        "release_conditions": ["verified stable source identities and access scope",
            "independent human review of query, evidence, negatives and applicable conditions",
            "group leakage audit and frozen exposure declaration", "same corpus/query/ACL/config for ablations"],
        "review_priority": ["12 authored lab branches first", "legacy SOP safety evidence must not be invented",
                            "template FAQ/tickets/cards need information-gain audit before adding more questions"]}
    summary = {"files": len(sources), "missing_identity": sum(not r["tenant_id"] or not r["source_system"] or not r["source_id"] for r in sources),
        "tenants": dict(Counter(r["tenant_id"] for r in sources)), "existing_dev_cases": len(cases),
        "exposed_groups": len(groups), "verified_quote_atoms": len(evidence),
        "approved_sources": 0, "independent_frozen_cases": 0, "ingestion_run": False, "retrieval_run": False}
    return {"source-registration-pending.json": registry, "evaluation-plan.json": plan, "summary.json": summary}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--candidate", required=True)
    parser.add_argument("--output", required=True)
    args = parser.parse_args()
    candidate, output = Path(args.candidate).resolve(), Path(args.output).resolve()
    if output.exists() or output.is_relative_to(candidate) or candidate.is_relative_to(output):
        raise ValueError("review output must be new and separate from candidate")
    package = prepare(candidate)
    output.mkdir(parents=True, exist_ok=False)
    hashes = {}
    for name, value in package.items():
        data = (json.dumps(value, ensure_ascii=False, sort_keys=True, indent=2) + "\n").encode()
        (output / name).write_bytes(data)
        hashes[name] = digest(data)
    (output / "artifact-hashes.json").write_text(json.dumps(hashes, indent=2), encoding="utf-8")
    print(json.dumps(package["summary.json"], ensure_ascii=False))


if __name__ == "__main__":
    main()
