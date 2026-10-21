"""Trusted-file approval boundary for new eval proposals; not reviewer authentication."""
import copy
import hashlib
import json

from qa_core.quality.review_contract import check_approval, audit_groups


CANDIDATE_PROTOCOL = "eval-review-candidate-v1"
EXPORT_PROTOCOL = "reviewed-eval-v1"


def export_digest(payload):
    return hashlib.sha256(json.dumps({k: v for k, v in payload.items() if k != "export_sha256"},
        ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False).encode()).hexdigest()


def validate_review_payload(payload, *, require_approved=False):
    if payload.get("protocol") not in {CANDIDATE_PROTOCOL, EXPORT_PROTOCOL}:
        raise ValueError("unsupported review protocol")
    sources, cases = payload.get("sources", []), payload.get("cases", [])
    if not sources or not cases: raise ValueError("empty review roster")
    by_name = {s["filename"]: s for s in sources}
    if len(by_name) != len(sources) or len({s["source_id_proposal"] for s in sources}) != len(sources):
        raise ValueError("duplicate source identity or filename")
    for source in sources:
        check_approval(source, required=require_approved)
        if hashlib.sha256(source["text"].encode()).hexdigest() != source["sha256"]:
            raise ValueError("source text digest mismatch")
        name = source["filename"]
        if not name or any(t in name for t in ("/", "\\", "..")):
            raise ValueError("invalid exact source filename")
        if source["tenant_id"] != "default" or source["scope_metadata"].get("tenant_id") != "default":
            raise ValueError("this initial exporter only supports default tenant sources")
    for case in cases:
        check_approval(case, required=require_approved)
        if case.get("request_context") != {"tenant_id": "default", "roles": ["ops"]}:
            raise ValueError("unsupported eval request context")
        if (case.get("source_match") != "exact" or case.get("expect_route") != "retrieval"
                or case.get("expect_status") != "ANSWERED" or case.get("category") != "rag_evidence"
                or not str(case.get("query", "")).strip()):
            raise ValueError("unsupported first-export case contract")
        source = by_name.get(case.get("expect_hit"))
        if not source or case["source_ids"] != [source["source_id_proposal"]] or case["body_families"] != [source["sha256"]]:
            raise ValueError("case source identity/body binding mismatch")
        units = case.get("evidence_units", [])
        if not units: raise ValueError("evidence required")
        for atom in units:
            lines = source["text"].splitlines(); n = atom.get("line")
            if (atom.get("source") != source["filename"] or atom.get("file_sha256") != source["sha256"]
                    or type(n) is not int or not 1 <= n <= len(lines) or not atom.get("quote")
                    or atom["quote"] != lines[n - 1]):
                raise ValueError("evidence location/content mismatch")
    return audit_groups(cases)


def export_review(payload, kb_version):
    validate_review_payload(payload, require_approved=True)
    if not isinstance(kb_version, str) or not kb_version.strip(): raise ValueError("explicit version required")
    exported = copy.deepcopy(payload)
    exported.update(protocol=EXPORT_PROTOCOL, metrics_eligible=True, kb_version=kb_version,
                    corpus_registration_verified=False, frozen_independence="not_claimed")
    # Export does not register sources, ingest or activate the target version.
    exported["export_sha256"] = export_digest(exported)
    return exported


def validate_export(payload):
    if payload.get("protocol") != EXPORT_PROTOCOL or payload.get("metrics_eligible") is not True:
        raise ValueError("pending review package cannot run official evaluation")
    if not payload.get("kb_version") or payload.get("export_sha256") != export_digest(payload):
        raise ValueError("export version or binding invalid")
    return validate_review_payload(payload, require_approved=True)
