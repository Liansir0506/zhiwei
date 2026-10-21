"""Preserve audit findings and page evidence; do not change product implementation."""
from datetime import datetime,timezone
import hashlib,html,json,socket
import xml.etree.ElementTree as ET
import sqlalchemy as sa
from scripts.audit_ops_20261019 import PROJECT,REPORT,PRIVATE,write,protected
from scripts import demo_release_runtime_r2 as release


def read(name):return json.loads((REPORT/name).read_text('utf-8'))


def main():
    if (REPORT/'artifact-hashes.json').exists():raise ValueError('Preserve sealed audit')
    release.install_adapter();settings=release.selected_settings()
    assert read('data-preservation.json')['equal'] and read('browser-admin-final-data-preservation.json')['equal']
    final=protected(settings);write('protected-final.json',final);assert final==read('protected-before.json')
    pages=read('browser/pages.json');admin=read('browser-admin-final/pages.json')
    assert len(pages['pages'])==40 and len(admin['pages'])==10
    assert all(p['state']=='loaded' for p in admin['pages'])
    assert not admin['errors'] and not pages['errors']
    evaluations=read('controlled-evaluation.json');finished=next(v['result'] for v in evaluations if v['stage']=='finished')
    report=next(v['result'] for v in evaluations if v['stage']=='report')
    assert finished['state']=='completed' and finished['completed']==31
    assert len(report['results'])==31 and all(all(r.get(k) is True for k in ['route_ok','hit_ok','body_ok','status_ok']) for r in report['results'])
    dashboard=read('dashboard-scope-probes.json');global_summary=read('version-summary-scope-probes.json')
    assert all(dashboard[r]['status']==200 and dashboard[r]['result']['overview']['devices']['total']==500 for r in ['scoped','public'])
    assert dashboard['foreign']['status']==500
    assert all(global_summary[r]['status']==200 and global_summary[r]['actor_disclosed'] for r in ['guest','foreign'])
    handoff=read('runtime-handoff-probe.json');assert handoff[-1]['result']['state']=='blocked'
    tree=ET.parse(REPORT/'cache-defect-reproductions.xml').getroot();assert len(list(tree.iter('testcase')))==2 and not list(tree.iter('failure'))
    for folder in ['browser-initial','browser-invalid-role-switch','browser','browser-admin-final']:
        assert not (REPORT/folder/'edge-profile').exists()
    assert not (PRIVATE/'keys.json').exists()
    engine=sa.create_engine(settings.mysql_dsn)
    try:
        with engine.connect() as c:
            remaining=c.execute(sa.text('SELECT COUNT(*) FROM api_keys WHERE user_id LIKE :pattern'),{'pattern':'ops-audit%'}).scalar_one()
        assert remaining==0
    finally:engine.dispose()
    listeners={}
    for port in [8010,18021,18024,18025]:
        with socket.socket() as probe:probe.settimeout(1);listeners[str(port)]=probe.connect_ex(('127.0.0.1',port))==0
    findings=[
      dict(id='F1',priority='P1',title='看板未执行部门、设备及资料级别访问限制',
           evidence='scoped/public账号均HTTP 200并得到500台设备、5000条告警；同范围领域查询会过滤或拒绝。',
           source='qa_core/api/route_dashboard.py:53',artifact='dashboard-scope-probes.json',
           fix='复用领域访问域过滤，或拒绝限域账号读取租户全局统计；覆盖指标、明细、监控和审计动态。'),
      dict(id='F2',priority='P1',title='guest及其他租户可读取全局版本治理摘要',
           evidence='两个账号均HTTP 200，返回19版本、10条治理操作和操作者；侧栏虽隐藏，后端没有对应角色/租户/限域检查。',
           source='qa_core/api/route_version_workspace.py:119',artifact='version-summary-scope-probes.json',
           fix='摘要接口加入明确角色、默认租户和全局范围门禁，不能只依赖导航隐藏。'),
      dict(id='F3',priority='P1',title='后续发布导致独立业务/训练及两套指南入口回归不可用',
           evidence='18021/18024/18025均不监听。业务控制器在readonly baseline changed处失败；5个绑定文件漂移。两个指南控制器导入旧content_quality链即ValueError。',
           source='scripts/training_business_runtime.py:21; scripts/execute_sop_evidence_stage.py:15',artifact='auxiliary-preflight.json',
           fix='通过新适配器兼容获准发布链、重审实际变更并重启自有进程；不可直接修改旧封存哈希绕过检查。'),
      dict(id='F4',priority='P1',title='当前发布版的完整服务交接准备无法完成',
           evidence='真实准备接口202受理，任务最终blocked且ValueError，未切版本。Preparer.profiles仍绑定旧content-quality发布对；页面还展示旧配置的“当前服务/ACTIVE”历史准备。',
           source='qa_core/quality/runtime_handoff.py:46',artifact='runtime-handoff-probe.json',
           fix='适配当前批准配置及恢复配置，区分历史/过期交接；验证完整恢复链，保持门禁与具体授权。'),
      dict(id='F5',priority='P2',title='空租户看板返回500',
           evidence='audit-empty租户实测500。告警和问答MAX时间为空时直接做timedelta运算。',
           source='qa_core/api/route_dashboard.py:73; qa_core/api/route_dashboard.py:125',artifact='dashboard-scope-probes.json',
           fix='无告警、无问答、无监控时返回零指标及合法空趋势，而不是服务器错误。'),
      dict(id='F6',priority='P2',title='目录缓存命中跳过实时复核，STAGED更新可继续显示旧内容',
           evidence='隔离TestClient复现2项：同页缓存不再次取最新行；同一STAGED新增来源后换page_size仍得到旧total。缓存TTL为300秒。未篡改在线数据库或向量。',
           source='qa_core/api/route_evidence.py:232; qa_core/api/route_evidence.py:116',artifact='cache-defect-reproductions.xml',
           fix='STAGED禁用缓存或纳入内容修订号/入库失效机制；保留撤回、权限及内容复核契约。'),
      dict(id='F7',priority='P2',title='业务入口提示的启动器过时，诊断说明与实际配置矛盾',
           evidence='页面仍提示start_readonly_workspace.ps1，并称不开隔离也能直接只读诊断；实际主站Agent关闭，当前增强工作区应使用训练启动器且仍有F3阻断。',
           source='static/business-workspace.js:73; static/business-workspace.js:78',artifact='browser-admin-final/admin-business.png',
           fix='修复启动链后更新唯一入口及深链；将诊断不可用和恢复步骤准确呈现，不能只更新文案假装恢复。')]
    matrix=[
      ['总览看板','有数据，但存在权限及空数据缺陷','6张指标卡与图表实测；F1/F5。'],
      ['运维问答','部分验证','推荐能填入；31条FAQ零模型全文/路由/状态/来源均通过。未重跑付费文档生成与SSE。'],
      ['只读诊断','当前不可实际使用','主站Agent关闭，独立服务回归不可用；F3。'],
      ['知识中心','浏览功能可用，缓存有隐患','目录、AMF筛选、下一页及来源详情实测；F6。'],
      ['文档与入库','读取及管理控件可用，写流程未重跑','文档可读，管理员有候选/上传表单，Worker ready且compatible；目前无兼容候选，本轮未创建或入库。'],
      ['业务与集成','只有说明入口，实际工作区不可用','主站四张说明卡可读；独立告警/设备/工单/变更及AMF训练不可达；F3/F7。'],
      ['版本与发布','只读可用，完整交接受阻','管理员能读版本和历史；真实完整配置准备blocked，未执行发布或回滚；F4。'],
      ['运行状态','可用','主站组件、历史/进程计数与Worker心跳正常；不能替代辅助入口或回答质量验收。'],
      ['评测记录','可用','本轮真实完成31项零模型FAQ回归并读到报告；独立质量分母仍为0。'],
      ['访问管理','读取与角色导航可用，仍有接口越权问题','管理员/审计员可读，ops/guest导航受限；本轮不操作用户的签发/吊销按钮；F2需修。']]
    summary=dict(date='2026-10-19',generated_at_utc=datetime.now(timezone.utc).isoformat(),
                 primary='http://127.0.0.1:8010',active_version='local-demo-release-r1',runtime_amendment='r2',
                 findings=findings,page_matrix=matrix,basic_http_observations=len(read('http-checks.json')),
                 browser_role_page_observations=50,faq_self_regression=dict(total=31,route=31,body=31,status=31,source=31,mode='no_llm'),
                 cache_defect_reproductions=2,paid_calls=0,knowledge_changes=0,release_or_rollback_executions=0,
                 temporary_audit_keys_remaining=remaining,protected_main_data_unchanged=True,listening=listeners,
                 limits=['没有付费文档生成或真实SSE答案复验。','没有对当前业务库执行上传、入库、发布、回滚。',
                         '零模型评测报告与一次blocked交接准备是本轮新建的审计产物；不是知识变更。',
                         '手机只测布局及截图；加载中的手机截图不作为功能通过证明。',
                         '初版浏览器存在DOM就绪/同文档角色切换问题，保留为无效工具证据；最终逐角色核对后又完成管理员严格内容加载验收。'])
    write('summary.json',summary)
    text='# 运维复核 · 2026-10-19\n\n主站恢复可读，但不能判定项目各功能已全部可用。发现4项P1、3项P2。\n\n'
    for row in findings:text+='- **'+row['priority']+' '+row['title']+'**：'+row['evidence']+' 源码：`'+row['source']+'`。建议：'+row['fix']+'\n'
    text+='\n| 页签 | 结论 | 本轮证据与范围 |\n|---|---|---|\n'+''.join('|'+ '|'.join(row)+'|\n' for row in matrix)
    text+='\n77项基础HTTP记录、50次角色/页签观察；真实零模型FAQ自回归31/31，2项缓存缺陷隔离复现。主站数据及预算前后相同，临时Key已清理。详见summary.json各项限制。\n'
    (REPORT/'review.md').write_text(text,'utf-8')
    items=''.join('<article><h3>'+html.escape(f['priority']+' · '+f['title'])+'</h3><p>'+html.escape(f['evidence'])+'</p><p><code>'+html.escape(f['source'])+'</code> · <a href="'+html.escape(f['artifact'],quote=True)+'">复核证据</a></p><p>建议：'+html.escape(f['fix'])+'</p></article>' for f in findings)
    table=''.join('<tr>'+''.join('<td>'+html.escape(cell)+'</td>' for cell in row)+'</tr>' for row in matrix)
    page='''<!doctype html><html lang="zh-CN"><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>运维复核 · 2026-10-19</title><style>body{font:16px/1.8 system-ui;color:#24334b;background:#f3f6fb;margin:0}main{max-width:1100px;margin:28px auto;background:white;border-radius:14px;padding:28px}h1{font-size:28px}article{border:1px solid #e4e8ef;padding:16px;border-radius:10px;margin:16px 0}h3{margin:0}table{border-collapse:collapse;width:100%}td,th{border:1px solid #dce4ef;padding:10px;text-align:left}code{overflow-wrap:anywhere}a{color:#315ad7}img{max-width:100%;border:1px solid #ddd}</style><main><h1>运维复核 · 2026-10-19</h1><p><strong>主站可读，不等于全部页签功能已贯通。4项P1、3项P2需要处理。</strong></p><p>启动时四端口均关闭。按当前批准链恢复8010并逐页测试，未自动解除绑定门禁或重发旧候选。77项基础HTTP记录、50次角色/页签观察；当前FAQ零模型回归31/31，2项缓存缺陷隔离复现。临时Key已清理，业务/知识/预算前后不变。</p><h2>逐页结论</h2><table><tr><th>页签</th><th>结论</th><th>证据与限制</th></tr>TABLE</table><h2>待处理问题</h2>ITEMS<h2>验收边界</h2><p>没有付费文档生成或SSE回答重跑，没有上传/入库/发布/回滚执行。只新增零模型评测报告及blocked交接准备；不能据此宣称独立质量通过。手机布局没有检测到主体横向溢出，加载中截图不作为功能通过证明。保留最初浏览器工具失败及无效角色切换记录，最终角色逐一由真实API身份核对，管理员10页等待实际内容完成。</p><p><a href="summary.json">完整结果</a> · <a href="http-checks.json">基础HTTP记录</a> · <a href="controlled-evaluation.json">31项真实自回归</a> · <a href="browser-admin-final/pages.json">管理员最终逐页结果</a> · <a href="review.md">文字版</a></p><h2>实际页面</h2><img src="browser-admin-final/admin-business.png" alt="业务工作区当前不可用"><img src="browser-admin-final/admin-versions.png" alt="当前版本与历史交接"><img src="browser-admin-final/admin-eval.png" alt="本次零模型回归报告"></main></html>'''.replace('TABLE',table).replace('ITEMS',items)
    (REPORT/'review.html').write_text(page,'utf-8')
    files=['scripts/audit_ops_20261019.py','scripts/audit_ops_pages_20261019.js','scripts/audit_ops_browser_resume_20261019.py',
           'scripts/audit_zero_model_eval_20261019.py','scripts/finalize_ops_audit_20261019.py','tests/unit/test_ops_audit_cache_20261019.py']
    write('audit-code-bindings.json',{name:hashlib.sha256((PROJECT/name).read_bytes()).hexdigest() for name in files})
    write('artifact-hashes.json',{p.relative_to(REPORT).as_posix():hashlib.sha256(p.read_bytes()).hexdigest() for p in sorted(REPORT.rglob('*')) if p.is_file() and p.name!='artifact-hashes.json'})
    print(json.dumps(dict(findings=len(findings),P1=4,P2=3,faq_passed=31,paid_calls=0,listening=listeners)))


if __name__=='__main__':main()
