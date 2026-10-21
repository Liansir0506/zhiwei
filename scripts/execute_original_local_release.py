"""Execute the reviewed original two-step local release through authenticated HTTP."""
import argparse
from datetime import datetime, timezone
import json
from pathlib import Path
import secrets
import socket
import subprocess
import tempfile

import httpx
import sqlalchemy as sa
from scripts.original_local_runtime import (PROJECT,ROOT,REVIEW,BASELINE,SUCCESSOR,build_profile,apply_profile,
    validate_review,load_state,launch,stop,inspect,read_key,command_lock)
from scripts.prepare_legacy_projection import read,write,digest,seal,verify_seal
from scripts.build_original_rollback_baseline import REPORT as STAGE,COLLECTIONS
from scripts.prepare_original_citation_review import sql_snapshot
from scripts.migrate_legacy_collections import read_rows,fingerprint

REPORT=PROJECT/'.runtime/test/reports/original-local-release-20261009'

def fingerprint_collections(settings,names):
    from pymilvus import MilvusClient
    from scripts.prepare_original_citation_release import wait_readable_collections
    client=MilvusClient(uri=settings.milvus_uri,timeout=120)
    try:
        readiness=wait_readable_collections(client,names)
        result={name:{'rows':len(rows),'sha256':fingerprint(rows)} for name in names for rows in [read_rows(client,name)]}
        return result,readiness
    finally:client.close()

def assert_preflight(before):
    expected=read(STAGE/'original-sql-after.json')
    if before!=expected:raise ValueError('original protected SQL drifted since completed STAGED verification')
    records=before['kb_versions']['records']
    if [r['version'] for r in records if r['status']=='ACTIVE']!=['v4-foundation-r3']:
        raise ValueError('requires the exact unique legacy ACTIVE before bootstrap')
    if any(next(r for r in records if r['version']==v)['status']!='STAGED' for v in (BASELINE,SUCCESSOR)):
        raise ValueError('both reviewed unpublished STAGED versions required')

def samples(version):
    from qa_core.retrieval.filters import _to_doc
    from qa_core.schemas.evidence import evidence_of
    from scripts.original_citation_candidate import CANDIDATE
    rows=read(STAGE/'expected-rows.json');positions=read(STAGE/'expected-locations.json')
    metadata=read(CANDIDATE/'raw/meta.json');result=[]
    for group in ('faq','fault_case','guide','sop','manual'):
        assets={m['asset_id'] for m in metadata.values() if m['doc_type']==group}
        row=next(r for r in rows.values() if r['kb_version']==version and r['asset_id'] in assets)
        doc=_to_doc({'id':row['id'],'entity':row});doc.extra['source_location']=positions[row['id']]
        result.append({'group':group,'doc_type':row['doc_type'],'reference':evidence_of(doc,'faq' if group=='faq' else 'doc')})
    return result

def active(http,key):
    response=http.get('/kb/versions',headers={'X-API-Key':key});response.raise_for_status()
    return [v['version'] for v in response.json()['versions'] if v['status']=='ACTIVE']

