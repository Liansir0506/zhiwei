"""Read-only original-store audit and a pending, content-bound repair proposal.

No ingestion, embeddings, approvals, collection creation, API or publication.
The validation command is offline; --audit connects only to the original stores.
"""
import argparse
from collections import Counter
from datetime import datetime, timezone
import hashlib
import html
import json
from pathlib import Path

from scripts.build_legacy_identity_review import digest, trace_rows, validate_package
from scripts.prepare_legacy_projection import load_plan, read, seal, verify_seal, write

ROOT = Path(__file__).resolve().parents[1]
PACKAGE = ROOT / 'data/candidates/legacy-identity-review-r2'
GROUP_REVIEW = ROOT / 'data/candidates/legacy-group-review-r2'
RECEIPT = ROOT / 'data/candidates/legacy-group-review-r2.receipt.approved.json'
OUTPUT = ROOT / '.runtime/test/reports/original-citation-readiness-20261009-r2'
VERSION = 'v4-foundation-r3'
TARGET = {'app_env': 'dev', 'mysql_host': '127.0.0.1', 'mysql_port': 3306,
          'database': 'rag_kb', 'milvus_uri': 'http://localhost:19530',
          'original_collections': ['doc_collection', 'faq_collection'],
          'candidate_version': 'local-citation-r1',
          'candidate_collections': ['local_citation_r1_doc', 'local_citation_r1_faq']}
REQUIRED = {'id', 'source', 'kb_version', 'source_version', 'asset_id', 'tenant_id',
            'dept_id', 'visibility', 'allowed_roles', 'role_scope', 'security_level',
            'device_type', 'status', 'effective_from', 'effective_to'}
TABLES = ('kb_versions', 'knowledge_assets', 'knowledge_relations', 'ingest_records',
          'kb_version_manifests', 'knowledge_source_withdrawals', 'evidence_locations', 'ingest_jobs')


def canonical(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(',', ':'), default=str)


def value_sha(value):
    return hashlib.sha256(canonical(value).encode()).hexdigest()


def runtime_settings():
    # Supply every field explicitly so inherited shell settings cannot change this target.
    from dotenv import dotenv_values
    from qa_core.config.settings import Settings
    values = {name: field.default for name, field in Settings.model_fields.items()}
    env = {key.lower(): value for key, value in dotenv_values(ROOT / '.env.runtime').items()}
    values.update({key: value for key, value in env.items() if key in values})
    settings = Settings(_env_file=None, **values)
    from sqlalchemy.engine import make_url
    url = make_url(settings.mysql_dsn)
    if (settings.app_env != 'dev' or url.host not in {'localhost', '127.0.0.1'}
            or url.port != 3306 or url.database != 'rag_kb'
            or settings.milvus_uri not in {'http://localhost:19530', 'http://127.0.0.1:19530'}
            or [settings.doc_collection, settings.faq_collection] != TARGET['original_collections']):
        raise ValueError('explicit original read-only target required')
    return settings


def sql_snapshot(engine):
    import sqlalchemy as sa
    available = set(sa.inspect(engine).get_table_names())
    result = {}
    with engine.connect() as conn:
        # Server-enforced read-only transaction; even an accidental DML must fail.
        conn.exec_driver_sql('SET TRANSACTION READ ONLY')
        conn.exec_driver_sql('START TRANSACTION WITH CONSISTENT SNAPSHOT')
        try:
            for table in TABLES:
                if table not in available:
                    result[table] = {'present': False}
                    continue
                rows = [dict(row) for row in conn.execute(sa.text('SELECT * FROM `' + table + '`')).mappings()]
                normalized = sorted((json.loads(canonical(row)) for row in rows), key=canonical)
                result[table] = {'present': True, 'rows': len(rows), 'sha256': value_sha(normalized)}
                if table == 'ingest_jobs':
                    result[table]['statuses'] = dict(Counter(row['status'] for row in rows))
                if table in {'kb_versions', 'knowledge_assets', 'knowledge_relations'}:
                    result[table]['records'] = normalized
            ledger = [dict(row) for row in conn.execute(sa.text(
                'SELECT filename,file_hash,asset_id,doc_type,status,chunk_count FROM ingest_records '
                'WHERE kb_version=:v ORDER BY filename'), {'v': VERSION}).mappings()]
        finally:
            conn.rollback()
    return result, ledger


def permission_audit(assets):
    original = read(PACKAGE / 'original-meta.json')
    rows = []
    keys = ('asset_id', 'dept_id', 'filename', 'role_scope', 'security_level', 'source_id',
            'source_system', 'tenant_id', 'visibility')
    for row in sorted(assets, key=lambda row: row['filename']):
        domain = {key: row[key] for key in keys}
        if isinstance(domain['role_scope'], str):
            domain['role_scope'] = json.loads(domain['role_scope'])
        meta = original[row['filename']]
        differences = {key: {'domain': domain[key], 'raw': meta.get(key)} for key in
                       ('tenant_id', 'dept_id', 'security_level', 'visibility', 'role_scope')
                       if domain[key] != meta.get(key)}
        rows.append({'filename': row['filename'], 'domain': domain, 'differences': differences})
    return {'read_only': True, 'rows': rows}


