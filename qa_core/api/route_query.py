"""查询工作台：自然语言 → QuerySpec → 受控只读执行。

安全边界（2026-10-19 收口）：
- 表白名单（5 张业务表）；
- 字段白名单 = 逐表预设 ∩ information_schema 真实列，再按角色收窄
  （ops/auditor 不读内部大字段；admin 可读）；排序/过滤同约束；
- 强制租户 + 部门/设备类型作用域（dept_ids/device_types，与 /alerts 一致）；
- 仅 SELECT；值参数化；limit ≤ 200；
- 定位为“业务明细查询”：聚合/分组/占比类意图明确拒绝并说明，不静默退化；
- 过滤条件 > 4、输出字段 > 12 明确拒绝，不切片静默丢弃；
- 真实截断判断（同条件 COUNT），语句设置 max_execution_time；
- 解析优先 LLM（generation 关闭或失败时规则兜底），两者走同一校验；
  解析来源（llm / rule / 不支持及原因）在响应中明示。
"""
import re
import time
from datetime import datetime, timezone

import sqlalchemy as sa
from fastapi import APIRouter, Depends, HTTPException, Query, Response
from pydantic import BaseModel, Field

from qa_core.api.auth import AuthContext, get_auth_context, require_roles
from qa_core.api.rate_limit import check_rate_limit
from qa_core.api.scope_sql import device_scope, scope_note
from qa_core.config.settings import get_settings
from qa_core.security.audit import audit_access

router = APIRouter(tags=["query-workbench"])

# 逐表预设可读字段（授权白名单的“预设”部分；真实列在运行时取交集）。
TABLE_FIELDS = {
    "alarms": ["alarm_id", "device_id", "alarm_code", "severity", "status", "occurred_at", "cleared_at", "source_system"],
    "devices": ["device_id", "name", "device_type", "vendor", "model", "software_version", "status", "created_at"],
    "work_orders": ["ticket_id", "device_id", "alarm_id", "title", "priority", "status", "assignee_user_id", "opened_at", "closed_at"],
    "change_records": ["change_id", "device_id", "ticket_id", "title", "change_type", "status", "window_start", "window_end", "operator_user_id"],
    "monitoring_metrics": ["observation_id", "device_id", "metric_name", "metric_value", "unit", "sampled_at"],
}
TABLES = list(TABLE_FIELDS)
# 内部/大字段：仅 admin 可读；ops/auditor 的字段白名单剔除。
INTERNAL_FIELDS = {"payload_json", "attributes_json", "cmdb_id", "network_tech", "dept_id", "updated_at"}
# 设备维度 JOIN：除 devices 本体外都需要 JOIN 才能套用部门/设备类型作用域。
JOIN_DEVICES = {
    "alarms": ("a", "alarms a LEFT JOIN devices d ON d.device_id=a.device_id AND d.tenant_id=a.tenant_id"),
    "devices": ("", "devices"),
    "work_orders": ("w", "work_orders w LEFT JOIN devices d ON d.device_id=w.device_id AND d.tenant_id=w.tenant_id"),
    "change_records": ("c", "change_records c LEFT JOIN devices d ON d.device_id=c.device_id AND d.tenant_id=c.tenant_id"),
    "monitoring_metrics": ("m", "monitoring_metrics m LEFT JOIN devices d ON d.device_id=m.device_id AND d.tenant_id=m.tenant_id"),
}
# 时间窗白名单：仅这些固定表达式可编译，绝不拼接用户输入。
# 每个窗口是 SQL 谓词模板列表（{f} 替换为时间列）；含下界+上界的窗口可表达"昨天/上周"这类区间。
TIME_WINDOWS = {
    "today": ["{f} >= CURDATE()"],
    "yesterday": ["{f} >= CURDATE() - INTERVAL 1 DAY", "{f} < CURDATE()"],
    "this_week": ["{f} >= CURDATE() - INTERVAL WEEKDAY(CURDATE()) DAY"],
    "last_week": ["{f} >= CURDATE() - INTERVAL WEEKDAY(CURDATE()) DAY - INTERVAL 7 DAY",
                  "{f} < CURDATE() - INTERVAL WEEKDAY(CURDATE()) DAY"],
    "last_7d": ["{f} >= CURDATE() - INTERVAL 6 DAY"],
    "last_30d": ["{f} >= CURDATE() - INTERVAL 29 DAY"],
}
TIME_FIELD = {"alarms": "occurred_at", "devices": "created_at", "work_orders": "opened_at",
              "change_records": "window_start", "monitoring_metrics": "sampled_at"}

