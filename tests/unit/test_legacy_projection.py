"""Synthetic approval fixtures exercise the projector; no real corpus approvals."""
import hashlib

import pytest

from qa_core.quality.review_contract import bind_pending, review_digest
from scripts.prepare_legacy_projection import (
    load_plan, prepare_review, project, read, seal, validate_projection, write,
)


@pytest.fixture
def review(tmp_path):
    package = tmp_path / "package"; package.mkdir()
    raw = package / "raw"; raw.mkdir()
    sources, mappings, meta, original, registrations, audit = [], [], {}, {}, [], []
    for name, kind in (("manual_example.md", "manual"), ("manual_peer.md", "manual"), ("faq_sim.csv", "faq")):
        (raw / name).write_text("<script>unsafe</script> " + name, encoding="utf-8")
        scope = {"tenant_id": "default", "dept_id": "public", "source_system": "project",
                 "source_version": "original", "visibility": "role_restricted", "allowed_roles": ["ops"],
                 "role_scope": ["ops"], "security_level": "internal", "synthetic": kind == "faq",
                 "license_status": "synthetic" if kind == "faq" else "link_only_no_redistribution"}
        checksum = hashlib.sha256((raw / name).read_bytes()).hexdigest()
        sid, asset = ("shared-manual" if kind == "manual" else "simulated-faq"), "asset-" + name
        registration = {"filename": name, "checksum": checksum, "asset_id": asset,
                        "source_id": sid, **{k: scope[k] for k in ("tenant_id", "source_system", "source_version")}}
        source = bind_pending({"filename": name, "sha256": checksum, "tenant_id": "default",
                    "source_system": "project", "proposed_source_id": sid, "proposed_asset_id": asset,
                    "original_scope_metadata": scope, "existing_registration": registration if kind == "manual" else None,
                    "legacy_ledger": {"doc_type": kind}, "parsed_chunks": 1, "exact_traced_rows": 1})
        sources.append(source)
        mappings.append(bind_pending({"filename": name, "sha256": checksum, "tenant_id": "default",
                                      "source_system": "project", "source_id": sid}))
        original[name] = scope
        meta[name] = {**scope, "status": "draft", "review_status": "pending", "source_id": "", "asset_id": ""}
        if kind == "manual":
            registrations.append(registration)
            audit.append({"filename": name, "domain": {**registration, "dept_id": None,
                         **{k:scope[k] for k in ("security_level", "visibility", "role_scope")}},
                         "differences": {"dept_id": {"domain": None, "raw": "public"}}})
    for name, value in {"raw/meta.json": meta, "original-meta.json": original,
                        "source-proposals.json": {"protocol": "legacy-identity-review-v2", "entries": sources},
                        "source-map.pending.json": {"entries": mappings},
                        "original-registrations.json": registrations,
                        "review-summary.json": {"shared_existing_source_ids": [{"tenant_id": "default",
                              "source_system": "project", "source_id": "shared-manual",
                              "filenames": ["manual_example.md", "manual_peer.md"]}]}}.items():
        write(package / name, value)
    seal(package)
    audit_path = tmp_path / "audit.json"; write(audit_path, {"read_only": True, "rows": audit})
    destination = tmp_path / "review"
    plan, template = prepare_review(package, audit_path, destination)
    receipt = tmp_path / "receipt.json"
    write(receipt, template)
    return package, destination, receipt, tmp_path / "projection", plan


def fixture_approve(receipt_path, **changes):
    item = read(receipt_path)
    item.update({"approved_filenames": ["faq_sim.csv"], "user_message": "SYNTHETIC UNIT TEST approval", **changes})
    item["approval"].update(status="approved", proposer="fixture-proposer", reviewer="fixture-reviewer",
                            evidence="SYNTHETIC UNIT TEST ONLY")
    item["approval"]["binding_sha256"] = review_digest(item)
    write(receipt_path, item)


def test_group_review_preserves_pending_and_escapes_full_body(review):
    package, folder, receipt, output, plan = review
    assert plan["human_approved_sources"] == 0 and plan["recommended_filenames"] == ["faq_sim.csv"]
    assert len(plan["department_conflicts"]) == 2
    rendered = (folder / "review.html").read_text(encoding="utf-8")
    assert "&lt;script&gt;unsafe&lt;/script&gt;" in rendered and "<script>" not in rendered
    assert read(receipt)["approval"]["status"] == "pending"
    assert load_plan(package, folder) == plan


def test_pending_rejected_before_creating_output(review):
    package, folder, receipt, output, _ = review
    with pytest.raises(ValueError, match="pending/rejected"):
        project(package, folder, receipt, output)
    assert not output.exists()


def test_partial_projection_preserves_acl_and_original_package(review):
    package, folder, receipt, output, _ = review
    before = (package / "artifact-hashes.json").read_bytes()
    fixture_approve(receipt)
    result = project(package, folder, receipt, output)
    assert result["approved_files"] == 1 and result["pending_files"] == 2 and result["db_writes"] == 0
    meta = read(output / "raw/meta.json")
    assert set(meta) == {"faq_sim.csv"}
    assert meta["faq_sim.csv"]["allowed_roles"] == ["ops"]
    assert meta["faq_sim.csv"]["synthetic"] is True and meta["faq_sim.csv"]["source_version"] == "original"
    assert (output / "raw/faq_sim.csv").read_bytes() == (package / "raw/faq_sim.csv").read_bytes()
    assert (package / "artifact-hashes.json").read_bytes() == before
    assert read(output / "candidate-intent.json")["database_version_created"] is False
    assert validate_projection(package, folder, output)["approved_files"] == 1


