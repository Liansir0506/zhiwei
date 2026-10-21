import copy
import pytest
from qa_core.quality.review_contract import bind_pending, check_approval, audit_groups


def case(identifier, source, topic, template, exposed=False, split="dev"):
    return bind_pending({"id": identifier, "source_ids": [source], "body_families": [source + "-body"],
        "topic_groups": [topic], "template_groups": [template], "development_exposed": exposed, "split": split})


def test_pending_binding_valid_but_not_approved():
    item = case("a", "s", "t", "q")
    check_approval(item)
    with pytest.raises(ValueError, match="pending"): check_approval(item, required=True)


def test_label_change_invalidates_review():
    item = case("a", "s", "t", "q")
    item["source_ids"] = ["other"]
    with pytest.raises(ValueError, match="binding"): check_approval(item)


@pytest.mark.parametrize("problem", ["self", "missing_evidence"])
def test_approval_requires_independent_review_fields(problem):
    item = case("a", "s", "t", "q")
    item["approval"].update(status="approved", proposer="test-editor", reviewer="test-reviewer", evidence="fixture only")
    if problem == "self": item["approval"]["reviewer"] = "test-editor"
    if problem == "missing_evidence": item["approval"]["evidence"] = ""
    with pytest.raises(ValueError): check_approval(item, required=True)


def test_transitive_links_join_sources_topics_and_templates():
    cases = [case("a", "s1", "t1", "q1"), case("b", "s1", "t2", "q2"), case("c", "s3", "t3", "q2")]
    assert audit_groups(cases)[0]["case_ids"] == ["a", "b", "c"]


def test_connected_group_cannot_cross_splits():
    with pytest.raises(ValueError, match="crosses"):
        audit_groups([case("a", "s1", "t", "q1"), case("b", "s2", "t", "q2", split="frozen")])


def test_exposure_propagates_to_unexposed_neighbor():
    with pytest.raises(ValueError, match="exposed"):
        audit_groups([case("a", "s1", "t", "q1", exposed=True, split="frozen"),
                      case("b", "s2", "t", "q2", split="frozen")])


def test_undeclared_group_links_are_rejected():
    item = case("a", "s", "t", "q")
    item["template_groups"] = []
    bind_pending(item)
    with pytest.raises(ValueError, match="declared"): audit_groups([item])


def test_approved_fixture_binds_payload_but_does_not_authenticate_people():
    item = case("a", "s", "t", "q")
    item["approval"].update(status="approved", proposer="fixture-editor", reviewer="fixture-reviewer", evidence="fixture-only approval")
    check_approval(item, required=True)
    altered = copy.deepcopy(item); altered["split"] = "frozen"
    with pytest.raises(ValueError, match="binding"): check_approval(altered, required=True)


@pytest.fixture
def package(tmp_path, monkeypatch):
    import json
    from scripts import build_core_source_review as script
    source = bind_pending({"filename": "manual.md", "proposed_source_id": "logical", "sha256": "hash"})
    label = case("a", "logical", "topic", "template", exposed=True)
    payload = {"source-proposals.json": {"entries": [source]}, "label-workbook.json": {"cases": [label]}}
    monkeypatch.setattr(script, "build", lambda *a: copy.deepcopy(payload))
    for name, value in payload.items(): (tmp_path / name).write_text(json.dumps(value))
    return tmp_path, script


def test_package_readonly_validation_does_not_promote_pending(package):
    root, script = package
    assert script.validate_package("fixture", root)["approved_cases"] == 0
    with pytest.raises(ValueError, match="pending"):
        script.validate_package("fixture", root, require_approved=True)


@pytest.mark.parametrize("name,field", [("source-proposals.json", "proposed_source_id"), ("label-workbook.json", "split")])
def test_edited_proposals_require_new_package_even_with_rebound_digest(package, name, field):
    import json
    root, script = package
    value = json.loads((root / name).read_text())
    item = value["entries" if name.startswith("source") else "cases"][0]
    item[field] = "changed"
    bind_pending(item)
    (root / name).write_text(json.dumps(value))
    with pytest.raises(ValueError, match="proposal changed"):
        script.validate_package("fixture", root)
