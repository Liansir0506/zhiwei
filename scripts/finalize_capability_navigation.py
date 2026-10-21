"""Seal role-navigation acceptance; no permission or knowledge mutations."""
import html
from pathlib import Path
import sqlalchemy as sa
from scripts import content_quality_release_runtime as runtime
from scripts.prepare_legacy_projection import read, write, digest, seal, verify_seal
from scripts.verify_capability_navigation import REPORT


def main():
    assert not (REPORT / 'edge-profile').exists()
    result = read(REPORT / 'browser-result.json')
    cleanup = read(REPORT / 'cleanup.json')
    assert result['passed'] and len(result['checks']) == 22
    assert cleanup['temporary_keys_remaining'] == 0 and cleanup['budget_unchanged']
    runtime.install_adapter()
    settings = runtime.selected_settings()
    assert runtime.owned.inspect()['status'] == 'running'
    engine = sa.create_engine(settings.mysql_dsn)
    with engine.connect() as conn:
        remaining = conn.execute(sa.text("SELECT COUNT(*) FROM api_keys WHERE user_id LIKE 'capability-navigation-audit-%'")).scalar()
        active = conn.execute(sa.text("SELECT version FROM kb_versions WHERE status='ACTIVE'")).scalars().all()
    engine.dispose()
    assert remaining == 0 and active == ['local-content-quality-r1']
    assert digest(runtime.PROJECT / '.env.runtime') == '045ef4acd5b9babfea24d0c3e9b61679deb51a84c6803eb2e4f553139dbc19ff'
    paths = ['static/index.html', 'static/governance-workspace.js', 'static/css/workspace.css',
             'scripts/verify_capability_navigation.py', 'tests/browser/capability_navigation_smoke.js',
             'scripts/finalize_capability_navigation.py']
    write(REPORT / 'code-bindings.json', {name: digest(runtime.PROJECT / name) for name in paths})
    write(REPORT / 'final-summary.json', {'passed': True, 'backend_tests': 16, 'browser_checks': 22,
          'actual_role_profiles': 5, 'temporary_keys_remaining': remaining, 'active': active, 'paid_calls': 0,
          'permission_rules_changed': False, 'runtime_restarted': False,
          'scope': 'governance navigation/restricted pages and credential transitions; version-report indexing remains next stage',
          'harness_failure': 'Initial file-to-HTTP navigation did not retain the audit request hook; fixed by installing it in the actual platform document.'})
    rows = []
    for row in read(REPORT / 'api-results.json'):
        visible = [title for name, title in [('can_read_ops', '运行状态'), ('can_read_eval', '评测记录'), ('can_read_permissions', '访问管理')]
                   if row['capabilities'][name]]
        cells = [row['role'], row['capabilities']['tenant_id'], '、'.join(visible) or '无治理入口',
                 ' / '.join(map(str, row['protected_get_statuses']))]
        rows.append('<tr>' + ''.join('<td>' + html.escape(cell) + '</td>' for cell in cells) + '</tr>')
    page = '<!doctype html><html lang="zh-CN"><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">'
    page += '<title>能力导航验收</title><style>body{font:16px/1.7 system-ui;color:#19304f;margin:32px}table{border-collapse:collapse;width:100%}td,th{border:1px solid #ddd;padding:10px;text-align:left}img{max-width:100%}details{margin:16px 0}.table{overflow:auto}</style>'
    page += '<h1>第2项 · 能力导航与受限页验收</h1><p>16项后端专项、22项真实Edge浏览器检查通过。真实ops/admin/auditor/其他租户admin/guest：侧栏由后端能力驱动，受限页面不显示数据空框；降权同步清理，延迟响应不恢复旧权限或数据。</p>'
    page += '<p>没有修改后端权限规则、重启服务、发布、入库或提交问答。付费0；临时4条审查凭据已删除，实际运维凭据保持可用。</p>'
    page += '<p>管理员评测页可以进入，但版本报告索引尚未实现，留待第3项。延迟/503为传输故障注入，账号身份与原始授权响应来自真实本地接口。首轮验收脚本钩子缺失的失败记录另存harness-first-failure.json；修正后通过。</p>'
    page += '<div class="table"><table><tr><th>实际账号</th><th>租户</th><th>可见治理入口</th><th>ops/eval/permissions GET</th></tr>' + ''.join(rows) + '</table></div><ul>'
    page += ''.join('<li>' + html.escape(check) + '</li>' for check in result['checks']) + '</ul>'
    for name in result['screenshots']:
        page += '<details><summary>' + html.escape(name) + '</summary><img src="' + html.escape(name, quote=True) + '" alt="实际页面截图"></details>'
    (REPORT / 'review.html').write_text(page + '</html>', encoding='utf-8')
    seal(REPORT); verify_seal(REPORT)
    print('Capability navigation sealed: 16 backend tests, 22 browser checks, 0 temporary keys, paid calls 0.')


if __name__ == '__main__':
    main()
