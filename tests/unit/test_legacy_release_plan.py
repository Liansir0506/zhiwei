from pathlib import Path
import pytest
from scripts.prepare_legacy_release import report_parent, require_first_release


@pytest.mark.parametrize("versions,jobs,keys", [
    ([], 0, 0),
    ([{"version": "test-legacy-demo-r1", "status": "ACTIVE"}], 0, 0),
    ([{"version": "test-legacy-demo-r1", "status": "STAGED"}, {"version": "other", "status": "ARCHIVED"}], 0, 0),
    ([{"version": "test-legacy-demo-r1", "status": "STAGED"}], 1, 0),
    ([{"version": "test-legacy-demo-r1", "status": "STAGED"}], 0, 1),
])
def test_first_release_refuses_pointer_or_access_drift(versions, jobs, keys):
    with pytest.raises(ValueError):
        require_first_release(versions, jobs, keys)


def test_exact_single_staged_candidate_can_be_planned():
    require_first_release([{"version": "test-legacy-demo-r1", "status": "STAGED"}], 0, 0)


@pytest.mark.parametrize("kind", ["outside", "missing", "wrong_name"])
def test_bound_gate_path_cannot_escape_candidate_stage(tmp_path, kind):
    stage = tmp_path / "stage"; stage.mkdir()
    report = (tmp_path if kind == "outside" else stage) / (
        "other.json" if kind == "wrong_name" else "quality_test-legacy-demo-r1.json")
    if kind != "missing": report.write_text("{}", encoding="utf-8")
    with pytest.raises(ValueError):
        report_parent(stage, report)


def test_existing_gate_keeps_its_bound_directory(tmp_path):
    stage = tmp_path / "stage"; report = stage / "recheck/reports/quality_test-legacy-demo-r1.json"
    report.parent.mkdir(parents=True); report.write_text("{}", encoding="utf-8")
    assert report_parent(stage, report) == report.parent.resolve()
