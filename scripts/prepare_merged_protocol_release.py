"""Build a reversible local STAGED projection from already admitted sources.

Never activate, edit previous rows, re-admit identities or call a paid model.
The new version shares the live collection pair, with version-isolated keys,
so the existing ACTIVE can be rolled back without replacing its vectors.
"""
import copy
import hashlib
import html
import json
import os
from pathlib import Path
import shutil
from collections import Counter

import sqlalchemy as sa
from pymilvus import MilvusClient

from scripts import original_local_runtime as runtime
from scripts import stage_protocol_faq_demo as protocol
from scripts.original_citation_candidate import CANDIDATE as ORIGINAL
from scripts.prepare_legacy_projection import read, write, digest, seal, verify_seal
from scripts.migrate_legacy_collections import read_rows, fingerprint
from scripts.build_original_rollback_baseline import apply_settings, check_vector
from qa_core.storage.location_dao import LocationDao
from qa_core.retrieval.filters import _to_doc

ROOT=Path(__file__).resolve().parents[1]
VERSION='local-merged-r2'
OLD='local-release-r1'
CANDIDATE=ROOT/'data/candidates/merged-protocol-release-r2'
REPORT=ROOT/'.runtime/test/reports/merged-protocol-release-20261010'
REPORTS_ROOT=ROOT/'.runtime/test/reports'


def settings_for():
    return runtime.build_profile().model_copy(update=dict(data_dir=str(CANDIDATE),
        reports_dir=str(REPORTS_ROOT),api_port=18018,redis_url='redis://127.0.0.1:6379/14',
        generation_enabled=False,agent_enabled=False,llm_api_key='',
        llm_base_url='http://127.0.0.1:1/disabled',semantic_cache_enabled=False))


def project_row(row):
    if row['kb_version'] not in {OLD,protocol.VERSION} or not row.get('asset_id'):
        raise ValueError('only admitted source versions may be projected')
    result=copy.deepcopy(row)
    result.update(id=hashlib.sha256(json.dumps([VERSION,row['id']],separators=(',',':')).encode()).hexdigest()[:32],
                  kb_version=VERSION,dense=check_vector(row['dense']))
    return result


def protected_sql(engine):
    result={}
    for table in ['kb_versions','kb_version_manifests','ingest_records','knowledge_assets',
                  'knowledge_relations','knowledge_source_withdrawals','ingest_jobs','evidence_locations']:
        with engine.connect() as conn:
            rows=[dict(r) for r in conn.execute(sa.text('SELECT * FROM '+table)).mappings()]
        if table in {'kb_versions','kb_version_manifests'}:
            rows=[r for r in rows if r['version']!=VERSION]
        elif table in {'ingest_records','ingest_jobs'}:
            rows=[r for r in rows if r['kb_version']!=VERSION]
        # New locations are checked separately against exact source lineage.
        result[table]=rows if table=='evidence_locations' else hashlib.sha256(
            json.dumps(sorted(rows,key=lambda r:json.dumps(r,sort_keys=True,default=str)),
                       sort_keys=True,default=str).encode()).hexdigest()
    return result


