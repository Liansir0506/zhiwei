"""Run the unchanged admitted readonly server with a narrow presentation adapter."""
import sys
import uvicorn
from fastapi.responses import Response,JSONResponse
from scripts import readonly_workspace_runtime as core
from scripts import training_business_runtime as runtime
from qa_core.presentation.training_workspace import ASSETS,display_asset


def main():
    instance=sys.argv[sys.argv.index('--instance')+1]
    record=core.state()
    if not record or record['instance']!=instance:raise ValueError('owned training instance mismatch')
    runtime.verify_presentation(record)
    original_verify=core.verify_admission
    def verify(settings):
        runtime.verify_presentation(record)
        return original_verify(settings)
    core.verify_admission=verify
    original_run=uvicorn.run
    def run(app,**kwargs):
        @app.middleware('http')
        async def training_copy(request,call_next):
            if request.method=='GET' and request.url.path in ASSETS:
                try:
                    runtime.verify_presentation(record)
                    name=ASSETS[request.url.path]
                    text=display_asset(name,(core.PROJECT/'static'/name).read_text(encoding='utf-8'))
                    return Response(text,media_type='text/html' if name.endswith('.html') else 'application/javascript',
                         headers={'Cache-Control':'no-store','X-Content-Type-Options':'nosniff'})
                except Exception:
                    return JSONResponse({'detail':'工作区界面绑定暂不可用，请重新检查服务'},status_code=503,headers={'Cache-Control':'no-store'})
            return await call_next(request)
        original_run(app,**kwargs)
    uvicorn.run=run
    # The process executes this exact existing entry; the module token is also
    # explicit on argv for the existing PID/cwd/instance ownership controller.
    from scripts import readonly_workspace_server
    sys.argv=[sys.argv[0],'--instance',instance]
    readonly_workspace_server.main()


if __name__=='__main__':main()
