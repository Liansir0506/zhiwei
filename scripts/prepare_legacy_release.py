"""Prepare a reviewable, read-only first-release plan for the approved test candidate."""
import argparse
from collections import Counter
from datetime import datetime, timezone
import html
import json
import os
from pathlib import Path

import sqlalchemy as sa
from qa_core.config.settings import get_settings
from qa_core.quality.publication import _digest, verify_publication
from scripts.prepare_legacy_projection import digest, read, validate_projection, write
from scripts.stage_legacy_candidate import configure, protected_snapshot, validate_base, validate_target
from scripts.migrate_legacy_collections import fingerprint, read_rows


def report_parent(stage, report_path):
    path = Path(report_path).resolve()
    if (not path.is_relative_to(stage.resolve()) or not path.is_file()
            or path.name != "quality_test-legacy-demo-r1.json"):
        raise ValueError("requires the exact bound ordinary gate report inside this candidate stage")
    return path.parent


def require_first_release(versions, jobs, enabled_keys):
    if versions != [{"version": "test-legacy-demo-r1", "status": "STAGED"}]:
        raise ValueError("requires this single STAGED version; first-release plan cannot adopt another pointer")
    if jobs or enabled_keys:
        raise ValueError("requires no unfinished jobs or existing enabled API keys")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--stage-report", required=True)
    parser.add_argument("--report-dir", required=True)
    args = parser.parse_args()
    stage = Path(args.stage_report).resolve(); root = Path(args.report_dir).resolve()
    environment = read(stage / "environment.json")
    candidate = Path(environment["candidate"]).resolve()
    if (root.exists() or "test" not in str(root).lower() or root.is_relative_to(candidate)
            or environment["scope"] != "isolated_local_demo_only"
            or environment["version"] != "test-legacy-demo-r1"
            or environment["candidate_artifacts_sha256"] != digest(candidate / "artifact-hashes.json")):
        raise ValueError("requires exact approved candidate and a new separate test report")
    validate_projection("data/candidates/legacy-identity-review-r2", "data/candidates/legacy-group-review-r2", candidate)
    base = get_settings(); validate_base(base)
    shared_before = protected_snapshot(base)
    settings = configure(base, stage, environment["database"])
    if (settings.doc_collection != environment["doc_collection"]
            or settings.faq_collection != environment["faq_collection"]):
        raise ValueError("collection binding mismatch")
    engine = sa.create_engine(settings.mysql_dsn, pool_pre_ping=True)
    try:
        with engine.connect() as conn:
            locked = conn.execute(sa.text("SELECT GET_LOCK('rag_kb_publication',0)")).scalar(); conn.commit()
            if locked != 1:
                raise ValueError("publication/ingestion busy; retry without changing the candidate")
            try:
                versions = [dict(r) for r in conn.execute(sa.text("SELECT version,status FROM kb_versions ORDER BY version")).mappings()]
                report = conn.execute(sa.text("SELECT quality_report_path FROM kb_versions WHERE version=:v"),
                                      {"v": environment["version"]}).scalar_one()
                jobs = conn.execute(sa.text("SELECT COUNT(*) FROM ingest_jobs WHERE status NOT IN ('success','failed','cancelled')")).scalar_one()
                enabled_keys = conn.execute(sa.text("SELECT COUNT(*) FROM api_keys WHERE enabled=1")).scalar_one()
                withdrawals = conn.execute(sa.text("SELECT COUNT(*) FROM knowledge_source_withdrawals")).scalar_one()
                require_first_release(versions, jobs, enabled_keys)
                if withdrawals:
                    raise ValueError("first-release plan requires an untouched withdrawal ledger")
                # reports_dir is a runtime path, not a collection binding. Use the
                # existing gate's validated directory; never copy or rebind a report.
                os.environ["REPORTS_DIR"] = str(report_parent(stage, report))
                get_settings.cache_clear(); settings = get_settings(); validate_target(settings, environment["database"])
                verified_report = verify_publication(settings, environment["version"], report)
                from pymilvus import MilvusClient
                client = MilvusClient(uri=settings.milvus_uri, timeout=120)
                try:
                    docs = read_rows(client, settings.doc_collection)
                    faqs = read_rows(client, settings.faq_collection)
                finally:
                    client.close()
                repaired = read(stage / "network-label-repair/summary.json")
                if (fingerprint(docs) != repaired["doc_fingerprint"]
                        or fingerprint(faqs) != repaired["after_faq_fingerprint"]):
                    raise ValueError("candidate content/vector/access metadata changed since approved validation")
                meta = read(candidate / "raw/meta.json")
                groups = Counter(m["doc_type"] for m in meta.values())
                if len(docs) != 3390 or len(faqs) != 1000 or groups != {"faq": 1, "fault_case": 15, "guide": 40, "sop": 200}:
                    raise ValueError("requires the exact 256-file/4390-chunk candidate")
                if [dict(r) for r in conn.execute(sa.text("SELECT version,status FROM kb_versions ORDER BY version")).mappings()] != versions:
                    raise ValueError("version state changed during planning")
            finally:
                conn.execute(sa.text("SELECT RELEASE_LOCK('rag_kb_publication')")); conn.commit()
    finally:
        engine.dispose()
    shared_after = protected_snapshot(base)
    if shared_before != shared_after:
        raise ValueError("shared test default knowledge changed during planning")
    root.mkdir(parents=True)
    limitation = "200份旧模拟SOP缺审批/回滚声明，整来源可读但处置流程仍missing_safety_controls；不修改正文或关闭安全拒绝。"
    plan = {"prepared_at_utc": datetime.now(timezone.utc).isoformat(), "scope": "isolated_local_demo_only",
        "version": environment["version"], "database": environment["database"],
        "doc_collection": settings.doc_collection, "faq_collection": settings.faq_collection,
        "candidate_artifacts_sha256": environment["candidate_artifacts_sha256"],
        "approval_receipt_sha256": digest(candidate / "approval-receipt.json"),
        "gate_report": report, "gate_report_sha256": digest(Path(report)),
        "snapshot_sha256": _digest(verified_report["snapshot"]),
        "gate_verified_live": True, "files": 256, "chunks": 4390, "groups": dict(groups),
        "status_before": "STAGED", "active_before": None, "rollback_target": None,
        "has_existing_rollback_target": False, "enabled_keys_before": enabled_keys,
        "unfinished_jobs": jobs, "withdrawn_sources": withdrawals,
        "publication_authorized": False, "published": False,
        "proposed_action": {"endpoint": "/kb/activate", "method": "POST", "body": {"version": environment["version"]}},
        "post_release_acceptance": ["unique ACTIVE in this isolated database only", "authenticated ops can read all four approved source groups",
            "no-key 401 and foreign-tenant 404", "full-source SOP safety refusal preserved", "knowledge/vector/access fingerprint unchanged",
            "authenticated actor audit and cache invalidation result recorded", "temporary HTTP server and keys removed after verification"],
        "fallback": "首次发布无上一ACTIVE/ARCHIVED目标；异常时停止临时演示入口并禁用临时Key，不称为版本回滚。不手工改库状态或删除撤回账本。",
        "withdrawal_and_rollback_rehearsal": "only disposable publication sandbox fixtures; do not withdraw actual candidate sources to rehearse",
        "content_limitation": limitation, "pending_reference_files": 20,
        "pending_other_sources": ["reference-card department/conflicting shared-source withdrawal", "BFD/NETCONF", "Lab"],
        "original_storage_accessed": False, "shared_test_knowledge_unchanged": True,
        "candidate_knowledge_matches_validated_fingerprints": True, "candidate_reembedded": False,
        "paid_llm_calls": 0, "independent_quality_verified": False}
    write(root / "release-plan.json", plan)
    plan_hash = digest(root / "release-plan.json")
    write(root / "summary.json", {"prepared": True, "status": "STAGED", "published": False,
        "plan_sha256": plan_hash, "gate_verified_live": True, "rollback_target": None,
        "candidate_knowledge_unchanged": True, "shared_test_knowledge_unchanged": True,
        "paid_llm_calls": 0, "publication_authorization_pending": True})
    rows = ''.join(f'<tr><td>{html.escape(k)}</td><td>{v}</td></tr>' for k, v in groups.items())
    page = f'''<!doctype html><html lang="zh-CN"><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>隔离模拟候选首次发布审核</title><style>body{{font:16px/1.7 system-ui,sans-serif;max-width:960px;margin:auto;padding:24px;color:#17243a;background:#f6f8fc}}section{{background:white;padding:20px;border:1px solid #ccd4df;border-radius:12px;margin:16px 0}}code{{overflow-wrap:anywhere}}table{{border-collapse:collapse;width:100%}}td,th{{padding:8px;border-bottom:1px solid #dae0e8;text-align:left}}.notice{{border-left:5px solid #b77c00}}a{{color:#175dc5}}</style>
<h1>隔离模拟候选首次发布审核</h1><p>准备完成 · 当前仍STAGED · 尚未发布 · 仅限隔离本地演示</p>
<section><h2>拟执行的具体操作</h2><p>仅在数据库 <code>{html.escape(environment['database'])}</code> 将 <code>test-legacy-demo-r1</code> 从STAGED激活为该库唯一ACTIVE。</p><p>集合：<code>{html.escape(settings.doc_collection)}</code> / <code>{html.escape(settings.faq_collection)}</code>。原库、共享test ACTIVE及默认集合均不切换。</p><pre>POST /kb/activate\n{{"version":"test-legacy-demo-r1"}}</pre><p>服务仅临时监听127.0.0.1，API Key仅在内存中；验收后关闭服务、删除临时Key。</p><table><tr><th>已批准组</th><th>文件数</th></tr>{rows}</table><p>共256文件 / 4,390片段；原文、向量、身份和权限指纹与已验候选一致。五项普通门禁已在线重验绑定及数据快照，使用原有通过报告，不沿用未经绑定的历史分数。</p></section>
<section class="notice"><h2>内容与回退限制</h2><p>{html.escape(limitation)}</p><p>当前无ACTIVE或ARCHIVED版本，首次发布后没有现成版本可回滚。异常时停止演示入口并禁用临时Key，这是入口停用，不是版本回滚。</p><p>撤回与回滚演练仅用可清理的临时沙箱夹具，不撤回此真实候选来源，不伪造上一版本。20份参考卡、部门/共同撤回关系、BFD/NETCONF和Lab继续pending。独立frozen仍0，不作独立质量结论。</p></section>
<section><h2>发布后的验收</h2><p>四类已批准原文：ops200；无Key401；外租户404。验证唯一ACTIVE、SOP安全拒绝、原文/向量/权限不变、认证用户审计和缓存失效。临时服务和Key在验收后清理。无付费LLM请求，不重新嵌入候选。</p><p><a href="release-plan.json">完整计划JSON</a> · <a href="summary.json">只读核对结果</a></p><p>计划SHA256：<code>{plan_hash}</code></p><p>本页没有执行按钮；实际发布需本聊天对该具体范围的明确确认，执行前再验快照。</p></section></html>'''
    (root / "review.html").write_text(page, encoding="utf-8")
    print(json.dumps({"prepared": True, "published": False, "plan_sha256": plan_hash, "rollback_target": None}))


if __name__ == "__main__":
    main()
