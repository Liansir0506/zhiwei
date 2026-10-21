"""Full 11200-row publication/rollback rehearsal in a disposable test namespace."""
import copy
import json
from pathlib import Path
import re
import secrets
import subprocess
import uuid

import numpy as np
import sqlalchemy as sa
from dotenv import dotenv_values

from qa_core.config.settings import Settings
from scripts.build_original_rollback_baseline import BASELINE, SUCCESSOR, REPORT as STAGE, VERSIONS, COLLECTIONS, apply_settings
from scripts.original_citation_candidate import CANDIDATE
from scripts.prepare_original_citation_release import wait_readable_collections
from scripts.prepare_original_citation_review import ROOT, runtime_settings, sql_snapshot
from scripts.prepare_legacy_projection import read, write, digest

REPORT = ROOT / '.runtime/test/reports/original-rollback-verification-20261009'


def root_sql(database, statement):
    if not re.fullmatch(r'test_rollback_pair_[a-f0-9]{12}', database):
        raise ValueError('owned disposable test database required')
    run = subprocess.run(['docker', 'exec', '-i', 'telecom-ops-rag-test-mysql-1', 'sh', '-c',
        'MYSQL_PWD="$MYSQL_ROOT_PASSWORD" exec mysql -uroot'], input=statement, text=True, capture_output=True)
    if run.returncode:
        raise RuntimeError('owned disposable SQL lifecycle failed')


