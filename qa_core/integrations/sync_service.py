"""Adapter 编排：增量分页、统一入库以及可观测的降级结果。"""
from __future__ import annotations

from datetime import datetime

from qa_core.integrations.adapters import SYNC_ORDER
from qa_core.integrations.errors import AdapterContractError, AdapterError


class EnterpriseSyncService:
    def __init__(self, adapters: dict, domain_dao, state_store):
        self.adapters = adapters
        self.domain_dao = domain_dao
        self.state_store = state_store

    def sync_one(self, name: str, tenant_id: str = "default", *, page_size: int = 100,
                 reset_cursor: bool = False, max_pages: int = 100) -> dict:
        if name not in self.adapters:
            raise ValueError(f"未知 Adapter: {name}")
        adapter = self.adapters[name]
        previous = self.state_store.get(name, tenant_id) or {}
        cursor = None if reset_cursor else previous.get("cursor_value")
        total = 0
        simulation = False
        self.state_store.save(name, tenant_id, cursor_value=cursor, sync_status="running",
                              last_error_code=None, last_error_message=None,
                              simulation=False, items_synced=0)
        try:
            for _ in range(max_pages):
                page = adapter.fetch_page(cursor=cursor, limit=page_size)
                simulation = simulation or page.simulation
                if not page.items:
                    cursor = page.next_cursor or cursor
                    self.state_store.save(name, tenant_id, cursor_value=cursor,
                                          sync_status="running", simulation=simulation,
                                          items_synced=total)
                    break
                snapshot = adapter.map_items(page.items)
                self.domain_dao.upsert_snapshot(snapshot)
                total += len(page.items)
                if cursor is not None and page.next_cursor == cursor:
                    raise AdapterContractError(name, "上游返回未前进的分页游标")
                cursor = page.next_cursor
                self.state_store.save(name, tenant_id, cursor_value=cursor,
                                      sync_status="running", simulation=simulation,
                                      items_synced=total)
                if cursor is None:
                    break
            else:
                raise RuntimeError(f"分页超过安全上限 {max_pages}")
        except AdapterError as exc:
            self.state_store.save(name, tenant_id, cursor_value=cursor,
                                  sync_status="degraded", simulation=simulation,
                                  items_synced=total, last_error_code=exc.code,
                                  last_error_message=exc.message,
                                  last_synced_at=datetime.now())
            return {"source": name, "status": "degraded", "simulation": simulation,
                    "items_synced": total, "cursor": cursor, "error": exc.to_dict()}
        self.state_store.save(name, tenant_id, cursor_value=cursor, sync_status="success",
                              simulation=simulation, items_synced=total,
                              last_error_code=None, last_error_message=None,
                              last_synced_at=datetime.now())
        return {"source": name, "status": "success", "simulation": simulation,
                "items_synced": total, "cursor": cursor, "error": None}

    def sync_all(self, tenant_id: str = "default", *, sources: list[str] | None = None,
                 page_size: int = 100, reset_cursor: bool = False) -> list[dict]:
        selected = sources or list(SYNC_ORDER)
        unknown = sorted(set(selected) - set(self.adapters))
        if unknown:
            raise ValueError(f"未知 Adapter: {', '.join(unknown)}")
        ordered = [name for name in SYNC_ORDER if name in selected]
        return [self.sync_one(name, tenant_id, page_size=page_size,
                              reset_cursor=reset_cursor) for name in ordered]
