"""Shared field projection and row-level permission checks for domain reads."""
import json
from copy import deepcopy
from fastapi import HTTPException
from qa_core.domain.models import Alarm, Device, MonitoringMetric, WorkOrder, ChangeRecord, KnowledgeAsset

MODELS = {'device': Device, 'alarm': Alarm, 'alarms': Alarm, 'monitoring': MonitoringMetric,
          'work_orders': WorkOrder, 'changes': ChangeRecord, 'knowledge': KnowledgeAsset}
PRIVATE_FIELDS = {'payload_json', 'attributes_json', 'labels_json', 'assignee_user_id', 'operator_user_id', 'reviewed_by'}

def _roles(value):
    try:
        value = json.loads(value) if isinstance(value, str) else value
        return set(value) if isinstance(value, list) and all(isinstance(x, str) for x in value) else set()
    except (ValueError, TypeError):
        return set()

def department_allowed(dept, scope):
    return bool(dept) and (not scope.dept_ids or dept == 'public' or dept in scope.dept_ids)

def project(kind, row):
    return {k: v for k, v in row.items() if k in MODELS[kind].model_fields and k not in PRIVATE_FIELDS}

def filter_context(result, ctx):
    result = deepcopy(result)
    scope = ctx.scope
    # Domain models have no row-level classification field: treat snapshots as internal.
    if 'internal' not in scope.security_levels:
        raise HTTPException(403, '业务快照需要内部资料访问权限')
    if result.get('tenant_id') != scope.tenant_id:
        raise HTTPException(404, '实体不存在或不可访问')
    device = result.get('device')
    if device:
        if (device.get('tenant_id') != scope.tenant_id
                or (scope.device_types and device.get('device_type') not in scope.device_types)
                or (scope.dept_ids and not department_allowed(device.get('dept_id'), scope))):
            raise HTTPException(403, '设备不在授权范围')
    elif scope.device_types or scope.dept_ids:
        raise HTTPException(403, '设备归属无法核验')
    alarm = result.get('alarm')
    if alarm and (alarm.get('tenant_id') != scope.tenant_id or
                  (alarm.get('device_id') and (not device or alarm['device_id'] != device['device_id']))):
        raise HTTPException(403, '告警归属无法核验')
    device_id = device.get('device_id') if device else None
    alarm_ids = {row['alarm_id'] for row in result.get('alarms', [])
                 if row.get('tenant_id') == scope.tenant_id and device_id and row.get('device_id') == device_id}
    if alarm:
        alarm_ids.add(alarm['alarm_id'])
    tickets = [r for r in result.get('work_orders', []) if r.get('tenant_id') == scope.tenant_id
               and device_id and r.get('device_id') == device_id and department_allowed(r.get('dept_id'), scope)
               and (not r.get('alarm_id') or r['alarm_id'] in alarm_ids)]
    ticket_ids = {r['ticket_id'] for r in tickets}
    output = {'tenant_id': scope.tenant_id, 'device': project('device', device) if device else None}
    if alarm:
        output['alarm'] = project('alarm', alarm)
    for kind in ('alarms', 'monitoring', 'changes'):
        if kind not in result:
            continue
        rows = []
        for row in result[kind]:
            if row.get('tenant_id') != scope.tenant_id or not device_id or row.get('device_id') != device_id:
                continue
            if kind == 'changes' and (not department_allowed(row.get('dept_id'), scope)
                    or (row.get('ticket_id') and row['ticket_id'] not in ticket_ids)):
                continue
            rows.append(project(kind, row))
        output[kind] = rows[:20]
    output['work_orders'] = [project('work_orders', r) for r in tickets[:20]]
    permitted = []
    for row in result.get('knowledge', []):
        if row.get('tenant_id') != scope.tenant_id or row.get('security_level') not in scope.security_levels:
            continue
        if row.get('status') != 'active' or (scope.dept_ids and not department_allowed(row.get('dept_id'), scope)):
            continue
        visibility = row.get('visibility')
        if (visibility == 'public' or
                (visibility == 'role_restricted' and _roles(row.get('role_scope')) & set(scope.roles)) or
                (visibility == 'department_restricted' and row.get('dept_id') in scope.dept_ids)):
            permitted.append(project('knowledge', row))
    output['knowledge'] = permitted[:20]
    output['truncated'] = any(len(output.get(k, [])) == 20 for k in ('alarms', 'monitoring', 'work_orders', 'changes', 'knowledge'))
    return output

class DomainContextService:
    def __init__(self, dao):
        self.dao = dao

    def read(self, kind, entity_id, ctx, *, include_relations=False):
        if kind not in {'alarm', 'device'}:
            raise ValueError('unknown domain tool')
        raw = (self.dao.get_alarm_context(entity_id, ctx.tenant_id) if kind == 'alarm'
               else self.dao.get_device_context(entity_id, ctx.tenant_id))
        if raw is None:
            return None
        raw['knowledge'] = []
        filtered = filter_context(raw, ctx)
        entities = []
        for key, field, relation in [('device', 'device_id', 'device'), ('alarm', 'alarm_id', 'alarm')]:
            if filtered.get(key):
                entities.append((relation, filtered[key][field]))
        for key, field, relation in [('alarms', 'alarm_id', 'alarm'), ('work_orders', 'ticket_id', 'work_order'), ('changes', 'change_id', 'change')]:
            entities.extend((relation, row[field]) for row in filtered.get(key, []))
        filtered['knowledge'] = self.dao.knowledge_for_entities(ctx.tenant_id, entities)
        output = filter_context(filtered, ctx)
        if include_relations:
            edges = self.dao.knowledge_edges_for_entities(ctx.tenant_id, entities)
            allowed = {(row['asset_id'], row.get('source_version')) for row in output['knowledge']}
            output['_relations'] = [edge for edge in edges if (edge['asset_id'], edge['source_version']) in allowed]
            output['_relations_truncated'] = len(edges) > 1620
        return output
