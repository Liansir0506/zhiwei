"""Read-only original release review. No publication, API, keys or model calls."""
import argparse
from datetime import datetime, timezone
import html
import os
from pathlib import Path
import socket
import time

import sqlalchemy as sa

from qa_core.config.settings import Settings
from qa_core.ingest.binding import version_binding
from qa_core.quality.publication import _digest, verify_publication
from scripts.original_citation_candidate import CANDIDATE, VERSION, validate_candidate
from scripts.prepare_legacy_projection import digest, read, write, seal, verify_seal
from scripts.prepare_original_citation_review import ROOT, runtime_settings, sql_snapshot
from scripts.stage_original_citation_candidate import REPORT as STAGE

PREVIOUS = ROOT / '.runtime/test/reports/original-citation-stage-verification-20261009'
REPORT = ROOT / '.runtime/test/reports/original-citation-release-readiness-20261009'
OLD_ACTIVE = 'v4-foundation-r3'


def wait_readable_collections(client, names, *, timeout=180, clock=time.monotonic, pause=time.sleep):
    """Bounded Strong read probes; only recovering-channel errors may be retried.

    Container health is insufficient after a cold start. Never retry publication,
    alter load state, reingest, or interpret a permanent/schema error as readiness.
    """
    from pymilvus.exceptions import MilvusException
    deadline, pending, attempts = clock() + timeout, list(dict.fromkeys(names)), {}
    while pending:
        for name in list(pending):
            remaining = deadline - clock()
            if remaining <= 0:
                raise TimeoutError('collection read readiness deadline exceeded: ' + ','.join(pending))
            attempts[name] = attempts.get(name, 0) + 1
            try:
                client.query(collection_name=name, filter='', limit=1, output_fields=['id'],
                             consistency_level='Strong', timeout=min(5, remaining))
            except MilvusException as exc:
                message = str(exc).lower()
                if exc.code != 106 or not any(marker in message for marker in
                        ('collection on recovering', 'do not found any channel in target')):
                    raise
                print('Waiting for recovering collection: ' + name, flush=True)
            else:
                pending.remove(name)
        if pending:
            remaining = deadline - clock()
            if remaining <= 0:
                raise TimeoutError('collection read readiness deadline exceeded: ' + ','.join(pending))
            pause(min(2, remaining))
    return {'passed': True, 'consistency': 'Strong', 'attempts': attempts,
            'deadline_seconds': timeout, 'collection_load_state_changed': False}


def inspect_live_collections(settings, legacy_names, report_path):
    # MilvusClient instances with the same URI share an SDK connection alias.
    # verify_publication owns/closes its alias; never keep another live client
    # across that call and then use the disconnected handle.
    from pymilvus import MilvusClient
    from scripts.migrate_legacy_collections import read_rows, fingerprint
    client = MilvusClient(uri=settings.milvus_uri, timeout=120)
    try:
        readiness = wait_readable_collections(client,
            [settings.doc_collection, settings.faq_collection, *legacy_names])
    finally:
        client.close()
    gate = verify_publication(settings, VERSION, report_path)
    client = MilvusClient(uri=settings.milvus_uri, timeout=120)
    try:
        vectors = {name: {'rows': len(rows), 'sha256': fingerprint(rows)}
            for name in legacy_names for rows in [read_rows(client, name)]}
    finally:
        client.close()
    return readiness, gate, vectors


def build_profile():
    """Explicit host configuration; credentials stay in memory and .env.runtime."""
    values = runtime_settings().model_dump()
    values.update(doc_collection='local_citation_r1_doc', faq_collection='local_citation_r1_faq',
        data_dir=str(CANDIDATE), reports_dir=str(STAGE / 'quality'),
        api_host='127.0.0.1', api_port=8010, redis_url='redis://127.0.0.1:6379/0',
        auth_required=True, rate_limit_qps=100, semantic_cache_enabled=False,
        generation_enabled=False, agent_enabled=False, workflow_evidence_enabled=True,
        llm_api_key='', llm_base_url='http://127.0.0.1:1/disabled',
        embedding_device='cpu', rerank_device='cpu')
    settings = Settings(_env_file=None, **values)
    validate_profile(settings)
    return settings


