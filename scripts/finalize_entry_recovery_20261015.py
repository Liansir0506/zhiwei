"""Seal the sanitized recovery evidence after live/browser checks and cleanup."""
import hashlib
import html
import json
from pathlib import Path
import xml.etree.ElementTree as ET
from scripts.recover_training_entries_20261015 import REPORT, PROJECT, write


def read(name):return json.loads((REPORT/name).read_text('utf-8'))


def update_docs(result):
    """Update current entry pointers only after the actual acceptance succeeds."""
    report='.runtime/test/reports/entry-recovery-20261015/review.html'
    note=('2026-10-15依赖与入口恢复完成：8010、业务18021、指南18024/18025均已恢复，'
          '10个既有存储健康；26单元、'+str(result['http_checks'])+'真实HTTP、20/20开发主题逐条引用、'
          '4项检索Edge及启动状态页检查通过，四入口3轮采样及后台观察通过。'
          '11组历史数据/配置指纹匹配，本轮只读验收前后一致；付费、入库和发布均0。')
    def change(path, transform):
        file=PROJECT/path
        text=file.read_bytes().decode('utf-8')
        newline='\r\n' if '\r\n' in text else '\n'
        text=text.replace('\r\n','\n')
        file.write_bytes(transform(text).replace('\n',newline).encode('utf-8'))
    def readme(text):
        lines=text.splitlines()
        if lines[2].startswith('> 2026-10-15流程演练样板完成：'):
            lines[2]='> '+note+'见[本轮报告]('+report+')，交接17.68、计划5.46。下一项可将[AMF本地演练](data/candidates/amf-sop-exercise-r1/exercise.html)接入业务工作区作为独立训练页；新SOP仍pending，不自动入库或外发。'
        text='\n'.join(lines)+'\n'
        text=text.replace('先读[交接17.67]','先读[交接17.68]')
        text=text.replace('独立启动 `scripts/start_readonly_workspace.ps1`','独立启动 `scripts/start_business_workspace.ps1`')
        text=text.replace('停止用 `.venv/Scripts/python.exe -m scripts.readonly_workspace_runtime stop`','停止用 `.venv/Scripts/python.exe -m scripts.training_business_runtime stop`')
        return text
    change('README.md',readme)
    entry='\n\n> 当前恢复状态17.68：'+note+'完整记录见[恢复验收](../'+report+')。历史不可用记录保留，不代表本次恢复后的状态。\n'
    handoff='''
### 17.68 在线依赖与四入口恢复验收（2026-10-15）

NOTE

本次Docker引擎已运行，但原/隔离五存储均停止；Milvus退出137且OOMKilled=false，不能据此认定内存故障或具体关停原因。按原统一入口等待存储健康、已发布快照校验和模型预热，再恢复隔离业务及r1/r2预览；保留同一容器ID与挂载对象。旧Docker API/Worker/模拟服务继续停止，只有当前配置匹配的专用Worker运行。

预览旧PID被复用导致安全检查阻断，新增preview_recovery_runtime适配器仅在创建时间不同或进程不存在时判为旧进程已退出，不接管/终止替代进程；同代命令、cwd、instance不符和AccessDenied继续失败。5项新回归验证该边界，旧封存控制器及候选未改。Docker Mounts数组重排引发一次原始哈希断言失败，保留失败记录及原哈希，通过同一容器ID、完整挂载对象排列匹配重新核实，没有修改卷。

本机登录：原8010用.runtime/original-local/open-platform.html；业务18021用.runtime/readonly-workspace/open-workspace.html；r1指南18024、r2指南18025分别用.runtime/sop-guide-preview/open-preview.html和.runtime/sop-record-preview/open-preview.html。凭据由原有自有控制器轮换，不共享Key或扩大权限。原8010生成开启/Agent关闭；业务生成关闭/只读诊断开启；两套指南生成与Agent关闭，保持STAGED、普通发布门禁未过。

原统一启停仍用scripts/start_platform.ps1、scripts/stop_platform.ps1。业务优先scripts/start_business_workspace.ps1，停止用scripts.training_business_runtime stop；指南使用`.venv/Scripts/python.exe -m scripts.preview_recovery_runtime guide-r1 start|status|stop`及guide-r2同命令，能安全识别旧PID复用。日期化恢复/验证脚本及本轮报告封存后不重跑覆盖。

历史封存在线基线的原/业务/r1/r2向量、原文、账本、业务表和配置11组均匹配，本轮只读验收前后快照相等。20个已暴露开发主题要求每条返回来源正确且实际重读引用；不算独立评测，独立分母仍0。Docker未启动及预热异常来自21项故障注入单元，本次真实引擎未关闭重演。3轮短时采样和后台更新不代表长期SLA。厂家型号资料、完整现场动作/停止/回退/恢复及人工真值仍有缺口。

完整报告：`.runtime/test/reports/entry-recovery-20261015/review.html`，含真实HTTP、Edge截图、历史及前后指纹、多轮观察、失败与修正记录。下一项将已完成的AMF离线内存演练作为独立训练页接入业务工作区；不据此启用pending正文、改业务权限、发布或新增模型外发。
'''.replace('NOTE',note)
    change('docs/HANDOFF_2026-10-07.md',lambda text:text.split('\n',1)[0]+entry+text.split('\n',1)[1]+handoff)
    plan='''
### 5.46 依赖恢复与真实读取验收完成（2026-10-15）

NOTE

8010保持local-content-quality-r1、原生成批准与预算；业务保持test-legacy-demo-r1；r1/r2指南保持独立STAGED。处理了旧预览PID复用：新适配器仅对创建时间不同的旧PID认定已退出，同代身份不符仍阻断，无关进程保留。原封存候选和代码不回写，旧Docker应用三容器不启用。

真实引擎此次已运行，Docker-off和依赖预热异常用21项单元覆盖；实际执行存储恢复、健康等待、快照校验、模型预热、业务关联原文和全部开发主题每条证据核对。历史11组数据/配置一致、本轮读前后快照一致，3轮连续观察和后台更新已验证；不声明长期稳定性或独立质量通过。

下一项：把AMF内存演练接入业务工作区作为独立训练页，保留演练确认、停止/回退/恢复边界与真实权限区别。pending正文不入库，指南不合并8010或发布，不新增DeepSeek外发。设备资料与独立人工问题/真值仍按准确范围处理。详见交接17.68及entry-recovery-20261015报告。
'''.replace('NOTE',note)
    change('docs/DEVELOPMENT_PLAN_2026-10-07.md',lambda text:text.split('\n',1)[0]+entry+text.split('\n',1)[1]+plan)
    change('docs/README.md',lambda text:text.replace('## 开发入口：先读这两份\n','## 开发入口：先读这两份\n\n- [当前交接17.68](HANDOFF_2026-10-07.md)：'+note+'计划5.46，见[实际恢复验收](../'+report+')。\n',1))


