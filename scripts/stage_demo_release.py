"""Stage local-demo-release-r1: inherit ACTIVE + admitted r2 guide vectors, register
new admissions, rebuild only new files, run the full gate and QA. Never activates."""
import os

os.environ.setdefault('APP_ENV_FILE', '.env.runtime')

import copy
import hashlib
import json
from collections import Counter
from datetime import datetime, timezone

import sqlalchemy as sa
from pymilvus import MilvusClient

from scripts.build_demo_release_package import VERSION, DOC, FAQ, CANDIDATE, RECEIPT
from scripts.migrate_legacy_collections import fingerprint, read_rows
from scripts.prepare_legacy_projection import digest, read, seal, verify_seal, write
from qa_core.retrieval.filters import _to_doc
from qa_core.storage.location_dao import LocationDao

ROOT = CANDIDATE.parents[2]
R2_STAGE = ROOT / '.runtime/test/sop-records-stage-r2/candidate'
REPORT = ROOT / '.runtime/test/reports/demo-release-stage-20261015'
QA_REPORT = REPORT / 'qa-recovery'
DEV_CASES = ROOT / 'data/candidates/sop-record-revision-r2/existing-development-cases.json'
PARENT_VERSION = 'local-content-quality-r1'
PARENT_DOC, PARENT_FAQ = 'local_content_quality_r1_doc', 'local_content_quality_r1_faq'
R2 = dict(version='test-sop-records-r2', milvus='http://127.0.0.1:29530',
          mysql=os.environ.get('ZHIWEI_R2_MYSQL_DSN', 'mysql+pymysql://rag@127.0.0.1:13306/test_sop_records_r2?charset=utf8mb4'),
          doc='test_sop_records_r2_doc', faq='test_sop_records_r2_faq')
TABLES = ['kb_versions', 'kb_version_manifests', 'ingest_records', 'knowledge_assets',
          'knowledge_relations', 'knowledge_source_withdrawals', 'ingest_jobs', 'evidence_locations']

DEVICE_QUESTIONS = {
    'device-bbu-01': 'ALM-BBU-PWR 告警的输入电压读数是多少？',
    'device-clock-01': 'ALM-CLK-HOLD 告警的保持状态持续了多少秒？',
    'device-gps-01': 'ALM-GNSS-LOSS 告警的失锁持续了多长时间？',
    'device-handover-01': 'ALM-NR-HO-PING 告警的切换次数和乒乓次数是多少？',
    'device-interference-01': '演示小区C的底噪从多少dBm升到了多少dBm？',
    'device-spn-01': 'ALM-SPN-OPT-RX 告警的双端收光功率读数是多少？',
    'device-transport-01': 'ALM-TR-LOS 告警的双端LOS情况是什么？',
    'device-vswr-01': 'ALM-VSWR-HIGH 告警的驻波比读数是多少？',
    'public-ngap-causes': 'NGAP的NG Reset和RAN Configuration Update属于哪个过程类别？',
    'public-vswr-rf': '驻波比2:1对应多少dB回波损耗，1.5:1对应多少？',
}


def hashed(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, ensure_ascii=False, default=str).encode()).hexdigest()


def validate():
    verify_seal(CANDIDATE)
    receipt = read(RECEIPT)
    if receipt.get('decision') != 'approved' or receipt.get('scope') != (
            'simulated_device_records_and_public_reference_summaries_isolated_local_staged_new_version_only'):
        raise ValueError('exact demo data receipt required')
    projection = read(CANDIDATE / 'projection.json')
    if projection['version'] != VERSION or projection['files'] != 336 or projection['new_admissions'] != 10 \
            or projection['inherited_guides'] != 20 or projection['publication_authorized']:
        raise ValueError('projection scope changed')
    bodies = read(CANDIDATE / 'approved-bodies.json')['entries']
    if len(bodies) != 10:
        raise ValueError('ten new admissions required')
    meta = read(CANDIDATE / 'raw/meta.json')
    if len(meta) != 336:
        raise ValueError('candidate raw roster changed')
    return receipt, bodies, meta


