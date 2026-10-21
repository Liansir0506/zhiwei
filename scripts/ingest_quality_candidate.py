"""Run only the selected isolated candidate/control jobs; never publish or drain a queue."""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import shutil
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from scripts.build_knowledge_candidate import encode, sha
from scripts.verify_knowledge_candidate import verify
from qa_core.config.settings import get_settings
from qa_core.config.environment import validate_isolated_environment
from qa_core.domain.models import EnterpriseSnapshot, KnowledgeAsset
from qa_core.storage.domain_dao import DomainDao
from qa_core.storage.kb_dao import KbDao
from qa_core.ingest.jobs import IngestJobDao
from scripts.init_collections import build
from scripts.rebuild_kb import guess_doc_type
from scripts.rebuild_kb import _resanitize, dedup_key, file_hash_of, IngestCancelled
from scripts.validate_enterprise_dataset import _chunks

PROFILES = {
    "control": ("v4-r3-quality-control", "test_quality_control_doc", "test_quality_control_faq"),
    "candidate": ("v4-quality-r1", "test_quality_r1_doc", "test_quality_r1_faq"),
}


def asset_id(version: str, filename: str, digest: str) -> str:
    return "kb-" + sha(f"{version}:{filename}:{digest}".encode())[:32]


class ReusedPassages:
    """Reuse exact old passage vectors; query inference still uses the local model.

    This is a migration of indexed vectors, not a claim that the old model checksum
    was captured. Different passages are embedded with the configured local model.
    """
    def __init__(self, vectors, settings):
        self.vectors, self.settings = vectors, settings
        self.reused = self.computed = 0
        self.model = None

    def embed_passages(self, texts):
        missing = list(dict.fromkeys(t for t in texts if t not in self.vectors))
        if missing:
            from qa_core.indexing.embedder.embedder import get_embedder
            self.model = self.model or get_embedder(self.settings.embedding_model_path, self.settings.embedding_device)
            self.vectors.update(zip(missing, self.model.embed_passages(missing)))
        self.computed += len(missing)
        self.reused += len(texts) - len(missing)
        return [self.vectors[t] for t in texts]


