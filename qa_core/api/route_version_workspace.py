"""Authenticated review/gate/release plans; original serving config is never flipped."""
import threading
import uuid
from fastapi import APIRouter, Depends, HTTPException, Response
from fastapi.routing import APIRoute
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from pydantic import BaseModel, ConfigDict, Field
from qa_core.api.auth import get_auth_context
from qa_core.api.route_ingest_workspace import reader as ingest_reader, writer
from qa_core.config.settings import get_settings
from qa_core.quality.version_workspace import Workspace, now

def reader(ctx):
    ingest_reader(ctx)
    if not set(ctx.roles)&{'admin','auditor'}: raise HTTPException(403,'版本审核仅管理员与审计员可查看')

def capabilities(ctx):
    readable=True; writable=True
    try: reader(ctx)
    except HTTPException: readable=False
    try: writer(ctx)
    except HTTPException: writable=False
    return {'can_read_versions':readable,'can_manage_versions':writable,'roles':ctx.roles,'tenant_id':ctx.tenant_id}

class GuardedRoute(APIRoute):
    def get_route_handler(self):
        handler=super().get_route_handler()
        async def guarded(request):
            try: return await handler(request)
            except RequestValidationError:
                return JSONResponse({'detail':'参数校验未通过；不接受额外字段'},status_code=422,headers={'Cache-Control':'no-store'})
            except HTTPException as error:
                error.headers=dict(error.headers or {},**{'Cache-Control':'no-store'}); raise
            except Exception:
                raise HTTPException(503,'版本工作区依赖、记录或绑定不可用；未完成该操作',headers={'Cache-Control':'no-store'}) from None
        return guarded

class Strict(BaseModel): model_config=ConfigDict(extra='forbid')
class ReviewBody(Strict): version:str=Field(min_length=1,max_length=32)
class GateBody(Strict): review_id:str=Field(pattern=r'^[a-f0-9]{32}$')
class PlanBody(ReviewBody): action:str=Field(pattern=r'^(publish|rollback)$')
class ExecuteBody(Strict): plan_id:str=Field(pattern=r'^[a-f0-9]{32}$'); confirmation:str=Field(max_length=64)
class HandoffBody(Strict): version:str=Field(min_length=1,max_length=32)

class HandoffManager:
    def __init__(self): self.slot=threading.BoundedSemaphore(1);self.instance=uuid.uuid4().hex
    def start(self,ws,actor,*,version=None,ident=None):
        from qa_core.quality.runtime_handoff import Preparer
        preparer=Preparer(ws)
        if ident:
            previous=ws.read('runtime-handoffs',ident)
            if previous['actor']!=actor:raise HTTPException(403,'只能重新核验本人准备的交接')
            import time
            if previous['expires_at']<time.time():raise HTTPException(409,'交接过期，请重新准备')
        if not self.slot.acquire(blocking=False):raise HTTPException(409,'完整交接检查正在进行，请稍后手动重试')
        job=dict(id=uuid.uuid4().hex,actor=actor,version=version,previous=ident,instance=self.instance,
            state='queued',created_at=now(),scope='full_configuration_preparation_only',paid_calls=0,execution_authorized=False)
        try:ws.write('runtime-handoff-jobs',job)
        except Exception:self.slot.release();raise
        def work():
            try:
                job.update(state='running');ws.write('runtime-handoff-jobs',job)
                result=preparer.revalidate(ident,actor) if ident else preparer.prepare(version,actor)
                job.update(state='completed',result=result,finished_at=now())
            except Exception as error:
                if isinstance(error, HTTPException):
                    detail = error.detail
                else:
                    detail = ('完整配置、门禁或依赖核验未通过（' + type(error).__name__ + '）：'
                              + str(error).replace(chr(10), ' ')[:300] + '；未切换版本')
                job.update(state='blocked' if isinstance(error,(HTTPException,ValueError)) else 'failed',
                    error=detail, finished_at=now())
            finally:
                try:ws.write('runtime-handoff-jobs',job)
                finally:self.slot.release()
        response={k:v for k,v in job.items() if k!='instance'}
        threading.Thread(target=work,name='runtime-handoff-preparation',daemon=True).start()
        return response
    def read(self,ws,ident):
        job=ws.read('runtime-handoff-jobs',ident)
        if job['state'] in ('queued','running') and job['instance']!=self.instance:
            job.update(state='interrupted',error='服务已重启；请重新准备交接，不自动继续或切换');ws.write('runtime-handoff-jobs',job)
        return {k:v for k,v in job.items() if k!='instance'}