def settings_for():
    from qa_core.config.settings import get_settings
    get_settings.cache_clear()
    base = get_settings()
    settings = base.model_copy(update=dict(
        doc_collection=DOC, faq_collection=FAQ, data_dir=str(CANDIDATE),
        reports_dir=str(REPORT / 'quality'), generation_enabled=False, agent_enabled=False,
        llm_api_key='', llm_base_url='http://127.0.0.1:1/disabled', semantic_cache_enabled=False,
        redis_url='redis://127.0.0.1:6379/15', embedding_device='cpu', rerank_device='cpu'))
    url = sa.engine.make_url(settings.mysql_dsn)
    if url.host not in {'localhost', '127.0.0.1'} or url.database != 'rag_kb':
        raise ValueError('local main storage required')
    if settings.milvus_uri != 'http://127.0.0.1:19530':
        raise ValueError('local main milvus required')
    # run_rebuild and other helpers read ambient settings; apply explicitly and clear the cache.
    from scripts.build_original_rollback_baseline import apply_settings
    apply_settings(settings)
    return settings


def sql_snapshot(engine):
    with engine.connect() as conn:
        rows = {t: [dict(r) for r in conn.execute(sa.text('SELECT * FROM ' + t)).mappings()] for t in TABLES}
    return json.loads(json.dumps(rows, default=str))


def protect(engine, client):
    before = sql_snapshot(engine)
    if (REPORT / 'protected-before.json').exists():
        assert read(REPORT / 'protected-before.json') == before, 'initialization storage changed; manual inspection required'
    else:
        write(REPORT / 'protected-before.json', before)
    vectors = {}
    for uri, names in (('http://127.0.0.1:19530', [PARENT_DOC, PARENT_FAQ, 'doc_collection', 'faq_collection', 'kb_doc', 'kb_faq']),
                       (R2['milvus'], [R2['doc'], R2['faq']])):
        c = MilvusClient(uri=uri, timeout=60)
        try:
            for n in names:
                if c.has_collection(n):
                    rows = read_rows(c, n)
                    vectors[n] = dict(rows=len(rows), sha256=fingerprint(rows))
                else:
                    vectors[n] = dict(exists=False)
        finally:
            c.close()
    write(REPORT / 'vectors-before.json', vectors)
    return before


def rekey(row):
    new = copy.deepcopy(row)
    new.update(id=hashed([VERSION, row['id']])[:32], kb_version=VERSION)
    return new


def carry_location(old_doc, new_doc, old_location):
    from qa_core.storage.location_dao import LocationDao as LD
    return LD.identity(new_doc), json.dumps(old_location, ensure_ascii=False)


def copy_parent(engine, client, meta):
    rows = [r for n in (PARENT_DOC, PARENT_FAQ) for r in read_rows(client, n)
            if r['kb_version'] == PARENT_VERSION]
    filenames = {m['asset_id']: name for name, m in meta.items()}
    locations = LocationDao(engine)
    projected = []
    for old in rows:
        new = rekey(old)
        location = locations.get(_to_doc(dict(id=old['id'], entity=old)))
        assert location and not location.get('ambiguous'), old['id']
        name = filenames[old['asset_id']]
        assert location['file_sha256'] == digest(CANDIDATE / 'raw' / name), (name, 'location mismatch')
        projected.append((new, location))
    counts = Counter(filenames[r['asset_id']] for r, _ in projected)
    with engine.begin() as conn:
        for new, location in projected:
            identity, payload = carry_location(None, _to_doc(dict(id=new['id'], entity=new)), location)
            conn.execute(sa.text('INSERT INTO evidence_locations(identity_hash,location_json) VALUES(:i,:l) '
                                 'ON DUPLICATE KEY UPDATE location_json=:l'), dict(i=identity, l=payload))
    for collection, faq in ((DOC, False), (FAQ, True)):
        selected = [r for r, _ in projected if (r['doc_type'] == 'faq') == faq]
        for offset in range(0, len(selected), 100):
            client.upsert(collection_name=collection, data=selected[offset:offset + 100])
        client.flush(collection)
    with engine.begin() as conn:
        conn.execute(sa.text('INSERT INTO ingest_records(file_hash,filename,asset_id,doc_type,kb_version,status,chunk_count) '
                             'VALUES(:file_hash,:filename,:asset_id,:doc_type,:kb_version,:status,:chunk_count)'),
                     [dict(file_hash=digest(CANDIDATE / 'raw' / name), filename=name,
                           asset_id=meta[name]['asset_id'], doc_type=meta[name]['doc_type'],
                           kb_version=VERSION, status='done', chunk_count=count) for name, count in counts.items()])
    return len(projected), counts