def prepare(source: Path, stage: Path, version: str):
    meta = json.loads((source / "meta.json").read_text(encoding="utf-8"))
    snapshot = EnterpriseSnapshot()
    stage.mkdir(parents=True, exist_ok=True)
    for filename, item in meta.items():
        data = (source / filename).read_bytes()
        target = stage / filename
        if target.exists() and target.read_bytes() != data:
            raise ValueError("prepared source changed")
        if not target.exists():
            shutil.copyfile(source / filename, target)
        digest = sha(data)
        item.update(asset_id=asset_id(version, filename, digest), source_version=version, dataset_id=version)
        snapshot.knowledge_assets.append(KnowledgeAsset(
            asset_id=item["asset_id"], tenant_id=item["tenant_id"], filename=filename,
            doc_type=guess_doc_type(filename), source_system=item["source_system"],
            dept_id=item["dept_id"], source_version=version, kb_version=version, checksum=digest,
            security_level=item["security_level"], visibility=item["visibility"],
            role_scope=item["role_scope"], status="draft",
            effective_from=item["effective_from"], effective_to=item["effective_to"]))
    encoded = encode(meta)
    sidecar = stage / "meta.json"
    if sidecar.exists() and sidecar.read_bytes() != encoded:
        raise ValueError("prepared metadata changed")
    sidecar.write_bytes(encoded)
    return snapshot


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--candidate", type=Path, required=True)
    parser.add_argument("--original", type=Path, required=True)
    parser.add_argument("--report", type=Path, required=True)
    parser.add_argument("--execute", action="store_true")
    parser.add_argument("--resume", action="store_true", help="retry only this runner's failed/cancelled job")
    args = parser.parse_args()
    if args.report.exists():
        raise ValueError("refuse to overwrite run report")
    settings = get_settings()
    errors = validate_isolated_environment(settings, "test")
    if errors:
        raise ValueError(errors)
    verification = verify(args.candidate, args.original)
    if not args.execute:
        print(json.dumps(verification, ensure_ascii=False))
        return
    from pymilvus import MilvusClient
    from sqlalchemy import text
    from scripts.ingest_worker import execute_job, _configure_logging
    _configure_logging()
    client = MilvusClient(uri=settings.milvus_uri, timeout=20)
    kb = KbDao(settings.mysql_dsn)
    jobs = IngestJobDao(settings.mysql_dsn)
    domain = DomainDao(settings.mysql_dsn)
    with kb.engine.connect() as conn:
        versions_before = [dict(r) for r in conn.execute(text("SELECT * FROM kb_versions ORDER BY version")).mappings()]
        assets_before = [dict(r) for r in conn.execute(text("SELECT * FROM knowledge_assets ORDER BY asset_id")).mappings()]
    old_fields = {}
    vectors_by_id = {}
    for collection in (settings.doc_collection, settings.faq_collection):
        old_fields[collection] = sorted(f["name"] for f in client.describe_collection(collection_name=collection)["fields"])
        iterator = client.query_iterator(collection_name=collection, filter='kb_version == "v4-foundation-r3"',
                                         output_fields=["id", "dense"], batch_size=200)
        try:
            while rows := iterator.next():
                for row in rows:
                    vectors_by_id[row["id"]] = row["dense"]
        finally:
            iterator.close()
    # Old row ID hashes the ORIGINAL passage before display normalization. Rebuild
    # these IDs from immutable raw bytes, proving exact passage identity for reuse.
    vectors, seen = {}, set()
    for path in sorted(args.original.iterdir()):
        if not path.is_file() or path.name == "meta.json":
            continue
        doc_type = guess_doc_type(path.name)
        chunks = _chunks(path)
        unique = []
        for chunk in chunks:
            if doc_type not in {"faq", "fault_case"}:
                chunk = _resanitize(chunk)
            key = dedup_key(chunk)
            if key not in seen:
                unique.append(chunk)
                seen.add(key)
        digest = file_hash_of(path)
        for i, chunk in enumerate(unique):
            ident = sha(f"{digest}:{i}:{chunk.text}".encode())[:32]
            if ident in vectors_by_id:
                vectors[chunk.text] = vectors_by_id[ident]
    print(f"verified reusable passage IDs: {len(vectors)}", flush=True)
    embedder = ReusedPassages(vectors, settings)
    result = {"old_schema_fields": old_fields, "verification": verification, "profiles": {},
              "old_embedding_checkpoint_hash": "not_recorded_in_historical_index",
              "published": False, "llm_used": False}
    result["reuse_identity"] = "raw_file_sha256 + chunk_index + exact_original_passage_sha256"
    original_collection_names = (settings.doc_collection, settings.faq_collection)
    try:
        for profile, (version, doc, faq) in PROFILES.items():
            os.environ.update(DOC_COLLECTION=doc, FAQ_COLLECTION=faq)
            get_settings.cache_clear()
            build(client, doc, False)
            build(client, faq, True)
            stage = Path(settings.data_dir) / "quality-candidates" / profile / "raw"
            snapshot = prepare(args.original if profile == "control" else args.candidate / "raw", stage, version)
            domain.upsert_snapshot(snapshot)
            identity = sha((stage / "meta.json").read_bytes())
            job, created = jobs.create(job_type="rebuild", tenant_id="default", actor="quality-candidate-runner",
                kb_version=version, idempotency_key=sha(f"quality:{version}:{identity}".encode()), max_attempts=1,
                payload={"raw_dir": str(stage.resolve()), "strict_metadata": True, "run_quality_gate": False,
                         "doc_collection": doc, "faq_collection": faq})
            if job["status"] in {"failed", "cancelled"} and args.resume:
                job = jobs.retry(job["job_id"], "default")
            if job["status"] != "success":
                claimed = jobs.claim_next("quality-candidate-runner", job_id=job["job_id"])
                if not claimed:
                    raise RuntimeError(f"selected job not claimable: {job['job_id']} {job['status']}")
                try:
                    stats = execute_job(claimed, jobs, "quality-candidate-runner", embedder_override=embedder)
                    jobs.finish(job["job_id"], "quality-candidate-runner", stats)
                except IngestCancelled:
                    jobs.cancelled(job["job_id"], "quality-candidate-runner")
                    raise
                except Exception as exc:
                    jobs.fail(job["job_id"], "quality-candidate-runner", f"{type(exc).__name__}: {exc}")
                    raise
            records = kb.list_records(version)
            result["profiles"][profile] = {"version": version, "doc_collection": doc, "faq_collection": faq,
                "job_id": job["job_id"], "job_status": jobs.get(job["job_id"], "default")["status"],
                "records": len(records), "loaded_chunks": sum(r["chunk_count"] or 0 for r in records),
                "kb_status": kb.get_version(version)["status"], "release_quality_gate": "not_run_not_published"}
        with kb.engine.connect() as conn:
            current_versions = {r["version"]: dict(r) for r in conn.execute(text("SELECT * FROM kb_versions")).mappings()}
            current_assets = {r["asset_id"]: dict(r) for r in conn.execute(text("SELECT * FROM knowledge_assets")).mappings()}
        result["old_versions_unchanged"] = all(current_versions[r["version"]] == r for r in versions_before)
        result["old_assets_unchanged"] = all(current_assets[r["asset_id"]] == r for r in assets_before)
        if not result["old_versions_unchanged"] or not result["old_assets_unchanged"]:
            raise RuntimeError("historical version/asset changed")
        result.update(reused_passages=embedder.reused, newly_embedded_passages=embedder.computed)
        args.report.parent.mkdir(parents=True, exist_ok=True)
        args.report.write_bytes(encode(result))
        print(json.dumps(result["profiles"], ensure_ascii=False, indent=2))
    finally:
        client.close()
        os.environ.update(DOC_COLLECTION=original_collection_names[0], FAQ_COLLECTION=original_collection_names[1])
        get_settings.cache_clear()


if __name__ == "__main__":
    main()
