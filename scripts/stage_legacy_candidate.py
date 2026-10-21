"""Strictly ingest the approved simulated legacy candidate into a new test DB.

Keeps the candidate STAGED. Uses local real embeddings, records source locations,
checks the ordinary five-part gate and authenticated evidence APIs, never publishes.
"""
import argparse
from collections import Counter
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import re
import secrets
import subprocess

import sqlalchemy as sa

from qa_core.config.environment import validate_isolated_environment
from qa_core.config.settings import get_settings
from qa_core.indexing.chunkers.token_budget import bounded_chunks
from qa_core.indexing.parsers.locations import locate_chunks
from scripts.prepare_legacy_projection import digest, read, validate_projection, write
from scripts.rebuild_kb import (TABLE_CHUNKERS, TEXT_CHUNKERS, _resanitize, dedup_key,
                               normalize_text, parse_dispatch, to_milvus_row)


def validate_base(settings):
    url = sa.engine.make_url(settings.mysql_dsn)
    errors = validate_isolated_environment(settings, "test")
    if (errors or url.database != "rag_kb_test" or url.host != "127.0.0.1" or url.port != 13306
            or settings.milvus_uri != "http://127.0.0.1:29530"):
        raise ValueError("requires the shared local .env.test profile; never original storage")


def validate_target(settings, database):
    url = sa.engine.make_url(settings.mysql_dsn)
    if (not re.fullmatch(r"test_legacy_stage_[0-9a-f]{12}", database)
            or validate_isolated_environment(settings, "test")
            or url.database != database or url.host != "127.0.0.1" or url.port != 13306
            or settings.milvus_uri != "http://127.0.0.1:29530"
            or (settings.doc_collection, settings.faq_collection) != (database + "_doc", database + "_faq")
            or not settings.auth_required):
        raise ValueError("requires exact separate test candidate database/collections and authentication")


def prepare_chunks(path, meta, tokenizer):
    """Reproduce the strict production pipeline before any durable writes."""
    kind = meta["doc_type"]
    parsed = parse_dispatch(kind, path)
    chunks = TABLE_CHUNKERS[kind](parsed) if kind in TABLE_CHUNKERS else TEXT_CHUNKERS[kind](parsed, source=path.name)
    for chunk in chunks:
        if not chunk.metadata.get("source"):
            chunk.metadata["source"] = path.name
        chunk.metadata.setdefault("asset_id", meta["asset_id"])
        chunk.metadata.setdefault("source_version", meta["source_version"])
        if kind == "fault_case":
            chunk.metadata.setdefault("ticket_id", chunk.metadata["source"])
        for k, value in meta.items():
            row_network = kind in TABLE_CHUNKERS and k == "network_tech" and bool(chunk.metadata.get(k))
            if not row_network and k not in {"source", "doc_type", "parent_id", "step_index", "question", "answer"}:
                chunk.metadata[k] = value
            else:
                chunk.metadata.setdefault(k, value)
    chunks = [c if kind in TABLE_CHUNKERS else _resanitize(c) for c in chunks]
    for chunk in chunks:
        chunk.text = normalize_text(chunk.text)
    return bounded_chunks(chunks, tokenizer, max_tokens=450, atomic_tokens=1200)