_COLUMN_CACHE: dict[str, tuple[float, str, set[str]]] = {}
_CACHE_TTL = 60.0
MAX_LIMIT = 200
MAX_SELECT = 12
MAX_FILTERS = 4
OPS = {"=", "!=", ">", ">=", "<", "<=", "like"}

SEVERITY_WORDS = {"紧急": "critical", "严重": "critical", "critical": "critical",
                  "重要": "major", "major": "major", "次要": "minor", "minor": "minor", "警告": "warning", "warning": "warning"}
# 状态词按实体分表：告警、工单、变更、设备的状态域不同，不能共用一套字典。
STATUS_WORDS_BY_TABLE = {
    "alarms": {"未处理": "open", "待处理": "open", "open": "open", "已确认": "acknowledged",
               "确认": "acknowledged", "acknowledged": "acknowledged", "已清除": "cleared",
               "已关闭": "closed", "closed": "closed"},
    "work_orders": {"待处理": "pending", "pending": "pending", "处理中": "in_progress",
                    "进行中": "in_progress", "in_progress": "in_progress", "已关闭": "closed",
                    "已完成": "closed", "closed": "closed"},
    "change_records": {"计划中": "planned", "planned": "planned", "已完成": "completed",
                       "完成": "completed", "completed": "completed", "已取消": "cancelled",
                       "cancelled": "cancelled"},
    "devices": {"在网": "online", "在线": "online", "online": "online",
                "离线": "offline", "offline": "offline", "维护": "maintenance",
                "维护": "maintenance", "maintenance": "maintenance", "退网": "retired", "retired": "retired"},
}
TABLE_WORDS = [("告警", "alarms"), ("设备", "devices"), ("终端", "devices"), ("工单", "work_orders"),
               ("变更", "change_records"), ("指标", "monitoring_metrics"), ("性能", "monitoring_metrics"), ("采样", "monitoring_metrics")]
AGG_WORDS = ["多少", "统计", "汇总", "平均", "总计", "占比", "排名", "趋势", "分布", "计数", "几个", "合计", "总数"]
UNSUPPORTED_HINT = ("当前查询工作台定位为业务明细查询（可按条件筛选、排序、限量导出）；"
                    "汇总统计请使用本页上方“常用统计”卡的五类固定统计（告警数量、级别分布、设备在线率、"
                    "工单积压、KPI 趋势），或在问题里说明要导出的明细条件。")


class Filter(BaseModel):
    field: str = Field(max_length=64)
    op: str = Field(max_length=4)
    value: str | int | float


class OrderBy(BaseModel):
    field: str = Field(max_length=64)
    dir: str = Field(default="desc", max_length=4)


class QuerySpec(BaseModel):
    table: str = Field(max_length=32)
    select: list[str] = Field(default_factory=list)
    filters: list[Filter] = Field(default_factory=list)
    order_by: OrderBy | None = None
    time_window: str | None = Field(default=None, max_length=16)
    limit: int = Field(default=50, ge=1, le=MAX_LIMIT)
    explain: str = Field(default="", max_length=160)


class ParseRequest(BaseModel):
    question: str = Field(min_length=1, max_length=300)


class ExecuteRequest(BaseModel):
    spec: QuerySpec


def _reader(ctx: AuthContext) -> None:
    require_roles(ctx, ("ops", "auditor", "admin"))
    check_rate_limit(ctx.user_id, get_settings().rate_limit_qps)


def _columns(table: str) -> set[str]:
    """真实列（60s 缓存，键含目标库 DSN，避免配置切换复用旧列集）。"""
    dsn = get_settings().mysql_dsn
    now = time.monotonic()
    hit = _COLUMN_CACHE.get(table)
    if hit and now - hit[0] < _CACHE_TTL and hit[1] == dsn:
        return hit[2]
    engine = sa.create_engine(dsn, pool_pre_ping=True)
    with engine.connect() as conn:
        rows = conn.execute(sa.text(
            "SELECT column_name FROM information_schema.columns "
            "WHERE table_schema = DATABASE() AND table_name = :t"), {"t": table}).scalars().all()
    cols = {row for row in rows if row != "tenant_id"}
    _COLUMN_CACHE[table] = (now, dsn, cols)
    return cols


def allowed_fields(ctx: AuthContext, table: str) -> set[str]:
    """字段授权白名单：逐表预设（admin 另含内部字段）∩ 真实列；ops/auditor 剔除内部字段。"""
    preset = set(TABLE_FIELDS.get(table, []))
    if "admin" in (ctx.roles or []):
        preset |= INTERNAL_FIELDS
    real = _columns(table) or preset
    fields = preset & real
    if "admin" not in (ctx.roles or []):
        fields -= INTERNAL_FIELDS
    return fields


