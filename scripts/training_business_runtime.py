"""Presentation upgrade preserving readonly profile, admission, API and lifecycle identity."""
from datetime import datetime,timezone
import argparse
import json
import os
import secrets
import socket
import subprocess
import time
import psutil
from scripts import readonly_workspace_runtime as core
from scripts.prepare_legacy_projection import read,write,digest

CODE=('qa_core/presentation/training_workspace.py','scripts/training_business_runtime.py','scripts/training_business_server.py')


def presentation_bindings():return {n:digest(core.PROJECT/n) for n in CODE}
def verify_presentation(record):
    if record.get('presentation_bindings')!=presentation_bindings() or record.get('presentation_mode')!='training_workspace':
        raise ValueError('training presentation binding changed')
    if record['code_bindings']!=core.bindings():raise ValueError('readonly baseline changed')


def owned_tree(record):
    processes={}
    for item in record.get('processes',[dict(pid=record.get('pid'),created_at=record.get('created_at'))]):
        p=core.owned_process({**record,**item})
        if p:processes[p.pid]=p
    for p in list(processes.values()):
        for child in p.children(recursive=True):
            check=core.owned_process({**record,'pid':child.pid,'created_at':child.create_time()})
            if check:processes[child.pid]=check
    return sorted(processes.values(),key=lambda p:p.create_time(),reverse=True)


def stop():
    record=core.state()
    if not record or record.get('status')=='stopped':return {'status':'stopped'}
    for process in owned_tree(record):
        try:process.terminate();process.wait(15)
        except psutil.NoSuchProcess:pass
        except psutil.TimeoutExpired:process.kill();process.wait(10)
    # Existing controller still owns the same profile/actor/key and performs cleanup.
    return core.stop()


def status():
    record=core.state()
    if record and record.get('status')=='running' and record.get('presentation_mode')=='training_workspace':
        verify_presentation(record)
    result=core.inspect()
    result['presentation_mode']=record.get('presentation_mode','legacy') if record else 'none'
    return result


def launcher(record,key):
    target=record['url']+'/#local_demo_key='+key
    page='''<!doctype html><html lang="zh-CN"><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>业务与只读诊断工作区</title><h1>业务与只读诊断工作区</h1><p>查看告警、设备、工单、变更和关联知识。</p><button id="open">进入工作区</button><p>本机登录凭据，请勿分享此文件。</p><script>document.getElementById('open').onclick=()=>location.replace(TARGET)</script></html>'''.replace('TARGET',json.dumps(target))
    (core.ROOT/'open-workspace.html').write_text(page,encoding='utf-8')


def start():
    selected=core.apply();core.verify_admission(selected);core.legacy.verify_active(selected)
    for name in ('business-isolated.html','business-workspace.js','agent-workspace.js'):
        from qa_core.presentation.training_workspace import display_asset
        display_asset(name,(core.PROJECT/'static'/name).read_text(encoding='utf-8'))
    record=core.state()
    if record and record.get('status')!='stopped':
        if record.get('presentation_mode')=='training_workspace':
            verify_presentation(record)
            if core.inspect()['status']=='running':return status()
        stop()
    with socket.socket() as probe:probe.bind(('127.0.0.1',core.PORT))
    core.ROOT.mkdir(parents=True,exist_ok=True)
    from qa_core.api.auth import create_api_key,hash_key
    instance=secrets.token_hex(16);key=secrets.token_urlsafe(32)
    record=dict(status='starting',instance=instance,actor='readonly-workspace-ops-'+instance,key_hash=hash_key(key),
        url=f'http://127.0.0.1:{core.PORT}',code_bindings=core.bindings(),presentation_bindings=presentation_bindings(),
        presentation_mode='training_workspace',pid=None)
    write(core.ROOT/'state.json',record);create_api_key(key,record['actor'],['ops'])
    (core.ROOT/'api-key.txt').write_text(key,encoding='utf-8')
    try:
        with (core.ROOT/'server.log').open('a',encoding='utf-8') as log:
            process=subprocess.Popen([str(core.PROJECT/'.venv/Scripts/pythonw.exe'),'-c',
                'from scripts.training_business_server import main; main()',
                '--entry',core.MODULE,'--instance',instance],cwd=core.PROJECT,
                env=core.legacy.profile_environment(selected.model_dump()),stdin=subprocess.DEVNULL,stdout=log,stderr=log,
                close_fds=True,creationflags=subprocess.CREATE_NO_WINDOW|subprocess.DETACHED_PROCESS)
        # Record both redirector and real server. Existing controller uses the real
        # server PID so stopping it cannot leave the service child orphaned.
        launcher_pid=process.pid;deadline=time.monotonic()+100
        while time.monotonic()<deadline:
            if process.poll() is not None:raise RuntimeError('training workspace exited before readiness')
            parent=psutil.Process(launcher_pid);tree=[parent]+parent.children(recursive=True)
            server=tree[-1]
            record.update(pid=server.pid,created_at=server.create_time(),processes=[dict(pid=p.pid,created_at=p.create_time()) for p in tree])
            write(core.ROOT/'state.json',record)
            if core.inspect()['status']=='running':
                record['status']='running';write(core.ROOT/'state.json',record);launcher(record,key);return status()
            time.sleep(1)
        raise TimeoutError('training workspace readiness timeout')
    except Exception:stop();raise


def main():
    parser=argparse.ArgumentParser();parser.add_argument('action',choices=['start','status','stop']);args=parser.parse_args()
    if args.action=='status':result=status()
    else:
        with core.lifecycle_lock():result={'start':start,'stop':stop}[args.action]()
    print(json.dumps(result,ensure_ascii=False))


if __name__=='__main__':main()