@pytest.mark.parametrize("changes,match", [
    ({"scope": "production"}, "isolated human"),
    ({"target_environment": "dev"}, "isolated human"),
    ({"user_message": ""}, "isolated human"),
    ({"plan_sha256": "changed"}, "isolated human"),
    ({"candidate_version": "v4-foundation-r3"}, "isolated candidate"),
    ({"approved_filenames": []}, "source selection"),
    ({"approved_filenames": ["faq_sim.csv", "faq_sim.csv"]}, "source selection"),
    ({"approved_filenames": ["../foreign.csv"]}, "source selection"),
    ({"approved_filenames": ["manual_example.md"]}, "department conflicts"),
    ({"acknowledged_withdrawal_groups": ["unknown"]}, "withdrawal group"),
])
def test_invalid_receipts_fail_without_writes(review, changes, match):
    package, folder, receipt, output, _ = review
    fixture_approve(receipt, **changes)
    with pytest.raises(ValueError, match=match):
        project(package, folder, receipt, output)
    assert not output.exists()


def test_self_approval_is_rejected(review):
    package, folder, receipt, output, _ = review
    fixture_approve(receipt)
    body = read(receipt); body["approval"]["reviewer"] = "fixture-proposer"; write(receipt, body)
    with pytest.raises(ValueError, match="independent reviewer"):
        project(package, folder, receipt, output)


def test_shared_source_requires_ack_and_does_not_approve_peer(review):
    package, folder, receipt, output, plan = review
    fixture_approve(receipt, approved_filenames=["manual_example.md"], department_resolution="use_domain_empty")
    with pytest.raises(ValueError, match="explicitly acknowledged"):
        project(package, folder, receipt, output)
    fixture_approve(receipt, approved_filenames=["manual_example.md"], department_resolution="use_domain_empty",
                    acknowledged_withdrawal_groups=[plan["shared_withdrawal_groups"][0]["group_binding_sha256"]])
    project(package, folder, receipt, output)
    meta = read(output / "raw/meta.json")
    assert set(meta) == {"manual_example.md"} and meta["manual_example.md"]["dept_id"] == ""
    assert meta["manual_example.md"]["source_id"] == "shared-manual"


def test_changed_source_and_changed_receipt_require_new_review(review):
    package, folder, receipt, output, _ = review
    fixture_approve(receipt)
    body = read(receipt); body["approved_filenames"] = ["manual_example.md"]; write(receipt, body)
    with pytest.raises(ValueError, match="binding changed"):
        project(package, folder, receipt, output)
    fixture_approve(receipt)
    (package / "raw/faq_sim.csv").write_text("changed", encoding="utf-8")
    with pytest.raises(ValueError, match="content changed"):
        project(package, folder, receipt, output)


def test_resealed_plan_cannot_change_acl(review):
    package, folder, receipt, output, _ = review
    plan = read(folder / "review-plan.json"); plan["entries"][0]["scope_metadata"]["visibility"] = "public"
    write(folder / "review-plan.json", plan); seal(folder)
    with pytest.raises(ValueError, match="frozen inputs"):
        load_plan(package, folder)


def test_refuse_existing_or_overlapping_projection_output(review):
    package, folder, receipt, output, _ = review
    fixture_approve(receipt)
    for target in (package / "nested", folder / "nested", package.parent):
        with pytest.raises(ValueError, match="new and separate"):
            project(package, folder, receipt, target)


def test_domain_permission_changes_cannot_be_hidden_in_a_resealed_review(review):
    package, folder, _, _, _ = review
    audit = read(folder / "domain-permission-audit.json")
    audit["rows"][0]["domain"]["visibility"] = "public"
    audit["rows"][0]["differences"]["visibility"] = {"domain": "public", "raw": "role_restricted"}
    write(folder / "domain-permission-audit.json", audit); seal(folder)
    with pytest.raises(ValueError, match="non-department access conflict"):
        load_plan(package, folder)


def test_explicit_raw_department_choice_preserves_public_declaration(review):
    package, folder, receipt, output, plan = review
    fixture_approve(receipt, approved_filenames=["manual_example.md"], department_resolution="use_raw_public",
                    acknowledged_withdrawal_groups=[plan["shared_withdrawal_groups"][0]["group_binding_sha256"]])
    project(package, folder, receipt, output)
    assert read(output / "raw/meta.json")["manual_example.md"]["dept_id"] == "public"


def test_resealed_candidate_cannot_expand_approved_scope(review):
    package, folder, receipt, output, _ = review
    fixture_approve(receipt); project(package, folder, receipt, output)
    metadata = read(output / "raw/meta.json"); metadata["faq_sim.csv"]["allowed_roles"] = []
    write(output / "raw/meta.json", metadata); seal(output)
    with pytest.raises(ValueError, match="explicit human approval"):
        validate_projection(package, folder, output)
