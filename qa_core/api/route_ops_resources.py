"""Read-only alert center and device inventory for the console.

Only the caller's tenant rows are returned; no writes, no generation, no
publication. Every read is audited with the same auth context as the other
console APIs.
"""
from datetime import datetime, timezone
import json

import re

import sqlalchemy as sa
from fastapi import APIRouter, Depends, HTTPException, Query, Response
from pydantic import BaseModel, Field

from qa_core.api.auth import AuthContext, get_auth_context, require_roles
from qa_core.api.rate_limit import check_rate_limit
from qa_core.config.settings import get_settings
from qa_core.security.audit import audit_access
from qa_core.api.scope_sql import device_scope, scope_note

router = APIRouter(tags=["ops-resources"])

SEVERITY_LABELS = {"critical": "紧急", "major": "重要", "minor": "次要", "warning": "警告", "info": "提示"}
STATUS_LABELS = {"open": "未处理", "acknowledged": "已确认", "cleared": "已清除", "closed": "已关闭"}
DEVICE_STATUS_LABELS = {"online": "在线", "offline": "离线", "maintenance": "维护", "retired": "退网"}


def _reader(ctx: AuthContext) -> None:
    require_roles(ctx, ("ops", "auditor", "admin"))
    check_rate_limit(ctx.user_id, get_settings().rate_limit_qps)


def _engine():
    return sa.create_engine(get_settings().mysql_dsn, pool_pre_ping=True)


def _iso(value):
    return value.isoformat() if hasattr(value, "isoformat") else value


def _payload(value):
    if not value:
        return {}
    if isinstance(value, dict):
        return value
    try:
        parsed = json.loads(value)
        return parsed if isinstance(parsed, dict) else {"value": parsed}
    except (TypeError, ValueError):
        return {}


def _parse_time_bound(value: str, *, end: bool = False):
    """解析明细时间边界：支持 YYYY-MM-DD 或 ISO 日期时间。

    只给日期时，起点取当天 00:00:00、终点取当天 23:59:59，保证"卡片区间"与"明细列表"
    对同一天的理解一致（含首尾）。
    """
    text = (value or "").strip()
    if not text:
        return None
    try:
        if re.fullmatch(r"\d{4}-\d{2}-\d{2}", text):
            day = datetime.strptime(text, "%Y-%m-%d")
            return day.replace(hour=23, minute=59, second=59, microsecond=999999) if end else day
        parsed = datetime.fromisoformat(text.replace("Z", "+00:00").replace(" ", "T"))
        if parsed.tzinfo is not None:
            parsed = parsed.astimezone(timezone.utc).replace(tzinfo=None)
        return parsed
    except ValueError:
        raise HTTPException(422, f"时间边界格式不正确：{text}（应为 YYYY-MM-DD 或 ISO 日期时间）") from None


