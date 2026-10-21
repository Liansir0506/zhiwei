"""权限管理、运营监控、效果评估接口（/admin/*）——答辩展示的治理三件套。

权限模型：guest（只读演示）/ ops（运维工程师）/ auditor（质量审计）/ admin（平台管理）。
管理类接口用 require_roles 强制角色校验（403），问答类接口校验由 auth.py 负责。
"""
from __future__ import annotations

import hashlib
import json
import uuid
from datetime import datetime, timezone
from pathlib import Path

import sqlalchemy as sa
from fastapi import APIRouter, Depends, Header, HTTPException, Request, Response
from fastapi.responses import FileResponse
from pydantic import BaseModel, Field

from qa_core.api.auth import (AuthContext, create_api_key, get_auth_context,
                              hash_key, require_roles, require_global_admin)
from qa_core.config.settings import get_settings
from qa_core.ingest.write_guard import candidate_write
from qa_core.ingest.binding import execution_binding, version_binding, digest as binding_digest
from qa_core.security.policy import SECURITY_LEVELS
from qa_core.storage.connections import check_all

def _no_store(response: Response):
    response.headers['Cache-Control'] = 'no-store'


router = APIRouter(prefix="/admin", tags=["admin"], dependencies=[Depends(_no_store)])

# 角色-接口权限矩阵（答辩展示用：说明权限模型设计）
PERMISSION_MATRIX = [
    {"role": "guest", "desc": "只读演示，仅健康检查与文档",
     "apis": ["GET /health", "GET /docs"]},
    {"role": "ops", "desc": "运维工程师：问答、告警接入、反馈",
     "apis": ["POST /qa", "WS /qa/ws", "POST /alerts/webhook",
              "POST /feedback", "GET /kb/versions", "GET /domain/*/context"]},
    {"role": "auditor", "desc": "质量审计：只读治理数据与评测结果",
     "apis": ["GET /admin/overview", "GET /admin/ops", "GET /admin/eval",
              "GET /admin/kb", "GET /admin/permissions", "GET /metrics"]},
    {"role": "admin", "desc": "平台管理员：全部只读接口 + API Key 签发/吊销 + 版本激活回滚",
     "apis": ["auditor 全部", "POST /admin/keys", "DELETE /admin/keys/{hash}",
              "POST /kb/activate", "POST /kb/rollback"]},
]


def _engine():
    return sa.create_engine(get_settings().mysql_dsn)


def _snapshot():
    return {'at': datetime.now(timezone.utc).isoformat(),
            'scope': '当前数据库全局历史记录；不按知识版本或单个租户归组',
            'process_scope': '当前API进程计数，重启后重置',
            'hourly_scope': '数据库时钟最近24小时，包含所有消息角色'}


def _require_global_reader(ctx, roles=('admin', 'auditor', 'ops')):
    require_roles(ctx, roles)
    if ctx.tenant_id != 'default':
        raise HTTPException(403, '当前统计为全局口径，租户账号不可读取')


@router.get('/capabilities')
def admin_capabilities(response: Response, ctx: AuthContext = Depends(get_auth_context)):
    response.headers['Cache-Control'] = 'no-store'
    return {'roles': ctx.roles, 'tenant_id': ctx.tenant_id,
            'can_read_ops': ctx.tenant_id == 'default' and bool(set(ctx.roles)&{'ops','auditor','admin'}),
            'can_read_eval': ctx.tenant_id == 'default' and bool(set(ctx.roles)&{'auditor','admin'}),
            'can_read_permissions': bool(set(ctx.roles)&{'auditor','admin'}),
            'can_read_business': bool(set(ctx.roles)&{'ops','auditor','admin'}) and 'internal' in ctx.scope.security_levels,
            'can_manage_keys': 'admin' in ctx.roles}


