"""Adapter 稳定接口；真实实现和模拟实现均遵守该协议。"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol

from qa_core.domain.models import EnterpriseSnapshot


@dataclass(frozen=True)
class AdapterPage:
    source: str
    items: list[dict]
    next_cursor: str | None
    simulation: bool


class EnterpriseAdapter(Protocol):
    name: str

    def fetch_page(self, cursor: str | None = None, limit: int = 100) -> AdapterPage: ...

    def map_items(self, items: list[dict]) -> EnterpriseSnapshot: ...
