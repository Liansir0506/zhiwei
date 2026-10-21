"""统一的授权作用域构造：列表、统计、详情、查询、导出共用。

语义（与 qa_core/security/policy.AccessScope 一致）：
- tenant_id：调用方始终追加，不在此重复；
- dept_ids / device_types：凭据声明才收窄，空表示不额外收窄；
- security_levels：业务表未建模密级列，记入 unapplied，由响应显式说明，
  不静默忽略、不假装生效；
- 所有值参数化，IN 子句按元素生成占位符，杜绝拼接。
"""
from __future__ import annotations

from qa_core.api.auth import AuthContext


def _in_clause(prefix: str, values, column: str):
    placeholders = ", ".join(f":{prefix}{index}" for index in range(len(values)))
    params = {f"{prefix}{index}": value for index, value in enumerate(values)}
    return f"{column} IN ({placeholders})", params


def device_scope(ctx: AuthContext, alias: str = "d", prefix: str = "scope"):
    """dept_ids/device_types → (SQL 片段列表, 参数, 已生效维度, 未生效维度)。

    alias 指向 JOIN 到 devices 表的别名（本体查询传空串）；调用方需保证该 JOIN 存在。
    prefix 区分同一次请求里的多处作用域，避免同名参数互相覆盖。
    """
    clauses: list[str] = []
    params: dict = {}
    applied: list[str] = []
    unapplied: list[str] = []
    def column(name: str) -> str:
        return f"{alias}.{name}" if alias else name

    if ctx.device_types:
        clause, values = _in_clause(prefix + "_dt_", tuple(ctx.device_types), column("device_type"))
        clauses.append(clause); params.update(values); applied.append("device_types")
    if ctx.dept_ids:
        clause, values = _in_clause(prefix + "_dept_", tuple(ctx.dept_ids), column("dept_id"))
        clauses.append(clause); params.update(values); applied.append("dept_ids")
    requested = {str(level).strip() for level in (ctx.security_levels or []) if str(level).strip()}
    if requested and requested != {"public"}:
        unapplied.append("security_levels")
    return clauses, params, applied, unapplied


def scope_note(ctx: AuthContext, applied: list[str], unapplied: list[str], subject: str) -> str:
    """响应里的人话说明：哪些维度真的收窄了，哪些没有建模。"""
    parts = [f"{subject}已按租户与角色核对"]
    parts.append("部门/设备类型：" + ("、".join(applied) if applied else "未声明，未收窄"))
    if unapplied:
        parts.append("密级：业务表未建模密级列，本次未生效")
    return "；".join(parts) + "。"
