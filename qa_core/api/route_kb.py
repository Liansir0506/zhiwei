"""知识库版本管理 API：查询 / 激活 / 回滚（含审计与 L2 缓存精准失效）。"""
from __future__ import annotations

import json
import logging

import sqlalchemy as sa
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field

from qa_core.api.auth import AuthContext, get_auth_context, require_roles, require_global_admin
from qa_core.cache.redis_cache import CacheLayer
from qa_core.config.settings import get_settings
from qa_core.quality.publication import validate_version, verify_publication

router = APIRouter(prefix="/kb", tags=["kb"])


class ActivateRequest(BaseModel):
    version: str = Field(min_length=1, max_length=32, pattern=r"^[A-Za-z0-9][A-Za-z0-9_.-]*$")
    # Backward-compatible input only; the authenticated user is the audit actor.
    actor: str = "api"


class WithdrawRequest(BaseModel):
    source_system: str = Field(min_length=1, max_length=64, pattern=r"^[A-Za-z0-9_.-]+$")
    source_id: str = Field(min_length=1, max_length=128, pattern=r"^[^\x00-\x1f]+$")
    reason: str = Field(min_length=1, max_length=500)


@router.post("/withdrawals")
def withdraw_source(body: WithdrawRequest, ctx: AuthContext = Depends(get_auth_context)) -> dict:
    require_roles(ctx, ("admin",))
    from qa_core.security.withdrawals import identity_hash
    from qa_core.storage.milvus_client import read_with_retry
    settings = get_settings()
    engine = _engine()
    key = identity_hash(ctx.tenant_id, body.source_system, body.source_id)
    try:
        with engine.connect() as conn:
            locked = conn.execute(sa.text("SELECT GET_LOCK('rag_kb_publication', 0)")).scalar()
            conn.commit()
            if locked != 1:
                raise HTTPException(409, "入库或发布进行中，请稍后重试撤回")
            try:
                found = False
                for collection in (settings.doc_collection, settings.faq_collection):
                    fields = {f["name"] for f in read_with_retry(settings.milvus_uri, "describe_collection", collection_name=collection)["fields"]}
                    if not {"tenant_id", "source_system", "source_id"} <= fields:
                        raise HTTPException(409, "集合缺稳定来源身份，需先完成受控治理")
                    rows = read_with_retry(settings.milvus_uri, "query", collection_name=collection, filter=(
                        f"tenant_id == {json.dumps(ctx.tenant_id)} and source_system == {json.dumps(body.source_system)} "
                        f"and source_id == {json.dumps(body.source_id)}"), output_fields=["id"], limit=1, consistency_level="Strong")
                    found = found or bool(rows)
                if not found:
                    raise HTTPException(404, "来源不存在或不属于当前租户")
                with conn.begin():
                    result = conn.execute(sa.text(
                        "INSERT IGNORE INTO knowledge_source_withdrawals(identity_hash,tenant_id,source_system,source_id,reason,actor) "
                        "VALUES(:h,:t,:s,:i,:r,:a)"),
                        {"h": key, "t": ctx.tenant_id, "s": body.source_system, "i": body.source_id, "r": body.reason, "a": ctx.user_id})
                    created = bool(result.rowcount)
                    conn.execute(sa.text("INSERT INTO audit_log(action,actor,payload_json) VALUES('withdraw_source',:a,:p)"),
                                 {"a": ctx.user_id, "p": json.dumps({"tenant_id": ctx.tenant_id, "source_system": body.source_system,
                                  "source_id": body.source_id, "created": created, "reason": body.reason}, ensure_ascii=False)})
            finally:
                try:
                    conn.execute(sa.text("SELECT RELEASE_LOCK('rag_kb_publication')"))
                    conn.commit()
                except Exception:
                    conn.invalidate()
                    logging.getLogger(__name__).exception("withdrawal lock cleanup failed")
    except HTTPException:
        raise
    except Exception:
        logging.getLogger(__name__).exception("source withdrawal unavailable")
        raise HTTPException(503, "撤回服务暂不可用") from None
    finally:
        engine.dispose()
    return {"withdrawn": True, "created": created, "tenant_id": ctx.tenant_id,
            "source_system": body.source_system, "source_id": body.source_id}


def _engine():
    return sa.create_engine(get_settings().mysql_dsn)


def _list_versions() -> list[dict]:
    with _engine().begin() as conn:
        rows = conn.execute(sa.text(
            "SELECT version, status, published_at, quality_report_path FROM kb_versions "
            "ORDER BY version DESC")).mappings().all()
    return [dict(r) for r in rows]


@router.get("/versions")
def versions(ctx: AuthContext = Depends(get_auth_context)) -> dict:
    require_roles(ctx, ("ops", "auditor", "admin"))
    return {"versions": _list_versions(), "can_preview_staged": "admin" in ctx.roles}