def copy_r2(engine, client, meta):
    remote = MilvusClient(uri=R2['milvus'], timeout=120)
    remote_engine = sa.create_engine(R2['mysql'], pool_pre_ping=True)
    try:
        rows = [r for n in (R2['doc'], R2['faq']) for r in read_rows(remote, n) if r['kb_version'] == R2['version']]
        assert rows and all(r['doc_type'] == 'guide' for r in rows), 'r2 guide roster changed'
        remote_locations = LocationDao(remote_engine)
        filenames = {m['asset_id']: name for name, m in meta.items() if m['doc_type'] == 'guide'}
        projected = []
        for old in rows:
            new = rekey(old)
            location = remote_locations.get(_to_doc(dict(id=old['id'], entity=old)))
            assert location and not location.get('ambiguous'), old['id']
            name = filenames[old['asset_id']]
            assert location['file_sha256'] == digest(CANDIDATE / 'raw' / name) == digest(R2_STAGE / 'raw' / name), name
            projected.append((new, location))
        counts = Counter(filenames[r['asset_id']] for r, _ in projected)
        with engine.begin() as conn:
            for new, location in projected:
                identity, payload = carry_location(None, _to_doc(dict(id=new['id'], entity=new)), location)
                conn.execute(sa.text('INSERT INTO evidence_locations(identity_hash,location_json) VALUES(:i,:l) '
                                     'ON DUPLICATE KEY UPDATE location_json=:l'), dict(i=identity, l=payload))
        for offset in range(0, len(projected), 100):
            client.upsert(collection_name=DOC, data=[r for r, _ in projected[offset:offset + 100]])
        client.flush(DOC)
        with engine.begin() as conn:
            conn.execute(sa.text('INSERT INTO ingest_records(file_hash,filename,asset_id,doc_type,kb_version,status,chunk_count) '
                                 'VALUES(:file_hash,:filename,:asset_id,:doc_type,:kb_version,:status,:chunk_count)'),
                         [dict(file_hash=digest(CANDIDATE / 'raw' / name), filename=name,
                               asset_id=meta[name]['asset_id'], doc_type='guide',
                               kb_version=VERSION, status='done', chunk_count=count) for name, count in counts.items()])
        return len(projected), counts
    finally:
        remote.close()
        remote_engine.dispose()


def register(engine, receipt):
    from qa_core.domain.models import KnowledgeAsset
    from qa_core.storage.domain_dao import _db_values
    meta = read(CANDIDATE / 'raw/meta.json')
    r2_receipt = read(R2_STAGE / 'approval-receipt.json')
    approved_at = datetime.fromisoformat(receipt['recorded_at_utc'])
    r2_approved_at = datetime.fromisoformat(r2_receipt['recorded_at_utc'])
    with engine.begin() as conn:
        for name, m in meta.items():
            if not (name.startswith('guide_sop_evidence_') or name.startswith('manual_device-')
                    or name.startswith('manual_public-')):
                continue
            stamp = r2_approved_at if m['doc_type'] == 'guide' else approved_at
            assert not conn.execute(sa.text('SELECT 1 FROM knowledge_assets WHERE asset_id=:a OR (tenant_id=:t AND source_system=:s AND source_id=:i)'),
                                    dict(a=m['asset_id'], t=m['tenant_id'], s=m['source_system'], i=m['source_id'])).first()
            model = KnowledgeAsset(asset_id=m['asset_id'], tenant_id=m['tenant_id'], filename=name,
                                   doc_type=m['doc_type'], source_system=m['source_system'],
                                   source_id=m['source_id'], source_version=m['source_version'],
                                   kb_version=VERSION, checksum=digest(CANDIDATE / 'raw' / name),
                                   dept_id=m.get('dept_id') or None, security_level=m['security_level'],
                                   visibility=m['visibility'], role_scope=m.get('role_scope'),
                                   status='active', reviewed_by=None, approved_at=stamp)
            values = _db_values(model)
            values['approved_at'] = values['approved_at'].astimezone(timezone.utc).replace(tzinfo=None)
            conn.execute(sa.text('INSERT INTO knowledge_assets (' + ','.join('`' + k + '`' for k in values) + ') VALUES ('
                                 + ','.join(':' + k for k in values) + ')'), values)
        conn.execute(sa.text('INSERT INTO audit_log(action,actor,payload_json) VALUES(:a,:u,:p)'),
                     dict(a='knowledge_admission', u='workbuddy-demo-release',
                          p=json.dumps(dict(version=VERSION, inherited_guides=20, new_admissions=10,
                                            receipt_sha256=digest(RECEIPT), publication_authorized=False,
                                            outbound_authorized=False), ensure_ascii=False)))


