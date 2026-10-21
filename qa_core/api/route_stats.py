# -*- coding: utf-8 -*-
"""业务固定统计路由（T5/C1）：五类常用统计，参数化模板执行，不做任意 SQL。

统一窗口规则（重要）：
- 演示数据与当前日期不同，窗口默认锚定"最近有数据时刻"，而不是真实今天；
- 7d/30d 表示**包含锚点日在内的 N 个日历日**，区间固定为 [锚点日 00:00, 锚点时刻]；
- 分子、分母、展示标签、明细钻取参数全部由同一个 `window` 计算，避免"卡片 7 天、明细 6 天"。
"""
from datetime import datetime, time, timedelta, timezone

import sqlalchemy as sa
from fastapi import APIRouter, Depends, HTTPException, Query, Response

from qa_core.api.auth import AuthContext, get_auth_context, require_roles
from qa_core.api.rate_limit import check_rate_limit
from qa_core.api.scope_sql import device_scope, scope_note
from qa_core.config.settings import get_settings
from qa_core.security.audit import audit_access

router = APIRouter()

STAT_KINDS = {
    "alarm_count": "活动与新增告警数量",
    "severity_distribution": "告警级别分布",
    "device_online_rate": "设备在线率",
    "ticket_backlog": "工单积压与超时",
    "kpi_trend": "KPI 指标趋势",
}
WINDOWS = {"7d": 7, "30d": 30}
_EPOCH = datetime(1970, 1, 1)  # MySQL DATETIME 下界 1000 年，不能用 _EPOCH


def _iso(value):
    if isinstance(value, datetime):
        return value.isoformat(sep=' ')
    return str(value) if value else None


def window_bounds(anchor, days):
    """统一窗口：包含锚点日在内的 days 个日历日 → (start, end)。

    纯函数，便于单测；start 为锚点日前 (days-1) 天的 00:00，end 为锚点时刻本身。
    """
    if anchor is None:
        return None, None
    start = datetime.combine(anchor.date() - timedelta(days=max(0, days - 1)), time.min)
    return start, anchor


def window_payload(start, end, days):
    if start is None or end is None:
        return None
    return {"from": _iso(start), "to": _iso(end), "days": days,
            "from_date": start.date().isoformat(), "to_date": end.date().isoformat(),
            "anchor": _iso(end)}


def _int(value):
    return int(value or 0)


