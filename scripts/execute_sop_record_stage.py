"""Execute specifically approved draft-r2 in a new namespace; no activation or LLM.

Derived from the sealed r1 execution pattern; r1 code and stores remain unchanged.
"""
from datetime import datetime,timezone
import hashlib
import json
import logging
import os
from pathlib import Path
import subprocess
import uuid
from unittest.mock import patch

import numpy as np
import sqlalchemy as sa

from scripts import content_quality_release_runtime as rt,readonly_workspace_runtime as ro
from scripts.prepare_sop_record_admission import ROOT,OUTPUT as REVIEW
from scripts.build_sop_record_candidate import CANDIDATE,validate_inputs
from scripts.sop_record_stage_profile import profile,validate_profile
from scripts.prepare_legacy_projection import read,write,digest,verify_seal
from scripts.verify_readonly_business_workspace import sql_snapshot
from scripts.migrate_domain_model import split_sql_statements
from scripts.migrate_legacy_collections import read_rows,fingerprint
from qa_core.quality.version_workspace import Workspace
from qa_core.quality.sop_record_admission import TARGET

VERSION=TARGET['version']
RECEIPT=ROOT/'data/candidates/sop-record-admission-review-r2.receipt.approved.json'
REPORT=ROOT/'.runtime/test/reports/sop-record-staged-20261014'


def root_sql(sql):
    # Static container and bounded new database SQL; password expanded inside container only.
    p=subprocess.run(['docker','exec','-i','telecom-ops-rag-test-mysql-1','sh','-c',
        'MYSQL_PWD="$MYSQL_ROOT_PASSWORD" exec mysql -uroot'],input=sql,text=True,capture_output=True,
        timeout=35,creationflags=subprocess.CREATE_NO_WINDOW)
    if p.returncode:raise RuntimeError('isolated database initialization failed; raw output withheld')


def create_collections(client):
    from scripts import init_collections as schema
    original=schema._common_fields
    class Proxy:
        def __init__(self,actual):self.actual=actual
        def __getattr__(self,k):return getattr(self.actual,k)
        def add_field(self,name,*args,**kwargs):
            if name=='quality':kwargs['max_length']=16
            return self.actual.add_field(name,*args,**kwargs)
    # Approved quality='local_demo' is 10 chars; preserve it without truncation.
    with patch.object(schema,'_common_fields',lambda s:original(Proxy(s))):
        for name,faq in ((TARGET['doc_collection'],False),(TARGET['faq_collection'],True)):
            if client.has_collection(name):raise ValueError('target collection collision')
            schema.build(client,name,faq)


