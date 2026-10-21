"""Seal factual development acceptance, including all failed attempts and limits."""
from collections import Counter
import html
import json
from pathlib import Path
import shutil
import xml.etree.ElementTree as ET

from scripts.run_development_selfcheck import ROOT,REPORT,EXPOSURES
from scripts.prepare_legacy_projection import read,write,seal,verify_seal,digest
from scripts.prepare_question_holdout import OUTPUT,PREVIOUS


def main():
    if (REPORT/'artifact-hashes.json').exists():raise ValueError('already sealed')
    attempts=[dict(folder=p.name,**read(p/'state.json')) for p in sorted(REPORT.glob('attempt-*'))]
    successful=[a for a in attempts if a.get('component_pass')==43 and a.get('http_pass')==19]
    assert len(successful)==1
    winner=successful[0];attempt=REPORT/winner['folder']
    assert read(attempt/'before.json')==read(attempt/'after.json') and winner['provider_attempts']==0
    components=read(attempt/'component-results.json');assert len(components)==43 and all(r['passed'] for r in components)
    assert len(read(attempt/'http-results.json'))==19
    reload=read(REPORT/'runtime-reload.json');assert reload['state']=='completed' and reload['before']==reload['after']
    browsers=[dict(folder=p.name,**read(p/'state.json')) for p in sorted(REPORT.glob('browser-*')) if p.is_dir()]
    successful_browser=[b for b in browsers if b['state']=='completed'];assert len(successful_browser)==1
    browser=successful_browser[0];bdir=REPORT/browser['folder'];assert read(bdir/'before.json')==read(bdir/'after.json')
    assert read(bdir/'browser-verification.json')['passed'] and browser['browser_checks']==9
    assert all(not (REPORT/b['folder']/'edge-profile').exists() for b in browsers)
    controls=read(REPORT/'exposure-controls/verification.json');assert controls['passed']
    for row in controls['controls']:verify_seal(Path(row['screen_report']).parent)
    verify_seal(Path(controls['empty_intake']['report']).parent)
    units=[]
    for name in ('development-selfcheck-boundary-unit-r1.xml','development-exposure-unit-r1.xml'):
        source=ROOT/'.runtime'/name;destination=REPORT/name;shutil.copyfile(source,destination)
        suites=list(ET.parse(destination).getroot().iter('testsuite'))
        counts={k:sum(int(s.get(k,0)) for s in suites) for k in ('tests','failures','errors','skipped')}
        assert counts['failures']==counts['errors']==counts['skipped']==0
        units.append(dict(file=name,**counts))
    assert sum(r['tests'] for r in units)==125
    overlays=[]
    for folder in sorted(EXPOSURES.iterdir()):
        verify_seal(folder);overlays.append(dict(folder=str(folder.relative_to(ROOT)),seal_sha256=digest(folder/'artifact-hashes.json')))
    assert len(overlays)==4
    for folder in (OUTPUT,PREVIOUS):verify_seal(folder)
    codes=('qa_core/intent/rules.py','qa_core/quality/development_exposure.py',
        'scripts/run_development_selfcheck.py','scripts/restart_development_boundary.py',
        'scripts/verify_development_boundary_page.py','scripts/screen_question_holdout_v2.py',
        'scripts/verify_development_exposure_controls.py','scripts/finalize_development_selfcheck.py',
        'tests/browser/development_boundary_smoke.js','tests/unit/test_development_exposure.py',
        'tests/unit/test_development_boundary_fixes.py')
    write(REPORT/'code-bindings.json',{name:digest(ROOT/name) for name in codes})
    summary=dict(state='completed',kb_version='local-content-quality-r1',scope='assistant-authored exposed development selfcheck',
        component_pass=43,component_total=43,http_pass=19,http_total=19,unit_tests=125,browser_checks=9,
        category_counts=dict(Counter(r['category'] for r in components)),attempts=attempts,browser_attempts=browsers,
        winning_attempt=winner['folder'],winning_browser=browser['folder'],development_overlays=overlays,
        protected_state_unchanged=True,paid_calls=0,temporary_keys_created=0,
        created_development_sessions=2,created_development_messages=8,
        original_reloaded=True,executable='pythonw.exe',version_published=False,key_rotated=False,
        human_gold=False,independent_frozen=0,independent_quality_metrics=None,generation_quality_assessed=False,
        exposure_coverage_complete=False,exposure_cli_controls=3,empty_intake_blocked=True,
        limitations=['No complete site SOP or equipment thresholds supplied',
            'No independent human truth or unseen denominator',
            'No model generation quality assessed',
            'No qualified full corpus increment or new 8010 configuration publication',
            'Short health observations do not establish long-term stability'])
    write(REPORT/'summary.json',summary)
    rows=''.join(f'<tr><td>{html.escape(r["id"])}</td><td>{html.escape(r["category"])}</td><td>{html.escape(r["query"])}</td><td>{html.escape(r["result"]["status"])}</td><td>{html.escape(r["result"]["route"])}</td><td>通过</td></tr>' for r in components)
    history=''.join(f'<li>{html.escape(a["folder"])}：{html.escape(a["state"])}；组件 {a.get("component_pass","未汇总")}/{a.get("component_total","未汇总")}；{html.escape(a.get("error_kind",""))}</li>' for a in attempts)
    page=f'''<!doctype html><html lang="zh-CN"><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>开发自检与边界修复</title>
<style>body{{font:16px system-ui;color:#21314c;background:#f3f6fb;margin:0}}main{{max-width:1120px;margin:auto;padding:24px}}section{{background:white;padding:20px;margin:16px 0;border-radius:12px}}h1{{font-size:26px}}p,li{{line-height:1.65}}a{{color:#245bdc;overflow-wrap:anywhere}}.scroll{{overflow:auto}}table{{border-collapse:collapse;width:100%;min-width:800px}}td,th{{padding:10px;text-align:left;border-bottom:1px solid #e3e9f2}}img{{max-width:100%;height:auto;border:1px solid #e3e9f2}}@media(max-width:500px){{main{{padding:12px}}section{{padding:14px}}}}</style>
<main><h1>开发自检与边界修复</h1><section id="scope"><p>当前版本 local-content-quality-r1；开发助手拟定并公开的自检，不是独立人工质量评测。</p>
<p id="counts">43/43 问答组件 · 19/19 真实只读 HTTP · 125 项单元 · 9 项真实无窗口 Edge。</p><p>31 个原始FAQ核对正文与来源；5 个改写核对回答及主题来源；2 个缺完整流程的问题保持证据边界；问候、注入、天气与2个权限控制另计。文档检查核对返回片段原文与身份，不等于完整手册或生成答案质量。</p></section>
<section id="fixes"><h2>修复及实际页面</h2><p>“忽略所有规则”命令被规则拦截；自然天气查询明确越界。网络故障中的天气影响和安全咨询仍保留业务识别。8010通过隐藏pythonw重载，当前版本、配置、API Key、原文向量和权限保持。</p>
<p>页面实际提交两道已曝光直答题，状态OUT_OF_SCOPE，来源数0，SSE结束。两次浏览器验证共留下2个明确开发会话、8条消息，不是业务知识变更。临时Key0、付费请求0。</p>
<a href="{browser['folder']}/browser-verification.json">浏览器实际结果</a> · <a href="runtime-reload.json">重载与保护快照</a><p><img alt="实际桌面会话" src="{browser['folder']}/boundary-desktop.png"></p></section>
<section id="exposure"><h2>后续留出防污染</h2><p>4个尝试的题集均在调用前登记曝光。v2入口合并后续问句、归一问句和模板哈希；3类实际CLI控制证明新增曝光被阻断，空题集继续阻断。原封存交接包不改。</p><p>独立冻结题0、人工真值未核验、独立指标null，语义/截图/聊天曝光覆盖仍不完整；匿名A/B只是职责占位，不拟造人工审核。</p><a href="exposure-controls/verification.json">实际CLI曝光阻断</a></section>
<section id="history"><h2>保留失败与修正记录</h2><ul>{history}</ul><p>首轮41/43，发现注入/天气错误走检索。两轮核对脚本KeyError分别来自：重分块参考卡未出现在增量投影表、工单号被误当文件名。已改为封存手册解析分块重放和资产身份核对。首轮浏览器9项页面通过，但会话核对未按NFKC规范化比较标点，包装验收失败；修正后第二轮完整通过，未重写首轮结果。</p></section>
<section id="limits"><h2>保留的开发缺口</h2><p>完整SOP、厂家型号/版本与设备阈值资料、人工新问题及真值、合格整库增量和原8010新配置发布、长期存活观察仍待完成。本轮零模型自检不评价生成质量，不能用这些公开题增加独立分母。</p></section>
<section><h2>逐题结果</h2><div class="scroll"><table><thead><tr><th>编号</th><th>范围</th><th>公开开发问题</th><th>状态</th><th>路由</th><th>结果</th></tr></thead><tbody>{rows}</tbody></table></div></section>
<section><a href="summary.json">汇总与范围</a> · <a href="{winner['folder']}/component-results.json">完整组件断言</a> · <a href="{winner['folder']}/http-results.json">HTTP结果</a> · <a href="development-selfcheck-boundary-unit-r1.xml">规则回归</a> · <a href="development-exposure-unit-r1.xml">曝光回归</a></section></main></html>'''
    (REPORT/'review.html').write_text(page,encoding='utf-8')
    seal(REPORT);verify_seal(REPORT)
    print(json.dumps(dict(state='sealed',component_pass=43,http_pass=19,unit_tests=125,browser_checks=9,paid_calls=0,report=str(REPORT/'review.html'))))


if __name__=='__main__':main()
