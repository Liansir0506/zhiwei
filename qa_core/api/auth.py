"""API Key 认证：key 明文仅创建时可见，库里存 sha256；roles 随 key 下发。

AUTH_REQUIRED=false（默认，本机开发）时回退 X-User-Roles 头模式；
=true（生产）时无有效 key 一律 401。
"""
from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field

import sqlalchemy as sa
from fastapi import Header, HTTPException
from sqlalchemy.exc import OperationalError

from qa_core.config.settings import get_settings
from qa_core.security.policy import AccessScope

ANON_ROLES = ["anon"]


@dataclass
class AuthContext:
    user_id: str
    roles: list[str]
    source: str  # api_key | header
    tenant_id: str = "default"
    dept_ids: list[str] = field(default_factory=list)
    device_types: list[str] = field(default_factory=list)
    security_levels: list[str] = field(default_factory=list)

    @property
    def scope(self) -> AccessScope:
        return AccessScope.build(tenant_id=self.tenant_id, roles=self.roles,
                                 dept_ids=self.dept_ids, device_types=self.device_types,
                                 requested_security_levels=self.security_levels)


def hash_key(raw_key: str) -> str:
    return hashlib.sha256(raw_key.encode()).hexdigest()


def create_api_key(raw_key: str, user_id: str, roles: list[str],
                   tenant_id: str = "default", dept_id: str | None = None,
                   device_types: list[str] | None = None,
                   security_levels: list[str] | None = None) -> None:
    """由 scripts/create_api_key.py 调用。"""
    engine = sa.create_engine(get_settings().mysql_dsn)
    params = {"h": hash_key(raw_key), "u": user_id, "r": json.dumps(roles),
              "tenant": tenant_id, "dept": dept_id,
              "devices": json.dumps(device_types or []),
              "levels": json.dumps(security_levels or [])}
    try:
        with engine.begin() as conn:
            conn.execute(sa.text(
                "INSERT INTO api_keys (key_hash, user_id, tenant_id, dept_id, device_scope, "
                "security_levels, roles) VALUES (:h, :u, :tenant, :dept, :devices, :levels, :r)"), params)
    except OperationalError:
        try:
            # 滚动升级兼容：第2步字段已存在、但第4步迁移尚未执行。
            with engine.begin() as conn:
                conn.execute(sa.text(
                    "INSERT INTO api_keys (key_hash, user_id, tenant_id, dept_id, roles) "
                    "VALUES (:h, :u, :tenant, :dept, :r)"), params)
        except OperationalError:
            with engine.begin() as conn:
                conn.execute(sa.text(
                    "INSERT INTO api_keys (key_hash, user_id, roles) VALUES (:h, :u, :r)"), params)


def verify_api_key(raw_key: str, *, dsn: str | None = None) -> AuthContext | None:
    engine = sa.create_engine(dsn or get_settings().mysql_dsn)
    try:
        return _read_api_key(engine, raw_key)
    finally:
        engine.dispose()


def _read_api_key(engine, raw_key):
    try:
        with engine.begin() as conn:
            row = conn.execute(sa.text(
                "SELECT user_id, roles, enabled, tenant_id, dept_id, device_scope, security_levels "
                "FROM api_keys WHERE key_hash=:h"), {"h": hash_key(raw_key)}).first()
    except OperationalError:
        try:
            with engine.begin() as conn:
                legacy = conn.execute(sa.text(
                    "SELECT user_id, roles, enabled, tenant_id, dept_id FROM api_keys WHERE key_hash=:h"),
                    {"h": hash_key(raw_key)}).first()
            row = (*legacy, None, None) if legacy else None
        except OperationalError:
            with engine.begin() as conn:
                legacy = conn.execute(sa.text(
                    "SELECT user_id, roles, enabled FROM api_keys WHERE key_hash=:h"),
                    {"h": hash_key(raw_key)}).first()
            row = (*legacy, "default", None, None, None) if legacy else None
    if row is None or not row[2]:
        return None
    roles = json.loads(row[1]) if isinstance(row[1], str) else list(row[1] or [])
    device_types = json.loads(row[5]) if isinstance(row[5], str) else list(row[5] or [])
    security_levels = json.loads(row[6]) if isinstance(row[6], str) else list(row[6] or [])
    return AuthContext(user_id=row[0], roles=roles, source="api_key",
                       tenant_id=row[3] or "default",
                       dept_ids=[row[4]] if row[4] else [], device_types=device_types,
                       security_levels=security_levels)


