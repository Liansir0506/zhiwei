"""仅用于技术验证的模拟企业系统，不代表任何真实客户或生产数据。"""
from __future__ import annotations

import base64

from fastapi import FastAPI, Header, HTTPException, Query, Response

app = FastAPI(
    title="SIMULATED Enterprise Systems",
    version="1.0.0",
    description="技术验证专用模拟 Wiki/CMDB/告警/监控/工单/变更服务；所有数据均为虚构。",
)

_DATA = {
    "cmdb": [
        {"device_id": "mock-device-001", "tenant_id": "default", "dept_id": "public",
         "cmdb_id": "MOCK-CMDB-001", "name": "模拟核心网网元-01", "device_type": "5GC-UPF",
         "vendor": "MockVendor", "model": "MV-UPF-1000", "network_tech": "5G",
         "software_version": "V1.2.0", "status": "online",
         "attributes_json": {"simulation": True, "region": "lab-a"}},
        {"device_id": "mock-device-002", "tenant_id": "default", "dept_id": "public",
         "cmdb_id": "MOCK-CMDB-002", "name": "模拟接入网基站-02", "device_type": "gNodeB",
         "vendor": "MockVendor", "model": "MV-GNB-2000", "network_tech": "5G",
         "software_version": "V3.4.1", "status": "maintenance",
         "attributes_json": {"simulation": True, "region": "lab-b"}},
    ],
    "alarms": [
        {"alarm_id": "mock-alarm-001", "tenant_id": "default", "device_id": "mock-device-001",
         "alarm_code": "MOCK-LINK-LOSS", "severity": "major", "status": "open",
         "source_system": "mock-alarm-platform", "occurred_at": "2026-09-18T08:30:00",
         "payload_json": {"simulation": True, "port": "N6-01"}},
    ],
    "monitoring": [
        {"observation_id": "mock-metric-001", "tenant_id": "default",
         "device_id": "mock-device-001", "metric_name": "packet_loss_ratio",
         "metric_value": 2.4, "unit": "%", "sampled_at": "2026-09-18T08:31:00",
         "source_system": "mock-monitoring", "labels_json": {"simulation": True}},
    ],
    "work-orders": [
        {"ticket_id": "mock-ticket-001", "tenant_id": "default", "dept_id": "public",
         "device_id": "mock-device-001", "alarm_id": "mock-alarm-001",
         "title": "模拟 N6 链路丢包排障", "priority": "P2", "status": "processing",
         "source_system": "mock-ticket-system", "opened_at": "2026-09-18T08:35:00"},
    ],
    "changes": [
        {"change_id": "mock-change-001", "tenant_id": "default", "dept_id": "public",
         "device_id": "mock-device-001", "ticket_id": "mock-ticket-001",
         "title": "模拟链路参数调整", "change_type": "network_parameter",
         "status": "approved", "window_start": "2026-09-19T02:00:00",
         "window_end": "2026-09-19T03:00:00", "source_system": "mock-change-system"},
    ],
    "wiki": [
        {"asset_id": "mock-asset-001", "tenant_id": "default", "dept_id": "public",
         "filename": "模拟UPF链路丢包处置手册.md", "doc_type": "runbook",
         "source_system": "mock-wiki", "source_id": "MOCK-WIKI-001", "source_version": "v2",
         "security_level": "internal", "visibility": "public", "status": "active",
         "effective_from": "2026-01-01T00:00:00", "kb_version": "2026.09",
         "relations": [
             {"relation_type": "device", "relation_id": "mock-device-001"},
             {"relation_type": "alarm", "relation_id": "mock-alarm-001"},
             {"relation_type": "work_order", "relation_id": "mock-ticket-001"},
         ]},
    ],
}


def _encode_cursor(offset: int) -> str:
    return base64.urlsafe_b64encode(f"offset:{offset}".encode()).decode().rstrip("=")


def _decode_cursor(cursor: str | None) -> int:
    if not cursor:
        return 0
    try:
        padded = cursor + "=" * (-len(cursor) % 4)
        value = base64.urlsafe_b64decode(padded.encode()).decode()
        prefix, offset = value.split(":", 1)
        if prefix != "offset" or int(offset) < 0:
            raise ValueError
        return int(offset)
    except (ValueError, UnicodeDecodeError) as exc:
        raise HTTPException(status_code=400, detail="invalid cursor") from exc


@app.get("/health")
def health() -> dict:
    return {"status": "ok", "simulation": True, "service": "enterprise-mock"}


@app.get("/mock/v1/{resource}")
def page(resource: str, response: Response, cursor: str | None = None,
         limit: int = Query(default=100, ge=1, le=200),
         x_mock_failure: str | None = Header(default=None)) -> dict:
    if resource not in _DATA:
        raise HTTPException(status_code=404, detail="unknown simulated resource")
    if x_mock_failure == "503":
        raise HTTPException(status_code=503, detail="injected simulated outage")
    if x_mock_failure == "invalid":
        return {"unexpected": "injected invalid contract"}
    start = _decode_cursor(cursor)
    records = _DATA[resource]
    items = records[start:start + limit]
    end = start + len(items)
    response.headers["X-Simulation"] = "true"
    source = "work_orders" if resource == "work-orders" else resource
    # 即使已到末尾也返回已消费位置，作为下一次增量同步的高水位。
    # 调用方再次请求该游标会得到空页；未来新增记录则从该位置继续。
    return {"source": source, "simulation": True, "items": items,
            "next_cursor": _encode_cursor(end)}
