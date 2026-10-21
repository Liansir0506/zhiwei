"""Bounded synchronous-response endpoint with disconnect-aware worker ownership."""
import asyncio
import threading
from time import monotonic
from concurrent.futures import ThreadPoolExecutor
import sqlalchemy as sa
from fastapi import APIRouter, Depends, Header, HTTPException, Query, Request, Response
from fastapi.encoders import jsonable_encoder
from fastapi.responses import JSONResponse
from qa_core.api.auth import AuthContext, get_auth_context, require_roles, verify_api_key
from qa_core.api.route_qa import _enforce_rate_limit
from qa_core.application.agent_diagnose import RunControl, diagnose
from qa_core.application.rule_diagnosis import rule_diagnose
from qa_core.config.settings import get_settings
from qa_core.security.audit import audit_access
from qa_core.schemas.agent import AgentRequest
from qa_core.storage.read_engine import bounded_dsn

router = APIRouter(prefix='/agent', tags=['readonly-agent'])
_slots = threading.BoundedSemaphore(2)
_workers = ThreadPoolExecutor(max_workers=2, thread_name_prefix='readonly-agent')


def require_agent(ctx: AuthContext = Depends(get_auth_context)):
    settings = get_settings()
    if not settings.agent_enabled or not settings.is_test or not settings.auth_required:
        raise HTTPException(503, '只读诊断入口未在隔离测试环境启用')
    if ctx.source != 'api_key':
        raise HTTPException(401, '只读诊断需要有效API Key')
    require_roles(ctx, ('ops', 'auditor', 'admin'))
    _enforce_rate_limit(ctx)
    return ctx


@router.get('/runs')
def agent_runs(alarm_id: str | None = Query(default=None, max_length=64),
               device_id: str | None = Query(default=None, max_length=64),
               limit: int = Query(default=10, ge=1, le=30),
               response: Response = None,
               ctx: AuthContext = Depends(get_auth_context)):
    """可重开的诊断任务列表（T6）：按告警/设备定位历史 run，刷新或换页后可继续。

    读接口：不要求诊断入口启用（只读历史记录），但要求角色与限流。
    """
    require_roles(ctx, ('ops', 'auditor', 'admin'))
    _enforce_rate_limit(ctx)
    if response is not None:
        response.headers['Cache-Control'] = 'no-store'
    clauses = ["tenant_id=:t"]
    params: dict = {"t": ctx.tenant_id, "limit": limit}
    if alarm_id:
        clauses.append("alarm_id=:a")
        params["a"] = alarm_id
    if device_id:
        clauses.append("device_id=:d")
        params["d"] = device_id
    engine = sa.create_engine(get_settings().mysql_dsn, pool_pre_ping=True)
    try:
        with engine.connect() as conn:
            rows = conn.execute(sa.text(
                "SELECT run_id, alarm_id, device_id, query, status, mode, kb_version, summary, created_by, "
                "created_at FROM diagnosis_runs WHERE " + " AND ".join(clauses) +
                " ORDER BY id DESC LIMIT :limit"), params).mappings().all()
        return {"items": [{
            "run_id": row["run_id"], "alarm_id": row["alarm_id"], "device_id": row["device_id"],
            "query": row["query"], "status": row["status"], "mode": row["mode"],
            "kb_version": row["kb_version"],
            "summary": row["summary"], "created_by": row["created_by"],
            "created_at": row["created_at"].isoformat() if row["created_at"] else None} for row in rows]}
    except Exception:
        return {"items": []}
    finally:
        engine.dispose()


@router.post('/diagnose-rule')
def agent_diagnose_rule(body: AgentRequest, response: Response,
                        ctx: AuthContext = Depends(get_auth_context)):
    """主入口规则诊断（不依赖 agent_enabled、不调用模型）。

    面向 8010 的常驻演示：只读业务快照 + 规则判断层。需要明确的告警/设备实体。
    """
    response.headers['Cache-Control'] = 'no-store'
    require_roles(ctx, ('ops', 'auditor', 'admin'))
    _enforce_rate_limit(ctx)
    if not body.alarm_id and not body.device_id:
        raise HTTPException(422, '规则诊断需要告警事件 ID 或设备 ID；只查知识请使用“运维问答”。')
    settings = get_settings()
    try:
        result = rule_diagnose(settings, ctx, alarm_id=body.alarm_id, device_id=body.device_id,
                               query=body.query)
    except LookupError as exc:
        audit_access(settings.mysql_dsn, actor=ctx.user_id, tenant_id=ctx.tenant_id,
                     auth_source=ctx.source, action="agent_rule_diagnose",
                     resource="/agent/diagnose-rule", decision="deny", reason="entity_unavailable",
                     roles=ctx.roles)
        raise HTTPException(404, str(exc)) from None
    audit_access(settings.mysql_dsn, actor=ctx.user_id, tenant_id=ctx.tenant_id,
                 auth_source=ctx.source, action="agent_rule_diagnose",
                 resource=f"/agent/diagnose-rule/{result['run_id']}", decision="allow", roles=ctx.roles)
    return result


@router.post('/diagnose')
async def agent_diagnose(body: AgentRequest, request: Request,
                        ctx: AuthContext = Depends(require_agent), x_api_key: str = Header(default='')):
    if not _slots.acquire(blocking=False):
        raise HTTPException(429, '只读诊断工作槽位已满')
    control = RunControl()
    settings = get_settings()
    def check_auth():
        return verify_api_key(x_api_key, dsn=bounded_dsn(settings.mysql_dsn))
    try:
        future = _workers.submit(diagnose, body, settings, ctx, check_auth, control)
    except Exception:
        _slots.release()
        raise HTTPException(503, '只读诊断工作线程不可用') from None
    future.add_done_callback(lambda _: _slots.release())
    try:
        while not future.done():
            if await request.is_disconnected():
                control.cancel()
                return Response(status_code=499)
            if control.remaining() <= 0:
                control.cancel('timeout')
                calls, remaining, size = control.budget
                return JSONResponse({'run_id': control.run_id, 'status': 'FAILED', 'stop_reason': 'timeout',
                    'mode': 'domain' if body.alarm_id or body.device_id else 'knowledge',
                    'kb_version': control.kb_version, 'answer': '', 'observations': [], 'sources': [], 'trace': [],
                    'limitations': ['工作可能仍在退出；诊断结果已丢弃，退出前保持槽位占用。'],
                    'budget': {'calls_used': calls, 'calls_remaining': remaining, 'result_bytes': size,
                               'elapsed_ms': round((monotonic() - control.started) * 1000, 2)}},
                    headers={'Cache-Control': 'no-store'})
            await asyncio.sleep(0.05)
        if control.cancelled.is_set() or await request.is_disconnected():
            return Response(status_code=499)
        result = future.result()
        return JSONResponse(jsonable_encoder(result), headers={'Cache-Control': 'no-store'})
    except asyncio.CancelledError:
        control.cancel()
        raise
