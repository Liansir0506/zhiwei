"""Persistent, isolated release rehearsal with actual approved protocol summaries."""
import argparse
import json
import os
import secrets
import shutil
import subprocess
from pathlib import Path

import sqlalchemy as sa
from fastapi.testclient import TestClient
from pymilvus import MilvusClient

from qa_core.config.settings import get_settings
from qa_core.quality.protocol_gate import PACKAGE, approved_sources, run_protocol_gate


def main():
    p = argparse.ArgumentParser(description=__doc__); p.add_argument("--report-dir", required=True)
    args = p.parse_args(); root = Path(args.report_dir).resolve()
    if root.exists(): raise ValueError("refuse report overwrite")
    base = get_settings(); url = sa.engine.make_url(base.mysql_dsn)
    if base.app_env != "test" or url.database != "rag_kb_test" or url.port != 13306:
        raise ValueError("requires .env.test")
    meta, receipt_sha = approved_sources()
    token = secrets.token_hex(6); database = "test_step5_" + token
    root.mkdir(parents=True)
    sql = f"CREATE DATABASE `{database}` CHARACTER SET utf8mb4; GRANT ALL ON `{database}`.* TO 'rag'@'%';"
    result = subprocess.run(["docker", "exec", "-i", "telecom-ops-rag-test-mysql-1", "sh", "-c",
        'MYSQL_PWD="$MYSQL_ROOT_PASSWORD" exec mysql -uroot'], input=sql, text=True, capture_output=True)
    if result.returncode: raise RuntimeError("isolated database creation failed")
    updates = dict(MYSQL_DSN=url.set(database=database).render_as_string(hide_password=False),
        DOC_COLLECTION=database+"_doc", FAQ_COLLECTION=database+"_faq", DATA_DIR=str(root/"test-data"),
        REPORTS_DIR=str(root/"reports"), AUTH_REQUIRED="true")
    os.environ.update(updates); get_settings.cache_clear(); settings=get_settings()
    engine = sa.create_engine(settings.mysql_dsn)
    from scripts.migrate_domain_model import split_sql_statements
    with engine.begin() as conn:
        for name in sorted(Path("docker/mysql/init").glob("*.sql")):
            for statement in split_sql_statements(name.read_text(encoding="utf-8")):
                conn.exec_driver_sql(statement)
    from scripts.init_collections import build
    client = MilvusClient(uri=settings.milvus_uri, timeout=60)
    build(client, settings.doc_collection, False); build(client, settings.faq_collection, True)
    from scripts.rebuild_kb import run_rebuild
    from qa_core.storage.kb_dao import KbDao
    from qa_core.quality.gate import run_gate, save_report
    dao = KbDao(settings.mysql_dsn)
    first, second = "v5-protocol-base", "v5-protocol-plus429"
    summary = {"database": database, "doc_collection":settings.doc_collection, "faq_collection":settings.faq_collection,
               "approval_receipt_sha256":receipt_sha, "scope":"isolated_development_only", "paid_llm_calls":0,
               "shared_active_switched":False, "ingestion":[], "checks":{}}
    (root/"environment.json").write_text(json.dumps({k:summary[k] for k in ("database","doc_collection","faq_collection","scope")},indent=2),encoding="utf-8")
    source_map=json.loads((PACKAGE/"source-map.json").read_text(encoding="utf-8"))
    for version, names in ((first, sorted(set(meta)-{"http_429_note.md"})), (second, sorted(meta))):
        raw=root/"test-data"/version/"raw"; raw.mkdir(parents=True)
        for name in names: shutil.copyfile(PACKAGE/"raw"/name, raw/name)
        (raw/"meta.json").write_text(json.dumps({n:meta[n] for n in names},ensure_ascii=False),encoding="utf-8")
        mapping={**source_map,"entries":[e for e in source_map["entries"] if e["filename"] in names]}
        map_path=raw.parent/"source-map.json"; map_path.write_text(json.dumps(mapping,ensure_ascii=False),encoding="utf-8")
        stats=run_rebuild(version,raw_dir=raw,strict_metadata=True,record_locations=True,
                          tokenizer_chunking=True,reviewed_source_map=map_path)
        assert stats["failed"]==0 and stats["done"]==len(names)
        assert dao.get_version(version)["status"]=="STAGED"
        if version==first:
            standard=run_gate(settings,version)
            summary["standard_operational_gate"]={"passed":standard.passed,"checks":standard.checks}
            assert not standard.passed
            save_report(standard,version,str(root/"standard-gate"))
        gate=run_protocol_gate(settings,version); assert gate.passed
        dao.set_quality_report(version,save_report(gate,version,settings.reports_dir))
        summary["ingestion"].append({"version":version,"files":names,"stats":stats,"staged":True})
    from app import create_app
    from qa_core.api.auth import create_api_key
    keys={name:secrets.token_urlsafe(24) for name in ("admin","ops","foreign")}
    for name,key in keys.items(): create_api_key(key,"step5-"+name,["admin"] if name!="ops" else ["ops"],tenant_id="foreign" if name=="foreign" else "default")
    headers={"X-API-Key":keys["admin"]}
    # Keys stay in memory. Reports contain responses and identities, never credentials.
    from contextlib import contextmanager
    @contextmanager
    def disable_keys_on_exit():
        try:
            yield
        finally:
            with engine.begin() as conn:
                for name in keys:
                    conn.execute(sa.text("UPDATE api_keys SET enabled=0 WHERE user_id=:u"), {"u":"step5-"+name})

    with disable_keys_on_exit(), TestClient(create_app()) as http:
        def post(path, body):
            r=http.post(path,json=body,headers=headers); assert r.status_code==200,(path,r.text); return r.json()
        # SDK clients for one URI share a connection alias; gate calls close theirs.
        client=MilvusClient(uri=settings.milvus_uri,timeout=60)
        rows=client.query(collection_name=settings.doc_collection,filter=f'kb_version == "{second}"',output_fields=["id","source"],limit=10,consistency_level="Strong")
        from qa_core.retrieval.searcher import Searcher
        from qa_core.security.policy import AccessScope
        from qa_core.schemas.evidence import evidence_of
        from qa_core.indexing.embedder.embedder import get_embedder
        from qa_core.cache.redis_cache import CacheLayer
        from qa_core.rerank.reranker import get_reranker
        from qa_core.application.evidence_service import EvidenceService
        searcher=Searcher(client=client,embedder=get_embedder(settings.embedding_model_path,settings.embedding_device),cache=CacheLayer(settings.redis_url),settings=settings)
        service=EvidenceService(searcher,get_reranker(settings.reranker_model_path,settings.rerank_device),settings)
        refs={}
        for row in rows:
            doc=searcher.fetch_evidence_chunk(row["id"],second,scope=AccessScope.build(roles=["admin"]))
            assert doc is not None
            ref=evidence_of(doc); refs[row["source"]]=ref
            r=http.get(ref["source_url"],headers=headers); assert r.status_code==200 and r.json()["text"]==doc.text
            assert r.json()["location"].get("available") is not False
            assert http.get(ref["source_url"]).status_code==401
            for who in ("ops","foreign"):
                assert http.get(ref["source_url"],headers={"X-API-Key":keys[who],"X-User-Roles":"admin","X-Tenant-Id":"default"}).status_code==404
        summary["checks"]["staged_preview_reauth_four_sources"]=True
        query="HTTP 429 响应一定需要 Retry-After 吗？"
        found=service.search(query,second,AccessScope.build(roles=["admin"]),5)
        assert found["status"]=="EVIDENCE_FOUND" and any(e["source"]=="http_429_note.md" for e in found["evidence"])
        (root/"retrieval-before-withdrawal.json").write_text(json.dumps(found,ensure_ascii=False,indent=2),encoding="utf-8")
        summary["checks"]["real_dense_bm25_rerank"]=True
        summary["publish_base"]=post("/kb/activate",{"version":first})
        summary["publish_plus429"]=post("/kb/activate",{"version":second})
        assert http.get(refs["http_429_note.md"]["source_url"],headers={"X-API-Key":keys["ops"]}).status_code==200
        summary["withdrawal"]=post("/kb/withdrawals",{"source_system":meta["http_429_note.md"]["source_system"],"source_id":meta["http_429_note.md"]["source_id"],"reason":"隔离实际候选撤回与安全回滚验收"})
        assert http.get(refs["http_429_note.md"]["source_url"],headers=headers).status_code==404
        summary["checks"]["withdrawn_reference_denied"]=True
        summary["rollback_base"]=post("/kb/rollback",{"version":first})
        denied=http.post("/kb/rollback",json={"version":second},headers=headers)
        assert denied.status_code==400 and "撤回" in denied.text
        summary["withdrawn_rollback_refusal"]={"status":denied.status_code,"detail":denied.json()["detail"]}
        with engine.connect() as conn:
            summary["versions_after"]=[dict(r) for r in conn.execute(sa.text("SELECT version,status FROM kb_versions ORDER BY version")).mappings()]
            summary["audit"]=[dict(r) for r in conn.execute(sa.text("SELECT action,actor,payload_json FROM audit_log ORDER BY id")).mappings()]
        assert next(r for r in summary["versions_after"] if r["version"]==first)["status"]=="ACTIVE"
    summary["passed"]=True
    (root/"summary.json").write_text(json.dumps(summary,ensure_ascii=False,indent=2)+"\n",encoding="utf-8")
    client.close();engine.dispose()
    print(json.dumps({"passed":True,"database":database,"sources":4,"rollback_active":first,"withdrawn_sources":1}))


if __name__ == "__main__": main()
