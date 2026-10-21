"""带分页、超时、有限重试与契约校验的 HTTP Adapter 基类。"""
from __future__ import annotations

import time
from typing import Callable

import httpx
from pydantic import BaseModel, ConfigDict, Field, ValidationError

from qa_core.integrations.base import AdapterPage
from qa_core.integrations.errors import (
    AdapterContractError,
    AdapterRejected,
    AdapterUnavailable,
)


class PageEnvelope(BaseModel):
    model_config = ConfigDict(extra="forbid")

    source: str
    simulation: bool = False
    items: list[dict] = Field(default_factory=list)
    next_cursor: str | None = None


class HttpEnterpriseAdapter:
    name = "base"
    resource = ""

    def __init__(self, base_url: str, *, timeout_seconds: float = 2.0,
                 max_retries: int = 2, client: httpx.Client | None = None,
                 sleeper: Callable[[float], None] = time.sleep,
                 headers: dict[str, str] | None = None):
        self.base_url = base_url.rstrip("/")
        self.timeout_seconds = timeout_seconds
        self.max_retries = max(0, max_retries)
        self.client = client or httpx.Client(timeout=timeout_seconds)
        self.sleeper = sleeper
        self.headers = headers or {}

    def fetch_page(self, cursor: str | None = None, limit: int = 100) -> AdapterPage:
        params = {"limit": max(1, min(int(limit), 200))}
        if cursor:
            params["cursor"] = cursor
        last_error: Exception | None = None
        for attempt in range(self.max_retries + 1):
            try:
                response = self.client.get(
                    f"{self.base_url}/{self.resource}", params=params,
                    headers=self.headers, timeout=self.timeout_seconds)
            except (httpx.TimeoutException, httpx.NetworkError) as exc:
                last_error = exc
                if attempt < self.max_retries:
                    self.sleeper(0.05 * (2 ** attempt))
                    continue
                raise AdapterUnavailable(self.name, f"请求超时或网络不可达: {exc}") from exc
            if response.status_code in (429, 502, 503, 504):
                last_error = RuntimeError(f"HTTP {response.status_code}")
                if attempt < self.max_retries:
                    self.sleeper(0.05 * (2 ** attempt))
                    continue
                raise AdapterUnavailable(
                    self.name, f"上游暂不可用: HTTP {response.status_code}",
                    response.status_code)
            if response.status_code >= 400:
                raise AdapterRejected(
                    self.name, f"上游拒绝请求: HTTP {response.status_code}",
                    response.status_code)
            try:
                envelope = PageEnvelope.model_validate(response.json())
            except (ValueError, ValidationError) as exc:
                raise AdapterContractError(self.name, f"分页响应不符合契约: {exc}") from exc
            if envelope.source != self.name:
                raise AdapterContractError(
                    self.name, f"响应 source={envelope.source!r} 与 Adapter 不一致")
            return AdapterPage(source=self.name, items=envelope.items,
                               next_cursor=envelope.next_cursor,
                               simulation=envelope.simulation)
        raise AdapterUnavailable(self.name, str(last_error or "未知错误"))
