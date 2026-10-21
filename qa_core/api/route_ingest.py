"""知识入库任务 API：快速排队、查询、取消与手动重试。"""
from __future__ import annotations

import hashlib
import json
from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field

from qa_core.api.auth import AuthContext, get_auth_context, require_roles, require_global_admin
from qa_core.config.settings import get_settings
from qa_core.ingest.jobs import IngestJobDao, TERMINAL_STATUSES
from qa_core.ingest.write_guard import candidate_write
from qa_core.ingest.binding import execution_binding, version_binding, digest as binding_digest

router = APIRouter(prefix="/kb/rebuild", tags=["ingest-jobs"])


class RebuildRequest(BaseModel):
    mode: str = Field(default="incremental", pattern="^(incremental|full)$")
    kb_version: str = Field(min_length=1, max_length=32, pattern=r"^[A-Za-z0-9._-]+$")
    data_dir: str | None = None
    run_quality_gate: bool = True


def _dao() -> IngestJobDao:
    return IngestJobDao(get_settings().mysql_dsn)


def _safe_raw_dir(value: str | None) -> Path:
    base = (Path(get_settings().data_dir) / "raw").resolve()
    selected = Path(value).resolve() if value else base
    # 管理 API 不允许借 data_dir 读取项目或主机任意路径。
    if selected != base and base not in selected.parents:
        raise HTTPException(400, "data_dir 必须位于 DATA_DIR/raw 内")
    if not selected.is_dir():
        raise HTTPException(400, f"数据目录不存在: {selected}")
    return selected


def _snapshot_fingerprint(raw_dir: Path) -> str:
    items = []
    for path in sorted(p for p in raw_dir.iterdir() if p.is_file() and not p.name.startswith(".")):
        stat = path.stat()
        items.append((path.name, stat.st_size, stat.st_mtime_ns))
    return hashlib.sha256(json.dumps(items, separators=(",", ":")).encode()).hexdigest()


def _public(job: dict) -> dict:
    payload = dict(job.get("payload_json") or {})
    payload.pop("staging_path", None)
    job = dict(job)
    job["payload"] = payload
    job.pop("payload_json", None)
    done, total = int(job.get("progress_done") or 0), int(job.get("progress_total") or 0)
    job["progress"] = {"done": done, "total": total,
                       "percent": round(done * 100 / total, 1) if total else 0.0}
    return job


@router.post("", status_code=202)
def create_rebuild(body: RebuildRequest,
                   ctx: AuthContext = Depends(get_auth_context)) -> dict:
    require_global_admin(ctx)
    raw_dir = _safe_raw_dir(body.data_dir)
    snapshot = _snapshot_fingerprint(raw_dir)
    idem = hashlib.sha256(
        f"rebuild:{ctx.tenant_id}:{body.kb_version}:{body.mode}:{raw_dir}:{snapshot}:{binding_digest(version_binding(get_settings()))}".encode()
    ).hexdigest()
    with candidate_write(body.kb_version):
        payload = {"raw_dir": str(raw_dir), "run_quality_gate": body.run_quality_gate,
                   "snapshot": snapshot}
        payload["execution_binding"] = execution_binding(get_settings(), "rebuild", payload)
        job, created = _dao().create(
            job_type="rebuild", tenant_id=ctx.tenant_id, actor=ctx.user_id,
            kb_version=body.kb_version, mode=body.mode,
            payload=payload, idempotency_key=idem)
    return {"created": created, "job": _public(job)}


@router.get("")
def list_jobs(limit: int = 50, ctx: AuthContext = Depends(get_auth_context)) -> dict:
    require_roles(ctx, ("ops", "auditor", "admin"))
    return {"jobs": [_public(job) for job in _dao().list(ctx.tenant_id, limit)]}


@router.get("/{job_id}")
def get_job(job_id: str, ctx: AuthContext = Depends(get_auth_context)) -> dict:
    require_roles(ctx, ("ops", "auditor", "admin"))
    job = _dao().get(job_id, ctx.tenant_id)
    if job is None:
        raise HTTPException(404, "任务不存在")
    return _public(job)


@router.post("/{job_id}/cancel")
def cancel_job(job_id: str, ctx: AuthContext = Depends(get_auth_context)) -> dict:
    require_roles(ctx, ("admin",))
    current = _dao().get(job_id, ctx.tenant_id)
    if current is None:
        raise HTTPException(404, "任务不存在")
    if current["status"] in TERMINAL_STATUSES:
        raise HTTPException(409, f"任务已处于终态 {current['status']}")
    return _public(_dao().request_cancel(job_id, ctx.tenant_id))


@router.post("/{job_id}/retry", status_code=202)
def retry_job(job_id: str, ctx: AuthContext = Depends(get_auth_context)) -> dict:
    require_roles(ctx, ("admin",))
    current = _dao().get(job_id, ctx.tenant_id)
    if current is None:
        raise HTTPException(404, "任务不存在")
    if current["job_type"] == "rebuild":
        require_global_admin(ctx)
    if current["status"] not in {"failed", "cancelled"}:
        raise HTTPException(409, "仅 failed/cancelled 任务可重试")
    with candidate_write(current["kb_version"]):
        return _public(_dao().retry(job_id, ctx.tenant_id))