@router.get("/alerts")
def alerts(response: Response, page: int = Query(1, ge=1, le=1000),
           page_size: int = Query(20, ge=1, le=50),
           query: str = Query("", max_length=80),
           severity: str = Query("", max_length=16),
           status: str = Query("", max_length=16),
           device_type: str = Query("", max_length=32),
           occurred_from: str = Query("", max_length=32),
           occurred_to: str = Query("", max_length=32),
           ctx: AuthContext = Depends(get_auth_context)):
    response.headers["Cache-Control"] = "no-store"
    _reader(ctx)
    needle = query.strip()
    range_from = _parse_time_bound(occurred_from, end=False)
    range_to = _parse_time_bound(occurred_to, end=True)
    if range_from and range_to and range_from > range_to:
        raise HTTPException(422, "发生时间起点不能晚于终点")
    where = ["a.tenant_id=:t"]
    params = {"t": ctx.tenant_id, "limit": page_size, "offset": (page - 1) * page_size}
    if severity:
        where.append("a.severity=:severity")
        params["severity"] = severity
    if status:
        where.append("a.status=:status")
        params["status"] = status
    if device_type:
        where.append("d.device_type=:device_type")
        params["device_type"] = device_type
    if range_from:
        where.append("a.occurred_at >= :range_from")
        params["range_from"] = range_from
    if range_to:
        where.append("a.occurred_at <= :range_to")
        params["range_to"] = range_to
    if needle:
        where.append("(a.alarm_id LIKE :like OR a.alarm_code LIKE :like OR d.name LIKE :like OR d.device_id LIKE :like)")
        params["like"] = "%" + needle + "%"
    scope_clauses, scope_params, applied, unapplied = device_scope(ctx, 'd')
    where.extend(scope_clauses)
    params.update(scope_params)
    clause = " AND ".join(where)
    scope_clause = (" AND " + " AND ".join(scope_clauses)) if scope_clauses else ""
    engine = _engine()
    try:
        with engine.connect() as conn:
            total = int(conn.execute(sa.text(
                "SELECT COUNT(*) FROM alarms a LEFT JOIN devices d "
                "ON d.device_id=a.device_id AND d.tenant_id=a.tenant_id WHERE " + clause), params).scalar_one())
            rows = conn.execute(sa.text(
                "SELECT a.alarm_id, a.alarm_code, a.severity, a.status, a.device_id, "
                "d.name AS device_name, d.device_type, d.dept_id, a.occurred_at, a.cleared_at, a.source_system, "
                "hs.state AS handling_state "
                "FROM alarms a LEFT JOIN devices d ON d.device_id=a.device_id AND d.tenant_id=a.tenant_id "
                "LEFT JOIN alert_handling_state hs ON hs.tenant_id=a.tenant_id AND hs.alarm_id=a.alarm_id "
                "WHERE " + clause + " ORDER BY FIELD(a.severity,'critical','major','minor','warning','info'), "
                "a.occurred_at DESC LIMIT :limit OFFSET :offset"), params).mappings().all()
            summary_params = {"t": ctx.tenant_id, **scope_params}
            summary_rows = conn.execute(sa.text(
                "SELECT a.status, a.severity, COUNT(*) c FROM alarms a LEFT JOIN devices d "
                "ON d.device_id=a.device_id AND d.tenant_id=a.tenant_id "
                "WHERE a.tenant_id=:t" + scope_clause + " GROUP BY a.status, a.severity"),
                summary_params).mappings().all()
            # “今日新增”按真实日期为 0（演示数据在历史窗口）；同时给出以最近有数据日为终点的 7 天窗口，
            # 避免"有大量告警、今日为 0"的误读。读取时间与事件时间分开呈现。
            win = conn.execute(sa.text(
                "SELECT DATE(MAX(a.occurred_at)) AS d_to, "
                "SUM(a.occurred_at >= (SELECT MAX(occurred_at) FROM alarms WHERE tenant_id=:t)"
                " - INTERVAL 6 DAY) AS recent "
                "FROM alarms a LEFT JOIN devices d ON d.device_id=a.device_id AND d.tenant_id=a.tenant_id "
                "WHERE a.tenant_id=:t" + scope_clause), {"t": ctx.tenant_id, **scope_params}).mappings().first()
            today = conn.execute(sa.text(
                "SELECT COUNT(*) FROM alarms a LEFT JOIN devices d "
                "ON d.device_id=a.device_id AND d.tenant_id=a.tenant_id "
                "WHERE a.tenant_id=:t AND a.occurred_at >= CURDATE()" + scope_clause),
                summary_params).scalar_one()
            device_types = [row[0] for row in conn.execute(sa.text(
                "SELECT DISTINCT d.device_type FROM alarms a JOIN devices d "
                "ON d.device_id=a.device_id AND d.tenant_id=a.tenant_id "
                "WHERE a.tenant_id=:t" + scope_clause + " AND d.device_type IS NOT NULL ORDER BY d.device_type"),
                summary_params).all()]
    finally:
        engine.dispose()
    summary = {"total": 0, "open": 0, "acknowledged": 0, "cleared": 0, "closed": 0,
               "critical_open": 0, "today": int(today),
               "window_recent": int(win["recent"] or 0) if win else 0,
               "data_to": _iso(win["d_to"]) if win and win["d_to"] else None}
    for row in summary_rows:
        count = int(row["c"])
        summary["total"] += count
        summary[row["status"]] = summary.get(row["status"], 0) + count
        if row["status"] in ("open", "acknowledged") and row["severity"] == "critical":
            summary["critical_open"] += count
    audit_access(get_settings().mysql_dsn, actor=ctx.user_id, tenant_id=ctx.tenant_id,
                 auth_source=ctx.source, action="alerts_read", resource="/alerts",
                 decision="allow", roles=ctx.roles, scope=ctx.scope.to_dict(),
                 query=needle or None)
    return {
        "items": [{
            "alarm_id": row["alarm_id"], "alarm_code": row["alarm_code"],
            "severity": row["severity"], "severity_label": SEVERITY_LABELS.get(row["severity"], row["severity"]),
            "status": row["status"], "status_label": STATUS_LABELS.get(row["status"], row["status"]),
            "device_id": row["device_id"], "device_name": row["device_name"] or "—",
            "device_type": row["device_type"] or "—", "dept_id": row["dept_id"] or "—",
            "occurred_at": _iso(row["occurred_at"]), "cleared_at": _iso(row["cleared_at"]),
            "source_system": row["source_system"] or "—",
            "handling_state": row["handling_state"] or "pending_confirm",
            "handling_state_label": HANDLING_STATES.get(row["handling_state"] or "pending_confirm", ("待确认", []))[0],
        } for row in rows],
        "page": page, "page_size": page_size, "total": total,
        "pages": max(1, (total + page_size - 1) // page_size),
        "summary": summary, "device_types": device_types,
        "range": {"from": _iso(range_from), "to": _iso(range_to),
                  "from_date": range_from.date().isoformat() if range_from else None,
                  "to_date": range_to.date().isoformat() if range_to else None},
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "scope": scope_note(ctx, applied, unapplied, "告警列表") + "按级别与发生时间排序。",
        "scope_applied": applied, "scope_unapplied": unapplied,
    }


@router.get("/alerts/{alarm_id}")
def alert_detail(alarm_id: str, response: Response, ctx: AuthContext = Depends(get_auth_context)):
    response.headers["Cache-Control"] = "no-store"
    _reader(ctx)
    if len(alarm_id) > 64:
        raise HTTPException(422, "告警 ID 过长")
    scope_clauses, scope_params, applied, unapplied = device_scope(ctx, 'd')
    scope_clause = (" AND " + " AND ".join(scope_clauses)) if scope_clauses else ""
    engine = _engine()
    try:
        with engine.connect() as conn:
            row = conn.execute(sa.text(
                "SELECT a.*, d.name AS device_name, d.device_type, d.vendor, d.model, "
                "d.software_version, d.status AS device_status, d.dept_id, hs.state AS handling_state, "
                "hs.state_note AS handling_note, hs.updated_at AS handling_updated_at "
                "FROM alarms a LEFT JOIN devices d ON d.device_id=a.device_id AND d.tenant_id=a.tenant_id "
                "LEFT JOIN alert_handling_state hs ON hs.tenant_id=a.tenant_id AND hs.alarm_id=a.alarm_id "
                "WHERE a.tenant_id=:t AND a.alarm_id=:a" + scope_clause),
                {"t": ctx.tenant_id, "a": alarm_id, **scope_params}).mappings().first()
            if not row:
                raise HTTPException(404, "告警不存在或不在当前授权范围")
            recent = conn.execute(sa.text(
                "SELECT a.alarm_id, a.alarm_code, a.severity, a.status, a.occurred_at FROM alarms a "
                "LEFT JOIN devices d ON d.device_id=a.device_id AND d.tenant_id=a.tenant_id "
                "WHERE a.tenant_id=:t AND a.device_id=:d AND a.alarm_id<>:a" + scope_clause
                + " ORDER BY a.occurred_at DESC LIMIT 5"),
                {"t": ctx.tenant_id, "d": row["device_id"], "a": alarm_id, **scope_params}).mappings().all()
    finally:
        engine.dispose()
    audit_access(get_settings().mysql_dsn, actor=ctx.user_id, tenant_id=ctx.tenant_id,
                 auth_source=ctx.source, action="alert_detail", resource="/alerts/" + alarm_id,
                 decision="allow", roles=ctx.roles, scope=ctx.scope.to_dict())
    return {
        "alarm_id": row["alarm_id"], "alarm_code": row["alarm_code"],
        "severity": row["severity"], "severity_label": SEVERITY_LABELS.get(row["severity"], row["severity"]),
        "status": row["status"], "status_label": STATUS_LABELS.get(row["status"], row["status"]),
        "occurred_at": _iso(row["occurred_at"]), "cleared_at": _iso(row["cleared_at"]),
        "source_system": row["source_system"] or "—", "payload": _payload(row["payload_json"]),
        "device": {"device_id": row["device_id"], "name": row["device_name"] or "—",
                   "device_type": row["device_type"] or "—", "vendor": row["vendor"] or "—",
                   "model": row["model"] or "—", "software_version": row["software_version"] or "—",
                   "status": row["device_status"] or "—",
                   "status_label": DEVICE_STATUS_LABELS.get(row["device_status"], row["device_status"] or "—"),
                   "dept_id": row["dept_id"] or "—"},
        "recent": [{"alarm_id": item["alarm_id"], "alarm_code": item["alarm_code"],
                    "severity": item["severity"], "status": item["status"],
                    "occurred_at": _iso(item["occurred_at"])} for item in recent],
        "handling_state": row["handling_state"] or "pending_confirm",
        "handling_state_label": HANDLING_STATES.get(row["handling_state"] or "pending_confirm", ("待确认", []))[0],
        "handling_note": row["handling_note"],
        "handling_updated_at": _iso(row["handling_updated_at"]),
        "scope": scope_note(ctx, applied, unapplied, "告警详情"),
        "scope_applied": applied, "scope_unapplied": unapplied,
    }


HANDLING_ACTIONS = {"confirm": "人工确认", "handle": "处理记录",
                    "observe": "恢复观察", "resolve": "标记解决"}
# 业务处理状态机：pending_confirm → processing → observing → resolved；观察失败回 processing。
# alarms.status 属于上游采集，不由人工备注改写；本状态是应用层独立处置状态。
HANDLING_STATES = {
    "pending_confirm": ("待确认", ["confirm"]),
    "processing": ("处理中", ["handle", "observe"]),
    "observing": ("恢复观察", ["observe", "resolve"]),
    "resolved": ("已解决", []),
}
_RUN_ID_RE = re.compile(r"^[0-9a-f]{32}$")


def _load_state(conn, tenant: str, alarm_id: str) -> str:
    row = conn.execute(sa.text(
        "SELECT state FROM alert_handling_state WHERE tenant_id=:t AND alarm_id=:a"),
        {"t": tenant, "a": alarm_id}).first()
    return row[0] if row else "pending_confirm"


def _save_state(conn, tenant: str, alarm_id: str, state: str, note: str, actor: str) -> None:
    conn.execute(sa.text(
        "INSERT INTO alert_handling_state (tenant_id, alarm_id, state, state_note, updated_by) "
        "VALUES (:t, :a, :s, :n, :u) "
        "ON DUPLICATE KEY UPDATE state=VALUES(state), state_note=VALUES(state_note), updated_by=VALUES(updated_by)"),
        {"t": tenant, "a": alarm_id, "s": state, "n": note or None, "u": actor})


def _last_observe_verdict(conn, tenant: str, alarm_id: str) -> str | None:
    row = conn.execute(sa.text(
        "SELECT verdict FROM alert_handling_log "
        "WHERE tenant_id=:t AND alarm_id=:a AND action='observe' ORDER BY id DESC LIMIT 1"),
        {"t": tenant, "a": alarm_id}).first()
    return row[0] if row else None


def _transition(action: str, state: str, verdict: str | None, last_verdict: str | None, note: str,
                metric_name: str | None, metric_value: str | None, observe_minutes: int | None):
    """纯状态机：返回 (new_state, state_note)；不合法组合抛 ValueError（由调用方转 409/422）。"""
    allowed = HANDLING_STATES[state][1]
    if action not in allowed:
        raise ValueError(f"当前状态为「{HANDLING_STATES[state][0]}」，不能执行「{HANDLING_ACTIONS.get(action, action)}」"
                         + ("；请先人工确认" if state == "pending_confirm" else ""))
    if action == "confirm":
        return "processing", note or "人工确认诊断结论，进入处理"
    if action == "handle":
        return "processing", note or None
    if action == "observe":
        if not (metric_name and metric_value and observe_minutes and verdict):
            raise ValueError("恢复观察必须提供验证依据：metric_name（指标名）、metric_value（处理后值）、"
                             "observe_minutes（观察窗口分钟）、verdict（pass/fail）")
        if verdict == "fail":
            return "processing", (note or "恢复观察未通过") + f"（{metric_name}={metric_value}）"
        return "observing", note or None
    # resolve
    if last_verdict != "pass":
        raise ValueError("最近一次恢复观察未通过（或还没有观察记录）；未恢复不能标记解决")
    return "resolved", note or "恢复观察通过，标记解决"


class HandlingBody(BaseModel):
    action: str = Field(max_length=16)
    note: str = Field(default="", max_length=500)
    run_id: str | None = Field(default=None, max_length=80)
    metric_name: str | None = Field(default=None, max_length=80)
    metric_value: str | None = Field(default=None, max_length=80)
    observe_minutes: int | None = Field(default=None, ge=1, le=10080)
    verdict: str | None = Field(default=None, max_length=8)


@router.get("/alerts/{alarm_id}/handling")
def alert_handling(alarm_id: str, response: Response, ctx: AuthContext = Depends(get_auth_context)):
    """告警处理时间线：正式工作流记录（人工确认/处理记录/恢复观察）。"""
    response.headers["Cache-Control"] = "no-store"
    _reader(ctx)
    if len(alarm_id) > 64:
        raise HTTPException(422, "告警 ID 过长")
    engine = _engine()
    try:
        with engine.connect() as conn:
            state = _load_state(conn, ctx.tenant_id, alarm_id)
            last_verdict = _last_observe_verdict(conn, ctx.tenant_id, alarm_id)
            rows = conn.execute(sa.text(
                "SELECT id, action, actor, run_id, note, created_at, metric_name, metric_value, "
                "observe_minutes, verdict FROM alert_handling_log "
                "WHERE tenant_id=:t AND alarm_id=:a ORDER BY id"),
                {"t": ctx.tenant_id, "a": alarm_id}).mappings().all()
    finally:
        engine.dispose()
    label, next_actions = HANDLING_STATES[state], HANDLING_STATES[state][1]
    return {"alarm_id": alarm_id, "state": state, "state_label": label[0],
            "next_actions": next_actions,
            "last_observe_verdict": last_verdict,
            "items": [
        {"id": row["id"], "action": row["action"], "action_label": HANDLING_ACTIONS.get(row["action"], row["action"]),
         "actor": row["actor"], "run_id": row["run_id"], "note": row["note"] or "",
         "metric_name": row["metric_name"], "metric_value": row["metric_value"],
         "observe_minutes": row["observe_minutes"], "verdict": row["verdict"],
         "created_at": _iso(row["created_at"])} for row in rows]}


@router.post("/alerts/{alarm_id}/handling", status_code=201)
def alert_handling_create(alarm_id: str, body: HandlingBody, response: Response,
                          ctx: AuthContext = Depends(get_auth_context)):
    """写入一条处理记录。告警必须存在且在授权范围内；动作白名单校验。"""
    response.headers["Cache-Control"] = "no-store"
    _reader(ctx)
    if len(alarm_id) > 64:
        raise HTTPException(422, "告警 ID 过长")
    if body.action not in HANDLING_ACTIONS:
        raise HTTPException(422, f"不支持的处理动作：{body.action}（可用：{'、'.join(HANDLING_ACTIONS)}）")
    run_id = (body.run_id or "").strip()
    if run_id and not _RUN_ID_RE.match(run_id):
        raise HTTPException(422, "run_id 格式无效（应为诊断返回的 32 位 run 标识）")
    verdict = (body.verdict or "").strip().lower() or None
    if verdict and verdict not in ("pass", "fail"):
        raise HTTPException(422, "verdict 只支持 pass / fail")
    scope_clauses, scope_params, applied, unapplied = device_scope(ctx, 'd')
    scope_clause = (" AND " + " AND ".join(scope_clauses)) if scope_clauses else ""
    engine = _engine()
    try:
        # 写账本与状态必须显式事务：connect() 结束会回滚未提交语句。
        with engine.begin() as conn:
            row = conn.execute(sa.text(
                "SELECT a.device_id FROM alarms a LEFT JOIN devices d "
                "ON d.device_id=a.device_id AND d.tenant_id=a.tenant_id "
                "WHERE a.tenant_id=:t AND a.alarm_id=:a" + scope_clause),
                {"t": ctx.tenant_id, "a": alarm_id, **scope_params}).first()
            if not row:
                raise HTTPException(404, "告警不存在或不在当前授权范围")
            state = _load_state(conn, ctx.tenant_id, alarm_id)
            note = body.note.strip()
            try:
                new_state, state_note = _transition(body.action, state, verdict,
                                                    _last_observe_verdict(conn, ctx.tenant_id, alarm_id),
                                                    note, (body.metric_name or "").strip() or None,
                                                    (body.metric_value or "").strip() or None, body.observe_minutes)
            except ValueError as exc:
                raise HTTPException(409, str(exc))
            conn.execute(sa.text(
                "INSERT INTO alert_handling_log (tenant_id, alarm_id, device_id, action, actor, run_id, note, "
                "metric_name, metric_value, observe_minutes, verdict) "
                "VALUES (:t, :a, :d, :action, :actor, :run, :note, :mn, :mv, :om, :v)"),
                {"t": ctx.tenant_id, "a": alarm_id, "d": row[0], "action": body.action,
                 "actor": ctx.user_id, "run": run_id or None, "note": note or None,
                 "mn": (body.metric_name or "").strip() or None, "mv": (body.metric_value or "").strip() or None,
                 "om": body.observe_minutes, "v": verdict})
            _save_state(conn, ctx.tenant_id, alarm_id, new_state, state_note, ctx.user_id)
    finally:
        engine.dispose()
    audit_access(get_settings().mysql_dsn, actor=ctx.user_id, tenant_id=ctx.tenant_id,
                 auth_source=ctx.source, action="alert_handling_" + body.action,
                 resource="/alerts/" + alarm_id + "/handling", decision="allow",
                 roles=ctx.roles, scope=ctx.scope.to_dict())
    return {"alarm_id": alarm_id, "action": body.action, "action_label": HANDLING_ACTIONS[body.action],
            "actor": ctx.user_id, "run_id": run_id or None,
            "state": new_state, "state_label": HANDLING_STATES[new_state][0]}


@router.get("/devices")
def devices(response: Response, page: int = Query(1, ge=1, le=1000),
            page_size: int = Query(50, ge=1, le=100),
            query: str = Query("", max_length=80),
            status: str = Query("", max_length=16),
            device_type: str = Query("", max_length=32),
            ctx: AuthContext = Depends(get_auth_context)):
    response.headers["Cache-Control"] = "no-store"
    _reader(ctx)
    needle = query.strip()
    where = ["tenant_id=:t"]
    params = {"t": ctx.tenant_id, "limit": page_size, "offset": (page - 1) * page_size}
    scope_clauses, scope_params, applied, unapplied = device_scope(ctx, '')
    where.extend(scope_clauses)
    params.update(scope_params)
    if status:
        where.append("status=:status")
        params["status"] = status
    if device_type:
        where.append("device_type=:device_type")
        params["device_type"] = device_type
    if needle:
        where.append("(device_id LIKE :like OR name LIKE :like OR cmdb_id LIKE :like)")
        params["like"] = "%" + needle + "%"
    clause = " AND ".join(where)
    engine = _engine()
    try:
        with engine.connect() as conn:
            total = int(conn.execute(sa.text("SELECT COUNT(*) FROM devices WHERE " + clause), params).scalar_one())
            rows = conn.execute(sa.text(
                "SELECT device_id, name, device_type, vendor, model, network_tech, software_version, "
                "status, dept_id, cmdb_id FROM devices WHERE " + clause +
                " ORDER BY FIELD(status,'online','maintenance','offline','retired'), name LIMIT :limit OFFSET :offset"),
                params).mappings().all()
            by_status = conn.execute(sa.text(
                "SELECT status, COUNT(*) c FROM devices WHERE tenant_id=:t GROUP BY status"),
                {"t": ctx.tenant_id}).mappings().all()
            by_type = conn.execute(sa.text(
                "SELECT device_type, COUNT(*) c FROM devices WHERE tenant_id=:t GROUP BY device_type "
                "ORDER BY c DESC, device_type"), {"t": ctx.tenant_id}).mappings().all()
    finally:
        engine.dispose()
    audit_access(get_settings().mysql_dsn, actor=ctx.user_id, tenant_id=ctx.tenant_id,
                 auth_source=ctx.source, action="devices_read", resource="/devices",
                 decision="allow", roles=ctx.roles, scope=ctx.scope.to_dict(),
                 query=needle or None)
    return {
        "items": [{"device_id": row["device_id"], "name": row["name"], "device_type": row["device_type"],
                   "vendor": row["vendor"] or "—", "model": row["model"] or "—",
                   "network_tech": row["network_tech"] or "—", "software_version": row["software_version"] or "—",
                   "status": row["status"], "status_label": DEVICE_STATUS_LABELS.get(row["status"], row["status"]),
                   "dept_id": row["dept_id"] or "—", "cmdb_id": row["cmdb_id"] or "—"} for row in rows],
        "page": page, "page_size": page_size, "total": total,
        "pages": max(1, (total + page_size - 1) // page_size),
        "summary": {"by_status": [{"name": row["status"], "label": DEVICE_STATUS_LABELS.get(row["status"], row["status"]),
                                   "count": int(row["c"])} for row in by_status],
                    "by_type": [{"name": row["device_type"], "count": int(row["c"])} for row in by_type]},
        "scope": scope_note(ctx, applied, unapplied, "设备清单"),
        "scope_applied": applied, "scope_unapplied": unapplied,
    }