class Manager:
    def __init__(self): self.slot=threading.BoundedSemaphore(1); self.instance=uuid.uuid4().hex
    def start(self,ws,review_id,actor):
        review=ws.read('reviews',review_id)
        if review['actor']!=actor: raise HTTPException(403,'审核发起人不匹配')
        if review['inspection']['status']!='STAGED': raise HTTPException(409,'仅STAGED候选可运行门禁')
        if not self.slot.acquire(blocking=False): raise HTTPException(409,'已有门禁任务，请等待后手动重试')
        job=dict(id=uuid.uuid4().hex,version=review['version'],review_id=review_id,actor=actor,instance=self.instance,
            state='queued',created_at=now(),completed=0,total=5,paid_calls=0,new_admissions=0)
        try: ws.write('jobs',job)
        except Exception: self.slot.release(); raise
        def work():
            try:
                job.update(state='running',started_at=now());ws.write('jobs',job)
                result=ws.gate(review_id,actor)
                job.update(state='passed' if result['passed'] else 'blocked',completed=5,result=result,finished_at=now())
            except Exception as error:
                job.update(state='failed',error=error.detail if isinstance(error,HTTPException) else '门禁依赖或记录不可用（'+type(error).__name__+'）；请刷新并重新审核',finished_at=now())
            finally:
                try: ws.write('jobs',job)
                finally: self.slot.release()
        response={k:v for k,v in job.items() if k!='instance'}
        threading.Thread(target=work,name='version-workspace-gate',daemon=True).start()
        return response
    def read(self,ws,ident):
        job=ws.read('jobs',ident)
        if job['state'] in ('queued','running') and job['instance']!=self.instance:
            job.update(state='interrupted',error='服务已重启，任务结果不确定；请刷新报告并重新审核，不自动重跑');ws.write('jobs',job)
        return {k:v for k,v in job.items() if k!='instance'}

