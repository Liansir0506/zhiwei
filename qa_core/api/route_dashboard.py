"""Read-only operations dashboard; no generation, no publication, no state changes.

Every number comes from the platform's own MySQL tables for the caller's tenant, and the
payload states the demo data window so fixed historical samples are never presented as
live values. Roles match the other read-only APIs: ops/auditor/admin.
"""
from datetime import datetime, timedelta, timezone
import sqlalchemy as sa
from fastapi import APIRouter, Depends, Query, Response

from qa_core.api.auth import AuthContext, get_auth_context, require_roles
from qa_core.config.settings import get_settings
from qa_core.security.audit import audit_access
from qa_core.api.scope_sql import device_scope, scope_note

router = APIRouter(prefix="/dashboard", tags=["dashboard"])

SEVERITY_LABELS = {"critical": "紧急", "major": "重要", "minor": "次要", "warning": "警告", "info": "提示"}
STATUS_LABELS = {"open": "未处理", "acknowledged": "已确认", "cleared": "已清除", "closed": "已关闭"}
ROUTE_LABELS = {"faq_exact": "FAQ 直出", "retrieval": "检索回答", "structured_lookup": "结构化查询",
                "direct_answer": "直答", "workflow": "流程指引"}
PRIORITY_LABELS = {"P1": "P1 紧急", "P2": "P2 高", "P3": "P3 中", "P4": "P4 低"}
DEVICE_STATUS_LABELS = {"online": "在线", "offline": "离线", "maintenance": "维护", "retired": "退网"}
METRIC_LABELS = {"cpu_usage": "CPU 使用率", "packet_loss": "丢包率", "latency": "时延"}


def _group(conn, sql, column, params, labels=None, order=None):
    rows = conn.execute(sa.text(sql), params).mappings().all()
    result = [{"name": labels.get(row[column], row[column]) if labels else row[column],
               "raw": row[column], "count": int(row["c"])} for row in rows]
    if order:
        result.sort(key=lambda row: order.index(row["raw"]) if row["raw"] in order else len(order))
    else:
        result.sort(key=lambda row: -row["count"])
    return result


def _daily_trend(conn, sql, params, start, days):
    rows = conn.execute(sa.text(sql), dict(params, start=start)).mappings().all()
    buckets = {row["bucket"]: int(row["total"]) for row in rows}
    labels, values = [], []
    for index in range(days):
        day = (start + timedelta(days=index)).date()
        labels.append(day.strftime("%m-%d"))
        values.append(buckets.get(day, 0))
    return {"labels": labels, "values": values}


