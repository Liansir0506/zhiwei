"""Seal actual network/browser/lifecycle preview acceptance and unchanged source state."""
import json
from xml.etree import ElementTree as ET
from scripts.verify_sop_record_preview import REPORT,protected
from scripts import sop_record_preview_runtime as runtime
from scripts.prepare_sop_record_alignment_v2 import OUTPUT,load_verified
from scripts.prepare_legacy_projection import read,write,digest,seal,verify_seal
from scripts.sop_record_stage_profile import profile
from qa_core.quality.record_preview_checks import verify_live


def main():
    if (REPORT/'artifact-hashes.json').exists():raise ValueError('never reseal historical reports')
    matrix=load_verified();result=verify_live(profile())
    assert result['read_preview_ready'] and not result['publication_eligible']
    attempts=[p for p in REPORT.glob('attempt-*') if read(p/'state.json')['state']=='completed']
    assert len(attempts)==1;attempt=attempts[0]
    http=read(attempt/'state.json');refs=read(attempt/'real-reference-verification.json')
    browser=read(attempt/'browser-checks.json');cleanup=read(attempt/'key-cleanup.json')
    assert http['http_checks']==19 and refs['proofs_reread']==431 and refs['unique_chunks']==120
    assert len(browser)==13 and all(c['passed'] for c in browser)
    assert cleanup==dict(temporary_created=4,temporary_removed=4,remaining=1)
    search=REPORT/'search-verification';search_state=read(search/'state.json');search_browser=read(search/'browser-checks.json')
    assert search_state['state']=='completed' and search_state['http_checks']==6
    assert len(search_browser)==4 and all(c['passed'] for c in search_browser)
    lifecycle=read(REPORT/'lifecycle.json');assert lifecycle['state']=='completed' and len(lifecycle['observations'])==3
    assert lifecycle['old_key_rejected'] and lifecycle['new_database_keys_after_restart']==1
    assert not (attempt/'edge-profile').exists() and not (search/'edge-profile').exists()
    suites=ET.parse(REPORT/'unit-tests.xml').getroot().findall('testsuite')
    tests=sum(int(s.attrib['tests']) for s in suites)
    assert tests==212 and all(int(s.attrib['failures'])==int(s.attrib['errors'])==int(s.attrib['skipped'])==0 for s in suites)
    final=protected();assert final==read(attempt/'before.json')==read(attempt/'after.json')
    write(REPORT/'final-protected.json',final)
    assert runtime.inspect()['status']=='running'
    write(REPORT/'initial-alignment-failure.json',dict(error='Initial analysis summary used integer keys; JSON roundtrip changed them to strings',
        correction='Separate v2 analysis uses string keys and validates JSON roundtrip before sealing',
        initial_package_retained_unchanged=True,initial_package_used_by_live_preview=False,
        database_writes=0,knowledge_or_permission_changes=0))
    code=(*runtime.CODE,'scripts/verify_sop_record_preview.py','scripts/verify_sop_record_preview_browser.js',
        'scripts/verify_sop_record_preview_lifecycle.py','scripts/verify_sop_record_preview_search.py',
        'scripts/verify_sop_record_search_browser.js','scripts/finalize_sop_record_preview.py',
        'tests/unit/test_sop_record_preview.py')
    write(REPORT/'code-bindings.json',{n:digest(runtime.PROJECT/n) for n in code})
    summary=dict(state='completed',version='test-sop-records-r2',status='STAGED',port=18025,source_files=20,
        stored_chunks=180,record_contracts=109,record_extensions=49,parent_bindings=200,step_alignment_rows=200,
        unit_tests_passed=tests,real_network_http_checks=25,real_headless_edge_checks=17,
        actual_line_proofs_reread=431,unique_source_chunks_reread=120,
        runtime_started_hidden=True,stop_restart_verified=True,restart_observations=3,old_key_rejected=True,
        original_and_business_and_r1_guides_and_r2_knowledge_snapshots_unchanged=True,
        original_and_business_and_r1_preview_keys_and_pids_unchanged=True,
        temporary_keys_created=4,temporary_keys_removed=4,new_namespace_persistent_technical_admin_keys=1,
        both_owned_browser_profiles_removed=True,new_ingestions=0,new_embeddings=0,new_admissions=0,
        qa_generation_requests=0,paid_calls=0,independent_denominator=0,full_sop_approved=False,
        ordinary_publication_gate_passed=False,published=False,activated=False,
        generation_enabled=False,agent_enabled=False,long_term_availability_proven=False,
        private_login_file=str(runtime.ROOT/'open-preview.html'),alignment_seal_sha256=digest(OUTPUT/'artifact-hashes.json'))
    write(REPORT/'summary.json',summary)
    page=f'''<!doctype html><html lang="zh-CN"><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>修订指南预览实际验收</title><style>body{{font:16px/1.7 system-ui;background:#f3f6fb;color:#20314c;margin:0}}main{{max-width:1050px;margin:auto;padding:24px}}section{{background:white;padding:20px;margin:16px 0;border-radius:10px;border:1px solid #dce4f1}}p,a{{overflow-wrap:anywhere}}img{{width:100%;height:auto}}</style><main><h1>新版只读预览与支持性对齐已完成</h1><section><p>新入口127.0.0.1:18025，test-sop-records-r2 STAGED。20指南/180片段、109取证项/49新增记录、200原步骤及200父身份绑定可筛选并跳转对应draft-r2原文。11变更、9依赖、10配置、19派生告警文字缺口已有具体要求；现场记录和执行依据仍缺100项，不把文字补齐当成现场支持。</p><p>指南目录、实际证据检索、引用重读、检查与缺口、SOP支持性对齐均使用真实新SQL/Milvus。仅default未限域管理员；原ops角色不提升，QA生成/Agent/上传/发布/写操作关闭。</p><a href="summary.json">结果与分母</a> · <a href="{attempt.name}/live-checks.json">实际完整性与支持性数据</a></section>
<section><p>212相关单元、25真实网络HTTP、17无窗口Edge检查通过。431逐行证明、120不同原文片段实际重读，SHA和行位置相符。支持性筛选/49新增项/原文、桌面/390手机无溢出；检索页真实AMF提问和引用、完整流程无证据提示及退出清理通过。</p><a href="{attempt.name}/http-checks.json">19边界检查</a> · <a href="search-verification/http-checks.json">6检索与版本检查</a> · <a href="{attempt.name}/browser-checks.json">13支持性浏览器检查</a> · <a href="search-verification/browser-checks.json">4检索浏览器检查</a> · <a href="{attempt.name}/real-reference-verification.json">真实逐行原文核对</a></section>
<section><p>新版自有进程实际停止、端口关闭、技术Key清除后隐藏重启；旧Key401、新Key有效，3次鉴权与109项检查通过。只证明这次启停与短时观察，不声明长期可用。4临时验证Key全部删除，仅新隔离库技术预览Key1；这不是拟造真人审核身份。两份浏览器会话profile已清理，截图保留。</p><p>原8010、业务和旧r1指南入口Key/PID保持；所有既有及r2知识、SQL业务/版本/位置、向量/权限、环境/预算前后相同。本步入库/嵌入/准入/付费0；仅新版预览的自有Key/PID按测试轮换。普通失败门禁及STAGED保留，未激活或合并8010，未扩生成外发。</p><a href="lifecycle.json">真实启停与观察</a> · <a href="final-protected.json">最终保护快照</a></section>
<section><p>首版分析封包的统计键使用整数，JSON读回后变字符串，验证拒绝。保留失败包和代码，另建v2字符串键分析，并在封存前核对序列化往返；实际预览只绑定v2，没有更改已准入正文或向量。完整现场SOP、设备依据、独立人工质量和合格整库发布仍待完成。</p><a href="initial-alignment-failure.json">首轮错误与修正</a></section><section><h2>实际页面</h2><img src="{attempt.name}/support-desktop.png" alt="新版109项支持性对齐桌面"><p><a href="{attempt.name}/support-mobile.png">390手机截图</a> · <a href="search-verification/search-desktop.png">真实检索与引用截图</a></p></section></main></html>'''
    (REPORT/'review.html').write_bytes(page.encode('utf-8'))
    seal(REPORT);verify_seal(REPORT);verify_seal(OUTPUT)
    print(json.dumps(dict(state='sealed',tests=tests,http_checks=25,browser_checks=17,contracts=109,paid_calls=0)))


if __name__=='__main__':main()
