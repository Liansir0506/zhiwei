"""Offline ingestion dry-run; no model, database, cache or live version writes."""
from __future__ import annotations

import argparse
import json
import sys
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from scripts.build_knowledge_candidate import sha, encode
from scripts.calibrate_eval_set import SCOPE_FIELDS, load_catalog
from scripts.validate_enterprise_dataset import _chunks


def verify(candidate: Path, original: Path) -> dict:
    hashes = json.loads((candidate / "artifact-hashes.json").read_text(encoding="utf-8"))
    for relative, expected in hashes.items():
        path = (candidate / relative).resolve()
        if not path.is_relative_to(candidate.resolve()) or sha(path.read_bytes()) != expected:
            raise ValueError(f"candidate artifact changed: {relative}")
    comparison = json.loads((candidate / "comparison.json").read_text(encoding="utf-8"))
    for name, expected in comparison["old_source_sha256"].items():
        if sha((original / name).read_bytes()) != expected:
            raise ValueError(f"original changed: {name}")
    manifest = json.loads((candidate / "manifests/dataset_manifest.json").read_text(encoding="utf-8"))
    catalog, _ = load_catalog(candidate / "raw", manifest)
    metadata = json.loads((candidate / "raw/meta.json").read_text(encoding="utf-8"))
    for name, meta in metadata.items():
        if any(k not in meta for k in SCOPE_FIELDS):
            raise ValueError(f"missing security metadata: {name}")
        if meta["source_version"] != comparison["version"]:
            raise ValueError(f"source version mismatch: {name}")
    old_counts, candidate_counts = Counter(), Counter()
    candidate_chunks = {}
    for root, counts in [(original, old_counts), (candidate / "raw", candidate_counts)]:
        for path in sorted(root.iterdir()):
            if path.is_file() and path.name != "meta.json":
                chunks = _chunks(path)
                if not chunks or any(not c.text.strip() for c in chunks):
                    raise ValueError(f"empty parsed content: {path.name}")
                for chunk in chunks:
                    counts[chunk.metadata["doc_type"]] += 1
                if root == candidate / "raw":
                    candidate_chunks[path.name] = chunks
    cases = json.loads((candidate / "evaluation/review-cases.json").read_text(encoding="utf-8"))["review_cases"]
    verified = 0
    for case in cases:
        positive = case["proposed_labels"]["expect_hit"]
        for negative in case["proposed_labels"]["hard_negatives"]:
            if catalog[negative]["body_sha256"] == catalog[positive]["body_sha256"]:
                raise ValueError("duplicate negative")
        for atom in case["required_evidence"]:
            data = (candidate / "raw" / positive).read_bytes()
            if sha(data) != atom["file_sha256"] or atom["quote"] not in data.decode().splitlines()[atom["line"]-1]:
                raise ValueError("invalid raw evidence")
            if not any(atom["quote"] in chunk.text for chunk in candidate_chunks[positive]):
                raise ValueError("chunker dropped required evidence")
            verified += 1
    return {"status": "offline_checks_passed", "version": comparison["version"],
            "original_unchanged": True, "candidate_hashes_valid": True,
            "old_chunks_by_type": dict(old_counts), "candidate_chunks_by_type": dict(candidate_counts),
            "old_total_chunks": sum(old_counts.values()), "candidate_total_chunks": sum(candidate_counts.values()),
            "verified_quote_count": verified, "retrieval_run": False, "new_hit_at_5": None,
            "note": "Evidence exists in source chunks; this is NOT evidence recalled in top-5."}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--candidate", type=Path, required=True)
    parser.add_argument("--original", type=Path, required=True)
    parser.add_argument("--report", type=Path, required=True)
    args = parser.parse_args()
    result = verify(args.candidate, args.original)
    if args.report.exists():
        raise ValueError("refuse to overwrite verification report")
    args.report.parent.mkdir(parents=True, exist_ok=True)
    args.report.write_bytes(encode(result))
    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
