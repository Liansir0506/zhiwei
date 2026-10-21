import json
from copy import deepcopy
from pathlib import Path

import pytest

from scripts.validate_holdout_source_review import (
    DEFAULT_PACKAGE, DEFAULT_RECEIPT, validate_content_receipt, validate_package,
)


def test_pending_source_packet_is_valid():
    result = validate_package(DEFAULT_PACKAGE)
    assert result["cases"] == 0
    assert len(result["pending_sources"]) == 2


def test_content_receipt_is_bound_but_source_stays_pending():
    result = validate_content_receipt(DEFAULT_PACKAGE, DEFAULT_RECEIPT)
    assert result["decision"] == "content_accepted_only"
    assert result["source_approval"] == "pending"


def test_content_receipt_rejects_stale_package_hash(tmp_path):
    altered = json.loads(DEFAULT_RECEIPT.read_text(encoding="utf-8"))
    altered["package_sha256"] = "0" * 64
    receipt = tmp_path / "receipt.json"
    receipt.write_text(json.dumps(altered), encoding="utf-8")
    with pytest.raises(ValueError, match="package hash changed"):
        validate_content_receipt(DEFAULT_PACKAGE, receipt)


def test_changed_body_is_rejected(tmp_path):
    package = tmp_path / "packet"
    (package / "raw").mkdir(parents=True)
    (package / "source-review.json").write_bytes((DEFAULT_PACKAGE / "source-review.json").read_bytes())
    for name in ("bfd_detection_note.md", "netconf_lock_candidate_note.md"):
        (package / "raw" / name).write_bytes((DEFAULT_PACKAGE / "raw" / name).read_bytes())
    (package / "raw" / "bfd_detection_note.md").write_bytes(b"changed")
    with pytest.raises(ValueError, match="source body changed"):
        validate_package(package)


def test_cases_or_approval_cannot_be_added(tmp_path):
    payload = json.loads((DEFAULT_PACKAGE / "source-review.json").read_text(encoding="utf-8"))
    for change in ("cases", "approval"):
        altered = deepcopy(payload)
        if change == "cases":
            altered["cases"] = []
        else:
            altered["sources"][0]["approval"]["status"] = "approved"
        package = tmp_path / change
        (package / "raw").mkdir(parents=True)
        for name in ("bfd_detection_note.md", "netconf_lock_candidate_note.md"):
            (package / "raw" / name).write_bytes((DEFAULT_PACKAGE / "raw" / name).read_bytes())
        (package / "source-review.json").write_text(json.dumps(altered), encoding="utf-8")
        with pytest.raises(ValueError):
            validate_package(package)
