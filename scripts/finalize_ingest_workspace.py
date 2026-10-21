"""Seal item-four evidence; retain earlier attempts and publication history."""
import html
import json
import sqlalchemy as sa
from scripts import content_quality_release_runtime as runtime
from scripts.prepare_legacy_projection import read,write,digest,seal,verify_seal
from scripts.verify_ingest_workspace import REPORT
from qa_core.ingest import workspace as ws
from scripts import workspace_ingest_worker as worker


def main():
    success=read(REPORT/'latest-success.json');attempt=REPORT/success['attempt']
    browser=read(attempt/'browser-result.json');supplement=read(attempt/'snapshot-verification.json')
    preservation=read(attempt/'preservation.json');cleanup=read(attempt/'cleanup.json')
    survival=read(REPORT/'survival.json');binding=read(REPORT/'binding-change-observation.json')
    assert browser['passed'] and supplement['passed'] and survival['passed'] and binding['passed']
    assert cleanup['temporary_keys_remaining']==0 and cleanup['budget_unchanged']
    assert all(preservation[field] for field in ('active_snapshot_unchanged','frozen_seals_verified','env_unchanged','budget_unchanged','old_worker_not_started'))
    assert not list(REPORT.glob('attempt-*/edge-profile'))
    runtime.install_adapter();settings=runtime.selected_settings();runtime.owned.apply_profile(settings)
    engine=sa.create_engine(settings.mysql_dsn)
    try:
        assert runtime.owned.verify_active(settings,engine)==runtime.VERSION
        with engine.connect() as conn:
            remaining=conn.execute(sa.text("SELECT COUNT(*) FROM api_keys WHERE user_id LIKE 'ingest-workspace-audit-%'")).scalar()
            assert remaining==0
    finally:engine.dispose()
    process=worker.owned(worker.load_state());assert process
    candidate=ws.get_candidate(success['candidate']);ws.verify_candidate(settings,candidate,writable=True)
    assert ws.worker_status()['status']=='ready'
    assert digest(runtime.PROJECT/'.env.runtime')=='045ef4acd5b9babfea24d0c3e9b61679deb51a84c6803eb2e4f553139dbc19ff'
    for folder in (runtime.merge.CANDIDATE,runtime.merge.REPORT,runtime.REVIEW):verify_seal(folder)
    paths=['app.py','qa_core/api/route_admin.py','qa_core/api/route_ingest_workspace.py','qa_core/ingest/workspace.py',
        'scripts/workspace_ingest_worker.py','scripts/start_ingest_workspace_worker.ps1','scripts/start_platform.ps1','scripts/stop_platform.ps1',
        'static/index.html','static/knowledge-workspace.js','tests/unit/test_ingest_workspace.py','tests/browser/ingest_workspace_smoke.js',
        'scripts/verify_ingest_workspace.py','scripts/verify_ingest_workspace_survival.py','scripts/verify_ingest_worker_binding.py',
        'scripts/finalize_ingest_workspace.py','README.md','docs/README.md','docs/DEVELOPMENT_PLAN_2026-10-07.md',
        'docs/HANDOFF_2026-10-07.md','docs/API_CONTRACT.md']
    write(REPORT/'code-bindings.json',{name:digest(runtime.PROJECT/name) for name in paths})
    summary={'passed':True,'completed_items':4,'total_items':6,'unit_tests':63,'browser_checks':len(browser['checks']),
        'supplementary_readonly_checks':len(supplement['checks']),'health_samples':len(survival['observations']),
        'active':runtime.VERSION,'candidate':success['candidate'],'candidate_status':'STAGED','worker_pid':process.pid,
        'api_pid':runtime.owned.load_state()['pid'],'paid_calls':0,'temporary_keys_remaining':remaining,
        'new_material_pending':True,'automatic_publication':False,'old_worker_started':False,'permissions_expanded':False,
        'frozen_bindings_verified':True,'independent_quality_verified':False,'scope':'Local development upload/retry/ledger; not production quality or complete publication workflow'}
    write(REPORT/'final-summary.json',summary)
    page='<!doctype html><html lang="zh-CN"><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>第4项入库工作区验收</title>'
    page+='<style>body{font:16px/1.8 system-ui;margin:32px;color:#19304f;max-width:1100px}img{max-width:100%}td,th{border:1px solid #ddd;padding:10px;text-align:left}table{border-collapse:collapse;width:100%}details{margin:16px 0}code,pre{overflow-wrap:anywhere;white-space:pre-wrap}</style>'
    page+='<h1>第4项 · 独立上传与入库工作区</h1><p>六项完成4项。63专项、15真实Edge流程、'+str(len(supplement['checks']))+'只读补充及四次间隔存活检查通过。真实管理员创建候选、上传、遇到实际文件缺失、恢复并在页面手动重试、完成真实BGE向量及SQL账本。未调用模型生成，未批准新资料或发布。</p>'
    page+='<p>写操作仅default未缩小访问范围的管理员；ops/auditor只读，guest/其他租户/受限凭据不能读写全局入库工作区。独立.runtime/ingest-workspace目录；历史封存候选不可写。候选绑定当前集合、模型、处理代码及来源准入包；Worker固定启动绑定，真实注释变更探针观察到暂停领取，精确恢复后就绪。旧Worker及其全局队列恢复未运行。</p>'
    page+='<table><tr><th>输入</th><th>真实结果</th><th>边界</th></tr><tr><td>新manual_pending_demo.md</td><td>awaiting_review，0队列任务、0向量</td><td>新正文/身份/范围须具体审核，不能参加问答</td></tr><tr><td>完全相同的已批准faq_revision_01.csv</td><td>第1次文件缺失失败；第2次手动重试成功；1 FAQ、0文档向量，1024维归一化BGE，done/1片段账本</td><td>只沿用既有正文/身份/权限/修订准入；成功不激活</td></tr></table>'
    page+='<p>只测试了上述本地FAQ真实入库与新MD隔离，不声明所有二进制格式、内容语义或设备现场流程已经验收。单片段批次、任务后释放Worker模型；四次20秒间隔API/Worker存活、IPv4/localhost健康保持，不代表长期SLA。当前唯一ACTIVE仍'+html.escape(runtime.VERSION)+'，新候选 '+html.escape(success['candidate'])+' 仅STAGED。原完整发布快照、三套封存目录全文件清单及字节、原.env.runtime、预算3/4保持，临时审查Key删除，付费0。</p>'
    page+='<p>失败记录保留：首轮复用发布入口严格配置函数被拒，改用独立受限Worker配置；第二轮后台启动管道等待导致调用方超时，Worker实际已记录文件缺失，改为显式脱离标准流。期间API退出，根因尚未确认，统一入口恢复后Key轮换，之后健康。来源详情字段document_id及guest等待条件的补充脚本错误修正，产品来源数据不改。最后审查补上固定启动绑定并重跑完整链路，前次候选保留并标为不兼容，不改旧绑定冒充当前验收。</p>'
    page+='<p>下一项为隔离只读诊断与业务工作区。第6项仍需来源子目录汇总、候选审核/完整门禁/封存发布/回滚；SOP支持性、厂家型号/版本/阈值/完整现场流程及未暴露独立质量继续待办。</p><h2>真实产品检查</h2><ul>'
    page+=''.join('<li>'+html.escape(check)+'</li>' for check in browser['checks']+supplement['checks'])+'</ul>'
    for name in browser['screenshots']+supplement['screenshots']:
        page+='<details><summary>'+html.escape(name)+'</summary><img src="'+html.escape(attempt.name+'/'+name,quote=True)+'" alt="真实本地页面"></details>'
    page+='<details><summary>实际运行摘要</summary><pre>'+html.escape(json.dumps(summary,ensure_ascii=False,indent=2))+'</pre></details></html>'
    (REPORT/'review.html').write_text(page,encoding='utf-8');seal(REPORT);verify_seal(REPORT)
    print('Item 4 sealed: 63 unit, 15 product, '+str(len(supplement['checks']))+' readonly, 4 survival checks; paid calls 0.')


if __name__=='__main__':main()