def main():
    from pymilvus import MilvusClient
    from scripts.migrate_legacy_collections import read_rows, fingerprint
    from scripts.migrate_domain_model import split_sql_statements
    from scripts.init_collections import build
    from qa_core.ingest.write_guard import candidate_write
    from qa_core.storage.version_manifest import bind_candidate
    from qa_core.storage.location_dao import LocationDao
    from qa_core.retrieval.filters import _to_doc
    from qa_core.quality.gate import run_gate, save_report
    from qa_core.storage.kb_dao import KbDao
    if not read(STAGE / 'stage-summary.json')['passed']:
        raise ValueError('completed real original STAGED baseline pair required')
    original = runtime_settings()
    original_engine = sa.create_engine(original.mysql_dsn)
    original_before, _ = sql_snapshot(original_engine)
    client = MilvusClient(uri=original.milvus_uri, timeout=120)
    try:
        rows = read_rows(client, COLLECTIONS[0]) + read_rows(client, COLLECTIONS[1])
        if len(rows) != 11200:
            raise ValueError('exact real baseline and successor required')
        source_fingerprint = fingerprint(sorted(rows, key=lambda row: row['id']))
    finally:
        client.close()
    token = uuid.uuid4().hex[:12]
    database = 'test_rollback_pair_' + token
    values = {name: field.default for name, field in Settings.model_fields.items()}
    values.update({key.lower(): value for key,value in dotenv_values(ROOT / '.env.test').items()
                   if key.lower() in values and value is not None})
    base = Settings(_env_file=None, **values)
    url = sa.engine.make_url(base.mysql_dsn)
    if url.database != 'rag_kb_test' or url.port != 13306 or base.milvus_uri != 'http://127.0.0.1:29530':
        raise ValueError('verified test storage profile required')
    settings = base.model_copy(update={'mysql_dsn': url.set(database=database).render_as_string(hide_password=False),
        'doc_collection': database + '_doc', 'faq_collection': database + '_faq',
        'reports_dir': str(REPORT / 'sandbox-quality'), 'redis_url': 'redis://127.0.0.1:16379/14',
        'auth_required': True, 'rate_limit_qps': 100, 'generation_enabled': False, 'agent_enabled': False,
        'semantic_cache_enabled': False, 'workflow_evidence_enabled': True, 'llm_api_key': '',
        'llm_base_url': 'http://127.0.0.1:1/disabled', 'embedding_device': 'cpu', 'rerank_device': 'cpu'})
    created, engine, result = False, None, None
    positions = read(STAGE / 'expected-locations.json')
    metadata = read(CANDIDATE / 'raw/meta.json')
    try:
        root_sql(database, f"CREATE DATABASE `{database}` CHARACTER SET utf8mb4; GRANT ALL ON `{database}`.* TO 'rag'@'%';")
        created = True
        engine = sa.create_engine(settings.mysql_dsn)
        with engine.begin() as conn:
            for path in sorted((ROOT / 'docker/mysql/init').glob('*.sql')):
                for statement in split_sql_statements(path.read_text(encoding='utf-8')):
                    conn.exec_driver_sql(statement)
        LocationDao(engine).ensure_schema()
        apply_settings(settings)
        import torch
        torch.set_num_threads(4)
        client = MilvusClient(uri=settings.milvus_uri, timeout=120)
        try:
            if any(client.has_collection(name) for name in (settings.doc_collection, settings.faq_collection)):
                raise ValueError('random fixture collection collision')
            build(client, settings.doc_collection, False)
            build(client, settings.faq_collection, True)
        finally:
            client.close()
        gates = {}
        for version in VERSIONS:
            selected = [row for row in rows if row['kb_version'] == version]
            with candidate_write(version, settings=settings):
                bind_candidate(settings, version)
                client = MilvusClient(uri=settings.milvus_uri, timeout=120)
                try:
                    for kind, name in zip(('doc', 'faq'), (settings.doc_collection, settings.faq_collection)):
                        chunks = [row for row in selected if (row['doc_type'] == 'faq') == (kind == 'faq')]
                        for offset in range(0, len(chunks), 100):
                            client.insert(collection_name=name, data=chunks[offset:offset+100])
                        client.flush(name)
                finally:
                    client.close()
                counts = {}
                for row in selected: counts[row['asset_id']] = counts.get(row['asset_id'], 0) + 1
                with engine.begin() as conn:
                    conn.execute(sa.text('INSERT INTO ingest_records(file_hash,filename,asset_id,doc_type,kb_version,status,chunk_count) '
                        "VALUES(:hash,:name,:asset,:type,:version,'done',:count)"),
                        [{'hash': digest(CANDIDATE/'raw'/name), 'name':name, 'asset':m['asset_id'], 'type':m['doc_type'],
                          'version':version, 'count':counts[m['asset_id']]} for name,m in metadata.items()])
                    conn.execute(sa.text('INSERT INTO evidence_locations(identity_hash,location_json) VALUES(:id,:p)'),
                        [{'id':LocationDao.identity(_to_doc({'id':r['id'],'entity':r})),
                          'p':json.dumps(positions[r['id']],ensure_ascii=False)} for r in selected])
            gate = run_gate(settings, version)
            if not gate.passed: raise ValueError('full real-data fixture gate failed')
            path = save_report(gate, version, settings.reports_dir)
            dao = KbDao(settings.mysql_dsn)
            try: dao.set_quality_report(version,path)
            finally: dao.engine.dispose()
            gates[version] = {'passed': True, 'checks': gate.checks}
        from qa_core.api.auth import create_api_key
        from qa_core.api.route_evidence import get_evidence_service
        from qa_core.schemas.evidence import evidence_of
        from qa_core.retrieval.searcher import Searcher
        from qa_core.security.policy import AccessScope
        from fastapi.testclient import TestClient
        from app import create_app
        actor = 'rollback-rehearsal-' + token
        keys = {role: secrets.token_urlsafe(24) for role in ('admin', 'ops', 'foreign')}
        for role,key in keys.items():
            create_api_key(key, actor if role == 'admin' else actor+'-'+role, ['ops'] if role=='ops' else ['admin'],
                           tenant_id='other' if role=='foreign' else 'default')
        get_evidence_service.cache_clear()
        actions, citations = [], []
        with TestClient(create_app()) as http:
            def post(endpoint, version, expected=200):
                response = http.post(endpoint, json={'version':version,'actor':'spoofed'}, headers={'X-API-Key':keys['admin']})
                if response.status_code != expected: raise ValueError('real rollback request failed: ' + str(response.status_code))
                if expected==200 and not response.json().get('cache_invalidation_ok'):
                    raise ValueError('publication committed but cache invalidation not successful')
                actions.append({'endpoint':endpoint,'version':version,'status':response.status_code,'body':response.json()})
                return response
            for role,code in ((None,401),('ops',403),('foreign',403)):
                response=http.post('/kb/activate',json={'version':BASELINE},headers={} if role is None else {'X-API-Key':keys[role]})
                if response.status_code != code: raise ValueError('fixture publication auth boundary failed')
            post('/kb/activate',BASELINE)
            post('/kb/activate',SUCCESSOR)
            post('/kb/rollback',BASELINE)
            versions=http.get('/kb/versions',headers={'X-API-Key':keys['ops']}).json()['versions']
            if [row['version'] for row in versions if row['status']=='ACTIVE'] != [BASELINE]:
                raise ValueError('actual rollback did not restore unique baseline ACTIVE')
            client=MilvusClient(uri=settings.milvus_uri,timeout=120)
            try:
                searcher=Searcher(client=client,settings=settings,embedder=object(),cache=object())
                for group in ('faq','fault_case','guide','sop','manual'):
                    assets={m['asset_id'] for m in metadata.values() if m['doc_type']==group}
                    row=next(r for r in rows if r['kb_version']==BASELINE and r['asset_id'] in assets)
                    doc=searcher.fetch_evidence_chunk(row['id'],BASELINE,scope=AccessScope.build(roles=['admin']),
                        collection_kind='faq' if group=='faq' else 'doc')
                    if doc is None: raise ValueError('rolled-back full-schema reference missing')
                    ref=evidence_of(doc, 'faq' if group=='faq' else 'doc')
                    statuses={}
                    for role in ('admin','ops','foreign'):
                        response=http.get(ref['source_url'],headers={'X-API-Key':keys[role]})
                        statuses[role]=response.status_code
                        if role!='foreign' and (response.status_code!=200 or response.json()!=ref):
                            raise ValueError('rolled-back original text/location mismatch: '+group+' / '+role+' / HTTP '+str(response.status_code))
                    statuses['no_key']=http.get(ref['source_url']).status_code
                    if statuses!={'admin':200,'ops':200,'foreign':404,'no_key':401}:
                        raise ValueError('rolled-back citation auth boundary failed')
                    citations.append({'group':group,'statuses':statuses,'reference':ref})
            finally: client.close()
            query=http.post('/kb/search-evidence',json={'query':'AMF注册成功率下降，排查时先核对哪些信息？','top_n':5},headers={'X-API-Key':keys['ops']})
            if query.status_code!=200 or query.json().get('status')!='EVIDENCE_FOUND':
                raise ValueError('default query after rollback failed')
            if query.json().get('kb_version') not in (None,BASELINE): raise ValueError('default query used wrong ACTIVE')
            sop=http.post('/kb/search-evidence',json={'query':'ALM-AMF-REG SOP 处置流程','top_n':5},headers={'X-API-Key':keys['ops']})
            if sop.status_code!=200 or sop.json().get('reason')!='missing_safety_controls':
                raise ValueError('rollback changed SOP safety refusal')
            post('/kb/rollback',SUCCESSOR)
            # Only this disposable fixture is changed to exercise fail-closed rollback.
            row=next(r for r in rows if r['kb_version']==BASELINE and r['doc_type']!='faq')
            changed=copy.deepcopy(row); changed['text']+=' fixture tamper'
            client=MilvusClient(uri=settings.milvus_uri,timeout=120)
            try: client.upsert(collection_name=settings.doc_collection,data=[changed]);client.flush(settings.doc_collection)
            finally: client.close()
            post('/kb/rollback',BASELINE,400)
            client=MilvusClient(uri=settings.milvus_uri,timeout=120)
            try: client.upsert(collection_name=settings.doc_collection,data=[row]);client.flush(settings.doc_collection)
            finally: client.close()
            withdrawn=http.post('/kb/withdrawals',json={'source_system':row['source_system'],'source_id':row['source_id'],
                'reason':'disposable rollback rehearsal only'},headers={'X-API-Key':keys['admin']})
            if withdrawn.status_code!=200: raise ValueError('fixture withdrawal failed')
            post('/kb/rollback',BASELINE,400)
            with engine.connect() as conn:
                active=conn.execute(sa.text("SELECT version FROM kb_versions WHERE status='ACTIVE'")).scalars().all()
                audit=[dict(r) for r in conn.execute(sa.text("SELECT action,actor FROM audit_log WHERE action IN ('activate','rollback') ORDER BY id")).mappings()]
            if active!=[SUCCESSOR] or [a['action'] for a in audit]!=['activate','activate','rollback','rollback'] or any(a['actor']!=actor for a in audit):
                raise ValueError('rollback refusal changed pointer or audit actor')
        client=MilvusClient(uri=settings.milvus_uri,timeout=120)
        try:
            final_rows=read_rows(client,settings.doc_collection)+read_rows(client,settings.faq_collection)
            if fingerprint(sorted(final_rows,key=lambda row:row['id']))!=source_fingerprint:
                raise ValueError('fixture vector/body tamper was not restored exactly')
        finally:client.close()
        original_after,_=sql_snapshot(original_engine)
        if original_before!=original_after:raise ValueError('original SQL changed during disposable rehearsal')
        result={'passed':True,'full_source_chunks':11200,'versions':gates,'actions':actions,'citations':citations,
            'default_active_query':query.json(),'sop_refusal':sop.json(),'real_standard_rollback_passed':True,
            'tamper_and_withdrawal_refuse_without_pointer_change':True,'authenticated_audit_actor_verified':True,
            'original_sql_unchanged':True,'original_active_changed':False,'paid_calls':0,
            'fixture_namespace':database,'fixture_keys_never_written_to_files':True}
    finally:
        if created:
            client=MilvusClient(uri=settings.milvus_uri,timeout=120)
            try:
                for name in (settings.doc_collection,settings.faq_collection):
                    if client.has_collection(name):client.drop_collection(name)
            finally:client.close()
            from qa_core.security.withdrawals import _ledger_engine
            _ledger_engine(settings.mysql_dsn).dispose()
            if engine is not None:engine.dispose()
            root_sql(database,f"DROP DATABASE `{database}`; REVOKE ALL ON `{database}`.* FROM 'rag'@'%';")
        original_engine.dispose()
    write(REPORT/'sandbox-summary.json',{**result,'fixture_database_collections_grants_keys_removed':True})
    print('Full 5300/5900 real-data fixture activation, rollback, citations and refusal checks passed; namespace removed',flush=True)


if __name__=='__main__': main()