def verify_http(http,keys,version):
    if active(http,keys['ops'])!=[version]:raise ValueError('actual original unique ACTIVE mismatch')
    checks=[]
    for sample in samples(version):
        ref=sample['reference'];statuses={}
        for role in ('admin','ops','foreign'):
            response=http.get(ref['source_url'],headers={'X-API-Key':keys[role],'X-User-Roles':'admin','X-Tenant-ID':'default'})
            statuses[role]=response.status_code
            if role!='foreign' and (response.status_code!=200 or response.json()!=ref):
                raise ValueError('actual original reference mismatch: '+version+'/'+sample['group']+'/'+role)
        statuses['no_key']=http.get(ref['source_url']).status_code
        if statuses!={'admin':200,'ops':200,'foreign':404,'no_key':401}:raise ValueError('actual original citation ACL mismatch')
        checks.append({**sample,'statuses':statuses})
    query=http.post('/kb/search-evidence',json={'query':'AMF注册成功率下降，排查时先核对哪些信息？','top_n':5},headers={'X-API-Key':keys['ops']})
    if query.status_code!=200 or query.json().get('status')!='EVIDENCE_FOUND' or query.json().get('kb_version')!=version:
        raise ValueError('actual default original retrieval failed')
    for ref in query.json()['evidence']:
        response=http.get(ref['source_url'],headers={'X-API-Key':keys['ops']});response.raise_for_status()
        for name in ('text','location','document_id','source_revision','kb_version'):
            if response.json()[name]!=ref[name]:raise ValueError('retrieved original citation content differs')
    sop=http.post('/kb/search-evidence',json={'query':'ALM-AMF-REG SOP 处置流程','top_n':5},headers={'X-API-Key':keys['ops']})
    if sop.status_code!=200 or sop.json().get('reason')!='missing_safety_controls':raise ValueError('original SOP safety refusal lost')
    return {'passed':True,'version':version,'citations':checks,'default_query':query.json(),'sop_refusal':sop.json()}

def assert_after(before,after,actor):
    for table in before:
        if table!='kb_versions' and before[table]!=after[table]:raise ValueError('protected original SQL changed: '+table)
    old={r['version']:r for r in before['kb_versions']['records']}
    new={r['version']:r for r in after['kb_versions']['records']}
    if set(old)!=set(new):raise ValueError('release changed version roster')
    allowed={'v4-foundation-r3':('ARCHIVED',{'status','updated_at'}),BASELINE:('ARCHIVED',{'status','published_at','activated_by','updated_at'}),
             SUCCESSOR:('ACTIVE',{'status','published_at','activated_by','updated_at'})}
    for version,row in new.items():
        if version not in allowed:
            if row!=old[version]:raise ValueError('unrelated version changed')
            continue
        status,fields=allowed[version]
        if row['status']!=status or any(row.get(k)!=old[version].get(k) for k in set(row)|set(old[version]) if k not in fields):
            raise ValueError('unexpected release version delta: '+version)
        expected_actor=actor[version] if isinstance(actor,dict) and version in (BASELINE,SUCCESSOR) else actor
        if version in (BASELINE,SUCCESSOR) and row['activated_by']!=expected_actor:raise ValueError('authenticated release actor differs')

def browser(http,ops_key):
    source=samples(SUCCESSOR)
    output=REPORT/'browser';output.mkdir()
    with tempfile.TemporaryDirectory(prefix='original-published-edge-',dir=PROJECT/'.runtime/test') as profile:
        if not Path(profile).resolve().is_relative_to(PROJECT/'.runtime/test'):raise ValueError('owned Edge profile boundary')
        with socket.socket() as connection:connection.bind(('127.0.0.1',0));debug=connection.getsockname()[1]
        payload={'url':'http://127.0.0.1:8010','edge':'C:/Program Files (x86)/Microsoft/Edge/Application/msedge.exe',
            'profile':profile,'debugPort':debug,'key':ops_key,'version':SUCCESSOR,'status':'ACTIVE',
            'sources':[dict(s['reference'],id=s['reference']['chunk_id'],doc_type=s['doc_type'],score=.8) for s in source],
            'reportDir':str(output)}
        run=subprocess.run(['node','tests/browser/original_citation_staged_smoke.js'],input=json.dumps(payload),
            encoding='utf-8',text=True,capture_output=True,timeout=90,creationflags=subprocess.CREATE_NO_WINDOW)
        (output/'browser.log').write_text(run.stdout+run.stderr,encoding='utf-8')
        if run.returncode:raise RuntimeError('original published source browser failed; inspect browser.log')
    return read(output/'browser.json')

