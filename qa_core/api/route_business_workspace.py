"""Only the exact admitted isolated domain can enter this workspace."""
from fastapi import APIRouter, Depends, HTTPException, Query, Response
from fastapi.routing import APIRoute
import sqlalchemy as sa
from qa_core.api.auth import get_auth_context
from qa_core.config.settings import get_settings
from qa_core.application.business_workspace import BusinessWorkspace, reader

class ReadRoute(APIRoute):
    def get_route_handler(self):
        handler=super().get_route_handler()
        async def guarded(request):
            try: return await handler(request)
            except HTTPException as error:
                error.headers=dict(error.headers or {}, **{'Cache-Control':'no-store'});raise
            except (ValueError,OSError,sa.exc.SQLAlchemyError):
                raise HTTPException(503,'已准入快照、读取绑定或数据库不可用；请手动刷新',headers={'Cache-Control':'no-store'}) from None
        return guarded
def no_store(response:Response): response.headers['Cache-Control']='no-store'
router=APIRouter(prefix='/business',tags=['readonly-business'],route_class=ReadRoute,dependencies=[Depends(no_store)])

def isolated(ctx):
    reader(ctx)
    settings=get_settings()
    from scripts.readonly_workspace_runtime import validate_profile, verify_admission
    try: validate_profile(settings)
    except ValueError:
        raise HTTPException(503,'当前服务未启用已准入的隔离业务快照；请进入只读诊断工作区') from None
    verify_admission(settings)
    return settings

@router.get('/availability')
def availability(ctx=Depends(get_auth_context)):
    reader(ctx)
    # No domain rows or totals are accessed on the original service.
    settings=get_settings()
    from scripts.readonly_workspace_runtime import validate_profile, public_status
    enabled=False
    try:validate_profile(settings);enabled=True
    except ValueError:pass
    return {'enabled_here':enabled,'read_only':True,'isolated':public_status(),
            'notice':'仅使用已批准16条领域资料，历史知识版本 test-legacy-demo-r1；与8010当前问答版本分开。'}

@router.get('/integrations')
def integrations(ctx=Depends(get_auth_context)):
    service=BusinessWorkspace(isolated(ctx))
    try:return service.integrations(ctx)
    finally:service.close()

@router.get('/{kind}')
def listing(kind:str, ctx=Depends(get_auth_context), after:str=Query('',max_length=64),
            query:str=Query('',max_length=100), limit:int=Query(20,ge=1,le=50)):
    service=BusinessWorkspace(isolated(ctx))
    try:return service.listing(kind,ctx,after=after,query=query,limit=limit)
    finally:service.close()

@router.get('/{kind}/{identity}')
def detail(kind:str,identity:str,ctx=Depends(get_auth_context)):
    if len(identity)>64:raise HTTPException(422,'实体ID过长')
    service=BusinessWorkspace(isolated(ctx))
    try:return service.detail(kind,identity,ctx)
    finally:service.close()
