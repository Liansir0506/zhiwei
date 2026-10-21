# -*- coding: utf-8 -*-
"""规则驱动的诊断判断层（T2）。

从领域上下文（告警/设备/近期告警/监控/工单/变更快照）提取事实，按场景规则生成
「候选原因 → 支持/反对/缺失证据 → 下一步检查」。全部候选只标记倾向（likely/
possible/unlikely），根因确认必须由人工复核完成——本模块不做自动定论。

敏感性契约：候选的支持/反对证据完全由 context 数据派生；补入下游异常证据
（伴生告警、指标劣化）或移除关键证据时，候选与结论应发生合理变化。
第一版内置 AMF 注册失败场景；未匹配场景时输出通用核查建议并明确说明。
"""
from datetime import datetime, timedelta, timezone
import re

VERDICT_LABELS = {'likely': '倾向支持', 'possible': '可能相关', 'unlikely': '倾向排除'}


def _iso(value):
    if isinstance(value, datetime):
        return value.isoformat()
    return str(value) if value else None


def _parse_time(value):
    if isinstance(value, datetime):
        return value
    if isinstance(value, str):
        try:
            return datetime.fromisoformat(value.replace('Z', '+00:00'))
        except ValueError:
            return None
    return None


def _rows(context, kind):
    rows = context.get(kind) or []
    return rows if isinstance(rows, list) else [rows]


def extract_facts(context):
    """事实与异常：每条事实标注来源观测，供候选引用。"""
    facts, refs = [], []

    def add(text, entity_type, entity_id):
        refs.append({'entity_type': entity_type, 'entity_id': entity_id})
        facts.append({'id': f'F{len(facts) + 1}', 'text': text, 'entity_type': entity_type,
                      'entity_id': entity_id})

    alarm = context.get('alarm') or {}
    device = context.get('device') or {}
    if alarm:
        add(f"告警事件 {alarm.get('alarm_id')}（{alarm.get('alarm_code')}，"
            f"级别 {alarm.get('severity')}，状态 {alarm.get('status')}）发生于 "
            f"{_iso(alarm.get('occurred_at')) or '未知时间'}", 'alarm', alarm.get('alarm_id'))
    if device:
        status = device.get('status')
        add(f"设备 {device.get('device_id')}（{device.get('name') or '—'}，类型 {device.get('device_type')}）"
            f"当前状态 {status or '未知'}，软件版本 {device.get('software_version') or '未知'}",
            'device', device.get('device_id'))
        if status and status != 'online':
            add(f"异常：设备状态为 {status}，非正常在线", 'device', device.get('device_id'))
    recent = _rows(context, 'alarms')
    same_code = [a for a in recent if a.get('alarm_code') == alarm.get('alarm_code')]
    other_code = [a for a in recent if a.get('alarm_code') != alarm.get('alarm_code')]
    if len(same_code) >= 2:
        add(f"异常：同设备近窗口内同码告警 {len(same_code)} 条（含本条），存在重复/持续触发", 'alarms',
            recent[0].get('alarm_id'))
    if other_code:
        codes = sorted({a.get('alarm_code') for a in other_code})
        add(f"伴生告警：同设备近期还有 {len(other_code)} 条其他告警（{', '.join(codes[:4])}"
            f"{'等' if len(codes) > 4 else ''}）", 'alarms', other_code[0].get('alarm_id'))
    elif len(same_code) < 2:
        add("同设备近期无其他告警且无同码重复（当前为孤立事件）", 'alarms', alarm.get('alarm_id'))
    metrics = _rows(context, 'monitoring')
    if metrics:
        names = sorted({m.get('metric_name') for m in metrics if m.get('metric_name')})
        latest = metrics[0]
        add(f"监控快照 {len(metrics)} 条，指标含：{', '.join(names[:5])}；最近采样 "
            f"{latest.get('metric_name')}={latest.get('metric_value')}{latest.get('unit') or ''}（"
            f"{_iso(latest.get('sampled_at')) or '时间未知'}）", 'monitoring',
            latest.get('observation_id'))
    else:
        add("缺失：无监控指标快照，无法判断性能趋势", 'monitoring', None)
    tickets = _rows(context, 'work_orders')
    if tickets:
        add(f"同设备存在 {len(tickets)} 张历史工单（最近：{tickets[0].get('ticket_id')}，"
            f"状态 {tickets[0].get('status')}）", 'work_orders', tickets[0].get('ticket_id'))
    changes = _rows(context, 'changes')
    if changes:
        change = changes[0]
        add(f"近期变更记录 {len(changes)} 条（最近：{change.get('change_id')}，"
            f"类型 {change.get('change_type')}，状态 {change.get('status')}）", 'changes',
            change.get('change_id'))
    else:
        add("近窗口内无变更记录", 'changes', None)
    return facts, refs


def _fact(facts, needle):
    for fact in facts:
        if needle in fact['text']:
            return fact
    return None


