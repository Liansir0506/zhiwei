#!/usr/bin/env python
"""离线校验 v4 数据：规模、来源、哈希、脱敏、去重、引用完整性和评测泄漏。"""
from __future__ import annotations

import argparse
import hashlib
import json
import re
import sys
from collections import Counter
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from qa_core.domain.models import EnterpriseSnapshot
from qa_core.indexing.chunkers.registry import chunk_rows, chunk_sop, chunk_standard, chunk_manual
from qa_core.indexing.parsers.parsers import parse_dispatch

PHONE = re.compile(r"(?<!\d)1[3-9]\d{9}(?!\d)")
ID_CARD = re.compile(r"(?<!\d)\d{17}[\dXx](?!\d)")
GENERATOR_REQUIRED = {"generator_version", "generation_seed", "origin_type",
                      "review_status", "license_status", "evidence_refs"}


def _sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _norm(text: str) -> str:
    return re.sub(r"\W+", "", text).lower()


def _chunks(path: Path):
    doc_type = next(t for t in ("fault_case", "faq", "manual", "standard", "guide", "sop")
                    if path.name.startswith(t + "_"))
    parsed = parse_dispatch(doc_type, path)
    if doc_type in {"fault_case", "faq"}:
        return chunk_rows(doc_type, parsed)
    if doc_type == "sop":
        return chunk_sop(parsed, source=path.name)
    if doc_type == "standard":
        return chunk_standard(parsed, source=path.name)
    return chunk_manual(parsed, source=path.name)


