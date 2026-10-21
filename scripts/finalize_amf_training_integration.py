"""Finalize the live training presentation evidence without admitting the SOP draft."""
import hashlib
import json
import xml.etree.ElementTree as ET
from scripts.verify_amf_training_integration import PROJECT,REPORT
from scripts.prepare_legacy_projection import write,verify_seal
from scripts.prepare_amf_sop_exercise import validate,OUTPUT
from scripts import training_exercise_runtime as runtime


def read(name):return json.loads((REPORT/name).read_text('utf-8'))


def docs(note):
    report='.runtime/test/reports/amf-workspace-integration-20261015/review.html'
    def change(name,fn):
        path=PROJECT/name;raw=path.read_bytes().decode('utf-8');ending='\r\n' if '\r\n' in raw else '\n'
        path.write_bytes(fn(raw.replace('\r\n','\n')).replace('\n',ending).encode('utf-8'))
    def readme(text):
        lines=text.splitlines();lines[2]='> '+note+'从[业务登录入口](.runtime/readonly-workspace/open-workspace.html)进入“流程训练 → AMF注册失败”；见[实际验收]('+report+')，交接17.69、计划5.47。'
        return ('\n'.join(lines)+'\n').replace('先读[交接17.68]','先读[交接17.69]').replace('独立启动 `scripts/start_business_workspace.ps1`','独立启动 `scripts/start_training_workspace.ps1`').replace('scripts.training_business_runtime stop','scripts.training_exercise_runtime stop')
    change('README.md',readme)
    handoff='''
### 17.69 AMF流程训练接入业务工作区（2026-10-15）

NOTE

现有18021业务入口新增“流程训练”页签，默认租户、未额外限定部门/设备且有internal资料权限的ops/admin可见并进入；auditor、guest、其他租户、部门/设备受限和public-only凭据不能进入训练。后端沿用API Key鉴权，角色头不能提升权限。原告警/设备/工单/变更与关联原文继续读取既有获准库，未改业务角色或来源权限。打开后自动定位演练区，固定返回按钮便于手机退出。

演练只加载原封存AMF包的引擎、界面和来源绑定，按每次读取复核封存哈希与来源引用；不提供sop-draft.md、review-package、任意路径或Key文件。iframe仅allow-scripts/allow-downloads，无allow-same-origin；独立nonce与CSP connect-src none，不能读取父页面凭据/存储或调用业务接口。页面离开、凭据修改和退出会取消待加载响应并删除沙箱，重新进入从新练习开始。只有用户点击才导出JSON，保留generated_training_model及真实操作/人工审核/现场真值均未认证的属性。

实际浏览器覆盖正常、下游、冲突、缺失、对象不符、应用中断、回退失败、复发八场景；确认前不可执行，同窗分母/历史积压分开、配置回退不冒充恢复；导出文件实际读取验证，1280/390布局已查看。父页凭据和存储读取被拒、业务fetch触发CSP阻断、晚到响应不挂回、返回与重新进入和退出清理均通过。

只升级自有业务进程与其ops技术Key，其余原8010与r1/r2指南PID/Key保持；原/业务/r1/r2向量、原文、账本、业务表及配置读前后相同，原生成验证预算未变。6个临时验收Key已删除，业务库仅保留当前1个自有Key。重复启动确认不会再次换PID/Key。旧封存代码/候选不改；新控制器scripts.training_exercise_runtime、服务scripts.training_exercise_server及scripts/start_training_workspace.ps1是当前入口，旧训练/业务启动器为历史模式，勿用于当前增强模式。

入口仍是.runtime/readonly-workspace/open-workspace.html；优先启动scripts/start_training_workspace.ps1，状态/停止用`.venv/Scripts/python.exe -m scripts.training_exercise_runtime status|stop`。8010无需重启，也未切版本。训练SOP草稿仍pending，未登记身份、入库、建STAGED、发布或新增模型外发；普通指南门禁和独立质量缺口不因训练通过而改变。

保留两次验收工具问题记录：初始单元把导出代码a.href误判为外部HTML链接，改为检查实际标签属性后16项通过；首次Edge未定位跨进程沙箱iframe，补齐CDP子Target会话并完善首屏定位后28项通过，没有关闭浏览器隔离。报告见.runtime/test/reports/amf-workspace-integration-20261015/review.html，初始失败与最终结果并存。下一项先做现有工作台完整用户旅程自检和收口，明确工程剩余项及设备/现场依据/独立人工真值缺口，不自动铺开其余19个家族或扩大资料准入。
'''.replace('NOTE',note)
    change('docs/HANDOFF_2026-10-07.md',lambda text:text.split('\n',1)[0]+'\n\n> 当前入口17.69：'+note+'见[验收报告](../'+report+')。\n'+text.split('\n',1)[1]+handoff)
    plan='''
### 5.47 AMF训练模块接入完成（2026-10-15）

NOTE

业务工作区现有登录后按训练能力展示页签，ops/admin默认内部范围可用，其他角色/受限范围不新增权限。原业务读取及四种列表、关联详情和原文保持。沙箱无同源权限，CSP禁用网络，演练确认/变更/回退只作用于内存；手动导出保留真实权限/人工审核/现场依据未获认证属性。离开、重登、晚到响应、手机和重新进入已经实际Edge验证。

业务自有PID/Key按控制器升级，重复启动幂等；其余入口和数据指纹、原预算保持，临时Key清理。pending SOP未入库、STAGED或发布，生成/外发0，独立分母0。当前优先scripts/start_training_workspace.ps1及scripts.training_exercise_runtime；完整范围见交接17.69和amf-workspace-integration-20261015报告。

下一项做现有工作台完整用户旅程自检与工程收口，按实际失败修复；设备厂家型号/版本、现场处置与恢复依据、独立人工问题/真值继续明确缺口，不用训练通过或有页面替代这些验收。
'''.replace('NOTE',note)
    change('docs/DEVELOPMENT_PLAN_2026-10-07.md',lambda text:text.split('\n',1)[0]+'\n\n> 当前入口5.47：'+note+'\n'+text.split('\n',1)[1]+plan)
    change('docs/README.md',lambda text:text.replace('## 开发入口：先读这两份\n','## 开发入口：先读这两份\n\n- [当前交接17.69](HANDOFF_2026-10-07.md)：'+note+'[业务登录](../.runtime/readonly-workspace/open-workspace.html) → 流程训练；[实际验收](../'+report+')，计划5.47。\n',1))


