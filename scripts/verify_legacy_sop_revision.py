"""Read-only SOP expansion and authenticated references for the approved STAGED demo."""
import argparse
from collections import Counter
import copy
from pathlib import Path
import secrets

import sqlalchemy as sa
from qa_core.config.settings import get_settings
from scripts.prepare_legacy_projection import digest, read, write, validate_projection
from scripts.stage_legacy_candidate import configure, protected_snapshot, validate_base
from scripts.migrate_legacy_collections import fingerprint, read_rows


def require_denial(operation):
    try:
        operation()
    except ValueError:
        return
    raise AssertionError("invalid SOP identity unexpectedly accepted")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--stage-report", required=True)
    parser.add_argument("--report-dir", required=True)
    args = parser.parse_args()
    base = get_settings(); validate_base(base)
    stage = Path(args.stage_report).resolve(); root = Path(args.report_dir).resolve()
    environment = read(stage / "environment.json")
    candidate = Path(environment["candidate"])
    if (root.exists() or "test" not in str(root).lower() or root.is_relative_to(candidate)
            or environment["scope"] != "isolated_local_demo_only"
            or environment["version"] != "test-legacy-demo-r1"
            or environment["candidate_artifacts_sha256"] != digest(candidate / "artifact-hashes.json")):
        raise ValueError("requires exact isolated approved candidate and a new test report")
    validate_projection("data/candidates/legacy-identity-review-r2", "data/candidates/legacy-group-review-r2", candidate)
    shared_before = protected_snapshot(base)
    root.mkdir(parents=True)
    settings = configure(base, root, environment["database"])
    if (settings.doc_collection != environment["doc_collection"]
            or settings.faq_collection != environment["faq_collection"]):
        raise ValueError("collection identity mismatch")
    from qa_core.storage.version_manifest import verify_read_binding
    from qa_core.storage.kb_dao import KbDao
    from pymilvus import MilvusClient
    from qa_core.retrieval.searcher import Searcher
    from qa_core.retrieval.filters import _to_doc
    from qa_core.retrieval.workflow import assemble_sop
    from qa_core.security.policy import AccessScope
    from qa_core.schemas.evidence import evidence_of
    dao = KbDao(settings.mysql_dsn)
    version = environment["version"]
    keys = {}
    client = MilvusClient(uri=settings.milvus_uri, timeout=120)
    try:
        if dao.get_version(version)["status"] != "STAGED":
            raise ValueError("candidate must remain STAGED")
        verify_read_binding(settings, version)
        before = read_rows(client, settings.doc_collection) + read_rows(client, settings.faq_collection)
        searcher = Searcher(client=client, settings=settings, embedder=object(), cache=object())
        scope = AccessScope.build(roles=["admin"])
        by_asset = {}
        for row in before:
            if row["doc_type"] == "sop":
                by_asset.setdefault(row["asset_id"], []).append(row)
        if len(by_asset) != 200:
            raise AssertionError("exact 200 approved SOP sources required")
        results, reasons = [], Counter()
        sample = None
        for asset, stored in sorted(by_asset.items()):
            seed = _to_doc({"id": stored[0]["id"], "entity": stored[0]})
            docs = searcher.fetch_sop_source(seed, ["admin"], version, scope=scope)
            if (len(docs) != 11 or {d.id for d in docs} != {r["id"] for r in stored}
                    or any(d.source_version != "v4-foundation-r3" or d.kb_version != version for d in docs)):
                raise AssertionError("SOP expansion changed source/version or lost chunks")
            wanted = {r["id"]: r for r in stored}
            if any(d.text != wanted[d.id]["text"] for d in docs):
                raise AssertionError("expanded SOP text drift")
            packet = assemble_sop(docs, seed)
            if packet.packet is None or packet.packet.extra["workflow_steps"] != list(range(1, 11)):
                raise AssertionError("source cannot be assembled completely")
            # Approved legacy templates lack approval and rollback statements.
            # Reading all steps must not turn those templates into executable evidence.
            if packet.ready or packet.reason != "missing_safety_controls":
                raise AssertionError("legacy SOP safety controls unexpectedly bypassed")
            reasons[packet.reason] += 1
            results.append({"asset_id": asset, "source": seed.source, "chunks": len(docs),
                            "source_revision": seed.source_version, "workflow_ready": packet.ready,
                            "workflow_reason": packet.reason})
            if sample is None:
                sample = (seed, docs)
        seed, docs = sample
        require_denial(lambda: searcher.fetch_sop_source(seed, ["admin"], "another-release"))
        require_denial(lambda: searcher.fetch_sop_source(seed, ["admin"], version, tenant_id="other"))
        for field, value in (("source_version", ""), ("source_version", "another-revision"),
                             ("source", "another-file.md"), ("asset_id", "another-asset")):
            altered = copy.deepcopy(seed); setattr(altered, field, value)
            require_denial(lambda: searcher.fetch_sop_source(altered, ["admin"], version))
        from qa_core.api.auth import create_api_key, hash_key
        from qa_core.api.route_evidence import get_evidence_service
        from fastapi.testclient import TestClient
        from app import create_app
        for role in ("admin", "ops", "foreign"):
            key = secrets.token_urlsafe(24); keys[role] = key
            create_api_key(key, "sop-revision-" + role, ["ops"] if role == "ops" else ["admin"],
                           tenant_id="other" if role == "foreign" else "default")
        get_evidence_service.cache_clear()
        citations = []
        with TestClient(create_app()) as http:
            for doc in sorted(docs, key=lambda d: d.extra["step_index"][0]):
                ref = evidence_of(doc)
                response = http.get(ref["source_url"], headers={"X-API-Key": keys["admin"]})
                if (response.status_code != 200 or response.json()["text"] != doc.text
                        or response.json()["source_revision"] != doc.source_version):
                    raise AssertionError("expanded source citation unavailable or changed")
                statuses = {"admin": response.status_code, "no_key": http.get(ref["source_url"]).status_code}
                for role in ("ops", "foreign"):
                    statuses[role] = http.get(ref["source_url"], headers={"X-API-Key": keys[role],
                        "X-User-Roles": "admin", "X-Tenant-ID": "default"}).status_code
                if statuses != {"admin": 200, "no_key": 401, "ops": 404, "foreign": 404}:
                    raise AssertionError("STAGED citation boundary failed")
                citations.append({"chunk_id": doc.id, "statuses": statuses, "location": response.json()["location"]})
        # TestClient shares the SDK alias; create a fresh handle for the final read.
        client = MilvusClient(uri=settings.milvus_uri, timeout=120)
        after = read_rows(client, settings.doc_collection) + read_rows(client, settings.faq_collection)
        if fingerprint(before) != fingerprint(after) or dao.get_version(version)["status"] != "STAGED":
            raise AssertionError("candidate knowledge changed during read-only verification")
        shared_after = protected_snapshot(base)
        if shared_before != shared_after:
            raise AssertionError("shared test knowledge changed during verification")
        write(root / "sources.json", results)
        write(root / "citations.json", citations)
        summary = {"passed": True, "sources": len(results), "chunks": 2200,
            "citations_checked": len(citations), "identity_denials": 6,
            "workflow_reasons": dict(reasons), "workflow_ready_sources": 0,
            "candidate_knowledge_unchanged": True, "shared_test_knowledge_unchanged": True,
            "candidate_fingerprint": fingerprint(after), "status": "STAGED", "published": False,
            "original_storage_accessed": False, "embedding_model_calls": 0, "paid_llm_calls": 0,
            "authorization": "user replied 允许 on 2026-10-08 to the concrete SOP revision proposal"}
    finally:
        from qa_core.api.auth import hash_key
        with dao.engine.begin() as conn:
            for key in keys.values():
                conn.execute(sa.text("DELETE FROM api_keys WHERE key_hash=:h"), {"h": hash_key(key)})
        client.close(); dao.engine.dispose()
    write(root / "summary.json", {**summary, "temporary_keys_removed": True})
    print("Verified all 200 SOP sources and 11 authenticated citations; legacy safety refusal preserved.")


if __name__ == "__main__":
    main()
