"""Deterministic read-only diagnosis. Tool results never become instructions."""
import re
import threading
from datetime import datetime, timezone
from time import monotonic
from types import SimpleNamespace
from uuid import uuid4

from qa_core.application.agent_tools import AgentBackend, AgentStop, ReadTools, json_text, has_body
from qa_core.config.scenario import get_scenario
from qa_core.intent.rules import find_injection, find_out_of_scope, find_alarm_code
from qa_core.application.qa_usecase import evidence_covers
from qa_core.retrieval.workflow import lab_source_applicable


class RunControl:
    def __init__(self, timeout=90):
        self.started = monotonic()
        self.deadline = self.started + timeout
        self.tool_deadline = self.deadline
        self.cancelled = threading.Event()
        self.reason = 'cancelled'
        self.run_id = uuid4().hex
        self.kb_version = None
        self.budget = (0, 10, 0)

    def cancel(self, reason='cancelled'):
        self.reason = reason
        self.cancelled.set()

    def remaining(self):
        return min(self.deadline, self.tool_deadline) - monotonic()

    def check(self):
        if self.cancelled.is_set():
            raise AgentStop(self.reason)
        if self.remaining() <= 0:
            raise AgentStop('timeout')


def observations(context, call_id):
    result = []
    now = datetime.now(timezone.utc).isoformat()
    specs = [('device', 'device_id', None), ('alarm', 'alarm_id', 'occurred_at'),
             ('alarms', 'alarm_id', 'occurred_at'), ('monitoring', 'observation_id', 'sampled_at'),
             ('work_orders', 'ticket_id', 'opened_at'), ('changes', 'change_id', None)]
    for kind, id_field, time_field in specs:
        rows = context.get(kind, [])
        if isinstance(rows, dict):
            rows = [rows]
        for row in rows or []:
            result.append({'observation_ref': {'call_id': call_id, 'entity_type': kind,
                'entity_id': row[id_field]}, 'fields': row, 'source_system': row.get('source_system'),
                'recorded_at': row.get(time_field) if time_field else None,
                'retrieved_at': now, 'freshness': 'snapshot' if time_field else 'unknown',
                'synthetic': True})
    return result


def _save_run(settings, ctx, result, body):
    """保存成功诊断为可重开任务（T6）；任何存储问题都不影响诊断结果。"""
    try:
        import sqlalchemy as sa
        summary = (result.get('answer') or '').strip().replace('\n', ' ')[:800]
        mode = 'rule' if result.get('mode') == 'rule' else 'agent'
        engine = sa.create_engine(settings.mysql_dsn, pool_pre_ping=True)
        with engine.begin() as conn:
            conn.execute(sa.text(
                "INSERT INTO diagnosis_runs (tenant_id, run_id, alarm_id, device_id, query, status, mode, "
                "kb_version, summary, created_by) VALUES (:t, :r, :a, :d, :q, 'ANSWERED', :m, :v, :s, :u) "
                "ON DUPLICATE KEY UPDATE summary=VALUES(summary), mode=VALUES(mode)"),
                {"t": ctx.tenant_id, "r": result['run_id'], "a": body.alarm_id, "d": body.device_id,
                 "q": body.query[:500], "m": mode, "v": result.get('kb_version'), "s": summary,
                 "u": ctx.user_id})
        engine.dispose()
    except Exception:
        pass