def _amf_candidates(facts, context, alarm):
    """AMF 注册失败场景候选：全部判断由事实数据派生。"""
    candidates = []
    alarm_code = (alarm or {}).get('alarm_code') or ''
    metric_fact = _fact(facts, '监控快照')
    no_metric = _fact(facts, '无监控指标快照')
    repeat_fact = _fact(facts, '同码告警')
    companion = _fact(facts, '伴生告警')
    isolated = _fact(facts, '孤立事件')
    device_offline = _fact(facts, '非正常在线')
    change_fact = _fact(facts, '近期变更记录')
    no_change = _fact(facts, '无变更记录')

    # 候选 A：AMF 服务过载/容量不足
    support, against, missing = [], [], []
    if repeat_fact:
        support.append(f"{repeat_fact['id']} {repeat_fact['text']}")
    if metric_fact and re.search(r'成功|注册|cpu|负载|容量', (metric_fact['text'] or ''), re.I):
        support.append(f"{metric_fact['id']} {metric_fact['text']}")
    if isolated:
        against.append(f"{isolated['id']} {isolated['text']}——过载通常影响多设备/多用户，孤立事件倾向排除")
    if no_metric:
        missing.append('缺少注册成功率与负载时序（监控快照缺失），无法量化过载程度')
    candidates.append({
        'id': 'amf-capacity', 'title': 'AMF 服务过载 / 容量不足',
        'support': support, 'against': against, 'missing': missing,
        'verdict': 'likely' if len(support) >= 2 and not against else
                   ('unlikely' if against and len(support) < 2 else 'possible'),
        'next_checks': ['查看 AMF 实例最近 5 分钟 CPU/内存与注册成功率时序',
                        '对比相邻 AMF 节点的负载与注册成功率，确认是否为局部过载',
                        '核对 AMF 容量告警阈值与当前会话数']})

    # 候选 B：gNodeB 接入侧异常（下游证据）
    support, against, missing = [], [], []
    if device_offline:
        support.append(f"{device_offline['id']} {device_offline['text']}")
    if companion:
        support.append(f"{companion['id']} {companion['text']}——伴生告警提示接入侧问题")
    if isolated and not device_offline:
        against.append(f"{isolated['id']} {isolated['text']}，且设备状态正常——接入侧全面异常可能性低")
    if not companion and not isolated:
        missing.append('缺少同设备近期告警列表，无法判断是否存在伴生异常')
    candidates.append({
        'id': 'ran-access', 'title': 'gNodeB 接入侧异常（N2 链路 / 小区）',
        'support': support, 'against': against, 'missing': missing,
        'verdict': 'likely' if len(support) >= 2 else ('possible' if support else 'unlikely'),
        'next_checks': ['核查该 gNB 的 N2/S1 链路状态与建链日志',
                        '确认小区可用性与 Recent 活动（重建/掉话统计）',
                        '从网管侧 ping/ traceroute 设备管理地址']})

    # 候选 C：近期变更引入
    support, against, missing = [], [], []
    if change_fact:
        occurred = _parse_time((alarm or {}).get('occurred_at'))
        change_time = None
        for ch in _rows(context, 'changes'):
            change_time = _parse_time(ch.get('applied_at') or ch.get('created_at')) or change_time
        if occurred and change_time and change_time <= occurred:
            support.append(f"{change_fact['id']} {change_fact['text']}，且变更时间早于告警发生")
        elif occurred and change_time:
            against.append(f"{change_fact['id']} {change_fact['text']}，但变更发生在告警之后——因果方向不符")
        else:
            support.append(f"{change_fact['id']} {change_fact['text']}（时间关系待核对）")
        missing.append('缺少变更内容明细（改了什么、影响范围、回滚预案）')
    elif no_change:
        against.append(f"{no_change['id']} {no_change['text']}——无变更窗口支撑")
    candidates.append({
        'id': 'recent-change', 'title': '近期网络变更引入（配置/版本）',
        'support': support, 'against': against, 'missing': missing,
        'verdict': 'likely' if support and not against else ('unlikely' if against and not support else 'possible'),
        'next_checks': ['调出近 7 天变更单明细，核对涉及网元与参数',
                        '如变更涉及 AMF/gNB，评估回滚或灰度恢复',
                        '将本告警加入变更后观察名单']})

    # 候选 D：软件版本已知缺陷
    version = ((context.get('device') or {}).get('software_version') or '')
    support, against, missing = [], [], []
    if version:
        missing.append(f'缺少版本 {version} 的已知缺陷清单比对结果')
        candidates.append({
            'id': 'sw-defect', 'title': f'软件版本缺陷（当前 {version}）',
            'support': [], 'against': [], 'missing': missing, 'verdict': 'possible',
            'next_checks': [f'在版本库核对 {version} 是否存在注册类已知问题',
                            '如同类版本设备无此告警，重点排查本机差异配置']})
    return candidates


