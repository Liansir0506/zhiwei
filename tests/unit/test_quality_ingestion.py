import pytest

from qa_core.ingest.jobs import IngestJobDao
from scripts.ingest_quality_candidate import asset_id
from scripts import rebuild_kb


@pytest.fixture(autouse=True)
def isolate_pipeline_guard(monkeypatch):
    # These tests inspect parsing/schema, not SQL publication locking.
    from contextlib import nullcontext
    from qa_core.ingest import write_guard
    monkeypatch.setattr(write_guard, "candidate_write", lambda *a, **kw: nullcontext())


class Dao:
    def __init__(self):
        self.statuses = []
        self.assets = []

    def file_done(self, *args):
        return False

    def upsert_record(self, *args, **kwargs):
        self.assets.append(kwargs["asset_id"])

    def set_status(self, *args, **kwargs):
        self.statuses.append(args[2])


class Embedder:
    def embed_passages(self, texts):
        return [[1.0, 0.0] for t in texts]


class Client:
    def __init__(self):
        self.rows = []

    def upsert(self, **kwargs):
        self.rows.extend(kwargs["data"])

    def flush(self, *args):
        pass


def test_asset_identity_is_version_and_source_scoped():
    assert asset_id("old", "a", "hash") != asset_id("new", "a", "hash")
    assert asset_id("new", "a", "hash") != asset_id("new", "b", "hash")


@pytest.mark.parametrize("key", ["", "x" * 65])
def test_rejects_invalid_idempotency_key_before_database_access(key):
    dao = object.__new__(IngestJobDao)
    with pytest.raises(ValueError, match="idempotency_key"):
        dao.create(job_type="rebuild", tenant_id="default", actor="test", kb_version="v",
                   idempotency_key=key)


def test_strict_metadata_replaces_empty_chunk_defaults_and_keeps_scope(tmp_path, monkeypatch):
    client, dao = Client(), Dao()
    monkeypatch.setattr(rebuild_kb, "_collection_field_names", lambda *args: None)
    stats = {"done": 0, "failed": 0, "skipped": 0, "duplicates": 0}
    seen = set()
    # Same body/source title, but access domains differ: both must survive.
    for dept in ("a", "b"):
        path = tmp_path / f"manual_{dept}.md"
        path.write_text("# same\n同一正文", encoding="utf-8")
        rebuild_kb.process_file(client, dao, Embedder(), path, "v-candidate", seen, stats,
            extra_meta={"dept_id": dept, "equipment_type": "UPF", "asset_id": f"asset-{dept}",
                        "security_level": "internal", "visibility": "department_restricted"},
            strict_metadata=True)
    assert stats["failed"] == 0
    assert len(client.rows) == 2
    assert {r["dept_id"] for r in client.rows} == {"a", "b"}
    assert all(r["equipment_type"] == "UPF" for r in client.rows)
    assert set(dao.assets) == {"asset-a", "asset-b"}


def test_strict_worker_rejects_wrong_collection_before_job_access():
    from scripts.ingest_worker import execute_job
    with pytest.raises(ValueError, match="collection binding"):
        execute_job({"payload_json": {"strict_metadata": True,
                    "doc_collection": "wrong", "faq_collection": "wrong"}}, None, "test")


def test_evidence_atom_probe_does_not_confuse_heading_with_steps():
    from scripts.compare_quality_retrieval import required_atoms_present
    case = {"evidence_requirements": [
        {"required": True, "any_of": [{"quote": "步骤 1：检查"}]},
        {"required": True, "any_of": [{"quote": "停止并转人工"}]},
        {"required": False, "any_of": [{"quote": "optional"}]}]}
    assert required_atoms_present(case, ["SOP 标题"]) == (0, 2)
    assert required_atoms_present(case, ["步骤1:检查；停止并转人工"]) == (2, 2)


def test_strict_schema_failure_occurs_before_any_database_write(monkeypatch):
    import pymilvus
    closed = []
    class IncompleteClient:
        def __init__(self, **kwargs): pass
        def describe_collection(self, **kwargs):
            return {"fields": [{"name": "text"}]}
        def close(self): closed.append(True)
    monkeypatch.setattr(pymilvus, "MilvusClient", IncompleteClient)
    monkeypatch.setattr(rebuild_kb, "KbDao", lambda *args: pytest.fail("must not write SQL"))
    monkeypatch.setattr(rebuild_kb, "get_embedder", lambda *args: pytest.fail("must not load model"))
    with pytest.raises(ValueError, match="schema incomplete"):
        rebuild_kb.run_rebuild("v-candidate", strict_metadata=True)
    assert closed == [True]


def test_visibility_schema_can_store_every_policy_value():
    from scripts.init_collections import _common_fields
    fields = {}
    class Schema:
        def add_field(self, name, *args, **kwargs): fields[name] = kwargs
        def add_function(self, *args): pass
    _common_fields(Schema())
    for value in ("public", "role_restricted", "department_restricted"):
        assert len(value.encode()) <= fields["visibility"]["max_length"]