def _overview() -> dict:
    settings = get_settings()
    out: dict = {"health": check_all(settings),
                 "llm": {"base_url": settings.llm_base_url, "model": settings.llm_model}}

    engine = _engine()
    with engine.begin() as conn:
        out["kb_versions"] = [dict(r) for r in conn.execute(sa.text(
            "SELECT version, status, published_at, activated_by FROM kb_versions "
            "ORDER BY version DESC")).mappings().all()]
        out["kb_active"] = next((v["version"] for v in out["kb_versions"]
                                 if v["status"] == "ACTIVE"), None)
        out["ingest_stats"] = [dict(r) for r in conn.execute(sa.text(
            "SELECT kb_version, doc_type, COUNT(*) files, SUM(chunk_count) chunks, "
            "SUM(status='failed') failed FROM ingest_records GROUP BY kb_version, doc_type "
            "ORDER BY kb_version, doc_type")).mappings().all()]
        out["bad_cases"] = {"open": conn.execute(sa.text(
            "SELECT COUNT(*) FROM bad_cases WHERE status='open'")).scalar(),
            "total": conn.execute(sa.text(
                "SELECT COUNT(*) FROM bad_cases")).scalar()}
        out["feedback"] = {str(r[0]): r[1] for r in conn.execute(sa.text(
            "SELECT rating, COUNT(*) FROM feedback GROUP BY rating")).all()}
        out["messages_total"] = conn.execute(sa.text(
            "SELECT COUNT(*) FROM messages")).scalar()
        out["sessions_total"] = conn.execute(sa.text(
            "SELECT COUNT(*) FROM sessions")).scalar()
        out["recent_bad_cases"] = [dict(r) for r in conn.execute(sa.text(
            "SELECT id, query_text, cause, created_at FROM bad_cases "
            "WHERE status='open' ORDER BY id DESC LIMIT 10")).mappings().all()]

    reports_dir = Path(settings.reports_dir)
    out["reports"] = sorted(p.name for p in reports_dir.glob("*.json")) if reports_dir.is_dir() else []
    from qa_core.api.route_qa import get_usecase

    try:
        llm = get_usecase().llm
        out["llm"].update({"calls": llm.total_calls,
                           "prompt_tokens": llm.total_prompt_tokens,
                           "completion_tokens": llm.total_completion_tokens})
    except Exception:  # noqa: BLE001
        pass
    out['snapshot'] = _snapshot()
    return out


@router.get("/overview")
def admin_overview(ctx: AuthContext = Depends(get_auth_context)) -> dict:
    _require_global_reader(ctx)
    return _overview()


@router.get("/overview/raw")
def admin_overview_raw(ctx: AuthContext = Depends(get_auth_context)) -> Response:
    _require_global_reader(ctx)
    return Response(content=json.dumps(_overview(), ensure_ascii=False, indent=2),
                    media_type="application/json")


@router.get("")
@router.get("/")
def admin_page() -> FileResponse:
    page = Path(__file__).resolve().parents[2] / "static" / "admin.html"
    return FileResponse(page)


# ================= 运营监控 =================

@router.get("/ops")
def admin_ops(ctx: AuthContext = Depends(get_auth_context)) -> dict:
    """运营监控聚合：请求分布、缓存命中、LLM 调用、最近趋势。"""
    _require_global_reader(ctx)
    engine = _engine()
    with engine.begin() as conn:
        route_dist = {str(r[0] or "unknown"): r[1] for r in conn.execute(sa.text(
            "SELECT route, COUNT(*) FROM messages WHERE role='assistant' "
            "GROUP BY route")).all()}
        hourly = [{"hour": str(r["h"]), "count": r["count"]} for r in conn.execute(sa.text(
            "SELECT DATE_FORMAT(created_at, '%m-%d %H:00') h, COUNT(*) count FROM messages "
            "WHERE created_at >= NOW() - INTERVAL 24 HOUR "
            "GROUP BY h ORDER BY h")).mappings().all()]
        intent_dist = {str(r[0] or "unknown"): r[1] for r in conn.execute(sa.text(
            "SELECT intent, COUNT(*) FROM messages WHERE role='assistant' "
            "GROUP BY intent")).all()}
        totals = {"messages": conn.execute(sa.text("SELECT COUNT(*) FROM messages")).scalar(),
                  "sessions": conn.execute(sa.text("SELECT COUNT(*) FROM sessions")).scalar(),
                  "bad_open": conn.execute(sa.text(
                      "SELECT COUNT(*) FROM bad_cases WHERE status='open'")).scalar(),
                  "feedback_up": conn.execute(sa.text(
                      "SELECT COUNT(*) FROM feedback WHERE rating='up'")).scalar(),
                  "feedback_down": conn.execute(sa.text(
                      "SELECT COUNT(*) FROM feedback WHERE rating='down'")).scalar()}

    # 缓存命中率来自 Searcher 全局 CacheLayer
    from qa_core.api.route_qa import get_usecase

    cache_stats = get_usecase().searcher.cache.stats()

    from qa_core.api.metrics import ALERT_TOTAL  # noqa: F401 触发注册
    try:
        from prometheus_client import REGISTRY

        llm_calls = REGISTRY.get_sample_value("qa_llm_calls_total") or 0
        qa_total = REGISTRY.get_sample_value("qa_requests_total") or 0
    except Exception:  # noqa: BLE001
        llm_calls, qa_total = 0, 0

    return {"route_distribution": route_dist, "intent_distribution": intent_dist,
            "hourly_messages": hourly, "totals": totals, "cache": cache_stats,
            "llm_calls": int(llm_calls), "qa_prometheus_total": int(qa_total), 'snapshot': _snapshot()}


