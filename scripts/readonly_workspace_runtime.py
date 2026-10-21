"""Owned loopback service for the already admitted 16-row domain. No import/publish."""
import argparse
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
from contextlib import contextmanager
import httpx
import psutil
import sqlalchemy as sa
from scripts import legacy_demo_runtime as legacy
from scripts.prepare_legacy_projection import read, digest
from qa_core.config.settings import Settings, get_settings
from qa_core.storage.domain_dao import TABLE_KEYS

PROJECT=legacy.PROJECT
ROOT=PROJECT/'.runtime/readonly-workspace'
PORT=18021
MODULE='scripts.readonly_workspace_server'
write=legacy.write
CODE=('scripts/readonly_workspace_runtime.py','scripts/readonly_workspace_server.py',
      'qa_core/application/business_workspace.py','qa_core/api/route_business_workspace.py',
      'static/business-workspace.js','static/business-isolated.html','static/css/business-workspace.css','static/css/readonly-theme.css',
      'static/agent-workspace.js','static/evidence-viewer.js',
      'qa_core/application/agent_tools.py','qa_core/application/agent_diagnose.py',
      'qa_core/application/agent_relations.py','qa_core/application/domain_context_service.py',
      'qa_core/storage/domain_dao.py','qa_core/api/route_agent.py','qa_core/api/route_domain.py',
      'qa_core/api/auth.py','qa_core/storage/read_engine.py')

def profile():
    return Settings(_env_file=None,**legacy.build_profile(PORT))

def validate_profile(settings):
    if settings.model_dump()!=profile().model_dump():raise ValueError('只允许已批准库的固定只读隔离配置')

def verify_admission(settings):
    from scripts.execute_agent_domain_admission import validate_receipt, batches, ALLOW
    from scripts.prepare_agent_domain_review import digest as content_digest
    receipt=read(PROJECT/'data/candidates/agent-domain-review-r1.receipt.approved.json')
    candidate=validate_receipt(PROJECT/'data/candidates/agent-domain-review-r1',receipt)
    admission=read(PROJECT/'.runtime/test/reports/agent-domain-executed-20261009/admission.json')
    if (admission['status']!='committed' or admission['candidate_sha256']!=content_digest(candidate)
            or admission['receipt_sha256']!=content_digest(receipt)):
        raise ValueError('准入账本与具体批准不一致')
    engine=sa.create_engine(settings.mysql_dsn)
    try:
        with engine.connect() as conn:
            for table,records in batches(candidate):
                if conn.exec_driver_sql(f'SELECT COUNT(*) FROM `{table}`').scalar_one()!=admission['domain_after'][table]:
                    raise ValueError('领域范围发生变化')
                for expected in records:
                    keys=TABLE_KEYS[table]
                    actual=conn.execute(sa.text(f'SELECT * FROM `{table}` WHERE '+ ' AND '.join(f'`{k}`=:{k}' for k in keys)),{k:expected[k] for k in keys}).mappings().one()
                    for field,value in expected.items():
                        got=actual[field]
                        if field in {'attributes_json','payload_json','labels_json','role_scope'}:
                            got=json.loads(got) if isinstance(got,str) else got
                            value=json.loads(value) if isinstance(value,str) else value
                        if got!=value:raise ValueError('已准入字段变化')
            if conn.exec_driver_sql("SELECT version FROM kb_versions WHERE status='ACTIVE'").scalars().all()!=[legacy.VERSION]:
                raise ValueError('隔离知识版本变化')
            required=candidate['prerequisites']['knowledge_assets'][0]
            asset=conn.execute(sa.text('SELECT * FROM knowledge_assets WHERE asset_id=:id'),{'id':required['asset_id']}).mappings().one()
            for field,value in required.items():
                got=asset[field]
                if field=='role_scope' and isinstance(got,str):got=json.loads(got)
                if got!=value:raise ValueError('关联知识准入身份或权限变化')
    finally:engine.dispose()
    return {'approved_rows':16,'version':legacy.VERSION,'synthetic':True}

def bindings():return {name:digest(PROJECT/name) for name in CODE}
def state():return read(ROOT/'state.json') if (ROOT/'state.json').exists() else None
def owned_process(record):
    if not record or not record.get('pid'):return None
    try:
        process=psutil.Process(record['pid']);cmd=process.cmdline()
        if not process.is_running() or process.status()==psutil.STATUS_ZOMBIE:return None
        if (abs(process.create_time()-record['created_at'])>.01 or MODULE not in cmd
                or Path(process.cwd()).resolve()!=PROJECT.resolve()
                or '--instance' not in cmd or cmd[cmd.index('--instance')+1]!=record['instance']):
            raise ValueError('进程身份不符，禁止按端口结束进程')
        return process
    except psutil.NoSuchProcess:return None
