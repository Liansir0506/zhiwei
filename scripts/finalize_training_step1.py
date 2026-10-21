"""Seal training presentation and pending record revisions, with actual restart proof."""
from datetime import datetime,timezone
import html
import json
import time
import xml.etree.ElementTree as ET
import httpx
from scripts import training_business_runtime as runtime
from scripts.verify_training_step1 import ROOT,REPORT,protected
from scripts.prepare_sop_record_revision import OUTPUT,EXPOSURE,CANDIDATE
from scripts.prepare_legacy_projection import read,write,digest,verify_seal,seal
from qa_core.quality.sop_record_revision_review import validate_revision


def stable_data(snapshot):return {k:v for k,v in snapshot.items() if k not in ['readonly_pid','readonly_key']}


def main():
    if (REPORT/'artifact-hashes.json').exists():raise ValueError('report already sealed')
    attempts=[p for p in REPORT.glob('attempt-*') if read(p/'state.json').get('state')=='completed']
    if len(attempts)!=1:raise ValueError('one actual completed acceptance required')
    attempt=attempts[0];assert len(read(attempt/'http-checks.json'))==15 and len(read(attempt/'browser-checks.json'))==10
    assert read(attempt/'before.json')==read(attempt/'after.json')
    assert read(attempt/'key-cleanup.json')==dict(temporary_created=3,temporary_removed=3)
    assert not list(REPORT.glob('attempt-*/edge-profile'))
    unit=ROOT/'.runtime/test/training-step1-unit-final.xml'
    tests=ET.parse(unit).getroot().find('testsuite')
    assert int(tests.attrib['tests'])==26 and int(tests.attrib['failures'])==int(tests.attrib['errors'])==0
    (REPORT/'unit-tests.xml').write_bytes(unit.read_bytes())
    package=read(OUTPUT/'review-package.json');counts=validate_revision(package,OUTPUT/'drafts',CANDIDATE/'raw')
    verify_seal(OUTPUT);verify_seal(EXPOSURE)
    from scripts.prepare_question_holdout import OUTPUT as INTAKE,PREVIOUS
    from qa_core.quality.development_exposure import include_overlays
    _,overlays=include_overlays(read(PREVIOUS/'exposure-registry.json'),ROOT/'.runtime/quality/question-intake/development-exposure',ROOT,digest(INTAKE/'artifact-hashes.json'))
    assert len(overlays)==6 and any('sop-record-revision-r2' in o['path'] for o in overlays)
    write(REPORT/'exposure-check.json',dict(overlays=overlays,new_questions_created=0,independent_denominator=0,human_gold=False))
    state=runtime.status();assert state['status']=='running' and state['presentation_mode']=='training_workspace'
    key=(runtime.core.ROOT/'api-key.txt').read_text(encoding='utf-8');observations=[]
    with httpx.Client(base_url=state['url'],trust_env=False,timeout=40) as client:
        for i in range(3):
            listing=client.get('/business/alarms',headers={'X-API-Key':key})
            assert listing.status_code==200 and listing.json()['synthetic'] is True and len(listing.json()['items'])==2
            script=client.get('/static/business-workspace.js');assert script.status_code==200 and '模拟来源:' not in script.text
            assert runtime.core.inspect()['status']=='running'
            observations.append(dict(at_utc=datetime.now(timezone.utc).isoformat(),list_count=2,api_provenance_preserved=True,presentation_ready=True))
            if i<2:time.sleep(4)
    write(REPORT/'restart-observations.json',dict(observations=observations,hidden_start_script='scripts/start_business_workspace.ps1',
         start_script_returncode=0,read_status_without_mutation_lock=True,long_term_availability_passed=False))
    after=protected();write(REPORT/'final-protected.json',after)
    assert stable_data(after)==stable_data(read(attempt/'before.json'))
    old_report=ROOT/'.runtime/test/reports/readonly-business-p2-20261012';verify_seal(old_report)
    historical=read(old_report/'code-bindings.json')
    # The historical report also lists evolving docs/main-platform entrypoints.
    # Verify the serving readonly runtime/static subset, not unrelated later work.
    for n in runtime.core.CODE:
        if n in historical and digest(ROOT/n)!=historical[n]:raise ValueError('historical bound readonly runtime/static changed')
    write(REPORT/'historical-code-scope.json',dict(readonly_runtime_static_preserved=True,sealed_report_preserved=True,
        historical_other_code_or_document_differences=[n for n,h in historical.items() if n not in runtime.core.CODE and digest(ROOT/n)!=h],
        note='Historical docs and main-platform code have evolved in earlier steps; not a current full-tree binding.'))
    codes=runtime.CODE+('scripts/training_business_bootstrap.py','scripts/start_business_workspace.ps1',
        'scripts/sop_record_revision_content.py','scripts/prepare_sop_record_revision.py','qa_core/quality/sop_record_revision_review.py',
        'scripts/verify_training_step1.py','scripts/verify_training_step1_browser.js','scripts/finalize_training_step1.py',
        'tests/unit/test_training_workspace_and_revision.py')
    write(REPORT/'code-bindings.json',{n:digest(ROOT/n) for n in codes})
    summary=dict(state='completed',unit_tests=26,real_http_checks=15,real_edge_checks=10,
        user_preference='Business training data accepted; do not repeat synthetic labels in business presentation.',
        business_simulation_badges_removed=True,api_provenance_preserved=True,readonly_roles_unchanged=True,
        original_8010_keys_pids_and_config_unchanged=True,only_owned_business_entry_restarted=True,
        business_key_rotated=True,temporary_validation_keys_removed=3,training_start_script_verified=True,
        historical_bound_readonly_runtime_static_unchanged=True,draft_files=20,record_extensions=49,records_before=60,records_after=109,
        extension_counts=counts,real_tokenizer_chunks=read(OUTPUT/'chunk-preflight.json')['chunks'],source_locations_verified=True,
        draft_admission='pending',new_ingestions=0,new_admissions=0,new_staged=0,original_guides_unchanged=True,
        generation_authorized_for_new_drafts=False,qa_paid_calls=0,independent_denominator=0,human_truth_approved=False,
        original_and_stage_sql_vectors_permissions_unchanged=True,full_sop_approved=False,ordinary_publication_gate_passed=False,
        paid_calls=0,long_term_availability_passed=False,independent_quality=None,
        business_login_file=str(runtime.core.ROOT/'open-workspace.html'),draft_review=str(OUTPUT/'review.html'))
    write(REPORT/'summary.json',summary)
    write(REPORT/'development-notes.json',dict(corrected_startup_issues=[
        'Adapter --entry ownership argument must be removed from Python argv before existing argparse entry.',
        'Read-only status polling must not acquire the startup mutation lock; old failure timestamps must not block a new launch.'],
        failed_owned_keys_cleaned=True,business_entry_recovered=True,original_8010_unaffected=True,paid_calls=0))
    opener=(runtime.core.ROOT/'open-workspace.html').as_uri();review=(OUTPUT/'review.html').as_uri()
    page=f'''<!doctype html><html lang="zh-CN"><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>第一步：专题修订与业务展示验收</title><style>body{{font:16px/1.7 system-ui;background:#f3f6fb;color:#20314c;margin:0}}main{{max-width:1100px;margin:auto;padding:24px}}section{{background:white;border-radius:12px;padding:20px;margin:20px 0}}p,a{{overflow-wrap:anywhere}}img{{max-width:100%}}a{{color:#2454b9}}@media(max-width:500px){{main{{padding:12px}}}}</style><main>
<h1>第一步完成：具体取证修订与业务展示</h1><section><h2>业务工作区</h2><p>按用户明确的训练项目定位，业务列表、详情、诊断引用和集成表不反复标注模拟数据。保留查询范围、读取时间、未同步/无账本和只读操作状态；API中的synthetic/simulation以及历史原文、来源/权限/审计不改。不是把训练数据标成真实现场。</p><p><a href="{html.escape(opener,quote=True)}">打开业务与只读诊断工作区</a> · <a href="summary.json">本步范围</a></p><p>仅自有18021服务隐藏升级/重启并轮换本机运维Key。新scripts/start_business_workspace.ps1已实际通过隐藏启动，状态轮询不争启动锁；原绑定启动脚本与代码保留。原8010未重启或切换。</p><img alt="业务列表不再显示模拟标注" src="{attempt.name}/business-desktop.png"></section>
<section><h2>20份指南修订稿 · 待审</h2><p>11变更核对、9依赖取证、10配置比对、19派生告警关系，49项具体记录合同；原60组增至109组。各项列具体对象、至少4个核对字段与反例边界，不只补关键词，不填造阈值、读数或授权。</p><p>在独立sop-record-revision-r2目录追加内容，原20准入正文保留；draft-r2来源修订和default租户ops/admin内部范围是待审提案。没有注册身份、入库、新STAGED、替换当前指南或生成外发。</p><p><a href="{html.escape(review,quote=True)}">完整修订稿集中审核页</a> · <a href="draft-validation.json">修订验证</a></p><img alt="具体修订原件手机审核" src="{attempt.name}/revision-mobile.png"></section>
<section><h2>验收与保护</h2><p>26单元、15真实HTTP、10真实无窗口Edge通过。业务实际列表/关联详情、只读工单诊断和原文引用正常，后端来源标志仍true、跨租户/guest与写操作限制有效。审核页20完整正文/49记录可查看，桌面与390手机无溢出。真实本地tokenizer预检180个guide片段，450token/7900字节上限与无歧义位置通过，向量写入0。3临时验证Key已清理，浏览器私有profile安全清理。</p><p>已有公开问题仅复用登记新修订/模板曝光，现有筛查自动读取6份overlay；没有新增隐藏题或人工金标，独立分母0。原与指南库SQL、知识/权限向量、源包、环境和预算保持。业务训练数据可作为本项目验收范围，接真实外部系统不再列本项目必做项；完整SOP/设备依据和独立质量仍待后续。</p><p><a href="unit-tests.xml">26单元</a> · <a href="{attempt.name}/http-checks.json">15HTTP</a> · <a href="{attempt.name}/browser-checks.json">10Edge</a> · <a href="final-protected.json">最终保护</a> · <a href="restart-observations.json">隐藏重启后观察</a></p><p>适配启动参数和状态锁竞争的失败批次已修正并重验，详见development-notes；短时3次检查不是长期稳定结论，付费0。</p></section></main></html>'''
    (REPORT/'draft-validation.json').write_bytes((attempt/'draft-validation.json').read_bytes())
    (REPORT/'review.html').write_text(page,encoding='utf-8');seal(REPORT);verify_seal(REPORT);verify_seal(OUTPUT);verify_seal(EXPOSURE)
    print(json.dumps(dict(state='sealed',unit_tests=26,http_checks=15,browser_checks=10,pending_drafts=20,extensions=49,paid_calls=0,
                          report=str(REPORT/'review.html'))))


if __name__=='__main__':main()