def validate_profile(settings):
    base = runtime_settings()
    wanted = base.model_dump()
    wanted.update(doc_collection='local_citation_r1_doc', faq_collection='local_citation_r1_faq',
        data_dir=str(CANDIDATE), reports_dir=str(STAGE / 'quality'),
        api_host='127.0.0.1', api_port=8010, redis_url='redis://127.0.0.1:6379/0',
        auth_required=True, rate_limit_qps=100, semantic_cache_enabled=False,
        generation_enabled=False, agent_enabled=False, workflow_evidence_enabled=True,
        llm_api_key='', llm_base_url='http://127.0.0.1:1/disabled',
        embedding_device='cpu', rerank_device='cpu')
    if settings.model_dump() != Settings(_env_file=None, **wanted).model_dump():
        raise ValueError('exact reviewed host citation profile required')


def profile_environment(settings):
    validate_profile(settings)
    env = dict(os.environ)
    for name in Settings.model_fields:
        env.pop(name.upper(), None)
    env.update({name.upper(): str(value).lower() if isinstance(value, bool) else str(value)
                for name, value in settings.model_dump().items()})
    env.update(APP_ENV_FILE=os.devnull, TOKENIZERS_PARALLELISM='false', OMP_NUM_THREADS='4',
               MKL_NUM_THREADS='4', HF_HUB_OFFLINE='1', TRANSFORMERS_OFFLINE='1',
               APP_SCENARIOS_DIR=str(ROOT / 'scenarios'))
    return env


def public_profile(settings):
    result = settings.model_dump()
    result.pop('mysql_dsn')
    result.pop('llm_api_key')
    result['mysql_target'] = {'host': '127.0.0.1', 'port': 3306, 'database': 'rag_kb'}
    return result


def require_states(records, pending_jobs):
    active = [row['version'] for row in records if row['status'] == 'ACTIVE']
    candidate = [row for row in records if row['version'] == VERSION]
    if active != [OLD_ACTIVE] or len(candidate) != 1 or candidate[0]['status'] != 'STAGED':
        raise ValueError('requires old unique ACTIVE and exact unpublished STAGED')
    if pending_jobs:
        raise ValueError('candidate still has unfinished ingest jobs')


def check_binding(settings, manifest):
    if not manifest or manifest['binding'] != version_binding(settings):
        raise ValueError('host collection/model/endpoint binding differs from durable candidate')


def validate_original_inputs():
    from scripts.prepare_original_citation_review import PACKAGE
    inputs = read(PACKAGE / 'inputs.json')
    raw = Path(inputs['raw_root'])
    entries = read(PACKAGE / 'source-proposals.json')['entries']
    if (len(entries) != 276 or any(digest(raw / row['filename']) != row['sha256'] for row in entries)
            or digest(raw / 'meta.json') != inputs['meta_sha256']
            or digest(Path(runtime_settings().data_dir) / 'manifests/v4-foundation-r3/dataset_manifest.json')
                != inputs['manifest_sha256']):
        raise ValueError('original raw source/metadata/manifest changed')


