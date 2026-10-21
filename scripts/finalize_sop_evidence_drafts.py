"""Seal concrete pending guides and bounded factual verification, never admission."""
from collections import Counter
import html
import json
from pathlib import Path
import shutil
import xml.etree.ElementTree as ET

from scripts.verify_sop_evidence_drafts import REPORT
from scripts.prepare_sop_evidence_drafts import ROOT,OUTPUT,EXPOSURE,validate
from scripts.prepare_legacy_projection import read,write,seal,verify_seal,digest


def main():
    if (REPORT/'artifact-hashes.json').exists():raise ValueError('already sealed')
    validate();verify_seal(EXPOSURE)
    attempts=[dict(folder=p.name,**read(p/'state.json')) for p in sorted(REPORT.glob('attempt-*')) if p.is_dir()]
    completed=[a for a in attempts if a['state']=='completed'];assert len(completed)==1
    winner=completed[0];attempt=REPORT/winner['folder'];assert read(attempt/'before.json')==read(attempt/'after.json')
    rows=read(attempt/'preview-results.json');assert len(rows)==120 and all(r['passed'] and not r['result']['action_authorized'] for r in rows)
    browser=read(attempt/'browser/browser-verification.json');assert browser['passed'] and len(browser['checks'])==9
    offline=read(REPORT/'offline-browser/browser-verification.json');assert offline['passed']
    cleanup=read(REPORT/'browser-cleanup.json');assert len(cleanup)==2 and all(r['owned_browser_exited'] and r['profile_removed'] for r in cleanup)
    recovery=read(REPORT/'environment-recovery-r2.json');assert recovery['state']=='completed'
    exposure=read(REPORT/'exposure-verification.json');assert exposure['passed']
    verify_seal(Path(exposure['screen']['report']).parent)
    empty=read(attempt/'empty-intake-screen.json');assert empty['state']=='blocked' and empty['case_count']==0
    verify_seal(Path(empty['report']).parent)
    source=ROOT/'.runtime/sop-evidence-preview-unit-20261014.xml';shutil.copyfile(source,REPORT/source.name)
    suites=list(ET.parse(source).getroot().iter('testsuite'))
    counts={k:sum(int(s.get(k,0)) for s in suites) for k in ('tests','failures','errors','skipped')}
    assert counts==dict(tests=142,failures=0,errors=0,skipped=0)
    previous=read(ROOT/'.runtime/test/reports/development-selfcheck-20261013/summary.json')
    baseline=read(ROOT/'.runtime/test/reports/development-selfcheck-20261013'/previous['winning_browser']/'after.json')
    before=read(attempt/'before.json')
    prior_match={k:before[k]==baseline[k] for k in ('active','original_sql','readonly_sql','env_sha256','budget_sha256')}
    assert all(prior_match.values()), 'protected corpus differs from last verified state'
    package=read(OUTPUT/'review-package.json');assert len(package['entries'])==20
    codes=('qa_core/quality/sop_evidence_preview.py','scripts/sop_evidence_draft_content.py',
        'scripts/prepare_sop_evidence_drafts.py','scripts/verify_sop_evidence_drafts.py',
        'scripts/recover_sop_review_environment.py','scripts/finalize_sop_evidence_drafts.py',
        'tests/unit/test_sop_evidence_preview.py','tests/browser/sop_evidence_drafts_smoke.js')
    write(REPORT/'code-bindings.json',{n:digest(ROOT/n) for n in codes})
    summary=dict(state='completed',scope='pending project-authored evidence guides and offline simulation; not operational SOP or independent quality',
        pending_guides=20,specific_record_groups=60,parent_files=200,parent_families=20,
        unit=counts,simulation_cases=120,simulation_outcomes=dict(Counter(r['result']['state'] for r in rows)),
        browser_checks=9,health_samples=3,winning_attempt=winner['folder'],attempts=attempts,
        protected_validation_before_after_equal=True,protected_corpus_matches_last_verified_state=prior_match,
        paid_calls=0,model_calls=0,qa_requests=0,new_admissions=0,vector_writes=0,new_staged=0,
        new_configuration_published=False,complete_operational_sop_approved=0,human_gold=False,
        independent_frozen=0,independent_quality_metrics=None,new_developer_question_occurrences=20,
        exposure_seal_sha256=digest(EXPOSURE/'artifact-hashes.json'),pending_review_seal_sha256=digest(OUTPUT/'artifact-hashes.json'),
        recovery_scope='Existing stopped storage and stale owned entries recovered using existing controllers',
        original_owned_key_reissued=recovery['original_key_reissued'],readonly_owned_key_reissued=recovery['readonly_key_reissued'],
        temporary_evaluation_keys_created=0,original_and_readonly_controllers_unchanged=True,
        real_authority_or_record_semantics_verified_by_preview=False,manufacturer_documents_supplied=0,
        limitations=['New body, identity and access proposal remain pending; no ingestion or model outbound authorization',
            'Simulation checks declarations and schema, not actual evidence/authority/causality',
            'Complete actions, approval, rollback and recovery require site-specific applicable sources',
            'No QA recall/ranking/generation or independent human quality assessed',
            'Short health observations do not establish long-term survival'])
    write(REPORT/'summary.json',summary)
    esc=lambda x:html.escape(str(x),quote=True)
    rows_html=''.join(f"<tr><td>{esc(e['equipment'])}</td><td>{esc(e['alarm'])}</td><td>{esc(e['hypothesis'])}（待验证）</td><td>pending</td></tr>" for e in package['entries'])
    page=f'''<!doctype html><html lang="zh-CN"><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>SOP取证指南完善报告</title>
<style>body{{font:16px/1.7 system-ui;color:#20314c;background:#f4f6fb;margin:0}}main{{max-width:1050px;margin:auto;padding:24px}}section{{background:white;padding:20px;margin:16px 0;border-radius:10px}}h1{{font-size:26px}}p,li,a{{overflow-wrap:anywhere}}a{{color:#245cc3}}.scroll{{overflow:auto}}table{{border-collapse:collapse;min-width:700px;width:100%}}td,th{{padding:9px;border-bottom:1px solid #dde5f0}}img{{max-width:100%;height:auto}}@media(max-width:500px){{main{{padding:12px}}section{{padding:14px}}}}</style><main>
<h1>20份SOP取证与条件分支正文已准备</h1><section><p>20个旧正文家族、200份原件关联，60组具体记录项；每份补支持/相反记录、可比性、停止与交接条件。旧模板原因只作为待验证假设。</p>
<p>新正文、来源身份及default租户ops/admin内部访问范围全部pending。准入/新STAGED/发布0，完整现场SOP批准0；缺厂家型号/版本资料、设备阈值和具体动作/回退/恢复依据。</p>
<p><a href="{esc((OUTPUT/'review.html').as_uri())}">集中审核页与20份完整正文</a> · <a href="summary.json">结果与范围</a> · <a href="{winner['folder']}/preview-results.json">120项开发模拟</a></p></section>
<section><h2>实际验证</h2><p>142项单元、120项公开模拟分支、9项无窗口Edge检查通过。模拟覆盖支持、相反、同时矛盾、均未支持、缺记录与无权读取，每个结果均不确认根因、不授权动作。补充单元核对对象/版本/出处/单位、时间时区及观察窗交集。</p>
<p>模拟的字段、授权和分支声明均为开发输入；预览不核验真实原件内容、真实权限或因果。审核页已检查20正文、200来源绑定、搜索、pending和缺口、全部本地链接及390px布局，并目视复核。</p>
<p>新20条公开问题及模板在运行前登记曝光，v2实际控制阻断；空题集仍阻断。人工真值/独立冻结0、指标null。无QA请求、模型/付费0，未测召回、重排或生成质量。</p>
<img alt="实际审核页AMF取证分支" src="{winner['folder']}/browser/sop-evidence-desktop.png"></section>
<section><h2>依赖恢复及保护快照</h2><p>今天首轮向量快照连接失败，既有存储容器停止、两个归属入口stale。沿用统一准备及只读启动控制器恢复；未创建容器或启动旧配置API/Worker。隔离容器首次健康后ACTIVE快照验证仍失败，后续实际准入/ACTIVE核验通过后重试成功。全部失败保留。</p>
<p>现有启动控制器重新签发两个本机入口凭据；当前版本/生成/Agent模式与角色范围保持，非临时评测Key。请通过本机登录页重新进入，不使用旧浏览器凭据。本报告不声称恢复阶段Key或PID保持不变。</p>
<p>恢复后本轮验证前后完整原/隔离SQL知识业务任务发布、向量及权限、环境/预算与父包封存相同；知识快照也与上一步最后核验状态相同。3次有界健康观察通过，不代表长期存活验收。</p>
<a href="environment-recovery.json">首轮恢复失败</a> · <a href="environment-recovery-r2.json">成功恢复及凭据变化</a> · <a href="{winner['folder']}/before.json">验证前</a> · <a href="{winner['folder']}/after.json">验证后</a></section>
<section><h2>待审范围</h2><div class="scroll"><table><tr><th>对象</th><th>告警</th><th>原模板假设</th><th>新正文状态</th></tr>{rows_html}</table></div><p>新指南定位为guide，只补取证/交接，不能作为完整处置SOP。旧200份原文、权限和8份设备记录单pending状态保持。具体准入决定不由单元或模拟测试代替，新增外发与发布另按准确授权范围执行。</p></section></main></html>'''
    (REPORT/'review.html').write_text(page,encoding='utf-8')
    seal(REPORT);verify_seal(REPORT);validate()
    print(json.dumps(dict(state='sealed',pending_guides=20,unit_tests=142,simulation_cases=120,browser_checks=9,
        paid_calls=0,report=str(REPORT/'review.html'))))


if __name__=='__main__':main()
