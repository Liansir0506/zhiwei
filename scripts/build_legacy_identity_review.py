"""Audit original ACTIVE against frozen raw files and propose identities, never approve.

The only writes are a new local review package. No embeddings, LLM, database
writes, collection changes or ACTIVE changes. Proposed UUIDs are allocations,
not inferred authoritative source identities or approved legacy aliases.
"""
import argparse
from collections import Counter, defaultdict
import copy
import hashlib
import json
from pathlib import Path
import uuid

import sqlalchemy as sa

from qa_core.quality.review_contract import bind_pending, check_approval
from qa_core.indexing.parsers.locations import locate_chunks
from qa_core.indexing.parsers.parsers import parse_dispatch
from scripts.rebuild_kb import TEXT_CHUNKERS, TABLE_CHUNKERS, _resanitize, to_milvus_row


def digest(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def content_key(row):
    # Match the complete stored FAQ answer too, not only its question/source ID.
    return json.dumps([row.get(k, "") for k in ("doc_type", "source", "text", "question", "answer")],
                      ensure_ascii=False, separators=(",", ":"))


def propose_identity(name, checksum, metadata, registration=None):
    if registration is not None:
        expected = {"filename": name, "checksum": checksum, "tenant_id": metadata["tenant_id"],
                    "source_system": metadata.get("source_system", ""), "source_version": metadata.get("source_version", "")}
        if any(registration.get(k) != value for k, value in expected.items()):
            raise ValueError("existing domain registration disagrees with source content/scope")
        if not registration.get("source_id") or not registration.get("asset_id"):
            raise ValueError("existing domain registration identity incomplete")
        if any(metadata.get(k) and metadata[k] != registration[k] for k in ("source_id", "asset_id")):
            raise ValueError("existing identity conflict requires registration review")
        return registration["source_id"], registration["asset_id"], "preserve existing declared domain identity; human approval still pending"
    allocation = uuid.uuid4().hex
    return "project.legacy." + allocation, "legacy-" + allocation, "new opaque UUID proposal; no inferred filename identity"


def reconstruct(path, metadata):
    from scripts.rebuild_kb import guess_doc_type
    kind = guess_doc_type(Path(path).name)
    parsed = parse_dispatch(kind, path)
    chunks = (TABLE_CHUNKERS[kind](parsed) if kind in TABLE_CHUNKERS else
              TEXT_CHUNKERS[kind](parsed, source=Path(path).name))
    for chunk in chunks:
        chunk.metadata.setdefault("source", Path(path).name)
        if not chunk.metadata["source"]:
            chunk.metadata["source"] = Path(path).name
        for key, value in metadata.items():
            chunk.metadata.setdefault(key, copy.deepcopy(value))
    if kind not in TABLE_CHUNKERS:
        chunks = [_resanitize(c) for c in chunks]
    return chunks


def trace_rows(raw, metadata, rows):
    """Pure source-to-stored-content comparison; all ambiguous matches stay blocked."""
    index, expected_counts = defaultdict(list), {}
    for name, meta in sorted(metadata.items()):
        chunks = reconstruct(raw / name, meta)
        expected_counts[name] = len(chunks)
        locations = locate_chunks(raw / name, chunks)
        for ordinal, (chunk, location) in enumerate(zip(chunks, locations)):
            expected = to_milvus_row(chunk, "audit-only", [], "audit-only")
            index[content_key(expected)].append((name, ordinal, expected, location))
    traces = []
    for kind, row in rows:
        options = index.get(content_key(row), [])
        trace = {"collection_kind": kind, "chunk_id": row["id"], "source": row.get("source"),
                 "content_sha256": hashlib.sha256(content_key(row).encode()).hexdigest(),
                 "matching_files": sorted({m[0] for m in options}), "match_count": len(options),
                 "status": "unmatched", "location": None}
        if len(options) == 1:
            name, ordinal, expected, location = options[0]
            conflicts = [k for k in ("tenant_id", "visibility", "allowed_roles", "dataset_id")
                         if k in row and row[k] != expected[k]]
            trace.update(filename=name, parsed_chunk=ordinal, location=location, scope_conflicts=conflicts,
                         status="scope_conflict" if conflicts else "location_unresolved" if location is None or location["ambiguous"] else "exact_trace")
        elif options:
            trace["status"] = "ambiguous"
        traces.append(trace)
    return traces, expected_counts


def validate_package(package, *, require_approved=False, live_raw=None):
    package = Path(package).resolve()
    hashes = json.loads((package / "artifact-hashes.json").read_text(encoding="utf-8"))
    actual_files = {str(p.relative_to(package)).replace("\\", "/") for p in package.rglob("*")
                    if p.is_file() and p != package / "artifact-hashes.json"}
    if actual_files != set(hashes):
        raise ValueError("review package file roster changed")
    for name, checksum in hashes.items():
        path = (package / name).resolve()
        if not path.is_relative_to(package) or digest(path) != checksum:
            raise ValueError("review package content changed")
    proposal = json.loads((package / "source-proposals.json").read_text(encoding="utf-8"))
    if proposal.get("protocol") != "legacy-identity-review-v2":
        raise ValueError("use the registry-aware v2 review package; initial proposal is superseded")
    sources = proposal["entries"]
    registrations = json.loads((package / "original-registrations.json").read_text(encoding="utf-8"))
    registered = {r["filename"]: r for r in registrations}
    if len(registered) != len(registrations):
        raise ValueError("original domain registrations have ambiguous filenames")
    metadata = json.loads((package / "raw/meta.json").read_text(encoding="utf-8"))
    original_meta = json.loads((package / "original-meta.json").read_text(encoding="utf-8"))
    mapping = json.loads((package / "source-map.pending.json").read_text(encoding="utf-8"))["entries"]
    mapped = {e["filename"]: e for e in mapping}
    if len(mapped) != len(mapping) or set(mapped) != set(metadata) or set(original_meta) != set(metadata):
        raise ValueError("source mapping or original metadata roster mismatch")
    for entry in sources:
        check_approval(entry, required=require_approved)
        name = entry["filename"]
        if Path(name).name != name or digest(package / "raw" / name) != entry["sha256"]:
            raise ValueError("source bytes changed")
        if live_raw is not None and digest(Path(live_raw) / name) != entry["sha256"]:
            raise ValueError("live original source changed since review")
        if metadata[name]["status"] != "draft" or metadata[name]["review_status"] != "pending":
            raise ValueError("review preparation cannot promote content")
        expected_meta = copy.deepcopy(entry["original_scope_metadata"])
        if expected_meta != original_meta[name]:
            raise ValueError("original scope metadata binding changed")
        expected_meta.update(status="draft", review_status="pending", source_id="", asset_id="")
        if metadata[name] != expected_meta:
            raise ValueError("candidate review metadata changed access or provenance")
        expected_map = {"filename": name, "sha256": entry["sha256"], "tenant_id": entry["tenant_id"],
                        "source_system": entry["source_system"], "source_id": entry["proposed_source_id"]}
        check_approval(mapped[name], required=require_approved)
        if {k: v for k, v in mapped[name].items() if k != "approval"} != expected_map:
            raise ValueError("proposed registration and pending source map disagree")
    if (len(sources) != len(metadata) or {e["filename"] for e in sources} != set(metadata)
            or len({e["proposed_asset_id"] for e in sources}) != len(sources)):
        raise ValueError("source proposal roster mismatch")
    declared_groups = defaultdict(list)
    for entry in sources:
        registration = entry.get("existing_registration")
        if registration != registered.get(entry["filename"]):
            raise ValueError("existing domain registration snapshot binding changed")
        if registration:
            sid, asset, _ = propose_identity(entry["filename"], entry["sha256"], entry["original_scope_metadata"], registration)
            if (entry["proposed_source_id"], entry["proposed_asset_id"]) != (sid, asset):
                raise ValueError("existing registered identity cannot be renamed")
        declared_groups[(entry["tenant_id"], entry["source_system"], entry["proposed_source_id"])].append(entry)
    for group in declared_groups.values():
        if len(group) > 1 and any(not e.get("existing_registration") for e in group):
            raise ValueError("new opaque identity allocations cannot silently merge files")
    if live_raw is not None:
        inputs = json.loads((package / "inputs.json").read_text(encoding="utf-8"))
        if digest(Path(live_raw) / "meta.json") != inputs["meta_sha256"]:
            raise ValueError("live original metadata changed since review")
    return {"binding_valid": True, "sources": len(sources), "approved": sum(e["approval"]["status"] == "approved" for e in sources),
            "production_ingestion_authorized": False}


def build(settings, raw, manifest_path, output):
    from pymilvus import MilvusClient
    from scripts.migrate_legacy_collections import read_rows, fingerprint
    database = sa.engine.make_url(settings.mysql_dsn).database
    if settings.app_env != "dev" or database != "rag_kb" or (settings.doc_collection, settings.faq_collection) != ("doc_collection", "faq_collection"):
        raise ValueError("requires explicit original runtime read profile")
    raw, output = Path(raw).resolve(), Path(output).resolve()
    if output.exists():
        raise ValueError("refuse package overwrite")
    metadata = json.loads((raw / "meta.json").read_text(encoding="utf-8"))
    manifest = json.loads(Path(manifest_path).read_text(encoding="utf-8"))
    version = manifest["dataset_version"]
    engine = sa.create_engine(settings.mysql_dsn)
    with engine.connect() as conn:
        versions = [dict(r) for r in conn.execute(sa.text("SELECT version,status FROM kb_versions ORDER BY version")).mappings()]
        if [r["version"] for r in versions if r["status"] == "ACTIVE"] != [version]:
            raise ValueError("raw manifest is not original ACTIVE")
        records = [dict(r) for r in conn.execute(sa.text(
            "SELECT filename,file_hash,asset_id,doc_type,status,chunk_count FROM ingest_records WHERE kb_version=:v ORDER BY filename"), {"v": version}).mappings()]
        registrations = [dict(r) for r in conn.execute(sa.text(
            "SELECT asset_id,tenant_id,filename,source_system,source_id,source_version,status,kb_version,checksum,reviewed_by "
            "FROM knowledge_assets WHERE kb_version=:v ORDER BY asset_id"), {"v": version}).mappings()]
    engine.dispose()
    roster = {r["filename"]: r for r in records}
    declared = {r["filename"]: r for r in manifest["files"]}
    if len(roster) != len(records) or set(roster) != set(metadata) or set(declared) != set(metadata):
        raise ValueError("raw metadata, manifest and ACTIVE ledger roster disagree")
    actual_raw_files = {p.name for p in raw.iterdir() if p.is_file() and p.name != "meta.json"}
    if actual_raw_files != set(metadata):
        raise ValueError("unlisted raw files cannot silently enter review")
    for name in metadata:
        path = (raw / name).resolve()
        if Path(name).name != name or path.parent != raw or not path.is_file():
            raise ValueError("source file missing or outside the declared raw root")
        checksum = digest(raw / name)
        if roster[name]["status"] != "done" or checksum != roster[name]["file_hash"] or checksum != declared[name]["sha256"]:
            raise ValueError("ACTIVE ledger/manifest/source bytes disagree")
    registered = {}
    for registration in registrations:
        name = registration["filename"]
        if name not in metadata or name in registered:
            raise ValueError("domain registration roster is ambiguous or outside ACTIVE sources")
        propose_identity(name, digest(raw / name), metadata[name], registration)
        registered[name] = registration
    client = MilvusClient(uri=settings.milvus_uri, timeout=120)
    active, collection_audit = [], []
    try:
        for kind, name in (("doc", settings.doc_collection), ("faq", settings.faq_collection)):
            all_rows = read_rows(client, name)
            selected = [r for r in all_rows if r["kb_version"] == version]
            collection_audit.append({"collection": name, "all_version_rows": len(all_rows), "active_rows": len(selected),
                                     "original_rows_and_vectors_sha256": fingerprint(all_rows)})
            active.extend((kind, r) for r in selected)
        traces, expected_counts = trace_rows(raw, metadata, active)
        # Re-read actual originals; a changed source invalidates this audit.
        for audit in collection_audit:
            if audit["original_rows_and_vectors_sha256"] != fingerprint(read_rows(client, audit["collection"])):
                raise ValueError("original vector snapshot changed during audit")
    finally:
        client.close()
    source_entries, mapping_entries, staged_meta = [], [], copy.deepcopy(metadata)
    matched_counts = Counter(t.get("filename") for t in traces if t["status"] == "exact_trace")
    for name, meta in sorted(metadata.items()):
        source_id, asset_id, basis = propose_identity(name, digest(raw / name), meta, registered.get(name))
        entry = bind_pending({"filename": name, "sha256": digest(raw / name), "tenant_id": meta["tenant_id"],
            "source_system": meta.get("source_system", ""), "proposed_source_id": source_id,
            "proposed_asset_id": asset_id, "existing_source_version": meta.get("source_version", ""),
            "existing_registration": registered.get(name),
            "original_scope_metadata": meta, "legacy_ledger": roster[name],
            "parsed_chunks": expected_counts[name], "exact_traced_rows": matched_counts[name],
            "registration_status": "proposal_only", "legacy_aliases_approved": False,
            "allocation_basis": basis})
        source_entries.append(entry)
        mapping_entries.append(bind_pending({"filename": name, "sha256": entry["sha256"], "tenant_id": meta["tenant_id"],
            "source_system": entry["source_system"], "source_id": entry["proposed_source_id"]}))
        staged_meta[name].update(status="draft", review_status="pending", source_id="", asset_id="")
    mismatches = [{"filename": name, "parsed": expected_counts[name], "ledger": roster[name]["chunk_count"],
                   "exact_traced": matched_counts[name]} for name in sorted(metadata)
                  if expected_counts[name] != roster[name]["chunk_count"] or matched_counts[name] != roster[name]["chunk_count"]]
    grouped = defaultdict(list)
    for entry in source_entries:
        grouped[(entry["tenant_id"], entry["source_system"], entry["proposed_source_id"])].append(entry["filename"])
    shared_identities = [{"tenant_id": k[0], "source_system": k[1], "source_id": k[2], "filenames": names,
        "approval_status": "pending_withdrawal_group_review"} for k, names in grouped.items() if len(names) > 1]
    summary = {"protocol": "legacy-identity-review-v2", "original_active_version": version,
        "source_files": len(metadata), "source_types": dict(Counter(r["doc_type"] for r in records)),
        "active_rows": len(active), "trace_status": dict(Counter(t["status"] for t in traces)),
        "file_count_mismatches": mismatches, "original_collections": collection_audit,
        "scope_metadata_promoted": False, "approved_sources": 0, "newly_registered_sources": 0,
        "existing_declared_domain_registrations": len(registrations), "new_identity_proposals": len(metadata) - len(registrations),
        "shared_existing_source_ids": shared_identities, "existing_reviewers_are_human_approval": False,
        "db_writes": 0, "vector_writes": 0, "paid_llm_calls": 0, "active_changed": False,
        "blocks": ["all identities/content/scope pending human registration review", "no candidate import or release authorized", "opaque proposed IDs require registry confirmation"]}
    output.mkdir(parents=True)
    (output / "raw").mkdir()
    for name in metadata:
        (output / "raw" / name).write_bytes((raw / name).read_bytes())
    artifacts = {"original-meta.json": metadata, "original-manifest.json": manifest, "original-registrations.json": registrations,
        "raw/meta.json": staged_meta, "source-proposals.json": {"protocol": "legacy-identity-review-v2", "entries": source_entries},
        "source-map.pending.json": {"protocol": "reviewed-source-map-v1", "entries": mapping_entries},
        "row-trace.json": {"version": version, "rows": traces}, "review-summary.json": summary,
        "inputs.json": {"raw_root": str(raw), "meta_sha256": digest(raw / "meta.json"), "manifest_sha256": digest(manifest_path), "versions_before": versions}}
    for name, value in artifacts.items():
        (output / name).write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    hashes = {str(p.relative_to(output)).replace("\\", "/"): digest(p) for p in output.rglob("*") if p.is_file()}
    (output / "artifact-hashes.json").write_text(json.dumps(hashes, sort_keys=True, indent=2) + "\n", encoding="utf-8")
    validate_package(output, live_raw=raw)
    return summary


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--raw-dir")
    parser.add_argument("--manifest")
    parser.add_argument("--output")
    parser.add_argument("--validate-package")
    parser.add_argument("--require-approved", action="store_true")
    args = parser.parse_args()
    if args.validate_package:
        result = validate_package(args.validate_package, require_approved=args.require_approved, live_raw=args.raw_dir)
    else:
        if not args.raw_dir or not args.manifest or not args.output or args.require_approved:
            parser.error("building needs raw-dir, manifest and a new output; it cannot approve")
        from qa_core.config.settings import get_settings
        result = build(get_settings(), args.raw_dir, args.manifest, args.output)
    print(json.dumps(result, ensure_ascii=False))


if __name__ == "__main__":
    main()
