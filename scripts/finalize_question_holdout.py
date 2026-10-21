"""Seal preparation evidence; do not turn empty human intake into quality results."""
import html
import json
import xml.etree.ElementTree as ET

from scripts.prepare_question_holdout import ROOT, OUTPUT, validate
from scripts.verify_question_holdout import REPORT
from scripts.prepare_legacy_projection import read,write,digest,seal,verify_seal


def main():
    if (REPORT/'artifact-hashes.json').exists():raise ValueError('report already sealed')
    validate();expected=digest(OUTPUT/'artifact-hashes.json')
    successful=[p for p in REPORT.glob('attempt-*') if read(p/'state.json').get('state')=='completed' and
                read(p/'state.json').get('preparation_seal_sha256')==expected]
    if len(successful)!=1:raise ValueError('one exact completed preparation verification required')
    attempt=successful[0];state=read(attempt/'state.json')
    assert read(attempt/'before.json')==read(attempt/'after.json')
    assert state['real_registry_controls']==5 and state['health_samples']==3
    assert state['new_human_cases']==state['new_truth']==state['frozen']==state['model_calls']==0
    browser=read(REPORT/'browser-verification.json');assert browser['passed'] and len(browser['checks'])==8
    cleanup=json.loads((REPORT/'browser-cleanup.json').read_text(encoding='utf-8-sig'))
    assert cleanup['owned_profile_removed']==str(REPORT/'edge-profile') and not cleanup['user_windows_touched']
    assert not (REPORT/'edge-profile').exists()
    unit=OUTPUT/'unit-verification.xml'
    suites=list(ET.parse(unit).getroot().iter('testsuite'))
    counts={k:sum(int(s.get(k,0)) for s in suites) for k in ('tests','failures','errors','skipped')}
    assert counts==dict(tests=56,failures=0,errors=0,skipped=0)
    screen_root=ROOT/'.runtime/quality/question-intake/screens'
    screens=[p for p in screen_root.iterdir() if p.is_dir() and (p/'screen.json').exists() and
             read(p/'screen.json').get('preparation_seal_sha256')==expected]
    assert len(screens)==1
    verify_seal(screens[0]);cli=read(screens[0]/'screen.json')
    assert cli['state']=='blocked' and cli['case_count']==0 and not cli['run_authorized']
    (REPORT/'unit-verification.xml').write_bytes(unit.read_bytes())
    write(REPORT/'offline-cli-screen.json',cli)
    files=('qa_core/quality/question_holdout.py','scripts/prepare_question_holdout.py',
        'scripts/screen_question_holdout.py','scripts/verify_question_holdout.py','scripts/finalize_question_holdout.py',
        'tests/unit/test_question_holdout.py','tests/browser/question_holdout_smoke.js')
    summary=dict(protocol='known-corpus-question-holdout-preparation-step4-v1',version='local-content-quality-r1',
        scope='explicit known-corpus new question/template holdout preparation, not source-independent acceptance',
        source_assets=306,unit=counts,browser_checks=8,real_registry_metadata_controls=5,health_samples=3,
        preparation_seal_sha256=expected,successful_attempt=attempt.name,
        aliases='role placeholders A/B; actual distinct custody/review not verified',
        source_independent=False,new_human_cases=0,new_truth_files=0,verified_question_holdout_frozen=0,
        verified_source_independent_frozen=0,quality_metrics=None,qa_evaluation_executed=False,
        freeze_executed=False,model_calls=0,paid_calls=0,new_admissions=0,temp_api_keys_created=0,
        original_active_unchanged=True,protected_sql_vectors_acl_files_budget_unchanged=True,
        original_8010_switched=False,previous_source_holdout_policy_unchanged=True,
        limitations=['人类题集、真值与实际审核凭据尚未提供；匿名角色不是认证真人或已审核记录',
            '共享来源/正文/主题是明确范围，不能称来源独立或泛化到生产设备',
            '哈希与模板声明不证明语义未曝光，截图/聊天/源码曝光仍须人工核对',
            '现库SOP与设备阈值/流程仍有缺口；本包不新增准入、完整运行快照或模型外发许可',
            '三次十秒间隔健康观察不证明长期稳定；五类元数据控制不是质量题目'],
        code_bindings={p:digest(ROOT/p) for p in files})
    write(REPORT/'final-summary.json',summary)
    link=html.escape((OUTPUT/'review.html').as_uri(),quote=True)
    (REPORT/'review.html').write_text(f'''<!doctype html><html lang="zh-CN"><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>新问题留出工程准备报告</title><style>body{{font:16px/1.7 system-ui;max-width:1000px;margin:30px auto;padding:20px;color:#1c2b42;background:#f3f6fa}}p,li,code{{overflow-wrap:anywhere}}a{{color:#315ebd}}</style>
<h1>现有语料新问题留出：工程交接准备完成</h1><p>用户选择已知语料的新问题留出，并要求不透露人员姓名。使用命题保管人A和真值审核人B作为职责占位；未编造真人、凭据或已审核记录。来源/正文/主题共享允许，原来源组隔离策略保持封存。</p>
<p>306份精确资产及正文族清单、空白哈希交接、A/B人工作业、真值证据要求、冻结前条件、评分和失败分母规则已准备。真实题集/真值/冻结均0，quality_metrics=null。</p>
<p>56单元（新范围32＋旧策略24）、8项真实无窗口Edge通过。实际曝光清单上的空模板、共享来源、旧冻结题、pending来源和正文错绑五类元数据控制符合预期；共享来源静态通过仍不授权冻结或运行。CLI真实空模板返回阻断。</p>
<p>3次十秒间隔原/隔离健康观察通过，前后保护SQL/原文/向量/权限/封存包/环境/预算一致。模型/付费0、新准入0、临时Key0，原8010未切换。本轮没有运行问答或独立质量评测。</p>
<p><a href="{link}">集中交接页和A/B任务单</a> · <a href="final-summary.json">结果与限制</a> · <a href="offline-cli-screen.json">真实空交接筛查</a></p>
<p>保管人员只交接元数据副本，可使用以下离线入口；问句、答案和保管目录不要交给开发助手。报告写入独立.runtime/quality/question-intake/screens新目录，不覆盖封存模板：</p>
<p><code>.venv/Scripts/python.exe -m scripts.screen_question_holdout --manifest &lt;哈希交接JSON的绝对路径&gt;</code></p>
<p>下一步由A/B在保管环境完成真实命题与另一轮真值/语义审核。随后核验人员隔离、保管凭据和元数据，准备具体冻结/运行/分母与外发方案。本包不授予未来题集模型外发、冻结或自动运行许可。</p>
<p>已知语料题目留出不具备来源独立性，也不证明现场生产质量。SOP安全处置与厂家型号资料继续缺；哈希不同、角色代号或有页面不能替代人类审核。</p></html>''',encoding='utf-8')
    validate();seal(REPORT);verify_seal(REPORT)
    print('Sealed: 56 unit / 8 Edge / five real metadata controls; human cases and frozen 0; model calls 0')


if __name__=='__main__':main()
