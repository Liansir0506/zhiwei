"""Build two new local STAGED versions from traced, admitted vectors; never publish.

Old content becomes a complete-schema recovery baseline. Both versions share a
new collection pair, so the existing manifest-checked rollback can use one runtime.
No original rows, asset registrations, ACTIVE, legacy ledger or receipts are edited.
"""
from collections import Counter
import copy
import hashlib
import json
import os
from pathlib import Path

import numpy as np
import sqlalchemy as sa

from qa_core.config.settings import Settings, get_settings
from qa_core.indexing.chunkers.base import Chunk
from scripts.original_citation_candidate import CANDIDATE, validate_candidate
from scripts.prepare_original_citation_release import (build_profile, validate_original_inputs,
    wait_readable_collections, REPORT as RELEASE_REVIEW)
from scripts.prepare_original_citation_review import ROOT, PACKAGE, runtime_settings, sql_snapshot
from scripts.prepare_legacy_projection import read, write, digest, verify_seal
from scripts.rebuild_kb import to_milvus_row

BASELINE = 'local-rollback-v4-r1'
SUCCESSOR = 'local-release-r1'
VERSIONS = (BASELINE, SUCCESSOR)
COLLECTIONS = ('local_release_r1_doc', 'local_release_r1_faq')
REPORT = ROOT / '.runtime/test/reports/original-rollback-baseline-20261009'
GOVERNED = ('tenant_id', 'dept_id', 'visibility', 'allowed_roles', 'role_scope', 'security_level',
    'device_type', 'source_system', 'source_id', 'source_version', 'effective_from', 'effective_to',
    'status', 'asset_id', 'origin_type', 'synthetic', 'generator_version', 'generation_seed',
    'review_status', 'license_status', 'evidence_refs')


def rekey(version, old_id):
    if version not in VERSIONS or not old_id:
        raise ValueError('exact new rollback pair and source chunk identity required')
    return hashlib.sha256(json.dumps([version, old_id], separators=(',', ':')).encode()).hexdigest()[:32]


def check_vector(vector):
    value = np.asarray(vector, dtype=np.float32)
    if value.shape != (1024,) or not np.isfinite(value).all() or abs(float(np.linalg.norm(value)) - 1) > .001:
        raise ValueError('source vector must be finite normalized 1024D')
    return value.tolist()


def baseline_row(row, trace, meta):
    from scripts.build_legacy_identity_review import content_key
    if (trace.get('status') != 'exact_trace' or trace.get('chunk_id') != row['id']
            or trace.get('content_sha256') != hashlib.sha256(content_key(row).encode()).hexdigest()
            or trace.get('scope_conflicts') or not trace.get('location') or trace['location'].get('ambiguous')):
        raise ValueError('complete original content, scope and source trace required')
    if meta.get('review_status') != 'human_chat_approved' or meta.get('status') != 'active':
        raise ValueError('only already admitted content may enter rollback baseline')
    if any(row.get(key) != meta.get(key) for key in ('tenant_id', 'visibility', 'allowed_roles')):
        raise ValueError('legacy access scope differs from admitted source')
    values = copy.deepcopy(row)
    values.update({key: copy.deepcopy(meta[key]) for key in GOVERNED if key in meta})
    # Stored empty VARCHAR dates are transport values, not valid datetimes for
    # the metadata model. The admitted declaration supplies the lifecycle.
    for key in ('effective_from', 'effective_to'):
        values[key] = meta.get(key) or None
    converted = to_milvus_row(Chunk(row['text'], row['doc_type'], values),
        rekey(BASELINE, row['id']), check_vector(row['dense']), BASELINE)
    # Preserve all original content/structural/technical fields, including FAQ
    # question AND answer and row-level network labels. Governance is explicit.
    for key in row:
        if key not in GOVERNED and key not in {'id', 'kb_version', 'dense'}:
            if key not in converted:
                raise ValueError('unhandled legacy field; no silent dropping')
            converted[key] = copy.deepcopy(row[key])
    return converted