# ================= 效果评估 =================

def _latest_eval_report() -> dict:
    from qa_core.quality.evaluation_workspace import reports
    active = _evaluation_active()
    indexed, _ = reports(get_settings(), active)
    current = [row for row in indexed if row['kb_version'] == active and row['kind'] == 'development_regression']
    if not current:
        return {"metrics": {}, "results": [], "file": None}
    data = current[0]
    data['report_modified_at'] = data['modified_at']
    return data


@router.get("/eval")
def admin_eval(ctx: AuthContext = Depends(get_auth_context)) -> dict:
    """效果评估：最新回归评测指标 + 分类统计 + 用例明细。"""
    _require_global_reader(ctx, ('admin','auditor'))
    data = _latest_eval_report()
    metrics = data.get("metrics", {})
    results = data.get("results", [])
    by_category = {}
    for r in results:
        c = by_category.setdefault(r.get("category") or '未分类', {"total": 0, "pass": 0, "evaluated": 0})
        c["total"] += 1
        values = [r.get(field) for field in ('route_ok', 'hit_ok', 'reject_ok', 'status_ok', 'body_ok')]
        if type(r.get('route_ok')) is bool and all(v is None or type(v) is bool for v in values):
            c['evaluated'] += 1
            c['pass'] += all(v is not False for v in values)
    for c in by_category.values():
        c["accuracy"] = round(c["pass"] / c["total"], 3) if c["total"] else 0.0
    checks = {}
    for field in ('route_ok', 'hit_ok', 'reject_ok', 'degraded'):
        values = [row[field] for row in results if type(row.get(field)) is bool]
        checks[field] = {'passed': sum(values), 'evaluated': len(values),
                         'ratio': sum(values)/len(values) if values else None}
    return {"metrics": metrics, "by_category": by_category,
            "results": results, "file": data.get("file"), 'checks': checks,
            'report': {'modified_at': data.get('report_modified_at'), 'run_id': data.get('run_id'),
                'kb_version': data.get('kb_version'), 'dataset': Path(data.get('dataset') or '').name,
                'purpose': '历史开发回归记录，非当前生产SLA或独立质量验收',
                'independent_evaluation': '未验证独立样本及冻结评测证明'},
            'snapshot': _snapshot()}


# ================= 知识库浏览数据 =================

def _evaluation_versions():
    engine = _engine()
    try:
        with engine.connect() as conn:
            return [dict(row) for row in conn.execute(sa.text(
                'SELECT version,status FROM kb_versions ORDER BY version DESC')).mappings()]
    finally:
        engine.dispose()


@router.get('/eval/reports')
def eval_reports(ctx: AuthContext = Depends(get_auth_context)):
    _require_global_reader(ctx, ('admin', 'auditor'))
    from qa_core.quality.evaluation_workspace import reports, recent_jobs, NOTE
    versions = _evaluation_versions()
    active = next((row['version'] for row in versions if row['status'] == 'ACTIVE'), None)
    indexed, warnings = reports(get_settings(), active)
    return {'active_version': active, 'versions': versions,
        'reports': [{k: v for k, v in row.items() if k not in ('results', 'by_category', 'gate_checks')} for row in indexed],
        'warnings': warnings, 'jobs': recent_jobs(), 'can_run': 'admin' in ctx.roles and not (ctx.dept_ids or ctx.device_types or ctx.security_levels),
        'independent_evaluation': NOTE, 'independent_verified': False,
        'workflow': '仅当前ACTIVE的1至40条ops可读FAQ原句自回归；先准备绑定计划，再执行零模型任务。独立评测需要另行完成未暴露样本、人工标注及冻结审核。',
        'snapshot': {'at': datetime.now(timezone.utc).isoformat(), 'scope': '按报告显式知识版本归属；无版本记录单独展示'}}


