"""Real 8010 handoff preparation/revalidation; original pointer and all content fixed."""
import csv
import json
import secrets
import subprocess
import time
import uuid
import httpx
import sqlalchemy as sa
from scripts import content_quality_release_runtime as rt, readonly_workspace_runtime as readonly
from scripts.prepare_legacy_projection import read, write, digest, verify_seal
from scripts.verify_readonly_business_workspace import sql_snapshot
from qa_core.api.auth import create_api_key, hash_key
from qa_core.quality.publication import capture_snapshot
from qa_core.ingest import workspace as uploads

REPORT=rt.PROJECT/'.runtime/test/reports/runtime-handoff-step1-20261013'


def main():
    folder=REPORT/('attempt-'+uuid.uuid4().hex[:8]);folder.mkdir(parents=True)
    rt.install_adapter();settings=rt.selected_settings();rt.owned.apply_profile(settings)
    assert rt.owned.inspect()['status']=='running' and readonly.inspect()['status']=='running'
    budget=rt.owned.ROOT/'generation-validation-budget.json'
    def snapshot():
        return dict(sql=sql_snapshot(settings),readonly_sql=sql_snapshot(readonly.profile()),
                    active=capture_snapshot(settings,rt.VERSION),budget=digest(budget),env=digest(rt.PROJECT/'.env.runtime'),
                    uploads={r['id']:digest(uploads.safe_path('uploads',r['id'],'upload.json')) for r in uploads.uploads()})
    before=snapshot();write(folder/'before.json',before)
    engine=sa.create_engine(settings.mysql_dsn);created=[];checks=[]
    keys={'ops':rt.owned.read_key(rt.owned.load_state())}
    try:
        for role,roles,scope in [('admin',['admin'],{}),('auditor',['auditor'],{}),('foreign',['admin'],{'tenant_id':'foreign-handoff'}),('scoped',['admin'],{'dept_id':'ran'})]:
            key=secrets.token_urlsafe(30);actor='runtime-handoff-audit-'+role+'-'+uuid.uuid4().hex[:8]
            created.append((key,actor));keys[role]=key;create_api_key(key,actor,roles,**scope)
        with httpx.Client(base_url='http://127.0.0.1:8010',timeout=30,trust_env=False) as client:
            def req(case,method,path,role='admin',status=200,**kwargs):
                response=client.request(method,'/admin/version-workspace'+path,headers={'X-API-Key':keys.get(role,'invalid')},**kwargs)
                assert response.status_code==status,(case,response.status_code,response.text[:160])
                assert response.headers['cache-control']=='no-store'
                assert settings.llm_api_key not in response.text and settings.mysql_dsn not in response.text
                checks.append(dict(case=case,role=role,status=response.status_code));write(folder/'api-checks.json',checks)
                return response.json()
            def completed(job):
                for _ in range(180):
                    response=client.get('/admin/version-workspace/runtime-handoff-jobs/'+job['id'],headers={'X-API-Key':keys['admin']})
                    response.raise_for_status();record=response.json()
                    if record['state'] not in ('queued','running'):break
                    time.sleep(1)
                assert record['state']=='completed',record
                return record['result']
            for role in ('ops','foreign','scoped','invalid'):
                req(role+'-handoff-list-denied','GET','/runtime-handoffs',role,status=401 if role=='invalid' else 403)
            req('auditor-cannot-prepare','POST','/runtime-handoffs','auditor',status=403,json={'version':rt.VERSION})
            req('extra-profile-and-approval-rejected','POST','/runtime-handoffs',status=422,json={'version':rt.VERSION,'approved':True,'profile':{'mysql_dsn':'not-accepted'}})
            plan=completed(req('current-complete-handoff','POST','/runtime-handoffs',status=202,json={'version':rt.VERSION}))
            assert len(plan['inspection']['profiles'])==2 and not plan['execution_authorized']
            assert plan['inspection']['profiles'][rt.VERSION]['snapshot']==before['active']
            assert plan['inspection']['profiles'][rt.OLD]['profile']['doc_collection']==rt.BASE.doc_collection
            assert plan['inspection']['profiles'][rt.OLD]['settings_field_count']==len(settings.model_dump())
            write(folder/'handoff-plan.json',plan)
            req('auditor-reads-prepared-handoff','GET','/runtime-handoffs','auditor')
            req('other-admin-cannot-revalidate','POST','/runtime-handoffs/'+plan['id']+'/revalidate','scoped',status=403,json={})
            validated=completed(req('full-pair-fresh-revalidation','POST','/runtime-handoffs/'+plan['id']+'/revalidate',status=202,json={}))
            assert validated['state']=='revalidated' and not validated['execution_authorized']
            delta=completed(req('delta-full-profile-gap','POST','/runtime-handoffs',status=202,json={'version':'local-work-eb06bef4017f'}))
            assert any('增量' in b for b in delta['inspection']['execution_blockers'])
            assert delta['inspection']['target']['pending_count']==1 and not delta['execution_authorized']
            write(folder/'delta-handoff.json',delta)
            req('original-publish-still-blocked','POST','/plans',status=409,json={'version':'local-work-eb06bef4017f','action':'publish'})
            req('original-rollback-still-explicit','POST','/plans',status=409,json={'version':rt.OLD,'action':'rollback'})
            # Exactly matching an admitted FAQ verifies that private Milvus probes
            # have not closed the live QA channel; it never requests generation.
            with (rt.merge.CANDIDATE/'raw/faq_revision_01.csv').open(encoding='utf-8',newline='') as stream:query=next(csv.DictReader(stream))['question']
            response=client.post('/qa',headers={'X-API-Key':keys['ops']},json={'query':query})
            assert response.status_code==200 and response.json()['route']=='faq_exact'
            checks.append(dict(case='existing-faq-after-private-probes',status=200,paid_calls=0))
        browser=subprocess.run(['node',str(rt.PROJECT/'tests/browser/runtime_handoff_smoke.js')],
            input=json.dumps(dict(url='http://127.0.0.1:8010',reportDir=str(folder),keys=keys,current=rt.VERSION)),
            capture_output=True,encoding='utf-8',timeout=300)
        (folder/'browser.log').write_text(browser.stdout+browser.stderr,encoding='utf-8')
        assert browser.returncode==0,'Browser verification failed; evidence retained'
        after=snapshot();write(folder/'after.json',after);assert before==after
        for source in (rt.merge.CANDIDATE,rt.merge.REPORT,rt.REVIEW,rt.PROJECT/'data/candidates/agent-domain-review-r1'):verify_seal(source)
        readonly.verify_admission(readonly.profile());uploads.verify_candidate(settings,uploads.get_candidate('local-work-eb06bef4017f'),writable=True)
        write(folder/'verification.json',dict(passed=True,api_checks=len(checks),full_profile_pair_checked=True,
            original_active_unchanged=True,protected_snapshots_unchanged=True,new_admissions=0,paid_calls=0,
            actual_switch_executed=False,pending_not_enabled=True,complete_handoff_prepared=True,
            live_handoff_execution_implemented=False,independent_quality_verified=False))
    finally:
        with engine.begin() as conn:
            for key,actor in created:conn.execute(sa.text('DELETE FROM api_keys WHERE key_hash=:h AND user_id=:u'),{'h':hash_key(key),'u':actor})
            remaining=conn.execute(sa.text('SELECT COUNT(*) FROM api_keys WHERE user_id LIKE :p'),{'p':'runtime-handoff-audit-%'}).scalar_one()
        engine.dispose();write(folder/'cleanup.json',dict(temporary_keys_remaining=remaining,paid_budget_unchanged=digest(budget)==before['budget']))
    write(REPORT/'latest-success.json',dict(attempt=folder.name));print('Complete profile preparation and browser passed; original ACTIVE/content fixed; paid calls 0.',flush=True)


if __name__=='__main__':main()
