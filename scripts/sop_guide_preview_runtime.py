"""Own only the new readonly preview PID/key; original entrypoints stay untouched."""
import argparse
from datetime import datetime, timezone
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
from scripts.prepare_sop_evidence_admission import ROOT as PROJECT
from scripts.prepare_legacy_projection import read, write, digest
from scripts.sop_evidence_stage_profile import profile
from scripts.build_original_rollback_baseline import apply_settings

ROOT=PROJECT/'.runtime/sop-guide-preview'
PORT=18024
MODULE='scripts.sop_guide_preview_server'
CODE=('qa_core/quality/guide_preview_checks.py', 'scripts/sop_guide_preview_server.py',
      'scripts/sop_guide_preview_runtime.py','static/guide-preview.html','static/guide-preview.js','static/guide-preview.css',
      'qa_core/api/auth.py','qa_core/api/route_evidence.py','qa_core/application/evidence_service.py',
      'scripts/sop_evidence_stage_profile.py')

def bindings(): return {name:digest(PROJECT/name) for name in CODE}
def record(): return read(ROOT/'state.json') if (ROOT/'state.json').exists() else None
def owned_process(state):
    if not state or not state.get('pid'): return None
    try:
        proc=psutil.Process(state['pid']);command=proc.cmdline()
        if (abs(proc.create_time()-state['created_at'])>.01 or Path(proc.cwd()).resolve()!=PROJECT.resolve()
            or MODULE not in command or '--instance' not in command
            or command[command.index('--instance')+1]!=state['instance']):
            raise ValueError('preview process ownership mismatch')
        return proc if proc.is_running() else None
    except psutil.NoSuchProcess: return None

def identity(state):
    return dict(instance=state['instance'],version='test-sop-evidence-r1',read_only=True,generation_enabled=False,agent_enabled=False)

def owned_tree(state):
    """Validate every PID before touching the venv launcher or its real server."""
    processes={}
    for item in state.get('processes', [dict(pid=state.get('pid'),created_at=state.get('created_at'))]):
        process=owned_process({**state,**item})
        if process:processes[process.pid]=process
    for process in list(processes.values()):
        for child in process.children(recursive=True):
            child_record={**state,'pid':child.pid,'created_at':child.create_time()}
            validated=owned_process(child_record)
            if validated:processes[child.pid]=validated
    return sorted(processes.values(),key=lambda process:process.create_time(),reverse=True)

def inspect():
    state=record()
    if not state or state['status']=='stopped':return dict(status='stopped')
    if state['code_bindings']!=bindings():return dict(status='binding_changed')
    if not owned_process(state):return dict(status='stale')
    try:
        response=httpx.get(f'http://127.0.0.1:{PORT}/preview/runtime',timeout=3,trust_env=False)
        if response.status_code!=200 or response.json()!=identity(state):return dict(status='unavailable')
        return dict(status='running',pid=state['pid'],url=f'http://127.0.0.1:{PORT}',read_only=True,
                    login_file=str(ROOT/'open-preview.html'))
    except httpx.TransportError:return dict(status='starting')

def stop():
    state=record()
    if not state or state['status']=='stopped':return dict(status='stopped')
    if state.get('actor')!='sop-guide-preview-admin-'+state.get('instance',''):
        raise ValueError('preview credential ownership mismatch')
    processes=owned_tree(state)
    for process in processes:
        try:
            process.terminate()
            try:process.wait(15)
            except psutil.TimeoutExpired:process.kill();process.wait(10)
        except psutil.NoSuchProcess:
            # Windows venv launcher exits with the validated child; still clean its key.
            pass
    engine=sa.create_engine(profile().mysql_dsn)
    try:
        with engine.begin() as conn:
            conn.execute(sa.text('DELETE FROM api_keys WHERE key_hash=:h AND user_id=:u'),{'h':state['key_hash'],'u':state['actor']})
    finally:engine.dispose()
    for name in ('api-key.txt','open-preview.html'):(ROOT/name).unlink(missing_ok=True)
    write(ROOT/'state.json',dict(status='stopped',instance=state['instance'],stopped_at_utc=datetime.now(timezone.utc).isoformat()))
    return dict(status='stopped',owned_preview_key_removed=True)

def start():
    state=record()
    if state and state['status']!='stopped':
        current=inspect()
        if current['status']=='running':return current
        raise ValueError('inspect and stop owned previous attempt before restart')
    from qa_core.quality.guide_preview_checks import verify_live
    selected=profile();apply_settings(selected);verify_live(selected)
    with socket.socket() as probe:probe.bind(('127.0.0.1',PORT))
    ROOT.mkdir(parents=True,exist_ok=True)
    if ROOT.resolve()!=ROOT:raise ValueError('preview root links forbidden')
    from qa_core.api.auth import create_api_key,hash_key
    instance=secrets.token_hex(16);key=secrets.token_urlsafe(32)
    state=dict(status='starting',instance=instance,actor='sop-guide-preview-admin-'+instance,
               key_hash=hash_key(key),code_bindings=bindings(),pid=None)
    write(ROOT/'state.json',state)
    create_api_key(key,state['actor'],['admin'],tenant_id='default')
    (ROOT/'api-key.txt').write_text(key,encoding='utf-8')
    try:
        python=PROJECT/'.venv/Scripts/pythonw.exe'
        with (ROOT/'server.log').open('a',encoding='utf-8') as log:
            server=subprocess.Popen([str(python),'-m',MODULE,'--instance',instance],cwd=PROJECT,
                stdin=subprocess.DEVNULL,stdout=log,stderr=log,close_fds=True,
                creationflags=subprocess.CREATE_NO_WINDOW|subprocess.DETACHED_PROCESS)
        state.update(pid=server.pid,created_at=psutil.Process(server.pid).create_time());write(ROOT/'state.json',state)
        deadline=time.monotonic()+90
        while time.monotonic()<deadline:
            if server.poll() is not None:raise RuntimeError('preview start failed; safe report retained')
            if inspect()['status']=='running':
                state['processes']=[dict(pid=p.pid,created_at=p.create_time()) for p in owned_tree(state)]
                state['status']='running';write(ROOT/'state.json',state)
                target=f'http://127.0.0.1:{PORT}/#preview_key='+key
                page='''<!doctype html><html lang="zh-CN"><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>打开指南候选预览</title><h1>20份指南 · STAGED只读预览</h1><p>仅限本机使用。此文件包含新隔离候选的技术预览凭据，请勿分享。不是人工审核身份证明。</p><button id="open">进入指南预览</button><script>document.getElementById('open').onclick=()=>location.replace(TARGET)</script></html>'''.replace('TARGET',json.dumps(target))
                (ROOT/'open-preview.html').write_text(page,encoding='utf-8')
                return inspect()
            time.sleep(1)
        raise TimeoutError('preview readiness timeout')
    except Exception:
        stop();raise

if __name__=='__main__':
    parser=argparse.ArgumentParser();parser.add_argument('action',choices=['start','status','stop']);args=parser.parse_args()
    print(json.dumps({'start':start,'status':inspect,'stop':stop}[args.action](),ensure_ascii=False))