@router.get('/eval/reports/{report_id}')
def eval_report_detail(report_id: str, ctx: AuthContext = Depends(get_auth_context)):
    _require_global_reader(ctx, ('admin', 'auditor'))
    from qa_core.quality.evaluation_workspace import reports
    versions = _evaluation_versions()
    active = next((row['version'] for row in versions if row['status'] == 'ACTIVE'), None)
    indexed, _ = reports(get_settings(), active)
    row = next((row for row in indexed if row['id'] == report_id), None)
    if row is None:
        raise HTTPException(404, '报告不存在或已变化，请刷新索引')
    return row


def _evaluation_active():
    active = [row['version'] for row in _evaluation_versions() if row['status'] == 'ACTIVE']
    if len(active) != 1:
        raise HTTPException(409, '需要唯一ACTIVE知识版本')
    return active[0]


@router.post('/eval/plans')
def eval_prepare(ctx: AuthContext = Depends(get_auth_context)):
    require_global_admin(ctx)
    if ctx.dept_ids or ctx.device_types or ctx.security_levels:
        raise HTTPException(403, '受限访问范围的凭据不可执行全局FAQ回归')
    from qa_core.quality.evaluation_workspace import prepare
    from qa_core.api.route_qa import get_usecase
    try:
        return prepare(get_settings(), get_usecase(), _evaluation_active(), ctx)
    except ValueError as error:
        raise HTTPException(409, str(error)) from None
    except Exception:
        raise HTTPException(503, '评测准备失败，请检查可写目录和存储依赖') from None


class EvalExecuteRequest(BaseModel):
    model_config = {'extra': 'forbid'}
    plan_id: str = Field(pattern=r'^[a-f0-9]{32}$')


@router.post('/eval/jobs', status_code=202)
def eval_execute(req: EvalExecuteRequest, ctx: AuthContext = Depends(get_auth_context)):
    require_global_admin(ctx)
    if ctx.dept_ids or ctx.device_types or ctx.security_levels:
        raise HTTPException(403, '受限访问范围的凭据不可执行全局FAQ回归')
    from qa_core.quality.evaluation_workspace import execute
    from qa_core.api.route_qa import get_usecase
    try:
        return execute(get_settings(), get_usecase(), _evaluation_active(), ctx, req.plan_id)
    except KeyError:
        raise HTTPException(404, '计划不存在') from None
    except ValueError as error:
        raise HTTPException(409, str(error)) from None
    except Exception:
        raise HTTPException(503, '任务无法启动，请检查可写目录和存储依赖') from None


@router.get('/eval/jobs/{job_id}')
def eval_job(job_id: str, ctx: AuthContext = Depends(get_auth_context)):
    _require_global_reader(ctx, ('admin', 'auditor'))
    from qa_core.quality.evaluation_workspace import job_state
    try:
        return job_state(job_id)
    except KeyError:
        raise HTTPException(404, '任务不存在') from None


@router.get("/kb")
def admin_kb(ctx: AuthContext = Depends(get_auth_context)) -> dict:
    """知识库浏览：按来源分组的全部 chunks（v3 激活版本）。"""
    require_roles(ctx, ("admin", "auditor", "ops"))
    from qa_core.application.qa_usecase import QaUseCase

    uc = QaUseCase.__new__(QaUseCase)
    from qa_core.config.settings import get_settings as _gs

    settings = _gs()
    uc.settings = settings
    kb_version = uc._active_kb_version()
    from qa_core.storage.milvus_client import query_with_retry

    _uri = settings.milvus_uri
    docs = query_with_retry(_uri, settings.doc_collection, filter=f'kb_version == "{kb_version}"',
                        output_fields=["id", "text", "doc_type", "source", "alarm_code",
                                       "equipment_type", "vendor", "network_tech", "clause"],
                        limit=300)
    faqs = query_with_retry(_uri, settings.faq_collection, filter=f'kb_version == "{kb_version}"',
                        output_fields=["id", "question", "answer", "source", "network_tech"],
                        limit=100)
    grouped: dict[str, int] = {}
    for d in docs:
        grouped[d["source"] or "未标注"] = grouped.get(d["source"] or "未标注", 0) + 1
    for f in faqs:
        grouped[f.get("source") or "未标注"] = grouped.get(f.get("source") or "未标注", 0) + 1
    return {"kb_version": kb_version, "doc_chunks": len(docs), "faqs": len(faqs),
            "total": len(docs) + len(faqs), "by_source": grouped,
            "faqs_detail": [{"question": f["question"], "answer": (f.get("answer") or "")[:300],
                             "source": f.get("source")} for f in faqs]}


