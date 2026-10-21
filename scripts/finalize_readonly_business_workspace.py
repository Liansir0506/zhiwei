"""Seal item five evidence, retaining failures and separate verification windows."""
import html
import json
import subprocess
import xml.etree.ElementTree as ET
import sqlalchemy as sa
from scripts import readonly_workspace_runtime as rt
from scripts import content_quality_release_runtime as current
from scripts.prepare_legacy_projection import read,write,digest,seal,verify_seal
from scripts.verify_readonly_business_workspace import REPORT,sql_snapshot
from qa_core.quality.publication import capture_snapshot
from qa_core.ingest import workspace as ws

def main():
    success=read(REPORT/'latest-success.json');attempt=REPORT/success['attempt']
    browser=read(attempt/'browser-result.json');cleanup=read(attempt/'cleanup.json')
    fault=read(REPORT/'storage-fault.json');survival=read(REPORT/'survival.json')
    visual=read(REPORT/'visual-final/visual-verification.json')
    assert all(r['passed'] for r in (success,browser,fault,survival,visual))
    assert cleanup['temporary_keys_remaining']==0 and cleanup['paid_budget_unchanged']
    assert not list(REPORT.glob('**/edge-profile'))
    suites=ET.parse(REPORT/'unit.xml').getroot().findall('testsuite')
    tests=sum(int(s.attrib['tests']) for s in suites)
    assert tests==149 and all(int(s.attrib['errors'])==int(s.attrib['failures'])==int(s.attrib['skipped'])==0 for s in suites)
    current.install_adapter();settings=current.selected_settings();current.owned.apply_profile(settings)
    assert current.owned.inspect()['status']=='running'
    isolated=rt.profile();rt.verify_admission(isolated);assert rt.inspect()['status']=='running'
    before=read(attempt/'before.json');after=read(attempt/'after.json');assert before==after
    assert sql_snapshot(settings)==after['original_sql'] and sql_snapshot(isolated)==after['isolated_sql']
    assert capture_snapshot(settings,current.VERSION)==after['active']
    assert digest(rt.PROJECT/'.env.runtime')==after['env'] and digest(current.owned.ROOT/'generation-validation-budget.json')==after['budget']
    for folder in (current.merge.CANDIDATE,current.merge.REPORT,current.REVIEW,rt.PROJECT/'data/candidates/agent-domain-review-r1'):verify_seal(folder)
    engine=sa.create_engine(isolated.mysql_dsn)
    try:
        with engine.connect() as conn:
            remaining=conn.execute(sa.text("SELECT COUNT(*) FROM api_keys WHERE user_id LIKE 'readonly-business-audit-%'")).scalar_one()
            assert remaining==0
    finally:engine.dispose()
    ws.verify_candidate(settings,ws.get_candidate('local-work-eb06bef4017f'),writable=True)
    worker=ws.worker_status();assert worker['status']=='ready'
    containers=subprocess.run(['docker','ps','--format','{{.Names}}'],capture_output=True,text=True,check=True).stdout.splitlines()
    assert len([n for n in containers if n.startswith('telecom-ops-rag-')])==10
    assert not any(n.endswith(('-api-1','-ingest-worker-1','-enterprise-mock-1')) for n in containers)
    paths=list(rt.CODE)+['app.py','qa_core/api/route_admin.py','static/index.html','static/governance-workspace.js',
        'scripts/start_readonly_workspace.ps1','scripts/verify_readonly_business_workspace.py',
        'scripts/verify_readonly_workspace_storage_fault.py','scripts/verify_readonly_workspace_survival.py',
        'scripts/verify_readonly_workspace_visual.py','scripts/finalize_readonly_business_workspace.py',
        'tests/unit/test_business_workspace.py','tests/unit/test_business_admission_guard.py',
        'tests/browser/business_workspace_smoke.js','README.md','docs/README.md',
        'docs/API_CONTRACT.md','docs/HANDOFF_2026-10-07.md','docs/DEVELOPMENT_PLAN_2026-10-07.md']
    write(REPORT/'code-bindings.json',{name:digest(rt.PROJECT/name) for name in paths})
    summary={'passed':True,'completed_items':5,'total_items':6,'unit_tests':tests,'api_checks':success['api_checks'],
             'browser_checks':len(browser['checks']),'actual_mysql_unavailable_checks':len(fault['checks']),
             'health_samples':len(survival['samples']),'cosmetic_readonly_capture':True,
             'api_pid':current.owned.load_state()['pid'],'isolated_pid':rt.state()['pid'],'worker_pid':worker['pid'],
             'original_active':current.VERSION,'isolated_version':rt.legacy.VERSION,'approved_domain_rows':16,
             'paid_calls':0,'new_admissions':0,'business_writes':0,'temporary_keys_remaining':remaining,
             'protected_snapshots_unchanged':True,'pending_original_domain_not_enabled':True,
             'item4_candidate_compatible':True,'independent_quality_verified':False,'permissions_expanded':False,
             'scope':'Admitted simulated local development; not live business sync, production root-cause or independent quality acceptance'}
    write(REPORT/'final-summary.json',summary)
    paragraphs=[
      '六项完成5项。149单元、37真实HTTP、23真实Edge通过；实际隔离MySQL停止后3路读取503无数据或凭据泄漏，恢复后同一API进程读取成功。最终四次20秒间隔观察通过，最后两张只读视觉截图已检查。零付费、无新增准入或业务数据写操作；访问审计、缓存、自有及临时凭据维护仍有正常写入。',
      '8010保留local-content-quality-r1及获准生成、Agent关闭。原历史业务行不在此次具体准入范围，新业务页只提供独立入口与状态，不转发原Key。18021固定回环，使用先前批准16条领域资料及历史test-legacy-demo-r1；禁用生成、同步、上传、发布、回滚及业务写，包括管理员。请用各自本机私有登录文件，本报告不含Key。',
      '已批准业务快照为2设备、2告警、1工单、1变更。列表在SQL分页前授权并核验关系，不泄漏其他范围总数，隐藏payload和人员字段。详情可点击关联对象，带入后仍手动诊断，实际4次工具调用、1条精确工单原文引用及位置。该证据不证明现场根因；不继承会话或自动执行。',
      '六类集成均没有同步账本，如实显示never_synced、次数和时间未知；未探测外部连接，无同步或重试控制，受限范围不能读租户汇总。每次业务和原文读取前后核对具体准入、范围、字段、关联身份/权限与代码绑定，变化停止发布。',
      '正常用户身份、快照与诊断来自真实接口。另HTTP503与延迟响应为补充传输故障注入，实际MySQL故障单独记录。降权或离开同步清空，旧响应不能恢复数据；15秒读取上限、手动刷新，390手机无横向溢出。最后仅空提示框CSS变化，重新绑定服务后只读截屏，未再次诊断，不追改前批功能证明。',
      '原和隔离业务/知识/定位/撤回/入库/版本SQL摘要、原完整发布快照、封存包、.env.runtime和预算3/4保持。7条临时审查Key已删除，既有STAGED上传候选仍兼容，专用Worker就绪，旧应用容器未启动。四次短时存活不证明长期SLA。',
      '失败保留：最初隔离存储停止导致启动失败；仅恢复既有test五存储，保留卷。随后一次无日志进程退出根因未确认，重启恢复。首次端到端原文路由404，补齐route_evidence后37/23通过；加强原文终检与归属保护后最终批次仍37/23。开发绑定变化使一段未完成观察停止，单独保留，最终窗口另验。',
      '下一项为候选审核、完整门禁、封存发布与回滚页面。SOP支持性、设备厂家/型号/版本/阈值和完整现场流程、BFD/NETCONF等pending以及未暴露独立质量仍有缺口。工程回归与模拟数据可读不能代替内容和生产验收。']
    page='<!doctype html><html lang="zh-CN"><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>第5项只读业务与诊断验收</title><style>body{font:16px/1.8 system-ui;color:#19304f;max-width:1100px;margin:32px}img{max-width:100%}pre{white-space:pre-wrap;overflow-wrap:anywhere}details{margin:18px 0}</style><h1>第5项 · 隔离只读业务与诊断</h1>'
    page+=''.join('<p>'+html.escape(p)+'</p>' for p in paragraphs)
    page+='<h2>真实页面检查</h2><ul>'+''.join('<li>'+html.escape(check)+'</li>' for check in browser['checks'])+'</ul>'
    for folder,shots in [(attempt,browser['screenshots']),(REPORT/'visual-final',visual['screenshots'])]:
        for name in shots:
            relative=str(folder.relative_to(REPORT)).replace('\\','/')+'/'+name
            page+='<details><summary>'+html.escape(relative)+'</summary><img alt="真实本地页面" src="'+html.escape(relative,quote=True)+'"></details>'
    page+='<details><summary>最终摘要</summary><pre>'+html.escape(json.dumps(summary,ensure_ascii=False,indent=2))+'</pre></details></html>'
    (REPORT/'review.html').write_text(page,encoding='utf-8');seal(REPORT);verify_seal(REPORT)
    print(json.dumps(summary,ensure_ascii=False))
if __name__=='__main__':main()
