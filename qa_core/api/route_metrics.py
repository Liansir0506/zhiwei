"""Prometheus /metrics 端点（prometheus_client 未安装时返回空指标+提示）。"""
from __future__ import annotations

from fastapi import APIRouter, Depends, Response
from fastapi import Depends

from qa_core.api.auth import AuthContext, get_auth_context

router = APIRouter(tags=["metrics"])

try:
    from prometheus_client import CONTENT_TYPE_LATEST, generate_latest
    from qa_core.api import metrics as _metrics_registry  # noqa: F401 提前注册指标

    @router.get("/metrics")
    def metrics(ctx: AuthContext = Depends(get_auth_context)) -> Response:
        return Response(content=generate_latest(), media_type=CONTENT_TYPE_LATEST)

except ImportError:
    @router.get("/metrics")
    def metrics(ctx: AuthContext = Depends(get_auth_context)) -> Response:
        return Response(
            content="# prometheus_client 未安装：在环境中 pip install prometheus_client 后重启\n",
            media_type="text/plain; version=0.0.4")
