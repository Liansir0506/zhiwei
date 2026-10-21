"""Explicitly admitted, isolated complete-content STAGED merge; never publish."""
import argparse
import copy
import csv
from collections import Counter
from datetime import datetime,timezone
import hashlib
import json
import shutil
from unittest.mock import Mock,patch

import sqlalchemy as sa
from pymilvus import MilvusClient
from scripts import original_local_runtime as runtime
from scripts import stage_triage_faq_demo as triage
from scripts.prepare_legacy_projection import read,write,digest,seal,verify_seal
from scripts.migrate_legacy_collections import read_rows,fingerprint
from scripts.build_original_rollback_baseline import apply_settings
from qa_core.quality.review_contract import check_approval
from qa_core.indexing.chunkers.supported_rebuild import supported_manual_rebuild
from qa_core.storage.location_dao import LocationDao
from qa_core.retrieval.filters import _to_doc

ROOT=runtime.PROJECT
REVIEW=ROOT/'data/candidates/content-quality-review-r1'
PARENT=ROOT/'data/candidates/merged-protocol-release-r2'
BASE_REPORT=ROOT/'.runtime/test/reports/merged-protocol-release-20261010'
CANDIDATE=ROOT/'data/candidates/content-quality-merged-r2'
REPORT=ROOT/'.runtime/test/reports/content-quality-merged-20261010-r2'
QA_REPORT=REPORT/'qa-recovery-r5'
RECEIPT=ROOT/'data/candidates/content-quality-review-r1.receipt.approved.json'
VERSION='local-content-quality-r1'
DOC,FAQ='local_content_quality_r1_doc','local_content_quality_r1_faq'
MESSAGE='批准20份，仅限隔离本地演示STAGED'


def hashed(value):
    return hashlib.sha256(json.dumps(value,sort_keys=True,ensure_ascii=False,default=str).encode()).hexdigest()


def validate():
    for path in (REVIEW,PARENT,BASE_REPORT,triage.CANDIDATE,triage.REPORT):verify_seal(path)
    package=read(REVIEW/'faq-revision-review.json')
    if len(package['entries'])!=20 or package['baseline_sha256']!=digest(BASE_REPORT/'expected-rows.json'):
        raise ValueError('exact frozen twenty-item review required')
    for e in package['entries']:
        check_approval(e)
        if (e['approval']['status']!='pending' or e['access_proposal']!=dict(tenant_id='default',
            roles=['ops','admin'],visibility='internal',security_level='internal') or not e['synthetic']):
            raise ValueError('review scope changed')
    receipt=read(RECEIPT)
    if receipt!=dict(decision='approved',reviewer_kind='human_user_chat_confirmation',user_message=MESSAGE,
        package_sha256=digest(REVIEW/'artifact-hashes.json'),exact_entry_ids=[e['id'] for e in package['entries']],
        scope='twenty_synthetic_faqs_isolated_local_staged_replace_templates_new_version_only',
        recorded_at_utc=receipt.get('recorded_at_utc'),publication_authorized=False,outbound_authorized=False,
        independent_quality_approved=False):raise ValueError('exact receipt required')
    datetime.fromisoformat(receipt['recorded_at_utc'])
    return package,receipt


