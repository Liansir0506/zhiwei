"""Seal version-workspace evidence without changing source admissions or ACTIVE."""
import html
import json
import shutil
import sys
import xml.etree.ElementTree as ET
import sqlalchemy as sa
from scripts import content_quality_release_runtime as current
from scripts import readonly_workspace_runtime as readonly
from scripts import platform_entry
from scripts.prepare_legacy_projection import read, write, digest, seal, verify_seal
from scripts.verify_version_workspace import REPORT
from scripts.verify_version_workspace_original import snapshot
from scripts.verify_readonly_business_workspace import sql_snapshot
from qa_core.quality.publication import capture_snapshot
from qa_core.quality.version_workspace import Workspace, ROOT
from qa_core.ingest import workspace as uploads


def main():
    assert not (REPORT / 'artifact-hashes.json').exists(), 'Keep already sealed evidence immutable'
    sandbox = REPORT / read(REPORT / 'latest.json')['attempt']
    original = REPORT / read(REPORT / 'original-latest.json')['attempt']
    proofs = [read(folder / 'verification.json') for folder in (sandbox, original)]
    browsers = [read(folder / 'browser-verification.json') for folder in (sandbox, original)]
    assert all(row['passed'] for row in proofs + browsers)
    assert read(sandbox / 'cleanup.json')['database_collections_grants_and_keys_removed']
    assert read(sandbox / 'cleanup.json')['temporary_http_stopped']
    assert read(original / 'cleanup.json')['temporary_keys_remaining'] == 0
    assert not list(REPORT.glob('**/edge-profile'))
    survival = read(REPORT / 'survival.json')
    assert survival['passed'] and len(survival['samples']) == 4
    assert all(p['visible_owned_windows'] == 0 for s in survival['samples'] for p in s['processes'].values())
    tests = 0
    for name in ('unit-tests-final-v3.xml', 'windowless-unit.xml'):
        source = ROOT / name
        suites = ET.parse(source).getroot().findall('testsuite')
        assert all(int(s.attrib[k]) == 0 for s in suites for k in ('failures', 'errors', 'skipped'))
        tests += sum(int(s.attrib['tests']) for s in suites)
        shutil.copyfile(source, REPORT / name)
    assert tests == 178
    current.install_adapter()
    settings = current.selected_settings()
    current.owned.apply_profile(settings)
    before, after = read(original / 'before.json'), read(original / 'after.json')
    assert before == after
    assert snapshot(settings) == after['sql']
    assert capture_snapshot(settings, current.VERSION) == after['active']
    assert digest(current.PROJECT / '.env.runtime') == after['env']
    budget = current.owned.ROOT / 'generation-validation-budget.json'
    assert digest(budget) == after['budget'] and read(budget)['used'] == 3 and read(budget)['limit'] == 4
    assert read(sandbox / 'before.json') == read(sandbox / 'after.json')
    assert sql_snapshot(readonly.profile()) == read(sandbox / 'after.json')['readonly_sql']
    readonly.verify_admission(readonly.profile())
    assert readonly.inspect()['status'] == current.owned.inspect()['status'] == 'running'
    assert uploads.worker_status()['status'] == 'ready' and platform_entry.observer_process() is not None
    uploads.verify_candidate(settings, uploads.get_candidate('local-work-eb06bef4017f'), writable=True)
    assert {row['id']: digest(uploads.safe_path('uploads', row['id'], 'upload.json')) for row in uploads.uploads()} == after['uploads']
    for folder in (current.merge.CANDIDATE, current.merge.REPORT, current.REVIEW,
                   current.PROJECT / 'data/candidates/agent-domain-review-r1'):
        verify_seal(folder)
    engine = sa.create_engine(settings.mysql_dsn)
    try:
        with engine.connect() as conn:
            assert conn.execute(sa.text("SELECT COUNT(*) FROM api_keys WHERE user_id LIKE :p"), {'p': 'version-original-audit-%'}).scalar_one() == 0
            assert conn.execute(sa.text("SELECT version FROM kb_versions WHERE status='ACTIVE'")).scalars().all() == [current.VERSION]
    finally:
        engine.dispose()
    packages = Workspace(settings).packages('local-work-eb06bef4017f')
    assert packages and all(p['merge_outcome'] == 'no_content_change' for p in packages)
    bootstrap = read(readonly.ROOT / 'bootstrap.json')
    assert bootstrap['phase'] == 'ready'
    summary = dict(passed=True, engineering_modules_implemented=6, total_requested_items=6,
        original_publication_workflow_complete=False, unit_tests=tests,
        sandbox_api_checks=proofs[0]['api_checks'], sandbox_browser_checks=len(browsers[0]['checks']),
        original_api_checks=proofs[1]['api_checks'], original_browser_checks=len(browsers[1]['checks']),
        actual_sandbox_publish_and_rollback=True, sandbox_files=3, sandbox_rows_per_version=62,
        original_source_package_files=306, original_source_package_chunks=4357,
        original_package_outcome='no_content_change', original_delta_gate='blocked',
        original_active=current.VERSION, original_active_unchanged=True, original_publication_executed=False,
        protected_snapshots_and_seals_unchanged=True, pending_not_admitted=True,
        worker_candidate_binding_preserved=True, approved_isolated_domain_rows=16,
        paid_calls=0, new_admissions=0, temporary_audit_keys_remaining=0,
        independent_quality_verified=False, health_samples=4, owned_runtime_visible_windows=0,
        window_scope='Owned processes and their descendants only; existing user terminals were not closed by title',
        health_scope='Four observations 20 seconds apart; no long-term SLA claim',
        remaining=['Original service full-configuration publication handoff for an eligible changed candidate',
                   'SOP safety support and complete field procedures', 'Vendor/model/software/threshold evidence',
                   'Unexposed samples and human truth for independent quality; frozen denominator remains zero'])
    write(REPORT / 'final-summary.json', summary)
    write(REPORT / 'visual-review.json', dict(reviewed=True,
        reviewed_images=[str(original.relative_to(REPORT)) + '/original-version-candidate.png',
                         str(original.relative_to(REPORT)) + '/original-version-mobile.png',
                         str(sandbox.relative_to(REPORT)) + '/version-published.png'],
        note='Original desktop/mobile show real blocked gate and pending separation. Sandbox publication capture includes detail reload in progress; SQL ACTIVE and history were independently asserted, so this image is not claimed to show final loaded detail.'))
    paths = ['app.py', 'qa_core/quality/version_workspace.py', 'qa_core/api/route_version_workspace.py',
        'static/index.html', 'static/version-workspace.js', 'static/css/version-workspace.css', 'static/version-rehearsal.html',
        'scripts/verify_version_workspace.py', 'scripts/verify_version_workspace_original.py',
        'scripts/verify_version_workspace_survival.py', 'scripts/finalize_version_workspace.py',
        'scripts/readonly_workspace_bootstrap.py', 'scripts/start_readonly_workspace.ps1',
        'scripts/start_platform.ps1', 'scripts/start_platform_runtime.ps1', 'scripts/start_platform_observer.ps1',
        'scripts/start_content_quality_background_v2.ps1', 'scripts/start_workspace_worker_background.ps1',
        'tests/unit/test_version_workspace.py', 'tests/unit/test_windowless_bootstrap.py',
        'tests/browser/version_workspace_smoke.js', 'tests/browser/version_workspace_original.js',
        'README.md', 'docs/README.md', 'docs/API_CONTRACT.md', 'docs/HANDOFF_2026-10-07.md', 'docs/DEVELOPMENT_PLAN_2026-10-07.md']
    write(REPORT / 'code-bindings.json', {name: digest(current.PROJECT / name) for name in paths})
    paragraphs = [
        '后台启动空窗修复：应用、只读服务、Worker、观察器改用pythonw.exe。只读依赖命令通过启动外壳显式CREATE_NO_WINDOW，冻结运行控制器与Worker绑定保持。最终四次观察均健康，自有进程及后代没有可见窗口；不按窗口标题关闭用户其他终端。隐藏控制台宿主不等于可见弹窗，首轮过严断言导致观察中断，记录保留。',
        '第6项版本页面：具体来源身份/范围及管理员原文读取、pending隔离、工程核对、完整原件封包、五项门禁任务/失败/重启中断、发布/回滚计划与确认、归属账本已实现。178相关单元通过。临时隔离库14接口/拒绝边界、13真实Edge检查；原8010另26接口、10真实Edge检查。',
        '真实发布与回滚仅在一次性隔离SQL/Milvus完成：3份既有批准模拟资料、每版本62行。门禁、唯一ACTIVE、版本状态事务、审核人及快照、缓存失效、计划防重放、实际向量篡改与撤回拒绝已验证。临时库、集合、grant、HTTP和Key均清理。这个小库不证明整体知识质量。',
        '原8010仍local-content-quality-r1、生成开/Agent关。现有STAGED只有相同FAQ复本及pending新文件；仅1FAQ/0文档，真实五项门禁blocked。可将全部306份获准原件汇总封存，4357片段对应完整基线，但合并no_content_change，不创建伪候选或改ACTIVE。不同集合的发布/回滚必须完整运行配置交接，原页明确阻断只改指针的操作。',
        '保持现有角色：未收窄范围default管理员写入，审计员只读，ops不获得全局治理或凭据权限；具体正文仅管理员。离开、换Key或降权立即清理，迟到管理员响应丢弃；390手机无横向溢出。工程审核不等于新增资料准入，pending无新任务/向量或封包准入。',
        '原ACTIVE完整发布快照、除现有STAGED门禁/快照外的保护SQL、隔离16条具体批准、父包封存、上传状态、.env.runtime及预算3/4保持；新付费0、新准入0，原发布未执行。恢复后本地登录Key轮换，请重新打开各自私有登录文件。',
        '失败保留：初次发布后UI迟到状态导致回滚探针失败；来源字段归一化丢失治理字段导致错误阻断；同URI SDK默认连接别名关闭其它句柄导致后续检查失败。分别修复epoch保护、原声明与规范字段合并、独立探针alias后完整复验。若干原入口预检查因入口未就绪退出，未当作验收通过；最终原入口批次单独留档。',
        '六项工程模块已实现限定路径，原完整配置发布交接尚未作为本步线上发布验收；SOP安全支持性、设备型号版本阈值与完整现场流程、BFD/NETCONF等pending、独立未暴露样本/人工真值仍缺。可读数据、结构门禁、小库发布或工程回归均不能代替这些验收。']
    page = '<!doctype html><html lang="zh-CN"><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>第6项版本工作区与后台窗口修复验收</title><style>body{font:16px/1.8 system-ui;color:#19304f;max-width:1100px;margin:32px}img{max-width:100%}pre{white-space:pre-wrap;overflow-wrap:anywhere}details{margin:18px 0}</style><h1>版本工作区与后台窗口修复</h1>'
    page += ''.join('<p>' + html.escape(p) + '</p>' for p in paragraphs)
    for folder, browser in zip((sandbox, original), browsers):
        page += '<h2>' + html.escape(folder.name) + '</h2><ul>' + ''.join('<li>' + html.escape(c) + '</li>' for c in browser['checks']) + '</ul>'
        for name in browser['screenshots']:
            relative = str(folder.relative_to(REPORT)).replace('\\', '/') + '/' + name
            page += '<details><summary>' + html.escape(relative) + '</summary><img alt="真实本地页面" src="' + html.escape(relative, quote=True) + '"></details>'
    page += '<details><summary>最终摘要</summary><pre>' + html.escape(json.dumps(summary, ensure_ascii=False, indent=2)) + '</pre></details></html>'
    (REPORT / 'review.html').write_text(page, encoding='utf-8')
    seal(REPORT)
    verify_seal(REPORT)
    print(json.dumps(summary, ensure_ascii=True), flush=True)


if __name__ == '__main__':
    main()