def _now():
    return datetime.now(timezone.utc)


def _validate(spec: QuerySpec, ctx: AuthContext):
    """校验并归一化；任何越界都明确拒绝，不切片、不静默丢弃。"""
    if spec.table not in TABLES:
        raise HTTPException(422, f"表不在只读白名单：{spec.table}")
    if len(spec.filters) > MAX_FILTERS:
        raise HTTPException(422, f"过滤条件最多 {MAX_FILTERS} 个（当前 {len(spec.filters)} 个）；请合并或分次查询")
    if len(spec.select) > MAX_SELECT:
        raise HTTPException(422, f"输出字段最多 {MAX_SELECT} 个（当前 {len(spec.select)} 个）")
    fields = allowed_fields(ctx, spec.table)
    select = (spec.select or sorted(fields))[:MAX_SELECT]
    for field in select:
        if field not in fields:
            raise HTTPException(422, f"字段不在授权白名单：{spec.table}.{field}")
    filters: list[tuple[str, str, object]] = []
    for item in spec.filters:
        if item.field not in fields:
            raise HTTPException(422, f"过滤字段不在授权白名单：{spec.table}.{item.field}")
        if item.op not in OPS:
            raise HTTPException(422, f"不支持的比较符：{item.op}")
        if item.op == "like" and not isinstance(item.value, str):
            raise HTTPException(422, "like 仅支持字符串值")
        filters.append((item.field, item.op, item.value))
    order = None
    if spec.order_by and spec.order_by.field:
        if spec.order_by.field not in fields:
            raise HTTPException(422, f"排序字段不在授权白名单：{spec.table}.{spec.order_by.field}")
        order = (spec.order_by.field, "asc" if spec.order_by.dir == "asc" else "desc")
    window = None
    if spec.time_window:
        if spec.time_window not in TIME_WINDOWS:
            raise HTTPException(422, f"不支持的时间窗：{spec.time_window}")
        window = spec.time_window
    return select, filters, order, window


def _compile(spec: QuerySpec, select, filters, order, window, ctx: AuthContext):
    alias, source = JOIN_DEVICES[spec.table]
    prefix = f"{alias}." if alias else ""
    parts = [f"SELECT {', '.join(prefix + name for name in select)} FROM {source}"]
    params: dict[str, object] = {"tenant": ctx.tenant_id}
    clauses = [f"{prefix}tenant_id = :tenant"]
    # 授权作用域：部门/设备类型（与 /alerts、看板统计同一构造）
    scope_clauses, scope_params, applied, unapplied = device_scope(ctx, 'd', prefix='q')
    clauses.extend(scope_clauses)
    params.update(scope_params)
    for index, (field, op, value) in enumerate(filters):
        key = f"v{index}"
        clauses.append(f"{prefix}{field} {op} :{key}")
        params[key] = value
    if window:
        field = TIME_FIELD[spec.table]
        for predicate in TIME_WINDOWS[window]:
            clauses.append(predicate.format(f=f"{prefix}{field}"))
    where_clause = "WHERE " + " AND ".join(clauses)
    parts.append(where_clause)
    if order:
        parts.append(f"ORDER BY {prefix}{order[0]} {order[1]}")
    parts.append(f"LIMIT {min(spec.limit, MAX_LIMIT)}")
    count_statement = sa.text(f"SELECT COUNT(*) FROM {source} {where_clause}")
    return sa.text(" ".join(parts)), count_statement, params, applied, unapplied


def _reject_aggregation(question: str) -> str | None:
    """聚合/占比类意图：规则与 LLM 之前的前置闸门，避免模型绕过。"""
    text = question.strip()
    for word in AGG_WORDS:
        if word in text:
            return UNSUPPORTED_HINT
    return None


