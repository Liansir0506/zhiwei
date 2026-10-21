# -*- coding: utf-8 -*-
"""问答问题单路由（T4）：差评 → 认领 → 补资料后服务端复测 → 人工确认关闭。

复测用 QaUseCase.ask(persist=False)：不落会话消息、不受会话状态影响；结果只写
问题单 verify_note，不自动关闭——关闭必须人工确认新旧答案对比。
"""
from datetime import datetime, timezone

import sqlalchemy as sa
from fastapi import APIRouter, Depends, HTTPException, Query, Response
from pydantic import BaseModel, Field

from qa_core.api.auth import AuthContext, get_auth_context, require_roles
from qa_core.api.rate_limit import check_rate_limit
from qa_core.config.settings import get_settings
from qa_core.security.audit import audit_access

router = APIRouter()

CAUSE_LABELS = {'missing_info': '资料缺失', 'retrieval_mismatch': '检索错配',
                'wrong_answer': '回答错误', 'unsupported_citation': '引用不支持'}


def _iso(value):
    if isinstance(value, datetime):
        return value.isoformat()
    return str(value) if value else None


def _engine():
    return sa.create_engine(get_settings().mysql_dsn, pool_pre_ping=True)


@router.get("/kb-fix/issues")
def issues(response: Response, status: str = Query("open", max_length=10),
           ctx: AuthContext = Depends(get_auth_context)):
    """问题单列表：原问题、原因、旧答案、复测结果（新旧对比由前端展示）。"""
    response.headers["Cache-Control"] = "no-store"
    require_roles(ctx, ('admin', 'ops', 'auditor'))
    check_rate_limit(ctx.user_id, get_settings().rate_limit_qps)
    if status not in ('open', 'fixed', 'all'):
        raise HTTPException(422, "status 只支持 open / fixed / all")
    clause = "" if status == 'all' else "WHERE status=:status"
    engine = _engine()
    try:
        with engine.connect() as conn:
            rows = conn.execute(sa.text(
                "SELECT id, query_text, answer_text, cause, status, fixed_version, message_id, "
                "session_id, kb_version, assigned_to, verify_note, verified_at, resolved_at, created_at "
                f"FROM bad_cases {clause} ORDER BY id DESC LIMIT 50"),
                {"status": status}).mappings().all()
        return {"items": [{
            "id": row["id"], "query_text": row["query_text"],
            "answer_text": (row["answer_text"] or '')[:800],
            "cause": row["cause"], "cause_label": CAUSE_LABELS.get(row["cause"], row["cause"] or '—'),
            "status": row["status"],
            "fixed_version": row["fixed_version"],
            "message_id": row["message_id"], "kb_version": row["kb_version"],
            "assigned_to": row["assigned_to"],
            "verify_note": row["verify_note"], "verified_at": _iso(row["verified_at"]),
            "resolved_at": _iso(row["resolved_at"]),
            "created_at": _iso(row["created_at"]),
        } for row in rows]}
    finally:
        engine.dispose()


@router.post("/kb-fix/issues/{issue_id}/assign")
def assign(issue_id: int, response: Response, ctx: AuthContext = Depends(get_auth_context)):
    """认领问题单（登记负责人）。"""
    response.headers["Cache-Control"] = "no-store"
    require_roles(ctx, ('admin', 'ops'))
    check_rate_limit(ctx.user_id, get_settings().rate_limit_qps)
    engine = _engine()
    try:
        with engine.begin() as conn:
            row = conn.execute(sa.text("SELECT status FROM bad_cases WHERE id=:id"), {"id": issue_id}).first()
            if not row:
                raise HTTPException(404, "问题单不存在")
            if row[0] != 'open':
                raise HTTPException(409, "问题单已关闭")
            conn.execute(sa.text("UPDATE bad_cases SET assigned_to=:u WHERE id=:id"),
                         {"u": ctx.user_id, "id": issue_id})
    finally:
        engine.dispose()
    audit_access(get_settings().mysql_dsn, actor=ctx.user_id, tenant_id=ctx.tenant_id,
                 auth_source=ctx.source, action="kbfix_assign", resource=f"/kb-fix/issues/{issue_id}",
                 decision="allow", roles=ctx.roles)
    return {"id": issue_id, "assigned_to": ctx.user_id, "message": f"已认领（负责人 {ctx.user_id}）；补资料后请点击“复测原问题”"}


