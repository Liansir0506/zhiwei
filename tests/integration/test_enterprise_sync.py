from __future__ import annotations

import sqlalchemy as sa
from fastapi.testclient import TestClient

from qa_core.config.settings import get_settings
from qa_core.integrations.adapters import SYNC_ORDER, build_adapter_registry
from qa_core.integrations.mock_service import app
from qa_core.integrations.state import MySQLSyncStateStore
from qa_core.integrations.sync_service import EnterpriseSyncService
from qa_core.storage.domain_dao import DomainDao
from scripts.migrate_domain_model import apply_migration


def test_simulated_enterprise_sync_persists_all_sources_and_cursors():
    settings = get_settings()
    assert settings.is_test
    apply_migration(settings.mysql_dsn)
    registry = build_adapter_registry(settings, client=TestClient(app))
    store = MySQLSyncStateStore(settings.mysql_dsn)
    service = EnterpriseSyncService(registry, DomainDao(settings.mysql_dsn), store)

    results = service.sync_all(page_size=1, reset_cursor=True)

    assert [item["source"] for item in results] == list(SYNC_ORDER)
    assert all(item["status"] == "success" for item in results)
    assert all(item["simulation"] is True for item in results)
    assert sum(item["items_synced"] for item in results) == 7
    states = store.list("default")
    assert len(states) == 6
    assert all(row["sync_status"] == "success" for row in states)
    assert all(row["cursor_value"] for row in states)
    incremental = service.sync_all(page_size=1)
    assert all(item["items_synced"] == 0 for item in incremental)

    engine = sa.create_engine(settings.mysql_dsn)
    with engine.begin() as conn:
        counts = {
            table: conn.execute(sa.text(
                f"SELECT COUNT(*) FROM `{table}` WHERE `{key}` LIKE 'mock-%'"
            )).scalar()
            for table, key in {
                "devices": "device_id", "monitoring_metrics": "observation_id",
                "alarms": "alarm_id", "work_orders": "ticket_id",
                "change_records": "change_id", "knowledge_assets": "asset_id",
            }.items()
        }
    assert counts == {"devices": 2, "monitoring_metrics": 1, "alarms": 1,
                      "work_orders": 1, "change_records": 1, "knowledge_assets": 1}
