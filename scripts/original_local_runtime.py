"""Owned original local citation entry. Lifecycle commands never publish or ingest."""
import argparse
from contextlib import contextmanager
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import secrets
import socket
import subprocess
import sys
import time

import httpx
import psutil
import sqlalchemy as sa
from qa_core.config.settings import Settings, get_settings
from scripts.build_original_rollback_baseline import BASELINE, SUCCESSOR, settings_for
from scripts.prepare_original_citation_release import public_profile
from scripts.prepare_legacy_projection import digest, read, verify_seal
from scripts.legacy_demo_runtime import write
from scripts.original_generation import generation_settings as _generation_settings

PROJECT=Path(__file__).resolve().parents[1]
ROOT=PROJECT/'.runtime/original-local'
REVIEW=PROJECT/'.runtime/test/reports/original-rollback-release-readiness-20261009'
SERVER_MODULE='scripts.original_local_server'

def build_profile():
    settings=settings_for().model_copy(update={'api_port':8010,'redis_url':'redis://127.0.0.1:6379/0'})
    settings=_generation_settings(settings,PROJECT,ROOT)
    from scripts.merged_release_runtime import selected_settings
    settings=selected_settings(settings)
    validate_profile(settings)
    return settings

def validate_profile(settings):
    expected=settings_for().model_copy(update={'api_port':8010,'redis_url':'redis://127.0.0.1:6379/0'})
    if public_profile(expected)!=read(REVIEW/'host-profile.json'):
        raise ValueError('host base profile differs from reviewed release')
    expected=_generation_settings(expected,PROJECT,ROOT)
    from scripts.merged_release_runtime import selected_settings
    expected=selected_settings(expected)
    if settings.model_dump()!=expected.model_dump():
        raise ValueError('exact reviewed original local runtime profile required')

def environment(settings):
    validate_profile(settings)
    env=dict(os.environ)
    env.pop('LLM_VALIDATION_BUDGET_FILE',None)
    for name in Settings.model_fields:env.pop(name.upper(),None)
    env.update({name.upper():str(value).lower() if isinstance(value,bool) else str(value)
                for name,value in settings.model_dump().items()})
    env.update(APP_ENV_FILE=os.devnull,TOKENIZERS_PARALLELISM='false',OMP_NUM_THREADS='4',MKL_NUM_THREADS='4',
        HF_HUB_OFFLINE='1',TRANSFORMERS_OFFLINE='1',APP_SCENARIOS_DIR=str(PROJECT/'scenarios'))
    if (ROOT/'generation-validation-budget.json').exists():
        env['LLM_VALIDATION_BUDGET_FILE']=str(ROOT/'generation-validation-budget.json')
    return env

def apply_profile(settings):
    os.environ.update(environment(settings));get_settings.cache_clear()
    if get_settings().model_dump()!=settings.model_dump():raise ValueError('inherited runtime settings changed')
    return settings

def validate_review():
    verify_seal(REVIEW)
    plan=read(REVIEW/'release-plan.json')
    if plan['baseline']!=BASELINE or plan['successor']!=SUCCESSOR or plan['scope']!='original_local_demo_only':
        raise ValueError('wrong original local release plan')
    if digest(REVIEW/'host-profile.json')!=plan['profile_sha256']:raise ValueError('host profile changed')
    for name,sha in plan['bindings'].items():
        path=(PROJECT/name).resolve()
        if not path.is_relative_to(PROJECT) or digest(path)!=sha:raise ValueError('review binding changed: '+name)
    return plan

def load_state():
    return read(ROOT/'state.json') if (ROOT/'state.json').exists() else None

def owned_process(state):
    if not state or not state.get('pid'):return None
    try:
        process=psutil.Process(state['pid'])
        if not process.is_running() or process.status()==psutil.STATUS_ZOMBIE:return None
        command=process.cmdline()
        def argument(name):
            return command[command.index(name)+1] if name in command and command.index(name)+1<len(command) else None
        if (abs(process.create_time()-state['created_at'])>.01 or SERVER_MODULE not in command
                or argument('--instance')!=state['instance'] or argument('--profile')!=str(ROOT/'profile.json')
                or Path(process.cwd()).resolve()!=PROJECT):
            raise ValueError('PID identity mismatch; refusing to stop unrelated process')
        return process
    except psutil.NoSuchProcess:return None

def read_key(state):
    key=(ROOT/'api-key.txt').read_text(encoding='utf-8')
    if hashlib.sha256(key.encode()).hexdigest()!=state['key_hash']:raise ValueError('owned key file changed')
    return key

