"""Wiki、CMDB、告警、监控、工单、变更六类 Adapter 与字段映射。"""
from __future__ import annotations

from qa_core.domain.models import (
    Alarm,
    ChangeRecord,
    Device,
    EnterpriseSnapshot,
    KnowledgeAsset,
    KnowledgeRelation,
    MonitoringMetric,
    WorkOrder,
)
from qa_core.integrations.http_adapter import HttpEnterpriseAdapter


class CmdbAdapter(HttpEnterpriseAdapter):
    name, resource = "cmdb", "cmdb"

    def map_items(self, items: list[dict]) -> EnterpriseSnapshot:
        return EnterpriseSnapshot(devices=[Device.model_validate(item) for item in items])


class AlarmAdapter(HttpEnterpriseAdapter):
    name, resource = "alarms", "alarms"

    def map_items(self, items: list[dict]) -> EnterpriseSnapshot:
        return EnterpriseSnapshot(alarms=[Alarm.model_validate(item) for item in items])


class MonitoringAdapter(HttpEnterpriseAdapter):
    name, resource = "monitoring", "monitoring"

    def map_items(self, items: list[dict]) -> EnterpriseSnapshot:
        return EnterpriseSnapshot(
            monitoring_metrics=[MonitoringMetric.model_validate(item) for item in items])


class WorkOrderAdapter(HttpEnterpriseAdapter):
    name, resource = "work_orders", "work-orders"

    def map_items(self, items: list[dict]) -> EnterpriseSnapshot:
        return EnterpriseSnapshot(work_orders=[WorkOrder.model_validate(item) for item in items])


class ChangeAdapter(HttpEnterpriseAdapter):
    name, resource = "changes", "changes"

    def map_items(self, items: list[dict]) -> EnterpriseSnapshot:
        return EnterpriseSnapshot(changes=[ChangeRecord.model_validate(item) for item in items])


class WikiAdapter(HttpEnterpriseAdapter):
    name, resource = "wiki", "wiki"

    def map_items(self, items: list[dict]) -> EnterpriseSnapshot:
        assets, relations = [], []
        for item in items:
            payload = dict(item)
            raw_relations = payload.pop("relations", [])
            asset = KnowledgeAsset.model_validate(payload)
            assets.append(asset)
            relations.extend(KnowledgeRelation(
                asset_id=asset.asset_id,
                relation_type=relation["relation_type"],
                relation_id=relation["relation_id"],
            ) for relation in raw_relations)
        return EnterpriseSnapshot(knowledge_assets=assets,
                                  knowledge_relations=relations)


ADAPTER_CLASSES = {
    "cmdb": CmdbAdapter,
    "alarms": AlarmAdapter,
    "monitoring": MonitoringAdapter,
    "work_orders": WorkOrderAdapter,
    "changes": ChangeAdapter,
    "wiki": WikiAdapter,
}
SYNC_ORDER = tuple(ADAPTER_CLASSES)


def build_adapter_registry(settings=None, *, client=None,
                           headers: dict[str, str] | None = None) -> dict:
    if settings is None:
        from qa_core.config.settings import get_settings

        settings = get_settings()
    return {
        name: cls(settings.enterprise_base_url,
                  timeout_seconds=settings.adapter_timeout_seconds,
                  max_retries=settings.adapter_max_retries,
                  client=client, headers=headers)
        for name, cls in ADAPTER_CLASSES.items()
    }