def identity(record):return {'instance':record['instance'],'version':legacy.VERSION,'read_only':True,'generation_enabled':False}
def inspect():
    record=state()
    if not record or record['status']=='stopped':return {'status':'stopped'}
    if record.get('url')!=f'http://127.0.0.1:{PORT}' or record.get('actor')!='readonly-workspace-ops-'+record.get('instance',''):
        raise ValueError('运行入口与凭据归属不符')
    # 先判断归属进程是否还活着：进程已退出时，旧代码绑定不可作为“不可恢复”的理由，
    # 否则一次代码更新就会把 dead record 永久锁死（start 与 stop 都读不到 stopped）。
    if not owned_process(record):return {'status':'stale'}
    if bindings()!=record['code_bindings']:raise ValueError('隔离服务实现已变化，需要显式重启')
    try:
        response=httpx.get(record['url']+'/readonly/runtime',trust_env=False,timeout=4)
        if response.status_code!=200:return {'status':'starting' if record['status']=='starting' else 'unavailable'}
        if response.json()!=identity(record):raise ValueError('HTTP服务身份不符')
        return {'status':'running','pid':record['pid'],'url':record['url'],'version':legacy.VERSION,
                'generation_enabled':False,'read_only':True,'login_file':str(ROOT/'open-workspace.html')}
    except httpx.TransportError:return {'status':'starting' if record['status']=='starting' else 'unavailable'}
def public_status():
    try:
        result=inspect()
        return {k:v for k,v in result.items() if k not in {'pid','login_file'}}
    except (ValueError,OSError) as error:
        return {'status':'unavailable','reason':'隔离服务记录或实现绑定不可用：'+str(error)}
def apply():
    settings=profile();legacy.apply_profile(settings.model_dump());return get_settings()
def stop():
    record=state()
    if not record or record.get('status')=='stopped':return {'status':'stopped'}
    if record.get('actor')!='readonly-workspace-ops-'+record.get('instance',''):
        raise ValueError('凭据归属不符，禁止删除')
    process=owned_process(record)
    if process:
        process.terminate()
        try:process.wait(15)
        except psutil.TimeoutExpired:process.kill();process.wait(10)
    settings=profile();engine=sa.create_engine(settings.mysql_dsn)
    try:
        with engine.begin() as conn:
            conn.execute(sa.text('DELETE FROM api_keys WHERE key_hash=:h AND user_id=:u'),{'h':record['key_hash'],'u':record['actor']})
    finally:engine.dispose()
    for name in ('api-key.txt','open-workspace.html'):(ROOT/name).unlink(missing_ok=True)
    write(ROOT/'state.json',dict(status='stopped',stopped_at=datetime.now(timezone.utc).isoformat(),instance=record['instance']))
    return {'status':'stopped','owned_key_removed':True}
def launcher(record,key):
    target=record['url']+'/#local_demo_key='+key
    page='''<!doctype html><html lang="zh-CN"><meta charset="utf-8"><title>打开隔离只读工作区</title><meta name="viewport" content="width=device-width,initial-scale=1"><main><h1>隔离只读诊断与业务工作区</h1><p>已批准模拟快照；历史知识版本 test-legacy-demo-r1。禁用生成、同步与业务动作。</p><button id="open">进入工作区</button><p>本机运维凭据，仅限本人使用，请勿分享此文件。</p></main><script>document.getElementById('open').onclick=()=>location.replace(TARGET)</script></html>'''.replace('TARGET',json.dumps(target))
    (ROOT/'open-workspace.html').write_text(page,encoding='utf-8')
def start():
    if state() and state().get('status')!='stopped':
        status=inspect()
        if status['status']=='running':return status
        if status['status']=='stale':
            # 归属进程已退出：清掉死记录与旧凭据后再启动，避免代码更新后永久锁死。
            stop()
        else:
            raise ValueError('先停止已有归属进程再启动，保留失败记录')
    settings=apply();verify_admission(settings);legacy.verify_active(settings)
    with socket.socket() as probe:probe.bind(('127.0.0.1',PORT))
    ROOT.mkdir(parents=True,exist_ok=True)
    instance=secrets.token_hex(16);key=secrets.token_urlsafe(32)
    from qa_core.api.auth import create_api_key, hash_key
    record={'status':'starting','instance':instance,'actor':'readonly-workspace-ops-'+instance,
            'key_hash':hash_key(key),'url':f'http://127.0.0.1:{PORT}','code_bindings':bindings(),'pid':None}
    write(ROOT/'state.json',record);create_api_key(key,record['actor'],['ops'])
    (ROOT/'api-key.txt').write_text(key,encoding='utf-8')
    try:
        options={'creationflags':subprocess.CREATE_NO_WINDOW|subprocess.DETACHED_PROCESS} if os.name=='nt' else {'start_new_session':True}
        with (ROOT/'server.log').open('a',encoding='utf-8') as log:
            server=subprocess.Popen([sys.executable,'-m',MODULE,'--instance',instance],cwd=PROJECT,
                env=legacy.profile_environment(settings.model_dump()),stdin=subprocess.DEVNULL,stdout=log,stderr=log,close_fds=True,**options)
        record.update(pid=server.pid,created_at=psutil.Process(server.pid).create_time());write(ROOT/'state.json',record)
        deadline=time.monotonic()+120
        while time.monotonic()<deadline:
            if server.poll() is not None:
                write(ROOT/'server-exit.json',{'exit_code':server.returncode,'at':datetime.now(timezone.utc).isoformat()})
                raise RuntimeError('隔离服务启动退出，请查看专属日志')
            if inspect()['status']=='running':
                record['status']='running';write(ROOT/'state.json',record);launcher(record,key);return inspect()
            time.sleep(1)
        raise TimeoutError('隔离服务启动超时')
    except Exception:
        stop();raise