def validate(output_root: Path, version: str, eval_dir: Path | None) -> dict:
    raw_dir = output_root / "raw" / version
    manifest_dir = output_root / "manifests" / version
    dataset_dir = output_root / "datasets" / version
    manifest = json.loads((manifest_dir / "dataset_manifest.json").read_text(encoding="utf-8"))
    sources = json.loads((manifest_dir / "sources.json").read_text(encoding="utf-8"))
    meta = json.loads((raw_dir / "meta.json").read_text(encoding="utf-8"))
    snapshot = EnterpriseSnapshot.model_validate_json(
        (dataset_dir / "enterprise_snapshot.json").read_text(encoding="utf-8"))

    errors: list[str] = []
    files = [p for p in raw_dir.iterdir() if p.is_file() and p.name != "meta.json"]
    expected_hashes = {row["filename"]: row["sha256"] for row in manifest["files"]}
    actual_names = {p.name for p in files}
    if actual_names != set(expected_hashes):
        errors.append("manifest file set differs from raw directory")
    bad_hashes = [p.name for p in files if expected_hashes.get(p.name) != _sha(p)]
    if bad_hashes:
        errors.append(f"checksum mismatch: {bad_hashes[:5]}")
    missing_meta = sorted(actual_names - set(meta))
    incomplete_meta = sorted(name for name in actual_names
                             if not GENERATOR_REQUIRED.issubset(meta.get(name, {})))
    if missing_meta:
        errors.append(f"missing metadata: {missing_meta[:5]}")
    if incomplete_meta:
        errors.append(f"incomplete governance metadata: {incomplete_meta[:5]}")

    source_ids = {row["source_id"] for row in sources}
    bad_refs = sorted({ref for item in meta.values() for ref in item.get("evidence_refs", [])
                       if ref not in source_ids})
    if bad_refs:
        errors.append(f"unknown evidence refs: {bad_refs}")
    redistributed = [row["source_id"] for row in sources if row.get("redistributed")]
    if redistributed:
        errors.append(f"third-party full text marked redistributed: {redistributed}")

    all_chunks = []
    pii_files = []
    for path in sorted(files):
        text = path.read_text(encoding="utf-8-sig", errors="ignore")
        if PHONE.search(text) or ID_CARD.search(text):
            pii_files.append(path.name)
        all_chunks.extend(_chunks(path))
    if pii_files:
        errors.append(f"PII patterns found: {pii_files[:5]}")
    chunk_hashes = [hashlib.sha256(chunk.text.encode()).hexdigest() for chunk in all_chunks]
    duplicate_chunks = sum(count - 1 for count in Counter(chunk_hashes).values() if count > 1)
    duplicate_hashes = {value for value, count in Counter(chunk_hashes).items() if count > 1}
    duplicate_types = Counter(chunk.doc_type for chunk, digest in zip(all_chunks, chunk_hashes)
                              if digest in duplicate_hashes)
    if duplicate_chunks:
        errors.append(f"exact duplicate chunks: {duplicate_chunks}")

    # 领域引用完整性：Pydantic 管字段，集合关系在此补校验。
    device_ids = {row.device_id for row in snapshot.devices}
    alarm_ids = {row.alarm_id for row in snapshot.alarms}
    ticket_ids = {row.ticket_id for row in snapshot.work_orders}
    user_ids = {row.user_id for row in snapshot.users}
    broken_refs = 0
    broken_refs += sum(a.device_id not in device_ids for a in snapshot.alarms if a.device_id)
    broken_refs += sum(w.device_id not in device_ids for w in snapshot.work_orders if w.device_id)
    broken_refs += sum(w.alarm_id not in alarm_ids for w in snapshot.work_orders if w.alarm_id)
    broken_refs += sum(w.assignee_user_id not in user_ids for w in snapshot.work_orders if w.assignee_user_id)
    broken_refs += sum(c.device_id not in device_ids for c in snapshot.changes if c.device_id)
    broken_refs += sum(c.ticket_id not in ticket_ids for c in snapshot.changes if c.ticket_id)
    broken_refs += sum(a.reviewed_by not in user_ids for a in snapshot.knowledge_assets if a.reviewed_by)
    if broken_refs:
        errors.append(f"broken relational references: {broken_refs}")

    knowledge_text = "\n".join(chunk.text for chunk in all_chunks)
    norm_knowledge = _norm(knowledge_text)
    leaked_cases = []
    if eval_dir and eval_dir.is_dir():
        for path in sorted(eval_dir.glob("*.json")):
            payload = json.loads(path.read_text(encoding="utf-8"))
            for case in payload.get("cases", []):
                query = _norm(str(case.get("query", "")))
                if len(query) >= 8 and query in norm_knowledge:
                    leaked_cases.append(f"{path.name}:{case.get('id')}")
    if leaked_cases:
        errors.append(f"evaluation query leakage: {leaked_cases[:10]}")

    counts = {
        **manifest["counts"], "raw_files": len(files), "predicted_chunks": len(all_chunks),
        "exact_duplicate_chunks": duplicate_chunks, "broken_references": broken_refs,
        "duplicate_chunk_types": dict(duplicate_types),
        "evaluation_leaks": len(leaked_cases), "source_catalog_entries": len(sources),
        "origin_types": dict(Counter(item["origin_type"] for item in meta.values())),
    }
    targets = {
        "reference_chapters": (100, 300), "experiment_scenarios": (20, 50),
        "devices": (300, 1000), "alarms": (5000, 20000),
        "work_orders": (500, 1000), "changes": (300, 500),
        "predicted_chunks": (5000, 15000),
    }
    target_results = {}
    for name, (minimum, maximum) in targets.items():
        value = counts[name]
        target_results[name] = {"value": value, "min": minimum, "max": maximum,
                                "passed": minimum <= value <= maximum}
        if not target_results[name]["passed"]:
            errors.append(f"target out of range: {name}={value}")
    return {"dataset_version": version, "passed": not errors, "errors": errors,
            "counts": counts, "targets": target_results,
            "checks": {"hashes": not bad_hashes, "metadata": not missing_meta and not incomplete_meta,
                       "pii": not pii_files, "duplicates": duplicate_chunks == 0,
                       "references": broken_refs == 0, "eval_leakage": not leaked_cases,
                       "no_third_party_fulltext": not redistributed}}


def main() -> None:
    parser = argparse.ArgumentParser(description="验证企业模拟数据")
    parser.add_argument("--output-root", type=Path, required=True)
    parser.add_argument("--version", default="v4-foundation")
    parser.add_argument("--eval-dir", type=Path, default=Path("eval_sets"))
    parser.add_argument("--report", type=Path)
    args = parser.parse_args()
    result = validate(args.output_root.resolve(), args.version, args.eval_dir.resolve())
    report = args.report or (args.output_root / "manifests" / args.version / "quality_report.json")
    report.parent.mkdir(parents=True, exist_ok=True)
    report.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(result, ensure_ascii=False, indent=2))
    raise SystemExit(0 if result["passed"] else 2)


if __name__ == "__main__":
    main()