@router.post("/kb-fix/issues/{issue_id}/retest")
def retest(issue_id: int, response: Response, ctx: AuthContext = Depends(get_auth_context)):
    """服务端用原问题复测：不落会话（persist=False），结果只写问题单，不自动关闭。"""
    response.headers["Cache-Control"] = "no-store"
    require_roles(ctx, ('admin', 'ops'))
    check_rate_limit(ctx.user_id, get_settings().rate_limit_qps)
    engine = _engine()
    try:
        with engine.connect() as conn:
            row = conn.execute(sa.text(
                "SELECT query_text, status FROM bad_cases WHERE id=:id"), {"id": issue_id}).first()
    finally:
        engine.dispose()
    if not row:
        raise HTTPException(404, "问题单不存在")
    if row[1] != 'open':
        raise HTTPException(409, "问题单已关闭，无需复测")
    question = row[0]
    from qa_core.api.route_qa import get_usecase, _scoped_session_id
    try:
        result = get_usecase().ask(question, user_id=ctx.user_id, roles=ctx.roles,
                                   session_id=_scoped_session_id(ctx, 'kbfix'),
                                   tenant_id=ctx.tenant_id, scope=ctx.scope, persist=False)
    except Exception:
        raise HTTPException(503, "复测执行失败：问答服务暂不可用")
    if "error" in result:
        note = "复测未成功：" + str(result["error"].get("message", result["error"]))
        sources, version = [], None
    else:
        version = result.get("kb_version")
        sources = result.get("sources") or result.get("citations") or []
        note = (result.get("answer") or '')[:1800]
        if sources:
            note += "\n\n【复测引用】" + "；".join(
                (s.get("source_name") or s.get("source") or s.get("document_id") or "知识原文")
                for s in sources[:3])
        note += f"\n\n【复测版本】{version or '—'}"
    engine = _engine()
    try:
        with engine.begin() as conn:
            conn.execute(sa.text(
                "UPDATE bad_cases SET verify_note=:note, verified_at=:now, kb_version=COALESCE(:v, kb_version) "
                "WHERE id=:id"), {"note": note, "now": datetime.now(timezone.utc).replace(tzinfo=None),
                                  "v": version, "id": issue_id})
    finally:
        engine.dispose()
    audit_access(get_settings().mysql_dsn, actor=ctx.user_id, tenant_id=ctx.tenant_id,
                 auth_source=ctx.source, action="kbfix_retest", resource=f"/kb-fix/issues/{issue_id}",
                 decision="allow", roles=ctx.roles)
    return {"id": issue_id, "retested": True, "kb_version": version,
            "message": "复测完成；请对比新旧答案与引用后人工确认是否关闭（复测通过不会自动关闭问题单）"}


class CloseBody(BaseModel):
    note: str = Field(default="", max_length=300)


@router.post("/kb-fix/issues/{issue_id}/close")
def close(issue_id: int, body: CloseBody, response: Response,
          ctx: AuthContext = Depends(get_auth_context)):
    """人工确认后关闭：必须已有复测记录（未复测不能关闭）。"""
    response.headers["Cache-Control"] = "no-store"
    require_roles(ctx, ('admin', 'ops'))
    check_rate_limit(ctx.user_id, get_settings().rate_limit_qps)
    engine = _engine()
    try:
        with engine.begin() as conn:
            row = conn.execute(sa.text(
                "SELECT status, verify_note FROM bad_cases WHERE id=:id"), {"id": issue_id}).first()
            if not row:
                raise HTTPException(404, "问题单不存在")
            if row[0] != 'open':
                raise HTTPException(409, "问题单已关闭")
            if not row[1]:
                raise HTTPException(409, "还没有复测记录：请先执行“复测原问题”，对比新旧答案后再确认关闭")
            version = conn.execute(sa.text(
                "SELECT version FROM kb_versions WHERE status='ACTIVE' LIMIT 1")).scalar()
            conn.execute(sa.text(
                "UPDATE bad_cases SET status='fixed', fixed_version=:v, resolved_at=:now, "
                "verify_note=CONCAT(verify_note, '\\n\\n【关闭确认】', :note, '（', :u, '）') WHERE id=:id"),
                {"v": version, "now": datetime.now(timezone.utc).replace(tzinfo=None),
                 "note": body.note.strip() or '人工确认新答案与引用可解决原问题', "u": ctx.user_id, "id": issue_id})
    finally:
        engine.dispose()
    audit_access(get_settings().mysql_dsn, actor=ctx.user_id, tenant_id=ctx.tenant_id,
                 auth_source=ctx.source, action="kbfix_close", resource=f"/kb-fix/issues/{issue_id}",
                 decision="allow", roles=ctx.roles)
    return {"id": issue_id, "status": "fixed",
            "message": "问题单已关闭（复测记录与关闭确认已留痕）"}
