"""Read-only support metadata is supplied through the existing admin checks boundary."""
import argparse
from fastapi.responses import HTMLResponse,Response
from scripts import sop_guide_preview_server as core
from scripts import sop_guide_support_runtime as runtime
from scripts.prepare_sop_support_alignment import ROOT,load_verified
from qa_core.quality.guide_preview_checks import verify_live as base_verify_live


def verify_support_live(settings):
    result=base_verify_live(settings)
    result['support_alignment']=load_verified()
    return result


def create_app(instance,binding_check=lambda:None):
    core.verify_live=verify_support_live
    app=core.create_app(instance,binding_check)
    names={'/','/guide-preview.js','/guide-preview.css'}
    app.router.routes[:]=[r for r in app.router.routes if getattr(r,'path',None) not in names]
    @app.get('/')
    def page():
        body=(ROOT/'static/guide-preview.html').read_text(encoding='utf-8')
        body=body.replace('</nav>','<button data-tab="support" aria-selected="false">SOP支持性对齐</button></nav>')
        section='''<section id="support" hidden><h2>SOP与指南的逐项对齐</h2><p>这里核对文字对应及来源位置。现场原始记录、厂家依据和动作授权仍未提供，不能据此执行处置。</p><label>筛选专题 <input id="support-filter" type="search" placeholder="如 AMF、LTE、光路"></label><p id="support-count" role="status"></p><div id="support-summary"></div><div id="support-entries"></div></section>'''
        return HTMLResponse(body.replace('<section id="source-panel"',section+'<section id="source-panel"'))
    @app.get('/guide-preview.js')
    def script():
        return Response((ROOT/'static/guide-preview.js').read_text(encoding='utf-8')+'\n'+
                        (ROOT/'static/guide-support.js').read_text(encoding='utf-8'),media_type='application/javascript')
    @app.get('/guide-preview.css')
    def css():
        return Response((ROOT/'static/guide-preview.css').read_text(encoding='utf-8')+'\n'+
                        (ROOT/'static/guide-support.css').read_text(encoding='utf-8'),media_type='text/css')
    return app


if __name__=='__main__':
    parser=argparse.ArgumentParser();parser.add_argument('--instance',required=True);args=parser.parse_args()
    runtime.configure()
    def binding_check():
        state=runtime.core.record()
        if not state or state['instance']!=args.instance or state['code_bindings']!=runtime.core.bindings():
            raise ValueError('owned support preview binding drift')
    binding_check();verify_support_live(runtime.core.profile())
    import torch;torch.set_num_threads(4)
    import uvicorn
    uvicorn.run(create_app(args.instance,binding_check),host='127.0.0.1',port=runtime.core.PORT,access_log=False)
