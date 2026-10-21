"""Real current-profile upload/retry/ledger evidence; no new admission or LLM calls."""
import base64
import hashlib
import json
import secrets
import subprocess
import sys
import uuid
from pathlib import Path
import httpx
import sqlalchemy as sa
from pymilvus import MilvusClient
from scripts import content_quality_release_runtime as runtime
from scripts.prepare_legacy_projection import read, write, digest, verify_seal

REPORT = runtime.PROJECT / '.runtime/test/reports/ingest-workspace-p1-20261012'


def main():
    resumed = '--complete-checks' in sys.argv
    snapshot = '--snapshot-only' in sys.argv
    attempt = REPORT / sys.argv[-1] if resumed or snapshot else REPORT / ('attempt-' + uuid.uuid4().hex[:8])
    if not attempt.resolve().is_relative_to(REPORT.resolve()): raise ValueError('验收路径越界')
    attempt.mkdir(parents=True,exist_ok=resumed or snapshot)
    runtime.install_adapter(); settings = runtime.selected_settings(); runtime.owned.apply_profile(settings)
    state = runtime.owned.load_state(); assert runtime.owned.inspect()['status'] == 'running'
    keys = {'ops': runtime.owned.read_key(state)}; created = []
    engine = sa.create_engine(settings.mysql_dsn)
    from qa_core.api.auth import create_api_key, hash_key
    from qa_core.quality.publication import capture_snapshot
    from qa_core.ingest import workspace as ws
    budget = runtime.owned.ROOT / 'generation-validation-budget.json'
    env = runtime.PROJECT / '.env.runtime'
    before = {'budget':digest(budget),'env':digest(env),'snapshot':capture_snapshot(settings,runtime.VERSION)}
    seals = [runtime.merge.CANDIDATE,runtime.merge.REPORT,runtime.REVIEW]
    for folder in seals:verify_seal(folder)
    sealed_before = {str(folder.relative_to(runtime.PROJECT)):digest(folder/'artifact-hashes.json') for folder in seals}
    metadata = read(Path(settings.data_dir)/'raw/meta.json'); filename='faq_revision_01.csv'
    body = (Path(settings.data_dir)/'raw'/filename).read_bytes(); source_id=metadata[filename]['source_id']
    assert ws.admission(settings,filename,source_id,hashlib.sha256(body).hexdigest())
    suffix = uuid.uuid4().hex[:10]
    try:
        for role, roles, tenant, dept in [('admin',['admin'],'default',None),('auditor',['auditor'],'default',None),
            ('tenant_admin',['admin'],'ingest-audit-only',None),('limited_admin',['admin'],'default','limited'),('guest',['guest'],'default',None)]:
            key=secrets.token_urlsafe(32); actor='ingest-workspace-audit-'+role+'-'+suffix
            created.append((key,actor));keys[role]=key;create_api_key(key,actor,roles,tenant_id=tenant,dept_id=dept)
        observations=[]
        for role,key in keys.items():
            with httpx.Client(base_url=state['url'],headers={'X-API-Key':key},timeout=30,trust_env=False) as client:
                response=client.get('/admin/ingest-workspace');expected=200 if role in ('admin','auditor','ops') else 403
                assert response.status_code==expected,(role,response.status_code)
                assert response.headers['cache-control']=='no-store'
                if role!='admin':assert client.post('/admin/ingest-workspace/candidates',json={'label':'must deny'}).status_code==403
                if role=='admin':
                    assert client.post('/admin/ingest-workspace/candidates',json={'label':' '}).status_code==400
                    assert client.post('/admin/ingest-workspace/uploads',params={'kb_version':'local-content-quality-r1','source_id':source_id},content=body,headers={'X-Filename':filename}).status_code==409
                    assert client.post('/admin/documents/upload',params={'kb_version':'local-content-quality-r1','source_id':source_id},content=body,headers={'X-Filename':filename}).status_code==409
                observations.append({'role':role,'read_status':response.status_code,'write_allowed':role=='admin'})
        write(attempt/'api-results.json',observations)
        params={'project':str(runtime.PROJECT),'workerRoot':str(ws.ROOT),'python':sys.executable,
            'reportDir':str(attempt),'url':state['url'],'keys':keys,'filename':filename,'sourceId':source_id,
            'fileBase64':base64.b64encode(body).decode(),'loginUrl':(runtime.owned.ROOT/'open-platform.html').as_uri(),'snapshotOnly':snapshot}
        if snapshot:params['approvedUpload']=read(attempt/'browser-result.json')['approved_upload']
        if not resumed:
            result=subprocess.run(['node',str(runtime.PROJECT/'tests/browser/ingest_workspace_smoke.js')],
                input=json.dumps(params),capture_output=True,encoding='utf-8',timeout=480)
            (attempt/('snapshot-browser.log' if snapshot else 'browser.log')).write_text(result.stdout+result.stderr,encoding='utf-8')
            assert result.returncode==0,'Browser failed; inspect preserved attempt evidence'
            print(result.stdout.strip())
            if snapshot:return
        browser=read(attempt/'browser-result.json');version=browser['candidate']
        with httpx.Client(base_url=state['url'],headers={'X-API-Key':keys['admin']},timeout=30,trust_env=False) as client:
            detail=client.get('/admin/ingest-workspace/uploads/'+browser['approved_upload']).json()
            pending=client.get('/admin/ingest-workspace/uploads/'+browser['pending_upload']).json()
            assert detail['job']['status']=='success' and detail['job']['attempt_count']==2
            assert pending['job_id'] is None and pending['admission_status']=='pending'
            assert client.post('/admin/ingest-workspace/uploads/'+browser['approved_upload']+'/retry').status_code==409
            assert client.post('/admin/ingest-workspace/uploads/'+browser['pending_upload']+'/retry').status_code==409
            catalog=client.get('/kb/catalog',params={'kb_version':version,'kind':'faq'});assert catalog.status_code==200
            items=catalog.json()['items'];assert len(items)==1 and items[0]['source_id']==source_id
            source=client.get('/kb/evidence/'+items[0]['evidence_id']);assert source.status_code==200
            assert source.json()['document_id']==metadata[filename]['asset_id'] and source.json()['source']==filename and source.json()['kb_version']==version and source.json()['text']
            write(attempt/'candidate-detail.json',detail);write(attempt/'pending-detail.json',pending)
            write(attempt/'candidate-catalog.json',catalog.json());write(attempt/'candidate-source.json',source.json())
        with httpx.Client(base_url=state['url'],headers={'X-API-Key':keys['ops']},timeout=30,trust_env=False) as client:
            assert client.get('/kb/catalog',params={'kb_version':version,'kind':'faq'}).status_code in (403,404)
        milvus=MilvusClient(uri=settings.milvus_uri,timeout=60)
        try:
            rows=milvus.query(collection_name=settings.faq_collection,filter='kb_version == '+json.dumps(version),
                output_fields=['id','dense','source_id','asset_id','source','tenant_id'],limit=10,consistency_level='Strong')
            docs=milvus.query(collection_name=settings.doc_collection,filter='kb_version == '+json.dumps(version),output_fields=['id'],limit=10,consistency_level='Strong')
        finally:milvus.close()
        assert len(rows)==1 and not docs and len(rows[0]['dense'])==1024
        norm=sum(float(v)**2 for v in rows[0]['dense'])**.5;assert .99<norm<1.01
        assert rows[0]['source_id']==source_id and rows[0]['asset_id']==metadata[filename]['asset_id']
        write(attempt/'embedding-proof.json',{'model':Path(settings.embedding_model_path).name,'dimension':1024,
            'norm':norm,'faq_rows':len(rows),'document_rows':len(docs),'real_embedder':True,'original_asset_id_preserved':True})
        for folder in seals:verify_seal(folder)
        assert all(digest(runtime.PROJECT/folder/'artifact-hashes.json')==checksum for folder,checksum in sealed_before.items())
        assert runtime.owned.verify_active(settings,engine)==runtime.VERSION
        assert capture_snapshot(settings,runtime.VERSION)==before['snapshot']
        with engine.connect() as conn:
            assert conn.execute(sa.text('SELECT status FROM kb_versions WHERE version=:v'),{'v':version}).scalar()=='STAGED'
            pending_jobs=conn.execute(sa.text("SELECT COUNT(*) FROM ingest_jobs WHERE payload_json->>'$.workspace_upload_id'=:u"),{'u':browser['pending_upload']}).scalar()
            assert pending_jobs==0
        containers=subprocess.run(['docker','ps','--format','{{.Names}}'],capture_output=True,text=True,check=True).stdout.splitlines()
        assert not any(name.endswith('-worker') or name.endswith('-api') or name.endswith('-mock') for name in containers)
        assert digest(env)==before['env'] and digest(budget)==before['budget']
        worker=ws.worker_status();assert worker['status']=='ready' and worker['binding_sha256']==ws.digest(ws.profile_binding(settings))
        write(attempt/'preservation.json',{'active':runtime.VERSION,'active_snapshot_unchanged':True,'frozen_seals_verified':True,
            'env_unchanged':True,'budget_unchanged':True,'old_worker_not_started':True,'pending_jobs':pending_jobs,
            'candidate_status':'STAGED','worker':worker,'paid_calls':0})
        write(REPORT/'latest-success.json',{'attempt':attempt.name,'passed':True,'browser_checks':len(browser['checks']),
            'candidate':version,'approved_upload':browser['approved_upload'],'pending_upload':browser['pending_upload'],'paid_calls':0})
    finally:
        with engine.begin() as conn:
            for key,actor in created:conn.execute(sa.text('DELETE FROM api_keys WHERE key_hash=:h AND user_id=:u'),{'h':hash_key(key),'u':actor})
        with engine.connect() as conn:
            remaining=sum(conn.execute(sa.text('SELECT COUNT(*) FROM api_keys WHERE user_id=:u'),{'u':actor}).scalar() for _,actor in created)
        engine.dispose();write(attempt/'cleanup.json',{'temporary_keys_remaining':remaining,'budget_unchanged':digest(budget)==before['budget']})
        assert remaining==0


if __name__=='__main__': main()