# ================= 文档管理（§10 对齐：列表 + 分块溯源） =================

@router.get("/documents")
def admin_documents(ctx: AuthContext = Depends(get_auth_context)) -> dict:
    """文档清单：入库状态、chunk 数、质量标记（对齐设计报告 §10.1）。"""
    require_roles(ctx, ("admin", "auditor", "ops"))
    engine = _engine()
    with engine.begin() as conn:
        rows = [dict(r) for r in conn.execute(sa.text(
            "SELECT kb_version, filename AS source, doc_type, COUNT(*) files, "
            "SUM(chunk_count) chunks, SUM(status='failed') failed, "
            "MIN(created_at) first_at, MAX(updated_at) last_at "
            "FROM ingest_records GROUP BY kb_version, filename, doc_type "
            "ORDER BY kb_version DESC, filename")).mappings().all()]
    for r in rows:  # MySQL SUM 返回 Decimal，JSON 序列化成字符串会导致前端字符串拼接
        r["chunks"] = int(r["chunks"] or 0)
        r["failed"] = int(r["failed"] or 0)
        r["files"] = int(r["files"] or 0)
    return {"documents": rows}


@router.get("/documents/chunks")
def admin_doc_chunks(source: str = "", doc_id: str = "", kb_version: str = "v3",
                     ctx: AuthContext = Depends(get_auth_context)) -> dict:
    """分块溯源：查看某来源文档被切成的 chunks（对齐 §10.1 分块查看）。

    两种模式：
    - doc_id 给定 → 按 chunk id 精确溯源（doc 集合，FAQ 回落 faq 集合并拼 Q/A），
      供对话窗"来源展开显示引用原文"使用；
    - source 给定 → 按文档查全部 chunks。fault_case 特殊：工单的 source 是
      工单号（TT-xxx）而非文件名，通过 file_hash 重建 chunk id 精确反查。
    """
    require_roles(ctx, ("admin", "auditor", "ops"))
    import hashlib
    from pathlib import Path as _Path

    from qa_core.storage.milvus_client import query_with_retry

    settings = get_settings()
    if doc_id:
        safe_id = doc_id.replace('"', '')
        out_fields = ["text", "doc_type", "source", "alarm_code", "clause",
                      "equipment_type", "network_tech", "kb_version"]
        rows = query_with_retry(settings.milvus_uri, settings.doc_collection,
                                filter=f'id == "{safe_id}"',
                                output_fields=out_fields, limit=1)
        if not rows:
            faq_rows = query_with_retry(settings.milvus_uri, settings.faq_collection,
                                        filter=f'id == "{safe_id}"',
                                        output_fields=["question", "answer", "source",
                                                       "kb_version"], limit=1)
            rows = [{**r, "text": f"Q: {r.get('question', '')}\nA: {r.get('answer', '')}"}
                    for r in faq_rows]
        items = [{k: str(v)[:600] for k, v in r.items()} for r in rows]
        return {"source": source or (rows[0].get("source") if rows else doc_id),
                "kb_version": kb_version, "chunks": len(items), "items": items,
                "mode": "chunk"}

    doc_type = None
    with _engine().begin() as conn:
        rec = conn.execute(sa.text(
            "SELECT doc_type, file_hash FROM ingest_records "
            "WHERE kb_version=:v AND filename=:f LIMIT 1"),
            {"v": kb_version, "f": source}).first()
    if rec is None:
        raise HTTPException(404, f"记录中不存在 {source}（{kb_version}）")
    doc_type, file_hash = rec[0], rec[1]

    if doc_type == "fault_case":
        # 工单：重新解析文件重建 chunk id 后按 id 精确反查
        path = _Path(settings.data_dir) / "raw" / source
        if not path.exists():
            raise HTTPException(410, f"源文件 {source} 已从 data/raw 移除，无法溯源分块")
        from qa_core.indexing.chunkers.registry import chunk_rows
        from scripts.rebuild_kb import dedup_key, file_hash_of, parse_dispatch

        fh = file_hash_of(path)
        chunks = chunk_rows(doc_type, parse_dispatch(doc_type, path))
        # 复现入库时的去重（unique_chunks）与 id 生成：sha256(file_hash:index:text)
        seen, unique = set(), []
        for c in chunks:
            k = dedup_key(c)
            if k in seen:
                continue
            seen.add(k)
            unique.append(c)
        ids = [hashlib.sha256(f"{fh}:{i}:{c.text}".encode()).hexdigest()[:32]
               for i, c in enumerate(unique)]
        id_list = ",".join(f'"{i}"' for i in ids)
        rows = query_with_retry(settings.milvus_uri, settings.doc_collection,
                                filter=f"id in [{id_list}]",
                                output_fields=["text", "doc_type", "alarm_code", "clause",
                                               "equipment_type", "network_tech"], limit=200)
        rows = sorted(rows, key=lambda r: ids.index(r["id"]) if r["id"] in ids else 999)
        return {"source": source, "kb_version": kb_version, "chunks": len(rows),
                "items": [{k: (str(v)[:400]) for k, v in r.items()} for r in rows]}

    src_escaped = source.replace('"', '\"')
    collection = settings.faq_collection if doc_type == "faq" else settings.doc_collection
    out_fields = (["question", "answer", "network_tech", "source"] if doc_type == "faq"
                  else ["text", "doc_type", "alarm_code", "clause",
                        "equipment_type", "network_tech"])
    rows = query_with_retry(settings.milvus_uri, collection,
                        filter=f'source == "{src_escaped}" and kb_version == "{kb_version}"',
                        output_fields=out_fields, limit=50)
    items = []
    for r in rows:
        item = {k: (str(v)[:400]) for k, v in r.items()}
        if doc_type == "faq":  # 统一 text 字段供前端展示
            item["text"] = f"Q: {r.get('question', '')}\nA: {r.get('answer', '')}"
        items.append(item)
    return {"source": source, "kb_version": kb_version, "chunks": len(items), "items": items}