def main():
    result=read('verified.json');assert result['status']=='passed'
    assert result['topic_checks']==20 and result['stable_samples']==3 and result['background_samples']>=2
    assert len(result['containers'])==10 and all(r['status']=='running' and r['health']=='healthy' for r in result['containers'])
    first=json.loads((REPORT/'events.jsonl').read_text('utf-8').splitlines()[0])['at_utc']
    observations=[json.loads(line) for line in (PROJECT/'.runtime/platform-entry/observations.jsonl').read_text('utf-8').splitlines()]
    observations=[r for r in observations if r['checked_at_utc']>=first]
    write('startup-and-observer-timeline.json',observations)
    assert any(r.get('code')=='dependencies_warming' for r in observations)
    assert any(r.get('code')=='application_starting' for r in observations)
    assert all(read('historical-data-comparison.json').values())
    assert all(r['same_id'] and r['same_mount_objects'] for r in read('container-identity-verification.json'))
    assert read('status-browser-result.json')['returncode']==0
    assert read('browser-result.json')['returncode']==0
    browser=read('topic-browser/browser-checks.json');assert len(browser)==4 and all(r['passed'] for r in browser)
    status=read('status-browser/browser-result.json')
    assert status['fresh_login'] and status['stale_login_hidden'] and not status['mobile_overflow']
    tests=0
    for name in ['platform-entry-tests.xml','preview-recovery-tests.xml']:
        root=ET.parse(REPORT/name).getroot()
        cases=list(root.iter('testcase'))
        assert cases and not any(list(root.iter(tag)) for tag in ['failure','error','skipped'])
        tests+=len(cases)
    assert tests==26
    for folder in ('topic-browser','status-browser'):
        assert not (REPORT/folder/'edge-profile').exists(), 'Remove exited owned browser profile before sealing'
    code=('scripts/recover_training_entries_20261015.py','scripts/preview_recovery_runtime.py',
          'scripts/verify_entry_recovery_20261015.py','scripts/finalize_entry_recovery_20261015.py',
          'tests/unit/test_preview_recovery_runtime.py')
    write('code-bindings.json',{name:hashlib.sha256((PROJECT/name).read_bytes()).hexdigest() for name in code})
    summary=dict(result,unit_tests=tests,topic_browser_checks=4,status_browser_verified=True,
       original_version='local-content-quality-r1',isolated_business_version='test-legacy-demo-r1',
       guide_versions=['test-sop-evidence-r1','test-sop-records-r2'],new_admissions=0,new_ingestions=0,new_publications=0,
       recovery_notes=[
          'Both existing storage groups were stopped. Milvus exit code 137 with OOMKilled=false does not establish the shutdown cause.',
          'First preview recovery was correctly blocked by a reused PID. The new adapter ignores only different creation times and preserves all same-generation identity failures.',
          'A raw Mounts-array hash assertion failed because Docker reordered entries. The original hashes are retained; permutation checks prove identical mount objects and unchanged container IDs.',
          'Restored snapshot and SQL equality are checked against the prior sealed online baseline, and again before/after the current read-only acceptance.',
          'Technical login keys rotated only through existing owned controllers; no new human reviewer identity or role expansion.'])
    write('summary.json',summary)
    links=[('8010问答入口','.runtime/original-local/open-platform.html'),
           ('业务工作区18021','.runtime/readonly-workspace/open-workspace.html'),
           ('指南r1 · 18024','.runtime/sop-guide-preview/open-preview.html'),
           ('指南r2 · 18025','.runtime/sop-record-preview/open-preview.html'),
           ('持续状态页','.runtime/platform-entry/status.html')]
    items=''.join('<li><a href="'+html.escape((PROJECT/path).as_uri(),quote=True)+'">'+label+'</a></li>' for label,path in links)
    page='''<!doctype html><html lang="zh-CN"><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>依赖与入口恢复验收 · 2026-10-15</title><style>body{font:16px/1.8 system-ui;background:#f3f6fb;color:#20334d;margin:0}main{max-width:900px;margin:30px auto;background:white;padding:30px;border-radius:14px}a{color:#3659cb}code{background:#edf1fa}img{width:100%;border:1px solid #ddd}h1{font-size:26px}</style>
<main><h1>依赖与入口恢复验收</h1><p>2026-10-15：四个入口恢复；10个既有存储容器健康，保留容器与数据卷。</p>
<p>UNIT项单元、HTTP项真实HTTP、20个已暴露开发主题及逐条引用重读、4项新版检索Edge检查、启动状态页桌面/手机/过期提示通过。四个入口连续采样3轮，后台观察持续更新。</p>
<h2>本机入口</h2><ul>LINKS</ul><p>登录文件包含本机技术凭据，请勿分享文件。原8010生成已按旧批准开启；本次验收只读，没有发送生成问答或调用付费模型。</p>
<h2>数据核验</h2><p>原库、业务库、r1/r2指南库及配置共11组指纹匹配上次封存在线基线，本轮只读验收前后快照一致。未新建候选、入库、发布或外发；两套指南继续STAGED，普通发布门禁仍未通过。</p>
<h2>发现并处理</h2><p>全部存储停止导致Milvus不可连接，按现有启动流程恢复并等待真实预热。旧预览PID被复用，新的恢复适配器根据创建时间识别已消失进程，保留其他进程和严格身份检查。Docker挂载数组顺序变化导致一次哈希断言失败，保留原记录并按同一挂载对象重新核对。</p>
<h2>验收范围</h2><p>真实Docker引擎本次已运行；Docker未启动、预热和失败原因分支来自故障注入单元测试。本次没有关闭Docker来重演。连续采样属于短时存活检查，不能证明长期SLA。20道题为已暴露开发题，独立分母仍为0；完整现场SOP、设备资料和人工真值缺口保留。</p>
<p><a href="summary.json">完整摘要</a> · <a href="http-checks.json">真实HTTP记录</a> · <a href="historical-data-comparison.json">历史指纹比较</a> · <a href="stable-samples.json">连续采样</a> · <a href="background-observations.json">后台观察</a> · <a href="events.jsonl">含失败的过程记录</a></p>
<h2>实际浏览器画面</h2><img alt="新版指南检索及原文" src="topic-browser/search-desktop.png"><img alt="启动状态" src="status-browser/status-desktop.png"></main></html>'''
    page=page.replace('UNIT',str(tests)).replace('HTTP',str(result['http_checks']),1).replace('LINKS',items)
    (REPORT/'review.html').write_text(page,encoding='utf-8')
    update_docs(result)
    checksums={str(p.relative_to(REPORT)).replace('\\','/'):hashlib.sha256(p.read_bytes()).hexdigest()
               for p in sorted(REPORT.rglob('*')) if p.is_file() and p.name!='artifact-hashes.json'}
    write('artifact-hashes.json',checksums)
    print(json.dumps(dict(status='sealed',unit_tests=tests,http_checks=result['http_checks'],topic_checks=20,paid_calls=0)))


if __name__=='__main__':main()
