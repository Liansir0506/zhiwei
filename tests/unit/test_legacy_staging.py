"""Prevent shared/original writes and reject altered staged rows."""
import copy
from types import SimpleNamespace

import pytest

from scripts.stage_legacy_candidate import prepare_chunks, validate_base, validate_rows, validate_target


def profile(database="rag_kb_test"):
    return SimpleNamespace(app_env="test", mysql_dsn=f"mysql+pymysql://rag:pass@127.0.0.1:13306/{database}",
        milvus_uri="http://127.0.0.1:29530", redis_url="redis://127.0.0.1:16379/15",
        doc_collection="test_doc_collection" if database == "rag_kb_test" else database + "_doc",
        faq_collection="test_faq_collection" if database == "rag_kb_test" else database + "_faq", data_dir=".runtime/test/data",
        reports_dir=".runtime/test/reports", auth_required=True)


@pytest.mark.parametrize("field,value", [
    ("app_env", "dev"), ("mysql_dsn", "mysql+pymysql://rag:pass@127.0.0.1:3306/rag_kb"),
    ("milvus_uri", "http://127.0.0.1:19530"), ("doc_collection", "doc_collection"),
    ("redis_url", "redis://127.0.0.1:16379/0"), ("data_dir", "data"),
])
def test_base_profile_cannot_target_original_storage(field, value):
    settings = profile(); setattr(settings, field, value)
    with pytest.raises(ValueError, match="shared local"):
        validate_base(settings)


def test_only_exact_fresh_test_target_is_accepted():
    name = "test_legacy_stage_123456abcdef"
    validate_base(profile())
    validate_target(profile(name), name)
    with pytest.raises(ValueError, match="separate test"):
        validate_target(profile(), "rag_kb_test")


@pytest.mark.parametrize("field,value", [
    ("app_env", "dev"), ("doc_collection", "test_doc_collection"),
    ("mysql_dsn", "mysql+pymysql://rag:pass@127.0.0.1:13306/rag_kb_test"),
    ("auth_required", False), ("faq_collection", "faq_collection"), ("reports_dir", "reports"),
])
def test_target_profile_must_keep_all_isolation_boundaries(field, value):
    name = "test_legacy_stage_123456abcdef"
    settings = profile(name); setattr(settings, field, value)
    with pytest.raises(ValueError, match="separate test"):
        validate_target(settings, name)


@pytest.mark.parametrize("change", ["extra_row", "missing_row", "acl", "answer", "bad_dim", "nan", "unnormalized"])
def test_post_ingestion_drift_is_rejected(change):
    expected = {"row": {"id": "row", "text": "Question?", "answer": "Original answer", "allowed_roles": ["ops"], "dense": []}}
    row = {**copy.deepcopy(expected["row"]), "dense": [1.0] + [0.0] * 1023}
    validate_rows([row], expected)
    rows = [row]
    if change == "extra_row": rows.append({**row, "id": "unapproved"})
    if change == "missing_row": rows = []
    if change == "acl": row["allowed_roles"] = []
    if change == "answer": row["answer"] = "Changed answer"
    if change == "bad_dim": row["dense"] = [1.0]
    if change == "nan": row["dense"][0] = float("nan")
    if change == "unnormalized": row["dense"][0] = 2.0
    with pytest.raises(ValueError):
        validate_rows(rows, expected)


def test_preflight_keeps_original_revision_and_strict_access_metadata(tmp_path):
    path = tmp_path / "faq_example.csv"
    path.write_text("question,answer,source\nQuestion?,Original answer,FAQ-1\n", encoding="utf-8")
    tokenizer = SimpleNamespace(encode=lambda text, **kwargs: list(text))
    meta = {"doc_type": "faq", "asset_id": "approved-asset", "source_version": "original-revision",
            "tenant_id": "approved-tenant", "allowed_roles": ["ops"], "review_status": "human_chat_approved"}
    chunks = prepare_chunks(path, meta, tokenizer)
    assert chunks[0].metadata["source_version"] == "original-revision"
    assert chunks[0].metadata["tenant_id"] == "approved-tenant"
    assert chunks[0].metadata["answer"] == "Original answer"


@pytest.mark.parametrize("row_tech,expected", [("4G", "4G"), ("承载", "承载"), ("", "5G")])
def test_csv_network_label_uses_record_semantics_with_file_fallback(tmp_path, row_tech, expected):
    path = tmp_path / "faq_network.csv"
    path.write_text(f"question,answer,source,network_tech\nQuestion?,Original answer,FAQ-1,{row_tech}\n", encoding="utf-8")
    tokenizer = SimpleNamespace(encode=lambda text, **kwargs: list(text))
    meta = {"doc_type": "faq", "asset_id": "approved-asset", "source_version": "original-revision",
            "tenant_id": "approved-tenant", "dept_id": "reviewed-dept", "allowed_roles": ["ops"],
            "network_tech": "5G", "security_level": "internal", "visibility": "role_restricted"}
    chunk = prepare_chunks(path, meta, tokenizer)[0]
    assert chunk.metadata["network_tech"] == expected
    assert chunk.metadata["tenant_id"] == "approved-tenant"
    assert chunk.metadata["dept_id"] == "reviewed-dept" and chunk.metadata["allowed_roles"] == ["ops"]
