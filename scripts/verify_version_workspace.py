"""Real admitted-subset gates, publication and rollback; disposable SQL/Milvus/UI."""
import copy
import hashlib
import json
import re
import secrets
import subprocess
import threading
import time
import uuid
from pathlib import Path
import httpx
import sqlalchemy as sa
from fastapi import FastAPI
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from fastapi.testclient import TestClient
from pymilvus import MilvusClient
from scripts import content_quality_release_runtime as current
from scripts import readonly_workspace_runtime as readonly
from scripts.prepare_legacy_projection import read,write,digest,seal,verify_seal
from scripts.verify_readonly_business_workspace import sql_snapshot
from scripts.migrate_legacy_collections import read_rows
from scripts.migrate_domain_model import split_sql_statements
from scripts.init_collections import build
from scripts.build_original_rollback_baseline import apply_settings
from qa_core.quality.version_workspace import Workspace,ROOT
from qa_core.api.route_version_workspace import make_router
from qa_core.ingest.write_guard import candidate_write
from qa_core.storage.version_manifest import bind_candidate
from qa_core.quality.publication import capture_snapshot
from qa_core.api.auth import create_api_key

REPORT=current.PROJECT/'.runtime/test/reports/version-workspace-p2-20261012'

def root_sql(database,sql):
    if not re.fullmatch(r'test_version_flow_[a-f0-9]{12}',database):raise ValueError('owned disposable namespace required')
    result=subprocess.run(['docker','exec','-i','telecom-ops-rag-test-mysql-1','sh','-c','MYSQL_PWD="$MYSQL_ROOT_PASSWORD" exec mysql -uroot'],input=sql,text=True,capture_output=True)
    if result.returncode:raise RuntimeError('Disposable SQL lifecycle failed')

