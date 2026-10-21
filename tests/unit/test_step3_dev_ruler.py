"""Protect denominators, review boundaries, source integrity and negative evidence."""
import json
from pathlib import Path

import pytest

from qa_core.quality.review_contract import audit_groups, bind_pending, check_approval
from qa_core.schemas.retrieval import RetrievedDoc
from qa_core.security.policy import AccessScope
from scripts.build_step3_dev_pack import ROOT, build, lab_records, verify
from scripts.run_eval import select_cases
from scripts.run_step3_dev_baseline import diagnostic_allowed, score_case


@pytest.fixture
def package(tmp_path):
    target = tmp_path / "pack"
    build(target, ROOT / "data/candidates/v4-quality-r1-final")
    return target


def test_numeric_records_are_recomputed_not_observed():
    record = lab_records()
    result = record["computed_outputs"]
    assert result["amf_v09_delta_error_ratio"] == .2
    assert result["amf_v10_window_error_ratios"] == [.3, .2]
    assert result["nrf_remaining_s"] == 5
    assert result["upf_rule_matched_ratio"] == .05
    assert result["upf_unattributed_drop"] == 10
    assert result["smf_durations_ms"] == [{"tx": "A", "policy": 20, "control": 200}, {"tx": "B", "policy": 180, "control": 20}]
    assert "not executed" in record["origin"]


def test_pack_has_explicit_denominators_and_no_independent_split(package):
    result = verify(package)
    assert result["knowledge_units"] == 16
    assert result["cases"] == 52
    assert result["case_kinds"] == {"answer": 32, "clarification": 12, "version_boundary": 4, "permission": 4}
    assert result["connected_groups"] == 1
    assert result["independent_frozen_cases"] == 0
    cases = json.loads((package / "cases.json").read_text(encoding="utf-8"))["cases"]
    for case in cases:
        with pytest.raises(ValueError, match="pending"):
            check_approval(case, required=True)


def test_drafts_cannot_enter_official_eval(package):
    payload = json.loads((package / "cases.json").read_text(encoding="utf-8"))
    with pytest.raises(ValueError):
        select_cases(payload, "dev")


def test_source_tamper_is_not_silently_accepted(package):
    source = next((package / "raw").glob("*.md"))
    source.write_bytes(source.read_bytes() + b"\nchanged\n")
    with pytest.raises(ValueError, match="hash mismatch"):
        verify(package)


def test_exposed_template_cannot_be_random_split(package):
    cases = json.loads((package / "cases.json").read_text(encoding="utf-8"))["cases"]
    cases[0]["split"] = "frozen"
    cases[0]["development_exposed"] = False
    bind_pending(cases[0])
    with pytest.raises(ValueError, match="crosses splits"):
        audit_groups(cases)


@pytest.mark.parametrize("change,context,allowed", [
    ({}, {"tenant_id": "default", "roles": ["ops"]}, True),
    ({}, {"tenant_id": "other", "roles": ["admin"]}, False),
    ({}, {"roles": ["guest"]}, False),
    ({"security_level": "restricted"}, {"roles": ["ops"]}, False),
    ({"visibility": "role_restricted", "role_scope": ["ops"]}, {"roles": ["auditor"]}, False),
    ({"visibility": "role_restricted", "role_scope": ["ops"]}, {"roles": ["ops"]}, True),
    ({"visibility": "department_restricted", "dept_id": "lab-a"}, {"roles": ["ops"], "dept_ids": ["lab-b"]}, False),
    ({"visibility": "department_restricted", "dept_id": "lab-a"}, {"roles": ["ops"], "dept_ids": ["lab-a"]}, True),
    ({"effective_to": "2026-10-07T00:00:00+00:00"}, {"roles": ["ops"]}, False),
    ({"effective_from": "2027-10-07T00:00:00+00:00"}, {"roles": ["ops"]}, False),
    ({"status": "retired"}, {"roles": ["admin"]}, False),
])
def test_local_fixture_filter_fails_closed(change, context, allowed):
    row = {"tenant_id": "default", "security_level": "internal", "visibility": "public", "status": "draft",
           "allowed_roles": [], "role_scope": [], **change}
    assert diagnostic_allowed(row, AccessScope.build(**context)) is allowed


def test_wrong_source_quote_does_not_satisfy_necessary_evidence():
    case = {"id": "a", "case_kind": "answer", "expect_hit": ["correct.md"], "expect_status": "ANSWERED",
            "required_evidence": [{"quote": "critical condition"}], "hard_negatives": ["wrong.md"]}
    correct = RetrievedDoc("1", "other text", .7, source="correct.md")
    wrong = RetrievedDoc("2", "critical condition", .6, source="wrong.md")
    row = score_case(case, [correct, wrong], [correct, wrong])
    assert row["hit_at_5"] and row["required_atoms_present"] == 0
    assert row["failure_class"] == "necessary_evidence_incomplete"


def test_negative_case_cannot_be_scored_as_answer_hit():
    case = {"id": "denied", "case_kind": "permission", "expect_hit": [], "expect_status": "NO_EVIDENCE",
            "required_evidence": [], "hard_negatives": ["private.md"]}
    row = score_case(case, [RetrievedDoc("1", "secret", 1, source="private.md")], [])
    assert row["hit_at_5"] is None and row["reciprocal_rank_at_5"] is None
    assert row["observed_api_status"] is None
    assert row["failure_class"] == "local_permission_leak"


def test_ingest_punctuation_normalization_preserves_literal_evidence():
    case = {"id": "norm", "case_kind": "answer", "expect_hit": ["correct.md"], "expect_status": "ANSWERED",
            "required_evidence": [{"quote": "比例：40/800=5%；"}], "hard_negatives": []}
    doc = RetrievedDoc("1", "比例:40/800=5%;", .7, source="correct.md")
    assert score_case(case, [doc], [doc])["all_necessary_evidence"]