def _activate(version: str, actor: str, *, action: str = "activate") -> dict:
    validate_version(version)
    settings = get_settings()
    engine = _engine()
    current = None
    try:
        with engine.connect() as conn:
            acquired = conn.execute(sa.text("SELECT GET_LOCK('rag_kb_publication', 0)")).scalar()
            conn.commit()
            if acquired != 1:
                raise HTTPException(409, "已有版本发布操作进行中")
            try:
                with conn.begin():
                    row = conn.execute(sa.text(
                        "SELECT status, quality_report_path FROM kb_versions WHERE version=:v FOR UPDATE"),
                        {"v": version}).first()
                    if row is None:
                        raise HTTPException(404, f"版本 {version} 不存在")
                    if row[0] == "ACTIVE":
                        raise HTTPException(400, f"版本 {version} 已是 ACTIVE")
                    expected_status = {"activate": "STAGED", "rollback": "ARCHIVED"}.get(action)
                    if expected_status is None or row[0] != expected_status:
                        raise HTTPException(400, "发布仅允许 STAGED，回滚仅允许 ARCHIVED 版本")
                    if not row[1]:
                        raise HTTPException(400, "无质量门禁报告，禁止激活")
                    pending = conn.execute(sa.text(
                        "SELECT COUNT(*) FROM ingest_jobs WHERE kb_version=:v "
                        "AND status NOT IN ('success','failed','cancelled')"), {"v": version}).scalar()
                    if pending:
                        raise HTTPException(409, "候选仍有未完成入库任务，禁止发布")
                    try:
                        report = verify_publication(settings, version, row[1])
                    except ValueError as exc:
                        raise HTTPException(400, str(exc)) from exc
                    active = conn.execute(sa.text(
                        "SELECT version FROM kb_versions WHERE status='ACTIVE' FOR UPDATE")).scalars().all()
                    if len(active) > 1:
                        raise HTTPException(409, "存在多个 ACTIVE 版本，需先修复状态")
                    if action == "rollback" and len(active) != 1:
                        raise HTTPException(409, "回滚必须存在唯一 ACTIVE 版本")
                    current = active[0] if active else None
                    if current:
                        conn.execute(sa.text(
                            "UPDATE kb_versions SET status='ARCHIVED' WHERE version=:v"), {"v": current})
                    conn.execute(sa.text(
                        "UPDATE kb_versions SET status='ACTIVE', published_at=NOW(), activated_by=:a "
                        "WHERE version=:v"), {"a": actor, "v": version})
                    conn.execute(sa.text(
                        "INSERT INTO audit_log (action, actor, payload_json) VALUES (:act, :a, :p)"),
                        {"act": action, "a": actor, "p": json.dumps(
                            {"from": current, "to": version, "snapshot": report["snapshot"]}, ensure_ascii=False)})
            finally:
                try:
                    conn.execute(sa.text("SELECT RELEASE_LOCK('rag_kb_publication')"))
                    conn.commit()
                except Exception:
                    # Close the physical connection rather than returning a
                    # session-level advisory lock to the pool. SQL may be committed.
                    conn.invalidate()
                    logging.getLogger(__name__).exception("publication lock cleanup failed")
    except HTTPException:
        raise
    except Exception as exc:
        logging.getLogger(__name__).exception("kb publication verification failed")
        raise HTTPException(503, "发布检查暂不可用，未完成发布") from exc
    finally:
        engine.dispose()
    deleted = 0
    cache_ok = True
    try:
        cache = CacheLayer(settings.redis_url)
        for target in {version, current} - {None}:
            deleted += cache.invalidate_version(target, strict=True)
    except Exception:
        cache_ok = False
        logging.getLogger(__name__).exception("kb publication committed; cache invalidation failed")
    return {"activated": version, "archived": current, "cache_invalidated": deleted,
            "cache_invalidation_ok": cache_ok}


def _require_global_admin(ctx: AuthContext) -> None:
    require_global_admin(ctx)


@router.post("/activate")
def activate(body: ActivateRequest,
             ctx: AuthContext = Depends(get_auth_context)) -> dict:
    _require_global_admin(ctx)
    return _activate(body.version, ctx.user_id)


@router.get("/versions/{version}/manifest")
def version_manifest(version: str, ctx: AuthContext = Depends(get_auth_context)) -> dict:
    _require_global_admin(ctx)
    from qa_core.storage.version_manifest import VersionManifestDao
    dao = VersionManifestDao(get_settings().mysql_dsn)
    try:
        manifest = dao.get(version)
        if manifest is None:
            raise HTTPException(404, "该版本无持久化摘要（旧版本未自动回填）")
        return {"version": version, "binding": manifest["binding"],
                "last_gate_snapshot": manifest["snapshot"]}
    finally:
        dao.close()


@router.post("/rollback")
def rollback(body: ActivateRequest,
             ctx: AuthContext = Depends(get_auth_context)) -> dict:
    """仅回滚合格 ARCHIVED 版本；实时重验绑定、快照和撤回，复用发布锁。"""
    _require_global_admin(ctx)
    return _activate(body.version, ctx.user_id, action="rollback")
