"""A bounded training view; the draft never becomes a knowledge source or SOP."""
import secrets
from fastapi import HTTPException
from fastapi.responses import HTMLResponse, JSONResponse, Response
from scripts.prepare_amf_sop_exercise import OUTPUT, validate

HEADERS = {'Cache-Control':'no-store','X-Content-Type-Options':'nosniff','Referrer-Policy':'no-referrer'}


def can_train(ctx):
    scope=ctx.scope
    return (ctx.source=='api_key' and scope.tenant_id=='default' and bool(set(scope.roles)&{'ops','admin'})
            and 'internal' in scope.security_levels and not scope.dept_ids and not scope.device_types)


def workspace_html(text):
    anchor='<button id="agentTab" type="button" hidden>只读诊断</button>'
    if text.count(anchor)!=1 or text.count('</main>')!=1:raise ValueError('Known workspace shape changed')
    text=text.replace(anchor,anchor+'<button id="trainingTab" type="button" hidden>流程训练</button>')
    panel='''<section id="view-training" hidden aria-labelledby="trainingTitle"><h1 id="trainingTitle">流程训练</h1>
<p>在独立训练对象中练习取证、分支判断、动作确认、停止、回退和恢复检查。</p>
<article class="training-card"><h2>AMF注册失败</h2><p>8个场景：入口排队、下游异常、证据冲突、记录缺失、对象不符、应用中断、回退失败与复发。</p>
<p>操作只改变本次练习。离开页签或重新登录会清空进度，可先导出演练记录。</p>
<div class="training-actions"><button id="trainingLaunch" type="button">开始演练</button><button id="trainingBack" type="button">返回业务列表</button></div></article>
<p id="trainingStatus" role="status" aria-live="polite"></p><div id="trainingFrameHost"></div></section>'''
    text=text.replace('</main>',panel+'</main>')
    text=text.replace('</head>','<link rel="stylesheet" href="/static/training-exercise-bridge.css"></head>')
    return text.replace('</body>','<script src="/static/training-exercise-bridge.js"></script></body>')


def exercise_document():
    validate()  # Includes sealed original bytes, exact draft boundary and source binding.
    nonce=secrets.token_urlsafe(24)
    text=(OUTPUT/'exercise.html').read_text('utf-8')
    old='<link rel="stylesheet" href="exercise.css"><script src="source-binding.js"></script><script defer src="engine.js"></script><script defer src="workspace.js"></script>'
    draft='<a href="sop-draft.md">查看流程草稿</a>'
    if text.count(old)!=1 or text.count(draft)!=1:raise ValueError('Known exercise shape changed')
    policy="default-src 'none'; script-src 'nonce-"+nonce+"'; style-src 'nonce-"+nonce+"'; connect-src 'none'; base-uri 'none'; form-action 'none'; object-src 'none'; frame-src 'none'"
    css=(OUTPUT/'exercise.css').read_text('utf-8')
    if '</style' in css.lower():raise ValueError('Invalid stylesheet terminator')
    text=text.replace(old,'<meta http-equiv="Content-Security-Policy" content="'+policy+'"><style nonce="'+nonce+'">'+css+'</style>')
    text=text.replace(draft,'')  # No draft body, metadata, review file or file route is exposed.
    scripts=[]
    for name in ('source-binding.js','engine.js','workspace.js'):
        body=(OUTPUT/name).read_text('utf-8')
        if '</script' in body.lower():raise ValueError('Invalid script terminator')
        scripts.append('<script nonce="'+nonce+'">'+body+'</script>')
    text=text.replace('</body>',''.join(scripts)+'</body>')
    return text,policy


def install(app, record, runtime):
    """Narrow middleware outside the frozen readonly guard. No general file serving."""
    from scripts import readonly_workspace_runtime as core
    from starlette.concurrency import run_in_threadpool
    from qa_core.api.auth import authenticate_headers
    paths={'/training/capabilities','/training/amf'}
    assets={'/static/training-exercise-bridge.js':'training-exercise-bridge.js',
            '/static/training-exercise-bridge.css':'training-exercise-bridge.css'}

    @app.middleware('http')
    async def exercise_view(request,call_next):
        path=request.url.path
        if path not in paths and path not in assets and path!='/':return await call_next(request)
        if request.method!='GET':return JSONResponse({'detail':'训练仅提供读取入口'},status_code=403,headers=HEADERS)
        try:
            await run_in_threadpool(runtime.verify_presentation,record)
            if path in assets:
                name=assets[path]
                return Response((core.PROJECT/'static'/name).read_text('utf-8'),headers=HEADERS,
                                media_type='application/javascript' if name.endswith('.js') else 'text/css')
            if path=='/':
                from qa_core.presentation.training_workspace import display_asset
                return HTMLResponse(workspace_html(display_asset('business-isolated.html',(core.PROJECT/'static/business-isolated.html').read_text('utf-8'))),headers=HEADERS)
            ctx=await run_in_threadpool(authenticate_headers,x_api_key=request.headers.get('x-api-key',''))
            permitted=can_train(ctx)
            if path=='/training/capabilities':
                return JSONResponse(dict(can_train_amf=permitted,environment='in_memory_training',business_writes=False),headers=HEADERS)
            if not permitted:raise HTTPException(403,'当前凭据不能进入此训练模块')
            text,policy=await run_in_threadpool(exercise_document)
            return HTMLResponse(text,headers={**HEADERS,'Content-Security-Policy':policy+"; sandbox allow-scripts allow-downloads; frame-ancestors 'self'"})
        except HTTPException as error:
            return JSONResponse({'detail':error.detail},status_code=error.status_code,headers=HEADERS)
        except Exception:
            return JSONResponse({'detail':'训练模块绑定暂不可用，请重新核对服务'},status_code=503,headers=HEADERS)