def main():
    if REPORT.exists():raise ValueError('durable execution exists; inspect before any resume')
    REPORT.mkdir(parents=True);write(REPORT/'state.json',dict(state='running',phase='before',paid_calls=0))
    stage='validate';engine=None
    try:
        plan,receipt,metadata,mapping=validate_inputs(RECEIPT);verify_seal(CANDIDATE)
        verify_seal(ROOT/'.runtime/test/reports/sop-record-admission-20261014')
        for name,checksum in read(ROOT/'.runtime/test/reports/sop-record-admission-20261014/code-bindings.json').items():
            assert digest(ROOT/name)==checksum
        rt.install_adapter();original=rt.selected_settings();iso=ro.profile();selected=profile();validate_profile(selected)
        def protected():
            with Workspace(iso).client() as client:
                prior_names=[n for n in client.list_collections() if n not in (TARGET['doc_collection'],TARGET['faq_collection'])]
                vectors={n:dict(rows=len(rows),sha256=fingerprint(rows)) for n in prior_names if (rows:=read_rows(client,n)) is not None}
            return dict(original=Workspace(original).snapshot(rt.VERSION),original_sql=sql_snapshot(original),readonly_sql=sql_snapshot(iso),
                isolated_prior_vectors=vectors,env_sha256=digest(ROOT/'.env.runtime'),budget_sha256=digest(rt.owned.ROOT/'generation-validation-budget.json'),
                original_pid=rt.owned.inspect()['pid'],readonly_pid=ro.inspect()['pid'],
                original_key_sha256=digest(rt.owned.ROOT/'api-key.txt'),readonly_key_sha256=digest(ro.ROOT/'api-key.txt'),
                candidate_seal_sha256=digest(CANDIDATE/'artifact-hashes.json'),review_seal_sha256=digest(REVIEW/'artifact-hashes.json'))
        from scripts.verify_sop_guide_preview import protected as previous_protected
        before=protected();before['previous_guides_and_runtime']=previous_protected();write(REPORT/'before.json',before)
        with sa.create_engine(iso.mysql_dsn).connect() as conn:
            assert conn.execute(sa.text('SELECT COUNT(*) FROM information_schema.SCHEMATA WHERE SCHEMA_NAME=:n'),{'n':TARGET['database']}).scalar()==0
        with Workspace(iso).client() as client:
            assert not any(client.has_collection(n) for n in (TARGET['doc_collection'],TARGET['faq_collection']))
        assert sa.engine.make_url(selected.mysql_dsn).username=='rag'
        stage='initialize_database';write(REPORT/'state.json',dict(state='running',phase=stage,paid_calls=0))
        root_sql('CREATE DATABASE `test_sop_records_r2` CHARACTER SET utf8mb4;\nGRANT ALL ON `test_sop_records_r2`.* TO \'rag\'@\'%\';')
        engine=sa.create_engine(selected.mysql_dsn)
        with engine.begin() as conn:
            assert conn.exec_driver_sql('SELECT DATABASE()').scalar()==TARGET['database']
            for file in sorted((ROOT/'docker/mysql/init').glob('*.sql')):
                for statement in split_sql_statements(file.read_text(encoding='utf-8')):
                    upper=statement.strip().upper()
                    if upper.startswith('INSERT') and 'INTO DEPARTMENTS' in upper:continue
                    if upper.startswith(('USE ','CREATE DATABASE','DROP ','GRANT ')):raise ValueError('unscoped schema statement')
                    conn.exec_driver_sql(statement)
            from qa_core.domain.models import KnowledgeAsset
            from qa_core.storage.domain_dao import _db_values
            for name,m in metadata.items():
                model=KnowledgeAsset(asset_id=m['asset_id'],tenant_id='default',filename=name,doc_type='guide',
                    source_system=m['source_system'],source_id=m['source_id'],source_version=m['source_version'],kb_version=VERSION,
                    checksum=digest(CANDIDATE/'raw'/name),dept_id=None,security_level='internal',visibility='role_restricted',
                    role_scope=['ops','admin'],status='active',reviewed_by=None,
                    approved_at=datetime.fromisoformat(receipt['recorded_at_utc']))
                values=_db_values(model);values['approved_at']=values['approved_at'].astimezone(timezone.utc).replace(tzinfo=None)
                columns=list(values);conn.execute(sa.text('INSERT INTO knowledge_assets ('+','.join('`'+c+'`' for c in columns)+') VALUES ('+','.join(':'+c for c in columns)+')'),values)
            conn.execute(sa.text('INSERT INTO audit_log(action,actor,payload_json) VALUES(:a,:u,:p)'),
                dict(a='knowledge_admission',u='codex-chat-approved-stage',p=json.dumps(dict(version=VERSION,files=20,
                    receipt_sha256=digest(RECEIPT),scope=receipt['scope'],publication_authorized=False,outbound_authorized=False))))
        stage='initialize_collections'
        with Workspace(selected).client() as client:create_collections(client)
        from scripts.build_original_rollback_baseline import apply_settings
        apply_settings(selected);os.environ['HF_HUB_OFFLINE']='1';os.environ['TRANSFORMERS_OFFLINE']='1'
        import torch;torch.set_num_threads(4)
        from transformers import AutoTokenizer
        tokenizer=AutoTokenizer.from_pretrained(selected.embedding_model_path,local_files_only=True)
        from scripts.prepare_sop_record_revision import OUTPUT as DRAFTS
        from qa_core.indexing.chunkers.sop_evidence_guide import chunk_evidence_guide
        from qa_core.indexing.parsers.parsers import parse_dispatch
        from qa_core.indexing.parsers.sanitize import sanitize
        from qa_core.indexing.parsers.locations import locate_chunks
        from scripts.rebuild_kb import normalize_text
        expected=[]
        for entry in plan['entries']:
            path=CANDIDATE/'raw'/entry['filename']
            chunks=chunk_evidence_guide(parse_dispatch('guide',path),tokenizer,source=entry['filename'])
            for chunk in chunks:chunk.text=normalize_text(sanitize(chunk.text)[0])
            locations=locate_chunks(path,chunks)
            assert all(loc and not loc['ambiguous'] for loc in locations)
            assert all(c.doc_type=='guide' and len(tokenizer.encode(c.text,add_special_tokens=True))<=450 and len(c.text.encode())<=7900 for c in chunks)
            expected.append(dict(filename=entry['filename'],body_sha256=digest(path),chunks=[dict(text=c.text,location=loc) for c,loc in zip(chunks,locations)]))
        assert sum(len(row['chunks']) for row in expected)==180
        old_preflight=read(DRAFTS/'chunk-preflight.json')
        assert {x['filename']:len(x['chunks']) for x in expected}=={x['filename']:x['chunks'] for x in old_preflight['sources']}
        write(REPORT/'chunk-preflight-execution.json',dict(sources=expected,total_chunks=180,real_local_tokenizer=True))
        from qa_core.indexing.chunkers.sop_evidence_guide import evidence_guide_rebuild
        from scripts.rebuild_kb import run_rebuild
        stage='ingestion'
        def progress(phase,done,total,stats):
            write(REPORT/'progress.json',dict(phase=phase,done=done,total=total,failed=stats['failed'],paid_calls=0))
            print(phase,done,'/',total,flush=True)
        with evidence_guide_rebuild(tokenizer):
            stats=run_rebuild(VERSION,raw_dir=CANDIDATE/'raw',strict_metadata=True,reviewed_source_map=CANDIDATE/'source-map.json',
                record_locations=True,tokenizer_chunking=True,progress=progress)
        write(REPORT/'ingestion.json',stats);assert stats['done']==20 and stats['failed']==0 and stats['duplicates']==0
        from qa_core.storage.kb_dao import KbDao
        from qa_core.storage.version_manifest import verify_read_binding
        kb=KbDao(selected.mysql_dsn);assert kb.get_version(VERSION)['status']=='STAGED' and verify_read_binding(selected,VERSION)
        stage='stored_source_permission_checks'
        with Workspace(selected).client() as client:
            rows=read_rows(client,selected.doc_collection);faq=read_rows(client,selected.faq_collection)
            assert len(rows)==180 and not faq
            by_source={r['filename']:r for r in expected}
            for r in rows:
                m=metadata[r['source']]
                assert r['doc_type']=='guide' and r['kb_version']==VERSION and r['quality']=='local_demo'
                assert r['text'] in [c['text'] for c in by_source[r['source']]['chunks']]
                assert all(r[k]==m[k] for k in ('asset_id','source_id','source_version','source_system','allowed_roles','role_scope','visibility','tenant_id'))
                assert np.asarray(r['dense']).shape==(1024,) and abs(float(np.linalg.norm(r['dense']))-1)<.001
            write(REPORT/'stored-rows.json',[{k:v for k,v in r.items() if k!='dense'} for r in rows])
            from qa_core.retrieval.searcher import Searcher
            from qa_core.application.evidence_service import create_evidence_service
            from qa_core.retrieval.filters import _to_doc
            from qa_core.schemas.evidence import evidence_of
            from qa_core.security.policy import AccessScope
            evidence=create_evidence_service(selected,searcher_type=lambda **kwargs:Searcher(client=client,**kwargs))
            admin=AccessScope.build(roles=['admin']);ops=AccessScope.build(roles=['ops'])
            guest=AccessScope.build(roles=['guest']);foreign=AccessScope.build(roles=['admin'],tenant_id='other')
            checks=[];sampled=set()
            for r in rows:
                token=evidence_of(_to_doc(dict(id=r['id'],entity=r)))['evidence_id']
                actual=evidence.source(token,admin);assert actual and actual['text']==r['text'] and actual['location']['file_sha256']==digest(CANDIDATE/'raw'/r['source'])
                assert not actual['location'].get('ambiguous')
                if r['asset_id'] not in sampled:
                    assert evidence.source(token,ops) and evidence.source(token,guest) is None and evidence.source(token,foreign) is None
                    sampled.add(r['asset_id'])
                checks.append(dict(id=r['id'],source=r['source'],exact_source_and_location=True))
            write(REPORT/'evidence-checks.json',dict(all_admin_reads=len(checks),ops_guest_foreign_sources=len(sampled),checks=checks,paid_calls=0))
        stage='ordinary_publication_gate'
        from qa_core.quality.gate import run_gate,save_report
        gate=run_gate(selected,VERSION);gate_path=save_report(gate,VERSION,selected.reports_dir);kb.set_quality_report(VERSION,gate_path)
        assert not gate.passed and any(c['check']=='alarm_recall' and not c['passed'] for c in gate.checks)
        write(REPORT/'gate-scope.json',dict(ordinary_publication_gate_passed=False,checks=gate.checks,
            reason='Pure guide candidate has no fault_case denominator; zero SOP is not complete SOP acceptance',
            full_sop_approved=False,publication_authorized=False))
        stage='after_snapshot';after=protected();after['previous_guides_and_runtime']=previous_protected();write(REPORT/'after.json',after);assert before==after
        assert kb.get_version(VERSION)['status']=='STAGED'
        with engine.connect() as conn:
            assert conn.exec_driver_sql("SELECT COUNT(*) FROM kb_versions WHERE status='ACTIVE'").scalar()==0
            assert conn.exec_driver_sql('SELECT COUNT(*) FROM knowledge_assets').scalar()==20
        write(REPORT/'state.json',dict(state='completed',version=VERSION,status='STAGED',admitted_guides=20,stored_chunks=180,
            real_embedding_dimensions=1024,source_locations_verified=180,role_boundary_sources=20,
            protected_original_and_readonly_unchanged=True,ordinary_publication_gate_passed=False,paid_calls=0,
            published=False,activated=False,generation_enabled=False,agent_enabled=False,full_sop_approved=False,independent_frozen=0))
        print(json.dumps(dict(state='completed',guides=20,chunks=180,status='STAGED',ordinary_publication_gate_passed=False,paid_calls=0)))
    except Exception as error:
        write(REPORT/'state.json',dict(state='failed',stage=stage,error_kind=type(error).__name__,paid_calls=0,automatic_replay=False))
        raise RuntimeError('Isolated stage interrupted; inspect retained safe stage before resume') from None
    finally:
        if engine is not None:engine.dispose()


if __name__=='__main__':main()
