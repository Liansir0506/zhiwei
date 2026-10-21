"""Read-only inventory/review validation; JSON to stdout, no database or source writes."""
import argparse
import hashlib
import json
from pathlib import Path
from qa_core.ingest.source_identity import validate_mapping


def inventory(root):
    root = Path(root)
    metadata = json.loads((root / "meta.json").read_text(encoding="utf-8"))
    entries = []
    for name, meta in sorted(metadata.items()):
        path = (root / name).resolve()
        if path.parent != root.resolve() or not path.is_file():
            raise ValueError("元数据文件越界或缺失")
        entries.append({"filename": name, "sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
                        "tenant_id": meta.get("tenant_id", ""),
                        "source_system": meta.get("source_system", ""), "source_id": meta.get("source_id", ""),
                        "approval": {"status": "pending", "proposer": "", "reviewer": "", "evidence": ""}})
    return {"protocol": "reviewed-source-map-v1", "entries": entries}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--raw-dir", required=True)
    parser.add_argument("--mapping", help="Validate manually reviewed mapping; omission prints pending inventory")
    args = parser.parse_args()
    if args.mapping:
        metadata = json.loads((Path(args.raw_dir) / "meta.json").read_text(encoding="utf-8"))
        result = validate_mapping(args.raw_dir, json.loads(Path(args.mapping).read_text(encoding="utf-8")), metadata)
        print(json.dumps({"valid": True, "files": len(result)}, ensure_ascii=False))
    else:
        print(json.dumps(inventory(args.raw_dir), ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