@router.get('/summary')
def summary(response: Response, days: int = Query(14, ge=3, le=60),
            ctx: AuthContext = Depends(get_auth_context)):
    response.headers['Cache-Control'] = 'no-store'
    require_roles(ctx, ("ops", "auditor", "admin"))
    params = {"t": ctx.tenant_id}
    dev_scope, dev_params, applied, unapplied = device_scope(ctx, '', prefix='dev')
    dev_clause = (" AND " + " AND ".join(dev_scope)) if dev_scope else ""
    params.update(dev_params)
    engine = sa.create_engine(get_settings().mysql_dsn, pool_pre_ping=True)
    try:
        with engine.connect() as conn:
            one = lambda sql, **extra: conn.execute(sa.text(sql), dict(params, **extra)).scalar_one()

            # ---- 领域资源：设备（本体即收窄） ----
            device_total = int(one("SELECT COUNT(*) FROM devices WHERE tenant_id=:t" + dev_clause))
            device_status = _group(conn, "SELECT status, COUNT(*) c FROM devices WHERE tenant_id=:t"
                                        + dev_clause + " GROUP BY status",
                                   "status", params, DEVICE_STATUS_LABELS,
                                   order=["online", "maintenance", "offline", "retired"])
            device_types = _group(conn, "SELECT device_type, COUNT(*) c FROM devices WHERE tenant_id=:t"
                                        + dev_clause + " GROUP BY device_type ORDER BY c DESC, device_type LIMIT 6",
                                  "device_type", params)
            # ---- 告警：窗口取数据自身最近区间，固定样本不被当作实时 ----
            # 告警按设备维度收窄：JOIN devices 后套用同一 scope（与 /alerts 一致）。
            al_scope, al_params, al_applied, al_unapplied = device_scope(ctx, 'd', prefix='al')
            params.update(al_params)
            al_clause = (" AND " + " AND ".join(al_scope)) if al_scope else ""
            al_join = ("FROM alarms a LEFT JOIN devices d ON d.device_id=a.device_id AND d.tenant_id=a.tenant_id ")
            alarm_total = int(one("SELECT COUNT(*) " + al_join + "WHERE a.tenant_id=:t" + al_clause))
            alarm_open = int(one("SELECT COUNT(*) " + al_join
                                 + "WHERE a.tenant_id=:t AND a.status IN ('open','acknowledged')" + al_clause))
            alarm_critical_open = int(one("SELECT COUNT(*) " + al_join
                                          + "WHERE a.tenant_id=:t AND a.status IN ('open','acknowledged') "
                                            "AND a.severity='critical'" + al_clause))
            alarm_end = one("SELECT MAX(a.occurred_at) " + al_join + "WHERE a.tenant_id=:t" + al_clause)
            alarm_start = ((alarm_end or datetime.now()) - timedelta(days=days - 1)).replace(
                hour=0, minute=0, second=0, microsecond=0)
            alarm_trend = _daily_trend(conn, "SELECT DATE(a.occurred_at) AS bucket, COUNT(*) AS total " + al_join
                                        + "WHERE a.tenant_id=:t AND a.occurred_at>=:start" + al_clause
                                        + " GROUP BY bucket ORDER BY bucket",
                                       params, alarm_start, days)
            alarm_severity = _group(conn, "SELECT a.severity, COUNT(*) c " + al_join
                                    + "WHERE a.tenant_id=:t" + al_clause + " GROUP BY a.severity",
                                    "severity", params, SEVERITY_LABELS,
                                    order=["critical", "major", "minor", "warning", "info"])
            alarm_status = _group(conn, "SELECT a.status, COUNT(*) c " + al_join
                                  + "WHERE a.tenant_id=:t" + al_clause + " GROUP BY a.status",
                                  "status", params, STATUS_LABELS,
                                  order=["open", "acknowledged", "cleared", "closed"])
            alarm_codes = _group(conn, "SELECT a.alarm_code, COUNT(*) c " + al_join
                                  + "WHERE a.tenant_id=:t" + al_clause
                                  + " GROUP BY a.alarm_code ORDER BY c DESC, a.alarm_code LIMIT 5",
                                  "alarm_code", params)
            pending_alarms = [{"alarm_id": row[0], "alarm_code": row[1], "severity": SEVERITY_LABELS.get(row[2], row[2]),
                               "status": STATUS_LABELS.get(row[3], row[3]), "device_id": row[4], "device_name": row[5] or "—",
                               "occurred_at": row[6].strftime("%m-%d %H:%M") if row[6] else ""}
                              for row in conn.execute(sa.text(
                                  "SELECT a.alarm_id, a.alarm_code, a.severity, a.status, a.device_id, d.name, a.occurred_at "
                                  + al_join + "WHERE a.tenant_id=:t AND a.status IN ('open','acknowledged')" + al_clause + " "
                                  "ORDER BY FIELD(a.severity,'critical','major','minor','warning','info'), a.occurred_at DESC "
                                  "LIMIT 6"), dict(params, **al_params)).all()]
            # ---- 工单与变更 ----
            # 工单体有 dept_id；设备类型经 JOIN devices 收窄（与告警一致）。
            wk_scope, wk_params, wk_applied, wk_unapplied = device_scope(ctx, 'wj', prefix='wk')
            params.update(wk_params)
            wk_join = ("FROM work_orders w LEFT JOIN devices wj "
                       "ON wj.device_id=w.device_id AND wj.tenant_id=w.tenant_id ")
            wk_clause = (" AND " + " AND ".join(wk_scope)) if wk_scope else ""
            work_total = int(one("SELECT COUNT(*) " + wk_join + "WHERE w.tenant_id=:t" + wk_clause))
            work_priority = _group(conn, "SELECT w.priority, COUNT(*) c " + wk_join
                                   + "WHERE w.tenant_id=:t" + wk_clause + " GROUP BY w.priority",
                                   "priority", params, PRIORITY_LABELS, order=["P1", "P2", "P3", "P4"])
            work_depts = _group(conn, "SELECT w.dept_id, COUNT(*) c " + wk_join
                                      + "WHERE w.tenant_id=:t" + wk_clause
                                      + " GROUP BY w.dept_id ORDER BY c DESC, w.dept_id LIMIT 5", "dept_id", params)
            # change_records 无 dept_id 列：部门维度对该表未建模，记入未生效说明。
            ch_scope, ch_params, ch_applied, ch_unapplied = device_scope(ctx, 'cj', prefix='ch')
            params.update(ch_params)
            ch_join = ("FROM change_records c LEFT JOIN devices cj "
                       "ON cj.device_id=c.device_id AND cj.tenant_id=c.tenant_id ")
            ch_clause = (" AND " + " AND ".join(ch_scope)) if ch_scope else ""
            change_status = _group(conn, "SELECT c.status, COUNT(*) c " + ch_join
                                         + "WHERE c.tenant_id=:t" + ch_clause + " GROUP BY c.status", "status", params)
            change_total = sum(row["count"] for row in change_status)
            change_types = _group(conn, "SELECT c.change_type, COUNT(*) c " + ch_join
                                        + "WHERE c.tenant_id=:t" + ch_clause
                                        + " GROUP BY c.change_type ORDER BY c DESC", "change_type", params)
            # ---- 知识与治理 ----
            versions = {row[0]: int(row[1]) for row in conn.execute(
                sa.text("SELECT status, COUNT(*) FROM kb_versions GROUP BY status")).all()}
            active_version = conn.execute(sa.text("SELECT version FROM kb_versions WHERE status='ACTIVE'")).scalar_one_or_none()
            asset_count = int(one("SELECT COUNT(*) FROM knowledge_assets WHERE status='active' AND tenant_id=:t"))
            # evidence_locations 没有 tenant_id，只能通过资产校验和归属到租户；无资产租户必须为 0。
            location_count = int(one(
                "SELECT COUNT(DISTINCT el.identity_hash) FROM evidence_locations el JOIN knowledge_assets ka "
                "ON ka.checksum = JSON_UNQUOTE(JSON_EXTRACT(el.location_json,'$.file_sha256')) "
                "WHERE ka.tenant_id=:t AND ka.status='active'"))
            if not asset_count:
                # 没有可见知识资产的租户不应看到平台级版本计数或激活版本。
                versions = {"ACTIVE": 0, "STAGED": 0, "ARCHIVED": 0}
                active_version = None
            ingest_jobs = [{"job_id": row[0], "kb_version": row[1], "status": row[2], "stage": row[3],
                            "done": row[4], "total": row[5]}
                           for row in conn.execute(sa.text("SELECT job_id, kb_version, status, stage, progress_done, "
                                                           "progress_total FROM ingest_jobs WHERE tenant_id=:t "
                                                           "ORDER BY created_at DESC LIMIT 3"), params).all()]
            # eval_runs 是平台级表（无 tenant_id），只对管理员/审计员展示，避免运维账号跨租户读取。
            privileged = any(role in ('admin', 'auditor') for role in ctx.roles)
            eval_runs = ([{"run_id": row[0], "dataset": row[1], "created_at": row[2].strftime("%m-%d %H:%M") if row[2] else ""}
                          for row in conn.execute(sa.text("SELECT run_id, dataset, created_at FROM eval_runs "
                                                          "ORDER BY created_at DESC LIMIT 3")).all()]
                         if privileged else [])
            # ---- 问答与反馈（按会话所属租户归集） ----
            join = ("SELECT COUNT(*) FROM messages m JOIN sessions s ON s.session_id=m.session_id "
                    "WHERE s.tenant_id=:t AND m.role='user'")
            question_total = int(one(join))
            session_total = int(one("SELECT COUNT(*) FROM sessions WHERE tenant_id=:t"))
            qa_end = one("SELECT MAX(m.created_at) FROM messages m JOIN sessions s ON s.session_id=m.session_id "
                         "WHERE s.tenant_id=:t AND m.role='user'")
            qa_start = ((qa_end or datetime.now()) - timedelta(days=days - 1)).replace(
                hour=0, minute=0, second=0, microsecond=0)
            qa_trend = _daily_trend(conn, "SELECT DATE(m.created_at) AS bucket, COUNT(*) AS total "
                                          "FROM messages m JOIN sessions s ON s.session_id=m.session_id "
                                          "WHERE s.tenant_id=:t AND m.role='user' AND m.created_at>=:start "
                                          "GROUP BY bucket ORDER BY bucket", params, qa_start, days)
            qa_routes = _group(conn, "SELECT m.route, COUNT(*) c FROM messages m JOIN sessions s ON s.session_id=m.session_id "
                                     "WHERE s.tenant_id=:t AND m.role='assistant' AND m.route<>'' GROUP BY m.route "
                                     "ORDER BY c DESC", "route", params, ROUTE_LABELS)
            feedback = {row[0]: int(row[1]) for row in conn.execute(sa.text(
                "SELECT f.rating, COUNT(*) FROM feedback f JOIN messages m ON m.message_id=f.message_id "
                "JOIN sessions s ON s.session_id=m.session_id WHERE s.tenant_id=:t GROUP BY f.rating"), params).all()}
            up, down = feedback.get("up", 0), feedback.get("down", 0)
            # ---- 监控指标（性能趋势） ----
            performance = []
            for metric in ("cpu_usage", "packet_loss", "latency"):
                rows = conn.execute(sa.text("SELECT sampled_at, metric_value, unit FROM monitoring_metrics "
                                            "WHERE tenant_id=:t AND metric_name=:m ORDER BY sampled_at DESC LIMIT 60"),
                                    dict(params, m=metric)).all()
                if not rows:
                    continue
                rows = list(reversed(rows))[::max(1, len(rows) // 30)]
                performance.append({"name": metric, "label": METRIC_LABELS.get(metric, metric), "unit": rows[0][2] or "",
                                    "series": [{"t": row[0].strftime("%m-%d %H:%M"), "v": round(float(row[1]), 3)} for row in rows]})
            # ---- 最近操作动态 ----
            activity = [{"action": row[0], "actor": row[1], "resource": row[2], "decision": row[3],
                         "at": row[4].strftime("%m-%d %H:%M") if row[4] else ""}
                        for row in conn.execute(sa.text("SELECT action, actor, resource, decision, created_at "
                                                        "FROM permission_audit WHERE tenant_id=:t ORDER BY id DESC LIMIT 8"),
                                                params).all()]
    finally:
        engine.dispose()
    audit_access(get_settings().mysql_dsn, actor=ctx.user_id, tenant_id=ctx.tenant_id, auth_source=ctx.source,
                 action="dashboard_read", resource="/dashboard/summary", decision="allow", roles=ctx.roles,
                 scope=ctx.scope.to_dict())
    windows = []
    if alarm_end:
        windows.append("告警截至 " + alarm_end.strftime("%Y-%m-%d") + "（近 " + str(days) + " 天）")
    if qa_end:
        windows.append("问答截至 " + qa_end.strftime("%Y-%m-%d") + "（近 " + str(days) + " 天）")
    if performance:
        windows.append("监控样本截至 " + performance[0]["series"][-1]["t"])
    online = next((row["count"] for row in device_status if row["raw"] == "online"), 0)
    return {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "scope": scope_note(ctx, sorted(set(applied) | set(al_applied) | set(wk_applied) | set(ch_applied)),
                            sorted(set(unapplied) | set(al_unapplied) | set(wk_unapplied) | set(ch_unapplied)),
                            "看板统计"),
        "scope_applied": sorted(set(applied) | set(al_applied) | set(wk_applied) | set(ch_applied)),
        "scope_unapplied": sorted(set(unapplied) | set(al_unapplied) | set(wk_unapplied) | set(ch_unapplied)),
        "demo_notice": "演示数据窗口：" + "；".join(windows) if windows else "暂无数据",
        "overview": {
            "devices": {"total": device_total, "online": online,
                        "online_rate": round(online / device_total * 100, 1) if device_total else 0.0,
                        "status": device_status},
            "alarms": {"total": alarm_total, "open": alarm_open, "critical_open": alarm_critical_open},
            "work_orders": {"total": work_total,
                            "p1": next((row["count"] for row in work_priority if row["raw"] == "P1"), 0)},
            "changes": {"total": change_total, "status": change_status,
                        "completed": next((row["count"] for row in change_status if row["raw"] == "completed"), 0)},
            "knowledge": {"active_version": active_version, "assets": asset_count, "locations": location_count,
                          "versions": {"active": versions.get("ACTIVE", 0), "staged": versions.get("STAGED", 0),
                                       "archived": versions.get("ARCHIVED", 0)}},
            "qa": {"questions": question_total, "sessions": session_total, "feedback_up": up, "feedback_down": down,
                   "satisfaction": round(up / (up + down) * 100, 1) if up + down else None},
        },
        "alarm_trend": alarm_trend,
        "alarm_severity": alarm_severity, "alarm_status": alarm_status, "alarm_codes": alarm_codes,
        "device_status": device_status, "device_types": device_types,
        "work_priority": work_priority, "work_depts": work_depts, "change_types": change_types,
        "qa_trend": qa_trend, "qa_routes": qa_routes,
        "performance": performance, "pending_alarms": pending_alarms, "activity": activity,
        "ingest_jobs": ingest_jobs, "eval_runs": eval_runs,
    }
