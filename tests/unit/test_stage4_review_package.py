import hashlib
import json
import pytest
from scripts.prepare_stage4_review import prepare


@pytest.fixture
def candidate(tmp_path):
    (tmp_path / "raw").mkdir(); (tmp_path / "evaluation").mkdir()
    body = b"# title\nverify specific evidence\n"
    (tmp_path / "raw/manual.md").write_bytes(body)
    checksum = hashlib.sha256(body).hexdigest()
    (tmp_path / "raw/meta.json").write_text(json.dumps({"manual.md": {"tenant_id": "tenant", "source_system": "lab", "synthetic": True}}))
    case = {"id": "q1", "group_id": "topic", "required_evidence": [
        {"source": "manual.md", "file_sha256": checksum, "line": 2, "quote": "specific evidence"}]}
    (tmp_path / "evaluation/review-cases.json").write_text(json.dumps({"review_cases": [case]}))
    names = ["raw/manual.md", "raw/meta.json", "evaluation/review-cases.json"]
    (tmp_path / "artifact-hashes.json").write_text(json.dumps({name: hashlib.sha256((tmp_path/name).read_bytes()).hexdigest() for name in names}))
    return tmp_path


def test_review_never_approves_or_relabels_exposed_cases(candidate):
    result = prepare(candidate)
    summary = result["summary.json"]
    assert summary["missing_identity"] == 1 and summary["verified_quote_atoms"] == 1
    assert summary["independent_frozen_cases"] == summary["approved_sources"] == 0
    assert result["source-registration-pending.json"]["entries"][0]["source_id"] == ""
    assert result["evaluation-plan.json"]["evidence_atoms"][0]["split"] == "dev"


def test_changed_candidate_is_rejected(candidate):
    (candidate / "raw/manual.md").write_text("changed")
    with pytest.raises(ValueError, match="artifact changed"): prepare(candidate)


def test_bad_evidence_rejected_even_if_artifact_hash_updated(candidate):
    path = candidate / "evaluation/review-cases.json"
    cases = json.loads(path.read_text())
    cases["review_cases"][0]["required_evidence"][0]["line"] = 0
    path.write_text(json.dumps(cases))
    hashes = json.loads((candidate / "artifact-hashes.json").read_text())
    hashes["evaluation/review-cases.json"] = hashlib.sha256(path.read_bytes()).hexdigest()
    (candidate / "artifact-hashes.json").write_text(json.dumps(hashes))
    with pytest.raises(ValueError, match="position"): prepare(candidate)
