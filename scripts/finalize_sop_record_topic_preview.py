"""Seal the stricter topic correction alongside the immutable preview baseline."""
import json
from xml.etree import ElementTree as ET
from scripts import sop_record_topic_runtime as runtime
from scripts.verify_sop_record_topic_preview import REPORT
from scripts.verify_sop_record_preview import protected
from scripts.prepare_sop_record_alignment_v2 import load_verified
from scripts.prepare_legacy_projection import read,write,digest,seal,verify_seal


def main():
    if (REPORT/'artifact-hashes.json').exists():raise ValueError('never reseal reports')
    baseline=runtime.core.PROJECT/'.runtime/test/reports/sop-record-preview-20261014';verify_seal(baseline)
    completed=[p for p in REPORT.glob('attempt-*') if (p/'state.json').exists() and read(p/'state.json')['state']=='completed']
    assert len(completed)==1;attempt=completed[0];outcome=read(attempt/'state.json')
    assert outcome['http_checks']==46 and outcome['every_returned_source_topic_matches']==20
    assert len(read(attempt/'retrieval.json'))==20 and all(r['all_returned_sources_match'] for r in read(attempt/'retrieval.json'))
    browser=read(attempt/'browser-checks.json');assert len(browser)==4 and all(r['passed'] for r in browser)
    suites=ET.parse(REPORT/'unit-tests.xml').getroot().findall('testsuite')
    assert sum(int(s.attrib['tests']) for s in suites)==17
    assert all(int(s.attrib['failures'])==int(s.attrib['errors'])==int(s.attrib['skipped'])==0 for s in suites)
    lifecycle=read(REPORT/'lifecycle.json');assert lifecycle['state']=='completed' and lifecycle['old_key_rejected']
    warm=read(REPORT/'post-restart-search.json');assert warm['passed'] and warm['all_returned_sources_match']
    assert not (attempt/'edge-profile').exists()
    final=protected();assert final==read(attempt/'before.json')==read(attempt/'after.json')==read(baseline/'final-protected.json')
    write(REPORT/'final-protected.json',final);matrix=load_verified();assert matrix['summary']['record_contracts']==109
    assert runtime.status()['status']=='running'
    write(REPORT/'correction-history.json',dict(
        baseline_weak_check='Expected AMF source present among results; other topics still appeared',
        actual_fix='Bind exact admitted asset/source revision constraint before recall, cache lookup and evidence reread',
        validation_now='Every returned item belongs to the explicit alarm/device topic; 20 exposed cases checked',
        initial_unit_failures='Chinese-adjacent alarm word boundary and device detection inside alarm codes; fixture missing fusion_score',
        initial_runtime_failure='FastAPI lazy IncludedRouter handled broad search before appended override',
        corrections='ASCII token boundaries; strip alarm spans before device recognition; explicit route ordered first, shared router untouched',
        failed_attempts_retained=True,new_data_admissions=0,baseline_report_unchanged=True))
    runtime.configure()
    code=(*runtime.core.CODE,'scripts/verify_sop_record_topic_preview.py','scripts/verify_sop_record_topic_browser.js',
        'scripts/verify_sop_record_topic_lifecycle.py','scripts/finalize_sop_record_topic_preview.py',
        'tests/unit/test_sop_record_preview_topic.py')
    write(REPORT/'code-bindings.json',{n:digest(runtime.core.PROJECT/n) for n in code})
    summary=dict(state='completed',version='test-sop-records-r2',status='STAGED',port=18025,record_contracts=109,
        baseline_unit_tests=212,topic_unit_tests=17,total_related_unit_tests=229,baseline_http_checks=25,
        strict_topic_http_checks=46,post_restart_search_checks=1,local_query_models_warmed=True,
        strict_topic_development_queries=20,every_returned_item_topic_matches=20,
        baseline_browser_checks=17,strict_topic_browser_checks=4,actual_line_proofs_reread=431,unique_proof_chunks=120,
        true_independent_quality_denominator=0,real_answer_truth_evaluated=False,original_8010_unchanged=True,
        original_business_and_r1_guides_and_all_knowledge_acl_snapshots_unchanged=True,
        only_r2_preview_owned_key_and_pid_rotated=True,persistent_technical_admin_keys=1,
        hidden_stop_restart=True,old_key_rejected=True,restart_observations=3,long_term_availability_proven=False,
        ordinary_gate_passed=False,generation_enabled=False,agent_enabled=False,published=False,activated=False,
        new_admissions=0,new_ingestions=0,new_embeddings=0,paid_calls=0,private_login_file=str(runtime.core.ROOT/'open-preview.html'))
    write(REPORT/'summary.json',summary)
    page=f'''<!doctype html><html lang="zh-CN"><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>修订指南预览最终验收</title><style>body{{font:16px/1.7 system-ui;background:#f3f6fb;color:#20314c;margin:0}}main{{max-width:1050px;margin:auto;padding:24px}}section{{background:white;padding:20px;margin:16px 0;border-radius:10px}}p,a{{overflow-wrap:anywhere}}img{{width:100%;height:auto}}</style><main><h1>新版只读预览与支持性对齐 · 最终验收</h1><section><p>127.0.0.1:18025，新版20指南/180片段、109取证项/49新增项、200步对齐可查看、筛选和核对原文。目录、证据检索、适用检查与支持性页面已接真实SQL/Milvus；原8010/业务/旧指南及角色不变。</p><p>当前检索对明确告警/设备先绑定已准入asset与draft-r2，再召回、缓存匹配和重读证据。20道公开开发题的每条返回项均属于对应来源，逐题引用核对通过；未知告警和设备冲突返回无证据，不放宽到其他专题。一般未指明设备/告警的查询仍是探索性证据检索。</p><a href="summary.json">最终结果及分母</a> · <a href="{attempt.name}/retrieval.json">20题逐条来源检验</a> · <a href="{attempt.name}/http-checks.json">46严格检索与边界检查</a></section><section><p>212基线相关单元加17主题约束/真实HTTP路由回归，共229通过；基线25网络HTTP、17Edge和431行证明/120原文片段保留。追加46严格网络HTTP和4真实Edge通过。严格验收要求每张AMF结果卡都来自对应指南，不仅检查包含一条正确来源。</p><p>截图复核发现原检索混入其他专题，旧验收与截图原样封存，新增修正单独记录。初次中文边界识别与懒加载路由接管问题通过反例和HTTP测试修正，首轮失败保留。</p><a href="correction-history.json">问题与修正依据</a> · <a href="{attempt.name}/browser-checks.json">严格浏览器结果</a></section><section><p>当前适配器实际隐藏停启，旧Key401、新Key有效，3次109项鉴权检查通过；所有知识/权限/SQL/环境预算及原/业务/旧指南Key/PID前后相同。只有新版预览自己的Key/PID轮换，新隔离库技术预览Key1，不拟造人工审核身份，浏览器profile已清理。</p><p>本步准入/入库/嵌入/付费0。STAGED及普通失败门禁保留，生成/Agent关闭，未合并/切8010/发布或新增外发。109项是取证要求，不是现场事实；完整SOP/设备依据和独立人工质量仍有缺口。启停和短时观察不证明长期可用。</p><a href="lifecycle.json">实际当前适配器启停</a> · <a href="final-protected.json">最终保护</a></section><section><h2>修正后实际检索页面</h2><img src="{attempt.name}/search-desktop.png" alt="所有AMF检索结果均来自同一对应指南"></section></main></html>'''
    (REPORT/'review.html').write_bytes(page.encode('utf-8'));seal(REPORT);verify_seal(REPORT)
    print(json.dumps(dict(state='sealed',related_tests=229,strict_topic_cases=20,strict_http_checks=46,paid_calls=0)))


if __name__=='__main__':main()
