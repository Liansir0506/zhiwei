"""Seal bounded P0 acceptance evidence; ongoing observations remain outside it."""
import html
from pathlib import Path
import sqlalchemy as sa
from scripts import platform_entry as entry
from scripts.prepare_legacy_projection import read, write, digest, seal, verify_seal

REPORT = entry.PROJECT / '.runtime/test/reports/platform-entry-p0-20261012'


def main():
    assert not (REPORT / 'edge-profile').exists()
    live = read(REPORT / 'live-result.json')
    assert live['observed_interruption'] and len(live['stable_samples']) >= 3
    assert all(row['status'] == 'ready' for row in live['stable_samples'])
    browser = read(REPORT / 'browser-result.json')
    assert browser['fresh_login'] and browser['stale_login_hidden'] and not browser['mobile_overflow']
    probe = entry.RuntimeProbe()
    final = probe.sample()
    assert final['status'] == 'ready' and entry.observer_process() is not None
    settings = probe.release.selected_settings()
    engine = sa.create_engine(settings.mysql_dsn)
    with engine.connect() as conn:
        active = conn.execute(sa.text("SELECT version FROM kb_versions WHERE status='ACTIVE'")).scalars().all()
    engine.dispose()
    assert active == ['local-content-quality-r1']
    assert digest(entry.PROJECT / '.env.runtime') == '045ef4acd5b9babfea24d0c3e9b61679deb51a84c6803eb2e4f553139dbc19ff'
    for name in ('bootstrap.json', 'bootstrap-events.jsonl', 'observer.json', 'availability.json'):
        (REPORT / name).write_bytes((entry.ROOT / name).read_bytes())
    bindings = ['scripts/platform_entry.py', 'scripts/platform_runtime_bootstrap.py',
                'scripts/start_platform.ps1', 'scripts/start_platform_runtime.ps1',
                'scripts/start_platform_observer.ps1', 'scripts/stop_platform.ps1',
                'scripts/verify_platform_entry_live.py', 'scripts/verify_platform_status_browser.js',
                'scripts/finalize_platform_entry.py', 'tests/unit/test_platform_entry.py',
                'tests/unit/test_platform_runtime_bootstrap.py']
    write(REPORT / 'code-bindings.json', {name: digest(entry.PROJECT / name) for name in bindings})
    write(REPORT / 'final-summary.json', {'final_runtime': final, 'active': active, 'specialized_tests_passed': 22,
          'persistent_observer_alive': True, 'paid_calls': 0, 'engine_stop_performed': False,
          'known_failure': 'first v2 recovery attempt failed; exact cause not captured; retained without attributing a cause',
          'limitations': ['Docker-off and storage-preheat failure scenarios are fault injected',
                         'short ongoing observation is not a long-term availability SLA',
                         'no QA generation or content-quality evaluation performed']})
    text = '<!doctype html><html lang="zh-CN"><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">'
    text += '<title>P0入口验收</title><style>body{font:16px/1.7 system-ui;margin:32px;color:#19304f}img{max-width:100%}pre{white-space:pre-wrap}</style>'
    text += '<h1>P0 · 统一依赖与入口验收</h1><p>统一启动scripts/start_platform.ps1；停止scripts/stop_platform.ps1。'
    text += '只处理五个既有存储、自有应用和观察进程，不运行旧API/Worker/mock、不发布或入库。付费0。</p>'
    text += '<p>22项专项通过。真实应用停止被后台记录，统一入口恢复后连续3次健康；后台持续约30秒检查。'
    text += '真实Edge桌面/390手机布局通过，超过90秒未更新会提示过期并隐藏登录入口。</p>'
    text += '<p>Docker未启动、存储预热失败为故障注入验证；本轮没有停止实际Docker引擎。'
    text += '首轮旧v2恢复失败原因未捕获，保留first-attempt-failure.json及后续v2结果，不能声称已经定位。'
    text += '新启动器记录配置校验、发布快照、应用预热阶段与耗时，最新真实启动完成。长期稳定性继续观察，不声明生产SLA。</p>'
    text += '<h2>最新真实启动阶段</h2><pre>' + html.escape((REPORT / 'bootstrap-events.jsonl').read_text(encoding='utf-8')) + '</pre>'
    text += '<h2>实际中断与恢复</h2><pre>' + html.escape((REPORT / 'observations.json').read_text(encoding='utf-8')) + '</pre>'
    for name in ('status-desktop.png', 'status-mobile.png', 'status-stale.png'):
        text += '<h2>' + name + '</h2><img src="' + name + '" alt="实际Edge状态页">'
    (REPORT / 'review.html').write_text(text, encoding='utf-8')
    seal(REPORT); verify_seal(REPORT)
    print('P0 acceptance sealed; ongoing observer alive; paid calls 0.')


if __name__ == '__main__':
    main()
