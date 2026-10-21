"""Fail-closed publication binding; not a retrieval-quality evaluation or signature."""
from __future__ import annotations

import hashlib
import json
import re
from pathlib import Path

import sqlalchemy as sa
from pymilvus import MilvusClient
from google.protobuf.message import Message

CHECKS = {"alarm_recall", "sop_integrity", "meta_completeness", "duplicates", "desensitize"}
PROTOCOL = "publication-snapshot-v1"


def validate_version(version: str) -> None:
    if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.-]{0,31}", version):
        raise ValueError("版本标识不合法")


def _digest(value) -> str:
    def encode_special(obj):
        if isinstance(obj, Message):
            return {"protobuf": obj.DESCRIPTOR.full_name,
                    "hex": obj.SerializeToString(deterministic=True).hex()}
        if type(obj).__module__.startswith("google.") and hasattr(obj, "__len__"):
            return list(obj)
        if hasattr(obj, "tolist"):
            return obj.tolist()
        raise TypeError(f"unsupported snapshot value: {type(obj).__name__}")
    encoded = json.dumps(value, sort_keys=True, ensure_ascii=False, allow_nan=False,
                         separators=(",", ":"), default=encode_special).encode()
    return hashlib.sha256(encoded).hexdigest()


def capture_snapshot(settings, version: str, client=None) -> dict:
    """Scan every version row (including vectors/ACL), plus completed ingestion ledger.

    Bounded fail-closed at 100k chunks. Two captures detect ordinary concurrent changes;
    this is NOT an atomic cross-store snapshot or an ingestion write lock.
    """
    validate_version(version)
    owned = client is None
    client = client or MilvusClient(uri=settings.milvus_uri, timeout=60)
    collections = {}
    sources = set()
    try:
        for kind, name in (("doc", settings.doc_collection), ("faq", settings.faq_collection)):
            schema = client.describe_collection(collection_name=name)
            # Milvus advances this operational timestamp during collection/index
            # maintenance. Actual fields/functions/properties and collection ID
            # stay bound; unrelated version writes must not invalidate a snapshot.
            schema = {k: v for k, v in schema.items() if k != "update_timestamp"}
            generated = {name for fn in schema.get("functions", [])
                         if int(fn["type"]) == 1 for name in fn["output_field_names"]}
            if any(f.get("is_function_output") and f["name"] not in generated
                   for f in schema["fields"]):
                raise ValueError("尚不支持此集合的自动生成字段快照")
            # Milvus forbids raw reads of BM25 output. Bind its input text and
            # function/schema definition instead; dense embeddings remain hashed.
            fields = sorted(f["name"] for f in schema["fields"] if f["name"] not in generated)
            required = {"id", "kb_version", "asset_id", "source_version", "tenant_id",
                        "dept_id", "visibility", "allowed_roles", "role_scope",
                        "security_level", "device_type", "status", "effective_from", "effective_to"}
            if not required.issubset(fields):
                raise ValueError("集合缺少发布所需权限或生命周期字段")
            iterator = client.query_iterator(collection_name=name, batch_size=500,
                                             filter=f"kb_version == {json.dumps(version)}",
                                             output_fields=fields, consistency_level="Strong")
            hashes = {}
            try:
                while True:
                    rows = iterator.next()
                    if not rows:
                        break
                    for row in rows:
                        sources.add((row.get("tenant_id") or "default", row.get("source_system") or "", row.get("source_id") or ""))
                        identity = str(row["id"])
                        if identity in hashes or len(hashes) >= 100000:
                            raise ValueError("发布快照重复或超过安全上限")
                        hashes[identity] = _digest(row)
            finally:
                iterator.close()
            collections[kind] = {"name": name, "count": len(hashes),
                                 "sha256": _digest(sorted(hashes.items())),
                                 "schema_sha256": _digest(schema)}
    finally:
        if owned:
            client.close()
    engine = sa.create_engine(settings.mysql_dsn, pool_pre_ping=True)
    try:
        with engine.connect() as conn:
            records = [dict(row) for row in conn.execute(sa.text(
                "SELECT file_hash, filename, asset_id, doc_type, status, chunk_count "
                "FROM ingest_records WHERE kb_version=:v ORDER BY file_hash"),
                {"v": version}).mappings()]
    finally:
        engine.dispose()
    if not records or any(r["status"] != "done" or not r["asset_id"] for r in records):
        raise ValueError("入库记录为空、未完成或缺少资产身份")
    count = sum(c["count"] for c in collections.values())
    if count == 0 or count != sum(r["chunk_count"] or 0 for r in records):
        raise ValueError("集合数量与完成入库账本不一致")
    return {"protocol": PROTOCOL, "kb_version": version,
            "endpoint_sha256": _digest(settings.milvus_uri),
            "collections": collections, "ingest_sha256": _digest(records),
            "files": records, "sources": [list(source) for source in sorted(sources)]}


def load_bound_report(settings, version: str, report_path: str) -> dict:
    validate_version(version)
    root = Path(settings.reports_dir).resolve()
    path = Path(report_path).resolve()
    if not path.is_relative_to(root) or not path.is_file() or path.stat().st_size > 1024 * 1024:
        raise ValueError("报告不存在、越界或超过大小限制")
    try:
        report = json.loads(path.read_text(encoding="utf-8"))
    except (ValueError, OSError) as exc:
        raise ValueError("无法读取有效质量报告") from exc
    if not isinstance(report, dict) or report.get("kb_version") != version or report.get("passed") is not True:
        raise ValueError("质量报告未通过或版本不一致")
    checks = report.get("checks")
    if not isinstance(checks, list) or len(checks) != len(CHECKS) or any(
        not isinstance(c, dict) or c.get("passed") is not True for c in checks
    ) or {c.get("check") for c in checks} != CHECKS:
        raise ValueError("质量报告检查项不完整或未通过")
    if any(c.get("applicable") is False for c in checks):
        from qa_core.quality.protocol_gate import PROFILE
        if report.get("profile") != PROFILE:
            raise ValueError("不适用检查必须绑定明确的隔离摘要门禁范围")
    snapshot = report.get("snapshot")
    if not isinstance(snapshot, dict) or snapshot.get("protocol") != PROTOCOL:
        raise ValueError("旧报告缺少数据快照，需重新运行门禁")
    return report


def verify_publication(settings, version: str, report_path: str) -> dict:
    from qa_core.storage.version_manifest import verify_read_binding
    manifest = verify_read_binding(settings, version)
    if not manifest:
        raise ValueError("版本缺少持久化集合/处理配置摘要，需重新运行候选门禁")
    report = load_bound_report(settings, version, report_path)
    if report.get("profile"):
        from qa_core.quality.protocol_gate import PROFILE, verify_profile
        if report["profile"] != PROFILE:
            raise ValueError("未知质量门禁范围")
        verify_profile(settings, version, report)
    if manifest.get("snapshot") != report["snapshot"]:
        raise ValueError("报告不匹配持久化版本摘要，需重新运行门禁")
    current = capture_snapshot(settings, version)
    if current != report["snapshot"]:
        raise ValueError("集合绑定或数据快照已变化，需重新运行门禁")
    from qa_core.security.withdrawals import assert_publishable_sources
    assert_publishable_sources(settings, current.get("sources", []))
    return report
