"""Stage only the eight content-bound, explicitly admitted local triage FAQs."""
import argparse
import copy
import csv
from datetime import datetime, timezone
import hashlib
import json
import secrets
from unittest.mock import Mock

import sqlalchemy as sa
from pymilvus import MilvusClient

from scripts.prepare_triage_faq_review import ROOT, OUTPUT as REVIEW, validate
from scripts.prepare_legacy_projection import read, write, digest, seal, verify_seal
from scripts.build_original_rollback_baseline import apply_settings
from scripts.stage_protocol_faq_demo import hashed, vector_protected
from scripts import original_local_runtime as runtime
from qa_core.quality.review_contract import check_approval

VERSION='local-triage-faq-r1'
DOC,FAQ='local_triage_faq_r1_doc','local_triage_faq_r1_faq'
CANDIDATE=ROOT/'data/candidates/triage-faq-approved-demo-r1'
REPORT=ROOT/'.runtime/test/reports/triage-faq-staged-20261010'
RECEIPT=ROOT/'data/candidates/triage-faq-review-r1.receipt.approved.json'


def validate_receipt(receipt, package):
    if (receipt.get('decision')!='approved' or receipt.get('reviewer_kind')!='human_user_chat_confirmation'
        or receipt.get('user_message')!='批准8份，仅限隔离本地演示候选'
        or receipt.get('package_sha256')!=digest(REVIEW/'review-package.json')
        or receipt.get('exact_entry_ids')!=[e['id'] for e in package['entries']]
        or receipt.get('scope')!='eight_triage_summaries_isolated_local_staged_demo_only'
        or receipt.get('publication_authorized') is not False or receipt.get('outbound_authorized') is not False
        or receipt.get('semantic_case_labels_approved') is not False):
        raise ValueError('exact eight-item chat admission required')


def prepare(receipt_path):
    package=validate(REVIEW);receipt=read(receipt_path);validate_receipt(receipt,package)
    if CANDIDATE.exists() or REPORT.exists():raise ValueError('durable state exists; inspect, never replay')
    CANDIDATE.mkdir(parents=True);raw=CANDIDATE/'raw';raw.mkdir()
    metadata,mapping,approved={},[],[]
    for i,original in enumerate(package['entries'],1):
        e=copy.deepcopy(original)
        e['approval'].update(status='approved',proposer='codex_authored_drafts',reviewer='human_user_chat_confirmation',
            evidence='Explicit chat admission; receipt_sha256='+digest(receipt_path))
        check_approval(e,required=True)
        name=f'faq_triage_{i:02}.csv';identity=e['source_identity_proposal']
        with (raw/name).open('w',encoding='utf-8',newline='') as f:
            writer=csv.DictWriter(f,fieldnames=['faq_id','question','answer']);writer.writeheader()
            writer.writerow(dict(faq_id=e['id'],question=e['question'],answer=e['answer']))
        metadata[name]=dict(tenant_id='default',dept_id='public',visibility='role_restricted',allowed_roles=['ops','admin'],
            role_scope=['ops','admin'],security_level='internal',source_system=identity['source_system'],
            source_id=identity['source_id'],source_version=identity['source_version'],asset_id='kb-'+hashed([VERSION,identity['source_id']])[:32],
            status='active',effective_from=None,effective_to=None,doc_type='faq',network_tech='5G',dataset_id='triage-faq-demo',
            origin_type=e['origin_type'],synthetic=False,generator_version='',generation_seed='',review_status='human_chat_approved',
            license_status='project_paraphrase_local_demo',evidence_refs=[package['sources'][k]['url'] for k in e['source_refs']])
        mapping.append(dict(filename=name,sha256=digest(raw/name),tenant_id='default',source_system=identity['source_system'],
            source_id=identity['source_id'],approval=e['approval']))
        approved.append(e)
    write(raw/'meta.json',metadata)
    write(CANDIDATE/'source-map.json',dict(protocol='reviewed-source-map-v1',entries=mapping))
    write(CANDIDATE/'approved-bodies.json',dict(entries=approved,source_package_sha256=receipt['package_sha256']))
    write(CANDIDATE/'approval-receipt.json',receipt)
    write(CANDIDATE/'projection.json',dict(version=VERSION,status='STAGED',collections=[DOC,FAQ],local_use_only=True,
        source_lifecycle_active_is_not_kb_activation=True,publication=False,outbound=False,review_cases_remain_pending=True,
        third_party_reproduction_permission_not_claimed=True,full_original_questions_supported=0))
    from qa_core.ingest.source_identity import validate_mapping
    validate_mapping(raw,read(CANDIDATE/'source-map.json'),metadata)
    seal(CANDIDATE);verify_seal(CANDIDATE)
    return metadata,receipt


