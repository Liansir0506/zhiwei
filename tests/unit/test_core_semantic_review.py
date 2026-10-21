import copy
import json
from pathlib import Path

import pytest

from scripts.review_core_semantics import revise, validate, verify_hashes
from scripts.prepare_stage4_review import digest
from qa_core.quality.review_contract import check_approval, audit_groups, bind_pending


ROOT = Path(__file__).resolve().parents[2]
CANDIDATE = ROOT / "data/candidates/v4-quality-r1-final"
PRIOR = ROOT / "data/candidates/stage4-core-label-review-r1"


@pytest.fixture(scope="module")
def revision():
    return revise(CANDIDATE, PRIOR)


def write_package(root, payload):
    hashes = {}
    for name, value in payload.items():
        data = json.dumps(value, ensure_ascii=False).encode()
        (root / name).write_bytes(data); hashes[name] = digest(data)
    (root / "artifact-hashes.json").write_text(json.dumps(hashes))


def test_twelve_reviews_preserve_old_atoms_and_expand_safety(revision):
    cases = revision["semantic-workbook.json"]["cases"]
    assert len(cases) == 12
    assert sum(len(c["prior_evidence_review"]) for c in cases) == 96
    assert sum(r["decision"] == "replace_keyword_only_support" for c in cases for r in c["prior_evidence_review"]) == 36
    for case in cases:
        assert case["answer_contract"]["mode"] == "read_only_plan_not_action_execution"
        assert case["semantic_review"]["unresolved"]
        check_approval(case)
        with pytest.raises(ValueError, match="pending"): check_approval(case, required=True)
        units = {u["role"]: u for u in case["evidence_units"]}
        assert len(units) == 8
        quote = units["approval_and_rollback"]["support_options"][0]["quote"]
        assert all(token in quote for token in ("操作前", "人工审批", "恶化", "恢复快照"))
        assert "未恢复" in units["recovery_validation"]["support_options"][0]["quote"]


def test_all_supports_match_exact_original_lines(revision):
    for case in revision["semantic-workbook.json"]["cases"]:
        atoms = [u["support_options"][0] for u in case["evidence_units"]]
        atoms.append(case["semantic_review"]["negative_support"])
        for atom in atoms:
            path = CANDIDATE / "raw" / atom["source"]
            assert digest(path.read_bytes()) == atom["file_sha256"]
            assert path.read_text(encoding="utf-8").splitlines()[atom["line"] - 1] == atom["quote"]


@pytest.mark.parametrize("kind,state", [("clarification", "NEEDS_CLARIFICATION"),
    ("no_evidence", "NO_EVIDENCE"), ("permissions", "NO_EVIDENCE"), ("production_boundary", "NO_EVIDENCE")])
def test_state_fixture_contracts_remain_dev_not_scores(revision, kind, state):
    cases = [c for c in revision["state-paths.json"]["cases"] if c["stratum"] == kind]
    assert len(cases) == 12
    for case in cases:
        assert case["expected_state_proposal"] == state
        assert case["expected_citations"] == []
        assert case["expected_action_execution"] is False
        assert case["answerable_retrieval_denominator"] is False
        assert case["split"] == "dev" and case["development_exposed"]
        if kind == "permissions":
            scope = case["fixture_context"]
            assert scope["request_scope"]["tenant_id"] != scope["source_scope"]["tenant_id"]


def test_derived_fixtures_and_originals_stay_one_dev_group(revision):
    combined = revision["semantic-workbook.json"]["cases"] + revision["state-paths.json"]["cases"]
    groups = audit_groups(combined)
    assert len(groups) == 1 and len(groups[0]["case_ids"]) == 60
    altered = copy.deepcopy(combined)
    altered[-1]["split"] = "frozen"; bind_pending(altered[-1])
    with pytest.raises(ValueError, match="crosses"): audit_groups(altered)


def test_readonly_validation_and_require_approval_reject(tmp_path, revision):
    write_package(tmp_path, revision)
    assert validate(CANDIDATE, PRIOR, tmp_path)["runtime_verified"] is False
    with pytest.raises(ValueError, match="pending"):
        validate(CANDIDATE, PRIOR, tmp_path, require_approved=True)


def test_modified_revision_rejected_even_with_new_hashes(tmp_path, revision):
    altered = copy.deepcopy(revision)
    case = altered["state-paths.json"]["cases"][0]
    case["expected_state_proposal"] = "ANSWERED"; bind_pending(case)
    write_package(tmp_path, altered)
    with pytest.raises(ValueError, match="differs"):
        validate(CANDIDATE, PRIOR, tmp_path)


def test_prior_hash_drift_rejected(tmp_path):
    (tmp_path / "file.json").write_text("changed")
    (tmp_path / "artifact-hashes.json").write_text(json.dumps({"file.json": digest(b"original")}))
    with pytest.raises(ValueError, match="changed"): verify_hashes(tmp_path)
