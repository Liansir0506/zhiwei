"""Materialize an explicit human chat receipt into a new approved dev package."""
import argparse
import copy
import json
from pathlib import Path

from scripts.prepare_stage4_review import digest, contained_file
from scripts.build_protocol_eval_review import build
from qa_core.quality.eval_review import validate_review_payload, export_review
from qa_core.ingest.source_identity import validate_mapping


def prepare(root, receipt_path, output, version):
    root, output = Path(root).resolve(), Path(output).resolve()
    receipt = json.loads(Path(receipt_path).read_text(encoding="utf-8"))
    original = contained_file(root, "review-package.json").read_bytes()
    if (receipt.get("decision") != "approved" or receipt.get("scope") !=
            "four_protocol_summaries_and_eight_dev_cases_isolated_development_only"
            or receipt.get("package_sha256") != digest(original)
            or receipt.get("reviewer_kind") != "human_user_chat_confirmation"
            or not receipt.get("user_message") or not receipt.get("reviewer")
            or not receipt.get("proposer") or receipt["reviewer"] == receipt["proposer"]):
        raise ValueError("explicit content-bound human receipt required")
    payload = json.loads(original)
    if payload != build(root): raise ValueError("original proposal/raw changed after approval")
    if output.exists() or output.is_relative_to(root) or root.is_relative_to(output):
        raise ValueError("approved output must be new and separate")
    approved = copy.deepcopy(payload)
    evidence = "Explicit user approval: " + receipt["user_message"] + "; receipt_sha256=" + digest(Path(receipt_path).read_bytes())
    for item in approved["sources"] + approved["cases"]:
        item["approval"].update(status="approved", reviewer=receipt["reviewer"], proposer=receipt["proposer"], evidence=evidence)
    validate_review_payload(approved, require_approved=True)
    exported = export_review(approved, version)
    metadata, mapping = {}, []
    for source in approved["sources"]:
        name = source["filename"]
        meta = {**source["scope_metadata"], "doc_type": "manual", "source_system": source["source_system"],
            "source_id": source["source_id_proposal"], "source_version": version, "asset_id": "kb-" + digest(
                json.dumps([version, "default", name, source["sha256"]], separators=(",", ":")).encode())[:32],
            "effective_from": None, "effective_to": None, "origin_type": "project_rfc_summary",
            "synthetic": False, "review_status": "human_chat_approved", "license_status": "project_paraphrase",
            "evidence_refs": [source["external_url"]]}
        metadata[name] = meta
        mapping.append({"filename": name, "sha256": source["sha256"], "tenant_id": "default",
            "source_system": source["source_system"], "source_id": source["source_id_proposal"],
            "approval": copy.deepcopy(source["approval"])})
    output.mkdir(parents=True, exist_ok=False); raw = output / "raw"; raw.mkdir()
    for source in approved["sources"]:
        (raw / source["filename"]).write_bytes(contained_file(root / "raw", source["filename"]).read_bytes())
    artifacts = {"approved-review.json": approved, "eval-dev.json": exported, "approval-receipt.json": receipt,
        "raw/meta.json": metadata, "source-map.json": {"protocol": "reviewed-source-map-v1", "entries": mapping}}
    validate_mapping(raw, artifacts["source-map.json"], metadata)
    hashes = {"raw/" + s["filename"]: s["sha256"] for s in approved["sources"]}
    for name, value in artifacts.items():
        data = (json.dumps(value, ensure_ascii=False, sort_keys=True, indent=2) + "\n").encode()
        (output / name).write_bytes(data); hashes[name] = digest(data)
    (output / "artifact-hashes.json").write_text(json.dumps(hashes, indent=2), encoding="utf-8")
    return {"sources": len(metadata), "approved_cases": len(approved["cases"]), "version": version,
            "scope": "isolated_dev_only", "published": False}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    for field in ("root", "receipt", "output", "version"): parser.add_argument("--" + field, required=True)
    args = parser.parse_args()
    print(json.dumps(prepare(args.root, args.receipt, args.output, args.version)))


if __name__ == "__main__": main()