def configure():
    base=runtime.build_profile()
    settings=base.model_copy(update=dict(doc_collection=DOC,faq_collection=FAQ,data_dir=str(CANDIDATE),reports_dir=str(REPORT/'quality'),
        generation_enabled=False,agent_enabled=False,llm_api_key='',llm_base_url='http://127.0.0.1:1/disabled',
        semantic_cache_enabled=False,auth_required=True,allow_header_roles=False,redis_url='redis://127.0.0.1:6379/14',
        embedding_device='cpu',rerank_device='cpu'))
    url=sa.engine.make_url(settings.mysql_dsn)
    if url.host not in {'localhost','127.0.0.1'} or url.database!='rag_kb':raise ValueError('local original storage required')
    apply_settings(settings)
    return base,settings


def protected_sql(engine):
    result={}
    for table in ['kb_versions','kb_version_manifests','ingest_records','knowledge_assets','knowledge_relations',
                  'knowledge_source_withdrawals','ingest_jobs','evidence_locations']:
        with engine.connect() as conn:rows=[dict(r) for r in conn.execute(sa.text('SELECT * FROM '+table)).mappings()]
        if table in {'kb_versions','kb_version_manifests'}:rows=[r for r in rows if r['version']!=VERSION]
        elif table in {'ingest_records','ingest_jobs','knowledge_assets'}:rows=[r for r in rows if r.get('kb_version')!=VERSION]
        if table=='evidence_locations':result[table]=rows
        else:result[table]=hashed(sorted(rows,key=lambda r:json.dumps(r,sort_keys=True,default=str)))
    return result


def verify_qa(settings,engine):
    from qa_core.application.qa_usecase import QaUseCase
    from qa_core.retrieval.searcher import Searcher
    from qa_core.application.evidence_service import create_evidence_service
    from qa_core.security.policy import AccessScope
    guard=Mock();guard.chat.side_effect=AssertionError('paid generation forbidden');guard.chat_stream.side_effect=AssertionError('paid generation forbidden')
    uc=QaUseCase(searcher=Searcher(settings=settings),llm=guard);scope=AccessScope.build(roles=['admin'])
    evidence=create_evidence_service(settings);samples=[]
    for e in read(CANDIDATE/'approved-bodies.json')['entries']:
        answer=uc.ask(e['question'],roles=['admin'],scope=scope,kb_version=VERSION,persist=False,use_llm=False)
        write(REPORT/(e['id'].lower()+'-qa.json'),answer)
        assert answer['route']=='faq_exact' and answer['status']=='ANSWERED',e['id']
        assert len(answer['sources'])==1
        source=evidence.source(answer['sources'][0]['evidence_id'],scope)
        assert source and source['text']==e['answer'] and source['location']['kind']!='legacy'
        samples.append(dict(id=e['id'],query=e['question'],answer=answer,source=source))
    write(REPORT/'exact-qa.json',dict(passed=True,samples=samples,use_llm=False,persist=False))
    observations=[]
    for case in read(REVIEW/'review-cases.json')['cases']:
        answer=uc.ask(case['query'],roles=['admin'],scope=scope,kb_version=VERSION,persist=False,use_llm=False)
        observations.append(dict(id=case['id'],query=case['query'],required_behavior=case['required_response_behavior'],
            result=answer,human_semantic_label_status='pending',independent_evaluation=False))
        print('development observation',case['id'],answer.get('status'),flush=True)
    write(REPORT/'development-observations.json',dict(rows=observations,metrics_generated=False))
    assert not guard.chat.called and not guard.chat_stream.called
    verify_http(engine,uc,samples)