def execute():
    if REPORT.exists() or CANDIDATE.exists():
        raise ValueError('durable candidate exists; use verify(), never replay copying')
    for parent in (ORIGINAL,protocol.CANDIDATE,protocol.REVIEW,
                   ROOT/'.runtime/test/reports/protocol-faq-recall-fix-20261010'):
        verify_seal(parent)
    settings=settings_for();engine=sa.create_engine(settings.mysql_dsn)
    client=MilvusClient(uri=settings.milvus_uri,timeout=120)
    names=[settings.doc_collection,settings.faq_collection,protocol.DOC,protocol.FAQ]
    try:
        with engine.connect() as conn:
            assert conn.execute(sa.text("SELECT version FROM kb_versions WHERE status='ACTIVE'")).scalars().all()==[OLD]
            assert not conn.execute(sa.text('SELECT 1 FROM kb_versions WHERE version=:v'),dict(v=VERSION)).first()
            ledgers=[dict(r) for r in conn.execute(sa.text('SELECT file_hash,filename,asset_id,doc_type,kb_version,status,chunk_count FROM ingest_records WHERE kb_version IN (:a,:b)'),dict(a=OLD,b=protocol.VERSION)).mappings()]
        before=protected_sql(engine)
        originals={name:read_rows(client,name) for name in names}
        source=[r for name in names for r in originals[name] if r['kb_version'] in {OLD,protocol.VERSION}]
        assert Counter(r['kb_version'] for r in source)=={OLD:5900,protocol.VERSION:3}
        assert len(ledgers)==279 and all(r['status']=='done' for r in ledgers)
        projected=[project_row(r) for r in source]
        assert len({r['id'] for r in projected})==5903 and len({r['asset_id'] for r in projected})==279
        locations=LocationDao(engine);positions={}
        for old,new in zip(source,projected):
            loc=locations.get(_to_doc(dict(id=old['id'],entity=old)))
            assert loc and loc.get('kind')!='legacy'
            positions[new['id']]=loc
        REPORT.mkdir(parents=True);(CANDIDATE/'raw').mkdir(parents=True)
        metadata={}
        for parent in (ORIGINAL,protocol.CANDIDATE):
            for name,meta in read(parent/'raw/meta.json').items():
                assert name not in metadata
                shutil.copyfile(parent/'raw'/name,CANDIDATE/'raw'/name)
                metadata[name]=meta
        assert len(metadata)==279
        for ledger in ledgers:
            assert digest(CANDIDATE/'raw'/ledger['filename'])==ledger['file_hash']
            assert metadata[ledger['filename']]['asset_id']==ledger['asset_id']
        write(CANDIDATE/'raw/meta.json',metadata)
        write(CANDIDATE/'projection.json',dict(protocol='admitted-merge-projection-v1',version=VERSION,
            parent_versions=[OLD,protocol.VERSION],files=279,chunks=5903,new_admissions=0,
            approval_inherited_from=[dict(path=str(p/'approval-receipt.json'),sha256=digest(p/'approval-receipt.json')) for p in (ORIGINAL,protocol.CANDIDATE)],
            publication_approval='pending',source_content_and_acl_unchanged=True))
        seal(CANDIDATE)
        write(REPORT/'protected-before.json',before)
        write(REPORT/'vectors-before.json',{n:dict(rows=len(r),sha256=fingerprint(r)) for n,r in originals.items()})
        write(REPORT/'expected-rows.json',{r['id']:{k:v for k,v in r.items() if k!='dense'} for r in projected})
        write(REPORT/'expected-locations.json',positions)
        write(REPORT/'lineage.json',[dict(source_collection=n,source_id=r['id'],source_version=r['kb_version'],
            target_id=project_row(r)['id'],dense_sha256=hashlib.sha256(__import__('numpy').asarray(r['dense'],dtype='<f4').tobytes()).hexdigest())
            for n,rows in originals.items() for r in rows if r['kb_version'] in {OLD,protocol.VERSION}])
        apply_settings(settings)
        from qa_core.ingest.write_guard import candidate_write
        from qa_core.storage.version_manifest import bind_candidate
        with candidate_write(VERSION,settings=settings):
            bind_candidate(settings,VERSION)
            for collection,faq in ((settings.doc_collection,False),(settings.faq_collection,True)):
                selected=[r for r in projected if (r['doc_type']=='faq')==faq]
                for offset in range(0,len(selected),100):client.insert(collection_name=collection,data=selected[offset:offset+100])
                client.flush(collection)
            with engine.begin() as conn:
                conn.execute(sa.text("INSERT INTO ingest_records(file_hash,filename,asset_id,doc_type,kb_version,status,chunk_count) VALUES(:file_hash,:filename,:asset_id,:doc_type,:kb_version,:status,:chunk_count)"),
                    [dict(r,kb_version=VERSION) for r in ledgers])
                conn.execute(sa.text('INSERT INTO evidence_locations(identity_hash,location_json) VALUES(:id,:location)'),
                    [dict(id=LocationDao.identity(_to_doc(dict(id=r['id'],entity=r))),location=json.dumps(positions[r['id']],ensure_ascii=False)) for r in projected])
                conn.execute(sa.text('INSERT INTO audit_log(action,actor,payload_json) VALUES(:a,:u,:p)'),dict(
                    a='admitted_merge_staged',u='codex-local-engineering',p=json.dumps(dict(version=VERSION,
                    user_request='下一步',files=279,chunks=5903,new_asset_admissions=0,publication_authorized=False,
                    projection_sha256=digest(CANDIDATE/'projection.json')))))
        print('copied 5903 admitted vectors; candidate STAGED only',flush=True)
    finally:
        client.close();engine.dispose()
    verify()