def _generic_candidates(facts, context, alarm):
    """未匹配专用场景：只给通用核查候选，明确说明规则库范围。"""
    device_offline = _fact(facts, '非正常在线')
    companion = _fact(facts, '伴生告警')
    candidates = [{
        'id': 'single-point', 'title': '单点设备异常（通用候选）',
        'support': [f"{device_offline['id']} {device_offline['text']}"] if device_offline else [],
        'against': [], 'missing': ['缺少该设备类型的专用规则与监控基线'],
        'verdict': 'possible' if device_offline else 'possible',
        'next_checks': ['核对设备运行状态与最近重启记录', '查看同类型其他设备是否出现同码告警']}]
    if companion:
        candidates[0]['support'].append(f"{companion['id']} {companion['text']}")
    return candidates


def build_diagnosis(context, query, sources):
    """诊断判断主入口：返回结构化 diagnosis（不自动定论根因）。"""
    alarm = context.get('alarm') or {}
    device = context.get('device') or {}
    facts, refs = extract_facts(context)
    alarm_code = (alarm.get('alarm_code') or '')
    query_text = (query or '') + ' ' + alarm_code
    is_amf = bool(re.search(r'AMF.{0,12}(注册|接入|REG)|ALM-AMF', query_text, re.I))
    candidates = _amf_candidates(facts, context, alarm) if is_amf else _generic_candidates(facts, context, alarm)
    # 排序：likely > possible > unlikely
    order = {'likely': 0, 'possible': 1, 'unlikely': 2}
    candidates.sort(key=lambda c: order.get(c['verdict'], 3))
    missing_info = []
    for c in candidates:
        missing_info.extend(c['missing'])
    recent = _rows(context, 'alarms')
    times = [t for t in (_parse_time(a.get('occurred_at')) for a in recent) if t]
    window = {'from': min(times).isoformat(), 'to': max(times).isoformat()} if times else None
    return {
        'scope': {'alarm_id': alarm.get('alarm_id'), 'alarm_code': alarm_code,
                  'device_id': device.get('device_id'), 'device_type': device.get('device_type'),
                  'occurred_at': _iso(alarm.get('occurred_at')), 'evidence_window': window,
                  'scenario': 'amf-registration' if is_amf else 'generic',
                  'scenario_note': '内置 AMF 注册失败规则' if is_amf
                                  else '未匹配专用规则，输出通用核查建议'},
        'facts': facts,
        'candidates': candidates,
        'missing_info': sorted(set(missing_info)),
        'next_steps': [s for c in candidates[:2] for s in c['next_checks']][:5],
        'recommendation': '以上候选均为待验证假设，不作为根因结论；请按“下一步检查”补充证据后人工确认。',
        'basis': 'rule-v1/amf-registration' if is_amf else 'rule-v1/generic',
        'fact_refs': refs,
    }


def render_diagnosis(diagnosis, sources):
    """把结构化判断渲染为中文 answer 文本（替代纯知识摘录拼接）。"""
    lines = []
    scope = diagnosis['scope']
    lines.append(f"【事件范围】{scope['alarm_code'] or '未知告警码'} @ {scope['device_id'] or '未知设备'}"
                 f"（类型 {scope['device_type'] or '—'}），发生于 {scope['occurred_at'] or '—'}。"
                 f"证据窗口：{scope['evidence_window']['from'][:16]} ~ {scope['evidence_window']['to'][:16]}"
                 if scope.get('evidence_window') else
                 f"【事件范围】{scope['alarm_code'] or '未知告警码'} @ {scope['device_id'] or '未知设备'}，"
                 f"发生于 {scope['occurred_at'] or '—'}")
    lines.append('')
    lines.append('【事实与异常】')
    for fact in diagnosis['facts']:
        lines.append(f"- {fact['id']} {fact['text']}")
    lines.append('')
    lines.append('【候选原因】（按倾向排序，需人工确认，不自动定论）')
    for c in diagnosis['candidates']:
        lines.append(f"○ {c['title']} —— {VERDICT_LABELS.get(c['verdict'], c['verdict'])}")
        for s in c['support']:
            lines.append(f"    支持：{s}")
        for a in c['against']:
            lines.append(f"    反对：{a}")
        for m in c['missing']:
            lines.append(f"    缺失：{m}")
        lines.append(f"    下一步：{'；'.join(c['next_checks'])}")
    if diagnosis['missing_info']:
        lines.append('')
        lines.append('【缺失信息】' + '；'.join(diagnosis['missing_info']))
    lines.append('')
    lines.append('【建议】' + diagnosis['recommendation'])
    if sources:
        lines.append('')
        lines.append('【知识依据】（可核对摘录）')
        for i, s in enumerate(sources, 1):
            lines.append(f'[{i}] {s["text"]}')
    return '\n'.join(lines)
