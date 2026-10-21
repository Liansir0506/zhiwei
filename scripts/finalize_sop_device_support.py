"""Seal the source audit and the successful bounded real-storage verification."""
import html
import shutil
import xml.etree.ElementTree as ET
from scripts.prepare_sop_device_support import OUTPUT, validate
from scripts.verify_sop_device_support import REPORT
from scripts.prepare_legacy_projection import read,write,digest,seal,verify_seal
from scripts import content_quality_release_runtime as rt


def main():
    if (REPORT/'artifact-hashes.json').exists():raise ValueError('report already sealed')
    validate()
    attempts=sorted(REPORT.glob('attempt-*'))
    completed=[p for p in attempts if (p/'state.json').exists() and read(p/'state.json')['state']=='completed']
    if len(completed)!=1:raise ValueError('exactly one complete real verification required')
    attempt=completed[0]
    state=read(attempt/'state.json');before=read(attempt/'before.json');after=read(attempt/'after.json')
    assert before==after and state['paid_calls']==state['new_admissions']==state['vector_writes']==0
    stored=read(attempt/'stored-sop-assembly.json');faqs=read(attempt/'faq-source-bindings.json')
    assert stored['passed'] and len(stored['checks'])==200 and all(not x['ready'] for x in stored['checks'])
    assert faqs['passed'] and len(faqs['checks'])==8 and not faqs['qa_runtime_tested']
    health=read(attempt/'health-observations.json')['samples'];assert len(health)==3
    browser=read(REPORT/'browser-r2/browser-verification.json');assert browser['passed'] and len(browser['checks'])==8
    cleanup=read(REPORT/'browser-cleanup.json');assert set(cleanup['owned_profiles_removed'])=={'browser-r1','browser-r2'}
    unit=rt.PROJECT/'.runtime/support-review-unit-r3.xml'
    suites=list(ET.parse(unit).getroot().iter('testsuite'))
    counts={k:sum(int(s.get(k,0)) for s in suites) for k in ('tests','failures','errors','skipped')}
    assert counts==dict(tests=19,failures=0,errors=0,skipped=0)
    for name in ('support-review-unit.xml','support-review-unit-r2.xml','support-review-unit-r3.xml'):
        target=REPORT/name
        if target.exists():raise ValueError('unit evidence must not be overwritten')
        shutil.copyfile(rt.PROJECT/'.runtime'/name,target)
    audit=read(OUTPUT/'sop-support-audit.json')
    summary=dict(protocol='sop-device-support-step2-v1',scope='content support preparation, not complete operational SOP acceptance',
        version=rt.VERSION,sop_files=200,stored_sop_chunks=sum(x['stored_chunks'] for x in stored['checks']),body_families=20,
        sop_literal_quotes_bound=True,full_source_chunking_replay_matched=200,
        stored_assembly_safety_blocked=200,approval_missing=200,rollback_missing=200,
        generic_missing_evidence_stop_present=200,network_applicability_conflicts=0,
        prior_lte_conflict_claim_corrected=True,complete_sop_approved=0,
        existing_bounded_faqs=8,pending_device_worksheets=8,manufacturer_manuals_supplied=0,
        family_repair_specifications=20,development_exposed_cases=28,independent_frozen=0,
        unit=counts,browser_checks=8,health_samples=3,
        current_active_unchanged=True,protected_sql_vectors_acl_and_files_unchanged=True,
        new_admissions=0,published=False,paid_calls=0,llm_requests=0,
        qa_route_or_recall_tested=False,actual_runtime_switch_executed=False,
        review_path=str(OUTPUT/'review.html'),review_seal_sha256=digest(OUTPUT/'artifact-hashes.json'),
        successful_attempt=attempt.name,failed_attempts=[p.name for p in attempts if p!=attempt],
        failures_retained=['initial literal audit wrongly assumed all network types were 5G; corrected from each metadata declaration',
            'Windows CRLF template comparison corrected while keeping original-byte SHA bindings',
            'initial raw-text equality ignored NFKC punctuation and repeated title context; exact frozen chunking replay now matches',
            'r1 search included English status text; r2 uses exact topic/source search fields'],
        limitations=['厂家/型号/软件版本、门限及完整批准动作/停止/回退/恢复依据未提供',
            'AI逐字审查与开发反例不能代替人工语义金标或独立质量',
            '真实存储组装检查不覆盖问答召回/排序/生成效果；短窗口不证明长期稳定',
            '8份新记录单pending，未准入/向DeepSeek外发；原FAQ准入不延伸到新正文'],
        code_bindings={n:digest(rt.PROJECT/n) for n in (
            'qa_core/quality/support_review.py','scripts/sop_device_support_content.py',
            'scripts/prepare_sop_device_support.py','scripts/verify_sop_device_support.py',
            'scripts/finalize_sop_device_support.py','tests/unit/test_support_review.py','tests/browser/support_review_smoke.js')})
    write(REPORT/'final-summary.json',summary)
    esc=lambda x:html.escape(str(x),quote=True)
    link=esc((OUTPUT/'review.html').as_uri())
    body=f"""<!doctype html><html lang='zh-CN'><meta charset='utf-8'><meta name='viewport' content='width=device-width,initial-scale=1'>
<title>SOP与设备支持性第二步报告</title><style>body{{font:16px/1.7 system-ui;max-width:1000px;margin:30px auto;padding:20px;background:#f5f7fb;color:#1b2940}}li,p{{overflow-wrap:anywhere}}a{{color:#315cbb}}</style>
<h1>SOP与设备支持性：本步审查和资料准备完成</h1>
<p>实际核对200份SOP（20个正文家族），逐文件原文/元数据SHA和行号证据绑定。审批及回退依据各缺200份；停止句存在，但完整动作、观察周期和恢复判据不足。可执行流程批准0。</p>
<p>当前向量库2200个SOP片段按冻结分块规则逐片段相符；200份完整组装全部因缺安全控制而阻断。这是实际存储组装检查，不是问答召回、生成效果或独立评测。</p>
<p>19项单元检查、8项真实无窗口Edge检查、8份已获准FAQ绑定核对、3次短窗口入口观察通过。原ACTIVE、SQL/向量/权限、封存资料、环境文件和预算前后保持；付费及模型调用0。</p>
<p><a href='{link}'>集中支持性审核页：20组修订规格与8份设备记录单</a> · <a href='final-summary.json'>完整结果与边界</a></p>
<p>设备记录单是新增pending草稿，没有自动准入、发布或新增外发。厂家/型号/软件版本、门限与完整现场流程仍待补。28道公开开发反例均不计独立样本；独立冻结分母0。</p>
<p>前述LTE/5G冲突推断已纠正：真实LTE原文和元数据均为4G。保留首轮模板匹配、原文规范化比较和搜索失败证据，最终结果取完成批次。</p>
<p>下一步为受控独立质量准备：划定来源/正文家族隔离与曝光登记，再准备未暴露样本的人工真值流程。不得把这28道已暴露开发题包装为独立结果。</p></html>"""
    (REPORT/'review.html').write_text(body,encoding='utf-8')
    verify_seal(OUTPUT);verify_seal(rt.merge.CANDIDATE);verify_seal(rt.REVIEW)
    seal(REPORT);verify_seal(REPORT)
    print('Sealed: 19 unit / 200 stored SOP packets / 8 FAQ bindings / 8 Edge checks; operational approval 0; paid 0')


if __name__=='__main__':main()