def write_login_file(state):
    """Local private launcher; never expose a credential through an HTTP endpoint."""
    key=read_key(state)
    from urllib.parse import quote
    target='http://localhost:8010/#local_demo_key='+quote(key,safe='')
    page='''<!doctype html><html lang="zh-CN"><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>打开智维平台</title><style>body{font:16px system-ui;background:#f3f6fb;color:#19304f;margin:0;padding:48px}main{max-width:520px;margin:auto;background:white;padding:32px;border-radius:12px}button{background:#3168ed;color:white;border:0;border-radius:8px;padding:14px 24px;font:inherit;cursor:pointer}</style>
<main><h1>打开智维平台</h1><p>使用本机运维凭据进入平台，无需复制Key。</p><button id="open">进入平台</button><p>仅供本机使用，请勿分享此文件。</p></main><script>
document.getElementById('open').onclick=()=>location.replace(TARGET);
</script></html>'''.replace('TARGET',json.dumps(target))
    path=ROOT/'open-platform.html';path.write_text(page,encoding='utf-8')
    return str(path)

def inspect(state=None):
    state=state if state is not None else load_state()
    if not state or state.get('status')=='stopped':return {'status':'stopped','generation_enabled':False,'agent_enabled':False}
    settings=build_profile()
    if digest(ROOT/'profile.json')!=state['profile_sha256'] or read(ROOT/'profile.json')!=public_profile(settings):
        raise ValueError('owned public profile changed')
    process=owned_process(state)
    if process is None:return {'status':'stale','cleanup_required':True}
    identity=httpx.get(state['url']+'/local/runtime',timeout=5,trust_env=False)
    identity.raise_for_status()
    if identity.json()!={'instance':state['instance'],'generation_enabled':settings.generation_enabled,'agent_enabled':False}:
        raise ValueError('HTTP runtime identity mismatch')
    return {'status':'running','url':state['url'],'pid':process.pid,'generation_enabled':settings.generation_enabled,'agent_enabled':False,
        'key_file':str(ROOT/'api-key.txt'),'profile':str(ROOT/'profile.json')}

def terminate_owned(state):
    process=owned_process(state)
    if process:
        identities=[]
        for child in process.children(recursive=True):
            try:
                identity=dict(state,pid=child.pid,created_at=child.create_time())
                if owned_process(identity):identities.append(identity)
            except (ValueError,psutil.NoSuchProcess):continue
        for identity in [*reversed(identities),state]:
            target=owned_process(identity)
            if target is None:continue
            try:
                target.terminate();target.wait(timeout=20)
            except psutil.NoSuchProcess:pass
            except psutil.TimeoutExpired:
                target=owned_process(identity)
                if target:target.kill();target.wait(timeout=10)
def stop(engine,state=None):
    state=state if state is not None else load_state()
    if not state or state.get('status')=='stopped':return inspect(state or {'status':'stopped'})
    terminate_owned(state)
    if state.get('key_hash'):
        with engine.begin() as conn:
            conn.execute(sa.text("DELETE FROM api_keys WHERE key_hash=:h AND user_id=:u AND tenant_id='default'"),
                {'h':state['key_hash'],'u':state['actor']})
    (ROOT/'api-key.txt').unlink(missing_ok=True)
    (ROOT/'open-platform.html').unlink(missing_ok=True)
    result={'status':'stopped','instance':state['instance'],'owned_key_deleted':True,'server_stopped':True,
        'stopped_at_utc':datetime.now(timezone.utc).isoformat()}
    write(ROOT/'state.json',result);return result

def launch(settings,engine):
    """Private publisher bootstrap; public start independently verifies an ACTIVE first."""
    previous=load_state()
    if previous and previous.get('status')!='stopped':raise ValueError('owned runtime already exists; inspect/stop it first')
    with socket.socket() as probe:probe.bind(('127.0.0.1',8010))
    ROOT.mkdir(parents=True,exist_ok=True)
    write(ROOT/'profile.json',public_profile(settings))
    instance=secrets.token_hex(16);key=secrets.token_urlsafe(32);actor='original-local-ops-'+instance
    from qa_core.api.auth import create_api_key,hash_key
    apply_profile(settings)
    state={'status':'starting','instance':instance,'actor':actor,'key_hash':hash_key(key),
        'profile_sha256':digest(ROOT/'profile.json'),'pid':None,'url':'http://127.0.0.1:8010'}
    write(ROOT/'state.json',state)
    server=None
    try:
        (ROOT/'api-key.txt').write_text(key,encoding='utf-8')
        create_api_key(key,actor,['ops'],tenant_id='default')
        options={'creationflags':subprocess.CREATE_NO_WINDOW|subprocess.DETACHED_PROCESS} if os.name=='nt' else {'start_new_session':True}
        with (ROOT/'server.log').open('a',encoding='utf-8') as log:
            server=subprocess.Popen([sys.executable,'-m',SERVER_MODULE,'--instance',instance,'--profile',str(ROOT/'profile.json')],
                cwd=PROJECT,env=environment(settings),stdin=subprocess.DEVNULL,stdout=log,stderr=log,**options)
        state.update(pid=server.pid,created_at=psutil.Process(server.pid).create_time());write(ROOT/'state.json',state)
        deadline=time.monotonic()+150
        while time.monotonic()<deadline:
            if server.poll() is not None:raise RuntimeError('owned original server exited; inspect server.log')
            try:
                status=inspect(state)
                health=httpx.get(state['url']+'/health',timeout=5,trust_env=False).json()
                if health['status']!='ok' or health['generation_enabled']!=settings.generation_enabled or health['agent_enabled']:
                    raise ValueError('original dependencies or safe mode are not healthy')
                state['status']='running';write(ROOT/'state.json',state);write_login_file(state);return state
            except httpx.TransportError:time.sleep(.3)
        raise TimeoutError('owned original entry startup deadline')
    except BaseException:
        if server and not state.get('pid'):server.terminate();server.wait(timeout=10)
        stop(engine,state);raise