def prepare():
    package,receipt=validate()
    if CANDIDATE.exists() or REPORT.exists():raise ValueError('durable state exists; use explicit resume/verify')
    (CANDIDATE/'raw').mkdir(parents=True);REPORT.mkdir(parents=True)
    meta={};source_maps={};removed=[]
    expected=read(BASE_REPORT/'expected-rows.json')
    template_ids={i for e in package['entries'] for i in e['origin_row_ids']}
    template_assets={r['asset_id'] for r in expected.values() if r['id'] in template_ids}
    template_sources={name for name,m in read(PARENT/'raw/meta.json').items() if m['asset_id'] in template_assets}
    assert len(template_sources)==1
    for parent in (PARENT,triage.CANDIDATE):
        if parent==PARENT:
            # Each inherited identity already has an explicit admitted parent receipt.
            mapping={}
        else:mapping={e['filename']:e for e in read(parent/'source-map.json')['entries']}
        for name,original in read(parent/'raw/meta.json').items():
            if name in template_sources:removed.append(name);continue
            assert name not in meta
            shutil.copyfile(parent/'raw'/name,CANDIDATE/'raw'/name);meta[name]=copy.deepcopy(original)
            source_maps[name]=mapping.get(name,dict(filename=name,sha256=digest(parent/'raw'/name),
                tenant_id=original['tenant_id'],source_system=original['source_system'],source_id=original['source_id'],
                approval=dict(status='approved',proposer='inherited_admitted_source',reviewer='human_user_chat_confirmation',
                    evidence='Inherited admitted merged package sha256='+digest(PARENT/'artifact-hashes.json'))))
    approved=[]
    for i,original in enumerate(package['entries'],1):
        e=copy.deepcopy(original);e['approval'].update(status='approved',proposer='codex_authored_drafts',
            reviewer='human_user_chat_confirmation',evidence='Explicit chat receipt sha256='+digest(RECEIPT))
        check_approval(e,required=True);approved.append(e)
        name=f'faq_revision_{i:02}.csv';identity=e['source_identity_proposal']
        with (CANDIDATE/'raw'/name).open('w',encoding='utf-8',newline='') as f:
            writer=csv.DictWriter(f,fieldnames=['faq_id','question','answer']);writer.writeheader()
            writer.writerow(dict(faq_id=e['id'],question=e['question'],answer=e['answer']))
        meta[name]=dict(tenant_id='default',dept_id='public',visibility='role_restricted',allowed_roles=['ops','admin'],
            role_scope=['ops','admin'],security_level='internal',source_system=identity['source_system'],
            source_id=identity['source_id'],source_version=identity['source_version'],asset_id='kb-'+hashed([VERSION,e['id']])[:32],
            status='active',effective_from=None,effective_to=None,doc_type='faq',network_tech='5G',dataset_id='faq-revision-demo',
            origin_type='synthetic',synthetic=True,generator_version='project_authored_revision-r1',generation_seed='',
            review_status='human_chat_approved',license_status='project_authored_local_demo',evidence_refs=[])
        source_maps[name]=dict(filename=name,sha256=digest(CANDIDATE/'raw'/name),tenant_id='default',
            source_system=identity['source_system'],source_id=identity['source_id'],approval=e['approval'])
    assert len(meta)==306
    write(CANDIDATE/'raw/meta.json',meta)
    write(CANDIDATE/'source-map.json',dict(protocol='reviewed-source-map-v1',entries=list(source_maps.values())))
    from qa_core.ingest.source_identity import validate_mapping
    validate_mapping(CANDIDATE/'raw',read(CANDIDATE/'source-map.json'),meta)
    write(CANDIDATE/'approved-bodies.json',dict(entries=approved))
    write(CANDIDATE/'approval-receipt.json',receipt)
    write(CANDIDATE/'projection.json',dict(version=VERSION,status='STAGED',collections=[DOC,FAQ],files=306,
        parent_versions=['local-merged-r2',triage.VERSION],new_admissions=20,removed_template_sources=removed,
        old_versions_preserved=True,publication_authorized=False,outbound_authorized=False,
        manual_rebuild_policy='tokenizer-paragraph-v1',independent_quality_approved=False))
    seal(CANDIDATE);verify_seal(CANDIDATE)


def settings_for():
    settings=runtime.build_profile().model_copy(update=dict(doc_collection=DOC,faq_collection=FAQ,data_dir=str(CANDIDATE),
        reports_dir=str(REPORT/'quality'),generation_enabled=False,agent_enabled=False,llm_api_key='',
        llm_base_url='http://127.0.0.1:1/disabled',semantic_cache_enabled=False,redis_url='redis://127.0.0.1:6379/14',
        embedding_device='cpu',rerank_device='cpu'))
    url=sa.engine.make_url(settings.mysql_dsn)
    if url.host not in {'localhost','127.0.0.1'} or url.database!='rag_kb':raise ValueError('local storage required')
    apply_settings(settings);return settings


TABLES=['kb_versions','kb_version_manifests','ingest_records','knowledge_assets','knowledge_relations',
    'knowledge_source_withdrawals','ingest_jobs','evidence_locations']


def sql_snapshot(engine):
    with engine.connect() as conn:
        rows={t:[dict(r) for r in conn.execute(sa.text('SELECT * FROM '+t)).mappings()] for t in TABLES}
    return json.loads(json.dumps(rows,default=str))


def protect(engine,client,base):
    before=sql_snapshot(engine)
    if (REPORT/'protected-before-v2.json').exists():
        assert read(REPORT/'protected-before-v2.json')==before,'initialization storage changed; manual inspection required'
    else:write(REPORT/'protected-before-v2.json',before)
    names=[base.doc_collection,base.faq_collection,triage.DOC,triage.FAQ,'kb_doc','kb_faq']
    vectors={}
    for n in names:
        if client.has_collection(n):
            rows=read_rows(client,n);vectors[n]=dict(rows=len(rows),sha256=fingerprint(rows))
        else:vectors[n]=dict(exists=False)
    write(REPORT/'vectors-before.json',vectors)


