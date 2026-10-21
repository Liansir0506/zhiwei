"""Create 12-core-branch registration and label proposals only; never approve or ingest."""
import argparse
import json
import re
from pathlib import Path
from scripts.prepare_stage4_review import prepare, digest, contained_file
from qa_core.quality.review_contract import bind_pending, audit_groups, check_approval

# Explicit new logical identity proposals for authored scenarios, not legacy aliases.
SOURCE_PROPOSALS = {
    "sop_lab_alm_amf_reg_ingress.md": "lab.amf.registration.ingress",
    "sop_lab_alm_amf_reg_auth.md": "lab.amf.registration.auth",
    "sop_lab_alm_smf_pdu_control.md": "lab.smf.pdu.control",
    "sop_lab_alm_smf_pdu_policy.md": "lab.smf.pdu.policy",
    "sop_lab_alm_upf_drop_queue.md": "lab.upf.drop.queue",
    "sop_lab_alm_upf_drop_rule.md": "lab.upf.drop.rule",
    "sop_lab_alm_nrf_disc_network.md": "lab.nrf.discovery.network",
    "sop_lab_alm_nrf_disc_expiry.md": "lab.nrf.discovery.expiry",
    "sop_lab_alm_udm_auth_dependency.md": "lab.udm.auth.dependency",
    "sop_lab_alm_udm_auth_subscription.md": "lab.udm.auth.subscription",
    "sop_lab_alm_ngap_sctp_path.md": "lab.ngap.sctp.path",
    "sop_lab_alm_ngap_sctp_peer.md": "lab.ngap.sctp.peer",
}


def build(candidate):
    candidate = Path(candidate).resolve()
    prepare(candidate)  # Verify immutable parent and exact original evidence first.
    metadata = json.loads((candidate / "raw/meta.json").read_text(encoding="utf-8"))
    old_cases = json.loads((candidate / "evaluation/review-cases.json").read_text(encoding="utf-8"))["review_cases"]
    if {c["proposed_labels"]["expect_hit"] for c in old_cases} != set(SOURCE_PROPOSALS):
        raise ValueError("explicit 12-source catalog does not match parent cases")
    sources, bodies = [], {}
    for name, proposed_id in SOURCE_PROPOSALS.items():
        path = contained_file(candidate / "raw", name)
        text = path.read_text(encoding="utf-8")
        meta = metadata[name]
        if meta.get("synthetic") is not True or meta.get("origin_type") != "synthetic_lab":
            raise ValueError("cannot propose authored scenario IDs for non-lab sources")
        if meta.get("source_id"):
            raise ValueError("existing identity requires separate registration review")
        bodies[name] = digest(re.sub(r"\s+", "", "\n".join(text.splitlines()[1:])).encode())
        sources.append(bind_pending({"filename": name, "sha256": digest(path.read_bytes()),
            "tenant_id": meta["tenant_id"], "source_system": meta["source_system"],
            "proposed_source_id": proposed_id, "source_revision": digest(path.read_bytes()),
            "scope_metadata": meta, "title": text.splitlines()[0], "applicable_lab_version": meta["applicable_lab_version"],
            "provenance": "project AI-authored synthetic lab draft; not vendor or production evidence",
            "legacy_aliases_approved": False, "registration_status": "proposal_only"}))
    cases = []
    for old in old_cases:
        positive = old["proposed_labels"]["expect_hit"]
        negatives = old["proposed_labels"]["hard_negatives"]
        names = [positive, *negatives]
        if any(name not in SOURCE_PROPOSALS for name in names):
            raise ValueError("unknown hard negative source")
        cases.append(bind_pending({"id": old["id"], "query": old["query"], "stratum": "workflow",
            "split": "dev", "development_exposed": True, "source_ids": [SOURCE_PROPOSALS[n] for n in names],
            "body_families": [bodies[n] for n in names], "topic_groups": [old["group_id"]],
            "template_groups": ["lab-workflow-evidence-exclusion-recovery-v1"],
            "positive_source": positive, "condition_negatives": negatives,
            "negative_review_status": "pending_semantic_condition_review",
            "evidence_units": [{"unit_id": f"{old['id']}-E{i:02}", "required_proposal": True,
                                "support_options": [atom]} for i, atom in enumerate(old["required_evidence"], 1)],
            "applicable_lab_version": old["applicable_lab_version"],
            "expected_state_proposal": "ANSWERED_in_complete_authorized_lab_context",
            "label_review_status": "pending"}))
    groups = audit_groups(cases)
    return {"source-proposals.json": {"protocol": "core-source-proposals-v1", "entries": sources},
        "label-workbook.json": {"protocol": "core-label-review-v1", "metrics_eligible": False, "cases": cases},
        "split-audit.json": {"protocol": "declared-link-audit-v1", "connected_groups": groups,
                             "note": "Declared exact links only, not automatic semantic paraphrase detection"},
        "summary.json": {"sources": len(sources), "cases": len(cases), "evidence_units": sum(len(c["evidence_units"]) for c in cases),
                         "topic_groups": len({g for c in cases for g in c["topic_groups"]}), "connected_groups": len(groups),
                         "approved_sources": 0, "approved_cases": 0, "frozen_cases": 0, "ingestion_run": False}}


