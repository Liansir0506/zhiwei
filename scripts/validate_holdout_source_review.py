"""Offline integrity check for the pending, source-only holdout review packet."""

import argparse
import hashlib
import json
from pathlib import Path

from qa_core.quality.review_contract import check_approval


DEFAULT_PACKAGE = Path("data/candidates/stage4-holdout-source-review-r1")
DEFAULT_RECEIPT = Path("docs/STAGE4_BFD_NETCONF_CONTENT_ACK_2026-09-29.json")
EXPECTED = {
    "bfd_detection_note.md": ("https://www.rfc-editor.org/rfc/rfc5880.html", ["6.8.4"]),
    "netconf_lock_candidate_note.md": (
        "https://www.rfc-editor.org/rfc/rfc6241.html", ["7.5", "8.3.1", "8.3.4.1"]
    ),
}


def validate_package(package: Path) -> dict:
    manifest = json.loads((package / "source-review.json").read_text(encoding="utf-8"))
    if set(manifest) != {"package_id", "purpose", "created_on", "independence", "sources"}:
        raise ValueError("unexpected package fields (no cases or scores allowed)")
    if manifest["purpose"] != "source_cards_only_pending_human_review":
        raise ValueError("source-only purpose required")
    if not str(manifest["independence"]).startswith("not_established"):
        raise ValueError("independence cannot be asserted at source-card stage")
    sources = manifest["sources"]
    if not isinstance(sources, list) or len(sources) != len(EXPECTED):
        raise ValueError("exactly two source cards required")
    seen = set()
    for source in sources:
        if set(source) != {
            "filename", "source_id_proposal", "sha256", "origin_type", "normative_original",
            "external_url", "external_sections", "read_date", "evidence_lines",
            "registration_status", "license_review", "approval"
        }:
            raise ValueError("unexpected source fields")
        name = source["filename"]
        if name not in EXPECTED or name in seen:
            raise ValueError("unexpected or duplicate source file")
        seen.add(name)
        expected_url, expected_sections = EXPECTED[name]
        if (source["external_url"], source["external_sections"]) != (expected_url, expected_sections):
            raise ValueError("RFC provenance changed")
        if source["origin_type"] != "AI_authored_paraphrase" or source["normative_original"] is not False:
            raise ValueError("source provenance must be explicit")
        if source["registration_status"] != "proposal_only" or source["license_review"] != "pending":
            raise ValueError("registration/license review must remain pending")
        if source["approval"] != {
            "status": "pending", "proposer": "", "reviewer": "", "evidence": "",
            "binding_sha256": source["approval"].get("binding_sha256")
        }:
            raise ValueError("human approval has not occurred")
        check_approval(source)
        body = (package / "raw" / name).read_bytes()
        if hashlib.sha256(body).hexdigest() != source["sha256"]:
            raise ValueError("source body changed; re-review required")
        lines = body.decode("utf-8").splitlines()
        if not source["evidence_lines"] or any(
            type(line) is not int or line < 1 or line > len(lines) or not lines[line - 1].strip()
            for line in source["evidence_lines"]
        ):
            raise ValueError("invalid evidence line")
    return {"package_id": manifest["package_id"], "pending_sources": sorted(seen), "cases": 0}


def validate_content_receipt(package: Path, receipt_path: Path) -> dict:
    """Bind the user's wording acknowledgement; never promote source approval."""
    receipt = json.loads(receipt_path.read_text(encoding="utf-8"))
    if receipt.get("protocol") != "chat-content-acknowledgement-v1" or receipt.get("decision") != "content_accepted_only":
        raise ValueError("not a content-only acknowledgement")
    if receipt.get("user_message") != "内容认可" or receipt.get("reviewer") != "user_in_current_chat":
        raise ValueError("unexpected acknowledgement provenance")
    manifest_path = package / "source-review.json"
    if hashlib.sha256(manifest_path.read_bytes()).hexdigest() != receipt.get("package_sha256"):
        raise ValueError("content acknowledgement package hash changed")
    result = validate_package(package)
    expected = {
        name: hashlib.sha256((package / "raw" / name).read_bytes()).hexdigest()
        for name in result["pending_sources"]
    }
    if receipt.get("source_sha256") != expected:
        raise ValueError("content acknowledgement source hash changed")
    return {"decision": receipt["decision"], "bound_sources": result["pending_sources"],
            "source_approval": "pending"}


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--package", type=Path, default=DEFAULT_PACKAGE)
    parser.add_argument("--receipt", type=Path, default=DEFAULT_RECEIPT)
    args = parser.parse_args()
    result = validate_package(args.package)
    result["content_receipt"] = validate_content_receipt(args.package, args.receipt)
    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
