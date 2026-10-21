"""Ingest approved protocol notes into exact new test collections/version only."""
import argparse
import json
from pathlib import Path

import sqlalchemy as sa
from pymilvus import MilvusClient

from qa_core.config.settings import get_settings
from qa_core.config.environment import validate_isolated_environment
from qa_core.quality.eval_review import validate_export
from qa_core.ingest.source_identity import validate_mapping
from qa_core.domain.models import EnterpriseSnapshot, KnowledgeAsset
from qa_core.storage.domain_dao import DomainDao
from qa_core.storage.kb_dao import KbDao
from scripts.review_core_semantics import verify_hashes
from scripts.init_collections import build
from scripts.rebuild_kb import run_rebuild

VERSION = "v4-protocol-dev-r1"
DOC = "test_protocol_r1_doc"
FAQ = "test_protocol_r1_faq"


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--package", required=True); parser.add_argument("--report", required=True)
    parser.add_argument("--execute", action="store_true")
    parser.add_argument("--resume-empty", action="store_true", help="continue only exact empty new collections and absent version")
    args = parser.parse_args()
    root, report = Path(args.package).resolve(), Path(args.report).resolve()
    if report.exists(): raise ValueError("refuse report overwrite")
    verify_hashes(root)
    dataset = json.loads((root / "eval-dev.json").read_text(encoding="utf-8"))
    validate_export(dataset)
    if dataset["kb_version"] != VERSION: raise ValueError("unsupported dev version")
    meta = json.loads((root / "raw/meta.json").read_text(encoding="utf-8"))
    validate_mapping(root / "raw", json.loads((root / "source-map.json").read_text(encoding="utf-8")), meta)
    settings = get_settings()
    errors = validate_isolated_environment(settings, "test")
    if errors or (settings.doc_collection, settings.faq_collection) != (DOC, FAQ):
        raise ValueError("requires exact isolated protocol profile: " + str(errors))
    if not args.execute:
        print(json.dumps({"ready": True, "files": len(meta), "version": VERSION})); return
    kb = KbDao(settings.mysql_dsn)
    with kb.engine.connect() as conn:
        before = [dict(r) for r in conn.execute(sa.text("SELECT version,status FROM kb_versions ORDER BY version")).mappings()]
    existing_version = next((row for row in before if row["version"] == VERSION), None)
    if existing_version:
        records = kb.list_records(VERSION)
        expected_records = {(s["filename"],s["sha256"]) for s in dataset["sources"]}
        from qa_core.storage.version_manifest import verify_read_binding
        if (not args.resume_empty or existing_version["status"] != "STAGED"
                or {(r["filename"],r["file_hash"]) for r in records} != expected_records
                or any(r["status"] != "failed" or r["chunk_count"] for r in records)
                or not verify_read_binding(settings, VERSION)):
            raise ValueError("existing version is not this exact failed empty candidate")
    client = MilvusClient(uri=settings.milvus_uri, timeout=30)
    try:
        existing = [name for name in (DOC,FAQ) if client.has_collection(name)]
        if existing:
            if not args.resume_empty or set(existing) != {DOC,FAQ}:
                raise ValueError("new collections already exist; inspect before retry")
            if any(client.query(collection_name=name, filter="", output_fields=["id"], limit=1,
                consistency_level="Strong") for name in existing):
                raise ValueError("resume only empty collections; never overwrite/delete existing rows")
        build(client, DOC, False); build(client, FAQ, True)
        snapshot = EnterpriseSnapshot(knowledge_assets=[KnowledgeAsset(
            asset_id=m["asset_id"], tenant_id=m["tenant_id"], filename=name, doc_type="manual",
            source_system=m["source_system"], source_id=m["source_id"], source_version=VERSION, kb_version=VERSION,
            checksum=next(s["sha256"] for s in dataset["sources"] if s["filename"] == name),
            dept_id=m["dept_id"], security_level=m["security_level"], visibility=m["visibility"],
            role_scope=m["role_scope"], status="draft") for name,m in meta.items()])
        DomainDao(settings.mysql_dsn).upsert_snapshot(snapshot)
        stats = run_rebuild(VERSION, raw_dir=root / "raw", strict_metadata=True,
            reviewed_source_map=root / "source-map.json", record_locations=True, tokenizer_chunking=True)
        records = kb.list_records(VERSION)
        with kb.engine.connect() as conn:
            after = [dict(r) for r in conn.execute(sa.text("SELECT version,status FROM kb_versions ORDER BY version")).mappings()]
        old_unchanged = [r for r in after if r["version"] != VERSION] == [r for r in before if r["version"] != VERSION]
        own = kb.get_version(VERSION)
        rows = client.query(collection_name=DOC, filter=f'kb_version == "{VERSION}"',
            output_fields=["id", "source", "tenant_id", "source_system", "source_id", "asset_id"], limit=100,
            consistency_level="Strong")
        actual_ids = {(r["source"], r["source_id"]) for r in rows}
        expected_ids = {(name,m["source_id"]) for name,m in meta.items()}
        passed = (stats["failed"] == 0 and stats["done"] == 4 and len(records) == 4
            and all(r["status"] == "done" for r in records) and own["status"] == "STAGED"
            and old_unchanged and actual_ids == expected_ids and all(r["tenant_id"] == "default" for r in rows))
        result = {"passed": passed, "stats": stats, "records": records, "actual_chunks": len(rows),
            "source_identity_verified": actual_ids == expected_ids, "old_versions_unchanged": old_unchanged,
            "versions_before": before, "versions_after": after, "collections": [DOC,FAQ], "status": own["status"],
            "published": False, "llm_used": False, "artifact_sha256": verify_hashes(root)}
        report.parent.mkdir(parents=True, exist_ok=True)
        report.write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        print(json.dumps({k:result[k] for k in ("passed","actual_chunks","source_identity_verified","old_versions_unchanged","status")}))
        if not passed: raise RuntimeError("protocol dev ingestion failed; inspect report, do not auto-delete/retry")
    finally:
        client.close()


if __name__ == "__main__": main()