def verify_plan(root):
    root = Path(root).resolve()
    verify_seal(root)
    plan = read(root / 'release-plan.json')
    if (plan['publication_authorized'] or plan['published'] or plan['scope'] != 'original_local_citation_release_only'
            or plan['active_before'] != OLD_ACTIVE or plan['version'] != VERSION
            or plan['proposed_action'] != {'method': 'POST', 'endpoint': '/kb/activate', 'body': {'version': VERSION}}
            or plan['standard_rollback_available'] or plan['failure_action'] != 'stop_owned_entry_and_revoke_owned_key_no_state_rewrite'):
        raise ValueError('reviewed release scope or failure boundary changed')
    if digest(ROOT / '.env.runtime') != plan['runtime_env_sha256']:
        raise ValueError('original runtime input changed')
    settings = build_profile()
    if public_profile(settings) != read(root / 'host-profile.json'):
        raise ValueError('reviewed host profile differs')
    check_binding(settings, {'binding': plan['candidate_binding']})
    for filename, key in [('host-profile.json', 'host_profile_sha256'), ('protected-sql.json', 'protected_sql_sha256'),
                          ('protected-vectors.json', 'protected_vectors_sha256')]:
        if digest(root / filename) != plan[key]:
            raise ValueError('reviewed release artifact changed')
    if digest(Path(plan['gate_report'])) != plan['gate_report_sha256']:
        raise ValueError('reviewed ordinary gate report changed')
    for name, checksum in plan['code_sha256'].items():
        if digest(ROOT / name) != checksum:
            raise ValueError('reviewed implementation changed')
    validate_candidate()
    validate_original_inputs()
    if digest(CANDIDATE / 'artifact-hashes.json') != plan['candidate_manifest_sha256']:
        raise ValueError('candidate changed')
    return plan


