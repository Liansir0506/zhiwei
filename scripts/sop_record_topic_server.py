"""Preserve the readonly middleware and bind explicit topics before recall and reread."""
import argparse
from fastapi import Depends,HTTPException,Response
from qa_core.api.auth import AuthContext
from qa_core.api.route_evidence import EvidenceRequest,get_evidence_service,resolve_version,record,_enforce_rate_limit
from qa_core.retrieval.record_preview_topic import constraint_for_query
from scripts import sop_record_preview_server as core
from scripts import sop_record_topic_runtime as runtime
from scripts.prepare_sop_record_alignment_v2 import load_verified
from scripts.prepare_sop_record_admission import load_verified as admission


def create_app(instance,binding_check=lambda:None):
    app=core.create_app(instance,binding_check)
    app.router.routes[:]=[r for r in app.router.routes if getattr(r,'path',None)!='/kb/search-evidence']
    @app.post('/kb/search-evidence')
    def search(body:EvidenceRequest,response:Response,ctx:AuthContext=Depends(core.admin)):
        _enforce_rate_limit(ctx);response.headers['Cache-Control']='no-store'
        try:
            version=resolve_version(body.kb_version,ctx)
            if not body.query.strip():raise HTTPException(422,'query 不能为空')
            constraint,topic=constraint_for_query(body.query,load_verified(),admission())
            result=get_evidence_service().search(body.query.strip(),version,ctx.scope,body.top_n,constraint=constraint)
            result['topic_scope']=topic
        except HTTPException:
            record(ctx,'evidence_search','deny');raise
        except Exception:
            record(ctx,'evidence_search','deny','verification_unavailable')
            raise HTTPException(503,'专题范围或证据验证暂不可用') from None
        record(ctx,'evidence_search','allow');return result
    # FastAPI can store include_router as a lazy _IncludedRouter, without path.
    # Put the explicit override first; never mutate the shared evidence router.
    app.router.routes.insert(0,app.router.routes.pop())
    return app


if __name__=='__main__':
    parser=argparse.ArgumentParser();parser.add_argument('--instance',required=True);args=parser.parse_args()
    runtime.configure()
    def binding_check():
        state=runtime.core.record()
        if not state or state['instance']!=args.instance or state['code_bindings']!=runtime.core.bindings():
            raise ValueError('owned topical preview binding drift')
    binding_check();core.verify_live(runtime.core.profile())
    import torch;torch.set_num_threads(4)
    import uvicorn
    uvicorn.run(create_app(args.instance,binding_check),host='127.0.0.1',port=runtime.core.PORT,access_log=False)
