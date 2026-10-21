"""Original entry review/package/failing delta gate; no publish/paid calls."""
import json
import secrets
import time
import uuid
import subprocess
import httpx
import sqlalchemy as sa
from scripts import content_quality_release_runtime as rt
from scripts import readonly_workspace_runtime as readonly
from scripts.prepare_legacy_projection import read,write,digest,verify_seal
from scripts.verify_readonly_business_workspace import sql_snapshot
from qa_core.api.auth import create_api_key,hash_key
from qa_core.ingest import workspace as uploads
from qa_core.quality.publication import capture_snapshot

REPORT=rt.PROJECT/'.runtime/test/reports/version-workspace-p2-20261012'
VERSION='local-work-eb06bef4017f'

def snapshot(settings):
    result=sql_snapshot(settings)
    result.pop('kb_versions');result.pop('kb_version_manifests')
    engine=sa.create_engine(settings.mysql_dsn)
    try:
        with engine.connect() as conn:
            for table in ('kb_versions','kb_version_manifests'):
                rows=[dict(r) for r in conn.execute(sa.text(f'SELECT * FROM `{table}` WHERE version<>:v ORDER BY version'),{'v':VERSION}).mappings()]
                result[table]=uploads.digest(json.loads(json.dumps(rows,default=str)))
    finally:engine.dispose()
    return result