def main():
    REPORT.mkdir(exist_ok=True);attempt=REPORT/('attempt-'+uuid.uuid4().hex[:8]);attempt.mkdir()
    current.install_adapter();original=current.selected_settings()
    protected=[current.merge.CANDIDATE,current.merge.REPORT,current.REVIEW]
    for folder in protected:verify_seal(folder)
    budget=current.owned.ROOT/'generation-validation-budget.json'
    before={'sql':sql_snapshot(original),'active':capture_snapshot(original,current.VERSION),
            'env':digest(current.PROJECT/'.env.runtime'),'budget':digest(budget),'readonly_sql':sql_snapshot(readonly.profile())}
    write(attempt/'before.json',before)
    metadata=read(current.merge.CANDIDATE/'raw/meta.json');mapping=read(current.merge.CANDIDATE/'source-map.json')
    source=MilvusClient(uri=original.milvus_uri,timeout=90)
    try:rows=[r for name in (original.doc_collection,original.faq_collection) for r in read_rows(source,name) if r['kb_version']==current.VERSION]
    finally:source.close()
    # Select three actual admitted files, not invented source approvals or vectors.
    names=[next(name for name,m in metadata.items() if m['doc_type']==kind and any(r['asset_id']==m['asset_id'] and (kind!='fault_case' or r['alarm_code']) for r in rows)) for kind in ('faq','fault_case','sop')]
    selected={name:metadata[name] for name in names};assets={m['asset_id'] for m in selected.values()}
    rows=[r for r in rows if r['asset_id'] in assets]
    token=uuid.uuid4().hex[:12];database='test_version_flow_'+token;versions=('vf-base-'+token,'vf-next-'+token)
    root=ROOT/('rehearsal-'+token);package=root/'package';(package/'raw').mkdir(parents=True)
    for name in names:(package/'raw'/name).write_bytes((current.merge.CANDIDATE/'raw'/name).read_bytes())
    write(package/'raw/meta.json',selected);write(package/'source-map.json',dict(protocol=mapping['protocol'],entries=[e for e in mapping['entries'] if e['filename'] in names]))
    write(package/'inheritance.json',dict(parent_seal_sha256=digest(current.merge.CANDIDATE/'artifact-hashes.json'),new_admissions=0,scope='exact approved bytes, identities and ACL; temporary subset only'))
    seal(package)
    base=readonly.profile();url=sa.engine.make_url(base.mysql_dsn)
    settings=base.model_copy(update={'mysql_dsn':url.set(database=database).render_as_string(hide_password=False),
        'doc_collection':database+'_doc','faq_collection':database+'_faq','data_dir':str(package),
        'reports_dir':str(root/'quality'),'redis_url':'redis://127.0.0.1:16379/13','app_env':'test','api_port':18022,
        'generation_enabled':False,'agent_enabled':False,'llm_api_key':'','llm_base_url':'http://127.0.0.1:1/disabled'})
    workspace=Workspace(settings,root,sandbox=True);engine=None;created=False;server=None;thread=None;checks=[]
    try:
        root_sql(database,f"CREATE DATABASE `{database}` CHARACTER SET utf8mb4;")
        created=True
        root_sql(database,f"GRANT ALL ON `{database}`.* TO 'rag'@'%';")
        engine=sa.create_engine(settings.mysql_dsn)
        with engine.begin() as conn:
            for file in sorted((current.PROJECT/'docker/mysql/init').glob('*.sql')):
                for statement in split_sql_statements(file.read_text(encoding='utf-8')):conn.exec_driver_sql(statement)
        apply_settings(settings)
        import torch;torch.set_num_threads(4)
        vectors=MilvusClient(uri=settings.milvus_uri,timeout=90)
        try:
            for name,faq in ((settings.doc_collection,False),(settings.faq_collection,True)):
                if vectors.has_collection(name):raise ValueError('fixture collection collision')
                build(vectors,name,faq)
            for version in versions:
                with candidate_write(version,settings=settings):
                    bind_candidate(settings,version)
                    for name,faq in ((settings.doc_collection,False),(settings.faq_collection,True)):
                        batch=[]
                        for row in rows:
                            if (row['doc_type']=='faq')!=faq:continue
                            item=copy.deepcopy(row);item.update(kb_version=version,id=hashlib.sha256((version+row['id']).encode()).hexdigest()[:32]);batch.append(item)
                        if batch:vectors.insert(collection_name=name,data=batch)
                        vectors.flush(name)
                    with engine.begin() as conn:
                        conn.execute(sa.text("INSERT INTO ingest_records(file_hash,filename,asset_id,doc_type,kb_version,status,chunk_count) VALUES(:hash,:name,:asset,:type,:version,'done',:count)"),
                            [dict(hash=digest(package/'raw'/name),name=name,asset=m['asset_id'],type=m['doc_type'],version=version,count=sum(r['asset_id']==m['asset_id'] for r in rows)) for name,m in selected.items()])
        finally:vectors.close()
        keys={role:secrets.token_urlsafe(28) for role in ('admin','auditor','ops','foreign','scoped','otheradmin')}
        for role,key in keys.items():create_api_key(key,'version-audit-'+role+'-'+token,['admin'] if role in ('admin','foreign','scoped','otheradmin') else [role],tenant_id='other' if role=='foreign' else 'default',dept_id='ran' if role=='scoped' else None)
        app=FastAPI();app.include_router(make_router(lambda:workspace))
        @app.get('/')
        def page():return FileResponse(current.PROJECT/'static/version-rehearsal.html',headers={'Cache-Control':'no-store'})
        app.mount('/static',StaticFiles(directory=current.PROJECT/'static'),name='static')
        with TestClient(app) as client:
            def req(label,method,path,role='admin',status=200,**kwargs):
                result=client.request(method,'/admin/version-workspace'+path,headers={'X-API-Key':keys.get(role,'invalid')},**kwargs)
                assert result.status_code==status,(label,result.status_code,result.text[:180]);assert result.headers['cache-control']=='no-store'
                checks.append({'case':label,'role':role,'status':result.status_code});write(attempt/'api-checks.json',checks);return result.json()
            for role in ('ops','foreign','scoped','invalid'):req(role+'-denied','GET','',role,status=401 if role=='invalid' else 403)
            req('auditor-read','GET','','auditor');req('auditor-write-denied','POST','/reviews','auditor',status=403,json={'version':versions[0]})
            req('strict-schema','POST','/reviews',status=422,json={'version':versions[0],'approved':True})
            # Establish the baseline gate through the actual HTTP task workflow.
            review=req('baseline-review','POST','/reviews',status=201,json={'version':versions[0]})
            req('other-actor-denied','POST','/gate-jobs','otheradmin',status=403,json={'review_id':review['id']})
            job=req('baseline-gate-start','POST','/gate-jobs',status=202,json={'review_id':review['id']})
            for _ in range(150):
                state=client.get('/admin/version-workspace/gate-jobs/'+job['id'],headers={'X-API-Key':keys['admin']}).json()
                if state['state'] not in ('queued','running'):break
                time.sleep(1)
            assert state['state']=='passed',state.get('error',state.get('result'));write(attempt/'baseline-gate.json',state)
            print('Actual admitted-subset baseline gate passed.',flush=True)
            # Explicit fixture setup: first ACTIVE is not represented as a tested publication.
            with engine.begin() as conn:conn.execute(sa.text("UPDATE kb_versions SET status='ACTIVE' WHERE version=:v"),{'v':versions[0]})
            req('wrong-target-status-refusal','POST','/plans',status=409,json={'version':versions[0],'action':'publish'})
        # The exact same router and UI now operate against a real loopback server.
        import uvicorn,socket
        listener=socket.socket();listener.bind(('127.0.0.1',18022));listener.close()
        server=uvicorn.Server(uvicorn.Config(app,host='127.0.0.1',port=18022,log_level='warning',access_log=False))
        thread=threading.Thread(target=server.run,daemon=True);thread.start()
        for _ in range(100):
            try:
                if httpx.get('http://127.0.0.1:18022/',trust_env=False,timeout=1).status_code==200:break
            except httpx.TransportError:pass
            time.sleep(.1)
        result=subprocess.run(['node',str(current.PROJECT/'tests/browser/version_workspace_smoke.js')],
            input=json.dumps(dict(url='http://127.0.0.1:18022',reportDir=str(attempt),keys=keys,baseline=versions[0],successor=versions[1])),capture_output=True,encoding='utf-8',timeout=260)
        (attempt/'browser.log').write_text(result.stdout+result.stderr,encoding='utf-8')
        assert result.returncode==0,'Real browser workflow failed; preserved evidence'
        print('Actual browser review, gate, publication and rollback passed.',flush=True)
        with TestClient(app) as client:
            headers={'X-API-Key':keys['admin']}
            history=client.get('/admin/version-workspace/history',headers=headers).json();assert {r['action'] for r in history['items']}=={'publish','rollback'}
            # Replay and target-status errors do not move the sole ACTIVE.
            plans=list(workspace.path('plans').glob('*.json'));publish=next(read(p) for p in plans if read(p)['action']=='publish')
            replay=client.post('/admin/version-workspace/executions',headers=headers,json={'plan_id':publish['id'],'confirmation':publish['confirmation']});assert replay.status_code==409;checks.append({'case':'publish-replay-refused','status':409})
            # Fresh report verification rejects real changed stored vectors.
            from qa_core.quality.publication import verify_publication
            path=workspace.status(versions[1])[1]
            vectors=MilvusClient(uri=settings.milvus_uri,timeout=30)
            try:
                modified=next(r for r in read_rows(vectors,settings.faq_collection) if r['kb_version']==versions[1]);untampered=copy.deepcopy(modified);modified['text']+=' tamper'
                vectors.upsert(collection_name=settings.faq_collection,data=[modified]);vectors.flush(settings.faq_collection)
                try:workspace.verify_publication(versions[1],path)
                except ValueError:checks.append({'case':'real-vector-tamper-refused','status':'blocked'})
                else:raise AssertionError('Tampered actual vector was accepted')
                vectors.upsert(collection_name=settings.faq_collection,data=[untampered]);vectors.flush(settings.faq_collection)
                from qa_core.security.withdrawals import identity_hash
                with engine.begin() as conn:
                    conn.execute(sa.text('INSERT INTO knowledge_source_withdrawals(identity_hash,tenant_id,source_system,source_id,reason,actor) VALUES(:h,:t,:s,:id,:r,:a)'),
                        dict(h=identity_hash(untampered['tenant_id'],untampered['source_system'],untampered['source_id']),t=untampered['tenant_id'],s=untampered['source_system'],id=untampered['source_id'],r='owned temporary rehearsal',a='version-audit-admin-'+token))
                withdrawn=client.post('/admin/version-workspace/plans',headers=headers,json={'version':versions[1],'action':'rollback'})
                assert withdrawn.status_code==409;assert '撤回' in withdrawn.json()['detail'];checks.append({'case':'real-source-withdrawal-refused','status':409})
            finally:vectors.close()
            with engine.connect() as conn:
                assert conn.execute(sa.text("SELECT version FROM kb_versions WHERE status='ACTIVE'")).scalars().all()==[versions[0]]
        write(attempt/'api-checks.json',checks);write(attempt/'history.json',history)
        after={'sql':sql_snapshot(original),'active':capture_snapshot(original,current.VERSION),'env':digest(current.PROJECT/'.env.runtime'),'budget':digest(budget),'readonly_sql':sql_snapshot(readonly.profile())}
        assert after==before;write(attempt/'after.json',after)
        for folder in protected:verify_seal(folder)
        write(attempt/'verification.json',dict(passed=True,api_checks=len(checks),rows_per_version=len(rows),admitted_files=names,
            real_sql_milvus=True,actual_publish_and_rollback=True,original_active_unchanged=True,protected_snapshots_unchanged=True,
            original_pending_not_admitted=True,paid_calls=0,new_admissions=0,independent_quality_verified=False))
    finally:
        if server:server.should_exit=True
        if thread:thread.join(timeout=15)
        if created:
            vectors=MilvusClient(uri=settings.milvus_uri,timeout=40)
            try:
                for name in (settings.doc_collection,settings.faq_collection):
                    if vectors.has_collection(name):vectors.drop_collection(name)
            finally:vectors.close()
            from qa_core.security.withdrawals import _ledger_engine
            _ledger_engine(settings.mysql_dsn).dispose()
            if engine:engine.dispose()
            root_sql(database,f"DROP DATABASE `{database}`; REVOKE ALL ON `{database}`.* FROM 'rag'@'%';")
            write(attempt/'cleanup.json',dict(database=database,database_collections_grants_and_keys_removed=True,temporary_http_stopped=not thread or not thread.is_alive()))
    write(REPORT/'latest.json',dict(attempt=attempt.name));print('Version rehearsal passed; temporary SQL/Milvus/keys removed; protected original snapshots unchanged.',flush=True)

if __name__=='__main__':main()