def validate_package(candidate, package, *, require_approved=False):
    expected = build(candidate)
    sources = json.loads((Path(package) / "source-proposals.json").read_text(encoding="utf-8"))["entries"]
    cases = json.loads((Path(package) / "label-workbook.json").read_text(encoding="utf-8"))["cases"]
    expected_sources = {s["filename"]: s for s in expected["source-proposals.json"]["entries"]}
    if len(sources) != len(expected_sources) or {s["filename"] for s in sources} != set(expected_sources):
        raise ValueError("source review roster changed")
    for source in sources:
        # This batch's proposed identity/scope is immutable; changed proposals
        # require a separate new package, not silently edited approvals.
        if {k: v for k, v in source.items() if k != "approval"} != {k: v for k, v in expected_sources[source["filename"]].items() if k != "approval"}:
            raise ValueError("source proposal changed; create a new package")
        check_approval(source, required=require_approved)
    expected_cases = {c["id"]: c for c in expected["label-workbook.json"]["cases"]}
    if len(cases) != len(expected_cases) or {c["id"] for c in cases} != set(expected_cases):
        raise ValueError("label review roster changed")
    for case in cases:
        if {k: v for k, v in case.items() if k != "approval"} != {k: v for k, v in expected_cases[case["id"]].items() if k != "approval"}:
            raise ValueError("label proposal changed; create a new package")
        check_approval(case, required=require_approved)
    groups = audit_groups(cases)
    return {"binding_valid": True, "approval_required": require_approved, "connected_groups": len(groups),
            "approved_sources": sum(s["approval"]["status"] == "approved" for s in sources),
            "approved_cases": sum(c["approval"]["status"] == "approved" for c in cases), "ingestion_run": False}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--candidate", required=True); parser.add_argument("--output")
    parser.add_argument("--validate-package"); parser.add_argument("--require-approved", action="store_true")
    args = parser.parse_args()
    if args.validate_package:
        if args.output:
            parser.error("validation is read-only; do not combine with output")
        print(json.dumps(validate_package(args.candidate, args.validate_package, require_approved=args.require_approved)))
        return
    if not args.output or args.require_approved:
        parser.error("generation requires output; require-approved is validation only")
    root, output = Path(args.candidate).resolve(), Path(args.output).resolve()
    if output.exists() or output.is_relative_to(root) or root.is_relative_to(output):
        raise ValueError("output must be new and separate")
    package = build(root); output.mkdir(parents=True, exist_ok=False)
    hashes = {}
    for name, item in package.items():
        data = (json.dumps(item, ensure_ascii=False, sort_keys=True, indent=2) + "\n").encode()
        (output / name).write_bytes(data); hashes[name] = digest(data)
    (output / "artifact-hashes.json").write_text(json.dumps(hashes, indent=2), encoding="utf-8")
    print(json.dumps(package["summary.json"], ensure_ascii=False))


if __name__ == "__main__": main()