def preflight(package, review, candidate, tokenizer):
    validate_projection(package, review, candidate)
    candidate = Path(candidate)
    meta = read(candidate / "raw/meta.json")
    intent = read(candidate / "candidate-intent.json")
    groups = Counter(m["doc_type"] for m in meta.values())
    if (groups != {"faq": 1, "fault_case": 15, "guide": 40, "sop": 200}
            or intent["version"] != "test-legacy-demo-r1"
            or any(m.get("synthetic") is not True or m.get("license_status") != "synthetic" for m in meta.values())):
        raise ValueError("requires the exact approved 256-file simulated candidate")
    seen, expected, locations, duplicates = set(), {}, {}, 0
    file_counts = {}
    for name, scope in sorted(meta.items()):
        path = candidate / "raw" / name
        chunks, unique = prepare_chunks(path, scope, tokenizer), []
        for chunk in chunks:
            key = dedup_key(chunk) + json.dumps({k: chunk.metadata.get(k) for k in (
                "tenant_id", "dept_id", "visibility", "allowed_roles", "role_scope", "security_level", "status",
                "source_version", "effective_from", "effective_to")}, sort_keys=True, ensure_ascii=False)
            if key in seen:
                duplicates += 1
            else:
                seen.add(key); unique.append(chunk)
        positions = locate_chunks(path, unique)
        if not unique or len(positions) != len(unique) or any(p is None or p["ambiguous"] for p in positions):
            raise ValueError("candidate needs complete unambiguous source anchors: " + name)
        checksum = digest(path)
        for i, (chunk, position) in enumerate(zip(unique, positions)):
            identity = hashlib.sha256(json.dumps([intent["version"], chunk.metadata["tenant_id"],
                chunk.metadata["asset_id"], checksum, i, chunk.text], ensure_ascii=False, separators=(",", ":")).encode()).hexdigest()[:32]
            expected[identity] = to_milvus_row(chunk, identity, [], intent["version"])
            locations[identity] = position
        file_counts[name] = len(unique)
    return meta, expected, locations, {"files": len(meta), "chunks": len(expected), "duplicates": duplicates,
        "file_groups": dict(groups), "stored_doc_types": dict(Counter(r["doc_type"] for r in expected.values())),
        "file_counts": file_counts, "all_source_locations_unambiguous": True, "tokenizer_chunking": True}


def validate_rows(rows, expected):
    import numpy as np
    if len(rows) != len(expected) or {r["id"] for r in rows} != set(expected):
        raise ValueError("actual candidate chunk roster differs from preflight")
    for row in rows:
        wanted = expected[row["id"]]
        if any(row.get(k) != value for k, value in wanted.items() if k != "dense"):
            raise ValueError("actual candidate content/identity/access fields differ from approval")
        dense = np.asarray(row["dense"], dtype=float)
        if dense.shape != (1024,) or not np.isfinite(dense).all() or abs(float(np.linalg.norm(dense)) - 1) > .001:
            raise ValueError("invalid embedding shape, finiteness or normalization")


def protected_snapshot(settings):
    from pymilvus import MilvusClient
    from scripts.migrate_legacy_collections import fingerprint, read_rows
    engine = sa.create_engine(settings.mysql_dsn)
    try:
        with engine.connect() as conn:
            versions = [dict(r) for r in conn.execute(sa.text("SELECT version,status FROM kb_versions ORDER BY version")).mappings()]
            assets = [dict(r) for r in conn.execute(sa.text("SELECT asset_id,tenant_id,dept_id,filename,source_id,source_system,checksum,status,kb_version,reviewed_by FROM knowledge_assets ORDER BY asset_id")).mappings()]
    finally:
        engine.dispose()
    client = MilvusClient(uri=settings.milvus_uri, timeout=120)
    try:
        collections = [{"name": n, "rows": len(rows), "sha256": fingerprint(rows)}
                       for n in (settings.doc_collection, settings.faq_collection) for rows in [read_rows(client, n)]]
    finally:
        client.close()
    return {"versions": versions, "assets": assets, "collections": collections}


def configure(base, root, database):
    os.environ.update(MYSQL_DSN=sa.engine.make_url(base.mysql_dsn).set(database=database).render_as_string(hide_password=False),
        DOC_COLLECTION=database + "_doc", FAQ_COLLECTION=database + "_faq", AUTH_REQUIRED="true",
        DATA_DIR=str(root / "test-data"), REPORTS_DIR=str(root / "reports"), RATE_LIMIT_QPS="100",
        SEMANTIC_CACHE_ENABLED="false", TOKENIZERS_PARALLELISM="false", OMP_NUM_THREADS="4", MKL_NUM_THREADS="4")
    get_settings.cache_clear()
    settings = get_settings(); validate_target(settings, database)
    return settings


def initialize_database(database):
    if not re.fullmatch(r"test_legacy_stage_[0-9a-f]{12}", database):
        raise ValueError("unsafe isolated database name")
    sql = f"CREATE DATABASE `{database}` CHARACTER SET utf8mb4; GRANT ALL ON `{database}`.* TO 'rag'@'%';"
    run = subprocess.run(["docker", "exec", "-i", "telecom-ops-rag-test-mysql-1", "sh", "-c",
        'MYSQL_PWD="$MYSQL_ROOT_PASSWORD" exec mysql -uroot'], input=sql, text=True, encoding="utf-8", capture_output=True)
    if run.returncode:
        raise RuntimeError("new isolated database creation failed; inspect without automatic overwrite")


