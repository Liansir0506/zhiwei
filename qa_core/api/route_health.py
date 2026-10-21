"""健康检查：纯函数 + 路由。阶段3 起 lifespan 探测组件并缓存到 app.state。"""
from __future__ import annotations

from typing import Literal

from fastapi import APIRouter, Request

from qa_core.config.settings import get_settings

router = APIRouter()


def _component_ok(value: str | None) -> bool:
    return bool(value) and str(value).startswith("ok")


def build_health_payload(component_status: dict[str, str] | None = None) -> dict:
    """组装 /health 响应（纯函数，便于单测）。"""
    settings = get_settings()
    status = component_status or {
        "mysql": "not_checked",
        "redis": "not_checked",
        "milvus": "not_checked",
        "models": "not_checked",
    }
    overall = "ok" if all(_component_ok(v) for v in status.values()) else "degraded"
    from qa_core.intent.model_classifier import readiness

    return {
        "status": overall,
        "app": settings.app_name,
        "environment": settings.app_env,
        "scenario": settings.scenario,
        "generation_enabled": settings.generation_enabled,
        "agent_enabled": bool(settings.agent_enabled and settings.is_test and settings.auth_required),
        "components": status,
        "intent_model": readiness(settings.intent_model_path, settings.embedding_device),
    }


@router.get("/health")
def health(request: Request, refresh: int = 0) -> dict:
    """refresh=1 时实时探测各组件，否则用 lifespan 启动时的缓存结果。"""
    status = getattr(request.app.state, "health_components", None)
    if refresh or status is None:
        from qa_core.storage.connections import check_all

        status = check_all(get_settings())
        request.app.state.health_components = status
    return build_health_payload(status)
