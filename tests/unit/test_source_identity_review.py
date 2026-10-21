import hashlib
from types import SimpleNamespace
from unittest.mock import Mock

import pytest
import sqlalchemy as sa
from qa_core.ingest.source_identity import validate_mapping, verify_upload_replacement
from qa_core.storage.kb_dao import KbDao


@pytest.fixture
def mapping(tmp_path):
    (tmp_path / "manual.md").write_text("specific source body", encoding="utf-8")
    entry = {"filename": "manual.md", "sha256": hashlib.sha256((tmp_path / "manual.md").read_bytes()).hexdigest(),
             "tenant_id": "tenant-a", "source_system": "wiki", "source_id": "page-123",
             "approval": {"status": "approved", "proposer": "editor", "reviewer": "reviewer", "evidence": "verified wiki page ID"}}
    meta = {"manual.md": {"tenant_id": "tenant-a", "allowed_roles": ["ops"], "source_id": ""}}
    return tmp_path, {"protocol": "reviewed-source-map-v1", "entries": [entry]}, meta


def test_reviewed_mapping_keeps_acl(mapping):
    root, manifest, meta = mapping
    result = validate_mapping(root, manifest, meta)
    assert result["manual.md"]["source_id"] == "page-123"
    assert result["manual.md"]["allowed_roles"] == ["ops"]
    assert meta["manual.md"]["source_id"] == ""


@pytest.mark.parametrize("change", ["body", "pending", "self_review", "tenant", "rename", "path", "partial"])
def test_mapping_fails_closed(mapping, change):
    root, manifest, meta = mapping
    entry = manifest["entries"][0]
    if change == "body": (root / "manual.md").write_text("changed", encoding="utf-8")
    if change == "pending": entry["approval"]["status"] = "pending"
    if change == "self_review": entry["approval"]["reviewer"] = "editor"
    if change == "tenant": entry["tenant_id"] = "other"
    if change == "rename": meta["manual.md"]["source_id"] = "previous"
    if change == "path": entry["filename"] = "../manual.md"
    if change == "partial": meta["other.md"] = {"tenant_id": "tenant-a"}
    with pytest.raises(ValueError): validate_mapping(root, manifest, meta)


@pytest.mark.parametrize("asset,status", [("other", "done"), ("other", "pending"), (None, "done")])
def test_same_hash_other_asset_is_not_idempotent(asset, status):
    dao = object.__new__(KbDao)
    dao.engine = sa.create_engine("sqlite://")
    with dao.engine.begin() as c:
        c.execute(sa.text("CREATE TABLE ingest_records(file_hash TEXT,kb_version TEXT,asset_id TEXT,status TEXT)"))
        c.execute(sa.text("INSERT INTO ingest_records VALUES('h','v',:a,:s)"), {"a": asset, "s": status})
    try:
        with pytest.raises(ValueError, match="其他或未知资产"): dao.file_done("h", "v", "expected")
    finally: dao.engine.dispose()


@pytest.mark.parametrize("rows", [([], []), ([{"id": "x"}], [{"id": "foreign"}])])
def test_foreign_or_unknown_replacement_denied(rows):
    client = Mock()
    client.describe_collection.return_value = {"fields": [{"name": n} for n in ("asset_id", "tenant_id", "source_system", "source_id")]}
    client.query.side_effect = rows
    with pytest.raises(ValueError):
        verify_upload_replacement(client, [{"asset_id": "a", "doc_type": "manual"}],
                                  SimpleNamespace(doc_collection="doc", faq_collection="faq"), "v", "tenant", "stable")
    client.delete.assert_not_called()


def test_same_source_replacement_is_allowed():
    client = Mock()
    client.describe_collection.return_value = {"fields": [{"name": n} for n in ("asset_id", "tenant_id", "source_system", "source_id")]}
    client.query.side_effect = [[{"id": "own"}], []]
    verify_upload_replacement(client, [{"asset_id": "a", "doc_type": "manual"}],
                              SimpleNamespace(doc_collection="doc", faq_collection="faq"), "v", "tenant", "stable")
    assert client.query.call_count == 2


def test_inventory_never_approves_or_guesses_identity(tmp_path):
    import json
    from scripts.review_source_identity import inventory
    (tmp_path / "manual.md").write_text("body", encoding="utf-8")
    (tmp_path / "meta.json").write_text(json.dumps({"manual.md": {"tenant_id": "tenant"}}), encoding="utf-8")
    entry = inventory(tmp_path)["entries"][0]
    assert entry["source_id"] == entry["source_system"] == ""
    assert entry["approval"]["status"] == "pending"
