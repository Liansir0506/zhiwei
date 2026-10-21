"""企业运维领域对象与知识元数据规范。"""
from __future__ import annotations

from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator


class DomainModel(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)


class Tenant(DomainModel):
    tenant_id: str = Field(min_length=1, max_length=64)
    name: str = Field(min_length=1, max_length=128)
    status: Literal["active", "disabled"] = "active"


class Department(DomainModel):
    dept_id: str = Field(min_length=1, max_length=64)
    tenant_id: str = Field(min_length=1, max_length=64)
    name: str = Field(min_length=1, max_length=128)
    parent_dept_id: str | None = Field(default=None, max_length=64)
    status: Literal["active", "disabled"] = "active"


class EnterpriseUser(DomainModel):
    user_id: str = Field(min_length=1, max_length=64)
    tenant_id: str = Field(min_length=1, max_length=64)
    username: str = Field(min_length=1, max_length=128)
    display_name: str | None = Field(default=None, max_length=128)
    status: Literal["active", "disabled"] = "active"


class UserDepartment(DomainModel):
    user_id: str = Field(min_length=1, max_length=64)
    dept_id: str = Field(min_length=1, max_length=64)
    is_primary: bool = False


class Device(DomainModel):
    device_id: str = Field(min_length=1, max_length=64)
    tenant_id: str = Field(min_length=1, max_length=64)
    name: str = Field(min_length=1, max_length=128)
    device_type: str = Field(min_length=1, max_length=64)
    dept_id: str | None = Field(default=None, max_length=64)
    cmdb_id: str | None = Field(default=None, max_length=64)
    vendor: str | None = Field(default=None, max_length=64)
    model: str | None = Field(default=None, max_length=64)
    network_tech: str | None = Field(default=None, max_length=32)
    software_version: str | None = Field(default=None, max_length=64)
    status: Literal["online", "offline", "maintenance", "retired"] = "online"
    attributes_json: dict[str, Any] | None = None


class Alarm(DomainModel):
    alarm_id: str = Field(min_length=1, max_length=64)
    tenant_id: str = Field(min_length=1, max_length=64)
    alarm_code: str = Field(min_length=1, max_length=32)
    source_system: str = Field(min_length=1, max_length=64)
    occurred_at: datetime
    device_id: str | None = Field(default=None, max_length=64)
    severity: Literal["critical", "major", "minor", "warning", "info"] = "major"
    status: Literal["open", "acknowledged", "cleared", "closed"] = "open"
    cleared_at: datetime | None = None
    payload_json: dict[str, Any] | None = None


class MonitoringMetric(DomainModel):
    """实时监控只保留结构化时序值，不写入向量库。"""

    observation_id: str = Field(min_length=1, max_length=64)
    tenant_id: str = Field(min_length=1, max_length=64)
    device_id: str = Field(min_length=1, max_length=64)
    metric_name: str = Field(min_length=1, max_length=128)
    metric_value: float
    sampled_at: datetime
    source_system: str = Field(min_length=1, max_length=64)
    unit: str | None = Field(default=None, max_length=32)
    labels_json: dict[str, Any] | None = None


class WorkOrder(DomainModel):
    ticket_id: str = Field(min_length=1, max_length=64)
    tenant_id: str = Field(min_length=1, max_length=64)
    title: str = Field(min_length=1, max_length=256)
    source_system: str = Field(min_length=1, max_length=64)
    opened_at: datetime
    dept_id: str | None = Field(default=None, max_length=64)
    device_id: str | None = Field(default=None, max_length=64)
    alarm_id: str | None = Field(default=None, max_length=64)
    priority: Literal["P1", "P2", "P3", "P4"] = "P3"
    status: Literal["open", "processing", "resolved", "closed", "cancelled"] = "open"
    assignee_user_id: str | None = Field(default=None, max_length=64)
    closed_at: datetime | None = None


class ChangeRecord(DomainModel):
    change_id: str = Field(min_length=1, max_length=64)
    tenant_id: str = Field(min_length=1, max_length=64)
    title: str = Field(min_length=1, max_length=256)
    change_type: str = Field(min_length=1, max_length=64)
    source_system: str = Field(min_length=1, max_length=64)
    dept_id: str | None = Field(default=None, max_length=64)
    device_id: str | None = Field(default=None, max_length=64)
    ticket_id: str | None = Field(default=None, max_length=64)
    status: Literal["planned", "approved", "executing", "completed", "failed", "cancelled"] = "planned"
    window_start: datetime | None = None
    window_end: datetime | None = None
    operator_user_id: str | None = Field(default=None, max_length=64)

    @model_validator(mode="after")
    def validate_window(self):
        if self.window_start and self.window_end and self.window_end < self.window_start:
            raise ValueError("window_end 不能早于 window_start")
        return self