def audit_original():
    import sqlalchemy as sa
    from pymilvus import MilvusClient
    from scripts.migrate_legacy_collections import fingerprint, read_rows
    settings = runtime_settings()
    inputs = read(PACKAGE / 'inputs.json')
    raw = Path(inputs['raw_root'])
    validate_package(PACKAGE, live_raw=raw)
    group_plan = load_plan(PACKAGE, GROUP_REVIEW)
    metadata = read(raw / 'meta.json')
    manifest_path = Path(settings.data_dir) / 'manifests' / VERSION / 'dataset_manifest.json'
    if digest(manifest_path) != inputs['manifest_sha256']:
        raise ValueError('live manifest changed')
    manifest = read(manifest_path)
    entries = read(PACKAGE / 'source-proposals.json')['entries']
    for entry in entries:
        if digest(raw / entry['filename']) != entry['sha256']:
            raise ValueError('live source bytes changed')
    engine = sa.create_engine(settings.mysql_dsn)
    client = None
    try:
        before, ledger = sql_snapshot(engine)
        versions = sorted(({'version': r['version'], 'status': r['status']} for r in
                           before['kb_versions']['records']), key=lambda r: r['version'])
        if versions != inputs['versions_before']:
            raise ValueError('original version states changed')
        if ledger != [entry['legacy_ledger'] for entry in entries]:
            raise ValueError('ACTIVE ingest ledger changed')
        assets = [r for r in before['knowledge_assets']['records'] if r['kb_version'] == VERSION]
        registered = read(PACKAGE / 'original-registrations.json')
        projected = sorted(({key: row[key] for key in registered[0]} for row in assets), key=lambda r: r['asset_id'])
        if projected != registered:
            raise ValueError('existing reference registrations changed')
        permissions = permission_audit(assets)
        if permissions != read(GROUP_REVIEW / 'domain-permission-audit.json'):
            raise ValueError('domain permission snapshot changed')
        client = MilvusClient(uri=settings.milvus_uri, timeout=120)
        names = sorted(client.list_collections())
        if any(name in names for name in TARGET['candidate_collections']):
            raise ValueError('proposed candidate collection collision')
        if TARGET['candidate_version'] in {row['version'] for row in versions}:
            raise ValueError('proposed candidate version collision')
        active, snapshots, schemas = [], [], []
        for kind, name in zip(('doc', 'faq'), TARGET['original_collections']):
            description = client.describe_collection(collection_name=name)
            fields = {field['name'] for field in description['fields']}
            schemas.append({'collection': name, 'fields': sorted(fields), 'missing_evidence_fields':
                            sorted((REQUIRED | ({'question', 'answer'} if kind == 'faq' else {'text'})) - fields)})
            rows = read_rows(client, name)
            selected = [row for row in rows if row['kb_version'] == VERSION]
            snapshots.append({'collection': name, 'all_version_rows': len(rows), 'active_rows': len(selected),
                              'original_rows_and_vectors_sha256': fingerprint(rows)})
            active.extend((kind, row) for row in selected)
        if snapshots != read(PACKAGE / 'review-summary.json')['original_collections']:
            raise ValueError('original vectors/content changed')
        traces, counts = trace_rows(raw, metadata, active)
        if traces != read(PACKAGE / 'row-trace.json')['rows']:
            raise ValueError('actual ACTIVE content/position trace changed')
        if any(counts[e['filename']] != e['exact_traced_rows'] for e in entries):
            raise ValueError('source chunk count changed')
        parallel = []
        for name in names:
            if name in TARGET['original_collections']:
                continue
            # Inventory only; no load, mutation, alias or activation of quarantined collections.
            description = client.describe_collection(collection_name=name)
            parallel.append({'collection': name, 'fields': sorted(f['name'] for f in description['fields']),
                             'status': 'not_selected_or_approved_by_this_audit'})
        for snap in snapshots:
            if fingerprint(read_rows(client, snap['collection'])) != snap['original_rows_and_vectors_sha256']:
                raise ValueError('original vector store changed during audit')
        after, ledger_after = sql_snapshot(engine)
        if after != before or ledger_after != ledger:
            raise ValueError('protected SQL state changed during audit')
        return {'protocol': 'original-citation-audit-v1', 'read_only': True,
                'audited_at': datetime.now(timezone.utc).isoformat(), 'target': TARGET,
                'source_files': len(entries), 'active_rows': len(active), 'trace_status': dict(Counter(t['status'] for t in traces)),
                'manifest_files': len(manifest['files']), 'schemas': schemas, 'collections': snapshots,
                'parallel_collections': parallel, 'sql_before': before, 'sql_after': after,
                'domain_permissions': permissions, 'department_conflicts': len(group_plan['department_conflicts']),
                'shared_withdrawal_groups': len(group_plan['shared_withdrawal_groups']),
                'protected_state_unchanged': True, 'database_writes': 0, 'vector_writes': 0,
                'model_calls': 0, 'api_started': False, 'active_changed': False}
    finally:
        if client is not None:
            client.close()
        engine.dispose()