# ================= 文档上传（知识闭环最后一环：拖拽上传→自动增量入库） =================

ALLOWED_EXT = {".md", ".csv", ".txt", ".docx", ".pdf", ".xlsx"}
MAX_UPLOAD_BYTES = 20 * 1024 * 1024


@router.post("/documents/upload", status_code=202)
async def admin_upload_document(request: Request,
                                x_filename: str = Header(...),
                                kb_version: str = "",
                                ctx: AuthContext = Depends(get_auth_context)) -> dict:
    """上传到隔离暂存目录并快速返回持久化任务；Worker 异步入库。

    body 为原始字节流（application/octet-stream），文件名经 X-Filename 头传递——
    绕开 python-multipart 依赖，简化镜像。
    """
    require_roles(ctx, ("admin",))
    filename = Path(x_filename).name
    ext = Path(filename).suffix.lower()
    if ext not in ALLOWED_EXT:
        raise HTTPException(400, f"不支持的格式 {ext}（支持：md/csv/txt/docx/pdf/xlsx）")
    content = await request.body()
    if not content:
        raise HTTPException(400, "文件为空")
    if len(content) > MAX_UPLOAD_BYTES:
        raise HTTPException(400, "文件超过 20MB")

    settings = get_settings()
    if (Path(settings.data_dir) / 'artifact-hashes.json').exists():
        from qa_core.api.route_ingest_workspace import writer
        from qa_core.ingest import workspace
        writer(ctx)
        # Current runtime owns a sealed corpus; legacy callers must use the
        # independent workspace, never create .ingest under that corpus.
        record, created = workspace.upload(settings, ctx, kb_version, x_filename, source_id=request.query_params.get('source_id', ''), content=content)
        return dict(workspace.public_upload(record), created=created)
    if not kb_version:
        raise HTTPException(400, "请明确指定 STAGED 候选 kb_version，不再默认写入 ACTIVE")
    source_id = request.query_params.get("source_id", "")
    if not source_id.strip() or len(source_id) > 128 or any(ord(c) < 32 for c in source_id):
        raise HTTPException(400, "上传需明确提供稳定 source_id，同一来源跨版本沿用该 ID")
    digest = hashlib.sha256(content).hexdigest()
    idem = hashlib.sha256(
        f"upload:{ctx.tenant_id}:{kb_version}:{source_id}:{filename}:{digest}:{binding_digest(version_binding(settings))}".encode()).hexdigest()
    job_id = str(uuid.uuid4())
    staging_dir = Path(settings.data_dir) / ".ingest" / job_id
    staging_dir.mkdir(parents=True, exist_ok=False)
    staged = staging_dir / filename
    staged.write_bytes(content)
    from qa_core.ingest.jobs import IngestJobDao

    try:
        with candidate_write(kb_version, settings=settings):
            payload = {"filename": filename, "staging_path": str(staged), "sha256": digest, "source_id": source_id}
            payload["execution_binding"] = execution_binding(settings, "upload", payload)
            job, created = IngestJobDao(settings.mysql_dsn).create(
                job_type="upload", tenant_id=ctx.tenant_id, actor=ctx.user_id,
                kb_version=kb_version, mode="incremental",
                payload=payload,
                idempotency_key=idem, job_id=job_id)
    except Exception:
        staged.unlink(missing_ok=True)
        staging_dir.rmdir()
        raise
    if not created:
        # 本次暂存文件未被任务引用，立即清理；原任务及其暂存文件保持不动。
        staged.unlink(missing_ok=True)
        try:
            staging_dir.rmdir()
        except OSError:
            pass
    return {"created": created, "job_id": job["job_id"], "filename": filename,
            "kb_version": kb_version, "status": job["status"],
            "poll_url": f"/kb/rebuild/{job['job_id']}"}