def main():
    parser=argparse.ArgumentParser(description=__doc__);parser.add_argument('--execute',action='store_true',required=True)
    args=parser.parse_args()
    plan=validate_review();settings=build_profile();apply_profile(settings)
    if REPORT.exists():raise ValueError('preserve release attempt; never blindly repeat activation')
    REPORT.mkdir()
    write(REPORT/'authorization.json',{'user_instruction':'下一步','context':'after explicit two-step release and 8010 approval request',
        'authorization_interpretation':'execute the concretely reviewed next step','scope':plan['scope'],
        'plan_sha256':digest(REVIEW/'release-plan.json'),'recorded_at_utc':datetime.now(timezone.utc).isoformat(),
        'paid_allowed':False,'production_allowed':False,'agent_allowed':False})
    engine=sa.create_engine(settings.mysql_dsn,pool_pre_ping=True);keys={};temp_actors={};state=None
    baseline_accepted=False;success=False;actions=[];before=None
    from qa_core.api.auth import create_api_key,hash_key
    try:
        with command_lock(engine):
            before,_=sql_snapshot(engine);assert_preflight(before);write(REPORT/'original-sql-before.json',before)
            protected=read(STAGE/'protected-vectors-after.json');paired=read(STAGE/'new-collection-fingerprints.json')
            vectors,ready=fingerprint_collections(settings,[*protected,*COLLECTIONS])
            if vectors!={**protected,**paired}:raise ValueError('protected/new vectors changed before release')
            write(REPORT/'vectors-before.json',vectors);write(REPORT/'collection-readiness.json',ready)
            from qa_core.quality.publication import verify_publication
            for version in (BASELINE,SUCCESSOR):
                gate=verify_publication(settings,version,str(STAGE/'quality'/('quality_'+version+'.json')))
                write(REPORT/('preflight-'+version+'.json'),gate)
            print('Fresh original protected SQL/vector and both publication preflights passed',flush=True)
            state=launch(settings,engine);keys['ops']=read_key(state)
            for role in ('admin','foreign'):
                keys[role]=secrets.token_urlsafe(32);temp_actors[role]='original-release-'+role+'-'+state['instance']
                create_api_key(keys[role],temp_actors[role],['admin'],tenant_id='other' if role=='foreign' else 'default')
            with httpx.Client(base_url=state['url'],timeout=240,trust_env=False) as http:
                for role,code in ((None,401),('ops',403),('foreign',403)):
                    response=http.post('/kb/activate',json={'version':BASELINE},headers={} if role is None else {'X-API-Key':keys[role]})
                    if response.status_code!=code:raise ValueError('original publication ACL negative failed')
                def change(endpoint,version):
                    response=http.post(endpoint,json={'version':version},headers={'X-API-Key':keys['admin']})
                    entry={'endpoint':endpoint,'version':version,'http_status':response.status_code,'body':response.json()}
                    actions.append(entry);write(REPORT/'actions.json',actions)
                    if response.status_code!=200 or not response.json().get('cache_invalidation_ok'):
                        raise ValueError('original publication failed or committed cache invalidation failed')
                change('/kb/activate',BASELINE)
                baseline=verify_http(http,keys,BASELINE);write(REPORT/'baseline-http.json',baseline);baseline_accepted=True
                print('Original baseline legitimately ACTIVE and default query/citations/ACL/SOP accepted',flush=True)
                change('/kb/activate',SUCCESSOR)
                successor=verify_http(http,keys,SUCCESSOR);write(REPORT/'successor-http.json',successor)
                stream=http.post('/qa/sse',json={'query':'AMF注册成功率下降，排查时先核对哪些信息？','session_id':'original-release-'+state['instance']},headers={'X-API-Key':keys['ops']})
                if stream.status_code!=200:raise ValueError('published default SSE HTTP failed')
                events=[]
                for frame in stream.text.split('\n\n'):
                    label=next((line[6:].strip() for line in frame.splitlines() if line.startswith('event:')),None)
                    value='\n'.join(line[5:].strip() for line in frame.splitlines() if line.startswith('data:'))
                    if label and value:events.append({'event':label,'data':json.loads(value)})
                finals=[e['data'] for e in events if e['event']=='done']
                if len(finals)!=1 or finals[0]['status']!='ANSWERED' or finals[0].get('kb_version')!=SUCCESSOR:
                    raise ValueError('published citation SSE terminal/version failed')
                write(REPORT/'real-default-sse.json',{'passed':True,'controlled':False,'generation_enabled':False,'events':events})
                health=http.get('/health?refresh=1').json()
                if health['status']!='ok' or health['generation_enabled'] or health['agent_enabled']:raise ValueError('published safe runtime unhealthy')
                write(REPORT/'health.json',health)
                ui=browser(http,keys['ops']);write(REPORT/'browser-summary.json',ui)
                with engine.connect() as conn:
                    audits=[dict(r) for r in conn.execute(sa.text("SELECT action,actor,payload_json FROM audit_log WHERE actor=:a AND action IN ('activate','rollback') ORDER BY id"),{'a':temp_actors['admin']}).mappings()]
                if [a['action'] for a in audits]!=['activate','activate']:raise ValueError('actual release audit transitions differ')
                write(REPORT/'publication-audit.json',audits)
                after,_=sql_snapshot(engine);assert_after(before,after,temp_actors['admin']);write(REPORT/'original-sql-after.json',after)
                verified,ready=fingerprint_collections(settings,vectors)
                if verified!=vectors:raise ValueError('publication altered corpus vectors or rows')
                write(REPORT/'vectors-after.json',verified)
                rollback=verify_publication(settings,BASELINE,str(STAGE/'quality'/('quality_'+BASELINE+'.json')))
                write(REPORT/'archived-baseline-rollback-check.json',rollback)
                print('Successor ACTIVE; actual default SSE and source browser passed; archived baseline rollback preflight passed',flush=True)
                success=True
    except BaseException as error:
        failure={'error_type':type(error).__name__,'baseline_accepted':baseline_accepted,'actions':actions,
            'old_legacy_rollback_attempted':False,'original_pointer_forged':False}
        if state and keys.get('admin'):
            try:
                with httpx.Client(base_url=state['url'],timeout=180,trust_env=False) as http:
                    current=active(http,keys['admin']);failure['active_at_failure']=current
                    if baseline_accepted and current==[SUCCESSOR]:
                        response=http.post('/kb/rollback',json={'version':BASELINE},headers={'X-API-Key':keys['admin']})
                        failure['standard_baseline_rollback']={'status':response.status_code,'body':response.json()}
                        if response.status_code==200:failure['rollback_http_check']=verify_http(http,keys,BASELINE)
            except Exception as recovery:failure['recovery_error_type']=type(recovery).__name__
        if state:stop(engine,state)
        with engine.connect() as conn:failure['actual_active_final']=conn.execute(sa.text("SELECT version FROM kb_versions WHERE status='ACTIVE'")).scalars().all()
        write(REPORT/'failure.json',failure)
        raise
    finally:
        with engine.begin() as conn:
            for role,actor in temp_actors.items():
                conn.execute(sa.text('DELETE FROM api_keys WHERE key_hash=:h AND user_id=:u'),{'h':hash_key(keys[role]),'u':actor})
        engine.dispose()
    if success:
        write(REPORT/'release-summary.json',{'passed':True,'published':True,'original_active':SUCCESSOR,'baseline_archived':True,
            'real_rollback_eligibility_verified':True,'runtime':inspect(state),'temporary_admin_foreign_keys_deleted':True,
            'persistent_key_role':'ops','paid_calls':0,'generation_enabled':False,'agent_enabled':False,
            'source_vectors_sql_protected':True,'review_sha256':digest(REVIEW/'release-plan.json'),
            'code_sha256':{name:digest(PROJECT/name) for name in ('scripts/execute_original_local_release.py','scripts/original_local_runtime.py','scripts/original_local_server.py')}})
        print('Original two-step release completed; local 8010 retained with ops-only key',flush=True)

if __name__=='__main__':main()