def make_router(provider):
    def no_store(response:Response):response.headers['Cache-Control']='no-store'
    router=APIRouter(prefix='/admin/version-workspace',tags=['version-workspace'],route_class=GuardedRoute,dependencies=[Depends(no_store)])
    manager=Manager()
    handoffs=HandoffManager()
    @router.get('/capabilities')
    def caps(ctx=Depends(get_auth_context)):return capabilities(ctx)
    @router.get('/summary')
    def summary(ctx=Depends(get_auth_context)):
        """按角色分级的只读摘要。

        管理员/审计员：版本状态与近期发布/回滚操作；
        其他角色：仅当前激活版本名与状态计数，不含操作明细与账号信息。
        """
        import json as _json
        import sqlalchemy as sa
        privileged = bool(set(ctx.roles) & {'admin', 'auditor'})
        engine=sa.create_engine(get_settings().mysql_dsn,pool_pre_ping=True)
        with engine.connect() as conn:
            versions=[dict(row) for row in conn.execute(sa.text(
                'SELECT version,status FROM kb_versions ORDER BY version')).mappings()]
            rows=[]
            if privileged:
                rows=[dict(row) for row in conn.execute(sa.text(
                    "SELECT id,action,actor,payload_json,created_at FROM audit_log "
                    "WHERE action IN ('publish','rollback','activate') ORDER BY id DESC LIMIT 10")).mappings()]
        for row in rows:
            payload=_json.loads(row.pop('payload_json'))
            row['from_version']=payload.get('from_version',payload.get('from'));row['to_version']=payload.get('to_version',payload.get('to'))
        active=next((item['version'] for item in versions if item['status']=='ACTIVE'),None)
        if not privileged:
            counts={'ACTIVE':0,'STAGED':0,'ARCHIVED':0}
            for item in versions: counts[item['status']]=counts.get(item['status'],0)+1
            return {'active':active,'counts':counts,'versions':[],'recent_operations':[],
                    'note':'只读摘要：发布、回滚与操作明细仅管理员与审计员可查看。'}
        return {'active':active,'versions':versions,'recent_operations':rows,
                'note':'只读摘要：发布、回滚与完整交接需管理员或审计员执行；门禁与来源细节不对其他账号开放。'}
    @router.post('/runtime-handoffs',status_code=202)
    def prepare_handoff(body:HandoffBody,ctx=Depends(get_auth_context)):
        writer(ctx);return handoffs.start(provider(),ctx.user_id,version=body.version)
    @router.get('/runtime-handoffs')
    def list_handoffs(ctx=Depends(get_auth_context)):
        reader(ctx);ws=provider()
        return {'items':[ws.read('runtime-handoffs',path.stem) for path in sorted(ws.path('runtime-handoffs').glob('*.json'),key=lambda p:p.stat().st_mtime,reverse=True)[:10]],
                'jobs':[handoffs.read(ws,path.stem) for path in sorted(ws.path('runtime-handoff-jobs').glob('*.json'),key=lambda p:p.stat().st_mtime,reverse=True)[:10]],
                'execution_authorized':False,'scope':'仅完整配置准备与恢复交接；没有发布或回滚执行接口'}
    @router.get('/runtime-handoff-jobs/{ident}')
    def handoff_job(ident:str,ctx=Depends(get_auth_context)):
        reader(ctx);return handoffs.read(provider(),ident)
    @router.post('/runtime-handoffs/{ident}/revalidate',status_code=202)
    def revalidate_handoff(ident:str,ctx=Depends(get_auth_context)):
        writer(ctx);return handoffs.start(provider(),ctx.user_id,ident=ident)
    @router.get('')
    def index(ctx=Depends(get_auth_context)):
        reader(ctx);return dict(provider().versions(),**capabilities(ctx))
    @router.get('/versions/{version}')
    def detail(version:str,ctx=Depends(get_auth_context)):
        reader(ctx);ws=provider();result=ws.inspection(version)
        recent=[]
        for path in sorted(ws.path('jobs').glob('*.json'),key=lambda p:p.stat().st_mtime,reverse=True)[:20]:
            record=manager.read(ws,path.stem)
            if record['version']==version:recent.append(record)
        return dict(result,gate_jobs=recent,packages=ws.packages(version))
    @router.get('/versions/{version}/sources/{filename}')
    def source(version:str,filename:str,ctx=Depends(get_auth_context)):
        writer(ctx);return provider().source(version,filename)
    @router.get('/versions/{version}/gate')
    def gate_report(version:str,ctx=Depends(get_auth_context)):
        reader(ctx);return provider().gate_report(version)
    @router.post('/reviews',status_code=201)
    def review(body:ReviewBody,ctx=Depends(get_auth_context)):
        writer(ctx);return provider().review(body.version,ctx.user_id)
    @router.post('/gate-jobs',status_code=202)
    def gate(body:GateBody,ctx=Depends(get_auth_context)):
        writer(ctx);return manager.start(provider(),body.review_id,ctx.user_id)
    @router.post('/packages',status_code=201)
    def package(body:GateBody,ctx=Depends(get_auth_context)):
        writer(ctx);return provider().prepare_package(body.review_id,ctx.user_id)
    @router.get('/gate-jobs/{ident}')
    def job(ident:str,ctx=Depends(get_auth_context)):
        reader(ctx);return manager.read(provider(),ident)
    @router.post('/plans',status_code=201)
    def plan(body:PlanBody,ctx=Depends(get_auth_context)):
        writer(ctx);return provider().plan(body.version,body.action,ctx.user_id)
    @router.post('/executions')
    def execute(body:ExecuteBody,ctx=Depends(get_auth_context)):
        writer(ctx);return provider().execute(body.plan_id,body.confirmation,ctx.user_id)
    @router.get('/history')
    def history(ctx=Depends(get_auth_context)):
        reader(ctx);ws=provider()
        with ws.engine() as engine,engine.connect() as conn:
            import sqlalchemy as sa
            rows=[dict(r) for r in conn.execute(sa.text("SELECT id,action,actor,payload_json,created_at FROM audit_log WHERE action IN ('publish','rollback','activate') ORDER BY id DESC LIMIT 30")).mappings()]
        # Old audit payloads can contain filesystem/profile information; show only
        # authoritative operation, actor, target and timestamp.
        import json
        for row in rows:
            payload=json.loads(row.pop('payload_json'))
            row['from_version']=payload.get('from_version',payload.get('from'));row['to_version']=payload.get('to_version',payload.get('to'))
        return {'items':rows,'scope':'当前库最近30次已提交版本操作；历史归属不转移'}
    return router

_workspaces={};_lock=threading.Lock()
def current_workspace():
    settings=get_settings();key=(settings.mysql_dsn,settings.doc_collection,settings.faq_collection,settings.data_dir,settings.reports_dir)
    with _lock:
        if key not in _workspaces:_workspaces[key]=Workspace(settings)
        return _workspaces[key]
router=make_router(current_workspace)
