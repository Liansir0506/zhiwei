# -*- coding: utf-8 -*-
"""主入口可用的规则诊断（C2）。

与 `/agent/diagnose` 的区别：
- 不要求 `agent_enabled`、不访问知识库/向量检索、不调用任何模型；
- 只读取授权范围内的业务快照（告警、设备、指标、工单、变更），复用
  `diagnosis_judgment` 的规则判断层输出"事实 → 候选 → 缺失 → 下一步"；
- 仍然强制租户/角色/授权范围，结果落审计与 `diagnosis_runs`，可重开。

这样 8010 保持"问答配置、不启用生成"时，主入口依然能完成
告警 → 诊断 → 工单 → 恢复验证的演示主线。
"""
from time import monotonic
from types import SimpleNamespace
from uuid import uuid4

from qa_core.application.agent_diagnose import _save_run, observations
from qa_core.application.diagnosis_judgment import build_diagnosis, render_diagnosis
from qa_core.application.domain_context_service import DomainContextService
from qa_core.storage.domain_dao import DomainDao


def rule_diagnose(settings, ctx, *, alarm_id=None, device_id=None, query=''):
    """执行一次规则诊断；实体不存在或不可读时抛 LookupError。"""
    started = monotonic()
    kind = 'alarm' if alarm_id else 'device'
    identity = alarm_id or device_id
    dao = DomainDao(settings.mysql_dsn)
    try:
        context = DomainContextService(dao).read(kind, identity, ctx, include_relations=True)
    finally:
        dao.engine.dispose()
    if not context:
        raise LookupError('实体不存在或不在当前授权范围内')

    diagnosis = build_diagnosis(context, query or '', [])
    answer = render_diagnosis(diagnosis, [])
    run_id = uuid4().hex
    result = {
        'run_id': run_id,
        'status': 'ANSWERED',
        'stop_reason': 'rule_context_ready',
        'mode': 'rule',
        'kb_version': None,
        'answer': answer,
        'diagnosis': diagnosis,
        'observations': observations(context, 'rule-' + run_id[:12]),
        'sources': [],
        'trace': [
            {'tool': 'get_alarm_context' if alarm_id else 'get_device_context',
             'phase': 'context', 'status': 'ok',
             'elapsed_ms': round((monotonic() - started) * 1000, 2), 'calls_remaining': 0},
            {'tool': 'build_diagnosis', 'phase': 'verify', 'status': 'ok',
             'elapsed_ms': round((monotonic() - started) * 1000, 2), 'calls_remaining': 0},
        ],
        'limitations': [
            '规则诊断仅基于当前授权范围内的业务快照（告警/设备/指标/工单/变更），不检索知识库，不调用模型。',
            '候选原因均为待验证假设，不能替代现场核实与人工确认。',
        ],
        'budget': {'calls_used': 2, 'calls_remaining': 8,
                   'elapsed_ms': round((monotonic() - started) * 1000, 2)},
    }
    _save_run(settings, ctx, result, SimpleNamespace(
        alarm_id=alarm_id, device_id=device_id, query=query or '规则诊断'))
    return result
