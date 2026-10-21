"""Seal implementation evidence without touching publication or evaluation history."""
import html
import sqlalchemy as sa
from scripts import content_quality_release_runtime as runtime
from scripts.prepare_legacy_projection import read, write, digest, seal, verify_seal
from scripts.verify_evaluation_workspace import REPORT


def main():
    browser = read(REPORT / 'browser-result.json')
    detail = read(REPORT / 'current-report.json')
    cleanup = read(REPORT / 'cleanup.json')
    preservation = read(REPORT / 'preservation.json')
    supplement = read(REPORT / 'snapshot-verification.json')
    assert supplement['passed']
    assert browser['passed'] and detail['sample_count'] == 31 and not detail['independent_verified']
    assert cleanup['temporary_keys_remaining'] == 0 and cleanup['budget_unchanged']
    assert all(preservation[name] for name in ('frozen_seal_hashes_unchanged', 'messages_unchanged', 'sessions_unchanged', 'budget_unchanged'))
    assert not (REPORT / 'edge-profile').exists()
    settings = runtime.selected_settings(); runtime.install_adapter()
    assert runtime.owned.inspect()['status'] == 'running'
    engine = sa.create_engine(settings.mysql_dsn)
    with engine.connect() as conn:
        active = conn.execute(sa.text("SELECT version FROM kb_versions WHERE status='ACTIVE'")).scalars().all()
        remaining = conn.execute(sa.text("SELECT COUNT(*) FROM api_keys WHERE user_id LIKE 'evaluation-workspace-audit-%'")).scalar()
    engine.dispose()
    assert active == [runtime.VERSION] and remaining == 0
    assert digest(runtime.PROJECT / '.env.runtime') == '045ef4acd5b9babfea24d0c3e9b61679deb51a84c6803eb2e4f553139dbc19ff'
    state = runtime.owned.load_state()
    paths = ['qa_core/api/route_admin.py', 'qa_core/quality/evaluation_workspace.py', 'static/index.html',
        'static/governance-workspace.js', 'static/css/workspace.css', 'tests/unit/test_evaluation_workspace.py',
        'tests/browser/evaluation_workspace_smoke.js', 'scripts/verify_evaluation_workspace.py',
        'scripts/finalize_evaluation_workspace.py']
    write(REPORT / 'code-bindings.json', {name: digest(runtime.PROJECT / name) for name in paths})
    write(REPORT / 'final-summary.json', {'passed': True, 'unit_tests': 39, 'browser_checks': len(browser['checks']),
        'current_faq_cases': 31, 'independent_quality_verified': False, 'generation_quality_assessed': False,
        'paid_calls': 0, 'active': active, 'api_pid': state.get('pid'), 'temporary_keys_remaining': remaining,
        'first_failure': 'Windows atomic job-file replacement sharing conflict before first case; synchronized reads/writes and bounded retry added.',
        'second_harness_failure': 'Browser wait expired before the real task finished successfully in about 50 seconds; bounded wait adjusted.',
        'third_metric_failure': 'Raw substring check marked 10 preserved footnote-label conversions as failures (21/31). Prior reports kept; new whole-body criterion only restores documented footnotes.',
        'permissions_expanded': False, 'original_env_unchanged': True, 'frozen_bindings_verified': True})
    page = '<!doctype html><html lang="zh-CN"><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>版本评测工作区验收</title>'
    page += '<style>body{font:16px/1.8 system-ui;margin:32px;color:#19304f}img{max-width:100%}td,th{border:1px solid #ddd;padding:10px;text-align:left}table{border-collapse:collapse;width:100%}details{margin:16px 0}code{overflow-wrap:anywhere}</style>'
    page += '<h1>第3项 · 按版本组织报告与受控评测</h1><p>39项专项测试、' + str(len(browser['checks'])) + '项真实Edge检查通过。既有11份历史问答评测报告、当前结构门禁及新FAQ回归接入同一索引；不复制或重写历史报告。3份缺少版本的历史报告保持未归属。</p>'
    page += '<p>真实default管理员从产品按钮准备计划、执行31题原句FAQ回归、查看结果与刷新追踪；route/hit/status/body明确检查各31/31，拒答分母0。受控任务固定ACTIVE/配置/代码/FAQ摘要/账号，强制no_llm、隔离无会话持久化，独立可写.runtime/evaluations目录，不往封存候选或门禁报告目录写入。</p>'
    page += '<p>这是同库原文自回归，不能证明FAQ语义正确、文档生成、拒答或独立质量。当前独立评测仍缺未暴露样本、人工真值及冻结审核；结构门禁不证明200份SOP支持性。设备型号、版本、阈值及完整现场流程缺口继续保留。</p>'
    page += '<p>首轮Windows进度文件共享冲突0/31失败及第二轮浏览器等待超时均保留。第二轮实际任务约50秒完成，后续扩大测试等待后完整通过。报告读取503为传输故障注入，身份/版本/任务/FAQ均来自真实本地服务。临时审查Key全部删除，生成验证预算未变，模型付费0；正常鉴权审计与生命周期凭据写入发生，不声明数据库零写入。</p>'
    page += '<p>第三轮17项页面检查通过，但旧正文子串判据得到21/31：10份正文的[n]原文脚注按既有显示规则改为〔原文注n〕。原21/31报告保留；新判据只还原这一标记后精确比较完整正文，空白、段落、截断或额外文字均不忽略，并在页面显示判据。</p>'
    page += '<p>接口：GET /admin/eval/reports、GET /admin/eval/reports/{id}、POST /admin/eval/plans、POST /admin/eval/jobs、GET /admin/eval/jobs/{id}。default管理员执行，审计员只读；ops及其他租户不能读取或执行。原兼容GET /admin/eval只选择当前版本的开发回归。</p><ul>'
    page += ''.join('<li>' + html.escape(check) + '</li>' for check in browser['checks']) + '</ul><table><tr><th>检查</th><th>实际通过/分母</th></tr>'
    for field, check in detail['checks'].items():
        page += '<tr><td>' + html.escape(field) + '</td><td>' + str(check['passed']) + '/' + str(check['evaluated']) + '</td></tr>'
    page += '</table>'
    for name in browser['screenshots']:
        page += '<details><summary>' + html.escape(name) + '</summary><img src="' + html.escape(name, quote=True) + '" alt="实际页面"></details>'
    page += '<details><summary>读取时间与版本范围补充核对（只读）</summary><img src="evaluation-snapshot.png" alt="范围核对"></details>'
    (REPORT / 'review.html').write_text(page + '</html>', encoding='utf-8')
    seal(REPORT); verify_seal(REPORT)
    print('Evaluation workspace sealed: 39 unit checks, ' + str(len(browser['checks'])) + ' browser checks, 31 FAQ cases, paid calls 0.')


if __name__ == '__main__':
    main()