def rebuild():
    from transformers import AutoTokenizer
    from scripts.rebuild_kb import run_rebuild
    from qa_core.indexing.chunkers.supported_rebuild import supported_manual_rebuild
    settings = settings_for()
    tokenizer = AutoTokenizer.from_pretrained(settings.embedding_model_path, local_files_only=True)
    with supported_manual_rebuild(tokenizer):
        stats = run_rebuild(VERSION, raw_dir=CANDIDATE / 'raw', strict_metadata=True,
                            reviewed_source_map=CANDIDATE / 'source-map.json', record_locations=True,
                            tokenizer_chunking=True,
                            progress=lambda stage, done, total, s: print(stage, done, '/', total, flush=True))
    write(REPORT / 'ingestion.json', stats)
    assert stats['failed'] == 0 and stats['duplicates'] == 0 and stats['done'] == 10, stats
    return stats


def build_collections(client):
    """Same schema as the r2 isolated stage: approved quality='local_demo' is 10 chars,
    so the quality varchar is widened to 16 without truncation (frozen sources untouched)."""
    from unittest.mock import patch
    from scripts import init_collections as schema
    original = schema._common_fields

    class Proxy:
        def __init__(self, actual):
            self.actual = actual

        def __getattr__(self, k):
            return getattr(self.actual, k)

        def add_field(self, name, *args, **kwargs):
            if name == 'quality':
                kwargs['max_length'] = 16
            return self.actual.add_field(name, *args, **kwargs)

    with patch.object(schema, '_common_fields', lambda s: original(Proxy(s))):
        for name, faq in ((DOC, False), (FAQ, True)):
            if client.has_collection(name):
                raise ValueError('target collection collision')
            schema.build(client, name, faq)


def execute():
    receipt, bodies, meta = validate()
    REPORT.mkdir(parents=True, exist_ok=True)
    settings = settings_for()
    engine = sa.create_engine(settings.mysql_dsn, pool_pre_ping=True)
    try:
        with engine.connect() as conn:
            active = conn.execute(sa.text("SELECT version FROM kb_versions WHERE status='ACTIVE'")).scalars().all()
            assert active == [PARENT_VERSION], active
            assert not conn.execute(sa.text('SELECT 1 FROM kb_versions WHERE version=:v'), dict(v=VERSION)).first()
        protect(engine, None)
        client = MilvusClient(uri=settings.milvus_uri, timeout=120)
        try:
            assert not client.has_collection(DOC) and not client.has_collection(FAQ)
            build_collections(client)
            copied, parent_counts = copy_parent(engine, client, meta)
            r2_copied, r2_counts = copy_r2(engine, client, meta)
            write(REPORT / 'copy-summary.json', dict(parent_rows=copied, parent_files=len(parent_counts),
                                                     r2_rows=r2_copied, r2_files=len(r2_counts)))
            register(engine, receipt)
        finally:
            client.close()
    finally:
        engine.dispose()
    rebuild()
    verify()


