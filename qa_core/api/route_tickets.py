# -*- coding: utf-8 -*-
"""工单辅助路由（T3）：告警/诊断 → 草稿 → 人工核对建单 → 受理 → 回执 → 关闭验证。

复用既有 work_orders 表（alarm_id 列天然支持"同一告警的重复点击定位已有工单"），
不另建工单平台；分类/优先级只是规则建议，建单前必须人工核对。
"""
import hashlib
import re
from datetime import datetime, timezone

import sqlalchemy as sa
from fastapi import APIRouter, Depends, HTTPException, Query, Response
from pydantic import BaseModel, Field

from qa_core.api.auth import AuthContext, get_auth_context, require_roles
from qa_core.api.rate_limit import check_rate_limit
from qa_core.api.scope_sql import device_scope
from qa_core.config.settings import get_settings
from qa_core.security.audit import audit_access

router = APIRouter()

# 分类建议：告警码前缀 → 业务专业（仅建议，人工可改）
CATEGORY_RULES = [
    (r'AMF|注册|N2|寻呼', '核心网（AMF/注册）', 'core-ops'),
    (r'GNB|CELL|小区|切换|无线', '无线接入（gNB/小区）', 'ran-ops'),
    (r'TRAN|传输|链路', '承载传输', 'transport-ops'),
]
PRIORITY_BY_SEVERITY = {'critical': 'P1', 'major': 'P2', 'minor': 'P3'}
TICKET_OPEN_STATES = ('open', 'processing')


def _iso(value):
    if isinstance(value, datetime):
        return value.isoformat()
    return str(value) if value else None


def _category(alarm_code: str, device_type: str):
    text = f"{alarm_code or ''} {device_type or ''}"
    for pattern, category, dept in CATEGORY_RULES:
        if re.search(pattern, text, re.I):
            return category, dept
    return '综合保障（待分类）', 'noc'


def _load_alarm(conn, ctx, alarm_id: str, scope_clause: str, scope_params: dict):
    row = conn.execute(sa.text(
        "SELECT a.alarm_id, a.alarm_code, a.severity, a.status, a.occurred_at, a.device_id, "
        "d.name AS device_name, d.device_type, d.dept_id "
        "FROM alarms a LEFT JOIN devices d ON d.device_id=a.device_id AND d.tenant_id=a.tenant_id "
        "WHERE a.tenant_id=:t AND a.alarm_id=:a" + scope_clause),
        {"t": ctx.tenant_id, "a": alarm_id, **scope_params}).mappings().first()
    if not row:
        raise HTTPException(404, "告警不存在或不在当前授权范围")
    return row


def _existing_ticket(conn, ctx, alarm_id: str):
    # text() 不会展开元组参数：IN 按元素生成占位符
    placeholders = ",".join(f":s{i}" for i in range(len(TICKET_OPEN_STATES)))
    params = {"t": ctx.tenant_id, "a": alarm_id}
    for i, state in enumerate(TICKET_OPEN_STATES):
        params[f"s{i}"] = state
    return conn.execute(sa.text(
        "SELECT ticket_id, status, priority, assignee_user_id, title, opened_at FROM work_orders "
        f"WHERE tenant_id=:t AND alarm_id=:a AND status IN ({placeholders}) ORDER BY opened_at DESC LIMIT 1"),
        params).mappings().first()


class TicketBody(BaseModel):
    title: str = Field(min_length=4, max_length=120)
    category: str = Field(min_length=2, max_length=40)
    priority: str = Field(pattern=r'^P[1-4]$')
    owner: str = Field(min_length=2, max_length=64)
    summary: str = Field(default="", max_length=500)


class TicketStateBody(BaseModel):
    action: str = Field(max_length=16)  # accept | progress | close
    note: str = Field(default="", max_length=500)


TICKET_STATE_LABELS = {"open": "待受理", "processing": "处理中", "closed": "已关闭",
                      "resolved": "待验证", "cancelled": "已取消"}