def proposal(audit):
    if (audit.get('target') != TARGET or not audit.get('read_only') or not audit.get('protected_state_unchanged')
            or audit.get('source_files') != 276 or audit.get('trace_status') != {'exact_trace': 5300}
            or audit['sql_before'] != audit['sql_after']):
        raise ValueError('verified original read-only audit required')
    validate_package(PACKAGE)
    group = load_plan(PACKAGE, GROUP_REVIEW)
    receipt = read(RECEIPT)
    if (receipt['scope'] != 'isolated_local_demo_only' or receipt['target_environment'] != 'test'
            or receipt.get('approval', {}).get('status') != 'approved'):
        raise ValueError('historical isolated receipt scope changed')
    sources = read(PACKAGE / 'source-proposals.json')['entries']
    approved = set(receipt['approved_filenames'])
    expected = {s['filename'] for s in sources if s['original_scope_metadata'].get('synthetic') is True}
    if approved != expected or len(approved) != 256:
        raise ValueError('historical isolated approval roster changed')
    entries = []
    for source in sources:
        meta = dict(source['original_scope_metadata'])
        if source['existing_registration']:
            # SQL NULL and index empty-string mean no department; retain SQL row unchanged.
            meta['dept_id'] = ''
        meta.update(asset_id=source['proposed_asset_id'], source_id=source['proposed_source_id'])
        entries.append({'filename': source['filename'], 'source_sha256': source['sha256'],
                        'asset_id': meta['asset_id'], 'source_id': meta['source_id'],
                        'source_revision': source['existing_source_version'], 'proposed_metadata': meta,
                        'review_status': 'pending', 'historical_isolated_approval': source['filename'] in approved,
                        'expected_original_rows': source['exact_traced_rows'],
                        'existing_registration_action': 'preserve_unchanged' if source['existing_registration'] else 'insert_new_after_approval'})
    return {'protocol': 'original-citation-proposal-v1', 'status': 'pending', 'target': TARGET,
            'scope': 'original_local_demo_staged_only', 'requested_source_files': 276,
            'requested_reference_cards': 20, 'historical_isolated_sources': 256,
            'expected_original_rows': 5300, 'new_ingest_rows_require_preflight': True,
            'approval_authorizes': ['prepare approved projection', 'new STAGED candidate in rag_kb',
                                   'new complete-schema collections only', 'fresh local BGE embeddings and record locations',
                                   'create evidence_locations if absent; do not backfill legacy locations',
                                   'insert 256 missing asset identities; preserve 20 existing registrations',
                                   'isolated candidate preview and fresh regression/publication gates'],
            'not_authorized': ['original ACTIVE/config/port switch or release', 'old collection overwrite',
                               'reuse old gate reports', 'paid LLM', 'independent quality claims',
                               'BFD/NETCONF or Lab pending sources', 'SOP safety-control content changes'],
            'department_resolution': {'choice': 'use_domain_empty', 'reference_cards': 20,
                                      'index_dept_id': '', 'sql_dept_id': None, 'existing_sql_update': False},
            'shared_withdrawal_groups': group['shared_withdrawal_groups'],
            'reference_facts_and_license': 'original project-written summaries; no external fact/license verification or production approval claimed',
            'execution_order': ['revalidate sealed review and explicit content-bound approval receipt',
                                'revalidate live original target, protected snapshots, missing asset IDs and candidate name collisions',
                                'keep original API/Worker stopped; do not claim or change the four legacy jobs',
                                'offline strict parser/tokenizer preflight; expected new rows derived rather than assumed 5300',
                                'create only missing location table and new candidate version/collections; bind candidate manifest',
                                'insert missing 256 asset identities only; append admission audit, preserve 20 original registrations',
                                'strict reviewed-source-map ingest and local BGE embedding with candidate write guard',
                                'validate complete schema, source revision/checksum/ACL, every content/location and normalized vector',
                                'fresh five gates and own authenticated candidate preview: four source groups, whole SOP and negative boundaries',
                                'compare protected original/shared/isolated stores; stop preview and clear owned keys/profile',
                                'review an actual release/config-switch proposal only after candidate validation passes'],
            'acceptance_limits': ['5300 is current original trace count, not a promised new chunk count',
                                  'candidate preview validates QA citations; original Agent version/asset associations are not automatically migrated',
                                  'existing 20 asset kb_version/reviewed_by/checksum and typed relations remain unchanged',
                                  'no root-cause, external license, independent quality or production acceptance claims'],
            'entries': entries, 'inputs': {
                str(PACKAGE / 'artifact-hashes.json'): digest(PACKAGE / 'artifact-hashes.json'),
                str(GROUP_REVIEW / 'artifact-hashes.json'): digest(GROUP_REVIEW / 'artifact-hashes.json'),
                str(RECEIPT): digest(RECEIPT)}, 'audit_sha256': value_sha(audit)}


