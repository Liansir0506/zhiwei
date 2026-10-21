"""Resume the owned completed-bootstrap release after a successful standard rollback."""
import argparse
import json
import secrets
import httpx
import sqlalchemy as sa
from scripts import execute_original_local_release as release
from scripts.original_local_runtime import (PROJECT,BASELINE,SUCCESSOR,build_profile,apply_profile,validate_review,
    launch,stop,inspect,read_key,command_lock)
from scripts.prepare_legacy_projection import read,write,digest
from scripts.prepare_original_citation_review import sql_snapshot
from scripts.build_original_rollback_baseline import REPORT as STAGE

FIRST=release.REPORT
REPORT=PROJECT/'.runtime/test/reports/original-local-release-resume-20261009'

def main():
    parser=argparse.ArgumentParser(description=__doc__);parser.add_argument('--execute',action='store_true',required=True);parser.parse_args()
    validate_review();settings=build_profile();apply_profile(settings)
    failed=read(FIRST/'failure.json')
    if (failed.get('actual_active_final')!=[BASELINE] or failed.get('standard_baseline_rollback',{}).get('status')!=200
            or not failed.get('rollback_http_check',{}).get('passed')):raise ValueError('owned successful baseline rollback proof required')
    if not read(FIRST/'baseline-http.json')['passed'] or not read(FIRST/'successor-http.json')['passed']:
        raise ValueError('both originally accepted HTTP stages required')
    if REPORT.exists():raise ValueError('preserve resumption attempt; never blindly repeat')
    REPORT.mkdir();release.REPORT=REPORT
    write(REPORT/'authorization.json',{'inherited':read(FIRST/'authorization.json'),'action':'resume reviewed successor after successful standard baseline rollback',
        'original_failed_attempt_preserved':True,'paid_calls':0})
    engine=sa.create_engine(settings.mysql_dsn,pool_pre_ping=True)
    state=None;keys={};actors={};success=False
    from qa_core.api.auth import create_api_key,hash_key
    try:
        with command_lock(engine):
            before,_=sql_snapshot(engine)
            if before!=read(FIRST/'original-sql-after-recovery.json'):raise ValueError('recovered protected SQL drifted')
            versions={r['version']:r for r in before['kb_versions']['records']}
            if [r['version'] for r in versions.values() if r['status']=='ACTIVE']!=[BASELINE] or versions[SUCCESSOR]['status']!='ARCHIVED':
                raise ValueError('exact recovered baseline ACTIVE/successor ARCHIVED required')
            write(REPORT/'original-sql-before.json',before)
            vectors,ready=release.fingerprint_collections(settings,read(FIRST/'vectors-before.json'))
            if vectors!=read(FIRST/'vectors-before.json'):raise ValueError('original vectors changed during first release')
            write(REPORT/'vectors-before.json',vectors);write(REPORT/'collection-readiness.json',ready)
            from qa_core.quality.publication import verify_publication
            for version in (BASELINE,SUCCESSOR):
                write(REPORT/('preflight-'+version+'.json'),verify_publication(settings,version,str(STAGE/'quality'/('quality_'+version+'.json'))))
            state=launch(settings,engine);keys['ops']=read_key(state)
            for role in ('admin','foreign'):
                keys[role]=secrets.token_urlsafe(32);actors[role]='original-resume-'+role+'-'+state['instance']
                create_api_key(keys[role],actors[role],['admin'],tenant_id='other' if role=='foreign' else 'default')
            with httpx.Client(base_url=state['url'],timeout=240,trust_env=False) as http:
                write(REPORT/'baseline-http.json',release.verify_http(http,keys,BASELINE))
                response=http.post('/kb/rollback',json={'version':SUCCESSOR},headers={'X-API-Key':keys['admin']})
                write(REPORT/'actions.json',[{'endpoint':'/kb/rollback','version':SUCCESSOR,'http_status':response.status_code,'body':response.json()}])
                if response.status_code!=200 or not response.json().get('cache_invalidation_ok'):raise ValueError('resume standard successor rollback failed')
                print('Existing archived successor restored through standard rollback; no forged status or repeated ingestion',flush=True)
                write(REPORT/'successor-http.json',release.verify_http(http,keys,SUCCESSOR))
                sse=http.post('/qa/sse',json={'query':'AMF注册成功率下降，排查时先核对哪些信息？','session_id':'original-resume-'+state['instance']},headers={'X-API-Key':keys['ops']})
                if sse.status_code!=200:raise ValueError('resumed SSE HTTP failed')
                events=[]
                for frame in sse.text.split('\n\n'):
                    name=next((line[6:].strip() for line in frame.splitlines() if line.startswith('event:')),None)
                    value='\n'.join(line[5:].strip() for line in frame.splitlines() if line.startswith('data:'))
                    if name and value:events.append({'event':name,'data':json.loads(value)})
                finals=[e['data'] for e in events if e['event']=='done']
                if len(finals)!=1 or finals[0]['status']!='ANSWERED' or finals[0].get('kb_version')!=SUCCESSOR:raise ValueError('resumed SSE terminal/version failed')
                write(REPORT/'real-default-sse.json',{'passed':True,'controlled':False,'generation_enabled':False,'events':events})
                write(REPORT/'browser-summary.json',release.browser(http,keys['ops']))
                health=http.get('/health?refresh=1').json()
                if health['status']!='ok' or health['generation_enabled'] or health['agent_enabled']:raise ValueError('resumed safe mode unhealthy')
                write(REPORT/'health.json',health)
            after,_=sql_snapshot(engine)
            expected_actors={BASELINE:versions[BASELINE]['activated_by'],SUCCESSOR:actors['admin']}
            release.assert_after(read(FIRST/'original-sql-before.json'),after,expected_actors)
            write(REPORT/'original-sql-after.json',after)
            final_vectors,_=release.fingerprint_collections(settings,vectors)
            if final_vectors!=vectors:raise ValueError('resumption altered corpus')
            write(REPORT/'vectors-after.json',final_vectors)
            write(REPORT/'archived-baseline-rollback-check.json',verify_publication(settings,BASELINE,str(STAGE/'quality'/('quality_'+BASELINE+'.json'))))
            with engine.connect() as conn:
                audits=[dict(r) for r in conn.execute(sa.text("SELECT action,actor,payload_json FROM audit_log WHERE actor=:a AND action IN ('activate','rollback') ORDER BY id"),{'a':actors['admin']}).mappings()]
            if [a['action'] for a in audits]!=['rollback']:raise ValueError('resumption authenticated audit differs')
            write(REPORT/'publication-audit.json',audits);success=True
    except BaseException as error:
        failure={'error_type':type(error).__name__,'legacy_rollback_attempted':False}
        if state and keys.get('admin'):
            try:
                with httpx.Client(base_url=state['url'],timeout=240,trust_env=False) as http:
                    if release.active(http,keys['admin'])==[SUCCESSOR]:
                        response=http.post('/kb/rollback',json={'version':BASELINE},headers={'X-API-Key':keys['admin']})
                        failure['standard_baseline_rollback']={'status':response.status_code,'body':response.json()}
            except Exception as recovery:failure['recovery_error_type']=type(recovery).__name__
        if state:stop(engine,state)
        with engine.connect() as conn:failure['actual_active_final']=conn.execute(sa.text("SELECT version FROM kb_versions WHERE status='ACTIVE'")).scalars().all()
        write(REPORT/'failure.json',failure);raise
    finally:
        with engine.begin() as conn:
            for role,actor in actors.items():conn.execute(sa.text('DELETE FROM api_keys WHERE key_hash=:h AND user_id=:u'),{'h':hash_key(keys[role]),'u':actor})
        engine.dispose()
    if success:
        write(REPORT/'release-summary.json',{'passed':True,'published':True,'original_active':SUCCESSOR,'baseline_archived':True,
            'real_rollback_eligibility_verified':True,'actual_original_baseline_rollback_passed':True,
            'runtime':inspect(state),'temporary_admin_foreign_keys_deleted':True,'persistent_key_role':'ops',
            'paid_calls':0,'generation_enabled':False,'agent_enabled':False,'source_vectors_sql_protected':True,
            'first_failed_attempt':str(FIRST),'automatic_updated_at_delta_accounted':True,
            'code_sha256':{name:digest(PROJECT/name) for name in ('scripts/execute_original_local_release.py','scripts/resume_original_local_release.py','scripts/original_local_runtime.py','scripts/original_local_server.py')}})
        print('Resumed original release passed; actual original rollback proven; 8010 retained with ops key',flush=True)

if __name__=='__main__':main()
