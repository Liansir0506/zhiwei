"""模拟企业系统集成管理接口。"""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy.exc import SQLAlchemyError

from qa_core.api.auth import AuthContext, get_auth_context, require_roles
from qa_core.config.settings import get_settings
from qa_core.integrations.adapters import SYNC_ORDER, build_adapter_registry
from qa_core.integrations.state import MySQLSyncStateStore
from qa_core.integrations.sync_service import EnterpriseSyncService
from qa_core.storage.domain_dao import DomainDao

router = APIRouter(prefix="/admin/integrations", tags=["integrations"])


class SyncRequest(BaseModel):
    sources: list[str] = Field(default_factory=lambda: list(SYNC_ORDER))
    reset_cursor: bool = False
    page_size: int = Field(default=100, ge=1, le=200)


def _components():
    settings = get_settings()
    store = MySQLSyncStateStore(settings.mysql_dsn)
    service = EnterpriseSyncService(build_adapter_registry(settings),
                                    DomainDao(settings.mysql_dsn), store)
    return store, service


@router.get("/status")
def status(ctx: AuthContext = Depends(get_auth_context)) -> dict:
    require_roles(ctx, ("ops", "auditor", "admin"))
    try:
        rows = _components()[0].list(ctx.tenant_id)
    except SQLAlchemyError as exc:
        raise HTTPException(status_code=503, detail="Adapter 状态表尚未迁移或数据库不可用") from exc
    return {"tenant_id": ctx.tenant_id, "simulation_endpoint": True, "adapters": rows}


@router.post("/sync")
def sync(request: SyncRequest, ctx: AuthContext = Depends(get_auth_context)) -> dict:
    require_roles(ctx, ("admin",))
    try:
        results = _components()[1].sync_all(
            ctx.tenant_id, sources=request.sources, page_size=request.page_size,
            reset_cursor=request.reset_cursor)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    except SQLAlchemyError as exc:
        raise HTTPException(status_code=503, detail="领域库尚未迁移或数据库不可用") from exc
    return {"tenant_id": ctx.tenant_id, "simulation_endpoint": True, "results": results}