def copy_existing(engine,client,base):
    package,_=validate();template_ids={i for e in package['entries'] for i in e['origin_row_ids']}
    rows=[r for name in (base.doc_collection,base.faq_collection) for r in read_rows(client,name)
        if r['kb_version']=='local-merged-r2' and r['doc_type']!='manual' and r['id'] not in template_ids]
    rows.extend(r for r in read_rows(client,triage.FAQ) if r['kb_version']==triage.VERSION)
    assert len(rows)==2961
    metadata=read(CANDIDATE/'raw/meta.json');filenames={m['asset_id']:name for name,m in metadata.items()}
    locations=LocationDao(engine);projected=[];positions={};lineage=[]
    for old in rows:
        new=copy.deepcopy(old);new.update(id=hashed([VERSION,old['id']])[:32],kb_version=VERSION)
        location=locations.get(_to_doc(dict(id=old['id'],entity=old)))
        assert location and not location.get('ambiguous') and location['file_sha256']==digest(CANDIDATE/'raw'/filenames[old['asset_id']])
        projected.append(new);positions[new['id']]=location
        lineage.append(dict(old_id=old['id'],target_id=new['id'],vector_sha256=hashed(old['dense'])))
    write(REPORT/'projected-rows.json',{r['id']:{k:v for k,v in r.items() if k!='dense'} for r in projected})
    write(REPORT/'projected-lineage.json',lineage);write(REPORT/'projected-locations.json',positions)
    from qa_core.ingest.write_guard import candidate_write
    from qa_core.storage.version_manifest import bind_candidate
    with candidate_write(VERSION):
        bind_candidate(settings_for(),VERSION)
        for collection,faq in ((DOC,False),(FAQ,True)):
            selected=[r for r in projected if (r['doc_type']=='faq')==faq]
            for offset in range(0,len(selected),100):client.upsert(collection_name=collection,data=selected[offset:offset+100])
            client.flush(collection)
        counts=Counter(filenames[r['asset_id']] for r in projected)
        with engine.begin() as conn:
            conn.execute(sa.text('INSERT INTO ingest_records(file_hash,filename,asset_id,doc_type,kb_version,status,chunk_count) VALUES(:file_hash,:filename,:asset_id,:doc_type,:kb_version,:status,:chunk_count)'),
                [dict(file_hash=digest(CANDIDATE/'raw'/name),filename=name,asset_id=metadata[name]['asset_id'],
                    doc_type=metadata[name]['doc_type'],kb_version=VERSION,status='done',chunk_count=count) for name,count in counts.items()])
            conn.execute(sa.text('INSERT INTO evidence_locations(identity_hash,location_json) VALUES(:id,:location)'),
                [dict(id=LocationDao.identity(_to_doc(dict(id=r['id'],entity=r))),location=json.dumps(positions[r['id']],ensure_ascii=False)) for r in projected])


def register(engine):
    from qa_core.domain.models import KnowledgeAsset
    from qa_core.storage.domain_dao import _db_values
    metadata=read(CANDIDATE/'raw/meta.json');_,receipt=validate()
    with engine.begin() as conn:
        for name,meta in metadata.items():
            if not name.startswith('faq_revision_'):continue
            assert not conn.execute(sa.text('SELECT 1 FROM knowledge_assets WHERE asset_id=:a OR (tenant_id=:t AND source_system=:s AND source_id=:i)'),
                dict(a=meta['asset_id'],t=meta['tenant_id'],s=meta['source_system'],i=meta['source_id'])).first()
            model=KnowledgeAsset(asset_id=meta['asset_id'],tenant_id='default',filename=name,doc_type='faq',
                source_system=meta['source_system'],source_id=meta['source_id'],source_version=meta['source_version'],
                kb_version=VERSION,checksum=digest(CANDIDATE/'raw'/name),dept_id='public',security_level='internal',
                visibility='role_restricted',role_scope=['ops','admin'],status='active',reviewed_by=None,
                approved_at=datetime.fromisoformat(receipt['recorded_at_utc']))
            values=_db_values(model);values['approved_at']=values['approved_at'].astimezone(timezone.utc).replace(tzinfo=None)
            conn.execute(sa.text('INSERT INTO knowledge_assets ('+','.join('`'+k+'`' for k in values)+') VALUES ('+','.join(':'+k for k in values)+')'),values)
        conn.execute(sa.text('INSERT INTO audit_log(action,actor,payload_json) VALUES(:a,:u,:p)'),dict(a='knowledge_admission',
            u='codex-approved-local-cli',p=json.dumps(dict(version=VERSION,new_admissions=20,receipt_sha256=digest(RECEIPT),publication_authorized=False,outbound_authorized=False))))


