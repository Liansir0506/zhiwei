"""Real admitted-domain HTTP and browser regression; zero LLM/business writes."""
import sys
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
import hashlib
import json
import secrets
import subprocess
import uuid
import httpx
import sqlalchemy as sa
from scripts import readonly_workspace_runtime as rt
from scripts import content_quality_release_runtime as current
from scripts.prepare_legacy_projection import write,read,digest,verify_seal
from qa_core.api.auth import create_api_key,hash_key
from qa_core.application.domain_context_service import PRIVATE_FIELDS
from qa_core.quality.publication import capture_snapshot

REPORT=rt.PROJECT/'.runtime/test/reports/readonly-business-p2-20261012'

def sql_snapshot(settings):
    engine=sa.create_engine(settings.mysql_dsn)
    try:
        with engine.connect() as conn:
            tables=sorted(set(sa.inspect(conn).get_table_names())-{'api_keys','audit_log','access_audit'})
            result={}
            for table in tables:
                # Application availability observer and counters may change; business,
                # ingestion, publication and all knowledge locations must stay fixed.
                if table not in {'devices','alarms','monitoring_metrics','work_orders','change_records',
                    'departments','users','user_departments','knowledge_assets','knowledge_relations',
                    'adapter_sync_state','kb_versions','kb_version_manifests','evidence_locations',
                    'knowledge_source_withdrawals','ingest_jobs','ingest_records'}:continue
                rows=[dict(r) for r in conn.exec_driver_sql(f'SELECT * FROM `{table}`').mappings()]
                encoded=sorted(json.dumps(r,sort_keys=True,default=str,ensure_ascii=False) for r in rows)
                result[table]={'rows':len(rows),'sha256':hashlib.sha256(json.dumps(encoded,ensure_ascii=False).encode()).hexdigest()}
            return result
    finally:engine.dispose()

