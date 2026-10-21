"""Guide-only read integrity. This report never grants publication or SOP quality."""
from collections import Counter
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path


def asset_scope_matches(assets, metadata, raw):
    if len(assets)!=20 or len({a['filename'] for a in assets})!=20 or {a['filename'] for a in assets}!=set(metadata):
        return False
    fields=('asset_id','tenant_id','doc_type','source_system','source_id','source_version','security_level','visibility','status')
    for asset in assets:
        m=metadata[asset['filename']]
        if any(asset.get(k)!=m.get(k) for k in fields):return False
        roles=asset.get('role_scope')
        if isinstance(roles,str):
            try:roles=json.loads(roles)
            except ValueError:return False
        if not isinstance(roles,list) or set(roles)!=set(m['role_scope']):return False
        if asset.get('kb_version')!='test-sop-records-r2' or any(asset.get(k) is not None
            for k in ('dept_id','effective_from','effective_to','reviewed_by')):return False
        if asset.get('checksum')!=hashlib.sha256((Path(raw)/asset['filename']).read_bytes()).hexdigest():return False
    return True


def check_snapshot(current, expected, metadata, raw, status):
    checks = []
    def add(name, passed, denominator):
        checks.append(dict(check=name, passed=bool(passed), denominator=denominator))
    files = current.get('files', [])
    counts = Counter(f.get('filename') for f in files)
    add('approved_source_roster', len(metadata) == 20 and set(counts) == set(metadata)
        and all(n == 1 for n in counts.values()), 20)
    add('guide_ledger', len(files) == 20 and all(f.get('status') == 'done'
        and f.get('doc_type') == 'guide' and f.get('chunk_count') == 9
        and f.get('asset_id') == metadata.get(f.get('filename'), {}).get('asset_id') for f in files), 20)
    add('source_bytes', len(files) == 20 and all(
        (Path(raw)/f['filename']).is_file() and hashlib.sha256((Path(raw)/f['filename']).read_bytes()).hexdigest()
        == f.get('file_hash') for f in files if f.get('filename') in metadata) and set(counts) == set(metadata), 20)
    add('source_access_contract', len(metadata) == 20 and all(
        m.get('doc_type') == 'guide' and m.get('tenant_id') == 'default'
        and set(m.get('allowed_roles', [])) == {'ops', 'admin'}
        and set(m.get('role_scope', [])) == {'ops', 'admin'}
        and m.get('visibility') == 'role_restricted' and m.get('security_level') == 'internal'
        and m.get('status') == 'active' for m in metadata.values()), 20)
    collections = current.get('collections', {})
    add('guide_only_vector_counts', collections.get('doc', {}).get('count') == 180
        and collections.get('faq', {}).get('count') == 0, 180)
    add('exact_admitted_snapshot', current == expected, 180)
    add('staged_without_active', status == [('test-sop-records-r2', 'STAGED')], 1)
    return dict(scope='guide_preview_integrity', checked_at_utc=datetime.now(timezone.utc).isoformat(),
        version='test-sop-records-r2', read_preview_ready=all(c['passed'] for c in checks), checks=checks,
        publication_eligible=False, full_sop_approved=False, independent_quality=None, independent_denominator=0,
        generation_enabled=False, agent_enabled=False,
        gaps=['普通发布门禁未通过：没有工单召回样本；SOP与工单去重分母均为0。',
              '指南仅支持取证、条件判断与交接；厂家型号、软件版本、阈值及完整现场处置流程仍缺。',
              '独立人工问题与真值未冻结，分母0，暂无独立质量结果。'])


def verify_live(settings):
    from scripts.build_sop_record_candidate import CANDIDATE, validate_inputs
    from scripts.execute_sop_record_stage import REPORT, RECEIPT, VERSION
    from scripts.sop_record_stage_profile import validate_profile
    from scripts.prepare_legacy_projection import read, verify_seal, digest
    from qa_core.quality.version_workspace import Workspace
    import sqlalchemy as sa
    validate_profile(settings)
    verify_seal(CANDIDATE); verify_seal(REPORT)
    plan, _, metadata, _ = validate_inputs(RECEIPT)
    from scripts.prepare_sop_record_alignment_v2 import OUTPUT,load_verified
    matrix=load_verified();baseline=read(OUTPUT/'preview-baseline.json')
    expected=baseline['target_snapshot']
    workspace = Workspace(settings)
    current = workspace.snapshot(VERSION)
    with workspace.engine() as engine, engine.connect() as conn:
        status = [tuple(r) for r in conn.exec_driver_sql('SELECT version,status FROM kb_versions ORDER BY version')]
        path = conn.execute(sa.text('SELECT quality_report_path FROM kb_versions WHERE version=:v'), {'v':VERSION}).scalar_one()
        assets=[dict(r) for r in conn.exec_driver_sql('SELECT * FROM knowledge_assets').mappings()]
        withdrawals=conn.exec_driver_sql('SELECT COUNT(*) FROM knowledge_source_withdrawals').scalar_one()
    gate_path = Path(path).resolve()
    if not gate_path.is_relative_to(Path(settings.reports_dir).resolve()):
        raise ValueError('ordinary gate path outside isolated reports')
    gate = read(gate_path)
    if (gate_path != (Path(settings.reports_dir)/('quality_'+VERSION+'.json')).resolve()
        or gate.get('passed') is not False or gate.get('snapshot') != expected
        or gate.get('checks') != read(REPORT/'gate-scope.json')['checks']
        or digest(gate_path)!=baseline['ordinary_gate_sha256']):
        raise ValueError('ordinary failed publication report drift')
    result = check_snapshot(current, expected, metadata, CANDIDATE/'raw', status)
    result['checks'].append(dict(check='live_source_identity_and_access',
        passed=asset_scope_matches(assets,metadata,CANDIDATE/'raw') and withdrawals==0, denominator=20))
    result['read_preview_ready']=all(c['passed'] for c in result['checks'])
    result['support_alignment']=matrix
    result['checks'].append(dict(check='revision_record_alignment',passed=matrix['summary']['record_contracts']==109 and matrix['summary']['record_extensions']==49,denominator=109))
    result['read_preview_ready']=all(c['passed'] for c in result['checks'])
    result['source_titles'] = {e['filename']:e['title'] for e in plan['entries']}
    result['ordinary_publication_gate'] = dict(passed=False, checks=gate['checks'], sha256=digest(gate_path))
    if not result['read_preview_ready']:
        raise ValueError('admitted guide snapshot drift; preview refused')
    return result
