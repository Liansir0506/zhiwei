"""Copy legacy rows to new full-schema collections, quarantining missing identities.

The old collections and ACTIVE pointer remain untouched. The new collections
are an inspectable migration candidate, never an approved knowledge release.
"""
import argparse
import hashlib
import json
import re
from pathlib import Path

from pymilvus import MilvusClient
from qa_core.config.settings import get_settings
from scripts.init_collections import build


def read_rows(client, name):
    fields = [f["name"] for f in client.describe_collection(collection_name=name)["fields"] if f["name"] != "sparse"]
    it = client.query_iterator(collection_name=name, filter="", output_fields=fields, batch_size=300,
                               consistency_level="Strong")
    rows = []
    try:
        while batch := it.next():
            rows.extend(batch)
    finally:
        it.close()
    return sorted(rows, key=lambda row: row["id"])


def fingerprint(rows):
    import numpy as np
    h = hashlib.sha256()
    for row in rows:
        meta = {k:v for k,v in row.items() if k != "dense"}
        h.update(json.dumps(meta, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode())
        h.update(np.asarray(row["dense"], dtype="<f4").tobytes())
    return h.hexdigest()


def adapt(row):
    new = dict(row)
    new.update(dept_id="", role_scope=list(row.get("allowed_roles") or []), security_level="internal",
               device_type="", source_system="legacy_unregistered", source_id="", source_version="",
               effective_from="", effective_to="", status="draft", ticket_id="", cmdb_id="", change_id="",
               asset_id="", origin_type="legacy", synthetic=False, generator_version="", generation_seed="",
               review_status="pending", license_status="unknown", evidence_refs=[])
    # Preserve any governance already present; never infer approval or registered IDs.
    new.update({k:v for k,v in row.items() if k in new})
    if not new["source_id"] or not new["asset_id"]:
        new["status"], new["review_status"] = "draft", "pending"
    return new


def migrate(settings, targets, report):
    names = (settings.doc_collection, settings.faq_collection)
    if len(set(names + tuple(targets))) != 4 or any(not re.fullmatch(r"[A-Za-z][A-Za-z0-9_]{1,127}", n) for n in targets):
        raise ValueError("new, distinct collection names required")
    report = Path(report)
    if report.exists():
        raise ValueError("refuse report overwrite")
    client = MilvusClient(uri=settings.milvus_uri, timeout=120)
    try:
        if any(client.has_collection(n) for n in targets):
            raise ValueError("target exists; inspect partial migration, never overwrite")
        result = {"active_switched": False, "legacy_deleted": False, "collections": []}
        for old, new, faq in zip(names, targets, (False, True)):
            before = read_rows(client, old)
            build(client, new, faq)
            transformed = [adapt(row) for row in before]
            for i in range(0, len(transformed), 100):
                client.insert(collection_name=new, data=transformed[i:i+100])
            client.flush(new)
            after = read_rows(client, new)
            preserved = [{key:row[key] for key in before[0]} for row in after] if before else []
            unchanged = fingerprint(before) == fingerprint(read_rows(client, old))
            if not unchanged or fingerprint(before) != fingerprint(preserved):
                raise RuntimeError("migration changed existing fields or vectors")
            result["collections"].append({"source": old, "target": new, "rows": len(after),
                "source_sha256": fingerprint(before), "source_unchanged": unchanged,
                "existing_fields_and_vectors_preserved": True,
                "pending_identity_rows": sum(not row["asset_id"] or not row["source_id"] for row in after)})
        report.parent.mkdir(parents=True, exist_ok=True)
        report.write_text(json.dumps(result, ensure_ascii=False, indent=2)+"\n", encoding="utf-8")
        return result
    finally:
        client.close()


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--doc", required=True); p.add_argument("--faq", required=True)
    p.add_argument("--report", required=True); p.add_argument("--execute", action="store_true")
    args = p.parse_args()
    if not args.execute:
        raise SystemExit("confirm the runtime profile and use --execute")
    print(json.dumps(migrate(get_settings(), (args.doc, args.faq), args.report), ensure_ascii=False))


if __name__ == "__main__": main()