def successor_row(row):
    if row.get('kb_version') != 'local-citation-r1' or not row.get('asset_id'):
        raise ValueError('exact validated original STAGED source required')
    result = copy.deepcopy(row)
    result.update(id=rekey(SUCCESSOR, row['id']), kb_version=SUCCESSOR, dense=check_vector(row['dense']))
    return result


def settings_for(report=REPORT):
    admitted = build_profile()
    settings = admitted.model_copy(update={'doc_collection': COLLECTIONS[0], 'faq_collection': COLLECTIONS[1],
        'reports_dir': str(Path(report).resolve() / 'quality'), 'api_port': 18014,
        'redis_url': 'redis://127.0.0.1:6379/14'})
    url = sa.engine.make_url(settings.mysql_dsn)
    if (url.database != 'rag_kb' or url.port != 3306 or settings.app_env != 'dev'
            or settings.milvus_uri != admitted.milvus_uri or settings.generation_enabled
            or settings.agent_enabled or settings.llm_api_key or not settings.auth_required):
        raise ValueError('exact original local STAGED baseline target required')
    return settings


def apply_settings(settings):
    os.environ.update({name.upper(): str(value).lower() if isinstance(value, bool) else str(value)
        for name, value in settings.model_dump().items()})
    os.environ.update(APP_ENV_FILE=os.devnull, OMP_NUM_THREADS='4', MKL_NUM_THREADS='4',
        HF_HUB_OFFLINE='1', TRANSFORMERS_OFFLINE='1', TOKENIZERS_PARALLELISM='false',
        APP_SCENARIOS_DIR=str(ROOT / 'scenarios'))
    get_settings.cache_clear()
    if get_settings().model_dump() != settings.model_dump():
        raise ValueError('explicit profile changed by inherited environment')


def install_version(settings, version, rows, positions, meta):
    from pymilvus import MilvusClient
    from qa_core.ingest.write_guard import candidate_write
    from qa_core.storage.location_dao import LocationDao
    from qa_core.storage.version_manifest import bind_candidate
    from qa_core.retrieval.filters import _to_doc
    engine = sa.create_engine(settings.mysql_dsn)
    by_asset = Counter(row['asset_id'] for row in rows)
    if len(by_asset) != 276:
        raise ValueError('exact admitted 276-source roster required')
    try:
        with candidate_write(version, settings=settings):
            bind_candidate(settings, version)
            client = MilvusClient(uri=settings.milvus_uri, timeout=120)
            try:
                for kind, collection in zip(('doc', 'faq'), COLLECTIONS):
                    selected = [row for row in rows if (row['doc_type'] == 'faq') == (kind == 'faq')]
                    for offset in range(0, len(selected), 100):
                        client.insert(collection_name=collection, data=selected[offset:offset+100])
                    client.flush(collection)
            finally:
                client.close()
            ledger = [{'file_hash': digest(CANDIDATE / 'raw' / name), 'filename': name,
                'asset_id': item['asset_id'], 'doc_type': item['doc_type'], 'kb_version': version,
                'chunk_count': by_asset[item['asset_id']]} for name, item in sorted(meta.items())]
            locations = [{'id': LocationDao.identity(_to_doc({'id': row['id'], 'entity': row})),
                'location': json.dumps(positions[row['id']], ensure_ascii=False)} for row in rows]
            with engine.begin() as conn:
                conn.execute(sa.text('INSERT INTO ingest_records(file_hash,filename,asset_id,doc_type,kb_version,status,chunk_count) '
                    "VALUES(:file_hash,:filename,:asset_id,:doc_type,:kb_version,'done',:chunk_count)"), ledger)
                conn.execute(sa.text('INSERT INTO evidence_locations(identity_hash,location_json) VALUES(:id,:location)'), locations)
                conn.execute(sa.text('INSERT INTO audit_log(action,actor,payload_json) VALUES(:a,:u,:p)'),
                    {'a': 'rollback_baseline_staged', 'u': 'codex-local-engineering',
                     'p': json.dumps({'version': version, 'user_request': '解决下一步',
                         'scope': 'technical local STAGED rollback baseline from already admitted content',
                         'files': 276, 'chunks': len(rows), 'new_asset_admissions': 0,
                         'source_approval_receipt_sha256': digest(CANDIDATE / 'approval-receipt.json'),
                         'source_content_approval_not_extended': True, 'original_publication_authorized': False,
                         'passage_embeddings_recomputed': False}, ensure_ascii=False)})
    finally:
        engine.dispose()


