"""Content-bound gate for the four approved summaries, in a dedicated test DB only.

No alarm-recall claim is made for a manual-only corpus. This profile cannot
publish to the original database or substitute for the operational gate.
"""
import hashlib
import copy
import json
import re
from pathlib import Path

import sqlalchemy as sa
from pymilvus import MilvusClient

PROFILE = "approved-protocol-dev-v1"
ROOT = Path(__file__).resolve().parents[2]
PACKAGE = ROOT / "data/candidates/stage4-protocol-approved-r2"


def validate_scope(settings):
    database = sa.engine.make_url(settings.mysql_dsn).database or ""
    match = re.fullmatch(r"test_step5_([a-f0-9]{12})", database)
    if (settings.app_env != "test" or not match
            or settings.doc_collection != database + "_doc"
            or settings.faq_collection != database + "_faq"):
        raise ValueError("协议摘要门禁仅允许独立 test_step5 数据库和集合")


def approved_sources():
    from scripts.review_core_semantics import verify_hashes
    from qa_core.quality.eval_review import validate_review_payload
    verify_hashes(PACKAGE)
    receipt_bytes = (ROOT / "docs/STAGE4_PROTOCOL_APPROVAL_2026-09-27.json").read_bytes()
    receipt = json.loads(receipt_bytes)
    if (receipt != json.loads((PACKAGE / "approval-receipt.json").read_text(encoding="utf-8"))
            or receipt.get("decision") != "approved"
            or receipt.get("scope") != "four_protocol_summaries_and_eight_dev_cases_isolated_development_only"):
        raise ValueError("缺少内容绑定的隔离开发批准")
    review = json.loads((PACKAGE / "approved-review.json").read_text(encoding="utf-8"))
    validate_review_payload(review, require_approved=True)
    from scripts.build_protocol_eval_review import build
    proposal_root = ROOT / "data/candidates/stage4-protocol-notes-r1"
    proposal_bytes = (proposal_root / "review-package.json").read_bytes()
    proposal = json.loads(proposal_bytes)
    if hashlib.sha256(proposal_bytes).hexdigest() != receipt["package_sha256"] or proposal != build(proposal_root):
        raise ValueError("原批准提案正文已变化")
    expected = copy.deepcopy(proposal)
    evidence = "Explicit user approval: " + receipt["user_message"] + "; receipt_sha256=" + hashlib.sha256(receipt_bytes).hexdigest()
    for item in expected["sources"] + expected["cases"]:
        item["approval"].update(status="approved", reviewer=receipt["reviewer"], proposer=receipt["proposer"], evidence=evidence)
    if review != expected:
        raise ValueError("批准资料与原提案范围不一致")
    meta = json.loads((PACKAGE / "raw/meta.json").read_text(encoding="utf-8"))
    return meta, hashlib.sha256(receipt_bytes).hexdigest()


def validate_rows(rows, records, meta):
    """Allow an approved subset for rollback; require one complete chunk per summary."""
    from scripts.rebuild_kb import normalize_text, _resanitize, TEXT_CHUNKERS
    from qa_core.indexing.parsers.parsers import parse_dispatch
    if not rows or len(rows) > 4 or len(rows) != len(records):
        raise ValueError("必须是已批准摘要的非空子集")
    expected = {r["filename"]: r for r in records}
    if len(expected) != len(records) or {r["source"] for r in rows} != set(expected):
        raise ValueError("来源与入库账本不一致")
    for row in rows:
        name = row["source"]
        if name not in meta:
            raise ValueError("包含未批准来源")
        raw = (PACKAGE / "raw" / name).read_bytes()
        record = expected[name]
        if (record["file_hash"] != hashlib.sha256(raw).hexdigest()
                or record["status"] != "done" or record["chunk_count"] != 1
                or not record["asset_id"] or record["asset_id"] != row["asset_id"]):
            raise ValueError("正文或入库身份不匹配批准摘要")
        for key in ("tenant_id", "dept_id", "visibility", "allowed_roles", "role_scope",
                    "security_level", "source_system", "source_id", "status", "origin_type",
                    "synthetic", "review_status", "license_status", "evidence_refs", "doc_type"):
            if row.get(key) != meta[name].get(key):
                raise ValueError("来源范围或审核元数据改变: " + key)
        # These summaries fit one chunk; compare stored normalized content, not just filenames.
        chunks = TEXT_CHUNKERS["manual"](parse_dispatch("manual", PACKAGE / "raw" / name))
        if len(chunks) != 1:
            raise ValueError("批准摘要不再满足单片段假设")
        expected_text = normalize_text(_resanitize(chunks[0]).text)
        if row["text"].strip() != expected_text.strip() or not row["source_version"]:
            raise ValueError("索引正文不匹配已批准摘要")


def verify_profile(settings, version, report=None):
    validate_scope(settings)
    meta, receipt_sha = approved_sources()
    engine = sa.create_engine(settings.mysql_dsn)
    client = MilvusClient(uri=settings.milvus_uri, timeout=60)
    try:
        with engine.connect() as conn:
            records = [dict(r) for r in conn.execute(sa.text(
                "SELECT filename,file_hash,status,chunk_count,asset_id FROM ingest_records WHERE kb_version=:v"),
                {"v": version}).mappings()]
        rows = client.query(collection_name=settings.doc_collection, filter=f'kb_version == "{version}"',
                            output_fields=["*"], limit=10, consistency_level="Strong")
        faq = client.query(collection_name=settings.faq_collection, filter=f'kb_version == "{version}"',
                           output_fields=["id"], limit=1, consistency_level="Strong")
        if faq:
            raise ValueError("摘要门禁不允许 FAQ 或工单混入")
        validate_rows(rows, records, meta)
        if report is not None and report.get("approval_receipt_sha256") != receipt_sha:
            raise ValueError("批准收据绑定已变化")
        return {"receipt_sha256": receipt_sha, "sources": len(rows)}
    finally:
        client.close()
        engine.dispose()


def run_protocol_gate(settings, version):
    from qa_core.ingest.write_guard import candidate_write
    from qa_core.storage.version_manifest import bind_candidate, VersionManifestDao
    from qa_core.quality.publication import capture_snapshot
    from qa_core.security.withdrawals import assert_publishable_sources
    from qa_core.quality.gate import GateResult, check_sop_integrity, check_duplicates, check_desensitize
    validate_scope(settings)
    with candidate_write(version, settings=settings):
        bind_candidate(settings, version)
        before = capture_snapshot(settings, version)
        assert_publishable_sources(settings, before["sources"])
        approved = verify_profile(settings, version)
        result = GateResult(profile=PROFILE, approval_receipt_sha256=approved["receipt_sha256"])
        result.add("alarm_recall", True, {"applicable": False, "sample": 0, "hit_rate": None,
                   "reason": "已批准的纯协议摘要子集，无告警工单；不声称告警召回通过"})
        result.add("meta_completeness", True, {"sources": approved["sources"],
                   "reason": "逐来源核对正文、审批、身份和完整访问范围；无设备适用性声明"})
        client = MilvusClient(uri=settings.milvus_uri, timeout=60)
        try:
            for name, check in (("sop_integrity", check_sop_integrity), ("duplicates", check_duplicates),
                                ("desensitize", check_desensitize)):
                ok, detail = check(client, version, settings.doc_collection)
                result.add(name, ok, detail)
            after = capture_snapshot(settings, version, client)
        finally:
            client.close()
        result.passed = result.passed and before == after
        if before == after:
            result.snapshot = after
            dao = VersionManifestDao(settings.mysql_dsn)
            try:
                dao.record_snapshot(version, after)
            finally:
                dao.close()
        return result