def verify_active(settings,engine):
    from qa_core.quality.publication import verify_publication
    with engine.connect() as conn:
        active=conn.execute(sa.text("SELECT version,quality_report_path FROM kb_versions WHERE status='ACTIVE'")).all()
    from scripts.merged_release_runtime import allowed_active_versions
    if len(active)!=1 or active[0][0] not in (BASELINE,SUCCESSOR,*allowed_active_versions()):raise ValueError('requires an already-published local baseline or approved successor')
    verify_publication(settings,*active[0]);return active[0][0]

def restart(settings,engine,*,previous_profile=None):
    """Restart an attested owned entry without rotating the user's working ops key."""
    state=load_state()
    if not state or state.get('status')!='running':raise ValueError('restart requires a recorded running owned entry')
    expected_profile=previous_profile if previous_profile is not None else public_profile(settings)
    if digest(ROOT/'profile.json')!=state['profile_sha256'] or read(ROOT/'profile.json')!=expected_profile:
        raise ValueError('owned public profile changed')
    key=read_key(state)
    from qa_core.api.auth import verify_api_key
    apply_profile(settings)
    ctx=verify_api_key(key)
    if not ctx or ctx.user_id!=state['actor'] or ctx.roles!=['ops'] or ctx.tenant_id!='default':
        raise ValueError('restart requires the exact owned ops key')
    verify_active(settings,engine)
    # Attest ownership before modifying the recovery record or stopping anything.
    if owned_process(state) is None:raise ValueError('owned process missing; use explicit stop/start recovery')
    terminate_owned(state)
    if previous_profile is not None:
        write(ROOT/'profile.json',public_profile(settings))
        state['profile_sha256']=digest(ROOT/'profile.json')
    state.update(status='starting',pid=None);write(ROOT/'state.json',state)
    server=None
    try:
        options={'creationflags':subprocess.CREATE_NO_WINDOW|subprocess.DETACHED_PROCESS} if os.name=='nt' else {'start_new_session':True}
        with (ROOT/'server.log').open('a',encoding='utf-8') as log:
            server=subprocess.Popen([sys.executable,'-m',SERVER_MODULE,'--instance',state['instance'],'--profile',str(ROOT/'profile.json')],
                cwd=PROJECT,env=environment(settings),stdin=subprocess.DEVNULL,stdout=log,stderr=log,**options)
        state.update(pid=server.pid,created_at=psutil.Process(server.pid).create_time());write(ROOT/'state.json',state)
        deadline=time.monotonic()+150
        while time.monotonic()<deadline:
            if server.poll() is not None:raise RuntimeError('restarted owned server exited; inspect server.log')
            try:
                inspect(state)
                health=httpx.get(state['url']+'/health',timeout=5,trust_env=False).json()
                if health['status']!='ok' or health['generation_enabled']!=settings.generation_enabled or health['agent_enabled']:
                    raise ValueError('restarted safe mode not healthy')
                state['status']='running';write(ROOT/'state.json',state);write_login_file(state);return inspect(state)
            except httpx.TransportError:time.sleep(.3)
        raise TimeoutError('owned restart startup deadline')
    except BaseException:
        if server and not state.get('pid'):server.terminate();server.wait(timeout=10)
        stop(engine,state);raise

@contextmanager
def command_lock(engine):
    with engine.connect() as conn:
        if conn.execute(sa.text("SELECT GET_LOCK('original_local_runtime',0)")).scalar_one()!=1:
            raise ValueError('another original local lifecycle/release is running')
        conn.commit()
        try:yield
        finally:
            conn.execute(sa.text("SELECT RELEASE_LOCK('original_local_runtime')"));conn.commit()

def main():
    parser=argparse.ArgumentParser(description=__doc__);parser.add_argument('action',choices=('start','status','stop','restart'))
    args=parser.parse_args();settings=build_profile();apply_profile(settings)
    engine=sa.create_engine(settings.mysql_dsn,pool_pre_ping=True)
    try:
        with command_lock(engine):
            if args.action=='stop':result=stop(engine)
            elif args.action=='status':result=inspect()
            elif args.action=='restart':result=restart(settings,engine)
            else:
                validate_review();version=verify_active(settings,engine)
                state=load_state()
                if state and state.get('status')=='running':result=inspect(state)
                else:result=inspect(launch(settings,engine))
                result['version']=version
                result['login_file']=write_login_file(load_state())
        print(json.dumps(result,ensure_ascii=False,indent=2))
    finally:engine.dispose()

if __name__=='__main__':
    try:main()
    except Exception as error:
        print('Original local command failed ('+type(error).__name__+'). Inspect runtime state and storage readiness.',file=sys.stderr)
        sys.exit(1)