def render(plan):
    cards = []
    for entry in plan['entries']:
        if not entry['historical_isolated_approval']:
            body = (PACKAGE / 'raw' / entry['filename']).read_text(encoding='utf-8')
            cards.append('<details><summary>' + html.escape(entry['filename']) + '</summary><pre>' +
                         html.escape(canonical(entry)) + '</pre><pre>' + html.escape(body) + '</pre></details>')
    return ('<!doctype html><html lang="zh-CN"><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">'
            '<title>原库完整引用修复审核</title><style>body{max-width:1100px;margin:24px auto;padding:16px;font:16px/1.6 system-ui}'
            'pre{white-space:pre-wrap;overflow-wrap:anywhere;background:#f4f6f8;padding:16px}details{border:1px solid #ddd;padding:12px;margin:12px 0}'
            'summary{cursor:pointer}a{overflow-wrap:anywhere}</style><h1>原库完整引用修复 · 待批准</h1>'
            '<p>实际只读核对：276文件、5300 ACTIVE片段均有唯一原文位置；旧集合缺完整引用字段。原库与向量未改。</p>'
            '<p>一次审核范围：256份模拟资料增加原库本地演示准入；另审20份自编参考卡的身份、正文和范围。'
            '仅创建新STAGED候选local-citation-r1及两张新集合，不切换当前ACTIVE或8010入口。</p>'
            '<p>参考卡保留asset/source身份；采用原领域空部门（索引空字符串、SQL保持NULL），其他权限保留。'
            '3gpp-23501（01/18）、23502（02/19）、23503（03/20）继续各组共同撤回，不能单独撤回其中一份。</p>'
            '<p>此批准仅支持本地项目自编摘要演示，不代表规范事实、外部许可或生产质量核验。BFD/NETCONF、Lab继续pending；'
            '200旧SOP仍因缺审批/回滚拒绝流程；付费调用0、独立评测0。STAGED验收完成后，原入口切换按具体结果另行审核。</p>'
            '<p>执行会在缺失时建立evidence_locations定位表，仅记录新候选；旧任务不消费、旧20资产登记不更新。'
            '新切分数量以严格预检为准，5300仅为当前旧库计数。此轮验收QA完整引用，原Agent的跨版本资产关联另需设计。</p>'
            '<p><a href="' + html.escape((GROUP_REVIEW / 'review.html').as_uri(), quote=True) + '">查看原256模拟资料正文与声明</a> · '
            '<a href="proposal.json">完整276份身份、权限、哈希与执行范围</a> · <a href="live-audit.json">本次实际只读审计</a></p>'
            '<h2>20份参考卡逐份原文</h2>' + ''.join(cards) + '</html>')


def validate(output):
    output = Path(output).resolve()
    verify_seal(output)
    expected = proposal(read(output / 'live-audit.json'))
    if read(output / 'proposal.json') != expected or (output / 'review.html').read_text(encoding='utf-8') != render(expected):
        raise ValueError('proposal/page differs from verified inputs; do not re-seal changed decisions')
    return {'status': 'pending', 'validated': True, 'sources': 276, 'original_import_authorized': False,
            'original_release_authorized': False, 'proposal_sha256': digest(output / 'proposal.json')}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--audit', action='store_true')
    parser.add_argument('--validate', action='store_true')
    parser.add_argument('--output', type=Path, default=OUTPUT)
    args = parser.parse_args()
    if args.audit == args.validate:
        parser.error('choose --audit or --validate')
    if args.audit:
        output = args.output.resolve()
        if output.exists() or output.is_relative_to(PACKAGE) or PACKAGE.is_relative_to(output):
            raise ValueError('new, separate report directory required')
        audit = audit_original()
        plan = proposal(audit)
        output.mkdir(parents=True)
        write(output / 'live-audit.json', audit)
        write(output / 'proposal.json', plan)
        (output / 'review.html').write_text(render(plan), encoding='utf-8')
        seal(output)
    print(json.dumps(validate(args.output), ensure_ascii=False))


if __name__ == '__main__':
    main()