def main():
    REPORT.mkdir(exist_ok=True);folder=REPORT/('original-'+uuid.uuid4().hex[:8]);folder.mkdir()
    rt.install_adapter();settings=rt.selected_settings();rt.owned.apply_profile(settings)
    assert rt.owned.inspect()['status']=='running';assert readonly.inspect()['status']=='running'
    budget=rt.owned.ROOT/'generation-validation-budget.json';env=rt.PROJECT/'.env.runtime'
    before=dict(sql=snapshot(settings),active=capture_snapshot(settings,rt.VERSION),budget=digest(budget),env=digest(env),
        uploads={r['id']:digest(uploads.safe_path('uploads',r['id'],'upload.json')) for r in uploads.uploads()})
    write(folder/'before.json',before)
    keys={'ops':rt.owned.read_key(rt.owned.load_state())};created=[];checks=[];engine=sa.create_engine(settings.mysql_dsn)
    try:
        token=uuid.uuid4().hex[:8]
        for role,roles,scope in [('admin',['admin'],{}),('auditor',['auditor'],{}),('guest',['guest'],{}),
            ('foreign',['admin'],{'tenant_id':'other-version'}),('scoped',['admin'],{'dept_id':'ran'})]:
            key=secrets.token_urlsafe(30);actor='version-original-audit-'+role+'-'+token
            created.append((key,actor));keys[role]=key;create_api_key(key,actor,roles,**scope)
        with httpx.Client(base_url='http://127.0.0.1:8010',timeout=90,trust_env=False) as client:
            def req(case,method,path,role='admin',status=200,**kwargs):
                response=client.request(method,'/admin/version-workspace'+path,headers={'X-API-Key':keys.get(role,'invalid')},**kwargs)
                assert response.status_code==status,(case,response.status_code,response.text[:150]);assert response.headers['cache-control']=='no-store'
                checks.append(dict(case=case,role=role,status=response.status_code));write(folder/'api-checks.json',checks);return response.json()
            for role in keys:
                caps=req(role+'-capability','GET','/capabilities',role);assert caps['can_read_versions']==(role in ('admin','auditor'))
            for role in ('ops','guest','foreign','scoped','invalid'):req(role+'-index-denied','GET','',role,status=401 if role=='invalid' else 403)
            req('auditor-index','GET','','auditor');req('auditor-no-review','POST','/reviews','auditor',status=403,json={'version':VERSION})
            index=req('admin-index','GET','');assert index['execution_mode']=='review_only'
            detail=req('delta-detail','GET','/versions/'+VERSION);assert detail['candidate_kind']=='upload_delta' and len(detail['sources'])==1 and not detail['blockers'],detail['blockers']
            assert any(r['admission']=='pending' and r['filename']=='manual_pending_demo.md' for r in detail['uploads'])
            req('current-full-detail','GET','/versions/'+rt.VERSION)
            req('archived-config-refusal','GET','/versions/local-merged-r2',status=409)
            source=req('approved-original','GET','/versions/'+VERSION+'/sources/faq_revision_01.csv');assert source['sha256']==detail['sources'][0]['sha256']
            req('auditor-body-denied','GET','/versions/'+VERSION+'/sources/faq_revision_01.csv','auditor',status=403)
            review=req('concrete-review','POST','/reviews',status=201,json={'version':VERSION});assert review['new_admission'] is False
            packet=req('full-source-package','POST','/packages',status=201,json={'review_id':review['id']})
            assert packet['file_count']==306 and packet['chunk_count']==4357 and packet['merge_outcome']=='no_content_change'
            assert packet['pending_excluded'] and packet['new_admissions']==packet['sql_versions_created']==0
            write(folder/'package.json',packet)
            gate=req('actual-delta-gate','POST','/gate-jobs',status=202,json={'review_id':review['id']})
            for _ in range(160):
                state=client.get('/admin/version-workspace/gate-jobs/'+gate['id'],headers={'X-API-Key':keys['admin']}).json()
                if state['state'] not in ('queued','running'):break
                time.sleep(1)
            assert state['state']=='blocked',state
            assert len(state['result']['checks'])==5 and not state['result']['passed'];write(folder/'delta-gate.json',state)
            req('persisted-failed-gate','GET','/versions/'+VERSION+'/gate')
            req('original-pointer-publish-blocked','POST','/plans',status=409,json={'version':VERSION,'action':'publish'})
            req('original-profile-rollback-blocked','POST','/plans',status=409,json={'version':'local-merged-r2','action':'rollback'})
            # Source inspection must not close the persistent QA channel.
            exact=client.post('/qa',headers={'X-API-Key':keys['ops']},json={'query':next(__import__('csv').DictReader((rt.merge.CANDIDATE/'raw/faq_revision_01.csv').open(encoding='utf-8')))['question']})
            assert exact.status_code==200 and exact.json()['route']=='faq_exact';checks.append(dict(case='qa-faq-after-version-probes',status=200,route='faq_exact',paid_calls=0))
            write(folder/'api-checks.json',checks)
        result=subprocess.run(['node',str(rt.PROJECT/'tests/browser/version_workspace_original.js')],input=json.dumps(dict(url='http://127.0.0.1:8010',reportDir=str(folder),keys=keys,version=VERSION)),capture_output=True,encoding='utf-8',timeout=180)
        (folder/'browser.log').write_text(result.stdout+result.stderr,encoding='utf-8');assert result.returncode==0,'Original browser review failed; evidence preserved'
        after=dict(sql=snapshot(settings),active=capture_snapshot(settings,rt.VERSION),budget=digest(budget),env=digest(env),
            uploads={r['id']:digest(uploads.safe_path('uploads',r['id'],'upload.json')) for r in uploads.uploads()})
        assert after==before;write(folder/'after.json',after)
        uploads.verify_candidate(settings,uploads.get_candidate(VERSION),writable=True)
        for source in (rt.merge.CANDIDATE,rt.merge.REPORT,rt.REVIEW):verify_seal(source)
        write(folder/'verification.json',dict(passed=True,api_checks=len(checks),original_active_unchanged=True,
            protected_snapshots_unchanged=True,allowed_mutation='only existing STAGED candidate gate report and manifest snapshot; own new package/review/job files, temporary auth and QA session/audit',
            full_package_files=306,full_package_chunks=4357,pending_not_included=True,partial_gate_correctly_blocked=True,
            publication_not_executed=True,paid_calls=0,new_admissions=0))
    finally:
        with engine.begin() as conn:
            for key,actor in created:conn.execute(sa.text('DELETE FROM api_keys WHERE key_hash=:h AND user_id=:u'),{'h':hash_key(key),'u':actor})
            remaining=conn.execute(sa.text('SELECT COUNT(*) FROM api_keys WHERE user_id LIKE :prefix'),{'prefix':'version-original-audit-%'}).scalar()
        engine.dispose();write(folder/'cleanup.json',dict(temporary_keys_remaining=remaining,paid_budget_unchanged=digest(budget)==before['budget']))
    write(REPORT/'original-latest.json',dict(attempt=folder.name));print('Original review/package, failed delta gate, safe publication refusal and browser passed; temporary keys removed.',flush=True)
if __name__=='__main__':main()
