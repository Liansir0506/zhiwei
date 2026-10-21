"""Adapter 增量游标状态存储。"""
from __future__ import annotations

from datetime import datetime
from typing import Protocol

import sqlalchemy as sa


class SyncStateStore(Protocol):
    def get(self, adapter_name: str, tenant_id: str) -> dict | None: ...
    def save(self, adapter_name: str, tenant_id: str, **values) -> None: ...
    def list(self, tenant_id: str) -> list[dict]: ...


class MySQLSyncStateStore:
    def __init__(self, dsn: str):
        self.engine = sa.create_engine(dsn, pool_pre_ping=True)

    def get(self, adapter_name: str, tenant_id: str) -> dict | None:
        with self.engine.begin() as conn:
            row = conn.execute(sa.text(
                "SELECT * FROM adapter_sync_state WHERE adapter_name=:name AND tenant_id=:tenant"
            ), {"name": adapter_name, "tenant": tenant_id}).mappings().first()
        return dict(row) if row else None

    def save(self, adapter_name: str, tenant_id: str, **values) -> None:
        allowed = {"cursor_value", "sync_status", "last_error_code", "last_error_message",
                   "simulation", "items_synced", "last_synced_at"}
        payload = {key: value for key, value in values.items() if key in allowed}
        payload.update(adapter_name=adapter_name, tenant_id=tenant_id)
        columns = list(payload)
        updates = [c for c in columns if c not in {"adapter_name", "tenant_id"}]
        sql = (f"INSERT INTO adapter_sync_state ({', '.join(f'`{c}`' for c in columns)}) "
               f"VALUES ({', '.join(f':{c}' for c in columns)}) ON DUPLICATE KEY UPDATE "
               + ", ".join(f"`{c}`=VALUES(`{c}`)" for c in updates))
        with self.engine.begin() as conn:
            conn.execute(sa.text(sql), payload)

    def list(self, tenant_id: str) -> list[dict]:
        with self.engine.begin() as conn:
            rows = conn.execute(sa.text(
                "SELECT * FROM adapter_sync_state WHERE tenant_id=:tenant ORDER BY adapter_name"
            ), {"tenant": tenant_id}).mappings().all()
        return [dict(row) for row in rows]


class MemorySyncStateStore:
    def __init__(self):
        self.states: dict[tuple[str, str], dict] = {}

    def get(self, adapter_name: str, tenant_id: str) -> dict | None:
        state = self.states.get((adapter_name, tenant_id))
        return dict(state) if state else None

    def save(self, adapter_name: str, tenant_id: str, **values) -> None:
        key = (adapter_name, tenant_id)
        state = self.states.setdefault(key, {"adapter_name": adapter_name, "tenant_id": tenant_id})
        state.update(values, updated_at=datetime.now())

    def list(self, tenant_id: str) -> list[dict]:
        return [dict(v) for (name, tenant), v in sorted(self.states.items()) if tenant == tenant_id]
