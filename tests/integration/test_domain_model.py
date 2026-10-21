"""第2步企业统一领域模型：关系写入、租户隔离与上下文 API。"""
from datetime import datetime, timedelta
from uuid import uuid4

import pytest
import sqlalchemy as sa
from fastapi.testclient import TestClient

from app import create_app
from qa_core.config.settings import get_settings
from qa_core.domain.models import (
    Alarm,
    ChangeRecord,
    Department,
    Device,
    EnterpriseSnapshot,
    EnterpriseUser,
    KnowledgeAsset,
    KnowledgeRelation,
    MonitoringMetric,
    Tenant,
    UserDepartment,
    WorkOrder,
)
from qa_core.storage.domain_dao import DomainDao
from scripts.migrate_domain_model import apply_migration


@pytest.fixture()
def domain_fixture():
    settings = get_settings()
    result = apply_migration(settings.mysql_dsn)
    assert result["recorded"] and len(result["tables"]) == 11

    suffix = uuid4().hex[:8]
    ids = {
        "tenant": f"test-tenant-{suffix}", "dept": f"test-dept-{suffix}",
        "user": f"test-user-{suffix}", "device": f"test-device-{suffix}",
        "alarm": f"test-alarm-{suffix}", "ticket": f"test-ticket-{suffix}",
        "change": f"test-change-{suffix}", "asset": f"test-asset-{suffix}",
        "metric": f"test-metric-{suffix}",
    }
    now = datetime.now().replace(microsecond=0)
    snapshot = EnterpriseSnapshot(
        tenants=[Tenant(tenant_id=ids["tenant"], name="集成测试租户")],
        departments=[Department(dept_id=ids["dept"], tenant_id=ids["tenant"], name="无线运维部")],
        users=[EnterpriseUser(user_id=ids["user"], tenant_id=ids["tenant"],
                              username=f"engineer-{suffix}")],
        user_departments=[UserDepartment(user_id=ids["user"], dept_id=ids["dept"],
                                         is_primary=True)],
        devices=[Device(device_id=ids["device"], tenant_id=ids["tenant"],
                        dept_id=ids["dept"], cmdb_id=f"CMDB-{suffix}", name="测试 BBU",
                        device_type="BBU", vendor="华为", network_tech="5G")],
        monitoring_metrics=[MonitoringMetric(
            observation_id=ids["metric"], tenant_id=ids["tenant"],
            device_id=ids["device"], metric_name="optical_rx_power",
            metric_value=-18.6, unit="dBm", sampled_at=now,
            source_system="monitoring-platform")],
        alarms=[Alarm(alarm_id=ids["alarm"], tenant_id=ids["tenant"],
                      device_id=ids["device"], alarm_code="ALM-1201",
                      source_system="alarm-platform", occurred_at=now)],
        work_orders=[WorkOrder(ticket_id=ids["ticket"], tenant_id=ids["tenant"],
                               dept_id=ids["dept"], device_id=ids["device"],
                               alarm_id=ids["alarm"], title="传输中断处置",
                               source_system="ticket-system", opened_at=now,
                               assignee_user_id=ids["user"])],
        changes=[ChangeRecord(change_id=ids["change"], tenant_id=ids["tenant"],
                              dept_id=ids["dept"], device_id=ids["device"],
                              ticket_id=ids["ticket"], title="光模块更换",
                              change_type="hardware", source_system="change-system",
                              operator_user_id=ids["user"], window_start=now,
                              window_end=now + timedelta(hours=1))],
        knowledge_assets=[KnowledgeAsset(
            asset_id=ids["asset"], tenant_id=ids["tenant"], dept_id=ids["dept"],
            filename="sop_transmission.md", doc_type="sop", source_system="confluence",
            source_id="PAGE-1001", source_version="3.2", status="active",
            effective_from=now - timedelta(days=1), role_scope=["ops"],
        )],
        knowledge_relations=[
            KnowledgeRelation(asset_id=ids["asset"], relation_type="device",
                              relation_id=ids["device"]),
            KnowledgeRelation(asset_id=ids["asset"], relation_type="alarm",
                              relation_id=ids["alarm"]),
            KnowledgeRelation(asset_id=ids["asset"], relation_type="work_order",
                              relation_id=ids["ticket"]),
            KnowledgeRelation(asset_id=ids["asset"], relation_type="change",
                              relation_id=ids["change"]),
        ],
    )
    counts = DomainDao(settings.mysql_dsn).upsert_snapshot(snapshot)
    assert counts["devices"] == 1 and counts["monitoring_metrics"] == 1
    assert counts["knowledge_relations"] == 4
    yield ids

    engine = sa.create_engine(settings.mysql_dsn)
    with engine.begin() as conn:
        for table, column, value in (
            ("knowledge_relations", "asset_id", ids["asset"]),
            ("knowledge_assets", "asset_id", ids["asset"]),
            ("change_records", "change_id", ids["change"]),
            ("work_orders", "ticket_id", ids["ticket"]),
            ("alarms", "alarm_id", ids["alarm"]),
            ("monitoring_metrics", "observation_id", ids["metric"]),
            ("devices", "device_id", ids["device"]),
            ("user_departments", "user_id", ids["user"]),
            ("users", "user_id", ids["user"]),
            ("departments", "dept_id", ids["dept"]),
            ("tenants", "tenant_id", ids["tenant"]),
        ):
            conn.execute(sa.text(f"DELETE FROM `{table}` WHERE `{column}`=:value"), {"value": value})


@pytest.mark.integration
def test_alarm_context_joins_enterprise_entities(domain_fixture):
    ids = domain_fixture
    client = TestClient(create_app())
    headers = {"X-User-Roles": "ops", "X-Tenant-ID": ids["tenant"]}
    response = client.get(f"/domain/alarms/{ids['alarm']}/context", headers=headers)
    assert response.status_code == 200
    body = response.json()
    assert body["device"]["cmdb_id"].startswith("CMDB-")
    assert body["monitoring"][0]["metric_name"] == "optical_rx_power"
    assert body["work_orders"][0]["ticket_id"] == ids["ticket"]
    assert body["changes"][0]["change_id"] == ids["change"]
    assert body["knowledge"][0]["source_system"] == "confluence"


@pytest.mark.integration
def test_domain_context_is_tenant_isolated(domain_fixture):
    ids = domain_fixture
    client = TestClient(create_app())
    response = client.get(
        f"/domain/devices/{ids['device']}/context",
        headers={"X-User-Roles": "ops", "X-Tenant-ID": "default"},
    )
    assert response.status_code == 404