def verify():
    receipt, bodies, meta = validate()
    settings = settings_for()
    engine = sa.create_engine(settings.mysql_dsn)
    client = MilvusClient(uri=settings.milvus_uri, timeout=120)
    remote = MilvusClient(uri=R2['milvus'], timeout=120)
    try:
        rows = [r for n in (DOC, FAQ) for r in read_rows(client, n)]
        assert all(r['kb_version'] == VERSION and len(r['dense']) == 1024 for r in rows)
        types = Counter(r['doc_type'] for r in rows)
        new_manual = [r for r in rows if r['source'].startswith(('manual_device-', 'manual_public-'))]
        assert new_manual, 'new demo manual chunks missing'
        assert types['sop'] == 2200 and types['fault_case'] == 750 and types['faq'] == 31 \
            and types['guide'] == 180 and types['manual'] == 1376 + len(new_manual), dict(types)
        assert all(r['source_version'] in ('demo-r1', '2026-10-15') for r in new_manual), 'new manual roster'
        filenames = {m['asset_id']: name for name, m in meta.items()}
        locations = LocationDao(engine)
        for row in rows:
            location = locations.get(_to_doc(dict(id=row['id'], entity=row)))
            name = filenames[row['asset_id']]
            assert location and not location.get('ambiguous') and location['file_sha256'] == digest(CANDIDATE / 'raw' / name), name
            for k in ('tenant_id', 'source_system', 'source_id', 'source_version', 'asset_id',
                      'visibility', 'security_level', 'allowed_roles', 'role_scope'):
                assert row[k] == meta[name][k], (name, k)
        with engine.connect() as conn:
            assert conn.execute(sa.text("SELECT version FROM kb_versions WHERE status='ACTIVE'")).scalars().all() == [PARENT_VERSION]
            assert conn.execute(sa.text('SELECT status FROM kb_versions WHERE version=:v'), dict(v=VERSION)).scalar() == 'STAGED'
            records = [dict(r) for r in conn.execute(sa.text(
                'SELECT filename,status,chunk_count FROM ingest_records WHERE kb_version=:v'), dict(v=VERSION)).mappings()]
        assert len(records) == 336 and all(r['status'] == 'done' and r['chunk_count'] for r in records), len(records)
        after = sql_snapshot(engine)
        before = read(REPORT / 'protected-before.json')
        extras = {}
        for table, prior in before.items():
            old = {hashed(r) for r in prior}
            current = {hashed(r) for r in after[table]}
            assert old <= current, table
            extras[table] = len(current - old)
        assert extras['knowledge_assets'] == 30 and extras['kb_versions'] == 1 and extras['kb_version_manifests'] >= 1, extras
        for name, expected in read(REPORT / 'vectors-before.json').items():
            c = remote if name.startswith('test_sop_records_r2') else client
            if expected.get('exists') is False:
                assert not c.has_collection(name), name
            else:
                actual = read_rows(c, name)
                assert dict(rows=len(actual), sha256=fingerprint(actual)) == expected, name
        write(REPORT / 'structural-verification.json', dict(passed=True, rows=len(rows), files=336,
                                                            doc_types=dict(types), new_manual_chunks=len(new_manual),
                                                            old_sql_and_vectors_unchanged=True))
    finally:
        client.close()
        remote.close()
        engine.dispose()
    from qa_core.quality.gate import run_gate, save_report
    from qa_core.storage.kb_dao import KbDao
    gate = run_gate(settings, VERSION)
    path = save_report(gate, VERSION, settings.reports_dir)
    dao = KbDao(settings.mysql_dsn)
    try:
        dao.set_quality_report(VERSION, path)
    finally:
        dao.engine.dispose()
    write(REPORT / 'gate.json', dict(passed=gate.passed, report=path, independent_quality_approved=False))
    print('Full gate passed=' + str(gate.passed), flush=True)
    for check in gate.checks:
        print(' ', check['check'], check['passed'], flush=True)
    if not gate.passed:
        return
    verify_qa(settings)