def _rule_parse(question: str) -> tuple[QuerySpec | None, str]:
    """确定性规则：仅识别明细意图；聚合/无表意图明确拒绝。返回 (spec 或 None, 原因)。"""
    text = question.strip()
    reject = _reject_aggregation(text)
    if reject:
        return None, reject
    table = None
    for word, candidate in TABLE_WORDS:
        if word in text:
            table = candidate
            break
    if table is None:
        return None, "没有识别到要查询的业务对象（告警/设备/工单/变更/监控指标）；" + UNSUPPORTED_HINT
    filters: list[Filter] = []
    for word, value in SEVERITY_WORDS.items():
        if word in text and table == "alarms":
            filters.append(Filter(field="severity", op="=", value=value))
            break
    for word, value in STATUS_WORDS_BY_TABLE.get(table, {}).items():
        if word in text:
            filters.append(Filter(field="status", op="=", value=value))
            break
    window = None
    for word, candidate in [("昨天", "yesterday"), ("昨日", "yesterday"),
                            ("上周", "last_week"), ("上个星期", "last_week"), ("上一周", "last_week"),
                            ("今天", "today"), ("今日", "today"), ("本周", "this_week"), ("这周", "this_week"),
                            ("近30天", "last_30d"), ("最近30天", "last_30d"), ("本月", "last_30d"),
                            ("近7天", "last_7d"), ("最近7天", "last_7d")]:
        if word in text:
            window = candidate
            break
    match = re.search(r"(\d{1,3})\s*(?:条|行|个|台)", text)
    limit = int(match.group(1)) if match else 50
    return QuerySpec(table=table, filters=filters, order_by=OrderBy(field=TIME_FIELD[table], dir="desc"),
                     time_window=window, limit=max(1, min(limit, MAX_LIMIT)),
                     explain="规则解析（未调用模型）"), ""


def _llm_parse(question: str) -> QuerySpec | None:
    settings = get_settings()
    if not settings.generation_enabled or not settings.llm_api_key:
        return None
    try:
        import json as _json

        from qa_core.generation.llm import LLMClient
        client = LLMClient(settings.llm_base_url, settings.llm_api_key, settings.llm_model,
                           temperature=0.0, max_tokens=600)
        catalog = {name: TABLE_FIELDS[name] for name in TABLES}
        messages = [
            {"role": "system", "content":
                "你是只读明细查询规划器。根据用户问题输出 JSON QuerySpec，不要输出任何解释。"
                "只做明细查询：筛选、排序、限量；不要设计聚合、分组或统计。"
                "可用表与可读字段（tenant_id 已被强制过滤，禁止出现在任何位置）：\n"
                + "\n".join(f"- {name}: {', '.join(cols)}" for name, cols in catalog.items())
                + "\n时间窗仅可用 today / yesterday / this_week / last_week / last_7d / last_30d。"
                  "输出格式：{\"table\": \"...\", \"select\": [\"...\"], \"filters\": [{\"field\": \"...\", \"op\": \"=|!=|>|>=|<|<=|like\", \"value\": \"...\"}], "
                  "\"order_by\": {\"field\": \"...\", \"dir\": \"desc\"}, \"time_window\": null, \"limit\": 50, \"explain\": \"一句话说明\"}。"
                  "severity 取值 critical/major/minor/warning；status 按字段语义。无法对应任何表时 table 用空串。"},
            {"role": "user", "content": question},
        ]
        raw = client.chat(messages)
        match = re.search(r"\{.*\}", raw, re.S)
        if not match:
            return None
        data = _json.loads(match.group(0))
        if not isinstance(data, dict) or not data.get("table"):
            return None
        return QuerySpec(**{key: value for key, value in data.items() if key in QuerySpec.model_fields})
    except Exception:
        return None


@router.post("/query/parse")
def parse(response: Response, body: ParseRequest, ctx: AuthContext = Depends(get_auth_context)):
    response.headers["Cache-Control"] = "no-store"
    _reader(ctx)
    audit_access(get_settings().mysql_dsn, actor=ctx.user_id, tenant_id=ctx.tenant_id,
                 auth_source=ctx.source, action="query_parse", resource="query_workbench",
                 decision="allow", roles=list(ctx.roles), query=body.question[:120])
    gate = _reject_aggregation(body.question)
    if gate:
        return {"supported": False, "reason": gate, "spec": None, "source": "gate", "resolved": None}
    spec, reason = _rule_parse(body.question)
    source = "rule"
    if spec is None:
        spec = _llm_parse(body.question)
        source = "llm"
    if spec is None:
        # 规则与模型都无法构成明细查询：明确拒绝，不退化默认表。
        return {"supported": False, "reason": reason or UNSUPPORTED_HINT, "spec": None,
                "source": source, "resolved": None}
    try:
        select, filters, order, window = _validate(spec, ctx)
    except HTTPException as error:
        if source == "llm":
            spec, reason = _rule_parse(body.question)
            source = "rule-fallback"
            if spec is None:
                return {"supported": False, "reason": reason or UNSUPPORTED_HINT, "spec": None,
                        "source": source, "resolved": None}
            select, filters, order, window = _validate(spec, ctx)
        else:
            return {"supported": False, "reason": error.detail, "spec": spec.model_dump(),
                    "source": source, "resolved": None}
    return {"supported": True, "reason": "", "spec": spec.model_dump(), "source": source,
            "resolved": {"select": select, "filters": [{"field": f, "op": o, "value": v} for f, o, v in filters],
                         "order_by": {"field": order[0], "dir": order[1]} if order else None,
                         "time_window": window}}