def verify_candidate(settings, meta, expected, positions, root, *, version="test-legacy-demo-r1",
                     groups=("faq", "fault_case", "guide", "sop")):
    from pymilvus import MilvusClient
    from scripts.migrate_legacy_collections import read_rows
    from qa_core.storage.kb_dao import KbDao
    from qa_core.storage.location_dao import LocationDao
    from qa_core.retrieval.filters import _to_doc
    from qa_core.storage.version_manifest import verify_read_binding
    dao = KbDao(settings.mysql_dsn)
    client = MilvusClient(uri=settings.milvus_uri, timeout=120)
    try:
        rows = read_rows(client, settings.doc_collection) + read_rows(client, settings.faq_collection)
        validate_rows(rows, expected)
        locations = LocationDao(dao.engine)
        for row in rows:
            if locations.get(_to_doc({"id": row["id"], "entity": row})) != positions[row["id"]]:
                raise ValueError("stored original location differs from preflight")
        records = dao.list_records(version)
        if (len(records) != len(meta) or any(r["status"] != "done" for r in records)
                or sum(r["chunk_count"] for r in records) != len(rows) or dao.get_version(version)["status"] != "STAGED"):
            raise ValueError("candidate ledger/version is incomplete")
        manifest = verify_read_binding(settings, version)
        if not manifest:
            raise ValueError("candidate lacks durable collection/processing binding")
        write(root / "storage-verification.json", {"passed": True, "files": len(records), "chunks": len(rows),
            "actual_doc_types": dict(Counter(r["doc_type"] for r in rows)), "all_rows_match_approved_pipeline": True,
            "all_locations_verified": True, "manifest": manifest, "status": "STAGED", "records": records})
    finally:
        client.close()
    from qa_core.quality.gate import run_gate, save_report
    gate = run_gate(settings, version)
    gate_path = save_report(gate, version, settings.reports_dir)
    dao.set_quality_report(version, gate_path)
    # Gate closes a same-URI SDK alias. Reacquire a fresh client afterwards.
    client = MilvusClient(uri=settings.milvus_uri, timeout=120)
    try:
        from qa_core.api.auth import create_api_key, hash_key
        from qa_core.api.route_evidence import get_evidence_service
        from qa_core.retrieval.searcher import Searcher
        from qa_core.security.policy import AccessScope
        from qa_core.schemas.evidence import evidence_of
        from fastapi.testclient import TestClient
        from app import create_app
        keys = {role: secrets.token_urlsafe(24) for role in ("admin", "ops", "foreign")}
        for role, key in keys.items():
            create_api_key(key, "legacy-stage-" + role, ["ops"] if role == "ops" else ["admin"],
                           tenant_id="other" if role == "foreign" else "default")
        get_evidence_service.cache_clear()
        searcher = Searcher(client=client, settings=settings)
        samples, api_results = [], []
        for kind in groups:
            assets = {m["asset_id"] for m in meta.values() if m["doc_type"] == kind}
            samples.append(next(r for r in rows if r["asset_id"] in assets))
        try:
            # No app lifespan/model warmup; evidence endpoint performs actual lazy local model reads.
            with TestClient(create_app()) as http:
                for row in samples:
                    kind = "faq" if row["doc_type"] == "faq" else "doc"
                    doc = searcher.fetch_evidence_chunk(row["id"], version, scope=AccessScope.build(roles=["admin"]),
                                                        collection_kind=kind)
                    if doc is None:
                        raise ValueError("approved STAGED evidence unreadable")
                    ref = evidence_of(doc, kind)
                    response = http.get(ref["source_url"], headers={"X-API-Key": keys["admin"]})
                    if (response.status_code != 200 or response.json()["text"] != ref["text"]
                            or response.json()["location"] != positions[row["id"]]):
                        raise ValueError("authenticated citation does not match stored original")
                    statuses = {"admin": response.status_code, "no_key": http.get(ref["source_url"]).status_code}
                    for role in ("ops", "foreign"):
                        statuses[role] = http.get(ref["source_url"], headers={"X-API-Key": keys[role],
                            "X-User-Roles": "admin", "X-Tenant-ID": "default"}).status_code
                    if statuses != {"admin": 200, "no_key": 401, "ops": 404, "foreign": 404}:
                        raise ValueError("STAGED preview authentication failed")
                    api_results.append({"doc_type": doc.doc_type, "statuses": statuses, "evidence": response.json()})
                query = "AMF注册成功率下降，排查时先核对哪些信息？"
                request = {"query": query, "kb_version": version, "top_n": 5}
                result = http.post("/kb/search-evidence", json=request, headers={"X-API-Key": keys["admin"]})
                if (result.status_code != 200 or result.json().get("status") != "EVIDENCE_FOUND"
                        or not result.json().get("evidence")):
                    raise ValueError("real candidate evidence retrieval failed")
                write(root / "retrieval.json", {"query": query, "status_code": result.status_code, "result": result.json(),
                      "development_probe_only": True, "paid_llm_calls": 0})
                for role in ("ops", "foreign"):
                    denied = http.post("/kb/search-evidence", json=request, headers={"X-API-Key": keys[role]})
                    # A foreign admin can resolve STAGED, but tenant-scoped search must be empty.
                    valid_denial = (denied.status_code == 404 if role == "ops" else
                                    denied.status_code == 200 and denied.json().get("status") == "NO_EVIDENCE"
                                    and not denied.json().get("evidence"))
                    if not valid_denial:
                        raise ValueError("unauthorized candidate evidence search exposed data")
        finally:
            with dao.engine.begin() as conn:
                for key in keys.values():
                    conn.execute(sa.text("DELETE FROM api_keys WHERE key_hash=:h"), {"h": hash_key(key)})
            get_evidence_service.cache_clear()
        write(root / "evidence-api.json", {"passed": True, "samples": api_results, "temporary_keys_removed": True})
    finally:
        client.close(); dao.engine.dispose()
    return {"gate_passed": gate.passed, "gate_checks": gate.checks, "gate_report": gate_path,
            "storage_verified": True, "authenticated_citations_verified": True, "status": "STAGED", "published": False}


