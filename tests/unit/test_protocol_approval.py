import json
from pathlib import Path

import pytest

from scripts.prepare_approved_protocol import prepare
from scripts.rebuild_kb import resolve_doc_type
from qa_core.quality.eval_review import validate_export

ROOT = Path(__file__).resolve().parents[2]


def test_receipt_creates_separate_bound_approved_package(tmp_path):
    result = prepare(ROOT / "data/candidates/stage4-protocol-notes-r1",
        ROOT / "docs/STAGE4_PROTOCOL_APPROVAL_2026-09-27.json", tmp_path / "approved", "fixture-version")
    assert result["approved_cases"] == 8 and result["published"] is False
    data = json.loads((tmp_path / "approved/eval-dev.json").read_text(encoding="utf-8"))
    assert len(validate_export(data)) == 4
    assert all(s["approval"]["reviewer"] == "user_in_current_chat" for s in data["sources"])
    meta = json.loads((tmp_path / "approved/raw/meta.json").read_text(encoding="utf-8"))
    from qa_core.domain.models import KnowledgeMetadata
    for item in meta.values():
        assert item["effective_from"] is None and item["effective_to"] is None
        KnowledgeMetadata.model_validate({key:value for key,value in item.items() if key in KnowledgeMetadata.model_fields})


def test_receipt_digest_drift_rejected_before_write(tmp_path):
    receipt = json.loads((ROOT / "docs/STAGE4_PROTOCOL_APPROVAL_2026-09-27.json").read_text(encoding="utf-8"))
    receipt["package_sha256"] = "wrong"
    path = tmp_path / "receipt.json"; path.write_text(json.dumps(receipt), encoding="utf-8")
    with pytest.raises(ValueError, match="receipt"):
        prepare(ROOT / "data/candidates/stage4-protocol-notes-r1", path, tmp_path / "output", "fixture")
    assert not (tmp_path / "output").exists()


def test_unknown_approved_filename_requires_strict_explicit_type():
    with pytest.raises(ValueError): resolve_doc_type("http_503_note.md", {"doc_type": "manual"})
    assert resolve_doc_type("http_503_note.md", {"doc_type": "manual"}, strict_metadata=True) == "manual"


@pytest.mark.parametrize("name,kind", [("manual_a.md", "sop"), ("note.md", "invalid")])
def test_bad_or_conflicting_explicit_type_rejected(name, kind):
    with pytest.raises(ValueError): resolve_doc_type(name, {"doc_type": kind}, strict_metadata=True)
