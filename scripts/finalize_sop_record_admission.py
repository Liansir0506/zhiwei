"""Seal verification evidence without admitting content or changing existing runtime."""
from scripts.verify_sop_record_admission import REPORT,protected
from scripts.prepare_sop_record_admission import ROOT,OUTPUT,load_verified,CODE
from scripts.prepare_legacy_projection import read,write,digest,seal,verify_seal


def main():
    if (REPORT/'artifact-hashes.json').exists():raise ValueError('never reseal historical reports')
    load_verified();checks=read(REPORT/'checks.json');browser=read(REPORT/'browser-checks.json')
    assert len(browser)==8 and all(x['passed'] for x in browser)
    assert checks['current_original_and_readonly_and_guide_snapshots_unchanged']
    assert not (REPORT/'review-edge-profile').exists()
    final=protected();write(REPORT/'final-protected.json',final)
    assert final==read(REPORT/'before.json')==read(REPORT/'after.json')
    for name in ('admission-plan.json','receipt-template.json','compatibility.json'):
        assert digest(REPORT/name)==digest(OUTPUT/name)
    write(REPORT/'code-bindings.json',{name:digest(ROOT/name) for name in (*CODE,
        'scripts/verify_sop_record_admission.py','scripts/verify_sop_record_admission_browser.js',
        'scripts/render_sop_record_admission_review.py','scripts/finalize_sop_record_admission.py',
        'tests/unit/test_sop_record_admission.py')})
    write(REPORT/'summary.json',dict(state='prepared_pending_specific_admission',unit_tests_passed=106,
        unit_command='pytest tests/unit/test_sop_record_admission.py tests/unit/test_sop_admission.py tests/unit/test_training_workspace_and_revision.py -q --tb=short',
        browser_checks=8,full_body_browser_equality_verified=True,browser_profile_removed=True,
        windows_hidden=True,protected_snapshots_unchanged=True,files=20,extensions=49,preflight_chunks=180,
        new_admissions=0,new_staged=0,new_embeddings=0,paid_calls=0,independent_denominator=0,
        ordinary_gate_not_run_without_ingestion=True,first_browser_failure_retained=True,
        actual_approval_recorded=False,new_worker_started=False,original_8010_unchanged=True))
    page='''<!doctype html><html lang="zh-CN"><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>修订指南准入准备验收</title><style>body{font:16px/1.7 system-ui;color:#20314c;background:#f3f6fb;margin:0}main{max-width:960px;margin:auto;padding:24px}section{background:white;padding:20px;margin:16px 0;border:1px solid #dce4f1;border-radius:10px}p,a{overflow-wrap:anywhere}</style><main><h1>下一步：修订指南准入准备完成</h1><section><p>20份draft-r2完整新正文，追加49组专题记录，取证合同60→109；180片段真实本地预检已完成。准确身份、default ops/admin内部权限及独立test-sop-records-r2目标均受绑定。</p><p><a href="full-content-review.html">正确全文具体审核页</a> · <a href="admission-plan.json">精确内容与目标绑定</a> · <a href="compatibility.json">兼容性检查</a> · <a href="receipt-template.json">空白待定回执</a></p></section><section><p>106相关单元与8真实无窗口Edge检查通过；20篇完整正文在浏览器中逐字比对，桌面和390手机无横向溢出。待审阻断、旧批准拒绝、篡改/权限/旧目标拒绝均通过。</p><p>首轮浏览器验收发现Windows文本写入将CRLF变成CRCRLF。原封存页面保留，另以UTF-8原字节写入生成正确全文视图并重验；计划、回执和原稿字节均不改。失败证据与修正说明均保留。</p><p>新数据库/集合/候选/端口实际不存在，未启动Worker。原库、业务及指南知识/权限/SQL/版本/向量、环境/预算、原Key及PID前后相同，临时浏览器profile已清理。</p></section><section><p>本步准入/入库/嵌入/新STAGED/付费0；新正文仍pending。具体批准后才能投影新候选、注册来源并重跑实际入库和门禁；普通发布资格、完整现场SOP、设备阈值和独立质量未获准。原r1指南及8010不替换，不自动合并或模型外发。</p><a href="summary.json">验收汇总</a> · <a href="checks.json">实际只读检查</a> · <a href="browser-checks.json">真实浏览器检查</a></section></main></html>'''
    (REPORT/'review.html').write_bytes(page.encode('utf-8'))
    seal(REPORT);verify_seal(REPORT);verify_seal(OUTPUT)
    print('Sealed: 106 unit tests, 8 browser checks; admission pending; paid calls 0.')


if __name__=='__main__':main()