def main():
    p = argparse.ArgumentParser(description=__doc__)
    for name in ("package", "review", "candidate", "report-dir"):
        p.add_argument("--" + name, required=True)
    p.add_argument("--execute", action="store_true")
    p.add_argument("--verify-existing", action="store_true", help="verify this exact completed STAGED candidate; never reingest")
    args = p.parse_args()
    base = get_settings(); validate_base(base)
    candidate = Path(args.candidate).resolve(); root = Path(args.report_dir).resolve()
    if ("test" not in str(root).lower() or root.is_relative_to(candidate)
            or (root.exists() and not args.verify_existing) or (args.verify_existing and (not root.is_dir() or args.execute))):
        raise ValueError("requires a new separate test report directory")
    from transformers import AutoTokenizer
    tokenizer = AutoTokenizer.from_pretrained(base.embedding_model_path, local_files_only=True)
    meta, expected, positions, plan = preflight(args.package, args.review, candidate, tokenizer)
    if args.verify_existing:
        environment = read(root / "environment.json")
        if (environment.get("candidate") != str(candidate)
                or environment.get("candidate_artifacts_sha256") != digest(candidate / "artifact-hashes.json")
                or environment.get("scope") != "isolated_local_demo_only"
                or environment.get("version") != "test-legacy-demo-r1"):
            raise ValueError("existing environment is not this approved candidate")
        check_root = root / ("recheck-" + datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S"))
        if check_root.exists():
            raise ValueError("refuse verification report overwrite")
        check_root.mkdir()
        settings = configure(base, check_root, environment["database"])
        if (settings.doc_collection, settings.faq_collection) != (environment["doc_collection"], environment["faq_collection"]):
            raise ValueError("existing environment collection binding mismatch")
        result = verify_candidate(settings, meta, expected, positions, check_root)
        write(check_root / "verification-result.json", result)
        print(json.dumps(result)); return
    if not args.execute:
        print(json.dumps({k:v for k,v in plan.items() if k != "file_counts"}, ensure_ascii=False)); return
    root.mkdir(parents=True)
    write(root / "preflight.json", plan)
    # Construct the original read profile before configuring the isolated process.
    from qa_core.config.settings import Settings
    original = Settings(_env_file=Path(".env.runtime").resolve())
    if original.app_env != "dev" or sa.engine.make_url(original.mysql_dsn).database != "rag_kb":
        raise ValueError("original read-only profile unavailable")
    before = {"shared_test": protected_snapshot(base), "original": protected_snapshot(original)}
    write(root / "protected-before.json", before)
    database = "test_legacy_stage_" + secrets.token_hex(6)
    settings = configure(base, root, database)
    write(root / "environment.json", {"database": database, "doc_collection": settings.doc_collection,
        "faq_collection": settings.faq_collection, "version": "test-legacy-demo-r1", "scope": "isolated_local_demo_only",
        "candidate": str(candidate), "candidate_artifacts_sha256": digest(candidate / "artifact-hashes.json"),
        "rate_limit_qps_for_verification": 100, "started_at_utc": datetime.now(timezone.utc).isoformat(),
        "paid_llm_calls": 0})
    initialize_database(database)
    engine = sa.create_engine(settings.mysql_dsn)
    from scripts.migrate_domain_model import split_sql_statements
    with engine.begin() as conn:
        for name in sorted(Path("docker/mysql/init").glob("*.sql")):
            for statement in split_sql_statements(name.read_text(encoding="utf-8")):
                conn.exec_driver_sql(statement)
    engine.dispose()
    from pymilvus import MilvusClient
    from scripts.init_collections import build
    client = MilvusClient(uri=settings.milvus_uri, timeout=120)
    try:
        if any(client.has_collection(n) for n in (settings.doc_collection, settings.faq_collection)):
            raise ValueError("new candidate collection collision; never overwrite")
        build(client, settings.doc_collection, False); build(client, settings.faq_collection, True)
    finally:
        client.close()
    from qa_core.domain.models import EnterpriseSnapshot, EnterpriseUser, KnowledgeAsset
    from qa_core.storage.domain_dao import DomainDao
    receipt = read(candidate / "approval-receipt.json")
    domain = DomainDao(settings.mysql_dsn)
    try:
        domain.upsert_snapshot(EnterpriseSnapshot(users=[EnterpriseUser(
            user_id=receipt["approval"]["reviewer"], tenant_id="default", username="local-chat-review-receipt",
            display_name="本地聊天批准凭据映射（不代表平台认证）")], knowledge_assets=[KnowledgeAsset(
            asset_id=m["asset_id"], tenant_id=m["tenant_id"], filename=name, doc_type=m["doc_type"],
            source_system=m["source_system"], source_id=m["source_id"], source_version=m["source_version"],
            kb_version="test-legacy-demo-r1", dept_id=m["dept_id"], security_level=m["security_level"],
            visibility=m["visibility"], role_scope=m["role_scope"], status=m["status"],
            effective_from=m["effective_from"], effective_to=m["effective_to"], checksum=digest(candidate / "raw" / name),
            reviewed_by=receipt["approval"]["reviewer"], approved_at=None) for name, m in meta.items()]))
    finally:
        domain.engine.dispose()
    import torch
    torch.set_num_threads(4)
    from scripts.rebuild_kb import run_rebuild
    def progress(stage, done, total, stats):
        write(root / "progress.json", {"stage": stage, "completed": done, "total": total, "stats": stats})
        print(json.dumps({"stage": stage, "files": done, "total": total, "failed": stats["failed"]}), flush=True)
    stats = run_rebuild("test-legacy-demo-r1", raw_dir=candidate / "raw", strict_metadata=True,
        reviewed_source_map=candidate / "source-map.json", record_locations=True, tokenizer_chunking=True, progress=progress)
    write(root / "ingestion.json", stats)
    if stats["failed"] or stats["done"] != 256:
        raise ValueError("candidate ingestion incomplete; preserve artifacts for diagnosis")
    verification = verify_candidate(settings, meta, expected, positions, root)
    after = {"shared_test": protected_snapshot(base), "original": protected_snapshot(original)}
    write(root / "protected-after.json", after)
    if before != after:
        raise ValueError("protected original/shared test storage changed during execution")
    write(root / "summary.json", {**verification, "database": database, "files": len(meta), "chunks": len(expected),
          "new_domain_registrations": len(meta), "protected_storage_unchanged": True,
          "source_approval_scope": "isolated_local_demo_only", "paid_llm_calls": 0, "independent_quality_verified": False})
    print(json.dumps({"completed": True, **verification, "database": database, "chunks": len(expected)}))


if __name__ == "__main__":
    main()