def prepare_storage():
    """Existing owned storage only. No compose-wide up, applications or new volumes."""
    ROOT.mkdir(parents=True,exist_ok=True)
    def stage(phase,**extra):write(ROOT/'bootstrap.json',dict(phase=phase,at=datetime.now(timezone.utc).isoformat(),**extra))
    stage('docker_check')
    if subprocess.run(['docker','info','--format','{{.ServerVersion}}'],capture_output=True,timeout=10).returncode:
        stage('failed',reason='Docker未启动或不可访问');raise RuntimeError('Docker不可用')
    names=[f'telecom-ops-rag-test-{name}-1' for name in ('mysql','redis','etcd','minio','milvus')]
    result=subprocess.run(['docker','inspect',*names],capture_output=True,text=True,encoding='utf-8',timeout=10)
    if result.returncode:stage('failed',reason='已有隔离存储容器缺失，禁止自行替换数据卷');raise RuntimeError('已有隔离容器缺失')
    for container,name in zip(json.loads(result.stdout),('mysql','redis','etcd','minio','milvus')):
        labels=container['Config'].get('Labels',{})
        if labels.get('com.docker.compose.project')!='telecom-ops-rag-test' or labels.get('com.docker.compose.service')!=name:
            stage('failed',reason='隔离存储归属不符');raise ValueError('隔离存储归属变化')
    stage('storage_start',scope='仅既有test五存储；保留卷')
    subprocess.run(['docker','start',*names],check=True,capture_output=True,timeout=30)
    deadline=time.monotonic()+100
    while time.monotonic()<deadline:
        result=subprocess.run(['docker','inspect','--format','{{.State.Health.Status}}',*names],capture_output=True,text=True,timeout=10)
        states=result.stdout.splitlines()
        stage('storage_warmup',healthy=sum(v=='healthy' for v in states),total=5)
        if result.returncode==0 and states==['healthy']*5:stage('storage_ready');return
        time.sleep(3)
    stage('failed',reason='隔离存储健康检查超时');raise TimeoutError('隔离依赖预热超时')

def prepared_start():
    prepare_storage();result=start();write(ROOT/'bootstrap.json',{'phase':'ready','at':datetime.now(timezone.utc).isoformat()});return result

@contextmanager
def lifecycle_lock():
    ROOT.mkdir(parents=True,exist_ok=True)
    with (ROOT/'lifecycle.lock').open('a+b') as lock:
        lock.seek(0);lock.write(b'0');lock.flush();lock.seek(0)
        if os.name=='nt':
            import msvcrt
            msvcrt.locking(lock.fileno(),msvcrt.LK_NBLCK,1)
            try:yield
            finally:lock.seek(0);msvcrt.locking(lock.fileno(),msvcrt.LK_UNLCK,1)
        else:
            import fcntl
            fcntl.flock(lock.fileno(),fcntl.LOCK_EX|fcntl.LOCK_NB)
            try:yield
            finally:fcntl.flock(lock.fileno(),fcntl.LOCK_UN)
def main():
    parser=argparse.ArgumentParser();parser.add_argument('action',choices=('start','prepare-start','status','stop'));args=parser.parse_args()
    try:
        if args.action=='status':result=inspect()
        else:
            with lifecycle_lock():result={'start':start,'prepare-start':prepared_start,'stop':stop}[args.action]()
        print(json.dumps(result,ensure_ascii=False))
    except Exception as error:
        ROOT.mkdir(parents=True,exist_ok=True)
        failure={'error_type':type(error).__name__,'at':datetime.now(timezone.utc).isoformat(),'action':args.action}
        write(ROOT/'failure.json',failure)
        (ROOT/'failures').mkdir(exist_ok=True);write(ROOT/'failures'/(secrets.token_hex(8)+'.json'),failure)
        print('只读工作区操作失败（'+type(error).__name__+'）；保留状态并查看专属日志。',file=sys.stderr);sys.exit(1)
if __name__=='__main__':main()
