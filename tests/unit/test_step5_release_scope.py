import copy
import hashlib
from types import SimpleNamespace

import pytest

from qa_core.quality.protocol_gate import PACKAGE, approved_sources, validate_rows, validate_scope
from scripts.rebuild_kb import TEXT_CHUNKERS, _resanitize, normalize_text
from qa_core.indexing.parsers.parsers import parse_dispatch
from scripts.migrate_legacy_collections import adapt


def settings(database="test_step5_123456abcdef", env="test"):
    return SimpleNamespace(app_env=env, mysql_dsn="mysql+pymysql://rag:pass@localhost/"+database,
                           doc_collection=database+"_doc", faq_collection=database+"_faq")


@pytest.mark.parametrize("database,env", [("rag_kb","dev"),("rag_kb_test","test"),
                                         ("test_step5_123456abcdef","dev"),("test_step5_unsafe","test")])
def test_protocol_scope_cannot_publish_shared_or_original(database, env):
    with pytest.raises(ValueError): validate_scope(settings(database,env))


def approved_row():
    meta, _ = approved_sources(); name="http_429_note.md"
    chunk=TEXT_CHUNKERS["manual"](parse_dispatch("manual", PACKAGE/"raw"/name))[0]
    row={**copy.deepcopy(meta[name]),"source":name,"text":normalize_text(_resanitize(chunk).text)}
    record={"filename":name,"file_hash":hashlib.sha256((PACKAGE/"raw"/name).read_bytes()).hexdigest(),
            "status":"done","chunk_count":1,"asset_id":row["asset_id"]}
    return row, record, meta


def test_approved_subset_has_exact_content_and_scope():
    validate_scope(settings()); row,record,meta=approved_row()
    validate_rows([row],[record],meta)


@pytest.mark.parametrize("key,value", [("text","invented"),("review_status","pending"),
    ("tenant_id","other"),("allowed_roles",["admin"]),("asset_id","wrong"),("source","unapproved.md")])
def test_candidate_drift_rejected(key,value):
    row,record,meta=approved_row(); row[key]=value
    with pytest.raises(ValueError): validate_rows([row],[record],meta)


def test_shadow_migration_preserves_access_and_quarantines_identity():
    old={"id":"row", "tenant_id":"private", "visibility":"restricted", "allowed_roles":["ops"],
         "text":"body", "dense":[1.0], "source":"old.md"}
    new=adapt(old)
    assert all(new[k]==v for k,v in old.items())
    assert new["role_scope"]==["ops"] and new["status"]=="draft"
    assert new["review_status"]=="pending" and not new["asset_id"] and not new["source_id"]