def verify_http(engine,uc,samples):
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from qa_core.api import route_qa,route_evidence
    from qa_core.api.auth import create_api_key,hash_key
    route_qa._usecase=uc;app=FastAPI();app.include_router(route_qa.router);app.include_router(route_evidence.router)
    keys={}
    try:
        for label,roles,tenant,levels in [('admin',['admin'],'default',None),('ops',['ops'],'default',None),
            ('foreign',['admin'],'other',None),('restricted',['admin'],'default',['public'])]:
            key=secrets.token_urlsafe(32);create_api_key(key,'triage-stage-probe-'+label,roles,tenant_id=tenant,security_levels=levels);keys[label]=key
        with TestClient(app) as h:
            checks=[]
            for sample in samples:
                url=sample['source']['source_url']
                statuses={label:h.get(url,headers={'X-API-Key':key}).status_code for label,key in keys.items()}
                statuses['unauthenticated']=h.get(url).status_code
                assert statuses==dict(admin=200,ops=404,foreign=404,restricted=404,unauthenticated=401),statuses
                assert h.get(url,headers={'X-API-Key':keys['admin']}).json()['text']==sample['source']['text']
                checks.append(dict(id=sample['id'],statuses=statuses,complete_original=True))
            recommendations=h.get('/qa/recommendations',params={'kb_version':VERSION},headers={'X-API-Key':keys['admin']})
            recommendations.raise_for_status();assert len(recommendations.json()['items'])==8
            write(REPORT/'http-auth.json',dict(passed=True,transport='real in-process FastAPI ASGI with database keys; not socket/browser',
                checks=checks,recommendations=recommendations.json(),staged_ops_denial_expected=True))
    finally:
        with engine.begin() as c:
            for key in keys.values():c.execute(sa.text('DELETE FROM api_keys WHERE key_hash=:h'),dict(h=hash_key(key)))
        with engine.connect() as c:assert c.execute(sa.text("SELECT COUNT(*) FROM api_keys WHERE user_id LIKE 'triage-stage-probe-%'")).scalar()==0