def prepare(output=REPORT):
    output = Path(output).resolve()
    if output.exists() or not output.is_relative_to(ROOT / '.runtime/test/reports'):
        raise ValueError('new separate local test report required')
    validate_candidate()
    validate_original_inputs()
    verify_seal(PREVIOUS)
    verify_seal(STAGE)
    for name, checksum in read(PREVIOUS / 'delivery-summary.json')['code_sha256'].items():
        if digest(ROOT / name) != checksum:
            raise ValueError('validated candidate implementation drifted')
    settings = build_profile()
    base = runtime_settings()
    expected = read(PREVIOUS / 'protected-original-after.json')
    engine = sa.create_engine(base.mysql_dsn, pool_pre_ping=True)
    try:
        before, _ = sql_snapshot(engine)
        if before != expected['sql']:
            raise ValueError('protected SQL rows changed since STAGED validation')
        with engine.connect() as conn:
            pending = conn.execute(sa.text('SELECT COUNT(*) FROM ingest_jobs WHERE kb_version=:v '
                "AND status NOT IN ('success','failed','cancelled')"), {'v': VERSION}).scalar_one()
            keys = conn.execute(sa.text('SELECT COUNT(*) FROM api_keys WHERE enabled=1')).scalar_one()
        require_states(before['kb_versions']['records'], pending)
        from qa_core.storage.version_manifest import VersionManifestDao
        manifests = VersionManifestDao(base.mysql_dsn)
        try:
            manifest = manifests.get(VERSION)
            old_manifest = manifests.get(OLD_ACTIVE)
        finally:
            manifests.close()
        check_binding(settings, manifest)
        if old_manifest is not None:
            raise ValueError('old rollback premise changed; prepare a fresh reviewed strategy')
        report_path = str(STAGE / 'quality/quality_local-citation-r1.json')
        candidate = next(row for row in before['kb_versions']['records'] if row['version'] == VERSION)
        if Path(candidate['quality_report_path']).resolve() != Path(report_path).resolve():
            raise ValueError('candidate quality report pointer changed')
        readiness, gate, vectors = inspect_live_collections(settings, expected['vectors'], report_path)
        if vectors != expected['vectors']:
            raise ValueError('protected legacy or pending vectors changed')
        after, _ = sql_snapshot(engine)
        if before != after:
            raise ValueError('SQL changed during read-only release preparation')
    finally:
        engine.dispose()
    for port in (8010, 18011, 18013):
        with socket.socket() as probe:
            probe.settimeout(.5)
            if probe.connect_ex(('127.0.0.1', port)) == 0:
                raise ValueError('entry is occupied; never replace another process')
    output.mkdir(parents=True)
    write(output / 'host-profile.json', public_profile(settings))
    write(output / 'protected-sql.json', before)
    write(output / 'protected-vectors.json', vectors)
    write(output / 'collection-readiness.json', readiness)
    write(output / 'live-publication-check.json', {'passed': True, 'candidate_binding': manifest['binding'],
        'gate_checks': gate['checks'], 'snapshot_sha256': _digest(gate['snapshot']),
        'candidate_pending_jobs': pending, 'existing_enabled_keys': keys,
        'old_manifest_present': False, 'standard_rollback_available': False})
    names = ('scripts/prepare_original_citation_release.py', 'qa_core/api/route_kb.py',
             'qa_core/quality/publication.py', 'qa_core/storage/version_manifest.py',
             'qa_core/ingest/binding.py', 'qa_core/config/settings.py', 'app.py',
             'qa_core/config/scenario.py', 'scenarios/telecom_ops.yaml')
    plan = {'protocol': 'original-citation-release-plan-v1', 'prepared_at_utc': datetime.now(timezone.utc).isoformat(),
        'scope': 'original_local_citation_release_only', 'version': VERSION, 'database': 'rag_kb',
        'active_before': OLD_ACTIVE, 'status_before': 'STAGED', 'files': 276, 'chunks': 5900,
        'candidate_binding': manifest['binding'], 'candidate_manifest_sha256': digest(CANDIDATE / 'artifact-hashes.json'),
        'gate_report': report_path, 'gate_report_sha256': digest(Path(report_path)),
        'snapshot_sha256': _digest(gate['snapshot']), 'runtime_env_sha256': digest(ROOT / '.env.runtime'),
        'host_profile_sha256': digest(output / 'host-profile.json'),
        'protected_sql_sha256': digest(output / 'protected-sql.json'),
        'protected_vectors_sha256': digest(output / 'protected-vectors.json'),
        'code_sha256': {name: digest(ROOT / name) for name in names},
        'publication_authorized': False, 'published': False,
        'proposed_action': {'method': 'POST', 'endpoint': '/kb/activate', 'body': {'version': VERSION}},
        'runtime': {'url': 'http://127.0.0.1:8010', 'implementation': 'owned_host_venv_process',
                    'persistent_after_acceptance': True, 'generation_enabled': False, 'agent_enabled': False,
                    'key': 'new owned local admin key; only private runtime file, never report/console'},
        'standard_rollback_available': False, 'old_manifest_present': False,
        'failure_action': 'stop_owned_entry_and_revoke_owned_key_no_state_rewrite',
        'failure_before_commit': 'old ACTIVE remains; stop owned entry, clean owned key and restore storage lifecycle',
        'failure_after_commit': 'candidate may remain ACTIVE and old version ARCHIVED; stop entry, revoke owned key, '
                                'report actual pointer; do not claim rollback or rewrite statuses',
        'execution_order': ['fresh live state/hash/binding/publication checks and confirm free port',
            'start only original five storage services; leave old Docker API/worker/mock and test services stopped',
            'owned host server with exact profile and new owned key; validate STAGED references',
            'authenticated /kb/activate under existing gate/snapshot/write-lock checks; inspect commit and cache result',
            'verify unique ACTIVE, ops default query and original citations, missing key/foreign/security denials, SOP refusal',
            'verify old vectors/source/ACL/relations/jobs unchanged; retain owned host8010/key/original storage for local use'],
        'limits': ['no paid model or generation', 'no original Agent cross-version relation migration',
                   'no input/config rewrite or ingest/worker', 'no production or independent quality claim',
                   'old ACTIVE is not an API rollback target; entry shutdown is not version rollback']}
    write(output / 'release-plan.json', plan)
    plan_sha = digest(output / 'release-plan.json')
    page = f'''<!doctype html><html lang="zh-CN"><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>原入口完整引用发布审核</title><style>body{{font:16px/1.7 system-ui;max-width:940px;margin:auto;padding:24px;background:#f5f7fb;color:#17243a}}section{{padding:20px;background:white;border:1px solid #ccd4df;border-radius:12px;margin:16px 0}}code,pre{{white-space:pre-wrap;overflow-wrap:anywhere}}.notice{{border-left:5px solid #b77c00}}</style>
<h1>原入口完整引用发布审核</h1><p>只读准备完成 · local-citation-r1仍STAGED · 未发布、未启动8010</p>
<section><h2>拟执行的范围</h2><p>原rag_kb将local-citation-r1激活为唯一ACTIVE，旧v4-foundation-r3改为ARCHIVED。仅本地演示，276文件、5900已验证片段。</p><pre>POST /kb/activate\n{{"version":"local-citation-r1"}}</pre><p>宿主工作树.venv监听127.0.0.1:8010，使用local_citation_r1_doc/faq、http://localhost:19530及原宿主模型路径。Redis使用原6379/0。强鉴权，关闭付费生成与Agent，保留引用和SOP安全拒绝；不改.env.runtime、不启动Docker API/Worker/mock。</p><p>验收通过后保留8010、原五存储和新自有Key供本地使用，Key仅保存在私有运行目录，不打印或进入审核报告；原有Keys不更改。</p></section>
<section class="notice"><h2>明确的回退限制</h2><p>旧ACTIVE没有完整引用schema和持久化发布摘要，现有/kb/rollback无法直接回滚到它。此次方案不增加绕过门禁或手工恢复版本状态的分支。</p><p>提交前失败：旧ACTIVE保持，关闭自有入口/Key并恢复原存储状态。提交后失败：停止8010并撤销新自有Key；新候选可能仍ACTIVE、旧版本ARCHIVED，必须报告实际状态。入口停用不是版本回滚。不能接受这个限制时，保持STAGED，先另行解决合格回滚目标。</p></section>
<section><h2>实际检查与执行验收</h2><p>现有五门禁与新manifest/5900行完整快照已重新在线校验，SQL及四旧集合向量与17.19相同；原276正文/定位/ACL及三组共同撤回规则沿用。执行前再次核对状态、快照、配置与代码哈希；任何漂移拒绝执行。</p><p>执行后检查唯一ACTIVE、普通ops默认检索/原文、无Key/外租户/密级拒绝、SOP安全拒绝、缓存失效和审计、旧向量/登记/关系/任务不变。无需重新入库、嵌入或付费；20原登记不迁移Agent关系，其他pending和独立frozen0不扩大。</p><p><a href="release-plan.json">完整具体计划</a> · <a href="host-profile.json">不含凭据的宿主配置</a> · <a href="live-publication-check.json">实际发布前核验</a></p><p>计划SHA256：<code>{html.escape(plan_sha)}</code></p><p>本页无执行按钮。原聊天批准仅STAGED；此发布、8010启用及提交后停用边界需要对本具体方案另行确认。</p></section></html>'''
    (output / 'review.html').write_text(page, encoding='utf-8')
    write(output / 'summary.json', {'prepared': True, 'plan_sha256': plan_sha, 'published': False,
        'publication_authorized': False, 'status': 'STAGED', 'standard_rollback_available': False,
        'live_publication_check_passed': True, 'protected_original_unchanged': True,
        'api_started': False, 'keys_created': 0, 'model_calls': 0, 'paid_calls': 0,
        'shared_and_isolated_test_accessed': False})
    seal(output)
    verify_plan(output)
    return read(output / 'summary.json')


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--validate-only', action='store_true')
    parser.add_argument('--output', type=Path, default=REPORT)
    args = parser.parse_args()
    import json
    if args.validate_only:
        plan = verify_plan(args.output)
        print(json.dumps({'validated': True, 'publication_authorized': False,
                          'plan_sha256': digest(args.output / 'release-plan.json'), 'version': plan['version']}))
    else:
        print(json.dumps(prepare(args.output)))


if __name__ == '__main__':
    main()