def verify():
    verify_seal(CANDIDATE)
    settings=settings_for();apply_settings(settings)
    engine=sa.create_engine(settings.mysql_dsn);client=MilvusClient(uri=settings.milvus_uri,timeout=120)
    try:
        expected=read(REPORT/'expected-rows.json');positions=read(REPORT/'expected-locations.json')
        lineage=read(REPORT/'lineage.json');mapping={r['target_id']:r for r in lineage}
        rows=[];protected={}
        for name,before in read(REPORT/'vectors-before.json').items():
            allrows=read_rows(client,name)
            old=[r for r in allrows if r['kb_version']!=VERSION]
            protected[name]=dict(rows=len(old),sha256=fingerprint(old))
            assert protected[name]==before
            rows.extend(r for r in allrows if r['kb_version']==VERSION)
        assert len(rows)==5903 and {r['id'] for r in rows}==set(expected)
        for row in rows:
            assert {k:v for k,v in row.items() if k!='dense'}==expected[row['id']]
            assert hashlib.sha256(__import__('numpy').asarray(row['dense'],dtype='<f4').tobytes()).hexdigest()==mapping[row['id']]['dense_sha256']
            assert LocationDao(engine).get(_to_doc(dict(id=row['id'],entity=row)))==positions[row['id']]
        after=protected_sql(engine);before=read(REPORT/'protected-before.json')
        current_locations={r['identity_hash']:r for r in after.pop('evidence_locations')}
        previous_locations=before.pop('evidence_locations')
        assert all(current_locations[r['identity_hash']]==r for r in previous_locations)
        assert after==before
        write(REPORT/'protected-after.json',dict(sql=after,old_locations_unchanged=True,vectors=protected))
    finally:client.close();engine.dispose()
    from qa_core.quality.gate import run_gate,save_report
    from qa_core.storage.kb_dao import KbDao
    from qa_core.quality.publication import verify_publication
    import torch
    torch.set_num_threads(4)
    gate=run_gate(settings,VERSION)
    path=save_report(gate,VERSION,str(REPORT/'quality'))
    dao=KbDao(settings.mysql_dsn)
    try:dao.set_quality_report(VERSION,path)
    finally:dao.engine.dispose()
    assert gate.passed
    ready=verify_publication(settings,VERSION,path)
    old_report=runtime.build_profile().reports_dir+'/quality_'+OLD+'.json'
    rollback=verify_publication(settings,OLD,old_report)
    write(REPORT/'gate-and-rollback.json',dict(candidate=ready,previous_active=rollback,rollback_pointer_changed=False,
        rollback_verification='real snapshot gate, no activation/rollback performed',reports_root=str(REPORTS_ROOT)))
    print('candidate and old ACTIVE real publication snapshots passed',flush=True)


if __name__=='__main__':
    import argparse
    parser=argparse.ArgumentParser(description=__doc__);parser.add_argument('--execute',action='store_true');parser.add_argument('--verify-existing',action='store_true')
    args=parser.parse_args()
    if args.execute==args.verify_existing:parser.error('select execute or read-only verification of existing copying')
    execute() if args.execute else verify()
