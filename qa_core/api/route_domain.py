"""企业统一领域上下文只读接口。"""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.exc import SQLAlchemyError

from qa_core.api.auth import AuthContext, get_auth_context, require_roles
from qa_core.config.settings import get_settings
from qa_core.storage.domain_dao import DomainDao
from qa_core.security.audit import audit_access
from qa_core.application.domain_context_service import DomainContextService, filter_context


def _apply_abac(result: dict, ctx: AuthContext, resource: str) -> dict:
    """领域上下文同样遵守部门、设备域和密级限制，不能绕开检索层。"""
    return filter_context(result, ctx)

router = APIRouter(prefix="/domain", tags=["domain"])


def _dao() -> DomainDao:
    return DomainDao(get_settings().mysql_dsn)


def _query(kind: str, entity_id: str, ctx: AuthContext) -> dict:
    require_roles(ctx, ("ops", "auditor", "admin"))
    decision, reason, dao = 'deny', '', None
    try:
        dao = _dao()
        try:
            result = DomainContextService(dao).read(kind, entity_id, ctx)
        finally:
            dao.engine.dispose()
        if result is None:
            raise HTTPException(404, '实体不存在或不可访问')
        decision = 'allow'
        return result
    except SQLAlchemyError:
        reason = 'domain_unavailable'
        raise HTTPException(503, '领域模型尚未迁移或数据库不可用') from None
    except HTTPException:
        reason = 'entity_unavailable'
        raise
    finally:
        audit_access(get_settings().mysql_dsn, actor=ctx.user_id, tenant_id=ctx.tenant_id,
                     auth_source=ctx.source, action="domain_context", resource=f"/domain/{kind}",
                     decision=decision, reason=reason, roles=ctx.roles,
                     scope=ctx.scope.to_dict())


@router.get("/alarms/{alarm_id}/context")
def alarm_context(alarm_id: str, ctx: AuthContext = Depends(get_auth_context)) -> dict:
    return _query("alarm", alarm_id, ctx)


@router.get("/devices/{device_id}/context")
def device_context(device_id: str, ctx: AuthContext = Depends(get_auth_context)) -> dict:
    return _query("device", device_id, ctx)


@router.get("/suggest")
def suggest(kind: str, q: str = Query('', max_length=64), ctx: AuthContext = Depends(get_auth_context),
            limit: int = Query(8, ge=1, le=10)) -> dict:
    """告警/设备输入联想：与上下文查询同一角色与租户边界，只读列表字段。"""
    require_roles(ctx, ("ops", "auditor", "admin"))
    if kind not in ("alarm", "device"):
        raise HTTPException(422, '不支持的联想类型')
    decision, reason, dao = 'deny', '', None
    try:
        dao = _dao()
        try:
            items = dao.suggest(kind, q, ctx.tenant_id, limit)
        finally:
            dao.engine.dispose()
        decision = 'allow'
        return {"kind": kind, "items": items}
    except SQLAlchemyError:
        reason = 'domain_unavailable'
        raise HTTPException(503, '领域模型尚未迁移或数据库不可用') from None
    except HTTPException:
        reason = 'unsupported_kind'
        raise
    finally:
        audit_access(get_settings().mysql_dsn, actor=ctx.user_id, tenant_id=ctx.tenant_id,
                     auth_source=ctx.source, action="domain_suggest", resource="/domain/suggest",
                     decision=decision, reason=reason, roles=ctx.roles,
                     scope=ctx.scope.to_dict())