def execute():
    if REPORT.exists():
        raise ValueError('existing STAGED work must be inspected, never repeated')
    validate_candidate()
    validate_original_inputs()
    verify_seal(RELEASE_REVIEW)
    base, settings = runtime_settings(), settings_for()
    engine = sa.create_engine(base.mysql_dsn)
    from pymilvus import MilvusClient
    from scripts.migrate_legacy_collections import read_rows, fingerprint
    client = MilvusClient(uri=base.milvus_uri, timeout=120)
    try:
        before, _ = sql_snapshot(engine)
        if before != read(RELEASE_REVIEW / 'protected-sql.json'):
            raise ValueError('original SQL changed since actual release preflight')
        if any(version in {row['version'] for row in before['kb_versions']['records']} for version in VERSIONS):
            raise ValueError('rollback version collision; do not adopt or overwrite')
        if any(client.has_collection(name) for name in COLLECTIONS):
            raise ValueError('new collection collision; no overwrite')
        protected_names = [*read(RELEASE_REVIEW / 'protected-vectors.json'), 'local_citation_r1_doc', 'local_citation_r1_faq']
        wait_readable_collections(client, protected_names)
        protected_rows = {name: read_rows(client, name) for name in protected_names}
        protected_vectors = {name: {'rows': len(rows), 'sha256': fingerprint(rows)} for name, rows in protected_rows.items()}
        if {name: protected_vectors[name] for name in read(RELEASE_REVIEW / 'protected-vectors.json')} != read(RELEASE_REVIEW / 'protected-vectors.json'):
            raise ValueError('legacy or pending vectors drifted')
        traces = {row['chunk_id']: row for row in read(PACKAGE / 'row-trace.json')['rows']}
        meta = read(CANDIDATE / 'raw/meta.json')
        original = [row for name in ('doc_collection', 'faq_collection') for row in protected_rows[name]
                    if row['kb_version'] == 'v4-foundation-r3']
        if len(original) != 5300 or {row['id'] for row in original} != set(traces):
            raise ValueError('exact original 5300 ACTIVE lineage required')
        baseline = [baseline_row(row, traces[row['id']], meta[traces[row['id']]['filename']]) for row in original]
        old_positions = {rekey(BASELINE, row['id']): traces[row['id']]['location'] for row in original}
        source = protected_rows['local_citation_r1_doc'] + protected_rows['local_citation_r1_faq']
        if len(source) != 5900 or any(row['kb_version'] != 'local-citation-r1' for row in source):
            raise ValueError('exact completed STAGED 5900 source required')
        from scripts.stage_legacy_candidate import validate_rows
        expected = read(ROOT / '.runtime/test/reports/original-citation-staged-20261009/expected-rows.json')
        validate_rows(source, expected)
        successor = [successor_row(row) for row in source]
        source_positions = read(ROOT / '.runtime/test/reports/original-citation-staged-20261009/expected-locations.json')
        new_positions = {rekey(SUCCESSOR, row['id']): source_positions[row['id']] for row in source}
        positions = {**old_positions, **new_positions}
        with engine.connect() as conn:
            original_locations = {row[0]: row[1] for row in conn.execute(sa.text('SELECT identity_hash,location_json FROM evidence_locations'))}
            old_ledger = [dict(row) for row in conn.execute(sa.text('SELECT * FROM ingest_records ORDER BY id')).mappings()]
            old_manifests = [dict(row) for row in conn.execute(sa.text('SELECT * FROM kb_version_manifests ORDER BY version')).mappings()]
        REPORT.mkdir(parents=True)
        write(REPORT / 'original-sql-before.json', before)
        write(REPORT / 'protected-vectors-before.json', protected_vectors)
        write(REPORT / 'source-map.json', [{'target_version': version, 'source_version': row['kb_version'],
            'source_chunk_id': row['id'], 'new_chunk_id': rekey(version, row['id']),
            'dense_sha256': hashlib.sha256(np.asarray(row['dense'], dtype='<f4').tobytes()).hexdigest()}
            for version, rows in ((BASELINE, original), (SUCCESSOR, source)) for row in rows])
        write(REPORT / 'expected-rows.json', {row['id']: {key: value for key, value in row.items() if key != 'dense'}
                                              for row in baseline + successor})
        write(REPORT / 'expected-locations.json', positions)
        from scripts.init_collections import build
        build(client, COLLECTIONS[0], False)
        build(client, COLLECTIONS[1], True)
    finally:
        client.close()
    apply_settings(settings)
    import torch
    torch.set_num_threads(4)
    results = {}
    for version, rows in ((BASELINE, baseline), (SUCCESSOR, successor)):
        print('Installing new STAGED ' + version + ': ' + str(len(rows)) + ' exact copied vectors', flush=True)
        install_version(settings, version, rows, positions, meta)
        from qa_core.quality.gate import run_gate, save_report
        from qa_core.storage.kb_dao import KbDao
        gate = run_gate(settings, version)
        path = save_report(gate, version, settings.reports_dir)
        dao = KbDao(settings.mysql_dsn)
        try:
            dao.set_quality_report(version, path)
        finally:
            dao.engine.dispose()
        if not gate.passed:
            raise ValueError('real ordinary gate failed; preserve new STAGED diagnostics')
        from qa_core.quality.publication import verify_publication
        verify_publication(settings, version, path)
        results[version] = {'status': 'STAGED', 'chunks': len(rows), 'gate_passed': True, 'checks': gate.checks,
                            'gate_report': path, 'passage_vectors_copied_exactly': True}
    client = MilvusClient(uri=settings.milvus_uri, timeout=120)
    try:
        stored = read_rows(client, COLLECTIONS[0]) + read_rows(client, COLLECTIONS[1])
        wanted = {row['id']: row for row in baseline + successor}
        if len(stored) != 11200 or {row['id'] for row in stored} != set(wanted):
            raise ValueError('new baseline/successor roster differs')
        for row in stored:
            if any(row.get(key) != value for key, value in wanted[row['id']].items() if key != 'dense'):
                raise ValueError('new stored content/governance differs')
            if not np.array_equal(np.asarray(row['dense'], dtype=np.float32), np.asarray(wanted[row['id']]['dense'], dtype=np.float32)):
                raise ValueError('copied vector changed')
        after_vectors = {name: {'rows': len(rows), 'sha256': fingerprint(rows)} for name in protected_names
                         for rows in [read_rows(client, name)]}
        if after_vectors != protected_vectors:
            raise ValueError('protected source/legacy/pending vectors changed')
        write(REPORT / 'new-collection-fingerprints.json', {name: {'rows': len(rows), 'sha256': fingerprint(rows)}
                    for name in COLLECTIONS for rows in [read_rows(client, name)]})
    finally:
        client.close()
    from qa_core.storage.location_dao import LocationDao
    from qa_core.retrieval.filters import _to_doc
    with engine.connect() as conn:
        all_locations = {row[0]: row[1] for row in conn.execute(sa.text('SELECT identity_hash,location_json FROM evidence_locations'))}
        protected_ledger = [dict(row) for row in conn.execute(sa.text(
            'SELECT * FROM ingest_records WHERE kb_version NOT IN (:a,:b) ORDER BY id'), {'a': BASELINE, 'b': SUCCESSOR}).mappings()]
        protected_manifests = [dict(row) for row in conn.execute(sa.text(
            'SELECT * FROM kb_version_manifests WHERE version NOT IN (:a,:b) ORDER BY version'), {'a': BASELINE, 'b': SUCCESSOR}).mappings()]
    if protected_ledger != old_ledger or protected_manifests != old_manifests:
        raise ValueError('legacy/citation ledger or manifest changed')
    if any(all_locations.get(key) != value for key, value in original_locations.items()):
        raise ValueError('old 5900 location rows changed')
    for row in stored:
        value = all_locations.get(LocationDao.identity(_to_doc({'id': row['id'], 'entity': row})))
        if isinstance(value, str): value = json.loads(value)
        if value != positions[row['id']]:
            raise ValueError('new source location differs')
    after, _ = sql_snapshot(engine)
    engine.dispose()
    for table in ('knowledge_assets', 'knowledge_relations', 'ingest_jobs', 'knowledge_source_withdrawals'):
        if before[table] != after[table]:
            raise ValueError('protected registrations/relations/jobs/withdrawals changed')
    if [row for row in after['kb_versions']['records'] if row['version'] not in VERSIONS] != before['kb_versions']['records']:
        raise ValueError('old version state changed')
    if any(row['status'] != 'STAGED' for row in after['kb_versions']['records'] if row['version'] in VERSIONS):
        raise ValueError('original new versions must remain STAGED')
    write(REPORT / 'original-sql-after.json', after)
    write(REPORT / 'protected-vectors-after.json', after_vectors)
    write(REPORT / 'stage-summary.json', {'passed': True, 'versions': results, 'new_chunks': 11200,
        'new_locations': 11200, 'new_ledger_rows': 552, 'new_asset_admissions': 0,
        'original_active': 'v4-foundation-r3', 'original_active_changed': False,
        'passage_embeddings_recomputed': False, 'paid_calls': 0, 'original_publication_authorized': False,
        'bootstrap_requires_separate_activation': True, 'old_legacy_is_still_not_rollback_eligible': True})
    print('Both complete-schema versions verified STAGED; original ACTIVE unchanged', flush=True)


