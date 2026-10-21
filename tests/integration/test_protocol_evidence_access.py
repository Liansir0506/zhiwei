"""Read approved protocol evidence through real auth/storage, without publishing it."""
import copy
import uuid
from unittest.mock import Mock

from fastapi.testclient import TestClient
from pymilvus import MilvusClient
from sqlalchemy import text

from qa_core.config.settings import get_settings
from qa_core.api import auth, route_evidence
from qa_core.api.auth import create_api_key, hash_key
from qa_core.application.evidence_service import EvidenceService
from qa_core.retrieval.searcher import Searcher
from qa_core.schemas.evidence import evidence_of
from qa_core.security.policy import AccessScope
from qa_core.storage.kb_dao import KbDao
from qa_core.storage.location_dao import LocationDao


def test_approved_protocol_real_auth_and_content_bound_references(monkeypatch):
    s = get_settings()
    assert s.is_test and s.doc_collection == "test_protocol_r1_doc"
    assert s.faq_collection == "test_protocol_r1_faq"
    version = "v4-protocol-dev-r1"
    token = uuid.uuid4().hex
    dao = KbDao(s.mysql_dsn)
    client = MilvusClient(uri=s.milvus_uri, timeout=15)
    settings = s.model_copy(update={"auth_required": True})
    searcher = Searcher(client=client, embedder=Mock(), cache=Mock(), settings=settings)
    service = EvidenceService(searcher, Mock(), settings)
    monkeypatch.setattr(auth, "get_settings", lambda: settings)
    monkeypatch.setattr(route_evidence, "get_settings", lambda: settings)
    monkeypatch.setattr(route_evidence, "get_evidence_service", lambda: service)
    keys = [("protocol-admin-" + token, ["admin"], "default"),
            ("protocol-ops-" + token, ["ops"], "default"),
            ("protocol-foreign-" + token, ["admin"], "other-" + token)]
    try:
        assert dao.get_version(version)["status"] == "STAGED"
        before = client.query(collection_name=s.doc_collection,
                              filter=f'kb_version == "{version}"',
                              output_fields=["*"], consistency_level="Strong")
        assert len(before) == 4
        for key, roles, tenant in keys:
            create_api_key(key, key, roles, tenant_id=tenant)
        from app import create_app
        with TestClient(create_app()) as http:
            urls = []
            for row in before:
                doc = searcher.fetch_evidence_chunk(row["id"], version,
                    scope=AccessScope.build(roles=["admin"]))
                assert doc is not None
                expected = evidence_of(doc)
                location = LocationDao(dao.engine).get(doc)
                assert location and expected["location"] == location
                urls.append(expected["source_url"])
                response = http.get(urls[-1], headers={"X-API-Key": keys[0][0]})
                assert response.status_code == 200, response.text
                assert response.headers["cache-control"] == "no-store"
                assert response.json()["text"] == doc.text
                assert response.json()["location"] == location
                assert response.json()["source_revision"] == expected["source_revision"]
                changed = copy.deepcopy(doc)
                changed.text += " NOT THE STORED CONTENT"
                assert http.get(evidence_of(changed)["source_url"],
                    headers={"X-API-Key": keys[0][0]}).status_code == 404
            assert http.get(urls[0]).status_code == 401
            assert http.get(urls[0], headers={"X-API-Key": keys[1][0],
                "X-User-Roles": "admin"}).status_code == 404
            assert http.get(urls[0], headers={"X-API-Key": keys[2][0],
                "X-Tenant-Id": "default"}).status_code == 404
            with dao.engine.begin() as conn:
                conn.execute(text("UPDATE api_keys SET enabled=0 WHERE key_hash=:h"),
                             {"h": hash_key(keys[0][0])})
            assert http.get(urls[0], headers={"X-API-Key": keys[0][0]}).status_code == 401
        after = client.query(collection_name=s.doc_collection,
                             filter=f'kb_version == "{version}"',
                             output_fields=["*"], consistency_level="Strong")
        assert sorted(before, key=lambda r: r["id"]) == sorted(after, key=lambda r: r["id"])
        assert dao.get_version(version)["status"] == "STAGED"
        assert dao.get_version("v1")["status"] == "ACTIVE"
    finally:
        client.close()
        with dao.engine.begin() as conn:
            for key, _, _ in keys:
                conn.execute(text("DELETE FROM api_keys WHERE key_hash=:h"), {"h": hash_key(key)})
