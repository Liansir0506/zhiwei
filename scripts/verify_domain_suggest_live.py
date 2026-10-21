"""Live read-only verification of the workspace alarm/device suggestion endpoint."""
import json
from pathlib import Path

import httpx
import sqlalchemy as sa

from scripts import demo_release_runtime_r2 as release

PROJECT = release.PROJECT
REPORT = PROJECT / '.runtime/test/reports/domain-suggest-live-20261016'
LIMIT = 8


def write(name, value):
    REPORT.mkdir(parents=True, exist_ok=True)
    (REPORT / name).write_text(json.dumps(value, ensure_ascii=False, indent=2), encoding='utf-8')


def expected_rows(engine, kind, query, limit=LIMIT):
    """Mirror the reviewed DAO query exactly; used as database ground truth."""
    text = (query or '').strip()
    if len(text) < 2:
        return []
    escaped = text.replace('\\', '\\\\').replace('%', '\\%').replace('_', '\\_')
    if kind == 'alarm':
        sql = ("SELECT alarm_id, alarm_code, severity, status FROM alarms "
               "WHERE tenant_id='default' AND (alarm_id LIKE :pattern ESCAPE '\\\\' "
               "OR alarm_code LIKE :pattern ESCAPE '\\\\') "
               "ORDER BY occurred_at DESC, alarm_id LIMIT :limit")
    else:
        sql = ("SELECT device_id, name, device_type FROM devices "
               "WHERE tenant_id='default' AND (device_id LIKE :pattern ESCAPE '\\\\' "
               "OR name LIKE :pattern ESCAPE '\\\\') "
               "ORDER BY device_id LIMIT :limit")
    with engine.connect() as conn:
        return [dict(row) for row in conn.execute(
            sa.text(sql), {'pattern': '%' + escaped + '%', 'limit': limit}).mappings().all()]


def main():
    checks = []

    def check(name, ok, detail):
        checks.append({'check': name, 'ok': bool(ok), 'detail': detail})

    release.validate_review()
    settings = release.selected_settings()
    state = release.owned.load_state()
    if not state or state.get('status') != 'running':
        raise RuntimeError('owned runtime is not recorded as running')
    key = release.owned.read_key(state)
    engine = sa.create_engine(settings.mysql_dsn)
    headers = {'X-API-Key': key}

    with httpx.Client(base_url=state['url'], headers=headers, timeout=20, trust_env=False) as client:
        identity = client.get('/local/runtime').json()
        check('运行时身份', identity == {'instance': state['instance'], 'generation_enabled': settings.generation_enabled,
                                   'agent_enabled': False}, identity)
        health = client.get('/health?refresh=1').json()
        check('健康与安全模式', health.get('status') == 'ok' and health.get('generation_enabled') is True
              and health.get('agent_enabled') is False,
              {k: v for k, v in health.items() if k in ('status', 'generation_enabled', 'agent_enabled', 'components')})
        page = client.get('/').text
        check('页面含联想组件', 'id="contextSuggest"' in page and 'role="combobox"' in page, 'index.html')
        script = client.get('/static/workspace.js').text
        check('前端脚本含联想逻辑', '/domain/suggest' in script and 'contextSuggest' in script, 'workspace.js')

        cases = [('alarm', 'SIM-ALM-0000'), ('alarm', 'ALM-AMF'), ('alarm', 'ALM-UPF-DROP'),
                 ('device', 'SIM-DEV-000'), ('device', 'AMF'), ('device', '模拟SMF')]
        for kind, query in cases:
            response = client.get('/domain/suggest', params={'kind': kind, 'q': query, 'limit': LIMIT})
            body = response.json()
            truth = expected_rows(engine, kind, query)
            check('联想比对 ' + kind + '/' + query, response.status_code == 200 and body.get('kind') == kind
                  and body.get('items') == truth and 0 < len(truth) <= LIMIT,
                  {'http': response.status_code, 'returned': len(body.get('items', [])), 'expected': len(truth),
                   'first': (body.get('items') or [None])[0]})

        short = client.get('/domain/suggest', params={'kind': 'alarm', 'q': 'S'}).json()
        check('短查询返回空', short == {'kind': 'alarm', 'items': []}, short)
        bad = client.get('/domain/suggest', params={'kind': 'ticket', 'q': 'SIM'})
        check('非法类型拒绝', bad.status_code == 422, bad.status_code)
        anonymous = httpx.get(state['url'] + '/domain/suggest', params={'kind': 'alarm', 'q': 'SIM'},
                              timeout=20, trust_env=False)
        check('匿名访问拒绝', anonymous.status_code in (401, 403), anonymous.status_code)
        wide = client.get('/domain/suggest', params={'kind': 'alarm', 'q': 'SIM-ALM', 'limit': 10}).json()
        check('limit 生效', len(wide['items']) == 10, len(wide['items']))

    engine.dispose()
    passed = all(item['ok'] for item in checks)
    write('live.json', {'checks': checks, 'passed': passed, 'endpoint': '/domain/suggest',
                        'verification': 'http_live', 'paid_calls': 0, 'external_calls': 0})
    print(json.dumps(checks, ensure_ascii=False, indent=2))
    print('live verification:', 'passed' if passed else 'failed')
    return 0 if passed else 1


if __name__ == '__main__':
    raise SystemExit(main())
