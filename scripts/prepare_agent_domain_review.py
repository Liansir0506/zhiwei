"""Prepare a pending, content-bound domain review; no DB/network/model calls."""
from __future__ import annotations

import argparse
import csv
import hashlib
import html
import json
import re
import sys
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from qa_core.domain.models import EnterpriseSnapshot

PROTOCOL = 'agent-domain-review-v1'
PRIMARY = {'device': 'SIM-DEV-0001', 'alarm': 'SIM-ALM-000001', 'ticket': 'SIM-TT-00001',
           'change': 'SIM-CHG-00001', 'metric': 'SIM-OBS-000001'}
KEYS = {'tenants': 'tenant_id', 'departments': 'dept_id', 'users': 'user_id',
        'devices': 'device_id', 'monitoring_metrics': 'observation_id', 'alarms': 'alarm_id',
        'work_orders': 'ticket_id', 'changes': 'change_id', 'knowledge_assets': 'asset_id'}


def encoded(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(',', ':'), allow_nan=False).encode('utf-8')


def digest(value):
    return hashlib.sha256(encoded(value)).hexdigest()


def file_hash(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def load(path):
    return json.loads(Path(path).read_text(encoding='utf-8-sig'))


def audit_snapshot(raw, *, external_tenants=(), external_assets=()):
    """Check ownership/typed closure, not just whether foreign-key IDs exist."""
    snapshot = EnterpriseSnapshot.model_validate_json(encoded(raw), strict=True)
    problems = []
    def problem(code, table, identity):
        problems.append({'code': code, 'table': table, 'id': identity})
    indexes = {}
    for table, key in KEYS.items():
        rows = raw.get(table, [])
        counts = Counter(row[key] for row in rows)
        for identity, count in counts.items():
            if count != 1:
                problem('duplicate_id', table, identity)
        indexes[table] = {row[key]: row for row in rows}
    tenant_ids = set(indexes['tenants']) | set(external_tenants)
    assets = {**{row['asset_id']: row for row in external_assets}, **indexes['knowledge_assets']}
    for table, rows in raw.items():
        for row in rows:
            identity = row.get(KEYS.get(table, ''), row.get('asset_id', row.get('user_id', '?')))
            tenant = row.get('tenant_id')
            if tenant and tenant not in tenant_ids:
                problem('missing_tenant', table, identity)
            for field in ('occurred_at', 'cleared_at', 'sampled_at', 'opened_at', 'closed_at', 'window_start', 'window_end', 'effective_from', 'effective_to'):
                if row.get(field):
                    timestamp = datetime.fromisoformat(row[field].replace('Z', '+00:00'))
                    if timestamp.utcoffset() is None:
                        problem('timezone_missing', table, identity)
            if tenant and row.get('dept_id'):
                dept = indexes['departments'].get(row['dept_id'])
                if not dept or dept['tenant_id'] != tenant:
                    problem('department_ownership', table, identity)
            if tenant and row.get('device_id') and table != 'devices':
                device = indexes['devices'].get(row['device_id'])
                if not device or device['tenant_id'] != tenant:
                    problem('device_ownership', table, identity)
            if row.get('metric_value') is not None and not isinstance(row['metric_value'], (int, float)):
                problem('metric_type', table, identity)
            if row.get('closed_at') and row.get('opened_at') and datetime.fromisoformat(row['closed_at']) < datetime.fromisoformat(row['opened_at']):
                problem('reversed_ticket_time', table, identity)
            if row.get('cleared_at') and row.get('occurred_at') and datetime.fromisoformat(row['cleared_at']) < datetime.fromisoformat(row['occurred_at']):
                problem('reversed_alarm_time', table, identity)
    for row in raw.get('departments', []):
        if row.get('parent_dept_id'):
            parent = indexes['departments'].get(row['parent_dept_id'])
            if not parent or parent['tenant_id'] != row['tenant_id']:
                problem('department_parent', 'departments', row['dept_id'])
    for row in raw.get('user_departments', []):
        user, dept = indexes['users'].get(row['user_id']), indexes['departments'].get(row['dept_id'])
        if not user or not dept or user['tenant_id'] != dept['tenant_id']:
            problem('user_department_ownership', 'user_departments', row['user_id'])
    for identity, count in Counter((r['user_id'], r['dept_id']) for r in raw.get('user_departments', [])).items():
        if count != 1:
            problem('duplicate_membership', 'user_departments', '|'.join(identity))
    for row in raw.get('work_orders', []):
        alarm = indexes['alarms'].get(row.get('alarm_id'))
        if row.get('alarm_id') and (not alarm or alarm['tenant_id'] != row['tenant_id'] or alarm.get('device_id') != row.get('device_id')):
            problem('ticket_alarm_device_mismatch', 'work_orders', row['ticket_id'])
        if row.get('assignee_user_id'):
            user = indexes['users'].get(row['assignee_user_id'])
            if not user or user['tenant_id'] != row['tenant_id']:
                problem('ticket_user_ownership', 'work_orders', row['ticket_id'])
    for row in raw.get('changes', []):
        ticket = indexes['work_orders'].get(row.get('ticket_id'))
        if row.get('ticket_id') and (not ticket or ticket['tenant_id'] != row['tenant_id'] or ticket.get('device_id') != row.get('device_id')):
            problem('change_ticket_device_mismatch', 'changes', row['change_id'])
        if row.get('operator_user_id'):
            user = indexes['users'].get(row['operator_user_id'])
            if not user or user['tenant_id'] != row['tenant_id']:
                problem('change_user_ownership', 'changes', row['change_id'])
    relation_keys = Counter((r['asset_id'], r['relation_type'], r['relation_id']) for r in raw.get('knowledge_relations', []))
    for identity, count in relation_keys.items():
        if count != 1:
            problem('duplicate_relation', 'knowledge_relations', '|'.join(identity))
    relation_tables = {'device': 'devices', 'alarm': 'alarms', 'work_order': 'work_orders', 'change': 'changes'}
    for row in raw.get('knowledge_relations', []):
        asset = assets.get(row['asset_id'])
        entity = indexes[relation_tables[row['relation_type']]].get(row['relation_id'])
        if not asset or not entity or asset['tenant_id'] != entity['tenant_id']:
            problem('typed_knowledge_relation', 'knowledge_relations', row['relation_id'])
    return {'schema_valid': True, 'relation_and_time_valid': not problems,
            'counts': {name: len(getattr(snapshot, name)) for name in EnterpriseSnapshot.model_fields},
            'issues': problems, 'issue_counts': dict(Counter(item['code'] for item in problems))}


def select(raw, table, identity):
    rows = [row for row in raw[table] if row[KEYS[table]] == identity]
    if len(rows) != 1:
        raise ValueError('candidate row is missing or duplicated')
    return rows[0]


def prepare(source, projection):
    source, projection = Path(source).resolve(), Path(projection).resolve()
    raw = load(source)
    source_audit = audit_snapshot(raw)
    file = 'fault_case_simulated_v4_01.csv'
    source_map_path, metadata_path = projection / 'source-map.json', projection / 'raw/meta.json'
    source_entries = load(source_map_path)['entries']
    matches = [item for item in source_entries if item['filename'] == file]
    if len(matches) != 1 or matches[0]['approval']['status'] != 'approved':
        raise ValueError('requires an explicitly approved knowledge source')
    approved = matches[0]
    csv_path = projection / 'raw' / file
    if file_hash(csv_path) != approved['sha256']:
        raise ValueError('approved knowledge bytes changed')
    meta = load(metadata_path)[file]
    if (meta['review_status'] != 'human_chat_approved' or meta['tenant_id'] != 'default'
            or meta['synthetic'] is not True or meta['source_id'] != approved['source_id']
            or meta['source_system'] != approved['source_system'] or meta['doc_type'] != 'fault_case'):
        raise ValueError('approved knowledge metadata disagrees with the source map')
    with csv_path.open(encoding='utf-8-sig', newline='') as handle:
        records = list(csv.DictReader(handle))
    witness = [(index, row) for index, row in enumerate(records, 1) if row['ticket_id'] == PRIMARY['ticket']]
    if len(witness) != 1:
        raise ValueError('historical case identity is ambiguous')
    record_number, record = witness[0]
    primary_device = select(raw, 'devices', PRIMARY['device'])
    primary_alarm = select(raw, 'alarms', PRIMARY['alarm'])
    primary_ticket = select(raw, 'work_orders', PRIMARY['ticket'])
    if (record['source'] != PRIMARY['ticket'] or record['alarm_code'] != primary_alarm['alarm_code']
            or record['equipment_type'] != primary_device['device_type'] or record['title'] != primary_ticket['title']
            or not re.search(r'(?<![A-Za-z0-9_-])' + re.escape(PRIMARY['device']) + r'(?![A-Za-z0-9_-])', record['phenomenon'])):
        raise ValueError('case record does not corroborate the selected entity')
    candidate = {name: [] for name in EnterpriseSnapshot.model_fields}
    for table, ids in {'devices': [PRIMARY['device'], 'SIM-DEV-0002'],
                      'alarms': [PRIMARY['alarm'], 'SIM-ALM-000002'],
                      'monitoring_metrics': [PRIMARY['metric'], 'SIM-OBS-000002'],
                      'work_orders': [PRIMARY['ticket']], 'changes': [PRIMARY['change']]}.items():
        candidate[table] = [select(raw, table, identity) for identity in ids]
    departments = {row['dept_id'] for row in candidate['devices']}
    candidate['departments'] = [select(raw, 'departments', identity) for identity in sorted(departments)]
    users = {primary_ticket['assignee_user_id'], candidate['changes'][0]['operator_user_id']}
    candidate['users'] = [select(raw, 'users', identity) for identity in sorted(users)]
    candidate['user_departments'] = [row for row in raw['user_departments'] if row['user_id'] in users and row['dept_id'] in departments]
    if len(candidate['user_departments']) != len(users):
        raise ValueError('selected user memberships are incomplete')
    asset = {'asset_id': meta['asset_id'], 'tenant_id': meta['tenant_id'], 'filename': file,
             'source_system': meta['source_system'], 'source_id': meta['source_id'], 'source_version': meta['source_version'],
             'kb_version': 'test-legacy-demo-r1', 'dept_id': meta['dept_id'], 'security_level': meta['security_level'],
             'visibility': meta['visibility'], 'role_scope': meta['role_scope'], 'status': meta['status'],
             'checksum': approved['sha256'], 'doc_type': meta['doc_type']}
    candidate['knowledge_relations'] = [{'asset_id': asset['asset_id'], 'relation_type': kind, 'relation_id': identity}
        for kind, identity in [('device', PRIMARY['device']), ('alarm', PRIMARY['alarm']), ('work_order', PRIMARY['ticket']), ('change', PRIMARY['change'])]]
    audit = audit_snapshot(candidate, external_tenants=['default'], external_assets=[asset])
    if not audit['relation_and_time_valid']:
        raise ValueError('selected candidate has inconsistent ownership or timestamps')
    if any(row.get('tenant_id', 'default') != 'default' for rows in candidate.values() for row in rows):
        raise ValueError('candidate expands tenant scope')
    provenance = []
    for table, rows in candidate.items():
        if table == 'knowledge_relations':
            continue
        for row in rows:
            originals = [r for r in raw[table] if r == row]
            if len(originals) != 1:
                raise ValueError('candidate row was silently changed')
            provenance.append({'table': table, 'id': row.get(KEYS.get(table, ''), row.get('user_id')),
                               'source_row_sha256': digest(row), 'transformation': 'none'})
    binding = {'source_snapshot': {'path': str(source), 'sha256': file_hash(source)},
               'approved_projection': {'path': str(projection), 'source_map_sha256': file_hash(source_map_path),
                    'metadata_sha256': file_hash(metadata_path), 'receipt_sha256': file_hash(projection / 'approval-receipt.json')},
               'knowledge': asset, 'knowledge_file_sha256': file_hash(csv_path),
               'csv_record': {'number': record_number, 'ticket_id': PRIMARY['ticket'], 'sha256': digest(record)},
               'snapshot_sha256': digest(candidate), 'source_rows': provenance}
    envelope = {'protocol': PROTOCOL, 'review_status': 'pending', 'scope': 'isolated_local_demo_only',
        'target': {'database': 'test_legacy_stage_fb36d947ab67', 'version': 'test-legacy-demo-r1',
                   'expected_before': 'no candidate IDs, domain tables empty, existing tenant and approved asset must match'},
        'synthetic': True, 'as_of': '2026-10-09T00:00:00+00:00', 'business_timezone': 'UTC',
        'record_count': sum(len(rows) for rows in candidate.values()), 'binding': binding,
        'snapshot': candidate, 'prerequisites': {'tenants': ['default'], 'knowledge_assets': [asset]},
        'required_decision': 'Approve these exact 12 historical synthetic rows and 4 proposed typed relations for isolated local demonstration only.',
        'limitations': ['源数据为2026-09-19及此前的模拟快照，不代表实时现场。',
            '部门分配沿用生成器，AMF所属ran-ops不是实际企业归属声明。',
            '4条知识关系为本次待审核提案；case CSV整份资产含50条历史工单，不能将全部正文当作本实体观测。',
            '原快照20份参考知识资产及原20关系全部排除；没有新增知识原文或知识准入。',
            '未执行导入、同步、发布、嵌入、LLM请求、企业动作或根因质量评测。']}
    return envelope, source_audit, audit


def render_review(envelope):
    title = '只读诊断领域候选 · 待审核'
    rows = ''.join(f'<tr><td>{html.escape(table)}</td><td>{len(items)}</td></tr>' for table, items in envelope['snapshot'].items() if items)
    details = ''.join(f'<details><summary>{html.escape(table)} · {len(items)} 条</summary><pre>{html.escape(json.dumps(items,ensure_ascii=False,indent=2))}</pre></details>'
                      for table, items in envelope['snapshot'].items() if items)
    notes = ''.join(f'<li>{html.escape(note)}</li>' for note in envelope['limitations'])
    return f'<!doctype html><html lang="zh-CN"><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>{title}</title><style>body{{font:15px/1.7 system-ui;max-width:960px;margin:auto;padding:24px;color:#18304d}}table{{border-collapse:collapse;width:100%}}td{{border:1px solid #dde4ef;padding:8px}}pre{{white-space:pre-wrap;overflow-wrap:anywhere;background:#f3f6fb;padding:14px}}details{{margin:14px 0;border:1px solid #dde4ef;padding:12px;border-radius:8px}}summary{{cursor:pointer}}.pending{{padding:14px;background:#fff2d9}}</style><h1>{title}</h1><p class="pending">pending · {envelope["record_count"]}条 · 仅供隔离本地演示。此页不批准、导入或执行任何业务操作。</p><p>目标库：{envelope["target"]["database"]}；知识版本：{envelope["target"]["version"]}。12条旧模拟领域/身份记录原样提取，另提议4条知识关联；既有租户和知识资产只做前提检查，不写入。</p><ul>{notes}</ul><h2>候选范围</h2><table>{rows}</table>{details}<h2>输入与内容绑定</h2><pre>{html.escape(json.dumps(envelope["binding"],ensure_ascii=False,indent=2))}</pre><p>接受准入需另有明确确认与内容绑定凭据；本页保留待审状态。</p></html>'


def validate_package(directory, source, projection):
    directory = Path(directory)
    hashes = load(directory / 'artifact-hashes.json')
    if set(hashes) != {'candidate.json', 'source-audit.json', 'candidate-audit.json', 'review.html', 'acceptance-plan.json'}:
        raise ValueError('unexpected artifact list')
    for name, expected in hashes.items():
        if file_hash(directory / name) != expected:
            raise ValueError('review artifact changed')
    envelope, source_audit, candidate_audit = prepare(source, projection)
    if (load(directory / 'candidate.json') != envelope or load(directory / 'source-audit.json') != source_audit
            or load(directory / 'candidate-audit.json') != candidate_audit or load(directory / 'acceptance-plan.json') != acceptance_plan()
            or (directory / 'review.html').read_text(encoding='utf-8') != render_review(envelope)):
        raise ValueError('candidate inputs or derivation changed')
    if envelope['review_status'] != 'pending':
        raise ValueError('preparation never records an approval')
    return envelope


def acceptance_plan():
    return {'status': 'planned_not_executed', 'usage': 'dev/regression', 'independent_frozen': 0,
        'preconditions': ['exact candidate approval receipt', 'only approved isolated DB and actual frozen knowledge binding',
            'transactional insert-only conflict detection, no upsert of existing tenants/assets',
            'UTC naive storage conversion explicit, business timestamps preserved', 'existing 256 knowledge assets and all vectors/ACL/ACTIVE unchanged'],
        'cases': [
            {'id': 'A1', 'query': 'ALM-AMF-REG 常见原因', 'alarm_id': PRIMARY['alarm'], 'expected': 'ANSWERED/domain with projected snapshot plus real content-bound sources; no asserted root cause', 'max_calls': 8},
            {'id': 'A2', 'query': 'ALM-AMF-REG 常见原因', 'device_id': PRIMARY['device'], 'expected': 'ANSWERED/domain, real authenticated source rereads'},
            {'id': 'A3', 'alarm_id': PRIMARY['alarm'], 'device_id': 'SIM-DEV-0002', 'expected': 'NEEDS_CLARIFICATION/entity_mismatch, no partial data'},
            {'id': 'A4', 'device_id': 'SIM-DEV-0002', 'scope': {'dept_ids': ['ran-ops'], 'device_types': ['AMF'], 'security_levels': ['internal']}, 'expected': 'NO_EVIDENCE/entity_unavailable'},
            {'id': 'A5', 'alarm_id': PRIMARY['alarm'], 'scope': {'tenant_id': 'foreign'}, 'expected': 'NO_EVIDENCE/entity_unavailable'},
            {'id': 'A6', 'alarm_id': PRIMARY['alarm'], 'scope': {'security_levels': ['public']}, 'expected': 'NO_EVIDENCE/entity_unavailable'},
            {'id': 'A7', 'query': '现场注册失败诊断', 'expected': 'NEEDS_CLARIFICATION/missing_entity'},
            {'id': 'A8', 'query': 'ALM-AMF-REG SOP', 'alarm_id': PRIMARY['alarm'], 'expected': 'NO_EVIDENCE/missing_safety_controls, no partial observations/sources'},
            {'id': 'A9', 'query': '重启出现ALM-AMF-REG的设备', 'alarm_id': PRIMARY['alarm'], 'expected': 'OUT_OF_SCOPE, zero tools'},
            {'id': 'A10', 'change': 'revoke only owned temporary key during run', 'expected': 'late result suppressed and no partial evidence'},
            {'id': 'A11', 'browser': [1280, 390], 'expected': 'synthetic historical snapshots, unknown device/change observation time, provenance separate from excerpts, 3 fresh source reads'},
            {'id': 'A12', 'after': 'close owned API and delete temporary test keys; proposed domain demo rows persist only in approved isolated DB', 'expected': 'no original/shared storage mutation; domain before/after explicit; all knowledge fingerprints equal'}],
        'deferred': ['isolated fault-fixture tests for orphan records, list bounds, outage and lifecycle corruption; never mutate approved ACTIVE/manifest/withdrawal for a negative probe'],
        'current_agent_limitations': ['search is query-based; typed relation returns an associated asset but does not pin retrieval to its specific CSV record',
            'real retrieval need not cite SIM-TT-00001; citations may be other approved AMF historical cases',
            'new domain rows are outside knowledge publication snapshot; separate candidate receipt and before/after domain fingerprint required'],
        'execution': {'paid_llm_calls': 0, 'candidate_passage_embeddings': 0, 'business_actions': False, 'domain_import': 'not_authorized_yet'}}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--source', required=True, type=Path)
    parser.add_argument('--projection', required=True, type=Path)
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument('--output', type=Path)
    group.add_argument('--validate-package', type=Path)
    args = parser.parse_args()
    if args.validate_package:
        envelope = validate_package(args.validate_package, args.source, args.projection)
    else:
        output = args.output.resolve()
        if output.exists() or output == args.source.resolve() or output.is_relative_to(args.projection.resolve()) or args.projection.resolve().is_relative_to(output):
            raise ValueError('refuse existing or overlapping output')
        envelope, source_audit, candidate_audit = prepare(args.source, args.projection)
        output.mkdir(parents=True)
        values = {'candidate.json': envelope, 'source-audit.json': source_audit, 'candidate-audit.json': candidate_audit, 'acceptance-plan.json': acceptance_plan()}
        for name, value in values.items():
            (output / name).write_text(json.dumps(value,ensure_ascii=False,indent=2,allow_nan=False),encoding='utf-8')
        (output / 'review.html').write_text(render_review(envelope),encoding='utf-8')
        hashes = {name:file_hash(output / name) for name in [*values, 'review.html']}
        (output / 'artifact-hashes.json').write_text(json.dumps(hashes,indent=2),encoding='utf-8')
        validate_package(output, args.source, args.projection)
    print(json.dumps({'validated': True, 'written_to_database': False, 'review_status': envelope['review_status'],
                      'record_count': envelope['record_count'], 'candidate_sha256': digest(envelope)},ensure_ascii=False))


if __name__ == '__main__':
    main()
