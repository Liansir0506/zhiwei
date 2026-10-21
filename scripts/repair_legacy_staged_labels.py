"""Correct only approved CSV network labels in the exact isolated STAGED candidate."""
import argparse
from collections import Counter
from pathlib import Path

from qa_core.config.settings import get_settings
from qa_core.ingest.write_guard import candidate_write
from qa_core.storage.version_manifest import verify_read_binding
from scripts.prepare_legacy_projection import digest, read, write
from scripts.stage_legacy_candidate import configure, preflight, validate_base, validate_rows
from scripts.migrate_legacy_collections import fingerprint, read_rows


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--report-dir", required=True)
    args = parser.parse_args()
    base = get_settings(); validate_base(base)
    root = Path(args.report_dir).resolve()
    environment = read(root / "environment.json")
    candidate = Path(environment["candidate"]).resolve()
    if (environment["scope"] != "isolated_local_demo_only"
            or environment["version"] != "test-legacy-demo-r1"
            or environment["candidate_artifacts_sha256"] != digest(candidate / "artifact-hashes.json")):
        raise ValueError("not the exact approved isolated candidate")
    repair = root / "network-label-repair"
    if repair.exists():
        raise ValueError("refuse repair report overwrite")
    from transformers import AutoTokenizer
    tokenizer = AutoTokenizer.from_pretrained(base.embedding_model_path, local_files_only=True)
    _, expected, _, plan = preflight("data/candidates/legacy-identity-review-r2",
                                   "data/candidates/legacy-group-review-r2", candidate, tokenizer)
    settings = configure(base, repair, environment["database"])
    if (settings.doc_collection != environment["doc_collection"]
            or settings.faq_collection != environment["faq_collection"]):
        raise ValueError("collection binding mismatch")
    verify_read_binding(settings, environment["version"])
    from pymilvus import MilvusClient
    client = MilvusClient(uri=settings.milvus_uri, timeout=120)
    try:
        with candidate_write(environment["version"], settings=settings):
            doc_before = read_rows(client, settings.doc_collection)
            faq_before = read_rows(client, settings.faq_collection)
            rows = doc_before + faq_before
            # Verify every original field/vector against the corrected pipeline,
            # temporarily accounting for the known old CSV label merge rule.
            before_expected = {key: dict(value) for key, value in expected.items()}
            for row in rows:
                before_expected[row["id"]]["network_tech"] = row["network_tech"]
            validate_rows(rows, before_expected)
            updates = [dict(row, network_tech=expected[row["id"]]["network_tech"])
                       for row in faq_before
                       if row["network_tech"] != expected[row["id"]]["network_tech"]]
            if (len(updates) != 400 or any(row["network_tech"] != "5G" for row in faq_before)
                    or any(row["network_tech"] != expected[row["id"]]["network_tech"] for row in doc_before)
                    or any(row["doc_type"] != "faq" for row in updates)):
                raise ValueError("repair is limited to the exact 400 known FAQ label mismatches")
            repair.mkdir(parents=True)
            ids = {row["id"] for row in updates}
            write(repair / "before-rows.json", [row for row in faq_before if row["id"] in ids])
            for start in range(0, len(updates), 100):
                client.upsert(collection_name=settings.faq_collection, data=updates[start:start + 100])
            client.flush(settings.faq_collection)
            doc_after = read_rows(client, settings.doc_collection)
            faq_after = read_rows(client, settings.faq_collection)
            validate_rows(doc_after + faq_after, expected)
            restored_labels = [dict(row, network_tech="5G") for row in faq_after]
            if (fingerprint(doc_before) != fingerprint(doc_after)
                    or fingerprint(faq_before) != fingerprint(restored_labels)):
                raise ValueError("unexpected body/vector/access/identity mutation")
            write(repair / "summary.json", {"passed": True, "changed_faq_labels": len(updates),
                "faq_labels": dict(Counter(row["network_tech"] for row in faq_after)),
                "files": plan["files"], "chunks": len(doc_after) + len(faq_after),
                "source_body_vectors_access_identity_unchanged": True,
                "doc_fingerprint": fingerprint(doc_after), "before_faq_fingerprint": fingerprint(faq_before),
                "after_faq_fingerprint": fingerprint(faq_after),
                "status": "STAGED", "published": False, "paid_llm_calls": 0,
                "new_embeddings": 0, "fresh_quality_gate_required": True,
                "candidate_artifacts_sha256": digest(candidate / "artifact-hashes.json")})
    finally:
        client.close()
    print("Corrected 400 FAQ labels; content, vectors and access fields unchanged; rerun gate.")


if __name__ == "__main__":
    main()