@router.get("/stats/{kind}")
def stats(kind: str, response: Response, window: str = Query("7d", max_length=4),
          metric: str = Query("", max_length=40),
          ctx: AuthContext = Depends(get_auth_context)):
    """五类固定统计。window 支持 7d / 30d；kpi_trend 可选 metric（默认 cpu_usage）。"""
    response.headers["Cache-Control"] = "no-store"
    require_roles(ctx, ('admin', 'ops', 'auditor'))
    check_rate_limit(ctx.user_id, get_settings().rate_limit_qps)
    if kind not in STAT_KINDS:
        raise HTTPException(422, f"不支持的统计类型：{kind}（可用：{'、'.join(STAT_KINDS)}）")
    if window not in WINDOWS:
        raise HTTPException(422, "window 只支持 7d / 30d")
    days = WINDOWS[window]
    scope_clauses, scope_params, applied, unapplied = device_scope(ctx, 'd', prefix='st')
    scope_clause = (" AND " + " AND ".join(scope_clauses)) if scope_clauses else ""
    base = {"t": ctx.tenant_id, **scope_params}
    engine = sa.create_engine(get_settings().mysql_dsn, pool_pre_ping=True)
    try:
        with engine.connect() as conn:
            payload = {"kind": kind, "kind_label": STAT_KINDS[kind], "window": window,
                       "scope": scope_note(ctx, applied, unapplied, STAT_KINDS[kind]),
                       "scope_applied": applied, "scope_unapplied": unapplied,
                       "generated_at": datetime.now(timezone.utc).isoformat()}

            if kind == "alarm_count":
                # 统一锚点（只算授权范围内）；窗口与 severity/明细共用同一 start/end。
                anchor = conn.execute(sa.text(
                    "SELECT MAX(a.occurred_at) FROM alarms a "
                    "LEFT JOIN devices d ON d.device_id=a.device_id AND d.tenant_id=a.tenant_id "
                    "WHERE a.tenant_id=:t" + scope_clause), base).scalar()
                start, end = window_bounds(anchor, days)
                win = window_payload(start, end, days)
                row = conn.execute(sa.text(
                    "SELECT COUNT(*) AS total, "
                    "SUM(a.status IN ('open','acknowledged')) AS active, "
                    "SUM(a.occurred_at >= :ws AND a.occurred_at <= :we) AS recent "
                    "FROM alarms a LEFT JOIN devices d ON d.device_id=a.device_id AND d.tenant_id=a.tenant_id "
                    "WHERE a.tenant_id=:t" + scope_clause),
                    {**base, "ws": start or _EPOCH, "we": end or _EPOCH}).mappings().first()
                payload["result"] = {
                    "unit": "条",
                    "time_window": (f"数据窗口 {win['from_date']} ~ {win['to_date']}（含首尾 {days} 个日历日）"
                                    if win else "范围内暂无告警数据"),
                    "formula": "活动告警 = 状态 open/acknowledged（全量快照）；"
                               "新增告警 = occurred_at 落在同一数据窗口；分母 = 授权范围内全部告警",
                    "active": _int(row["active"]), "recent": _int(row["recent"]),
                    "total": _int(row["total"]), "data_window": win}
                if win:
                    payload["detail"] = {"view": "alerts", "params": {
                        "subtab": "alerts", "occurred_from": win["from_date"],
                        "occurred_to": win["to_date"], "reset": "true"},
                        "label": "告警列表（同一时间窗口）"}
                    payload["detail_hint"] = (f"同范围明细：告警与资源，occurred_at "
                                              f"{win['from_date']} 00:00 ~ {win['to_date']} 23:59")
                else:
                    payload["detail"] = {"view": "alerts", "params": {"subtab": "alerts"}, "label": "告警列表"}
            elif kind == "severity_distribution":
                anchor = conn.execute(sa.text(
                    "SELECT MAX(a.occurred_at) FROM alarms a "
                    "LEFT JOIN devices d ON d.device_id=a.device_id AND d.tenant_id=a.tenant_id "
                    "WHERE a.tenant_id=:t" + scope_clause), base).scalar()
                start, end = window_bounds(anchor, days)
                win = window_payload(start, end, days)
                rows = conn.execute(sa.text(
                    "SELECT a.severity, COUNT(*) c FROM alarms a "
                    "LEFT JOIN devices d ON d.device_id=a.device_id AND d.tenant_id=a.tenant_id "
                    "WHERE a.tenant_id=:t AND a.occurred_at >= :ws AND a.occurred_at <= :we"
                    + scope_clause + " GROUP BY a.severity"),
                    {**base, "ws": start or _EPOCH, "we": end or _EPOCH}).mappings().all()
                labels = {"critical": "紧急", "major": "重要", "minor": "次要",
                          "warning": "警告", "info": "提示"}
                total = sum(_int(r["c"]) for r in rows)
                payload["result"] = {
                    "unit": "条",
                    "time_window": (f"数据窗口 {win['from_date']} ~ {win['to_date']}（含首尾 {days} 个日历日）"
                                    if win else "范围内暂无告警数据"),
                    "formula": "按级别分组计数；分母 = 同一数据窗口内授权范围全部告警（total 与告警数量卡的 recent 相等）",
                    "total": total, "data_window": win,
                    "items": [{"severity": r["severity"], "label": labels.get(r["severity"], r["severity"]),
                               "count": _int(r["c"]), "ratio": round(_int(r["c"]) / total, 4) if total else 0}
                              for r in rows]}
                if win:
                    payload["detail"] = {"view": "alerts", "params": {
                        "subtab": "alerts", "occurred_from": win["from_date"],
                        "occurred_to": win["to_date"], "reset": "true"},
                        "label": "告警列表（同一时间窗口）"}
                else:
                    payload["detail"] = {"view": "alerts", "params": {"subtab": "alerts"}, "label": "告警列表"}
            elif kind == "device_online_rate":
                # 公式：在线率 = online 台数 / 授权范围内全部在册设备（maintenance 计入分母）
                row = conn.execute(sa.text(
                    "SELECT SUM(d.status='online') AS online, COUNT(*) AS total "
                    "FROM devices d WHERE d.tenant_id=:t" + scope_clause), base).mappings().first()
                total = _int(row["total"])
                online = _int(row["online"])
                payload["result"] = {
                    "unit": "%", "time_window": "当前快照",
                    "formula": "在线率 = status=online 设备数 / 在册设备总数（maintenance 计入分母，非在线）",
                    "online": online, "total": total,
                    "rate": round(online * 100.0 / total, 1) if total else None,
                    "note": "演示数据状态含 online / maintenance；无 offline 状态设备",
                    "data_window": None}
                payload["detail"] = {"view": "alerts", "params": {"subtab": "devices"},
                                     "label": "设备列表（同一授权范围）"}
            elif kind == "ticket_backlog":
                # 存储枚举是 open/processing/closed；in_progress 只是展示层历史别名。
                anchor = conn.execute(sa.text(
                    "SELECT MAX(w.opened_at) FROM work_orders w "
                    "LEFT JOIN devices d ON d.device_id=w.device_id AND d.tenant_id=w.tenant_id "
                    "WHERE w.tenant_id=:t" + scope_clause), base).scalar()
                overdue_before = (anchor - timedelta(hours=24)) if anchor else None
                row = conn.execute(sa.text(
                    "SELECT SUM(w.status IN ('open','processing')) AS backlog, "
                    "SUM(w.status IN ('open','processing') AND w.opened_at < :before) AS overdue, "
                    "COUNT(*) AS total FROM work_orders w "
                    "LEFT JOIN devices d ON d.device_id=w.device_id AND d.tenant_id=w.tenant_id "
                    "WHERE w.tenant_id=:t" + scope_clause),
                    {**base, "before": overdue_before or _EPOCH}).mappings().first()
                payload["result"] = {
                    "unit": "张", "time_window": "当前快照（超时以最近工单时间为基准）",
                    "formula": "积压 = 状态 open/processing；超时 = 积压中 opened_at 早于最近工单时间 24 小时",
                    "backlog": _int(row["backlog"]), "overdue_24h": _int(row["overdue"]),
                    "total": _int(row["total"]), "data_window": None,
                    "anchor": _iso(anchor)}
                payload["detail"] = {"view": "alerts",
                                     "params": {"subtab": "tickets", "state": "active"},
                                     "label": "工单列表（待受理 + 处理中）"}
            else:  # kpi_trend
                name = metric.strip() or 'cpu_usage'
                valid = conn.execute(sa.text(
                    "SELECT DISTINCT metric_name FROM monitoring_metrics WHERE tenant_id=:t"),
                    {"t": ctx.tenant_id}).scalars().all()
                if name not in valid:
                    raise HTTPException(422, f"指标 {name} 不存在；可用：{'、'.join(sorted(valid))}")
                anchor = conn.execute(sa.text(
                    "SELECT MAX(m.sampled_at) FROM monitoring_metrics m "
                    "LEFT JOIN devices d ON d.device_id=m.device_id AND d.tenant_id=m.tenant_id "
                    "WHERE m.tenant_id=:t AND m.metric_name=:mn" + scope_clause),
                    {**base, "mn": name}).scalar()
                start, end = window_bounds(anchor, days)
                win = window_payload(start, end, days)
                rows = conn.execute(sa.text(
                    "SELECT DATE(m.sampled_at) AS day, ROUND(AVG(m.metric_value), 2) AS avg_value, "
                    "COUNT(*) AS samples FROM monitoring_metrics m "
                    "LEFT JOIN devices d ON d.device_id=m.device_id AND d.tenant_id=m.tenant_id "
                    "WHERE m.tenant_id=:t AND m.metric_name=:mn "
                    "AND m.sampled_at >= :ws AND m.sampled_at <= :we"
                    + scope_clause + " GROUP BY DATE(m.sampled_at) ORDER BY day"),
                    {**base, "mn": name, "ws": start or _EPOCH, "we": end or _EPOCH}).mappings().all()
                unit_row = conn.execute(sa.text(
                    "SELECT unit FROM monitoring_metrics WHERE tenant_id=:t AND metric_name=:mn LIMIT 1"),
                    {"t": ctx.tenant_id, "mn": name}).scalar()
                unit = (unit_row or '').strip()
                unit = {'percent': '%', 'pct': '%'}.get(unit.lower(), unit)
                payload["result"] = {
                    "metric": name, "unit": unit,
                    "time_window": (f"数据窗口 {win['from_date']} ~ {win['to_date']}（含首尾 {days} 个日历日，按天均值）"
                                    if win else "范围内暂无采样"),
                    "formula": "按天取指标均值；分母 = 当日采样点数；窗口与趋势图同源",
                    "samples": sum(_int(r["samples"]) for r in rows),
                    "data_window": win,
                    "trend": [{"day": _iso(r["day"])[:10], "avg": float(r["avg_value"]),
                               "samples": _int(r["samples"])} for r in rows]}
                payload["detail"] = {"view": "alerts", "params": {"subtab": "devices"},
                                     "label": "设备列表（同一授权范围）"}
    finally:
        engine.dispose()
    audit_access(get_settings().mysql_dsn, actor=ctx.user_id, tenant_id=ctx.tenant_id,
                 auth_source=ctx.source, action="stats_read", resource=f"/stats/{kind}",
                 decision="allow", roles=ctx.roles)
    return payload
