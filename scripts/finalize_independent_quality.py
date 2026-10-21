"""Seal verified preparation, preserving the zero independent denominator."""
import html
import shutil
import xml.etree.ElementTree as ET
from scripts.prepare_independent_quality import OUTPUT,validate
from scripts.verify_independent_quality import REPORT
from scripts.prepare_legacy_projection import read,write,digest,seal,verify_seal
from scripts import content_quality_release_runtime as rt


def main():
    if (REPORT/'artifact-hashes.json').exists():raise ValueError('report already sealed')
    validate();expected=digest(OUTPUT/'artifact-hashes.json')
    attempts=sorted(REPORT.glob('attempt-*'))
    final=[p for p in attempts if (p/'state.json').exists() and
           read(p/'state.json').get('state')=='completed' and
           read(p/'state.json').get('preparation_seal_sha256')==expected]
    if len(final)!=1:raise ValueError('one completed exact r2 verification required')
    attempt=final[0];state=read(attempt/'state.json')
    assert read(attempt/'before.json')==read(attempt/'after.json')
    assert state['empty_intake_blocked'] and state['old_frozen_reuse_blocked'] and state['source_only_pending_reuse_blocked']
    assert state['newly_exposed_dev_records']==28 and state['new_questions']==state['new_truth']==state['frozen']==state['paid_calls']==0
    assert len(read(attempt/'health-observations.json')['samples'])==3
    browser=read(REPORT/'browser-r2/browser-verification.json');assert browser['passed'] and len(browser['checks'])==8
    cleanup=read(REPORT/'browser-cleanup.json');assert set(cleanup['owned_profiles_removed'])=={'browser-r1','browser-r2'}
    unit=rt.PROJECT/'.runtime/independent-readiness-unit-r2.xml'
    suites=list(ET.parse(unit).getroot().iter('testsuite'))
    counts={k:sum(int(s.get(k,0)) for s in suites) for k in ('tests','failures','errors','skipped')}
    assert counts==dict(tests=24,failures=0,errors=0,skipped=0)
    for name in ('independent-readiness-unit-r1.xml','independent-readiness-unit-r2.xml'):
        target=REPORT/name
        if target.exists():raise ValueError('existing unit evidence cannot be overwritten')
        shutil.copyfile(rt.PROJECT/'.runtime'/name,target)
    registry=read(OUTPUT/'exposure-registry.json')
    summary=dict(protocol='independent-quality-preparation-step3-v1',version=rt.VERSION,
        scope='known exposure inventory and empty human intake, not evaluation/freeze/independence acceptance',
        exposure=registry['summary'],unit=counts,browser_checks=8,health_samples=3,
        empty_intake_blocked=True,old_frozen_reuse_blocked=True,public_source_only_pending_reuse_blocked=True,
        previously_exposed_support_cases=28,preparation_path=str(OUTPUT/'review.html'),preparation_seal_sha256=expected,
        successful_attempt=attempt.name,earlier_attempts=[p.name for p in attempts if p!=attempt],
        verified_independent_frozen=0,metrics_eligible=False,quality_metrics=None,
        new_questions=0,new_truth=0,new_admissions=0,qa_evaluation_executed=False,freeze_executed=False,
        paid_calls=0,model_requests=0,temp_api_keys_created=0,published=False,original_active_unchanged=True,
        protected_sql_vectors_acl_files_and_budget_unchanged=True,
        iteration='r1 known-question inventory retained; r2 additionally excludes public historical/pending source-only bodies',
        limitations=['声明链接/格式归一只能发现部分曝光；同义改写、聊天/截图/测试源码仍需独立人工核对',
            '任何哈希和人工身份字符串都不证明真实保管、真值或冻结授权；离线筛查不能自批',
            '当前采用来源/正文/主题/模板连通组隔离，现有306来源按该策略排除；已知语料新问题留出须另定范围',
            '没有新的独立命题人、审核凭据或未暴露题/真值，不能计算独立质量',
            '短窗口健康观察不证明长期稳定，未切换原8010或运行新的付费评测'],
        code_bindings={n:digest(rt.PROJECT/n) for n in ('qa_core/quality/independent_readiness.py',
            'scripts/prepare_independent_quality.py','scripts/verify_independent_quality.py','scripts/finalize_independent_quality.py',
            'tests/unit/test_independent_readiness.py','tests/browser/independent_readiness_smoke.js')})
    write(REPORT/'final-summary.json',summary)
    link=html.escape((OUTPUT/'review.html').as_uri(),quote=True);s=registry['summary']
    (REPORT/'review.html').write_text(f"""<!doctype html><html lang='zh-CN'><meta charset='utf-8'><meta name='viewport' content='width=device-width,initial-scale=1'>
<title>独立质量准备第三步报告</title><style>body{{font:16px/1.7 system-ui;max-width:1000px;margin:30px auto;padding:20px;color:#1c2b42;background:#f3f6fa}}p,li{{overflow-wrap:anywhere}}a{{color:#315ebd}}</style>
<h1>独立质量：曝光隔离与人工交接准备完成</h1>
<p>{s['input_files']}份输入 / {s['query_occurrences']}条问句出现记录 / {s['unique_canonical_queries']}个归一问句 / {s['connected_groups']}个关联组。出现记录不是独立题数。旧51条frozen记录全部排除；上一步28道开发反例保持曝光。</p>
<p>当前306份来源按来源组策略排除；共登记{s['public_source_occurrences']}份公开当前/历史/pending来源出现记录，包含没有题目的BFD/NETCONF卡。原pending不准入。独立冻结分母0，指标null，新题/真值0。</p>
<p>24项单元与8项真实无窗口Edge通过，真实空模板、旧冻结题及公开来源卡复用均阻断；3次原/只读健康观察，前后SQL/向量/权限/文件/预算不变。模型请求0，原8010未切换。</p>
<p><a href='{link}'>集中审核页及空白人工交接模板</a> · <a href='final-summary.json'>完整结果与边界</a></p>
<p>清单和离线筛查不核验真人身份、保管隔离、人工真值或语义独立。没有冻结/评测执行器或自批按钮。截图/聊天/测试源码和语义改写覆盖未完整；静态无冲突也必须人工审查，不能得到独立通过结论。</p>
<p>下一项需要新来源、独立命题/保管和另一人工审核。未来问句/真值应留在开发工作区之外，只交接SHA和证据/关联manifest；具体准入、冻结及运行按审核范围推进。已有语料新问题留出若采用，需另定明示范围，不冒称来源独立。</p></html>""",encoding='utf-8')
    validate();verify_seal(rt.PROJECT/'data/candidates/independent-quality-preparation-r1')
    seal(REPORT);verify_seal(REPORT)
    print('Sealed: 24 unit / 8 Edge / three real blocking controls; independent frozen 0; model calls 0')


if __name__=='__main__':main()