@router.get("/tickets")
def tickets(response: Response, state: str = Query("open", max_length=12),
            mine: bool = Query(False), page: int = Query(1, ge=1, le=1000),
            page_size: int = Query(20, ge=1, le=50),
            ctx: AuthContext = Depends(get_auth_context)):
    """轻量工单列表（B1）：我的待办 / 处理中 / 已关闭。

    state 支持 open / processing / closed / active / all；mine=true 只看负责人是当前用户建单时指定人的派单。
    """
    response.headers["Cache-Control"] = "no-store"
    require_roles(ctx, ('admin', 'ops', 'auditor'))
    check_rate_limit(ctx.user_id, get_settings().rate_limit_qps)
    if state not in ("open", "processing", "closed", "active", "all"):
        raise HTTPException(422, "state 支持 open / processing / closed / active / all")
    clauses = ["w.tenant_id=:t", "w.source_system='ops-console'"]
    params = {"t": ctx.tenant_id, "limit": page_size, "offset": (page - 1) * page_size}
    if state == "active":
        clauses.append("w.status IN ('open','processing')")
    elif state != "all":
        clauses.append("w.status=:state")
        params["state"] = state
    if mine:
        clauses.append("w.created_by=:me")
        params["me"] = ctx.user_id
    where = " AND ".join(clauses)
    engine = sa.create_engine(get_settings().mysql_dsn, pool_pre_ping=True)
    try:
        with engine.connect() as conn:
            total = int(conn.execute(sa.text(
                "SELECT COUNT(*) FROM work_orders w WHERE " + where), params).scalar_one())
            rows = conn.execute(sa.text(
                "SELECT w.ticket_id, w.title, w.status, w.priority, w.assignee_user_id, w.dept_id, "
                "w.alarm_id, w.device_id, w.opened_at, w.closed_at, d.name AS device_name, a.alarm_code "
                "FROM work_orders w LEFT JOIN devices d ON d.device_id=w.device_id AND d.tenant_id=w.tenant_id "
                "LEFT JOIN alarms a ON a.alarm_id=w.alarm_id AND a.tenant_id=w.tenant_id "
                "WHERE " + where + " ORDER BY w.opened_at DESC LIMIT :limit OFFSET :offset"),
                params).mappings().all()
            counts = conn.execute(sa.text(
                "SELECT SUM(status='open') AS open_n, SUM(status='processing') AS processing_n, "
                "SUM(status='closed') AS closed_n FROM work_orders "
                "WHERE tenant_id=:t AND source_system='ops-console'"), {"t": ctx.tenant_id}).mappings().first()
        return {"items": [{
            "ticket_id": row["ticket_id"], "title": row["title"], "status": row["status"],
            "status_label": TICKET_STATE_LABELS.get(row["status"], row["status"]),
            "priority": row["priority"], "owner": row["assignee_user_id"], "dept_id": row["dept_id"],
            "alarm_id": row["alarm_id"], "alarm_code": row["alarm_code"],
            "device_id": row["device_id"], "device_name": row["device_name"],
            "opened_at": _iso(row["opened_at"]), "closed_at": _iso(row["closed_at"]),
        } for row in rows],
            "total": total, "page": page, "page_size": page_size,
            "pages": max(1, (total + page_size - 1) // page_size),
            "counts": {"open": int(counts["open_n"] or 0), "processing": int(counts["processing_n"] or 0),
                       "closed": int(counts["closed_n"] or 0)},
            "scope": "仅展示本控制台创建的工单（source_system=ops-console）；审批与派单以本表为准"}
    finally:
        engine.dispose()


@router.get("/alerts/{alarm_id}/ticket")
def alert_ticket(alarm_id: str, response: Response, ctx: AuthContext = Depends(get_auth_context)):
    """定位该告警的关联工单（重复点击防重复建单）。"""
    response.headers["Cache-Control"] = "no-store"
    require_roles(ctx, ('admin', 'ops', 'auditor'))
    check_rate_limit(ctx.user_id, get_settings().rate_limit_qps)
    scope_clauses, scope_params, _, _ = device_scope(ctx, 'd')
    scope_clause = (" AND " + " AND ".join(scope_clauses)) if scope_clauses else ""
    engine = sa.create_engine(get_settings().mysql_dsn, pool_pre_ping=True)
    try:
        with engine.connect() as conn:
            _load_alarm(conn, ctx, alarm_id, scope_clause, scope_params)
            row = _existing_ticket(conn, ctx, alarm_id)
            if not row:
                return {"ticket": None, "message": "该告警尚无关联工单；可从诊断结论生成工单草稿。"}
            return {"ticket": {"ticket_id": row["ticket_id"], "status": row["status"],
                               "priority": row["priority"], "owner": row["assignee_user_id"],
                               "title": row["title"], "opened_at": _iso(row["opened_at"])}}
    finally:
        engine.dispose()


@router.post("/alerts/{alarm_id}/ticket-draft")
def ticket_draft(alarm_id: str, response: Response, ctx: AuthContext = Depends(get_auth_context)):
    """生成工单草稿（不写库）：带入事件、设备、窗口、诊断候选与建议分类/优先级/缺项提示。"""
    response.headers["Cache-Control"] = "no-store"
    require_roles(ctx, ('admin', 'ops'))
    check_rate_limit(ctx.user_id, get_settings().rate_limit_qps)
    scope_clauses, scope_params, _, _ = device_scope(ctx, 'd')
    scope_clause = (" AND " + " AND ".join(scope_clauses)) if scope_clauses else ""
    engine = sa.create_engine(get_settings().mysql_dsn, pool_pre_ping=True)
    try:
        with engine.connect() as conn:
            alarm = _load_alarm(conn, ctx, alarm_id, scope_clause, scope_params)
            existing = _existing_ticket(conn, ctx, alarm_id)
            category, dept = _category(alarm["alarm_code"], alarm["device_type"])
            priority = PRIORITY_BY_SEVERITY.get(alarm["severity"], 'P4')
            # 关联诊断：取处理时间线最近一次 confirm 的 run_id
            run_row = conn.execute(sa.text(
                "SELECT run_id, actor, created_at FROM alert_handling_log "
                "WHERE tenant_id=:t AND alarm_id=:a AND action='confirm' AND run_id IS NOT NULL "
                "ORDER BY id DESC LIMIT 1"), {"t": ctx.tenant_id, "a": alarm_id}).first()
            missing = []
            if not alarm["device_id"]:
                missing.append('缺少设备 ID，请先补录设备信息')
            notes = conn.execute(sa.text(
                "SELECT note FROM alert_handling_log WHERE tenant_id=:t AND alarm_id=:a AND action='handle' "
                "ORDER BY id DESC LIMIT 1"), {"t": ctx.tenant_id, "a": alarm_id}).scalar()
            users = [row[0] for row in conn.execute(sa.text(
                "SELECT user_id FROM users WHERE tenant_id=:t AND status='active' ORDER BY user_id LIMIT 8"),
                {"t": ctx.tenant_id}).all()]
            owner_suggestion = users[0] if users else None
            draft = {
                'alarm_id': alarm_id, 'alarm_code': alarm["alarm_code"],
                'device_id': alarm["device_id"], 'device_name': alarm["device_name"],
                'occurred_at': _iso(alarm["occurred_at"]),
                'title': f"{alarm['device_name'] or alarm['device_id'] or '未知设备'} {alarm['alarm_code']} 处置工单",
                'category': category, 'dept_id': dept, 'priority': priority,
                'owner_suggestion': owner_suggestion, 'owner_candidates': users,
                'diagnosis_run_id': run_row[0] if run_row else None,
                'diagnosis_confirmed_by': run_row[1] if run_row else None,
                'last_handling_note': notes,
                'suggestions': [
                    f"建议分类：{category}（按告警码规则，请核对）",
                    f"建议优先级：{priority}（按告警级别 {alarm['severity']}，请核对影响范围）",
                    f"事件窗口：{(_iso(alarm['occurred_at']) or '—')[:16]} 起",
                ],
                'missing_hints': missing,
            }
            if not users:
                draft['missing_hints'].append('系统中没有可用负责人（users 表为空），请先建用户')
            if existing:
                draft['existing_ticket'] = {"ticket_id": existing["ticket_id"], "status": existing["status"],
                                            "priority": existing["priority"], "owner": existing["assignee_user_id"]}
                draft['suggestions'].append(
                    f"注意：该告警已有进行中工单 {existing['ticket_id']}（{existing['status']}），请勿重复建单")
            return draft
    finally:
        engine.dispose()


@router.post("/alerts/{alarm_id}/ticket", status_code=201)
def ticket_create(alarm_id: str, body: TicketBody, response: Response,
                  ctx: AuthContext = Depends(get_auth_context)):
    """人工核对后建单。同一告警已有进行中工单时返回 409 与已有工单号，不重复建单。"""
    response.headers["Cache-Control"] = "no-store"
    require_roles(ctx, ('admin', 'ops'))
    check_rate_limit(ctx.user_id, get_settings().rate_limit_qps)
    scope_clauses, scope_params, _, _ = device_scope(ctx, 'd')
    scope_clause = (" AND " + " AND ".join(scope_clauses)) if scope_clauses else ""
    engine = sa.create_engine(get_settings().mysql_dsn, pool_pre_ping=True)
    now = datetime.now(timezone.utc).replace(tzinfo=None)
    try:
        with engine.begin() as conn:
            alarm = _load_alarm(conn, ctx, alarm_id, scope_clause, scope_params)
            existing = _existing_ticket(conn, ctx, alarm_id)
            if existing:
                raise HTTPException(409, f"该告警已有进行中工单 {existing['ticket_id']}（{existing['status']}），"
                                         "请在该工单上继续处理，不重复建单")
            owner = body.owner.strip()
            valid_owner = conn.execute(sa.text(
                "SELECT user_id FROM users WHERE tenant_id=:t AND user_id=:u AND status='active'"),
                {"t": ctx.tenant_id, "u": owner}).first()
            if not valid_owner:
                candidates = [row[0] for row in conn.execute(sa.text(
                    "SELECT user_id FROM users WHERE tenant_id=:t AND status='active' ORDER BY user_id LIMIT 5"),
                    {"t": ctx.tenant_id}).all()]
                raise HTTPException(422, f"负责人 {owner} 不在用户列表（工单需关联真实用户）；可选：{'、'.join(candidates) or '（无）'}")
            ticket_id = 'OPS-TT-' + now.strftime('%Y%m%d%H%M%S')
            conn.execute(sa.text(
                "INSERT INTO work_orders (ticket_id, tenant_id, dept_id, device_id, alarm_id, title, "
                "priority, status, assignee_user_id, source_system, opened_at, created_at, updated_at, created_by) "
                "VALUES (:tid, :t, :dept, :dev, :a, :title, :p, 'open', :owner, 'ops-console', :now, :now, :now, :by)"),
                {"tid": ticket_id, "t": ctx.tenant_id, "dept": _category(alarm["alarm_code"], alarm["device_type"])[1],
                 "dev": alarm["device_id"], "a": alarm_id, "title": body.title.strip(),
                 "p": body.priority, "owner": owner, "now": now, "by": ctx.user_id})
            detail = (body.summary.strip() or '') + (f"（关联诊断 {body.summary and ''}" if False else "")
            # 处理回执进告警报本，保证告警时间线可追溯建单动作
            conn.execute(sa.text(
                "INSERT INTO alert_handling_log (tenant_id, alarm_id, device_id, action, actor, run_id, note) "
                "VALUES (:t, :a, :d, 'handle', :u, :run, :note)"),
                {"t": ctx.tenant_id, "a": alarm_id, "d": alarm["device_id"], "u": ctx.user_id,
                 "run": None, "note": f"创建工单 {ticket_id}（{body.category} / {body.priority}，负责人 {body.owner.strip()}）"})
            _ = detail
    finally:
        engine.dispose()
    audit_access(get_settings().mysql_dsn, actor=ctx.user_id, tenant_id=ctx.tenant_id,
                 auth_source=ctx.source, action="ticket_create", resource=f"/tickets/{alarm_id}",
                 decision="allow", roles=ctx.roles)
    return {"ticket_id": ticket_id, "status": "open", "priority": body.priority,
            "owner": body.owner.strip(), "message": "工单已创建（待受理）；处理回执已写入告警报本"}


@router.get("/tickets/{ticket_id}")
def ticket_detail(ticket_id: str, response: Response, ctx: AuthContext = Depends(get_auth_context)):
    """工单详情：返回原告警摘要与关联诊断 run（可回跳）。"""
    response.headers["Cache-Control"] = "no-store"
    require_roles(ctx, ('admin', 'ops', 'auditor'))
    check_rate_limit(ctx.user_id, get_settings().rate_limit_qps)
    engine = sa.create_engine(get_settings().mysql_dsn, pool_pre_ping=True)
    try:
        with engine.connect() as conn:
            row = conn.execute(sa.text(
                "SELECT w.*, d.name AS device_name, d.device_type, a.alarm_code, a.severity "
                "FROM work_orders w LEFT JOIN devices d ON d.device_id=w.device_id AND d.tenant_id=w.tenant_id "
                "LEFT JOIN alarms a ON a.alarm_id=w.alarm_id AND a.tenant_id=w.tenant_id "
                "WHERE w.tenant_id=:t AND w.ticket_id=:tid"),
                {"t": ctx.tenant_id, "tid": ticket_id}).mappings().first()
            if not row:
                raise HTTPException(404, "工单不存在")
            run_row = conn.execute(sa.text(
                "SELECT run_id, actor, created_at FROM alert_handling_log "
                "WHERE tenant_id=:t AND alarm_id=:a AND action='confirm' AND run_id IS NOT NULL "
                "ORDER BY id DESC LIMIT 1"), {"t": ctx.tenant_id, "a": row["alarm_id"]}).first()
            return {"ticket": {
                "ticket_id": row["ticket_id"], "title": row["title"], "status": row["status"],
                "priority": row["priority"], "owner": row["assignee_user_id"], "dept_id": row["dept_id"],
                "opened_at": _iso(row["opened_at"]), "closed_at": _iso(row["closed_at"]),
                "alarm": {"alarm_id": row["alarm_id"], "alarm_code": row["alarm_code"],
                          "severity": row["severity"], "device_id": row["device_id"],
                          "device_name": row["device_name"], "device_type": row["device_type"]},
                "diagnosis_run_id": run_row[0] if run_row else None}}
    finally:
        engine.dispose()


@router.post("/tickets/{ticket_id}/state")
def ticket_state(ticket_id: str, body: TicketStateBody, response: Response,
                 ctx: AuthContext = Depends(get_auth_context)):
    """工单状态流：accept（受理）→ progress（处理回执）→ close（关闭验证，要求验证结论）。"""
    response.headers["Cache-Control"] = "no-store"
    require_roles(ctx, ('admin', 'ops'))
    check_rate_limit(ctx.user_id, get_settings().rate_limit_qps)
    note = body.note.strip()
    engine = sa.create_engine(get_settings().mysql_dsn, pool_pre_ping=True)
    now = datetime.now(timezone.utc).replace(tzinfo=None)
    try:
        with engine.begin() as conn:
            row = conn.execute(sa.text(
                "SELECT ticket_id, status, alarm_id, device_id, assignee_user_id FROM work_orders "
                "WHERE tenant_id=:t AND ticket_id=:tid"), {"t": ctx.tenant_id, "tid": ticket_id}).first()
            if not row:
                raise HTTPException(404, "工单不存在")
            status, alarm_id = row[1], row[2]
            if body.action == 'accept':
                if status != 'open':
                    raise HTTPException(409, f"工单当前为「{status}」，只有待受理（open）工单可以认领")
                # 负责人维持建单时指定的真实用户（users 表外键）；API Key 账号不能直接当负责人。
                conn.execute(sa.text(
                    "UPDATE work_orders SET status='processing', updated_at=:now "
                    "WHERE tenant_id=:t AND ticket_id=:tid"),
                    {"now": now, "t": ctx.tenant_id, "tid": ticket_id})
                _handling(conn, ctx, row, note or '受理工单，开始处理')
                return {"ticket_id": ticket_id, "status": "processing",
                        "state_label": "处理中", "message": "工单已受理（负责人维持建单时指定），进入处理中"}
            if body.action == 'progress':
                if status not in ('open', 'processing'):
                    raise HTTPException(409, "工单已关闭；如需重开请说明原因并联系管理员")
                if not note:
                    raise HTTPException(422, "处理回执必须填写处理内容与结果")
                conn.execute(sa.text(
                    "UPDATE work_orders SET status='processing', updated_at=:now "
                    "WHERE tenant_id=:t AND ticket_id=:tid"), {"now": now, "t": ctx.tenant_id, "tid": ticket_id})
                _handling(conn, ctx, row, '工单回执：' + note)
                return {"ticket_id": ticket_id, "status": "processing", "state_label": "处理中", "message": "处理回执已记录"}
            if body.action == 'close':
                if status != 'processing':
                    raise HTTPException(409, "只有处理中（processing）的工单可以关闭")
                if len(note) < 8:
                    raise HTTPException(422, "关闭验证必须填写验证依据与结论（至少 8 字），不能只点按钮")
                conn.execute(sa.text(
                    "UPDATE work_orders SET status='closed', closed_at=:now, updated_at=:now "
                    "WHERE tenant_id=:t AND ticket_id=:tid"), {"now": now, "t": ctx.tenant_id, "tid": ticket_id})
                _handling(conn, ctx, row, '工单关闭验证：' + note)
                return {"ticket_id": ticket_id, "status": "closed",
                        "message": "工单已关闭（验证结论已记录）；可在诊断页提交案例草稿"}
            raise HTTPException(422, f"不支持的动作：{body.action}（可用：accept / progress / close）")
    finally:
        engine.dispose()


def case_source_id(case_id: int, ticket_id: str) -> str:
    """稳定来源 ID：同一案例在重复导出时不变，便于入库准入与追溯。"""
    return f"case:{ticket_id}:{case_id}"


def case_filename(case_id: int, ticket_id: str) -> str:
    """入库工作区要求英文类型前缀；文件名只含 ASCII 安全字符。"""
    safe = re.sub(r"[^A-Za-z0-9_.-]", "_", ticket_id)[:80]
    return f"fault_case_{case_id}_{safe}.md"


def case_document(row) -> tuple[str, str]:
    """生成带来源与审核关系的 Markdown（含 front matter），返回 (content, sha256)。"""
    source_id = row["source_id"] or case_source_id(row["id"], row["ticket_id"])
    front = [
        "---",
        f"source_id: {source_id}",
        f"ticket_id: {row['ticket_id']}",
        f"alarm_id: {row['alarm_id'] or ''}",
        f"case_id: {row['id']}",
        f"reviewed_by: {row['reviewed_by'] or ''}",
        f"reviewed_at: {(row['reviewed_at'] or '')}",
        "doc_type: fault_case",
        "---",
        "",
    ]
    content = "\n".join(front) + (row["content"] or "")
    return content, hashlib.sha256(content.encode("utf-8")).hexdigest()


@router.post("/tickets/{ticket_id}/case-draft", status_code=201)
def case_draft(ticket_id: str, response: Response, ctx: AuthContext = Depends(get_auth_context)):
    """工单关闭后生成案例草稿（C4）：告警+诊断摘要+处理回执+验证结论，提交人工审核。

    审核通过只是"可入库"标记；正式入库仍需在资料入库工作区执行，不在这里伪造。
    """
    response.headers["Cache-Control"] = "no-store"
    require_roles(ctx, ('admin', 'ops'))
    check_rate_limit(ctx.user_id, get_settings().rate_limit_qps)
    engine = sa.create_engine(get_settings().mysql_dsn, pool_pre_ping=True)
    try:
        with engine.begin() as conn:
            ticket = conn.execute(sa.text(
                "SELECT w.ticket_id, w.title, w.status, w.priority, w.alarm_id, w.device_id, w.assignee_user_id, "
                "w.closed_at, d.name AS device_name, d.device_type, a.alarm_code, a.severity "
                "FROM work_orders w LEFT JOIN devices d ON d.device_id=w.device_id AND d.tenant_id=w.tenant_id "
                "LEFT JOIN alarms a ON a.alarm_id=w.alarm_id AND a.tenant_id=w.tenant_id "
                "WHERE w.tenant_id=:t AND w.ticket_id=:tid"), {"t": ctx.tenant_id, "tid": ticket_id}).mappings().first()
            if not ticket:
                raise HTTPException(404, "工单不存在")
            if ticket["status"] != "closed":
                raise HTTPException(409, "只有已关闭的工单才能沉淀案例；请先完成关闭验证")
            existing = conn.execute(sa.text(
                "SELECT id FROM case_drafts WHERE tenant_id=:t AND ticket_id=:tid AND status='submitted' LIMIT 1"),
                {"t": ctx.tenant_id, "tid": ticket_id}).first()
            if existing:
                raise HTTPException(409, f"该工单已有一份待审案例草稿（#{existing[0]}），请先在待审列表中处理")
            notes = [row[0] for row in conn.execute(sa.text(
                "SELECT note FROM alert_handling_log WHERE tenant_id=:t AND alarm_id=:a AND note IS NOT NULL "
                "ORDER BY id"), {"t": ctx.tenant_id, "a": ticket["alarm_id"]}).all()]
            run = conn.execute(sa.text(
                "SELECT run_id, summary FROM diagnosis_runs WHERE tenant_id=:t AND alarm_id=:a "
                "ORDER BY id DESC LIMIT 1"), {"t": ctx.tenant_id, "a": ticket["alarm_id"]}).first()
            title = f"案例：{ticket['device_name'] or ticket['device_id'] or ''} {ticket['alarm_code'] or ''} 处置复盘".strip()
            lines = [
                f"# {title}", '',
                f"- 工单：{ticket['ticket_id']}（{ticket['priority']}）",
                f"- 告警：{ticket['alarm_id']} / {ticket['alarm_code'] or '—'}（{ticket['severity'] or '—'}）",
                f"- 设备：{ticket['device_name'] or '—'}（{ticket['device_type'] or '—'}，{ticket['device_id']}）",
                f"- 负责人：{ticket['assignee_user_id'] or '—'}",
                f"- 关闭时间：{ticket['closed_at'] or '—'}",
                '', '## 处置过程', '',
            ]
            handling = [n for n in notes if n and ('工单' in n or '回执' in n or '关闭' in n or '观察' in n)]
            for note in (handling or notes)[-8:]:
                lines.append(f"- {note}")
            if run and run[1]:
                lines += ['', '## 诊断结论摘要', '', str(run[1])[:600], '', f"（诊断 run：{run[0]}）"]
            lines += ['', '## 验证与结论', '',
                      '按关闭验证记录：故障处理后在观察窗口内指标恢复，告警未复发（详见工单关闭验证记录）。',
                      '', '## 审核提示', '',
                      '- 本页是案例草稿，提交后进入“待审与缺口”审核；',
                      '- 审核通过仅标记可入库，正式入库请在“资料入库”工作区执行并走版本发布门禁。']
            content = '\n'.join(lines)[:8000]
            cur = conn.execute(sa.text(
                "INSERT INTO case_drafts (tenant_id, ticket_id, alarm_id, title, content, status, created_by) "
                "VALUES (:t, :tid, :a, :title, :content, 'submitted', :u)"),
                {"t": ctx.tenant_id, "tid": ticket_id, "a": ticket["alarm_id"], "title": title,
                 "content": content, "u": ctx.user_id})
            case_id = cur.lastrowid
    finally:
        engine.dispose()
    audit_access(get_settings().mysql_dsn, actor=ctx.user_id, tenant_id=ctx.tenant_id,
                 auth_source=ctx.source, action="case_draft_create", resource=f"/tickets/{ticket_id}",
                 decision="allow", roles=ctx.roles)
    return {"case_id": case_id, "title": title, "status": "submitted",
            "message": "案例草稿已提交到“待审与缺口”；审核通过后请到资料入库工作区正式入库"}


@router.get("/case-drafts/{case_id}/document")
def case_document_export(case_id: int, response: Response,
                         ctx: AuthContext = Depends(get_auth_context)):
    """导出已审核案例的 Markdown 原文（稳定 source_id + sha256 + 审核关系）。

    只导出，不写入知识库；入库仍由资料入库工作区按既有准入流程执行。
    """
    response.headers["Cache-Control"] = "no-store"
    require_roles(ctx, ('admin', 'ops', 'auditor'))
    check_rate_limit(ctx.user_id, get_settings().rate_limit_qps)
    engine = sa.create_engine(get_settings().mysql_dsn, pool_pre_ping=True)
    now = datetime.now(timezone.utc).replace(tzinfo=None)
    try:
        with engine.begin() as conn:
            row = conn.execute(sa.text(
                "SELECT id, ticket_id, alarm_id, title, content, status, reviewed_by, reviewed_at, source_id "
                "FROM case_drafts WHERE tenant_id=:t AND id=:id"),
                {"t": ctx.tenant_id, "id": case_id}).mappings().first()
            if not row:
                raise HTTPException(404, "案例草稿不存在")
            if row["status"] != "approved":
                raise HTTPException(409, "只有审核通过的案例才能导出；请先完成审核")
            source_id = row["source_id"] or case_source_id(row["id"], row["ticket_id"])
            document, sha = case_document({**dict(row), "source_id": source_id})
            conn.execute(sa.text(
                "UPDATE case_drafts SET source_id=:sid, content_sha256=:sha, exported_at=:now WHERE id=:id"),
                {"sid": source_id, "sha": sha, "now": now, "id": case_id})
    finally:
        engine.dispose()
    audit_access(get_settings().mysql_dsn, actor=ctx.user_id, tenant_id=ctx.tenant_id,
                 auth_source=ctx.source, action="case_draft_export", resource=f"/case-drafts/{case_id}",
                 decision="allow", roles=ctx.roles)
    return {"case_id": case_id, "ticket_id": row["ticket_id"], "title": row["title"],
            "filename": case_filename(case_id, row["ticket_id"]),
            "source_id": source_id, "sha256": sha, "content": document,
            "reviewed_by": row["reviewed_by"], "reviewed_at": _iso(row["reviewed_at"]),
            "next_step": "在“资料入库”工作区上传该文件，并用 source_id 作为稳定来源登记、走既有准入"}


@router.get("/case-drafts")
def case_drafts(response: Response, status: str = Query("submitted", max_length=12),
                ctx: AuthContext = Depends(get_auth_context)):
    response.headers["Cache-Control"] = "no-store"
    require_roles(ctx, ('admin', 'ops', 'auditor'))
    check_rate_limit(ctx.user_id, get_settings().rate_limit_qps)
    if status not in ('submitted', 'approved', 'all'):
        raise HTTPException(422, "status 支持 submitted / approved / all")
    where = "" if status == 'all' else " AND status=:s"
    engine = sa.create_engine(get_settings().mysql_dsn, pool_pre_ping=True)
    try:
        with engine.connect() as conn:
            rows = conn.execute(sa.text(
                "SELECT id, ticket_id, alarm_id, title, content, status, created_by, reviewed_by, "
                "created_at, reviewed_at, source_id, content_sha256, exported_at "
                "FROM case_drafts WHERE tenant_id=:t" + where + " ORDER BY id DESC LIMIT 30"),
                {"t": ctx.tenant_id, "s": status}).mappings().all()
        return {"items": [{"id": row["id"], "ticket_id": row["ticket_id"], "alarm_id": row["alarm_id"],
                           "title": row["title"], "content": row["content"], "status": row["status"],
                           "created_by": row["created_by"], "reviewed_by": row["reviewed_by"],
                           "created_at": _iso(row["created_at"]), "reviewed_at": _iso(row["reviewed_at"]),
                           "source_id": row["source_id"], "sha256": row["content_sha256"],
                           "exported_at": _iso(row["exported_at"])}
                          for row in rows]}
    finally:
        engine.dispose()


class CaseReviewBody(BaseModel):
    decision: str = Field(pattern=r'^(approve|reject)$')
    note: str = Field(default="", max_length=300)


@router.post("/case-drafts/{case_id}/review")
def case_review(case_id: int, body: CaseReviewBody, response: Response,
                ctx: AuthContext = Depends(get_auth_context)):
    """案例审核：通过只标记"可入库"，不触发实际入库（入库走资料入库工作区）。"""
    response.headers["Cache-Control"] = "no-store"
    require_roles(ctx, ('admin',))
    check_rate_limit(ctx.user_id, get_settings().rate_limit_qps)
    engine = sa.create_engine(get_settings().mysql_dsn, pool_pre_ping=True)
    now = datetime.now(timezone.utc).replace(tzinfo=None)
    try:
        with engine.begin() as conn:
            row = conn.execute(sa.text(
                "SELECT status, ticket_id FROM case_drafts WHERE tenant_id=:t AND id=:id"),
                {"t": ctx.tenant_id, "id": case_id}).first()
            if not row:
                raise HTTPException(404, "案例草稿不存在")
            if row[0] != 'submitted':
                raise HTTPException(409, "该草稿已审核")
            status = 'approved' if body.decision == 'approve' else 'rejected'
            conn.execute(sa.text(
                "UPDATE case_drafts SET status=:s, reviewed_by=:u, reviewed_at=:now, "
                "content=CONCAT(content, '\n\n【审核】', :note) WHERE id=:id"),
                {"s": status, "u": ctx.user_id, "now": now,
                 "note": (body.note.strip() or ('通过' if status == 'approved' else '驳回')), "id": case_id})
            # 审核通过即冻结稳定来源与内容哈希，导出与入库引用同一身份。
            if status == 'approved':
                conn.execute(sa.text(
                    "UPDATE case_drafts SET source_id=:sid WHERE id=:id AND (source_id IS NULL OR source_id='')"),
                    {"sid": case_source_id(case_id, row[1]), "id": case_id})
    finally:
        engine.dispose()
    audit_access(get_settings().mysql_dsn, actor=ctx.user_id, tenant_id=ctx.tenant_id,
                 auth_source=ctx.source, action="case_draft_review", resource=f"/case-drafts/{case_id}",
                 decision="allow", roles=ctx.roles)
    return {"case_id": case_id, "status": status,
            "source_id": case_source_id(case_id, row[1]) if status == 'approved' else None,
            "message": ("已通过（可入库）：请到“待审与缺口”导出案例原文，再在资料入库工作区走准入"
                        if status == 'approved' else "已驳回")}


def _handling(conn, ctx, row, note):
    """工单动作同步写告警报本（告警时间线可追溯工单推进）。"""
    if not row[2]:
        return
    conn.execute(sa.text(
        "INSERT INTO alert_handling_log (tenant_id, alarm_id, device_id, action, actor, run_id, note) "
        "VALUES (:t, :a, :d, 'handle', :u, NULL, :note)"),
        {"t": ctx.tenant_id, "a": row[2], "d": row[3], "u": ctx.user_id, "note": note})