def rebuild():
    from transformers import AutoTokenizer
    from scripts.rebuild_kb import run_rebuild
    settings=settings_for();tokenizer=AutoTokenizer.from_pretrained(settings.embedding_model_path,local_files_only=True)
    with supported_manual_rebuild(tokenizer):
        stats=run_rebuild(VERSION,raw_dir=CANDIDATE/'raw',strict_metadata=True,reviewed_source_map=CANDIDATE/'source-map.json',
            record_locations=True,tokenizer_chunking=True,progress=lambda stage,done,total,stats:print(stage,done,'/',total,flush=True))
    write(REPORT/'ingestion.json',stats)
    assert stats['failed']==0 and stats['duplicates']==0


def execute(resume_initialize=False):
    base=runtime.build_profile()
    if resume_initialize:validate();verify_seal(CANDIDATE)
    else:prepare()
    settings=settings_for()
    engine=sa.create_engine(settings.mysql_dsn,pool_pre_ping=True);client=MilvusClient(uri=settings.milvus_uri,timeout=120)
    try:
        with engine.connect() as conn:
            assert conn.execute(sa.text("SELECT version FROM kb_versions WHERE status='ACTIVE'")).scalars().all()==['local-merged-r2']
            assert not conn.execute(sa.text('SELECT 1 FROM kb_versions WHERE version=:v'),dict(v=VERSION)).first()
        assert not client.has_collection(DOC) and not client.has_collection(FAQ)
        protect(engine,client,base)
        from scripts.init_collections import build
        build(client,DOC,False);build(client,FAQ,True)
        register(engine);copy_existing(engine,client,base)
    finally:client.close();engine.dispose()
    rebuild();verify()


def verify():
    validate();verify_seal(CANDIDATE);settings=settings_for()
    engine=sa.create_engine(settings.mysql_dsn);client=MilvusClient(uri=settings.milvus_uri,timeout=120)
    try:
        rows=[r for n in (DOC,FAQ) for r in read_rows(client,n)]
        assert len(rows)==4357 and all(r['kb_version']==VERSION and len(r['dense'])==1024 for r in rows)
        assert Counter(r['doc_type'] for r in rows)==dict(manual=1376,sop=2200,fault_case=750,faq=31)
        positions=LocationDao(engine);meta=read(CANDIDATE/'raw/meta.json')
        filenames={m['asset_id']:name for name,m in meta.items()}
        for row in rows:
            location=positions.get(_to_doc(dict(id=row['id'],entity=row)))
            name=filenames[row['asset_id']]
            assert location and not location.get('ambiguous') and location['file_sha256']==digest(CANDIDATE/'raw'/name)
            for k in ('tenant_id','source_system','source_id','source_version','asset_id','visibility','security_level','allowed_roles','role_scope'):
                assert row[k]==meta[name][k],(name,k)
        original=read(REPORT/'projected-rows.json');lineage={r['target_id']:r for r in read(REPORT/'projected-lineage.json')}
        for row in rows:
            if row['id'] in original:
                assert {k:v for k,v in row.items() if k!='dense'}==original[row['id']]
                assert hashed(row['dense'])==lineage[row['id']]['vector_sha256']
        proposals={p['source']:p for p in read(REVIEW/'manual-chunk-proposals.json')['sources']}
        from scripts.rebuild_kb import normalize_text
        for source,p in proposals.items():
            actual=[r for r in rows if r['source']==source]
            assert {(r['text'],r['clause']) for r in actual}=={(normalize_text(c['text']),c['clause']) for c in p['chunks']},source
        after=sql_snapshot(engine);before=read(REPORT/'protected-before-v2.json')
        extras={}
        for table,prior in before.items():
            old={hashed(r) for r in prior};current={hashed(r) for r in after[table]}
            assert old<=current,table
            extras[table]=len(current-old)
        assert extras==dict(kb_versions=1,kb_version_manifests=1,ingest_records=306,knowledge_assets=20,
            knowledge_relations=0,knowledge_source_withdrawals=0,ingest_jobs=0,evidence_locations=4357),extras
        for name,expected in read(REPORT/'vectors-before.json').items():
            if expected.get('exists') is False:assert not client.has_collection(name),name
            else:
                actual=read_rows(client,name);assert dict(rows=len(actual),sha256=fingerprint(actual))==expected,name
        with engine.connect() as conn:
            assert conn.execute(sa.text("SELECT version FROM kb_versions WHERE status='ACTIVE'")).scalars().all()==['local-merged-r2']
            assert conn.execute(sa.text('SELECT status FROM kb_versions WHERE version=:v'),dict(v=VERSION)).scalar()=='STAGED'
        write(REPORT/'structural-verification.json',dict(passed=True,rows=len(rows),files=306,extras=extras,
            old_sql_and_vectors_unchanged=True,original_manual_text_and_acl_preserved=True,new_embeddings=1396,copied_embeddings=2961))
    finally:client.close();engine.dispose()
    from qa_core.quality.gate import run_gate,save_report
    from qa_core.storage.kb_dao import KbDao
    gate=run_gate(settings,VERSION);path=save_report(gate,VERSION,settings.reports_dir)
    dao=KbDao(settings.mysql_dsn)
    try:dao.set_quality_report(VERSION,path)
    finally:dao.engine.dispose()
    write(REPORT/'gate.json',dict(passed=gate.passed,report=path,independent_quality_approved=False))
    print('Actual full gate passed='+str(gate.passed),flush=True)
    verify_qa(settings)


