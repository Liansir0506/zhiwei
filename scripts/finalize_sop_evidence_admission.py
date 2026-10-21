"""Seal completed admission preparation while keeping the actual decision pending."""
import html
import json
import shutil
import xml.etree.ElementTree as ET

from scripts.prepare_sop_evidence_admission import ROOT,OUTPUT,REPORT,DRAFTS
from scripts.prepare_legacy_projection import read,write,digest,seal,verify_seal
from scripts.sop_evidence_stage_profile import profile
from qa_core.ingest.binding import version_binding


def main():
    if (REPORT/'artifact-hashes.json').exists():raise ValueError('sealed')
    verify_seal(OUTPUT);verify_seal(DRAFTS)
    attempts=[dict(folder=p.name,**read(p/'state.json')) for p in sorted(REPORT.glob('attempt-*'))]
    winners=[a for a in attempts if a['state']=='completed'];assert len(winners)==1
    winner=winners[0];attempt=REPORT/winner['folder']
    assert read(attempt/'before.json')==read(attempt/'after.json') and winner['target_namespace_absent']
    browser=read(attempt/'browser/browser-verification.json');assert browser['passed'] and len(browser['checks'])==8
    assert not (attempt/'browser/edge-profile').exists()
    cleanup=read(REPORT/'browser-cleanup.json');assert cleanup['owned_browser_exited'] and cleanup['profile_removed']
    preflight=read(OUTPUT/'chunk-preflight.json');assert preflight['files']==20 and preflight['total_chunks']==160 and preflight['guide_type_preserved']
    plan=read(OUTPUT/'admission-plan.json');receipt=read(OUTPUT/'receipt-template.json')
    assert receipt['decision']=='pending' and receipt['user_message'] is None and not plan['publication_authorized']
    counts=None
    for name in ('sop-admission-unit-20261014.xml','sop-admission-unit-r2-20261014.xml','sop-admission-unit-r3-20261014.xml'):
        source=ROOT/'.runtime'/name;shutil.copyfile(source,REPORT/name)
        suites=list(ET.parse(source).getroot().iter('testsuite'))
        counts={k:sum(int(s.get(k,0)) for s in suites) for k in ('tests','errors','failures','skipped')}
        assert counts['errors']==counts['failures']==counts['skipped']==0
    assert counts['tests']==31
    bindings=('qa_core/quality/sop_admission.py','qa_core/indexing/chunkers/sop_evidence_guide.py',
        'scripts/prepare_sop_evidence_admission.py','scripts/build_sop_evidence_candidate.py',
        'scripts/sop_evidence_stage_profile.py','scripts/verify_sop_evidence_admission.py',
        'scripts/finalize_sop_evidence_admission.py','tests/unit/test_sop_admission.py','tests/browser/sop_admission_smoke.js')
    write(REPORT/'code-bindings.json',{n:digest(ROOT/n) for n in bindings})
    write(REPORT/'preparation-failure.json',dict(error_kind='ValueError',stage='runtime_profile_validation',
        description='First preparation omitted current release adapter; failed before review output and storage mutation. Adapter installed and preparation passed.',
        retrospective_tool_error_record=True,storage_writes=0))
    selected=profile()
    write(REPORT/'stage-binding.json',dict(version_binding=version_binding(selected),
        target=plan['target'],source_map_required=True,record_locations=True,strict_metadata=True,
        guide_adapter_sha256=digest(ROOT/'qa_core/indexing/chunkers/sop_evidence_guide.py'),
        current_worker_reuse_authorized=False,configuration_applied=False,qa_requests=0))
    summary=dict(state='preparation_completed_decision_pending',pending_sources=20,preflight_chunks=160,
        unit=counts,browser_checks=8,actual_target_namespace_absent=True,pending_receipt_rejected=True,
        candidate_writer_rejects_pending_before_mutation=True,guide_type_preserved=True,
        guides_not_eligible_for_complete_workflow=True,source_body_bytes_unchanged=True,
        protected_original_and_readonly_snapshots_equal=True,configuration_applied=False,
        new_admissions=0,new_staged=0,new_database=0,new_collections=0,new_assets=0,embeddings_created=0,
        paid_calls=0,qa_requests=0,worker_started=False,independent_frozen=0,human_truth_approved=False,
        target=plan['target'],review_seal_sha256=digest(OUTPUT/'artifact-hashes.json'),
        source_seal_sha256=digest(DRAFTS/'artifact-hashes.json'),winning_attempt=winner['folder'],
        publication_authorized=False,outbound_authorized=False,operational_sop_approved=False,
        limitations=['Only preparation verified; no real admission or ingestion yet',
            'Receipt fields bind scope but cannot authenticate a human on their own',
            'No actual vector/QA retrieval/generation or independent human quality evaluated'])
    write(REPORT/'summary.json',summary)
    link=html.escape((OUTPUT/'review.html').as_uri(),quote=True)
    page=f'''<!doctype html><html lang="zh-CN"><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>20份指南准入准备验证</title><style>body{{font:16px/1.7 system-ui;background:#f5f7fb;color:#21314c;max-width:1040px;margin:auto;padding:24px}}p,a{{overflow-wrap:anywhere}}img{{max-width:100%;height:auto}}</style>
<h1>20份指南的具体准入准备已完成，决定仍pending</h1><p>20份准确原字节正文、来源身份、default租户ops/admin内部访问及新的隔离目标已绑定。拟test-sop-evidence-r1，仅20指南；新MySQL test_sop_evidence_r1/13306及29530上两新集合，不复制或替换原资料。</p>
<p>实际本地tokenizer预检160片段，均≤450token、≤7900字节，完整段落和原文位置相符；专用适配器保留guide类型且退出后恢复注册表。完整SOP检索不接受guide，即使召回层忽略类型过滤也不升级为可执行流程。</p>
<p>31项最终单元、8项真实无窗口Edge通过。pending及“下一步”不能当具体凭据；内容/访问/目标/鉴权/生成开关漂移被阻断。凭据检查只是内容绑定，不认证人员；测试批准夹具只在内存，不保存为真实批准。</p>
<p>真实只读检查确认目标库和两集合尚不存在；原与只读SQL/完整原向量/权限、环境、预算与入口PID前后保持。无身份注册、新STAGED/入库/嵌入/QA/付费或Worker启动。首次准备遗漏获准运行适配器的ValueError已记录，修正后通过，未写存储。</p>
<p><a href="{link}">具体审核页：20项完整正文与身份/范围</a> · <a href="summary.json">验证结果</a> · <a href="stage-binding.json">拟执行配置与适配器绑定</a></p>
<p>候选原件构建器已准备，只在精确凭据检查后创建全新目录；数据库/向量STAGED执行尚未发生。新目标关闭生成/Agent、启用鉴权，不允许原库或旧Worker配置复用。发布/切8010、新外发、现场操作和独立质量不在本次准入范围。</p>
<img alt="具体准入审核实际截图" src="{winner['folder']}/browser/admission-desktop.png"></html>'''
    (REPORT/'review.html').write_text(page,encoding='utf-8');seal(REPORT);verify_seal(REPORT);verify_seal(OUTPUT)
    print(json.dumps(dict(state='sealed_preparation_decision_pending',unit_tests=31,browser_checks=8,preflight_chunks=160,new_admissions=0,paid_calls=0)))


if __name__=='__main__':main()
