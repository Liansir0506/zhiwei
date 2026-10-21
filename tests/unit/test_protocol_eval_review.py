import copy
from pathlib import Path

import pytest

from scripts.build_protocol_eval_review import build
from qa_core.quality.eval_review import export_review, validate_export, validate_review_payload, export_digest
from qa_core.quality.review_contract import bind_pending
from scripts.run_eval import select_cases, judge

ROOT = Path(__file__).resolve().parents[2] / "data/candidates/stage4-protocol-notes-r1"


@pytest.fixture
def proposal(): return build(ROOT)


def approved_fixture(proposal):
    fixture = copy.deepcopy(proposal)
    for row in fixture["sources"] + fixture["cases"]:
        row["approval"].update(status="approved", proposer="fixture-editor", reviewer="fixture-only-reviewer",
                               evidence="temporary test approval, not real person or project approval")
    return fixture


def test_small_candidate_exact_source_evidence_and_no_frozen(proposal):
    assert len(proposal["sources"]) == 4 and len(proposal["cases"]) == 8
    groups = validate_review_payload(proposal)
    assert len(groups) == 4
    assert all(c["split"] == "dev" and c["development_exposed"] for c in proposal["cases"])
    assert all(s["normative_original"] is False for s in proposal["sources"])


def test_pending_cannot_export_or_bypass_runner_with_final_acceptance(proposal):
    with pytest.raises(ValueError, match="pending"): export_review(proposal, "v-test")
    with pytest.raises(ValueError): select_cases(proposal, "all", final_acceptance=True)


def test_fixture_export_consumed_without_mutating_review(proposal):
    fixture = approved_fixture(proposal)
    output = export_review(fixture, "fixture-not-registered")
    assert len(select_cases(output)) == 8
    assert fixture["metrics_eligible"] is False
    assert output["corpus_registration_verified"] is False
    assert output["frozen_independence"] == "not_claimed"


@pytest.mark.parametrize("field,value", [("query", "edited"), ("split", "frozen"), ("expect_status", "NO_EVIDENCE")])
def test_exported_case_edits_rejected_even_with_outer_digest(proposal, field, value):
    output = export_review(approved_fixture(proposal), "fixture")
    output["cases"][0][field] = value
    output["export_sha256"] = export_digest(output)
    with pytest.raises(ValueError): validate_export(output)


def test_wrong_evidence_line_rejected_even_after_rebinding(proposal):
    proposal["cases"][0]["evidence_units"][0]["line"] = 1
    bind_pending(proposal["cases"][0])
    with pytest.raises(ValueError, match="evidence"): validate_review_payload(proposal)


def test_unsupported_tenant_not_silently_evaluated_as_ops_default(proposal):
    proposal["cases"][0]["request_context"]["tenant_id"] = "other"
    bind_pending(proposal["cases"][0])
    with pytest.raises(ValueError, match="context"): validate_review_payload(proposal)


def test_same_person_cannot_approve(proposal):
    fixture = approved_fixture(proposal)
    fixture["sources"][0]["approval"]["reviewer"] = "fixture-editor"
    with pytest.raises(ValueError, match="independent"): export_review(fixture, "fixture")


def test_exact_source_and_terminal_status_are_judged(proposal):
    case = proposal["cases"][0]
    result = {"route": "retrieval", "answer": "test", "status": "NO_EVIDENCE",
              "sources": [{"source": "copy-" + case["expect_hit"]}],
              "diagnostics": {"degraded": False, "timings_ms": {"total": 1}}}
    judged = judge(case, result)
    assert judged["hit_ok"] is False and judged["status_ok"] is False
    result["sources"][0]["source"] = case["expect_hit"]; result["status"] = "ANSWERED"
    judged = judge(case, result)
    assert judged["hit_ok"] and judged["status_ok"]


def test_legacy_diagnostic_dataset_keeps_compatibility():
    payload = {"cases": [{"id": "legacy", "query": "query"}]}
    assert select_cases(payload, "regression") == payload["cases"]
