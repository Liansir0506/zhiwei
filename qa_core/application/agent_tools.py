"""Finite read whitelist, content-bound references and fresh run bindings."""
import hashlib
import json
import re
from dataclasses import dataclass
from datetime import date, datetime
from time import monotonic

import sqlalchemy as sa
from fastapi import HTTPException
from qa_core.application.domain_context_service import DomainContextService
from qa_core.schemas.agent import AlarmArgs, DeviceArgs, SearchArgs, ReadArgs
from qa_core.schemas.evidence import decode_reference
from qa_core.storage.domain_dao import DomainDao
from qa_core.storage.read_engine import read_engine, bounded_dsn
from qa_core.ingest.binding import version_binding
from qa_core.security.audit import audit_access_checked


def json_text(value):
    def encode(v):
        if isinstance(v, (date, datetime)):
            return v.isoformat()
        raise TypeError('unsupported tool value')
    return json.dumps(value, sort_keys=True, ensure_ascii=False, default=encode, separators=(',', ':'))


def digest(value):
    return hashlib.sha256(json_text(value).encode()).hexdigest()


def has_body(text):
    """A title/applicability label alone cannot support diagnosis."""
    lines = [line.strip() for line in text.splitlines()]
    return any(len(line) >= 12 and not line.startswith('#') and not re.match(
        r'^(?:适用|模拟告警|来源|审核|版本|实验编号|source_|status|approval)[：:]', line, re.I) for line in lines)


@dataclass(frozen=True)
class Binding:
    version: str
    fingerprint: str


class AgentStop(Exception):
    def __init__(self, reason, status='FAILED', question=''):
        self.reason, self.status, self.question = reason, status, question
        super().__init__(reason)


class AgentBackend:
    """Storage adapter has no publication, sync, command or LLM capability."""
    def __init__(self, settings, ctx, auth_check, control):
        self.settings, self.ctx, self.auth_check, self.control = settings, ctx, auth_check, control
        self.scope = ctx.scope
        self.service = None
        self.constraint = None

    def access(self):
        current = self.auth_check()
        if (current is None or current.user_id != self.ctx.user_id or current.source != self.ctx.source
                or current.scope != self.scope):
            raise AgentStop('access_changed', 'NO_EVIDENCE')

    def binding(self):
        engine = read_engine(self.settings.mysql_dsn)
        try:
            with engine.connect() as conn:
                rows = conn.execute(sa.text("SELECT version FROM kb_versions WHERE status='ACTIVE'")).scalars().all()
                if len(rows) != 1:
                    raise AgentStop('binding_changed', 'NO_EVIDENCE')
                version = rows[0]
                row = conn.execute(sa.text('SELECT binding_json,snapshot_json FROM kb_version_manifests WHERE version=:v'), {'v': version}).first()
                if not row:
                    raise AgentStop('binding_changed', 'NO_EVIDENCE')
                manifest = [json.loads(x) if isinstance(x, str) else x for x in row]
                if manifest[0] != version_binding(self.settings):
                    raise AgentStop('binding_changed', 'NO_EVIDENCE')
                ledger = sorted(conn.execute(sa.text('SELECT identity_hash FROM knowledge_source_withdrawals WHERE tenant_id=:t'), {'t': self.scope.tenant_id}).scalars())
            return Binding(version, digest([version, manifest, ledger, version_binding(self.settings)]))
        finally:
            engine.dispose()

    def audit(self, run_id, event, reason='', query=None):
        return audit_access_checked(self.settings.mysql_dsn, actor=self.ctx.user_id,
            tenant_id=self.scope.tenant_id, auth_source=self.ctx.source, action='agent_diagnose',
            resource=f'/agent/{run_id}/{event}', decision='allow' if not reason else 'deny',
            reason=reason, roles=self.scope.roles, scope=self.scope.to_dict(), query=query)

    def domain(self, kind, identity):
        dao = DomainDao(self.settings.mysql_dsn)
        try:
            return DomainContextService(dao).read(kind, identity, self.ctx, include_relations=True)
        except HTTPException as exc:
            if exc.status_code in {403, 404}:
                return None
            raise
        finally:
            dao.engine.dispose()

    def evidence(self):
        if self.service is None:
            from qa_core.application.evidence_service import create_evidence_service
            from qa_core.retrieval.searcher import Searcher
            owner = self
            class BoundedSearcher(Searcher):
                def search_docs(self, query_variants, roles, kb_version, **kwargs):
                    from qa_core.retrieval.workflow import is_workflow_query
                    if not is_workflow_query(query_variants[0]):
                        # Ordinary diagnosis cannot expose fragments of an unchecked SOP.
                        kwargs.update(doc_types=['fault_case', 'manual'], strict_doc_types=True)
                    result = super().search_docs(query_variants, roles, kb_version, **kwargs)
                    if not is_workflow_query(query_variants[0]):
                        result.docs = [doc for doc in result.docs if has_body(doc.text)]
                    return result

                def _call(self, method_name, *args, **kwargs):
                    owner.control.check()
                    # No transparent reconnect/retry for this bounded run.
                    kwargs['timeout'] = min(float(kwargs.get('timeout', 10)), owner.control.remaining(), 10)
                    result = getattr(self._mc(), method_name)(*args, **kwargs)
                    owner.control.check()
                    return result
            settings = self.settings.model_copy(update={
                'mysql_dsn': bounded_dsn(self.settings.mysql_dsn), 'workflow_evidence_enabled': True})
            self.service = create_evidence_service(settings, BoundedSearcher)
        return self.service

    def search(self, query, version, top_n):
        return self.evidence().search(query, version, self.scope, top_n, constraint=self.constraint)

    def source(self, token):
        return self.evidence().source(token, self.scope, constraint=self.constraint)

    def verify_record(self, version, references):
        if self.constraint is not None and self.constraint.exact:
            from qa_core.schemas.evidence import evidence_of
            try:
                docs = self.evidence().searcher.fetch_record(version, scope=self.scope, constraint=self.constraint)
            except ValueError:
                raise AgentStop('record_incomplete', 'NO_EVIDENCE') from None
            if {evidence_of(doc)['evidence_id'] for doc in docs} != references:
                raise AgentStop('evidence_changed', 'NO_EVIDENCE')


