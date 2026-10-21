"""Seal literal support and real UI evidence, retaining all unresolved field gaps."""
from datetime import datetime,timezone
import html
import json
import time
import xml.etree.ElementTree as ET
from pathlib import Path
import httpx
from scripts import sop_guide_support_runtime as runtime
from scripts.prepare_sop_support_alignment import ROOT,OUTPUT,load_verified
from scripts.verify_sop_support_alignment import REPORT,protected
from scripts.prepare_legacy_projection import read,write,digest,verify_seal,seal


def main():
    if (REPORT/'artifact-hashes.json').exists():raise ValueError('report already sealed')
    runtime.select_record()
    attempts=[p for p in REPORT.glob('attempt-*') if read(p/'state.json').get('state')=='completed'
              and read(p/'code-bindings.json')==runtime.core.bindings()]
    if len(attempts)!=1:raise ValueError('one completed exact-code acceptance required')
    attempt=attempts[0];assert read(attempt/'before.json')==read(attempt/'after.json')
    assert len(read(attempt/'http-checks.json'))==19 and len(read(attempt/'browser-checks.json'))==10
    assert read(attempt/'key-cleanup.json')==dict(temporary_created=4,temporary_removed=4,remaining=1)
    assert not list(REPORT.glob('attempt-*/edge-profile'))
    references=read(attempt/'real-reference-verification.json')
    assert references['proofs_reread']==382 and references['unique_chunks']==100 and references['raw_file_sha_and_line_verified']
    unit=ROOT/'.runtime/test/sop-support-alignment-unit-final.xml'
    tests=ET.parse(unit).getroot().find('testsuite')
    assert int(tests.attrib['tests'])==95 and int(tests.attrib['failures'])==int(tests.attrib['errors'])==0
    (REPORT/'unit-tests.xml').write_bytes(unit.read_bytes())
    old=ROOT/'.runtime/test/reports/sop-guide-preview-20261014';verify_seal(old)
    for name,checksum in read(old/'code-bindings.json').items():
        if digest(ROOT/name)!=checksum:raise ValueError('sealed v1 preview code changed')
    matrix=load_verified();write(REPORT/'alignment-summary.json',matrix['summary'])
    observations=[];key=(runtime.core.ROOT/'api-key.txt').read_text(encoding='utf-8')
    with httpx.Client(base_url='http://127.0.0.1:18024',trust_env=False,timeout=50) as client:
        for i in range(3):
            response=client.get('/preview/checks',headers={'X-API-Key':key})
            assert response.status_code==200 and response.json()['support_alignment']==matrix
            assert response.json()['ordinary_publication_gate']['passed'] is False
            observations.append(dict(at_utc=datetime.now(timezone.utc).isoformat(),read_preview_ready=True,
                     alignment_matches=True,ordinary_gate_passed=False))
            if i<2:time.sleep(5)
    write(REPORT/'bounded-observations.json',dict(observations=observations,long_term_availability_passed=False))
    now=protected();write(REPORT/'final-protected.json',now);assert now==read(attempt/'before.json')
    assert runtime.status()['status']=='running';verify_seal(OUTPUT);verify_seal(old)
    codes=runtime.core.CODE+('scripts/verify_sop_support_alignment.py','scripts/verify_sop_support_alignment_browser.js',
                           'scripts/finalize_sop_support_alignment.py','tests/unit/test_sop_support_alignment.py')
    write(REPORT/'code-bindings.json',{n:digest(ROOT/n) for n in codes})
    write(REPORT/'preparation-notes.json',dict(corrected_before_output_creation=[
        'Use raw bytes decode to retain Windows CRLF in exact source hash.',
        'Use existing source-location canonical normalization to match raw punctuation with stored sanitized chunks.'],
        source_files_edited=False,storage_modified_during_preparation=False,paid_calls=0))
    summary=dict(state='completed',version='test-sop-evidence-r1',status='STAGED',analysis_artifact=str(OUTPUT/'alignment.json'),
        unit_tests=95,real_network_http_checks=19,real_edge_checks=10,reference_proofs=382,unique_guide_chunks_reread=100,
        guide_families=20,parent_source_identities=200,step_rows=200,record_contracts=60,primary_conditions=40,counter_conditions=40,
        per_family_missing_requirement_categories=5,unresolved_requirement_rows=100,
        topic_record_omissions={key:matrix['summary']['text_status_counts'][key] for key in (
            'change_records_missing','dependency_records_missing','comparison_missing','alarm_records_missing')},
        text_alignment_only=True,human_semantic_approved=False,site_evidence_verified=0,actions_approved=0,
        independent_denominator=0,independent_quality=None,manufacturer_documents_added=0,
        new_knowledge_assets=0,new_ingestions=0,published=False,activated=False,ordinary_publication_gate_passed=False,
        paid_calls=0,qa_requests=0,generation_enabled=False,agent_enabled=False,long_term_availability_passed=False,
        original_and_readonly_keys_pids_roles_unchanged=True,original_and_stage_knowledge_permissions_unchanged=True,
        only_owned_guide_preview_upgraded=True,sealed_v1_code_and_reports_unchanged=True,
        technical_preview_key_rotated=True,temporary_validation_keys_removed=4,persistent_preview_keys=1,
        old_parent_bodies_served_or_imported=False,final_attempt=attempt.name,
        private_login_file=str(runtime.core.ROOT/'open-preview.html'))
    write(REPORT/'summary.json',summary)
    rows=[]
    for entry in matrix['entries']:
        counts=entry['steps']
        missing=', '.join(str(r['old_step']) for r in counts if r['text_status'].endswith('_missing'))
        rows.append(f"<tr><td>{html.escape(entry['title'])}</td><td>10 / 3</td><td>{missing}</td><td>未通过</td></tr>")
    opener=(runtime.core.ROOT/'open-preview.html').as_uri()
    page=f'''<!doctype html><html lang="zh-CN"><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>SOP指南逐项对齐验收</title><style>body{{font:16px/1.7 system-ui;color:#20314c;background:#f3f6fb;margin:0}}main{{max-width:1100px;margin:auto;padding:24px}}section{{background:white;border-radius:12px;padding:22px;margin:20px 0}}table{{border-collapse:collapse;width:100%}}th,td{{padding:10px;text-align:left;border-bottom:1px solid #e0e7f1}}p,a,td{{overflow-wrap:anywhere}}img{{max-width:100%}}a{{color:#2454b9}}@media(max-width:500px){{main{{padding:12px}}section{{padding:14px}}}}</style><main>
<h1>SOP与指南逐项对齐已接入预览</h1><section><p>20指南/20文字家族与200父原件身份、200项步骤、60组取证项、40支持与40相反条件对应。父原件只是来源身份和行位置，不向新指南命名空间导入或返回旧正文。取证合同不是现场证据，20编号家族不算200条独立依据。</p><p><a href="{html.escape(opener,quote=True)}">本机私有指南预览：选择SOP支持性对齐</a> · <a href="summary.json">验收范围</a> · <a href="alignment-summary.json">对齐分类统计</a></p><p>具体缺项：11家族未明确列变更取证，9未明确列上下游依赖取证，10未明确列配置比对，19未明确列派生告警关系。每家族还缺厂家适用、真实现场证据、逐动作依据/授权、回退/升级、恢复/观察5类材料，共100行缺项。这里的字面覆盖不证明真实根因或现场适用。</p></section>
<section><h2>真实功能验收</h2><p>95单元、19真实网络HTTP、10真实无窗口Edge通过；382处行证明通过真实原文服务重读，涉及100个不同指南片段，原件SHA、准确行位置与规范化片段对应。浏览器实际加载20家族/200步骤、按AMF筛选、核对禁止动作及支持/相反分支、跳转真实原文、390像素不溢出，退出后清除内容和凭据。不是模拟接口或独立人工评测。</p><p><a href="unit-tests.xml">95单元</a> · <a href="{attempt.name}/http-checks.json">19网络HTTP</a> · <a href="{attempt.name}/browser-checks.json">10浏览器</a> · <a href="{attempt.name}/real-reference-verification.json">382原文行及位置</a></p><img alt="真实支持性对齐页面" src="{attempt.name}/support-desktop.png"><img alt="手机筛选与指南对齐" src="{attempt.name}/support-mobile.png"></section>
<section><h2>20专题缺项概览</h2><table><tr><th>指南</th><th>逐步/取证组</th><th>字面未补足的原步骤</th><th>完整现场SOP</th></tr>{''.join(rows)}</table></section>
<section><h2>保护与限制</h2><p>只有自有18024指南预览使用新适配器升级并轮换技术凭据，请通过私有入口重新进入；原8010与既有只读Key/角色/PID、知识/权限/SQL业务/版本/位置、环境/费用预算保持。已封存v1代码与报告未修改，新分析另封存，不新增KnowledgeAsset或向量入库。4临时验证Key全部删除，新库保留1个自有技术预览Key，浏览器私有profile清理；3次短时真实检查通过，未证明长期存活。</p><p>普通门禁仍失败、STAGED保持、生成/Agent关闭，QA/付费0。设备手册、现场证据、动作/回退/恢复方案和独立真值仍待提供，人工审核/现场动作/独立分母均0。不能根据本页推导合并、发布、8010切换或新模型外发授权。</p><p><a href="final-protected.json">最终数据保护核对</a> · <a href="bounded-observations.json">短时观察</a> · <a href="preparation-notes.json">原字节与规范化修正记录</a></p></section></main></html>'''
    (REPORT/'review.html').write_text(page,encoding='utf-8');seal(REPORT);verify_seal(REPORT)
    print(json.dumps(dict(state='sealed',unit_tests=95,http_checks=19,browser_checks=10,proofs=382,paid_calls=0,
              status='STAGED',report=str(REPORT/'review.html'))))


if __name__=='__main__':main()
