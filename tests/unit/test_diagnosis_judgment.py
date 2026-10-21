# -*- coding: utf-8 -*-
"""T2 诊断判断层单测：事实提取、候选排序、证据变化敏感性。"""
from qa_core.application.diagnosis_judgment import build_diagnosis, render_diagnosis


def ctx(**kw):
    base = {
        'alarm': {'alarm_id': 'SIM-ALM-000001', 'alarm_code': 'ALM-AMF-REG', 'severity': 'critical',
                  'status': 'open', 'occurred_at': '2026-10-19T10:00:00', 'device_id': 'SIM-DEV-1'},
        'device': {'device_id': 'SIM-DEV-1', 'name': 'AMF-01', 'device_type': 'AMF',
                   'status': kw.pop('device_status', 'online'), 'software_version': 'v2.4.1'},
        'alarms': kw.pop('alarms', [{'alarm_id': 'SIM-ALM-000001', 'alarm_code': 'ALM-AMF-REG',
                                     'occurred_at': '2026-10-19T10:00:00'}]),
        'monitoring': kw.pop('monitoring', [{'observation_id': 'O1', 'metric_name': '注册成功率',
                                             'metric_value': '99.1', 'unit': '%',
                                             'sampled_at': '2026-10-19T09:58:00'}]),
        'work_orders': [], 'changes': kw.pop('changes', []),
    }
    base.update(kw)
    return base


def test_amf_scenario_candidates_present():
    d = build_diagnosis(ctx(), 'AMF 注册失败排查', [])
    assert d['scope']['scenario'] == 'amf-registration'
    ids = {c['id'] for c in d['candidates']}
    assert {'amf-capacity', 'ran-access', 'recent-change'} <= ids
    # 孤立事件 + 设备在线 + 无变更：过载 unlikely、接入 unlikely、变更 unlikely
    verdicts = {c['id']: c['verdict'] for c in d['candidates']}
    assert verdicts['amf-capacity'] == 'unlikely'
    assert verdicts['ran-access'] == 'unlikely'
    assert verdicts['recent-change'] == 'unlikely'


def test_downstream_evidence_promotes_ran_candidate():
    # 补入下游异常证据：设备离线 + 伴生告警 → 接入侧候选升为 likely
    c = ctx(device_status='offline',
            alarms=[{'alarm_id': 'A1', 'alarm_code': 'ALM-AMF-REG', 'occurred_at': '2026-10-19T10:00:00'},
                    {'alarm_id': 'A2', 'alarm_code': 'ALM-N2-LINK', 'occurred_at': '2026-10-19T09:41:00'},
                    {'alarm_id': 'A3', 'alarm_code': 'ALM-CELL-DOWN', 'occurred_at': '2026-10-19T09:30:00'}])
    d = build_diagnosis(c, 'AMF 注册失败排查', [])
    verdicts = {x['id']: x['verdict'] for x in d['candidates']}
    assert verdicts['ran-access'] == 'likely'
    # 事实应包含伴生告警
    assert any('伴生' in f['text'] for f in d['facts'])


def test_capacity_evidence_promotes_overload():
    # 同码告警集中 + 指标劣化 → 过载候选升为 likely
    c = ctx(alarms=[{'alarm_id': f'A{i}', 'alarm_code': 'ALM-AMF-REG', 'occurred_at': '2026-10-19T10:00:00'}
                    for i in range(4)],
            monitoring=[{'observation_id': 'O1', 'metric_name': '注册成功率', 'metric_value': '82',
                         'unit': '%', 'sampled_at': '2026-10-19T09:58:00'}])
    d = build_diagnosis(c, 'AMF 注册失败排查', [])
    verdicts = {x['id']: x['verdict'] for x in d['candidates']}
    assert verdicts['amf-capacity'] == 'likely'


def test_change_before_alarm_promotes_change_candidate():
    c = ctx(changes=[{'change_id': 'CHG-1', 'change_type': 'config', 'status': 'completed',
                      'applied_at': '2026-10-19T08:00:00'}])
    d = build_diagnosis(c, 'AMF 注册失败排查', [])
    verdicts = {x['id']: x['verdict'] for x in d['candidates']}
    assert verdicts['recent-change'] == 'likely'
    assert any('变更内容明细' in m for m in d['missing_info'])


def test_missing_monitoring_flagged():
    d = build_diagnosis(ctx(monitoring=[]), 'AMF 注册失败排查', [])
    assert any('监控' in m for m in d['missing_info'])
    # 且事实里有显式缺失说明
    assert any(f['text'].startswith('缺失') for f in d['facts'])


def test_generic_scenario_declared():
    d = build_diagnosis(ctx(alarm={'alarm_id': 'A1', 'alarm_code': 'ALM-XXX', 'severity': 'major',
                                   'status': 'open', 'occurred_at': '2026-10-19T10:00:00'}),
                        '设备异常排查', [])
    assert d['scope']['scenario'] == 'generic'
    assert '未匹配专用规则' in d['scope']['scenario_note']
    assert d['candidates']


def test_no_auto_root_cause():
    d = build_diagnosis(ctx(), 'AMF 注册失败排查', [])
    assert '不作为根因结论' in d['recommendation']
    # 候选只有 likely/possible/unlikely，没有 confirmed
    assert all(c['verdict'] in ('likely', 'possible', 'unlikely') for c in d['candidates'])


def test_render_is_chinese_readable():
    text = render_diagnosis(build_diagnosis(ctx(), 'AMF 注册失败排查', []), [])
    for section in ['【事件范围】', '【事实与异常】', '【候选原因】', '【建议】']:
        assert section in text
