"""企业领域模型 DAO：幂等快照写入与统一运维上下文查询。"""
from __future__ import annotations

import json
from typing import Any

import sqlalchemy as sa

from qa_core.domain.models import EnterpriseSnapshot


TABLE_KEYS = {
    "tenants": ("tenant_id",),
    "departments": ("dept_id",),
    "users": ("user_id",),
    "user_departments": ("user_id", "dept_id"),
    "devices": ("device_id",),
    "monitoring_metrics": ("observation_id",),
    "alarms": ("alarm_id",),
    "work_orders": ("ticket_id",),
    "change_records": ("change_id",),
    "knowledge_assets": ("asset_id",),
    "knowledge_relations": ("asset_id", "relation_type", "relation_id"),
}


def _db_values(model) -> dict[str, Any]:
    values = model.model_dump()
    for key, value in list(values.items()):
        if isinstance(value, (dict, list)):
            values[key] = json.dumps(value, ensure_ascii=False)
    return values


class DomainDao:
    def __init__(self, dsn: str):
        from qa_core.storage.read_engine import read_engine
        self.engine = read_engine(dsn)

    @staticmethod
    def _upsert(conn, table: str, values: dict[str, Any]) -> None:
        if table not in TABLE_KEYS:
            raise ValueError(f"不允许写入未知领域表: {table}")
        columns = list(values)
        keys = set(TABLE_KEYS[table])
        quoted = ", ".join(f"`{c}`" for c in columns)
        params = ", ".join(f":{c}" for c in columns)
        updates = [c for c in columns if c not in keys]
        update_sql = ", ".join(f"`{c}`=VALUES(`{c}`)" for c in updates)
        sql = f"INSERT INTO `{table}` ({quoted}) VALUES ({params})"
        if update_sql:
            sql += f" ON DUPLICATE KEY UPDATE {update_sql}"
        else:
            # 纯关联表的所有列均属于复合主键，没有可更新字段；重复同步应保持幂等。
            sql = f"INSERT IGNORE INTO `{table}` ({quoted}) VALUES ({params})"
        conn.execute(sa.text(sql), values)

    def upsert_snapshot(self, snapshot: EnterpriseSnapshot) -> dict[str, int]:
        """按依赖顺序写入来自 CMDB/告警/工单/变更系统的统一快照。"""
        batches = [
            ("tenants", snapshot.tenants),
            ("departments", snapshot.departments),
            ("users", snapshot.users),
            ("user_departments", snapshot.user_departments),
            ("devices", snapshot.devices),
            ("monitoring_metrics", snapshot.monitoring_metrics),
            ("alarms", snapshot.alarms),
            ("work_orders", snapshot.work_orders),
            ("change_records", snapshot.changes),
            ("knowledge_assets", snapshot.knowledge_assets),
            ("knowledge_relations", snapshot.knowledge_relations),
        ]
        counts: dict[str, int] = {}
        with self.engine.begin() as conn:
            for table, models in batches:
                for model in models:
                    self._upsert(conn, table, _db_values(model))
                counts[table] = len(models)
        return counts

    @staticmethod
    def _one(conn, sql: str, params: dict) -> dict | None:
        row = conn.execute(sa.text(sql), params).mappings().first()
        return dict(row) if row else None

    @staticmethod
    def _all(conn, sql: str, params: dict) -> list[dict]:
        return [dict(r) for r in conn.execute(sa.text(sql), params).mappings().all()]

    def get_alarm_context(self, alarm_id: str, tenant_id: str) -> dict | None:
        with self.engine.begin() as conn:
            alarm = self._one(conn,
                "SELECT * FROM alarms WHERE alarm_id=:id AND tenant_id=:tenant",
                {"id": alarm_id, "tenant": tenant_id})
            if alarm is None:
                return None
            device = None
            if alarm.get("device_id"):
                device = self._one(conn,
                    "SELECT * FROM devices WHERE device_id=:id AND tenant_id=:tenant",
                    {"id": alarm["device_id"], "tenant": tenant_id})
            monitoring = self._all(conn,
                "SELECT * FROM monitoring_metrics WHERE device_id=:device "
                "AND tenant_id=:tenant ORDER BY sampled_at DESC, observation_id LIMIT 21",
                {"device": alarm.get("device_id") or "", "tenant": tenant_id})
            work_orders = self._all(conn,
                "SELECT * FROM work_orders WHERE alarm_id=:id AND tenant_id=:tenant "
                "ORDER BY opened_at DESC, ticket_id LIMIT 21", {"id": alarm_id, "tenant": tenant_id})
            changes = self._all(conn,
                "SELECT c.* FROM change_records c WHERE c.tenant_id=:tenant AND "
                "(c.device_id=:device OR c.ticket_id IN "
                "(SELECT w.ticket_id FROM work_orders w WHERE w.alarm_id=:alarm AND w.tenant_id=:tenant)) "
                "ORDER BY c.created_at DESC, c.change_id LIMIT 21",
                {"tenant": tenant_id, "device": alarm.get("device_id") or "", "alarm": alarm_id})
            relation_ids = [alarm_id]
            relation_ids += [w["ticket_id"] for w in work_orders]
            relation_ids += [c["change_id"] for c in changes]
            if alarm.get("device_id"):
                relation_ids.append(alarm["device_id"])
            knowledge = self._knowledge_for(conn, tenant_id, relation_ids)
        return {"tenant_id": tenant_id, "alarm": alarm, "device": device,
                "monitoring": monitoring,
                "work_orders": work_orders, "changes": changes, "knowledge": knowledge}

    def get_device_context(self, device_id: str, tenant_id: str) -> dict | None:
        with self.engine.begin() as conn:
            device = self._one(conn,
                "SELECT * FROM devices WHERE device_id=:id AND tenant_id=:tenant",
                {"id": device_id, "tenant": tenant_id})
            if device is None:
                return None
            alarms = self._all(conn,
                "SELECT * FROM alarms WHERE device_id=:id AND tenant_id=:tenant "
                "ORDER BY occurred_at DESC, alarm_id LIMIT 21", {"id": device_id, "tenant": tenant_id})
            monitoring = self._all(conn,
                "SELECT * FROM monitoring_metrics WHERE device_id=:id AND tenant_id=:tenant "
                "ORDER BY sampled_at DESC, observation_id LIMIT 21", {"id": device_id, "tenant": tenant_id})
            work_orders = self._all(conn,
                "SELECT * FROM work_orders WHERE device_id=:id AND tenant_id=:tenant "
                "ORDER BY opened_at DESC, ticket_id LIMIT 21", {"id": device_id, "tenant": tenant_id})
            changes = self._all(conn,
                "SELECT * FROM change_records WHERE device_id=:id AND tenant_id=:tenant "
                "ORDER BY created_at DESC, change_id LIMIT 21", {"id": device_id, "tenant": tenant_id})
            relation_ids = [device_id]
            relation_ids += [a["alarm_id"] for a in alarms]
            relation_ids += [w["ticket_id"] for w in work_orders]
            relation_ids += [c["change_id"] for c in changes]
            knowledge = self._knowledge_for(conn, tenant_id, relation_ids)
        return {"tenant_id": tenant_id, "device": device, "monitoring": monitoring,
                "alarms": alarms,
                "work_orders": work_orders, "changes": changes, "knowledge": knowledge}

    def suggest(self, kind: str, query: str, tenant_id: str, limit: int = 8) -> list[dict]:
        """领域实体联想：仅返回列表所需字段，按租户隔离，LIKE 通配符已转义。"""
        if kind not in {'alarm', 'device'}:
            raise ValueError('unknown domain suggestion kind')
        text = (query or '').strip()
        if len(text) < 2 or len(text) > 64:
            return []
        escaped = text.replace('\\', '\\\\').replace('%', '\\%').replace('_', '\\_')
        pattern = '%' + escaped + '%'
        if kind == 'alarm':
            sql = ("SELECT alarm_id, alarm_code, severity, status FROM alarms "
                   "WHERE tenant_id=:tenant AND (alarm_id LIKE :pattern ESCAPE '\\\\' "
                   "OR alarm_code LIKE :pattern ESCAPE '\\\\') "
                   "ORDER BY occurred_at DESC, alarm_id LIMIT :limit")
        else:
            sql = ("SELECT device_id, name, device_type FROM devices "
                   "WHERE tenant_id=:tenant AND (device_id LIKE :pattern ESCAPE '\\\\' "
                   "OR name LIKE :pattern ESCAPE '\\\\') "
                   "ORDER BY device_id LIMIT :limit")
        with self.engine.begin() as conn:
            return self._all(conn, sql, {'tenant': tenant_id, 'pattern': pattern, 'limit': int(limit)})

    @staticmethod
    def _knowledge_for(conn, tenant_id: str, relation_ids: list[str]) -> list[dict]:
        if not relation_ids:
            return []
        statement = sa.text(
            "SELECT DISTINCT a.* FROM knowledge_assets a "
            "JOIN knowledge_relations r ON r.asset_id=a.asset_id "
            "WHERE a.tenant_id=:tenant AND r.relation_id IN :ids "
            "AND a.status='active' "
            "AND (a.effective_from IS NULL OR a.effective_from <= NOW()) "
            "AND (a.effective_to IS NULL OR a.effective_to > NOW()) "
            "ORDER BY a.updated_at DESC, a.asset_id LIMIT 21"
        ).bindparams(sa.bindparam("ids", expanding=True))
        return [dict(r) for r in conn.execute(
            statement, {"tenant": tenant_id, "ids": sorted(set(relation_ids))}
        ).mappings().all()]

    def knowledge_for_entities(self, tenant_id, entities):
        """Follow only typed relations of already authorized entities."""
        if not entities:
            return []
        clauses, params = [], {'tenant': tenant_id}
        for i, (kind, identity) in enumerate(sorted(set(entities))[:81]):
            if kind not in {'device', 'alarm', 'work_order', 'change'}:
                raise ValueError('unknown relation type')
            clauses.append(f'(r.relation_type=:kind{i} AND r.relation_id=:id{i})')
            params.update({f'kind{i}': kind, f'id{i}': identity})
        statement = sa.text(
            'SELECT DISTINCT a.* FROM knowledge_assets a JOIN knowledge_relations r ON r.asset_id=a.asset_id '
            "WHERE a.tenant_id=:tenant AND a.status='active' "
            'AND (a.effective_from IS NULL OR a.effective_from <= CURRENT_TIMESTAMP) '
            'AND (a.effective_to IS NULL OR a.effective_to > CURRENT_TIMESTAMP) AND ('
            + ' OR '.join(clauses) + ') ORDER BY a.updated_at DESC, a.asset_id LIMIT 21')
        with self.engine.connect() as conn:
            return [dict(r) for r in conn.execute(statement, params).mappings()]

    def knowledge_edges_for_entities(self, tenant_id, entities):
        """Internal bounded provenance; callers must intersect with authorized assets."""
        entities = sorted(set(entities))
        if len(entities) > 81:
            raise ValueError('authorized entity scope exceeds bound')
        if not entities:
            return []
        clauses, params = [], {'tenant': tenant_id}
        for i, (kind, identity) in enumerate(entities):
            if kind not in {'device', 'alarm', 'work_order', 'change'}:
                raise ValueError('unknown relation type')
            clauses.append(f'(r.relation_type=:kind{i} AND r.relation_id=:id{i})')
            params.update({f'kind{i}': kind, f'id{i}': identity})
        query = sa.text('SELECT r.asset_id,r.relation_type,r.relation_id,a.source_version '
            'FROM knowledge_relations r JOIN knowledge_assets a ON a.asset_id=r.asset_id '
            "WHERE a.tenant_id=:tenant AND a.status='active' "
            'AND (a.effective_from IS NULL OR a.effective_from<=CURRENT_TIMESTAMP) '
            'AND (a.effective_to IS NULL OR a.effective_to>CURRENT_TIMESTAMP) AND ('
            + ' OR '.join(clauses) + ') ORDER BY r.asset_id,r.relation_type,r.relation_id LIMIT 1621')
        with self.engine.connect() as conn:
            return [dict(row) for row in conn.execute(query, params).mappings()]