def _count_chunks(client, kb_version: str, filename: str) -> int:
    settings = get_settings()
    rows = client.query(settings.doc_collection,
                        filter=f'source == "{filename}" and kb_version == "{kb_version}"',
                        output_fields=["id"], limit=500)
    faq_rows = client.query(settings.faq_collection,
                            filter=f'source == "{filename}" and kb_version == "{kb_version}"',
                            output_fields=["id"], limit=500)
    return len(rows) + len(faq_rows)


@router.delete("/documents")
def admin_delete_document(source: str, kb_version: str = "",
                          ctx: AuthContext = Depends(get_auth_context)) -> dict:
    """从指定版本删除文档：Milvus chunks（工单按 id 反查）+ 入库记录 + 缓存失效。"""
    require_global_admin(ctx)
    from qa_core.storage.kb_dao import KbDao
    from qa_core.storage.milvus_client import get_milvus_client
    from scripts.rebuild_kb import delete_file_chunks

    settings = get_settings()
    if not kb_version:
        raise HTTPException(400, "删除仅允许明确指定 STAGED 候选 kb_version")
    stats = delete_file_chunks(
        get_milvus_client(settings.milvus_uri), KbDao(settings.mysql_dsn), source, kb_version)
    from qa_core.cache.redis_cache import CacheLayer

    CacheLayer(settings.redis_url).invalidate_version(kb_version)
    return {"source": source, "kb_version": kb_version, **stats}


# ================= 权限管理 =================

@router.get("/permissions")
def admin_permissions(ctx: AuthContext = Depends(get_auth_context)) -> dict:
    require_roles(ctx, ("admin", "auditor"))
    try:
        with _engine().begin() as conn:
            keys = [dict(r) for r in conn.execute(sa.text(
                "SELECT id, CONCAT(SUBSTR(key_hash,1,8),'…') key_hint, user_id, "
                "tenant_id, dept_id, device_scope, security_levels, roles, enabled, created_at "
                "FROM api_keys WHERE tenant_id=:tenant ORDER BY id DESC"),
                {"tenant": ctx.tenant_id}).mappings().all()]
    except sa.exc.OperationalError:
        raise HTTPException(503, '凭据清单校验暂不可用，请确认安全表结构') from None
    for k in keys:
        k["roles"] = json.loads(k["roles"]) if isinstance(k["roles"], str) else k["roles"]
        for field in ("device_scope", "security_levels"):
            k[field] = json.loads(k[field]) if isinstance(k.get(field), str) else (k.get(field) or [])
    return {"matrix": PERMISSION_MATRIX, "keys": keys}


