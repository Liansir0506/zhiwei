"""Current-profile candidate workspace; concrete admission stays separate."""
from pathlib import Path
import sqlalchemy as sa
from fastapi import APIRouter, Depends, Header, HTTPException, Request, Response
from fastapi.routing import APIRoute
from pydantic import BaseModel, Field, ConfigDict
from qa_core.api.auth import get_auth_context, require_global_admin, require_roles
from qa_core.config.settings import get_settings
from qa_core.ingest import workspace as ws
from qa_core.ingest.jobs import IngestJobDao


def no_store(response: Response): response.headers['Cache-Control'] = 'no-store'
class WorkspaceRoute(APIRoute):
    def get_route_handler(self):
        handler = super().get_route_handler()
        async def guarded(request):
            try: return await handler(request)
            except HTTPException as error:
                error.headers = dict(error.headers or {}, **{'Cache-Control': 'no-store'}); raise
            except (OSError, ValueError, sa.exc.SQLAlchemyError) as error:
                raise HTTPException(503, '工作区记录、绑定或存储依赖不可用（'+type(error).__name__+'）', headers={'Cache-Control':'no-store'}) from None
        return guarded
router = APIRouter(prefix='/admin/ingest-workspace', tags=['ingest-workspace'], route_class=WorkspaceRoute, dependencies=[Depends(no_store)])


def reader(ctx):
    require_roles(ctx, ('ops', 'auditor', 'admin'))
    if ctx.tenant_id != 'default': raise HTTPException(403, '此工作区为default租户的隔离本地候选')
    if ctx.dept_ids or ctx.device_types or ctx.security_levels:
        raise HTTPException(403, '受限凭据不能查看全局入库工作区')


def writer(ctx):
    require_global_admin(ctx)
    if ctx.dept_ids or ctx.device_types or ctx.security_levels:
        raise HTTPException(403, '受限凭据不能修改全局候选')


def jobs(settings): return IngestJobDao(settings.mysql_dsn)


@router.get('')
def overview(ctx=Depends(get_auth_context)):
    reader(ctx); settings = get_settings()
    engine = sa.create_engine(settings.mysql_dsn)
    try:
        with engine.connect() as conn:
            versions = [dict(row) for row in conn.execute(sa.text('SELECT version,status FROM kb_versions ORDER BY version DESC')).mappings()]
    finally: engine.dispose()
    candidates = []
    for row in versions:
        if row['status'] != 'STAGED': continue
        item = dict(row, managed=False, compatible=False, reason='历史候选，不属于本可写工作区；不允许上传', label=row['version'])
        if row['version'].startswith('local-work-'):
            try:
                candidate = ws.get_candidate(row['version']); ws.verify_candidate(settings, candidate)
                item.update(managed=True, compatible=True, reason='集合、准入包及处理配置匹配', label=candidate['label'])
            except (ValueError, HTTPException, OSError):
                item.update(managed=True, reason='候选与当前配置不兼容或记录不可用，请新建候选')
        candidates.append(item)
    dao = jobs(settings)
    try:
        records = [ws.public_upload(record, dao.get(record['job_id'], 'default') if record['job_id'] else None)
                   for record in ws.uploads()[:50]]
    finally: dao.engine.dispose()
    worker = ws.worker_status()
    try: worker['compatible'] = worker.get('binding_sha256') == ws.digest(ws.profile_binding(settings))
    except (ValueError, OSError): worker['compatible'] = False
    return {'candidates': candidates, 'uploads': records, 'worker': worker,
        'can_write': ctx.tenant_id == 'default' and 'admin' in ctx.roles and not (ctx.dept_ids or ctx.device_types or ctx.security_levels),
        'scope': '独立可写暂存及STAGED候选；新资料待审，相同获准原文沿用准入；不会自动发布'}


class CandidateRequest(BaseModel):
    model_config = ConfigDict(extra='forbid')
    label: str = Field(min_length=1, max_length=80)


@router.post('/candidates', status_code=201)
def create_candidate(body: CandidateRequest, ctx=Depends(get_auth_context)):
    writer(ctx)
    if not body.label.strip(): raise HTTPException(400, '候选名称不能为空')
    return ws.create_candidate(get_settings(), ctx.user_id, body.label.strip())


@router.post('/uploads', status_code=202)
async def upload(request: Request, kb_version: str, source_id: str, x_filename: str = Header(...), ctx=Depends(get_auth_context)):
    writer(ctx)
    # Reject sealed/unmanaged candidates before allocating a body or creating files.
    candidate = ws.get_candidate(kb_version); ws.verify_candidate(get_settings(), candidate, writable=True)
    chunks, size = [], 0
    async for chunk in request.stream():
        size += len(chunk)
        if size > 20*1024*1024: raise HTTPException(413, '文件超过20MB')
        chunks.append(chunk)
    record, created = ws.upload(get_settings(), ctx, kb_version, x_filename, source_id, b''.join(chunks))
    return dict(ws.public_upload(record), created=created)


@router.get('/uploads/{upload_id}')
def upload_detail(upload_id: str, ctx=Depends(get_auth_context)):
    reader(ctx); record = ws.get_upload(upload_id); dao = jobs(get_settings())
    try:
        job = dao.get(record['job_id'], 'default') if record['job_id'] else None
        return dict(ws.public_upload(record, job), ledger=ws.ledger(get_settings(), record['kb_version']))
    finally: dao.engine.dispose()


@router.post('/uploads/{upload_id}/retry', status_code=202)
def retry(upload_id: str, ctx=Depends(get_auth_context)):
    writer(ctx); settings = get_settings(); record = ws.get_upload(upload_id)
    candidate = ws.get_candidate(record['kb_version']); ws.verify_candidate(settings, candidate, writable=True)
    dao = jobs(settings)
    try:
        if not record['job_id']: raise HTTPException(409, '待审核资料没有可重试的任务')
        current = dao.get(record['job_id'], 'default')
        if not current or current['status'] not in ('failed', 'cancelled'):
            raise HTTPException(409, '仅失败或取消的任务可重试')
        # Recheck bytes and admission only after verifying this is a retry.
        ws.queue(settings, record)
        from qa_core.ingest.write_guard import candidate_write
        with candidate_write(record['kb_version'], settings=settings):
            retried = dao.retry(record['job_id'], 'default')
        return ws.public_upload(record, retried)
    finally: dao.engine.dispose()


@router.post('/uploads/{upload_id}/cancel')
def cancel(upload_id: str, ctx=Depends(get_auth_context)):
    writer(ctx); record = ws.get_upload(upload_id); dao = jobs(get_settings())
    try:
        if not record['job_id']: raise HTTPException(409, '待审核资料尚未排队，无执行任务可取消')
        current = dao.get(record['job_id'], 'default')
        if not current or current['status'] in ('failed', 'success', 'cancelled'): raise HTTPException(409, '任务已终止')
        return ws.public_upload(record, dao.request_cancel(record['job_id'], 'default'))
    finally: dao.engine.dispose()