def execute(receipt_path):
    metadata,receipt=prepare(receipt_path);REPORT.mkdir(parents=True)
    base,settings=configure();engine=sa.create_engine(settings.mysql_dsn,pool_pre_ping=True)
    client=MilvusClient(uri=settings.milvus_uri,timeout=120)
    try:
        before=protected_sql(engine);vectors=vector_protected(client,[base.doc_collection,base.faq_collection,'kb_doc','kb_faq'])
        write(REPORT/'protected-before.json',dict(sql=before,vectors=vectors))
        with engine.connect() as c:
            assert c.execute(sa.text("SELECT version FROM kb_versions WHERE status='ACTIVE'")).scalars().all()==['local-merged-r2']
            assert not c.execute(sa.text('SELECT 1 FROM kb_versions WHERE version=:v'),dict(v=VERSION)).first()
            for meta in metadata.values():
                assert not c.execute(sa.text('SELECT 1 FROM knowledge_assets WHERE asset_id=:a OR (tenant_id=:t AND source_system=:s AND source_id=:i)'),
                    dict(a=meta['asset_id'],t='default',s=meta['source_system'],i=meta['source_id'])).first()
        assert not client.has_collection(DOC) and not client.has_collection(FAQ)
        from scripts.init_collections import build
        build(client,DOC,False);build(client,FAQ,True)
        from qa_core.domain.models import KnowledgeAsset
        from qa_core.storage.domain_dao import _db_values
        with engine.begin() as c:
            for name,meta in metadata.items():
                model=KnowledgeAsset(asset_id=meta['asset_id'],tenant_id='default',filename=name,doc_type='faq',
                    source_system=meta['source_system'],source_id=meta['source_id'],source_version=meta['source_version'],
                    kb_version=VERSION,checksum=digest(CANDIDATE/'raw'/name),dept_id='public',security_level='internal',
                    visibility='role_restricted',role_scope=['ops','admin'],status='active',reviewed_by=None,
                    approved_at=datetime.fromisoformat(receipt['recorded_at_utc']))
                values=_db_values(model);values['approved_at']=values['approved_at'].astimezone(timezone.utc).replace(tzinfo=None)
                columns=list(values)
                c.execute(sa.text('INSERT INTO knowledge_assets ('+','.join('`'+x+'`' for x in columns)+') VALUES ('+','.join(':'+x for x in columns)+')'),values)
            c.execute(sa.text('INSERT INTO audit_log(action,actor,payload_json) VALUES(:a,:u,:p)'),dict(a='knowledge_admission',u='codex-approved-local-cli',
                p=json.dumps(dict(version=VERSION,files=8,receipt_sha256=digest(receipt_path),publication_authorized=False,outbound_authorized=False))))
        from scripts.rebuild_kb import run_rebuild
        stats=run_rebuild(VERSION,raw_dir=CANDIDATE/'raw',strict_metadata=True,reviewed_source_map=CANDIDATE/'source-map.json',
            record_locations=True,tokenizer_chunking=True,progress=lambda stage,done,total,stats:print(stage,done,'/',total,flush=True))
        write(REPORT/'ingestion.json',stats);assert stats['done']==8 and stats['failed']==0
        from qa_core.storage.kb_dao import KbDao
        from qa_core.storage.version_manifest import verify_read_binding
        dao=KbDao(settings.mysql_dsn);assert dao.get_version(VERSION)['status']=='STAGED';assert verify_read_binding(settings,VERSION)
        rows=client.query(collection_name=FAQ,filter=f'kb_version == "{VERSION}"',output_fields=['id','question','answer','allowed_roles','visibility','dense'],limit=20,consistency_level='Strong')
        assert len(rows)==8 and all(len(r['dense'])==1024 and r['visibility']=='role_restricted' and sorted(r['allowed_roles'])==['admin','ops'] for r in rows)
        write(REPORT/'rows.json',[{k:v for k,v in row.items() if k!='dense'} for row in rows])
        from qa_core.quality.gate import run_gate,save_report
        gate=run_gate(settings,VERSION);path=save_report(gate,VERSION,settings.reports_dir);dao.set_quality_report(VERSION,path)
        write(REPORT/'gate.json',dict(passed=gate.passed,report=path,
            scope='full-publication gate retained; FAQ-only STAGED may not satisfy alarm/doc checks'))
        verify_qa(settings,engine)
        after=protected_sql(engine);after_vectors=vector_protected(client,list(vectors))
        write(REPORT/'protected-after.json',dict(sql=after,vectors=after_vectors))
        assert {k:v for k,v in before.items() if k!='evidence_locations'}=={k:v for k,v in after.items() if k!='evidence_locations'}
        old_locations={r['identity_hash']:r for r in before['evidence_locations']};new_locations={r['identity_hash']:r for r in after['evidence_locations']}
        assert all(new_locations.get(k)==v for k,v in old_locations.items()) and len(new_locations)==len(old_locations)+8
        assert vectors==after_vectors
        assert digest(ROOT/'.env.runtime')=='045ef4acd5b9babfea24d0c3e9b61679deb51a84c6803eb2e4f553139dbc19ff'
        assert read(runtime.ROOT/'generation-validation-budget.json')['used']==3
        write(REPORT/'execution-summary.json',dict(passed=True,version=VERSION,status='STAGED',admitted_faqs=8,real_embedding_rows=8,
            embedding_dimensions=1024,active_and_protected_vectors_unchanged=True,source_locations=True,new_paid_requests=0,
            publication=False,outbound=False,publication_gate_passed=gate.passed,independent_quality_passed=False,
            semantic_labels_pending=16,full_original_questions_supported=0))
        seal(REPORT);verify_seal(REPORT);print('PASS: 8 admitted triage FAQs in isolated STAGED; live ACTIVE unchanged',flush=True)
    finally:client.close();engine.dispose()