def main():
    if (REPORT/'artifact-hashes.json').exists():raise ValueError('Preserve sealed integration report')
    result=read('result.json');assert result['status']=='passed' and result['http_checks']==39
    root=ET.parse(REPORT/'unit-tests.xml').getroot();assert len(list(root.iter('testcase')))==16
    assert not any(list(root.iter(t)) for t in ['failure','error','skipped'])
    browser=read('browser/browser-checks.json');assert len(browser)==28 and all(r['passed'] for r in browser)
    assert read('browser-result.json')['returncode']==0
    assert read('temporary-key-cleanup.json')==dict(created=6,removed=6,remaining=1)
    assert not any((REPORT/folder/'edge-profile').exists() for folder in ['browser','browser-attempt-1','browser-before-scroll-improvement'])
    validate();verify_seal(PROJECT/'.runtime/test/reports/entry-recovery-20261015')
    state=runtime.status();assert state['status']=='running' and state['training_module']
    code=runtime.BASE_CODE+runtime.EXTRA+('scripts/verify_amf_training_integration.py','scripts/verify_amf_training_bridge_browser.js',
         'scripts/finalize_amf_training_integration.py','tests/unit/test_amf_training_integration.py')
    write(REPORT/'code-bindings.json',{name:digest_file(PROJECT/name) for name in code})
    summary=dict(result,unit_tests=16,browser_checks=28,training_scenarios=8,training_environment='in_memory_training',
                 pending_draft_admitted=False,existing_exercise_seal_unchanged=True,owned_runtime=state,
                 limitation='Training model acceptance; no manufacturer/site SOP, independent human truth or long-term SLA claim.')
    write(REPORT/'summary.json',summary)
    note='2026-10-15 AMF流程训练已接入业务工作区：16单元、39真实HTTP、28项Edge检查通过，八场景及实际导出/权限/沙箱/退出清理验收完成；资料与预算保持，付费/入库/发布均0。'
    page='''<!doctype html><html lang="zh-CN"><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>AMF训练工作区接入验收</title><style>body{font:16px/1.8 system-ui;background:#f3f6fb;color:#20334d;margin:0}main{max-width:960px;margin:30px auto;background:#fff;padding:28px;border-radius:14px}img{max-width:100%;border:1px solid #ddd}a{color:#315ad7}h1{font-size:27px}</style><main><h1>AMF训练工作区接入验收</h1><p>NOTE</p>
<p><a href="LOGIN">使用本机凭据进入业务工作区</a> → 流程训练 → AMF注册失败。只升级18021自有业务进程和技术Key，原8010与两套指南保持。</p>
<h2>已验证的使用流程</h2><p>登录与能力导航、取证与分支、动作确认、八个场景、停止/回退/恢复、JSON实际导出、返回列表、重新进入、迟到响应丢弃、退出及手机布局。child不可访问父页凭据/存储，业务fetch被CSP阻断；关闭或重新登录清空内存。</p>
<h2>数据与权限边界</h2><p>沿用原业务库和鉴权，默认租户未额外限制部门/设备的ops/admin内部权限可进入；auditor、guest、其他租户、public-only和受限范围不新增训练权限。新接口只有GET，无业务写入、生成或任意文件服务。6个临时Key已删除，当前保留1个自有Key。</p>
<p>原库、业务库和两套指南的原文、向量、业务及账本指纹保持；只轮换自有业务PID/Key。训练SOP仍pending，不提供草稿下载、不登记来源、不入库或发布，也不向模型发送新资料。训练指标不能替代设备现场依据、人工真值或独立质量，独立分母0。</p>
<h2>证据</h2><p><a href="summary.json">摘要</a> · <a href="http-checks.json">真实HTTP</a> · <a href="browser/browser-checks.json">28项浏览器检查</a> · <a href="browser/downloads/amf-exercise-normal.json">实际导出演练记录</a> · <a href="temporary-key-cleanup.json">临时凭据清理</a></p>
<p>保留初始验收工具失败记录：单元断言曾误将导出用a.href视为外部链接；Edge定位跨进程沙箱时改用CDP子会话，未关闭浏览器隔离。最终单元与浏览器均通过。</p>
<h2>实际浏览器画面</h2><img src="browser/training-desktop.png" alt="工作区AMF流程训练"><img src="browser/training-mobile.png" alt="手机流程训练"></main></html>'''.replace('NOTE',note).replace('LOGIN',(PROJECT/'.runtime/readonly-workspace/open-workspace.html').as_uri())
    (REPORT/'review.html').write_text(page,encoding='utf-8');docs(note)
    write(REPORT/'artifact-hashes.json',{str(p.relative_to(REPORT)).replace('\\','/'):digest_file(p) for p in sorted(REPORT.rglob('*')) if p.is_file() and p.name!='artifact-hashes.json'})
    print(json.dumps(dict(status='sealed',unit_tests=16,http_checks=39,browser_checks=28,paid_calls=0)))


def digest_file(path):return hashlib.sha256(path.read_bytes()).hexdigest()


if __name__=='__main__':main()