class KeyRequest(BaseModel):
    key: str = Field(min_length=8, max_length=64)
    user_id: str = Field(min_length=1, max_length=64)
    roles: list[str] = Field(default_factory=lambda: ["ops"])
    tenant_id: str = Field(default="default", min_length=1, max_length=64)
    dept_id: str | None = Field(default=None, max_length=64)
    device_types: list[str] = Field(default_factory=list, max_length=32)
    security_levels: list[str] = Field(default_factory=list, max_length=4)


@router.post("/keys")
def admin_create_key(body: KeyRequest, ctx: AuthContext = Depends(get_auth_context)) -> dict:
    require_roles(ctx, ("admin",))
    if body.tenant_id != ctx.tenant_id:
        raise HTTPException(403, "不能为其他租户签发 API Key")
    if not body.roles or set(body.roles)-{'guest','ops','auditor','admin'}:
        raise HTTPException(422, 'roles需为非空的guest/ops/auditor/admin列表')
    unknown = sorted(set(body.security_levels) - set(SECURITY_LEVELS))
    if unknown:
        raise HTTPException(422, f"未知 security_levels: {', '.join(unknown)}")
    create_api_key(body.key, body.user_id, body.roles, body.tenant_id, body.dept_id,
                   body.device_types, body.security_levels)
    return {"ok": True, "user_id": body.user_id, "roles": body.roles,
            "tenant_id": body.tenant_id, "dept_id": body.dept_id,
            "device_types": body.device_types, "security_levels": body.security_levels,
            "hint": "key 明文仅在创建响应中可见"}


@router.delete("/keys/{hash_prefix}")
def admin_revoke_key(hash_prefix: str, ctx: AuthContext = Depends(get_auth_context)) -> dict:
    require_roles(ctx, ("admin",))
    if len(hash_prefix) < 8:
        raise HTTPException(400, "需提供至少 8 位 hash 前缀")
    with _engine().begin() as conn:
        n = conn.execute(sa.text(
            "UPDATE api_keys SET enabled=0 WHERE key_hash LIKE :p AND tenant_id=:tenant"),
            {"p": hash_prefix + "%", "tenant": ctx.tenant_id}).rowcount
    if not n:
        raise HTTPException(404, "未找到匹配的 key")
    return {"ok": True, "revoked": int(n)}


@router.delete('/key-records/{key_id}')
def admin_revoke_key_record(key_id: int, ctx: AuthContext = Depends(get_auth_context)):
    require_roles(ctx, ('admin',))
    with _engine().begin() as conn:
        count = conn.execute(sa.text('UPDATE api_keys SET enabled=0 WHERE id=:id '
            'AND tenant_id=:tenant AND enabled=1'), {'id': key_id, 'tenant': ctx.tenant_id}).rowcount
    if not count:
        raise HTTPException(409, '凭据不存在、已吊销或不在当前租户范围')
    return {'ok': True, 'revoked': 1}


@router.get("/audit/access")
def admin_access_audit(limit: int = 100, decision: str = "",
                       ctx: AuthContext = Depends(get_auth_context)) -> dict:
    """审计员查询权限允许/拒绝事件；不返回问题原文，仅保留其哈希。"""
    require_roles(ctx, ("admin", "auditor"))
    limit = max(1, min(limit, 500))
    params = {"tenant": ctx.tenant_id, "limit": limit}
    where = "tenant_id=:tenant"
    if decision in {"allow", "deny"}:
        where += " AND decision=:decision"
        params["decision"] = decision
    try:
        with _engine().begin() as conn:
            rows = conn.execute(sa.text(
                "SELECT id, actor, tenant_id, auth_source, action, resource, decision, reason, "
                "roles_json, scope_json, query_hash, created_at FROM permission_audit WHERE " + where +
                " ORDER BY id DESC LIMIT :limit"), params).mappings().all()
    except sa.exc.OperationalError as exc:
        raise HTTPException(503, "安全审计表尚未迁移或数据库不可用") from exc
    events = []
    for row in rows:
        event = dict(row)
        for field in ("roles_json", "scope_json"):
            event[field] = json.loads(event[field]) if isinstance(event[field], str) else event[field]
        events.append(event)
    return {"tenant_id": ctx.tenant_id, "events": events}
