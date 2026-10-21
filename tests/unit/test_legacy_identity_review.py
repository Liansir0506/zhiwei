import copy
import hashlib
import json
import pytest
from qa_core.quality.review_contract import bind_pending
from scripts.build_legacy_identity_review import propose_identity, reconstruct, trace_rows, validate_package
from scripts.rebuild_kb import to_milvus_row


@pytest.fixture
def raw(tmp_path):
    path = tmp_path / "faq_sim.csv"
    path.write_text('source,question,answer\nFAQ-SIM-1,问？,模拟答案\n', encoding="utf-8")
    metadata = {path.name: {"tenant_id": "default", "visibility": "public", "allowed_roles": [], "dataset_id": "original"}}
    chunk = reconstruct(path, metadata[path.name])[0]
    row = to_milvus_row(chunk, "actual-row", [], "original")
    return tmp_path, metadata, row


def test_trace_requires_complete_faq_answer(raw):
    root, metadata, row = raw
    traces, _ = trace_rows(root, metadata, [("faq", row)])
    assert traces[0]["status"] == "exact_trace" and traces[0]["location"]["format"] == "csv"
    row["answer"] = "different answer"
    assert trace_rows(root, metadata, [("faq", row)])[0][0]["status"] == "unmatched"


def test_scope_mismatch_is_a_review_block(raw):
    root, metadata, row = raw
    row["tenant_id"] = "foreign"
    trace = trace_rows(root, metadata, [("faq", row)])[0][0]
    assert trace["status"] == "scope_conflict" and trace["scope_conflicts"] == ["tenant_id"]


def test_duplicate_row_in_multiple_files_never_chooses_a_filename(raw):
    root, metadata, row = raw
    (root / "faq_duplicate.csv").write_bytes((root / "faq_sim.csv").read_bytes())
    metadata["faq_duplicate.csv"] = copy.deepcopy(metadata["faq_sim.csv"])
    trace = trace_rows(root, metadata, [("faq", row)])[0][0]
    assert trace["status"] == "ambiguous" and trace["match_count"] == 2 and "filename" not in trace


def fixture_package(tmp_path):
    raw = tmp_path / "raw"; raw.mkdir()
    path = raw / "manual_example.md"; path.write_text("# Demo\nEvidence.", encoding="utf-8")
    original = {"tenant_id": "default", "allowed_roles": ["ops"], "visibility": "role_restricted"}
    entry = bind_pending({"filename": path.name, "sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
                         "tenant_id": "default", "source_system": "project", "proposed_source_id": "allocated-proposal",
                         "proposed_asset_id": "allocated-asset", "original_scope_metadata": original})
    meta = {path.name: {**original, "status": "draft", "review_status": "pending", "source_id": "", "asset_id": ""}}
    (raw / "meta.json").write_text(json.dumps(meta), encoding="utf-8")
    (tmp_path / "original-meta.json").write_text(json.dumps({path.name: original}), encoding="utf-8")
    mapped = bind_pending({k:entry[k] for k in ("filename", "sha256", "tenant_id", "source_system") } | {"source_id":entry["proposed_source_id"]})
    (tmp_path / "source-map.pending.json").write_text(json.dumps({"entries":[mapped]}), encoding="utf-8")
    (tmp_path / "source-proposals.json").write_text(json.dumps({"protocol":"legacy-identity-review-v2", "entries": [entry]}), encoding="utf-8")
    (tmp_path / "original-registrations.json").write_text("[]", encoding="utf-8")
    update_hashes(tmp_path)
    return tmp_path


def update_hashes(root):
    hashes = {str(p.relative_to(root)).replace("\\", "/"): hashlib.sha256(p.read_bytes()).hexdigest()
              for p in root.rglob("*") if p.is_file() and p.name != "artifact-hashes.json"}
    (root / "artifact-hashes.json").write_text(json.dumps(hashes), encoding="utf-8")


def test_prepared_package_validates_but_cannot_claim_approval(tmp_path):
    package = fixture_package(tmp_path)
    assert validate_package(package)["approved"] == 0
    with pytest.raises(ValueError, match="pending/rejected"):
        validate_package(package, require_approved=True)


def test_body_mutation_invalidates_review_even_if_outer_hashes_updated(tmp_path):
    package = fixture_package(tmp_path)
    (package / "raw/manual_example.md").write_text("Changed evidence.", encoding="utf-8")
    update_hashes(package)
    with pytest.raises(ValueError, match="source bytes"):
        validate_package(package)


def test_proposed_identity_mutation_invalidates_item_binding(tmp_path):
    package = fixture_package(tmp_path)
    path = package / "source-proposals.json"
    body = json.loads(path.read_text()); body["entries"][0]["proposed_source_id"] = "renamed"
    path.write_text(json.dumps(body), encoding="utf-8"); update_hashes(package)
    with pytest.raises(ValueError, match="review binding changed"):
        validate_package(package)


def test_candidate_acl_change_cannot_hide_behind_updated_outer_hashes(tmp_path):
    package = fixture_package(tmp_path)
    path = package / "raw/meta.json"
    meta = json.loads(path.read_text()); meta["manual_example.md"]["visibility"] = "public"
    path.write_text(json.dumps(meta), encoding="utf-8"); update_hashes(package)
    with pytest.raises(ValueError, match="changed access"):
        validate_package(package)


def test_mapping_identity_cannot_diverge_even_with_a_new_mapping_binding(tmp_path):
    package = fixture_package(tmp_path)
    path = package / "source-map.pending.json"
    mapping = json.loads(path.read_text()); item = mapping["entries"][0]
    item["source_id"] = "different-id"; bind_pending(item)
    path.write_text(json.dumps(mapping), encoding="utf-8"); update_hashes(package)
    with pytest.raises(ValueError, match="source map disagree"):
        validate_package(package)


def test_existing_declared_identity_is_preserved_without_claiming_approval():
    meta = {"tenant_id": "default", "source_system": "curated_reference_card", "source_version": "original"}
    registration = {**meta, "filename": "manual_example.md", "checksum": "body-hash", "source_id": "shared-reference",
                    "asset_id": "existing-asset", "reviewed_by": "sim-user-quality-gate"}
    source, asset, basis = propose_identity("manual_example.md", "body-hash", meta, registration)
    assert (source, asset) == ("shared-reference", "existing-asset") and "pending" in basis


@pytest.mark.parametrize("field", ["tenant_id", "source_system", "source_version", "checksum"])
def test_inconsistent_existing_registration_requires_review(field):
    meta = {"tenant_id": "default", "source_system": "curated_reference_card", "source_version": "original"}
    registration = {**meta, "filename": "manual_example.md", "checksum": "body-hash", "source_id": "registered",
                    "asset_id": "existing-asset"}
    registration[field] = "different"
    with pytest.raises(ValueError, match="disagrees"):
        propose_identity("manual_example.md", "body-hash", meta, registration)