@router.post("/query/execute")
def execute(response: Response, body: ExecuteRequest, ctx: AuthContext = Depends(get_auth_context)):
    response.headers["Cache-Control"] = "no-store"
    _reader(ctx)
    spec = body.spec
    select, filters, order, window = _validate(spec, ctx)
    statement, count_statement, params, applied, unapplied = _compile(spec, select, filters, order, window, ctx)
    engine = sa.create_engine(get_settings().mysql_dsn, pool_pre_ping=True,
                              connect_args={"init_command": "SET SESSION max_execution_time=8000"})
    started = time.monotonic()
    try:
        with engine.connect() as conn:
            result = conn.execute(statement, params)
            rows = [dict(row._mapping) for row in result]
            total = conn.execute(count_statement, params).scalar_one()
    except sa.exc.SQLAlchemyError as error:
        raise HTTPException(422, f"查询未执行（{type(error).__name__}）；请调整条件后重试") from None
    elapsed = round((time.monotonic() - started) * 1000, 1)
    for row in rows:
        for key, value in list(row.items()):
            if hasattr(value, "isoformat"):
                row[key] = value.isoformat()
    audit_access(get_settings().mysql_dsn, actor=ctx.user_id, tenant_id=ctx.tenant_id,
                 auth_source=ctx.source, action="query_execute", resource=f"query_workbench.{spec.table}",
                 decision="allow", roles=list(ctx.roles), query=spec.explain[:120])
    return {"columns": select, "rows": rows, "row_count": len(rows),
            "total": int(total), "truncated": int(total) > len(rows), "elapsed_ms": elapsed,
            "tenant_id": ctx.tenant_id, "executed_at": _now().isoformat(),
            "scope": scope_note(ctx, applied, unapplied, "查询结果"),
            "scope_applied": applied, "scope_unapplied": unapplied}


BACKUP_TABLES = ["alarms", "devices", "work_orders", "change_records", "monitoring_metrics"]
BACKUP_MAX_ROWS = 500


@router.get("/backup")
def backup(response: Response, tables: str = Query("", max_length=200),
           ctx: AuthContext = Depends(get_auth_context)):
    """业务样本导出：按当前租户与授权范围导出关键业务表快照（JSON）。

    只读、限行数、限表白名单；不导出凭据与密钥材料；写审计。
    """
    response.headers["Cache-Control"] = "no-store"
    require_roles(ctx, ("admin", "auditor"))
    check_rate_limit(ctx.user_id, get_settings().rate_limit_qps)
    wanted = [name.strip() for name in tables.split(",") if name.strip()] or BACKUP_TABLES
    for name in wanted:
        if name not in BACKUP_TABLES:
            raise HTTPException(422, f"表不在导出白名单：{name}")
    scope_clauses, scope_params, applied, unapplied = device_scope(ctx, 'd', prefix='bk')
    scope_clause = (" AND " + " AND ".join(scope_clauses)) if scope_clauses else ""
    engine = sa.create_engine(get_settings().mysql_dsn, pool_pre_ping=True)
    payload = {"exported_at": _now().isoformat(), "tenant_id": ctx.tenant_id,
               "scope": scope_note(ctx, applied, unapplied, "样本导出"),
               "tables": {}, "row_counts": {}, "totals": {}}
    with engine.connect() as conn:
        for name in wanted:
            alias, source = JOIN_DEVICES[name]
            prefix = f"{alias}." if alias else ""
            columns = sorted(allowed_fields(ctx, name) | {"tenant_id"})
            select = ", ".join(prefix + column for column in columns)
            total = conn.execute(sa.text(
                f"SELECT COUNT(*) FROM {source} WHERE {prefix}tenant_id = :tenant" + scope_clause),
                {"tenant": ctx.tenant_id, **scope_params}).scalar_one()
            rows = conn.execute(sa.text(
                f"SELECT {select} FROM {source} WHERE {prefix}tenant_id = :tenant" + scope_clause
                + f" ORDER BY 1 LIMIT {BACKUP_MAX_ROWS}"),
                {"tenant": ctx.tenant_id, **scope_params}).mappings().all()
            payload["tables"][name] = [dict(row) for row in rows]
            payload["row_counts"][name] = len(rows)
            payload["totals"][name] = int(total)
    audit_access(get_settings().mysql_dsn, actor=ctx.user_id, tenant_id=ctx.tenant_id,
                 auth_source=ctx.source, action="demo_backup", resource="backup",
                 decision="allow", roles=list(ctx.roles))
    return payload