def diagnose(body, settings, ctx, auth_check, control=None, *, backend=None, tool_limits=None):
    control = control or RunControl()
    run_id, started = control.run_id, monotonic()
    backend = backend or AgentBackend(settings, ctx, auth_check, control)
    result = {'run_id': run_id, 'status': 'FAILED', 'stop_reason': 'tool_error',
              'mode': 'domain' if body.alarm_id or body.device_id else 'knowledge',
              'kb_version': None, 'answer': '', 'observations': [], 'sources': [],
              'limitations': [], 'trace': [], 'budget': {'calls_used': 0, 'calls_remaining': 10}}
    result['retrieval_scope'] = None
    tools = None
    try:
        control.check()
        backend.access()
        if not backend.audit(run_id, 'start', query=body.query):
            raise AgentStop('audit_unavailable')
        cfg = get_scenario(settings.scenario)
        if find_injection(body.query, cfg) or re.search(r'忽略(?:之前|以前|以上|原有|所有)(?:的)?(?:指令|规则|限制)', body.query):
            raise AgentStop('prompt_injection', 'OUT_OF_SCOPE')
        if find_out_of_scope(body.query, cfg):
            raise AgentStop('unsafe_request', 'OUT_OF_SCOPE')
        if not body.alarm_id and not body.device_id:
            alarm_code = find_alarm_code(body.query, cfg)
            if (re.search(r'现场|这台|当前设备|这条告警|当前告警|告警事件|关联(?:告警|工单)|这[个台].*故障|^(?:那|那么|它|该设备)', body.query)
                    or (alarm_code and body.query.upper() == alarm_code.upper())):
                raise AgentStop('missing_entity', 'NEEDS_CLARIFICATION', '请提供明确的alarm_id或device_id；告警码不是告警事件ID。')
        binding = backend.binding()
        result['kb_version'] = binding.version
        control.kb_version = binding.version
        tools = ReadTools(backend, binding, control, run_id, **(tool_limits or {}))
        context, query = None, body.query
        if body.alarm_id:
            context = tools.call('get_alarm_context', {'alarm_id': body.alarm_id}, 'context')
        elif body.device_id:
            context = tools.call('get_device_context', {'device_id': body.device_id}, 'context')
        if result['mode'] == 'domain':
            if context is None:
                raise AgentStop('entity_unavailable', 'NO_EVIDENCE')
            if body.device_id and body.alarm_id and (context.get('device') or {}).get('device_id') != body.device_id:
                raise AgentStop('entity_mismatch', 'NEEDS_CLARIFICATION', '请核对alarm_id与device_id是否属于同一事件。')
            result['observations'] = observations(context, tools.trace[-1]['call_id'])
            result['limitations'].append('领域内容为隔离测试库快照；未验证实时状态或根因。')
            if context.get('truncated'):
                result['limitations'].append('相关记录只展示有权限的最近20条。')
            tools.bind_context('alarm' if body.alarm_id else 'device', body.alarm_id or body.device_id, context, body.query)
            extra = []
            for value in [(context.get('alarm') or {}).get('alarm_code'), (context.get('device') or {}).get('device_type'), (context.get('device') or {}).get('software_version')]:
                if isinstance(value, str) and re.fullmatch(r'[A-Za-z0-9_.:-]{1,64}', value) and value.casefold() not in query.casefold():
                    extra.append(value)
            expanded = query + (' ' + ' '.join(extra) if extra else '')
            if len(expanded) <= 500:
                query = expanded
        found = tools.call('search_evidence', {'query': query, 'top_n': 3}, 'search')
        if found.get('status') == 'NEEDS_CLARIFICATION':
            raise AgentStop(found.get('reason') or 'missing_condition', 'NEEDS_CLARIFICATION', found.get('question', '请补充诊断条件。'))
        if found.get('status') == 'NO_EVIDENCE':
            raise AgentStop(found.get('reason') or 'insufficient_evidence', 'NO_EVIDENCE')
        references = found.get('evidence', [])
        if not references:
            raise AgentStop('insufficient_evidence', 'NO_EVIDENCE')
        sources = []
        for item in references:
            source = tools.call('read_evidence', {'evidence_id': item['evidence_id']}, 'read')
            if not has_body(source['text']):
                raise AgentStop('insufficient_evidence', 'NO_EVIDENCE')
            if not lab_source_applicable(query, source['text']):
                raise AgentStop('inapplicable_evidence', 'NO_EVIDENCE')
            sources.append(source)
        covered, ratio = ((True, 1.0) if tools.constraint is not None and tools.constraint.exact else
                         evidence_covers(query, [SimpleNamespace(text=s['text']) for s in sources], settings.evidence_min_coverage))
        if not covered:
            raise AgentStop('insufficient_evidence', 'NO_EVIDENCE')
        # Reread every cited chunk immediately before final publication.
        sources = [tools.call('read_evidence', {'evidence_id': s['evidence_id']}, 'verify') for s in sources]
        tools.guard()
        result['status'], result['stop_reason'] = 'ANSWERED', 'evidence_ready'
        result['sources'] = sources
        if tools.constraint is not None:
            from qa_core.application.agent_relations import explanation
            result['retrieval_scope'] = explanation(tools.constraint)
        if result['mode'] == 'domain' and context is not None:
            # T2 诊断判断层：事实→候选原因→支持/反对/缺失→下一步（不自动定论根因）
            from qa_core.application.diagnosis_judgment import build_diagnosis, render_diagnosis
            result['diagnosis'] = build_diagnosis(context, body.query, sources)
            result['answer'] = render_diagnosis(result['diagnosis'], sources)
            _save_run(settings, ctx, result, body)  # T6：可重开任务记录（失败不阻断诊断）
        else:
            result['answer'] = '以下为可核对的知识摘录，尚不足以确认现场根因：\n\n' + '\n\n'.join(
                f'[{i}] {s["text"]}' for i, s in enumerate(sources, 1))
        result['limitations'].append('仅提供证据摘录；历史或工具文本不作为操作指令，未执行设备或业务动作。')
    except AgentStop as exc:
        result.update(status=exc.status, stop_reason=exc.reason, answer=exc.question)
        result['observations'], result['sources'], result['diagnosis'] = [], [], None
    except Exception:
        result.update(status='FAILED', stop_reason='tool_error', answer='', observations=[], sources=[])
    finally:
        if tools:
            result['trace'] = tools.trace
            result['budget'] = {'calls_used': tools.calls, 'calls_remaining': tools.max_calls - tools.calls,
                                'result_bytes': tools.bytes}
        result['budget']['elapsed_ms'] = round((monotonic() - started) * 1000, 2)
        try:
            if not backend.audit(run_id, 'end', reason=result['stop_reason'] if result['status'] != 'ANSWERED' else ''):
                result.update(status='FAILED', stop_reason='audit_unavailable', answer='', observations=[], sources=[])
        except Exception:
            result.update(status='FAILED', stop_reason='audit_unavailable', answer='', observations=[], sources=[])
    # Final authorization/binding check after termination audit; no subsequent tool calls.
    if result['status'] == 'ANSWERED':
        try:
            tools.guard(final=True)
        except AgentStop as exc:
            result.update(status=exc.status, stop_reason=exc.reason, answer='', observations=[], sources=[])
        except Exception:
            result.update(status='FAILED', stop_reason='tool_error', answer='', observations=[], sources=[])
        if result['status'] != 'ANSWERED':
            try:
                if not backend.audit(run_id, 'final-deny', reason=result['stop_reason']):
                    result.update(status='FAILED', stop_reason='audit_unavailable')
            except Exception:
                result.update(status='FAILED', stop_reason='audit_unavailable')
    if tools and len(json_text(result).encode()) > tools.max_bytes:
        result.update(status='FAILED', stop_reason='size_budget', answer='', observations=[], sources=[])
    if result['status'] != 'ANSWERED':
        result['retrieval_scope'] = None
    return result