class ReadTools:
    ARGS = {'get_alarm_context': AlarmArgs, 'get_device_context': DeviceArgs,
            'search_evidence': SearchArgs, 'read_evidence': ReadArgs}

    def __init__(self, backend, binding, control, run_id, *, max_calls=10, max_bytes=131072, per_result=32768):
        self.backend, self.binding, self.control, self.run_id = backend, binding, control, run_id
        self.max_calls, self.max_bytes, self.per_result = max_calls, max_bytes, per_result
        self.calls, self.bytes = 0, 0
        self.counts = {'domain': 0, 'search': 0, 'read': 0}
        self.trace, self.seen, self.references, self.initial_reads = [], set(), set(), set()
        self.context_target, self.context_digest, self.constraint = None, None, None
        self.control.budget = (0, self.max_calls, 0)

    def bind_context(self, kind, identity, context, query):
        from qa_core.application.agent_relations import select_targets, RelationSelectionError
        self.context_target, self.context_digest = (kind, identity), digest(context)
        try:
            self.constraint = select_targets(context, self.binding.version, query, self.context_digest)
        except RelationSelectionError as exc:
            raise AgentStop(exc.reason, exc.status, exc.question) from None
        self.backend.constraint = self.constraint

    def guard(self, *, final=False):
        self.control.check()
        self.backend.access()
        if self.backend.binding() != self.binding:
            raise AgentStop('binding_changed', 'NO_EVIDENCE')
        if self.context_target is not None:
            current = self.backend.domain(*self.context_target)
            if current is None or digest(current) != self.context_digest:
                raise AgentStop('relation_changed', 'NO_EVIDENCE')
        if final and self.constraint is not None and self.constraint.exact:
            self.backend.verify_record(self.binding.version, self.references)
        self.control.check()

    def call(self, name, arguments, phase):
        if name not in self.ARGS or phase not in {'context', 'search', 'read', 'verify'}:
            raise AgentStop('tool_not_allowed')
        args = self.ARGS[name].model_validate(arguments)
        group = 'domain' if name.startswith('get_') else ('search' if name == 'search_evidence' else 'read')
        if ((group == 'domain' and phase != 'context') or (group == 'search' and phase != 'search')
                or (group == 'read' and phase not in {'read', 'verify'})):
            raise AgentStop('tool_not_allowed')
        key = (phase, name, digest(args.model_dump()))
        if key in self.seen:
            raise AgentStop('repeated_call')
        if self.calls >= self.max_calls or self.counts[group] >= {'domain': 2, 'search': 2, 'read': 6}[group]:
            raise AgentStop('call_budget')
        self.guard()
        if group == 'read':
            if (args.evidence_id not in self.references or decode_reference(args.evidence_id)[0] != self.binding.version
                    or (phase == 'verify' and args.evidence_id not in self.initial_reads)):
                raise AgentStop('invalid_reference', 'NO_EVIDENCE')
        self.seen.add(key)
        self.calls += 1
        self.counts[group] += 1
        self.control.budget = (self.calls, self.max_calls - self.calls, self.bytes)
        started = monotonic()
        event = {'call_id': f'{self.run_id}:{self.calls}', 'phase': phase, 'tool': name,
                 'args_sha256': key[2], 'status': 'error', 'calls_remaining': self.max_calls - self.calls}
        self.trace.append(event)
        self.control.tool_deadline = min(self.control.deadline, started + 30)
        try:
            if not self.backend.audit(self.run_id, f'call-{self.calls}', query=json_text(args.model_dump())):
                raise AgentStop('audit_unavailable')
            self.control.check()
            if group == 'domain':
                result = self.backend.domain('alarm' if name == 'get_alarm_context' else 'device',
                    args.alarm_id if name == 'get_alarm_context' else args.device_id)
            elif group == 'search':
                result = self.backend.search(args.query, self.binding.version, args.top_n)
            else:
                result = self.backend.source(args.evidence_id)
            self.control.check()
            encoded = json_text(result).encode()
            self.bytes += len(encoded)
            self.control.budget = (self.calls, self.max_calls - self.calls, self.bytes)
            if len(encoded) > self.per_result or self.bytes > self.max_bytes:
                raise AgentStop('size_budget')
            if group == 'search':
                evidence = result.get('evidence', [])
                if len(evidence) > 3:
                    raise AgentStop('evidence_budget', 'NO_EVIDENCE')
                for item in evidence:
                    if decode_reference(item['evidence_id'])[0] != self.binding.version or item.get('kb_version') != self.binding.version:
                        raise AgentStop('binding_changed', 'NO_EVIDENCE')
                    identity = decode_reference(item['evidence_id'])
                    if self.constraint is not None and (identity[3], identity[4]) not in self.constraint.pairs:
                        raise AgentStop('evidence_changed', 'NO_EVIDENCE')
                self.references.update(item['evidence_id'] for item in evidence)
            if group == 'read':
                if result is None or result.get('evidence_id') != args.evidence_id:
                    raise AgentStop('evidence_changed', 'NO_EVIDENCE')
                identity = decode_reference(args.evidence_id)
                if (hashlib.sha256(result['text'].encode()).hexdigest() != identity[5]
                        or [result.get(k) for k in ('kb_version', 'chunk_id', 'document_id', 'source_revision')] != [identity[0], identity[2], identity[3], identity[4]]):
                    raise AgentStop('evidence_changed', 'NO_EVIDENCE')
                self.initial_reads.add(args.evidence_id)
            event['status'] = 'ok'
            return result
        except AgentStop as exc:
            event['status'] = exc.reason
            raise
        except Exception:
            raise AgentStop('tool_error') from None
        finally:
            event['elapsed_ms'] = round((monotonic() - started) * 1000, 2)
            self.control.tool_deadline = self.control.deadline
