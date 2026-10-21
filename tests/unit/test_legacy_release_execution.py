import copy
import pytest
from scripts.execute_legacy_release import APPROVED_PLAN_SHA256, compare_plans, validate_plan


def test_wrong_approval_digest_refused_before_any_plan_read(tmp_path):
    with pytest.raises(ValueError, match="exact plan"):
        validate_plan(tmp_path / "nonexistent.json", "0" * 64)


def test_changed_approved_file_is_refused(tmp_path):
    path = tmp_path / "plan.json"; path.write_text('{}', encoding='utf-8')
    with pytest.raises(ValueError, match="drift"):
        validate_plan(path, APPROVED_PLAN_SHA256)


@pytest.mark.parametrize("field", ["database", "doc_collection", "faq_collection", "gate_report_sha256",
    "snapshot_sha256", "candidate_artifacts_sha256", "version", "active_before", "rollback_target",
    "content_limitation", "proposed_action", "scope"])
def test_preflight_cannot_drift_from_any_approved_boundary(field):
    approved = {"prepared_at_utc": "old", field: "approved-value"}
    fresh = copy.deepcopy(approved); fresh[field] = "changed-value"
    with pytest.raises(ValueError, match="differs"):
        compare_plans(approved, fresh)


def test_only_preflight_observation_time_can_change():
    compare_plans({"prepared_at_utc": "old", "version": "v"},
                  {"prepared_at_utc": "new", "version": "v"})


def test_preflight_cannot_omit_an_approved_constraint():
    with pytest.raises(ValueError):
        compare_plans({"version": "v", "constraint": True}, {"version": "v"})
