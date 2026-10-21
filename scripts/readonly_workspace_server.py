"""Minimal isolated application: no QA generation, admin writes, sync or ingestion."""
import argparse
from pathlib import Path
import uvicorn
from fastapi import FastAPI, Depends
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from scripts import readonly_workspace_runtime as rt

def main():
    parser=argparse.ArgumentParser();parser.add_argument('--instance',required=True);args=parser.parse_args()
    record=rt.state()
    if record['instance']!=args.instance or record['code_bindings']!=rt.bindings():raise ValueError('服务归属或实现绑定变化')
    settings=rt.apply();rt.verify_admission(settings);rt.legacy.verify_active(settings)
    from qa_core.api.route_agent import router as agent
    from qa_core.api.route_business_workspace import router as business
    from qa_core.api.route_domain import router as domain
    from qa_core.api.route_kb import router as kb
    from qa_core.api.route_evidence import router as evidence
    from qa_core.api.auth import get_auth_context
    from qa_core.api.route_health import router as health
    from qa_core.api.route_admin import admin_capabilities
    from fastapi import Response
    app=FastAPI(title='隔离只读诊断与业务工作区')
    for router in (health,business,domain,agent,kb,evidence):app.include_router(router)
    allowed_get=('/health','/admin/capabilities','/readonly/runtime','/business/','/domain/',
                 '/kb/versions','/kb/evidence/','/kb/catalog','/agent/runs')
    @app.middleware('http')
    async def read_guard(request,call_next):
        path=request.url.path
        if not (path=='/' or path.startswith('/static/') or
                request.method=='GET' and any(path==p or p.endswith('/') and path.startswith(p) for p in allowed_get) or
                request.method=='POST' and path in {'/agent/diagnose','/kb/search-evidence'}):
            return JSONResponse({'detail':'隔离工作区不提供生成、同步、发布或业务写操作'},status_code=403,headers={'Cache-Control':'no-store'})
        if path.startswith(('/agent/','/domain/','/business/','/kb/')):
            try:
                if rt.bindings()!=record['code_bindings']:raise ValueError('实现变化')
                # Run blocking verification off the event loop; independent bounded SQL reads.
                from starlette.concurrency import run_in_threadpool
                await run_in_threadpool(rt.verify_admission,settings)
            except Exception:
                return JSONResponse({'detail':'已准入快照或实现绑定变化，停止发布读取结果'},status_code=503,headers={'Cache-Control':'no-store'})
        response=await call_next(request)
        if path.startswith(('/agent/','/domain/','/business/','/kb/')) and response.status_code==200:
            try:
                if rt.bindings()!=record['code_bindings']:raise ValueError('实现变化')
                await run_in_threadpool(rt.verify_admission,settings)
            except Exception:
                return JSONResponse({'detail':'结束核对发现快照或绑定变化，读取结果已丢弃'},status_code=503,headers={'Cache-Control':'no-store'})
        response.headers['Cache-Control']='no-store';return response
    @app.get('/admin/capabilities')
    def capabilities(response:Response,ctx=Depends(get_auth_context)):
        result=admin_capabilities(response,ctx)
        for flag in ('can_read_ops','can_read_eval','can_read_permissions','can_manage_keys'):result[flag]=False
        return result
    @app.get('/readonly/runtime')
    def identity():return rt.identity(record)
    @app.get('/')
    def index():return FileResponse(rt.PROJECT/'static/business-isolated.html',headers={'Cache-Control':'no-store'})
    app.mount('/static',StaticFiles(directory=rt.PROJECT/'static'),name='static')
    uvicorn.run(app,host='127.0.0.1',port=rt.PORT,log_level='warning',access_log=False)
if __name__=='__main__':main()
