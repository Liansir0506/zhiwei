"""Verify protected state and seal the first remaining step's preparation evidence."""
import html
import json
import shutil
import xml.etree.ElementTree as ET
import sqlalchemy as sa
from scripts import content_quality_release_runtime as rt, readonly_workspace_runtime as readonly
from scripts.prepare_legacy_projection import read,write,digest,seal,verify_seal
from scripts.verify_runtime_handoff import REPORT
from scripts.verify_readonly_business_workspace import sql_snapshot
from qa_core.quality.publication import capture_snapshot
from qa_core.quality.runtime_handoff import CODE
from qa_core.ingest import workspace as uploads


def main():
    assert not (REPORT/'artifact-hashes.json').exists()
    attempt=REPORT/read(REPORT/'latest-success.json')['attempt']
    proof=read(attempt/'verification.json');browser=read(attempt/'browser-verification.json');survival=read(REPORT/'survival.json')
    assert proof['passed'] and browser['passed'] and survival['passed']
    assert read(attempt/'cleanup.json')['temporary_keys_remaining']==0
    assert not list(REPORT.glob('**/edge-profile'))
    suites=ET.parse(rt.PROJECT/'.runtime/version-workspace/handoff-final-unit-r3.xml').getroot().findall('testsuite')
    assert all(int(s.attrib[k])==0 for s in suites for k in ('failures','errors','skipped'))
    tests=sum(int(s.attrib['tests']) for s in suites);assert tests==70
    for filename in ('handoff-preliminary-unit.xml','handoff-final-unit.xml','handoff-final-unit-r2.xml','handoff-final-unit-r3.xml'):
        shutil.copyfile(rt.PROJECT/'.runtime/version-workspace'/filename,REPORT/filename)
    rt.install_adapter();settings=rt.selected_settings();rt.owned.apply_profile(settings)
    after=read(attempt/'after.json');assert after==read(attempt/'before.json')
    assert sql_snapshot(settings)==after['sql'] and sql_snapshot(readonly.profile())==after['readonly_sql']
    assert capture_snapshot(settings,rt.VERSION)==after['active']
    budget=rt.owned.ROOT/'generation-validation-budget.json'
    assert digest(budget)==after['budget'] and read(budget)['used']==3 and read(budget)['limit']==4
    assert digest(rt.PROJECT/'.env.runtime')==after['env']
    assert {r['id']:digest(uploads.safe_path('uploads',r['id'],'upload.json')) for r in uploads.uploads()}==after['uploads']
    assert rt.owned.inspect()['status']==readonly.inspect()['status']=='running' and uploads.worker_status()['status']=='ready'
    readonly.verify_admission(readonly.profile());uploads.verify_candidate(settings,uploads.get_candidate('local-work-eb06bef4017f'),writable=True)
    for folder in (rt.merge.CANDIDATE,rt.merge.REPORT,rt.REVIEW,rt.PROJECT/'data/candidates/agent-domain-review-r1'):verify_seal(folder)
    engine=sa.create_engine(settings.mysql_dsn)
    try:
        with engine.connect() as conn:
            remaining=conn.execute(sa.text('SELECT COUNT(*) FROM api_keys WHERE user_id LIKE :p'),{'p':'runtime-handoff-audit-%'}).scalar_one();assert remaining==0
    finally:engine.dispose()
    plan=read(attempt/'handoff-plan.json');profiles=plan['inspection']['profiles']
    assert len(profiles)==2 and not plan['execution_authorized']
    assert all(digest(rt.PROJECT/name)==sha for name,sha in plan['inspection']['code_bindings'].items())
    assert all(p['visible_owned_windows']==0 for row in survival['samples'] for p in row['processes'].values())
    summary=dict(passed=True,step='remaining_step_1_full_configuration_preparation',preparation_completed=True,
        actual_switch_executed=False,live_handoff_execution_implemented=False,execution_authorized=False,
        unit_tests=tests,api_checks=proof['api_checks'],browser_checks=len(browser['checks']),health_samples=len(survival['samples']),
        original_version=rt.VERSION,recovery_version=rt.OLD,
        version_sizes={v:{'files':len(p['snapshot']['files']),'chunks':sum(c['count'] for c in p['snapshot']['collections'].values())} for v,p in profiles.items()},
        original_active_and_protected_data_unchanged=True,legacy_pending_quarantined=True,
        paid_calls=0,new_admissions=0,temporary_keys_remaining=remaining,
        runtime_visible_owned_windows=0,independent_quality_verified=False,
        actual_docker_cold_start_recovery=True,created_or_deleted_containers_or_volumes=False,
        scope='Admitted local demo preparation only; new original full-service switching, SOP/device evidence and independent quality are not accepted by this result')
    write(REPORT/'final-summary.json',summary)
    paths=set(CODE)|{'scripts/verify_runtime_handoff.py','scripts/observe_runtime_handoff.py','scripts/finalize_runtime_handoff.py',
        'tests/browser/runtime_handoff_smoke.js','tests/unit/test_runtime_handoff.py','tests/unit/test_platform_entry.py',
        'README.md','docs/README.md','docs/API_CONTRACT.md','docs/DEVELOPMENT_PLAN_2026-10-07.md','docs/HANDOFF_2026-10-07.md'}
    write(REPORT/'code-bindings.json',{name:digest(rt.PROJECT/name) for name in sorted(paths)})
    paragraphs=[
        '第一步完成的是原8010完整配置交接准备与即时重验。70相关单元、14真实接口、8真实Edge检查通过；当前完整306份/4357片段、上一完整279份/5903片段，各自配置、真实向量/权限、报告/持久快照、撤回、封存来源与运行归属重验。准备不改ACTIVE，不实际切换原服务，不授予新准入或外发权限。',
        '版本页管理员手动准备，审计员只读；ops/跨租户/受限管理员无全局交接内容。持久后台任务、重启中断、15分钟计划与同发起人重新核验，额外profile/approved字段拒绝。所有配置展示不含Key/DSN，依赖异常不原样返回；迟到管理员响应在降权后丢弃，390手机截图已人工查看。',
        '完整交接包含当前与上一完整服务的集合、模型/读取实现、全部Settings摘要、Worker/观察器和凭据恢复要求，以及停机前、提交前、提交后预热失败、提交状态不明、恢复失败、缓存后处理失败六类响应。现有重复FAQ增量及pending没有新整库发布条件。旧不兼容local-work-96e878cd877a的pending任务继续隔离，未取消、领取或交给旧Worker；关联未完成任务或任何执行中任务仍阻断。',
        '真实冷启动发现Docker info格式化命令在命名管道失效时returncode0且版本为空，原判断误报容器缺失。已改解析非空服务端版本，断连与真实容器缺失分开；隐藏桌面启动Docker并恢复已有原/test存储卷。旧应用/Worker/mock容器保留停止，未创建/删除容器或卷。原8010和只读18021、专用Worker/观察器均恢复，私有Key轮换。',
        '最终四次20秒间隔观察三路健康、稳定自有PID、Worker就绪和Docker容器健康。自有后台进程没有可见窗口；不按标题关闭用户终端，短时恢复不证明长期SLA。原及隔离SQL/完整ACTIVE快照、上传及准入、父包封存、.env.runtime与预算3/4保持，临时Key0，付费0，新准入0。',
        '失败保留：首轮真实检查把其他候选pending队列过严当成准备阻断，临时Key已finally删除；调整为隔离旧pending并记录真实执行前缺口后重验成功。首轮单元两处夹具错误：延迟路由没有path、测试环境误用真实冻结配置；修正为直接路由/独立配置并保留失败XML，最终70全通过。',
        '本流程不含新的原8010完整服务切换执行器，也未发布或回滚原版本。后续实际切换需要完整合格变更候选与具体方案；SOP安全/设备型号版本阈值与完整流程、BFD/NETCONF等pending及独立未暴露样本/人工真值仍缺，不用准备报告替代这些验收。']
    page='<!doctype html><html lang="zh-CN"><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>第一步：完整配置交接准备</title><style>body{font:16px/1.8 system-ui;max-width:1100px;margin:32px;color:#19304f}img{max-width:100%}pre{white-space:pre-wrap;overflow-wrap:anywhere}details{margin:18px 0}</style><h1>原8010完整配置交接准备</h1>'
    page+=''.join('<p>'+html.escape(p)+'</p>' for p in paragraphs)
    page+='<ul>'+''.join('<li>'+html.escape(c)+'</li>' for c in browser['checks'])+'</ul>'
    for name in browser['screenshots']:
        relative=attempt.name+'/'+name;page+='<details><summary>'+html.escape(name)+'</summary><img src="'+html.escape(relative,quote=True)+'" alt="真实原8010页面"></details>'
    page+='<details><summary>最终摘要</summary><pre>'+html.escape(json.dumps(summary,ensure_ascii=False,indent=2))+'</pre></details></html>'
    (REPORT/'review.html').write_text(page,encoding='utf-8');seal(REPORT);verify_seal(REPORT)
    print(json.dumps(summary,ensure_ascii=True),flush=True)


if __name__=='__main__':main()