class KnowledgeAsset(DomainModel):
    asset_id: str = Field(min_length=1, max_length=64)
    tenant_id: str = Field(min_length=1, max_length=64)
    filename: str = Field(min_length=1, max_length=256)
    doc_type: str = Field(min_length=1, max_length=32)
    source_system: str = Field(min_length=1, max_length=64)
    dept_id: str | None = Field(default=None, max_length=64)
    source_id: str | None = Field(default=None, max_length=128)
    source_version: str | None = Field(default=None, max_length=64)
    security_level: Literal["public", "internal", "confidential", "restricted"] = "internal"
    visibility: Literal["public", "role_restricted", "department_restricted"] = "public"
    role_scope: list[str] = Field(default_factory=list)
    status: Literal["draft", "reviewing", "active", "retired"] = "draft"
    effective_from: datetime | None = None
    effective_to: datetime | None = None
    kb_version: str | None = Field(default=None, max_length=32)
    checksum: str | None = Field(default=None, min_length=64, max_length=64)
    reviewed_by: str | None = Field(default=None, max_length=64)
    approved_at: datetime | None = None

    @model_validator(mode="after")
    def validate_effective_period(self):
        if self.effective_from and self.effective_to and self.effective_to < self.effective_from:
            raise ValueError("effective_to 不能早于 effective_from")
        if self.visibility == "role_restricted" and not self.role_scope:
            raise ValueError("role_restricted 知识必须声明 role_scope")
        return self


class KnowledgeRelation(DomainModel):
    asset_id: str = Field(min_length=1, max_length=64)
    relation_type: Literal["device", "alarm", "work_order", "change"]
    relation_id: str = Field(min_length=1, max_length=64)


class EnterpriseSnapshot(DomainModel):
    tenants: list[Tenant] = Field(default_factory=list)
    departments: list[Department] = Field(default_factory=list)
    users: list[EnterpriseUser] = Field(default_factory=list)
    user_departments: list[UserDepartment] = Field(default_factory=list)
    devices: list[Device] = Field(default_factory=list)
    monitoring_metrics: list[MonitoringMetric] = Field(default_factory=list)
    alarms: list[Alarm] = Field(default_factory=list)
    work_orders: list[WorkOrder] = Field(default_factory=list)
    changes: list[ChangeRecord] = Field(default_factory=list)
    knowledge_assets: list[KnowledgeAsset] = Field(default_factory=list)
    knowledge_relations: list[KnowledgeRelation] = Field(default_factory=list)


class KnowledgeMetadata(DomainModel):
    """写入 Milvus 的统一知识元数据；兼容原有 allowed_roles/equipment_type。"""

    tenant_id: str = "default"
    dept_id: str = "public"
    role_scope: list[str] = Field(default_factory=list)
    security_level: Literal["public", "internal", "confidential", "restricted"] = "internal"
    device_type: str = ""
    source_system: str = "manual_upload"
    source_id: str = ""
    source_version: str = ""
    effective_from: datetime | None = None
    effective_to: datetime | None = None
    status: Literal["draft", "reviewing", "active", "retired"] = "active"
    ticket_id: str = ""
    cmdb_id: str = ""
    change_id: str = ""
    asset_id: str = ""

    @model_validator(mode="after")
    def validate_period(self):
        if self.effective_from and self.effective_to and self.effective_to < self.effective_from:
            raise ValueError("effective_to 不能早于 effective_from")
        return self

    def to_milvus(self) -> dict[str, Any]:
        data = self.model_dump()
        data["effective_from"] = self.effective_from.isoformat() if self.effective_from else ""
        data["effective_to"] = self.effective_to.isoformat() if self.effective_to else ""
        return data


def normalize_knowledge_metadata(raw: dict[str, Any]) -> dict[str, Any]:
    """把旧 sidecar/行内字段映射到统一元数据，并返回 Milvus 可写字典。"""
    values = dict(raw)
    values.setdefault("device_type", values.get("equipment_type") or "")
    values.setdefault("role_scope", values.get("allowed_roles") or [])
    values.setdefault("source_id", values.get("ticket_id") or values.get("cmdb_id") or values.get("source") or "")
    model = KnowledgeMetadata.model_validate({
        key: values[key]
        for key in KnowledgeMetadata.model_fields
        if key in values
    })
    return model.to_milvus()