def verify_staged():
    """Read-only completion after durable ingestion; never repeat assets or embeddings."""
    package=validate(REVIEW);validate_receipt(read(RECEIPT),package);verify_seal(CANDIDATE)
    if (REPORT/'artifact-hashes.json').exists():raise ValueError('report already sealed; do not overwrite')
    base,settings=configure();engine=sa.create_engine(settings.mysql_dsn)
    client=MilvusClient(uri=settings.milvus_uri,timeout=120)
    try:
        from qa_core.storage.kb_dao import KbDao
        from qa_core.storage.version_manifest import verify_read_binding
        dao=KbDao(settings.mysql_dsn)
        assert dao.get_version(VERSION)['status']=='STAGED' and verify_read_binding(settings,VERSION)
        records=dao.list_records(VERSION);assert len(records)==8 and all(r['status']=='done' and r['chunk_count']==1 for r in records)
        rows=client.query(collection_name=FAQ,filter=f'kb_version == "{VERSION}"',output_fields=['id','question','answer','dense'],limit=20,consistency_level='Strong')
        approved=read(CANDIDATE/'approved-bodies.json')['entries']
        assert len(rows)==8 and {r['question']:r['answer'] for r in rows}=={e['question']:e['answer'] for e in approved}
        assert all(len(r['dense'])==1024 for r in rows)
        assert read(REPORT/'ingestion.json')['done']==8
        verify_qa(settings,engine)
        before=read(REPORT/'protected-before.json');after=protected_sql(engine)
        vectors=vector_protected(client,list(before['vectors']))
        write(REPORT/'protected-after.json',dict(sql=after,vectors=vectors))
        assert {k:v for k,v in before['sql'].items() if k!='evidence_locations'}=={k:v for k,v in after.items() if k!='evidence_locations'}
        old={r['identity_hash']:r for r in before['sql']['evidence_locations']};new={r['identity_hash']:r for r in after['evidence_locations']}
        assert all(new.get(k)==v for k,v in old.items()) and len(new)==len(old)+8
        assert vectors==before['vectors']
        assert digest(ROOT/'.env.runtime')=='045ef4acd5b9babfea24d0c3e9b61679deb51a84c6803eb2e4f553139dbc19ff'
        assert read(runtime.ROOT/'generation-validation-budget.json')['used']==3
        write(REPORT/'execution-summary.json',dict(passed=True,engineering_checks_passed=True,version=VERSION,status='STAGED',
            admitted_faqs=8,real_embedding_rows=8,embedding_dimensions=1024,active_and_protected_vectors_unchanged=True,
            source_locations=True,new_paid_requests=0,publication=False,outbound=False,publication_gate_passed=read(REPORT/'gate.json')['passed'],
            independent_quality_passed=False,semantic_labels_pending=16,full_original_questions_supported=0,
            resumed_read_only_after_full_publication_gate_failed=True))
        (REPORT/'initial-staging.log').write_bytes((ROOT/'.runtime/test/triage-faq-staging.log').read_bytes())
        seal(REPORT);verify_seal(REPORT)
        print('PASS: eight exact FAQs, originals and authorization; STAGED retained; full publication gate remains failed',flush=True)
    finally:client.close();engine.dispose()


if __name__=='__main__':
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('--approval-receipt',type=type(RECEIPT),required=True)
    p.add_argument('--verify-staged',action='store_true',help='read-only verification of already-ingested exact candidate')
    args=p.parse_args()
    if args.approval_receipt.resolve()!=RECEIPT.resolve():raise ValueError('exact approved receipt path required')
    if args.verify_staged:verify_staged()
    else:execute(args.approval_receipt)
