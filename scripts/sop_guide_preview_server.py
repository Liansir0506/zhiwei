"""Loopback admin preview of the admitted STAGED guides; no mutation/LLM routes."""
import argparse
import asyncio
import json
import os
from pathlib import Path
from fastapi import FastAPI, Depends, HTTPException, Request
from fastapi.responses import FileResponse, JSONResponse
from starlette.concurrency import run_in_threadpool
from starlette.middleware.trustedhost import TrustedHostMiddleware
from qa_core.api.auth import get_auth_context, require_global_admin
from qa_core.quality.guide_preview_checks import verify_live
from scripts.sop_evidence_stage_profile import profile
from scripts.build_original_rollback_baseline import apply_settings
from scripts.prepare_sop_evidence_admission import ROOT

VERSION = 'test-sop-evidence-r1'


def admin(ctx=Depends(get_auth_context)):
    require_global_admin(ctx)
    if ctx.dept_ids or ctx.device_types or ctx.security_levels:
        raise HTTPException(403, '此候选完整检查需要未限定访问域的本地管理员')
    return ctx


def create_app(instance, binding_check=lambda: None):
    selected = profile().model_copy(update={'api_host':'127.0.0.1','api_port':18024})
    apply_settings(selected)
    os.environ['HF_HUB_OFFLINE']='1'; os.environ['TRANSFORMERS_OFFLINE']='1'
    from qa_core.api.route_evidence import router
    app = FastAPI(docs_url=None, redoc_url=None, openapi_url=None)
    app.add_middleware(TrustedHostMiddleware, allowed_hosts=['127.0.0.1','localhost','testserver'])
    lock = asyncio.Lock()

    @app.middleware('http')
    async def read_boundary(request, call_next):
        path, method = request.url.path, request.method
        public = method == 'GET' and path in {'/', '/guide-preview.js', '/guide-preview.css', '/preview/runtime'}
        data = (method == 'GET' and (path in {'/preview/checks','/kb/catalog','/kb/capabilities'}
                or path.startswith('/kb/evidence/'))) or (method == 'POST' and path == '/kb/search-evidence')
        if not public and not data:
            response = JSONResponse({'detail':'此入口只提供已准入指南的管理员只读预览'}, status_code=403)
        elif public:
            response = await call_next(request)
        else:
            # Authenticate before snapshot work; arbitrary data routes stay closed.
            try:
                from qa_core.api.auth import authenticate_headers
                ctx=authenticate_headers(x_api_key=request.headers.get('x-api-key',''))
                admin(ctx)
                if path == '/kb/catalog' and request.query_params.get('kb_version') != VERSION:
                    raise HTTPException(409,'请显式选择此STAGED候选版本')
                if path == '/kb/search-evidence':
                    try: body = await request.json()
                    except (ValueError, TypeError): raise HTTPException(422,'检索请求格式错误') from None
                    if not isinstance(body,dict) or body.get('kb_version') != VERSION:
                        raise HTTPException(409,'请显式选择此STAGED候选版本')
                if path.startswith('/kb/evidence/'):
                    from qa_core.schemas.evidence import decode_reference
                    try: version=decode_reference(path.rsplit('/',1)[-1])[0]
                    except ValueError: raise HTTPException(404,'引用不存在或不可访问') from None
                    if version != VERSION: raise HTTPException(404,'引用不存在或不可访问')
                async with lock:
                    await run_in_threadpool(binding_check)
                    request.state.guide_checks = await run_in_threadpool(verify_live, selected)
                    response = await call_next(request)
                    await run_in_threadpool(binding_check)
                    await run_in_threadpool(verify_live, selected)
            except HTTPException as error:
                response=JSONResponse({'detail':error.detail},status_code=error.status_code)
            except Exception:
                response=JSONResponse({'detail':'候选内容、配置或依赖校验失败，已停止本次预览。请查看本机预览状态。'},status_code=503)
        response.headers['Cache-Control']='no-store'
        response.headers['Referrer-Policy']='no-referrer'
        response.headers['X-Content-Type-Options']='nosniff'
        response.headers['Content-Security-Policy']="default-src 'self'; script-src 'self'; style-src 'self'; connect-src 'self'; object-src 'none'; base-uri 'none'; frame-ancestors 'none'"
        return response

    @app.get('/preview/runtime')
    def runtime():
        return dict(instance=instance, version=VERSION, read_only=True, generation_enabled=False, agent_enabled=False)

    @app.get('/preview/checks', dependencies=[Depends(admin)])
    def checks(request: Request):
        return request.state.guide_checks

    @app.get('/kb/capabilities', dependencies=[Depends(admin)])
    def capabilities():
        return dict(can_preview_staged=True, can_upload=False, can_read_jobs=False, can_retry_upload=False,
                    can_retry_rebuild=False, can_publish=False, can_generate=False)

    app.include_router(router, dependencies=[Depends(admin)])
    def serve_file(name):
        def file_response(): return FileResponse(ROOT/'static'/name)
        return file_response
    for path, name in [('/', 'guide-preview.html'),('/guide-preview.js','guide-preview.js'),('/guide-preview.css','guide-preview.css')]:
        app.add_api_route(path, serve_file(name), methods=['GET'], include_in_schema=False)
    return app


if __name__ == '__main__':
    parser=argparse.ArgumentParser();parser.add_argument('--instance',required=True);args=parser.parse_args()
    from scripts import sop_guide_preview_runtime as rt
    def binding_check():
        state=rt.record()
        if not state or state['instance']!=args.instance or state['code_bindings']!=rt.bindings():
            raise ValueError('owned preview binding drift')
    binding_check(); verify_live(profile())
    import torch; torch.set_num_threads(4)
    import uvicorn
    uvicorn.run(create_app(args.instance,binding_check),host='127.0.0.1',port=rt.PORT,access_log=False)
