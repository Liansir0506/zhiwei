import json

from scripts.generate_enterprise_dataset import generate
from scripts.validate_enterprise_dataset import validate


def test_enterprise_dataset_is_reproducible_and_passes_governance(tmp_path):
    first = tmp_path / "first"
    second = tmp_path / "second"
    generate(first, "v4-test", 20260919, None)
    generate(second, "v4-test", 20260919, None)

    first_manifest = json.loads(
        (first / "manifests" / "v4-test" / "dataset_manifest.json").read_text(encoding="utf-8")
    )
    second_manifest = json.loads(
        (second / "manifests" / "v4-test" / "dataset_manifest.json").read_text(encoding="utf-8")
    )
    assert first_manifest == second_manifest

    report = validate(first, "v4-test", None)
    assert report["passed"] is True
    assert 5000 <= report["counts"]["predicted_chunks"] <= 15000
    assert report["counts"]["exact_duplicate_chunks"] == 0
    assert report["counts"]["raw_files"] == 276
    snapshot = json.loads(
        (first / "datasets" / "v4-test" / "enterprise_snapshot.json").read_text(encoding="utf-8")
    )
    user_ids = {row["user_id"] for row in snapshot["users"]}
    assert {row["reviewed_by"] for row in snapshot["knowledge_assets"]} <= user_ids
