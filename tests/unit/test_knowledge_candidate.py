"""Offline corpus candidate safety and evidence checks."""
import hashlib
import json
from pathlib import Path

import pytest

from scripts.build_knowledge_candidate import build, dedup_key
from scripts.calibrate_eval_set import SCOPE_FIELDS
from scripts.verify_knowledge_candidate import verify
from qa_core.indexing.chunkers.registry import chunk_sop


def fixture_corpus(tmp_path):
    raw = tmp_path / "old"
    raw.mkdir()
    meta, files = {}, []
    for name, body, dept in [
        ("sop_a.md", "# a\n步骤 1：检查\n步骤 2：停止", "public"),
        ("sop_b.md", "# b\n步骤 1：检查\n步骤 2：停止", "public"),
        ("sop_c.md", "# c\n步骤 1：检查\n步骤 2：停止", "secret"),
        ("manual_a.md", "# card\n项目自编", "public"),
    ]:
        data = body.encode()
        (raw / name).write_bytes(data)
        meta[name] = {**{k: "" for k in SCOPE_FIELDS}, "tenant_id": "default", "dept_id": dept, "source_version": "r3",
                      "origin_type": "public_reference_derived" if name.startswith("manual") else "synthetic",
                      "synthetic": False if name.startswith("manual") else True}
        files.append({"filename": name, "sha256": hashlib.sha256(data).hexdigest()})
    (raw / "meta.json").write_text(json.dumps(meta), encoding="utf-8")
    manifest = tmp_path / "manifest.json"
    manifest.write_text(json.dumps({"files": files}), encoding="utf-8")
    return raw, manifest


def test_dedup_keeps_security_domains_separate(tmp_path):
    raw, manifest = fixture_corpus(tmp_path)
    before = {p.name: p.read_bytes() for p in raw.iterdir()}
    output = tmp_path / "candidate"
    report = build(raw, manifest, output)
    assert report["retained_old_sop_files"] == 2
    assert report["removed_duplicate_copies_in_candidate"] == 1
    assert report["added_differentiated_sop_files"] == 12
    assert {p.name: p.read_bytes() for p in raw.iterdir()} == before
    assert not (output / "raw/sop_b.md").exists()
    assert (output / "raw/sop_c.md").exists()
    assert report["new_hit_at_5"] is None
    assert not report["index_changed"]
    meta = json.loads((output / "raw/meta.json").read_text(encoding="utf-8"))
    assert meta["manual_a.md"]["synthetic"] is True
    assert meta["manual_a.md"]["external_reference_support_verified"] is False


def test_generated_evidence_survives_existing_sop_chunker(tmp_path):
    raw, manifest = fixture_corpus(tmp_path)
    output = tmp_path / "candidate"
    build(raw, manifest, output)
    review = json.loads((output / "evaluation/review-cases.json").read_text(encoding="utf-8"))
    assert "cases" not in review
    assert not review["metrics_eligible"]
    assert len(review["review_cases"]) == 12
    assert len({c["id"] for c in review["review_cases"]}) == 12
    for case in review["review_cases"]:
        name = case["proposed_labels"]["expect_hit"]
        text = (output / "raw" / name).read_text(encoding="utf-8")
        chunks = chunk_sop(text, source=name)
        assert [c.metadata["step_index"] for c in chunks] == [[0], [1], [2], [3], [4]]
        for atom in case["required_evidence"]:
            assert atom["quote"] in text.splitlines()[atom["line"]-1]
            assert any(atom["quote"] in c.text for c in chunks)
        # Context must travel with every action; no heading-only eligibility assumed.
        for chunk in chunks[1:]:
            assert case["group_id"] in chunk.text
            assert "Lab-2026.09" in chunk.text


@pytest.mark.parametrize("field", ["tenant_id", "dept_id", "source_version", "effective_to", "allowed_roles", "vendor"])
def test_dedup_scope_fields(field):
    item = {"body_sha256": "same", "full_metadata": {}}
    other = {"body_sha256": "same", "full_metadata": {field: "different"}}
    assert dedup_key(item) != dedup_key(other)


def test_refs_are_only_collapsible_for_disclosed_synthetic_material():
    left = {"body_sha256": "same", "full_metadata": {"synthetic": True, "evidence_refs": ["a"]}}
    right = {"body_sha256": "same", "full_metadata": {"synthetic": True, "evidence_refs": ["b"]}}
    assert dedup_key(left) == dedup_key(right)
    left["full_metadata"]["synthetic"] = False
    right["full_metadata"]["synthetic"] = False
    assert dedup_key(left) != dedup_key(right)


def test_refuses_existing_output(tmp_path):
    raw, manifest = fixture_corpus(tmp_path)
    output = tmp_path / "exists"
    output.mkdir()
    with pytest.raises(ValueError, match="overwrite"):
        build(raw, manifest, output)


def test_refuses_output_inside_original(tmp_path):
    raw, manifest = fixture_corpus(tmp_path)
    with pytest.raises(ValueError, match="separate"):
        build(raw, manifest, raw / "new")


def test_rejects_bad_source_checksum_before_write(tmp_path):
    raw, manifest = fixture_corpus(tmp_path)
    (raw / "sop_a.md").write_text("tampered", encoding="utf-8")
    output = tmp_path / "candidate"
    with pytest.raises(ValueError, match="checksum"):
        build(raw, manifest, output)
    assert not output.exists()


def test_reproducible_outputs(tmp_path):
    raw, manifest = fixture_corpus(tmp_path)
    left, right = tmp_path / "left", tmp_path / "right"
    build(raw, manifest, left)
    build(raw, manifest, right)
    assert {str(p.relative_to(left)): p.read_bytes() for p in left.rglob("*") if p.is_file()} == {
        str(p.relative_to(right)): p.read_bytes() for p in right.rglob("*") if p.is_file()}


def test_offline_verification_and_chunk_count(tmp_path):
    raw, manifest = fixture_corpus(tmp_path)
    output = tmp_path / "candidate"
    build(raw, manifest, output)
    report = verify(output, raw)
    assert report["verified_quote_count"] == 96
    assert report["candidate_total_chunks"] > 60
    assert report["new_hit_at_5"] is None
    assert not report["retrieval_run"]


@pytest.mark.parametrize("tamper_original", [False, True])
def test_verifier_rejects_changed_artifacts(tmp_path, tamper_original):
    raw, manifest = fixture_corpus(tmp_path)
    output = tmp_path / "candidate"
    build(raw, manifest, output)
    root = raw if tamper_original else output / "raw"
    (root / "sop_a.md").write_text("changed", encoding="utf-8")
    with pytest.raises(ValueError, match="changed"):
        verify(output, raw)