def authenticate_headers(*, x_api_key: str = "", x_user_id: str = "anon",
                         x_user_roles: str = "", x_tenant_id: str = "default",
                         x_dept_ids: str = "", x_device_types: str = "",
                         x_security_levels: str = "") -> AuthContext:
    """HTTP/WS 共用认证入口；开发 Header 只能在 AUTH_REQUIRED=false 时使用。"""
    settings = get_settings()
    if x_api_key:
        ctx = verify_api_key(x_api_key)
        if ctx is None:
            from qa_core.security.audit import audit_access

            audit_access(settings.mysql_dsn, actor=x_user_id or "anonymous",
                         tenant_id=x_tenant_id or "default", auth_source="api_key",
                         action="authenticate", resource="/", decision="deny",
                         reason="invalid_api_key")
            raise HTTPException(status_code=401, detail="无效的 API Key")
        return ctx
    if settings.auth_required:
        from qa_core.security.audit import audit_access

        audit_access(settings.mysql_dsn, actor=x_user_id or "anonymous",
                     tenant_id=x_tenant_id or "default", auth_source="none",
                     action="authenticate", resource="/", decision="deny",
                     reason="missing_api_key")
        raise HTTPException(status_code=401, detail="缺少 X-API-Key")
    roles = [r.strip() for r in x_user_roles.split(",") if r.strip()]
    dept_ids = [d.strip() for d in x_dept_ids.split(",") if d.strip()]
    device_types = [d.strip() for d in x_device_types.split(",") if d.strip()]
    security_levels = [d.strip() for d in x_security_levels.split(",") if d.strip()]
    return AuthContext(user_id=x_user_id or "anon", roles=roles, source="header",
                       tenant_id=x_tenant_id.strip() or "default", dept_ids=dept_ids,
                       device_types=device_types, security_levels=security_levels)


def get_auth_context(x_api_key: str = Header(default=""),
                     x_user_id: str = Header(default="anon"),
                     x_user_roles: str = Header(default=""),
                     x_tenant_id: str = Header(default="default"),
                     x_dept_ids: str = Header(default=""),
                     x_device_types: str = Header(default=""),
                     x_security_levels: str = Header(default="")) -> AuthContext:
    """FastAPI dependency：API Key 优先，AUTH_REQUIRED=false 时回退 Header 模式。"""
    return authenticate_headers(
        x_api_key=x_api_key, x_user_id=x_user_id, x_user_roles=x_user_roles,
        x_tenant_id=x_tenant_id, x_dept_ids=x_dept_ids, x_device_types=x_device_types,
        x_security_levels=x_security_levels)


def get_websocket_auth_context(headers, query_params=None) -> AuthContext:
    """WebSocket 在 accept 前复用与 HTTP 相同的 API Key/开发 Header 规则。"""
    query_params = query_params or {}
    return authenticate_headers(
        x_api_key=headers.get("x-api-key", "") or query_params.get("key", ""),
        x_user_id=headers.get("x-user-id", "") or query_params.get("user_id", "anon"),
        x_user_roles=headers.get("x-user-roles", "") or query_params.get("roles", ""),
        x_tenant_id=headers.get("x-tenant-id", "") or query_params.get("tenant_id", "default"),
        x_dept_ids=headers.get("x-dept-ids", "") or query_params.get("dept_ids", ""),
        x_device_types=headers.get("x-device-types", "") or query_params.get("device_types", ""),
        x_security_levels=headers.get("x-security-levels", "") or query_params.get("security_levels", ""))


def require_roles(ctx: AuthContext, allowed: tuple[str, ...]) -> None:
    """角色校验（示例：管理接口要求 admin）。"""
    if not any(r in ctx.roles for r in allowed):
        from qa_core.security.audit import audit_access

        audit_access(get_settings().mysql_dsn, actor=ctx.user_id, tenant_id=ctx.tenant_id,
                     auth_source=ctx.source, action="role_gate", resource="/",
                     decision="deny", reason=f"requires:{'/'.join(allowed)}", roles=ctx.roles,
                     scope=ctx.scope.to_dict())
        raise HTTPException(status_code=403, detail=f"需要角色 {'/'.join(allowed)}")


def require_global_admin(ctx: AuthContext) -> None:
    """Global knowledge mutations are not tenant-admin self-service operations."""
    require_roles(ctx, ("admin",))
    if ctx.tenant_id != "default":
        raise HTTPException(403, "租户管理员不能修改全局知识库")