def verify_existing():
    """Read-only completion after a verifier error; never repeat copying/admission."""
    validate_candidate(); validate_original_inputs()
    settings = settings_for()
    expected = read(REPORT / 'expected-rows.json')
    positions = read(REPORT / 'expected-locations.json')
    mapping = read(REPORT / 'source-map.json')
    if len(expected) != 11200 or set(expected) != set(positions) or len(mapping) != 11200:
        raise ValueError('exact durable paired projection required')
    from pymilvus import MilvusClient
    from scripts.migrate_legacy_collections import read_rows, fingerprint
    from scripts.prepare_original_citation_review import canonical, value_sha
    protected_before = read(REPORT / 'protected-vectors-before.json')
    client=MilvusClient(uri=settings.milvus_uri,timeout=120)
    try:
        wait_readable_collections(client,[*protected_before,*COLLECTIONS])
        protected_rows={name:read_rows(client,name) for name in protected_before}
        protected_after={name:{'rows':len(rows),'sha256':fingerprint(rows)} for name,rows in protected_rows.items()}
        if protected_after!=protected_before:raise ValueError('protected vectors changed')
        stored=read_rows(client,COLLECTIONS[0])+read_rows(client,COLLECTIONS[1])
        if len(stored)!=11200 or {row['id'] for row in stored}!=set(expected):raise ValueError('paired roster differs')
        source={row['id']:row for rows in protected_rows.values() for row in rows}
        copied={entry['new_chunk_id']:entry for entry in mapping}
        for row in stored:
            if {k:v for k,v in row.items() if k!='dense'}!=expected[row['id']]:raise ValueError('paired fields differ')
            entry=copied[row['id']]; original=source[entry['source_chunk_id']]
            if rekey(row['kb_version'],original['id'])!=row['id']:raise ValueError('paired lineage ID differs')
            if not np.array_equal(np.asarray(row['dense'],dtype=np.float32),np.asarray(original['dense'],dtype=np.float32)):
                raise ValueError('copied vector differs from original')
            if hashlib.sha256(np.asarray(row['dense'],dtype='<f4').tobytes()).hexdigest()!=entry['dense_sha256']:
                raise ValueError('copied vector digest differs')
        write(REPORT/'new-collection-fingerprints.json',{name:{'rows':len(rows),'sha256':fingerprint(rows)}
            for name in COLLECTIONS for rows in [read_rows(client,name)]})
    finally:client.close()
    engine=sa.create_engine(settings.mysql_dsn)
    before=read(REPORT/'original-sql-before.json')
    def table_digest(rows):
        return value_sha(sorted([json.loads(canonical(row)) for row in rows],key=canonical))
    try:
        from qa_core.storage.location_dao import LocationDao
        from qa_core.retrieval.filters import _to_doc
        with engine.connect() as conn:
            location_rows=[dict(row) for row in conn.execute(sa.text('SELECT * FROM evidence_locations')).mappings()]
            locations={row['identity_hash']:row['location_json'] for row in location_rows}
            new_ids={LocationDao.identity(_to_doc({'id':row['id'],'entity':row})) for row in stored}
            if table_digest([row for row in location_rows if row['identity_hash'] not in new_ids])!=before['evidence_locations']['sha256']:
                raise ValueError('old location rows changed')
            for row in stored:
                value=locations.get(LocationDao.identity(_to_doc({'id':row['id'],'entity':row})))
                if isinstance(value,str):value=json.loads(value)
                if value!=positions[row['id']]:raise ValueError('copied source position differs')
            for table,column in (('ingest_records','kb_version'),('kb_version_manifests','version')):
                old=[dict(row) for row in conn.execute(sa.text('SELECT * FROM '+table+' WHERE '+column+' NOT IN (:a,:b)'),
                    {'a':BASELINE,'b':SUCCESSOR}).mappings()]
                if table_digest(old)!=before[table]['sha256']:raise ValueError('old ledger/manifest changed')
            for version,count in ((BASELINE,5300),(SUCCESSOR,5900)):
                records=conn.execute(sa.text('SELECT asset_id,status,chunk_count FROM ingest_records WHERE kb_version=:v'),{'v':version}).all()
                if len(records)!=276 or len({r[0] for r in records})!=276 or any(r[1]!='done' for r in records) or sum(r[2] for r in records)!=count:
                    raise ValueError('paired complete source ledger required')
        after,_=sql_snapshot(engine)
        for table in ('knowledge_assets','knowledge_relations','ingest_jobs','knowledge_source_withdrawals'):
            if before[table]!=after[table]:raise ValueError('protected domain/jobs/withdrawals changed')
        if [row for row in after['kb_versions']['records'] if row['version'] not in VERSIONS]!=before['kb_versions']['records']:
            raise ValueError('original version state changed')
        new_versions=[row for row in after['kb_versions']['records'] if row['version'] in VERSIONS]
        if len(new_versions)!=2 or any(row['status']!='STAGED' for row in new_versions):raise ValueError('both original pair versions must be STAGED')
    finally:engine.dispose()
    from qa_core.quality.publication import verify_publication
    results={}
    for version,count in ((BASELINE,5300),(SUCCESSOR,5900)):
        path=REPORT/'quality'/('quality_'+version+'.json')
        gate=verify_publication(settings,version,str(path))
        results[version]={'status':'STAGED','chunks':count,'gate_passed':True,'checks':gate['checks'],
                         'gate_report':str(path),'passage_vectors_copied_exactly':True}
    write(REPORT/'original-sql-after.json',after)
    write(REPORT/'protected-vectors-after.json',protected_after)
    write(REPORT/'stage-summary.json',{'passed':True,'versions':results,'new_chunks':11200,'new_locations':11200,
        'new_ledger_rows':552,'new_asset_admissions':0,'original_active':'v4-foundation-r3','original_active_changed':False,
        'passage_embeddings_recomputed':False,'paid_calls':0,'original_publication_authorized':False,
        'bootstrap_requires_separate_activation':True,'old_legacy_is_still_not_rollback_eligible':True,
        'verification_only_resume':True,'incorrect_total_assertion_corrected':True})
    print('All 11200 copied rows/vectors/locations and both STAGED publication gates verified without reimport',flush=True)


if __name__ == '__main__':
    import argparse
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--execute', action='store_true')
    parser.add_argument('--verify-existing',action='store_true')
    args=parser.parse_args()
    if args.execute==args.verify_existing:parser.error('choose --execute for fresh construction or --verify-existing for read-only completion')
    if args.verify_existing:verify_existing()
    else:execute()