def verify_qa(settings):
    QA_REPORT.mkdir(exist_ok=True)
    from unittest.mock import Mock, patch
    from qa_core.application.qa_usecase import QaUseCase
    from qa_core.application.evidence_service import create_evidence_service
    from qa_core.retrieval.supported_searcher import SupportedFaqSearcher as Searcher
    from qa_core.retrieval.supported_evidence_v2 import relevant_docs
    from qa_core.security.policy import AccessScope
    guard = Mock()
    guard.chat.side_effect = guard.chat_stream.side_effect = AssertionError('paid call forbidden')
    uc = QaUseCase(searcher=Searcher(settings=settings), llm=guard)
    scope = AccessScope.build(roles=['admin'])
    evidence = create_evidence_service(settings)
    import csv
    faq_results = []
    with patch('qa_core.application.qa_usecase.relevant_docs', relevant_docs):
        for name in sorted(n for n in read(CANDIDATE / 'raw/meta.json') if n.startswith('faq_')):
            with (CANDIDATE / 'raw' / name).open(encoding='utf-8', newline='') as f:
                row = next(iter(csv.DictReader(f)))
            result = uc.ask(row['question'], roles=['admin'], scope=scope, kb_version=VERSION,
                            use_llm=False, persist=False)
            assert result['route'] == 'faq_exact' and result['status'] == 'ANSWERED' and len(result['sources']) == 1, name
            original = evidence.source(result['sources'][0]['evidence_id'], scope)
            assert original['text'] == row['answer'], name
            faq_results.append(dict(id=name, status=result['status'], route=result['route']))
        write(QA_REPORT / 'faq-exact.json', faq_results)
        device_results = []
        for source_id, question in DEVICE_QUESTIONS.items():
            result = uc.ask(question, roles=['admin'], scope=scope, kb_version=VERSION,
                            use_llm=False, persist=False)
            source_ids = [s['source_id'] for s in result['sources']]
            device_results.append(dict(id=source_id, question=question, status=result['status'],
                                       route=result['route'], source_ids=source_ids))
            assert result['status'] == 'ANSWERED', (source_id, result['status'])
            assert source_id in source_ids, (source_id, source_ids)
        write(QA_REPORT / 'device-content.json', device_results)
        dev_results = []
        cases = read(DEV_CASES)['cases']
        guide_top = 0
        answered = 0
        for case in cases:
            expected = 'guide_sop_evidence_' + case['id'].split('-')[-1] + '.md'
            result = uc.ask(case['question'], roles=['admin'], scope=scope, kb_version=VERSION,
                            use_llm=False, persist=False)
            returned = [s for s in result['sources']]
            names = [s['source'] for s in returned]
            in_top5 = expected in names
            guide_top += 1 if in_top5 else 0
            if result['status'] == 'ANSWERED' and returned:
                answered += 1
            dev_results.append(dict(id=case['id'], status=result['status'], route=result['route'],
                                    sources=names, expected_guide=expected,
                                    expected_guide_in_top5=in_top5, development_exposed=True))
            # Merged-library regression: an alarm-code question with no matching ticket may
            # legitimately end NO_EVIDENCE (the old ACTIVE behaves the same); the guide
            # content stays reachable through the evidence-search path asserted below.
            assert result['status'] in ('ANSWERED', 'NO_EVIDENCE'), case['id']
        write(QA_REPORT / 'dev-regression.json', dev_results)
        assert answered >= 18, dict(answered=answered, total=len(cases))
        # Direct guide recall: within doc_type=guide the expected guide must be retrievable.
        searcher = Searcher(settings=settings)
        guide_recall = []
        for case in cases:
            expected = 'guide_sop_evidence_' + case['id'].split('-')[-1] + '.md'
            result = searcher.search_docs([case['question']], roles=['admin'], kb_version=VERSION,
                                          doc_types=['guide'], top_k=10, scope=scope)
            names = [d.source for d in result.docs]
            guide_recall.append(dict(id=case['id'], expected_guide=expected, in_top10=expected in names,
                                     returned=names[:10]))
            assert expected in names, (case['id'], names)
        write(QA_REPORT / 'guide-recall.json', guide_recall)
    assert not guard.chat.called and not guard.chat_stream.called
    write(QA_REPORT / 'qa-summary.json', dict(passed=True, faq_exact=len(faq_results),
                                              device_content=len(device_results), dev_regression=len(dev_results),
                                              dev_answered_with_sources=answered,
                                              expected_guide_in_qa_top5=guide_top,
                                              guide_recall_top10=len(guide_recall),
                                              paid_calls=0, persist=False,
                                              independent_quality_approved=False,
                                              note='development-exposed regression only; not independent quality'))
    print('QA passed: faq=%d device=%d dev_answered=%d/%d guide_recall=%d paid=0'
          % (len(faq_results), len(device_results), answered, len(dev_results), len(guide_recall)), flush=True)


if __name__ == '__main__':
    import sys
    import torch
    torch.set_num_threads(4)
    action = sys.argv[1] if len(sys.argv) > 1 else ''
    if action == '--execute':
        execute()
    elif action == '--verify':
        verify()
    elif action == '--qa':
        verify_qa(settings_for())
    else:
        raise SystemExit('select --execute, --verify or --qa')