def verify_qa(settings):
    QA_REPORT.mkdir(exist_ok=True)
    from qa_core.application.qa_usecase import QaUseCase
    from qa_core.application.evidence_service import create_evidence_service
    from qa_core.retrieval.supported_searcher import SupportedFaqSearcher as Searcher
    from qa_core.retrieval.supported_evidence_v2 import relevant_docs
    from qa_core.security.policy import AccessScope
    guard=Mock();guard.chat.side_effect=guard.chat_stream.side_effect=AssertionError('paid call forbidden')
    uc=QaUseCase(searcher=Searcher(settings=settings),llm=guard);scope=AccessScope.build(roles=['admin'])
    evidence=create_evidence_service(settings);observations=[]
    entries=read(CANDIDATE/'approved-bodies.json')['entries']+read(triage.CANDIDATE/'approved-bodies.json')['entries']
    with patch('qa_core.application.qa_usecase.relevant_docs',relevant_docs):
        for e in entries:
            result=uc.ask(e['question'],roles=['admin'],scope=scope,kb_version=VERSION,use_llm=False,persist=False)
            assert result['route']=='faq_exact' and result['status']=='ANSWERED' and len(result['sources'])==1,e['id']
            original=evidence.source(result['sources'][0]['evidence_id'],scope)
            assert original['text']==e['answer']
            observations.append(dict(id=e['id'],result=result))
            write(QA_REPORT/'exact-qa.json',observations)
        variants=[]
        for c in read(triage.REVIEW/'review-cases.json')['cases']:
            e=next(e for e in entries if e['id']==c['candidate_id'])
            result=uc.ask(c['query'],roles=['admin'],scope=scope,kb_version=VERSION,use_llm=False,persist=False)
            variants.append(dict(id=c['id'],result=result));write(QA_REPORT/'triage-variants.json',variants)
            assert result['status']=='ANSWERED' and [s['source_id'] for s in result['sources']]==[e['source_identity_proposal']['source_id']],c['id']
    assert not guard.chat.called and not guard.chat_stream.called
    write(QA_REPORT/'qa-summary.json',dict(passed=True,exact=28,triage_variants=16,paid_calls=0,
        persist=False,selector_integration='candidate scoped import only',independent_quality_approved=False))
    print('Candidate exact/variant QA passed; publication not performed',flush=True)


if __name__=='__main__':
    import torch
    torch.set_num_threads(4)
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--execute',action='store_true');parser.add_argument('--resume-initialize',action='store_true');parser.add_argument('--resume-rebuild',action='store_true');parser.add_argument('--verify-existing',action='store_true')
    args=parser.parse_args()
    if sum([args.execute,args.resume_initialize,args.resume_rebuild,args.verify_existing])!=1:parser.error('select one explicit action')
    if args.execute:execute()
    elif args.resume_initialize:execute(resume_initialize=True)
    elif args.resume_rebuild:validate();verify_seal(CANDIDATE);rebuild();verify()
    else:verify()