def main():
    attempt=REPORT/('attempt-'+uuid.uuid4().hex[:8]);attempt.mkdir(parents=True)
    iso=rt.apply();assert rt.inspect()['status']=='running';rt.verify_admission(iso)
    current.install_adapter();original=current.selected_settings()
    assert current.owned.inspect()['status']=='running'
    env=rt.PROJECT/'.env.runtime';budget=current.owned.ROOT/'generation-validation-budget.json'
    before={'original_sql':sql_snapshot(original),'isolated_sql':sql_snapshot(iso),
            'active':capture_snapshot(original,current.VERSION),'env':digest(env),'budget':digest(budget)}
    write(attempt/'before.json',before)
    seals=[current.merge.CANDIDATE,current.merge.REPORT,current.REVIEW,
           rt.PROJECT/'data/candidates/agent-domain-review-r1']
    for folder in seals:verify_seal(folder)
    created=[];keys={'ops':(rt.ROOT/'api-key.txt').read_text(encoding='utf-8')}
    engine=sa.create_engine(iso.mysql_dsn);suffix=uuid.uuid4().hex[:10]
    api=[]
    try:
        for label,roles,scope in [('admin',['admin'],{}),('auditor',['auditor'],{}),('guest',['guest'],{}),
            ('foreign',['ops'],{'tenant_id':'readonly-other'}),('department',['ops'],{'dept_id':'ran-ops','device_types':['AMF'],'security_levels':['internal']}),
            ('wrong_dept',['ops'],{'dept_id':'nope'}),('public',['ops'],{'security_levels':['public']})]:
            key=secrets.token_urlsafe(32);actor='readonly-business-audit-'+label+'-'+suffix
            created.append((key,actor));keys[label]=key;create_api_key(key,actor,roles,**scope)
        with httpx.Client(base_url=f'http://127.0.0.1:{rt.PORT}',timeout=150,trust_env=False) as client:
            def req(case,method,path,role='ops',expected=200,**kwargs):
                response=client.request(method,path,headers={'X-API-Key':keys.get(role,'invalid')},**kwargs)
                assert response.status_code==expected,(case,response.status_code,response.text[:100])
                assert response.headers.get('cache-control')=='no-store',(case,'missing no-store')
                data=response.json();api.append({'case':case,'role':role,'status':response.status_code,'response':data});write(attempt/'api.json',api);return data
            health=req('health','GET','/health');assert health['agent_enabled'] and not health['generation_enabled']
            for kind,expected_count in [('alarms',2),('devices',2),('tickets',1),('changes',1)]:
                data=req(kind+'-list','GET','/business/'+kind);assert len(data['items'])==expected_count
                for row in data['items']:assert not (PRIVATE_FIELDS&set(row))
                field={'alarms':'alarm_id','devices':'device_id','tickets':'ticket_id','changes':'change_id'}[kind]
                detail=req(kind+'-detail','GET','/business/'+kind+'/'+data['items'][0][field]);assert detail['synthetic']
                assert detail['entity'][field]==data['items'][0][field]
                for group in ('device','alarm','alarms','monitoring','work_orders','changes','knowledge'):
                    rows=detail['context'].get(group,[]);rows=[rows] if isinstance(rows,dict) else rows
                    assert all(not (PRIVATE_FIELDS&set(row)) for row in rows if row)
            first=req('pagination-first','GET','/business/devices?limit=1');assert len(first['items'])==1 and first['next_cursor']
            second=req('pagination-second','GET','/business/devices',params={'limit':1,'after':first['next_cursor']});assert len(second['items'])==1 and not second['next_cursor'];assert first['items'][0]['device_id']!=second['items'][0]['device_id']
            empty=req('literal-filter','GET','/business/alarms',params={'query':"%' OR 1=1 --"});assert not empty['items'] and 'total' not in empty
            d=req('scoped-department','GET','/business/devices','department');assert [r['device_id'] for r in d['items']]==['SIM-DEV-0001']
            for role in ('wrong_dept','foreign'):
                assert not req(role+'-empty','GET','/business/alarms',role)['items']
                req(role+'-hidden-detail','GET','/business/devices/SIM-DEV-0001',role,expected=404)
            for role in ('guest','public','invalid'):
                req(role+'-denied','GET','/business/devices',role,expected=401 if role=='invalid' else 403)
            for role in ('admin','auditor'):assert req(role+'-list','GET','/business/tickets',role)['items']
            adapters=req('integration-never-synced','GET','/business/integrations')['adapters'];assert len(adapters)==6
            assert all(not r['observed'] and r['sync_status']=='never_synced' and r['items_synced'] is None for r in adapters)
            req('scoped-integration-denied','GET','/business/integrations','department',expected=403)
            for path,body in [('/admin/integrations/sync',{}),('/kb/activate',{'version':'test-legacy-demo-r1'}),('/qa',{'query':'no paid call'}),('/admin/keys',{}),('/business/alarms',{})]:
                req('deny-write-'+path,'POST',path,'admin',expected=403,json=body)
            req('strict-agent-schema','POST','/agent/diagnose',expected=422,json={'query':'q','kb_version':'local-content-quality-r1'})
            req('agent-guest','POST','/agent/diagnose','guest',expected=403,json={'query':'ALM-AMF-REG 常见原因'})
            # Warm only local query models before the bounded diagnosis.
            warm=req('local-model-warmup','POST','/kb/search-evidence',json={'query':'ALM-AMF-REG 常见原因','top_n':1});assert warm['evidence']
            print('Real business/RBAC checks passed; local query models warmed.',flush=True)
            diag=req('domain-linked-record','POST','/agent/diagnose','department',json={'query':'请核对关联工单 SIM-TT-00001 的原文','alarm_id':'SIM-ALM-000001'})
            assert diag['status']=='ANSWERED' and diag['stop_reason']=='evidence_ready',(diag['status'],diag['stop_reason'])
            assert diag['kb_version']==rt.legacy.VERSION and diag['retrieval_scope']['strategy']=='linked_work_order'
            assert len(diag['sources'])==1 and len(diag['observations'])==5
            source=req('re-auth-source','GET','/kb/evidence/'+diag['sources'][0]['evidence_id'],'department')
            assert source['text']==diag['sources'][0]['text'] and source['source_revision']=='v4-foundation-r3'
            req('foreign-source-denied','GET','/kb/evidence/'+diag['sources'][0]['evidence_id'],'foreign',expected=404)
            clarify=req('missing-entity-clarify','POST','/agent/diagnose',json={'query':'关联工单的原文'});assert clarify['status']=='NEEDS_CLARIFICATION'
            unsafe=req('unsafe-operation','POST','/agent/diagnose',json={'query':'请重启设备执行配置变更'});assert unsafe['status']=='OUT_OF_SCOPE'
        current.owned.apply_profile(original);original_key=current.owned.read_key(current.owned.load_state())
        with httpx.Client(base_url='http://127.0.0.1:8010',headers={'X-API-Key':original_key},timeout=30,trust_env=False) as client:
            availability=client.get('/business/availability');assert availability.status_code==200 and not availability.json()['enabled_here']
            assert availability.json()['isolated']['status']=='running'
            assert client.get('/business/alarms').status_code==503
            health=client.get('/health').json();assert not health['agent_enabled'] and health['generation_enabled']
            write(attempt/'original-boundary.json',{'availability':availability.json(),'unadmitted_list_status':503,'agent_enabled':False,'generation_enabled':True})
        params={'project':str(rt.PROJECT),'reportDir':str(attempt),'url':f'http://127.0.0.1:{rt.PORT}',
                'loginUrl':(rt.ROOT/'open-workspace.html').as_uri(),'originalLoginUrl':(current.owned.ROOT/'open-platform.html').as_uri(),
                'keys':keys}
        browser=subprocess.run(['node',str(rt.PROJECT/'tests/browser/business_workspace_smoke.js')],input=json.dumps(params),capture_output=True,encoding='utf-8',timeout=540)
        (attempt/'browser.log').write_text(browser.stdout+browser.stderr,encoding='utf-8');assert browser.returncode==0,'Browser failed; preserved attempt evidence'
        print(browser.stdout.strip(),flush=True)
        for folder in seals:verify_seal(folder)
        rt.verify_admission(iso)
        after={'original_sql':sql_snapshot(original),'isolated_sql':sql_snapshot(iso),
               'active':capture_snapshot(original,current.VERSION),'env':digest(env),'budget':digest(budget)}
        write(attempt/'after.json',after);assert before==after,'Protected storage/config changed'
        proof=read(attempt/'browser-result.json')
        summary={'passed':True,'attempt':attempt.name,'api_checks':len(api),'browser_checks':len(proof['checks']),
                 'paid_calls':0,'business_writes':0,'new_admissions':0,'original_active':current.VERSION,
                 'isolated_version':rt.legacy.VERSION,'protected_snapshot_unchanged':True,'scope':'dev/regression, admitted simulated records; no independent quality claim'}
        write(attempt/'summary.json',summary);write(REPORT/'latest-success.json',summary);print(json.dumps(summary),flush=True)
    finally:
        with engine.begin() as conn:
            for key,actor in created:conn.execute(sa.text('DELETE FROM api_keys WHERE key_hash=:h AND user_id=:u'),{'h':hash_key(key),'u':actor})
        with engine.connect() as conn:remaining=sum(conn.execute(sa.text('SELECT COUNT(*) FROM api_keys WHERE user_id=:u'),{'u':actor}).scalar() for _,actor in created)
        engine.dispose();write(attempt/'cleanup.json',{'temporary_keys_remaining':remaining,'paid_budget_unchanged':digest(budget)==before['budget']});assert remaining==0
if __name__=='__main__':main()
