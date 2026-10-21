from __future__ import annotations

import httpx
import pytest
from fastapi.testclient import TestClient

from qa_core.integrations.adapters import ADAPTER_CLASSES, SYNC_ORDER, build_adapter_registry
from qa_core.integrations.errors import AdapterContractError
from qa_core.integrations.mock_service import app
from qa_core.integrations.state import MemorySyncStateStore
from qa_core.integrations.sync_service import EnterpriseSyncService


class _Settings:
    enterprise_base_url = "http://testserver/mock/v1"
    adapter_timeout_seconds = 0.1
    adapter_max_retries = 1


class _Dao:
    def __init__(self):
        self.snapshots = []

    def upsert_snapshot(self, snapshot):
        self.snapshots.append(snapshot)
        return {}


@pytest.mark.parametrize("name", SYNC_ORDER)
def test_each_adapter_connects_and_maps_simulated_service(name):
    adapter = build_adapter_registry(_Settings(), client=TestClient(app))[name]
    page = adapter.fetch_page(limit=1)
    snapshot = adapter.map_items(page.items)
    assert page.source == name
    assert page.simulation is True
    assert sum(len(value) for value in snapshot.model_dump().values()) >= 1


def test_pagination_uses_opaque_cursor():
    adapter = build_adapter_registry(_Settings(), client=TestClient(app))["cmdb"]
    first = adapter.fetch_page(limit=1)
    second = adapter.fetch_page(cursor=first.next_cursor, limit=1)
    third = adapter.fetch_page(cursor=second.next_cursor, limit=1)
    assert first.next_cursor
    assert first.items[0]["device_id"] != second.items[0]["device_id"]
    assert second.next_cursor
    assert third.items == []
    assert third.next_cursor == second.next_cursor


def test_saved_high_watermark_makes_second_sync_incremental():
    adapters = build_adapter_registry(_Settings(), client=TestClient(app))
    dao, state = _Dao(), MemorySyncStateStore()
    service = EnterpriseSyncService(adapters, dao, state)
    first = service.sync_one("cmdb", page_size=1)
    second = service.sync_one("cmdb", page_size=1)
    assert first["items_synced"] == 2
    assert second["items_synced"] == 0
    assert second["cursor"] == first["cursor"]


def test_invalid_contract_is_standardized():
    adapter = build_adapter_registry(
        _Settings(), client=TestClient(app), headers={"X-Mock-Failure": "invalid"})["cmdb"]
    with pytest.raises(AdapterContractError) as exc:
        adapter.fetch_page()
    assert exc.value.code == "CONTRACT_INVALID"


def test_upstream_outage_degrades_without_crashing_core():
    adapters = build_adapter_registry(
        _Settings(), client=TestClient(app), headers={"X-Mock-Failure": "503"})
    service = EnterpriseSyncService(adapters, _Dao(), MemorySyncStateStore())
    result = service.sync_one("cmdb")
    assert result["status"] == "degraded"
    assert result["error"]["retryable"] is True


def test_adapter_implementation_can_be_replaced_without_sync_core_change():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"source": "cmdb", "simulation": False,
            "items": [{"device_id": "real-shaped-1", "tenant_id": "default",
                       "name": "替代实现", "device_type": "router"}], "next_cursor": None})

    client = httpx.Client(transport=httpx.MockTransport(handler))
    adapter = ADAPTER_CLASSES["cmdb"]("https://replacement.example/v1", client=client)
    dao, state = _Dao(), MemorySyncStateStore()
    result = EnterpriseSyncService({"cmdb": adapter}, dao, state).sync_one("cmdb")
    assert result["status"] == "success"
    assert dao.snapshots[0].devices[0].device_id == "real-shaped-1"
