"""Read-only inventory of exposed eval material; never certifies semantic independence.

Usage: python -m scripts.audit_holdout_readiness --legacy F:/heima/myproject/eval_sets/v4-r3-tiered.json
"""
from __future__ import annotations

import argparse
import hashlib
import json
import unicodedata
from collections import defaultdict
from pathlib import Path

from qa_core.quality.eval_review import validate_export
from qa_core.quality.review_contract import audit_groups


def canonical_query(value: str) -> str:
    normalized = unicodedata.normalize("NFKC", value).casefold()
    return "".join(char for char in normalized
                   if unicodedata.category(char)[0] not in {"P", "S", "Z", "C"})


def exact_query_links(suites: dict[str, list[dict]]) -> list[dict]:
    by_query = defaultdict(list)
    for suite, cases in suites.items():
        for case in cases:
            query = canonical_query(case["query"])
            if query:
                by_query[query].append({"suite": suite, "case_id": case["id"]})
    return [{"query_sha256": hashlib.sha256(query.encode()).hexdigest(), "members": members}
            for query, members in sorted(by_query.items())
            if len({item["suite"] for item in members}) > 1]


def load(path: Path) -> tuple[dict, dict]:
    raw = path.read_bytes()
    return json.loads(raw), {"path": str(path), "sha256": hashlib.sha256(raw).hexdigest()}


def audit(root: Path, legacy: Path) -> dict:
    root, legacy = root.resolve(), legacy.resolve()
    files = {
        "legacy_r3": legacy,
        "legacy_v3": root / "eval_sets/v3.json",
        "core_dev": root / "data/candidates/stage4-core-label-review-r1/label-workbook.json",
        "state_dev": root / "data/candidates/stage4-semantic-review-r2/state-paths.json",
        "protocol_dev": root / "data/candidates/stage4-protocol-approved-r2/eval-dev.json",
    }
    payloads, inputs = {}, {}
    for name, path in files.items():
        payloads[name], inputs[name] = load(path)
    legacy_cases = payloads["legacy_r3"]["cases"]
    core = payloads["core_dev"]["cases"]
    state = payloads["state_dev"]["cases"]
    protocol = payloads["protocol_dev"]["cases"]
    core_groups = audit_groups(core + state)
    protocol_groups = validate_export(payloads["protocol_dev"])
    suites = {name: payload["cases"] for name, payload in payloads.items()}
    exact_links = exact_query_links(suites)
    new_cases = core + state + protocol
    overlap = {}
    for field in ("source_ids", "body_families", "topic_groups", "template_groups"):
        core_links = {link for case in core + state for link in case.get(field, [])}
        protocol_links = {link for case in protocol for link in case.get(field, [])}
        overlap[field] = sorted(core_links & protocol_links)
    return {
        "audit_kind": "read_only_exposure_inventory",
        "inputs": inputs,
        "case_counts": {name: len(cases) for name, cases in suites.items()},
        "legacy_r3_split_counts": {split: sum(case.get("split") == split for case in legacy_cases)
                                   for split in ("dev", "regression", "frozen")},
        "new_case_split_counts": {split: sum(case.get("split") == split for case in new_cases)
                                  for split in ("dev", "regression", "frozen")},
        "new_case_development_exposed_count": sum(case.get("development_exposed") is True
                                                  for case in new_cases),
        "new_case_approval_counts": {status: sum(case.get("approval", {}).get("status") == status
                                                 for case in new_cases)
                                     for status in ("pending", "approved", "rejected")},
        "core_state_declared_groups": len(core_groups),
        "core_state_group_sizes": [len(group["case_ids"]) for group in core_groups],
        "protocol_declared_groups": len(protocol_groups),
        "protocol_group_sizes": [len(group["case_ids"]) for group in protocol_groups],
        "cross_package_declared_link_overlap": overlap,
        "cross_suite_exact_query_groups": len(exact_links),
        "cross_suite_exact_query_examples": exact_links[:20],
        "declared_unexposed_approved_frozen_cases": sum(
            case.get("split") == "frozen" and case.get("development_exposed") is False
            and case.get("approval", {}).get("status") == "approved" for case in new_cases),
        "independence_conclusion": "not established; exact/declared links do not detect semantic paraphrases",
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--legacy", required=True, type=Path,
                        help="historical 174-case r3 dataset; read-only")
    parser.add_argument("--root", type=Path, default=Path(__file__).resolve().parents[1])
    args = parser.parse_args()
    print(json.dumps(audit(args.root, args.legacy), ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
