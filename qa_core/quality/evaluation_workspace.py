"""Version-attributed reports and bounded, zero-model development regression.

Historical reports and publication snapshots are read only. New plans/jobs/reports
live in an independent workspace. No filename implies version or independence.
"""
from __future__ import annotations

import hashlib
import json
import math
import re
import threading
import time
import traceback
import uuid
from datetime import datetime, timezone
from pathlib import Path

PROJECT = Path(__file__).resolve().parents[2]
WORKSPACE = PROJECT / '.runtime/evaluations'
INSTANCE = uuid.uuid4().hex
LOCK = threading.Lock()
IO_LOCK = threading.RLock()
NOTE = '未验证独立样本及冻结评测证明'
FIELDS = ('route_ok', 'hit_ok', 'reject_ok', 'status_ok', 'body_ok', 'degraded')


def digest(value):
    return hashlib.sha256(json.dumps(value, ensure_ascii=False, sort_keys=True,
                                    separators=(',', ':')).encode()).hexdigest()


def now():
    return datetime.now(timezone.utc).isoformat()


def write(path, data):
    # Refuse links/junctions into a sealed candidate or another workspace.
    root = WORKSPACE.absolute()
    if root.resolve() != root or not path.resolve().is_relative_to(root):
        raise ValueError('evaluation workspace must be independent')
    with IO_LOCK:
        path.parent.mkdir(parents=True, exist_ok=True)
        temp = path.with_name(path.name + '.' + uuid.uuid4().hex + '.tmp')
        temp.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding='utf-8')
        # Windows readers/scanners can briefly deny delete-sharing. Keep the old
        # valid record until atomic replace succeeds; never truncate it in place.
        for attempt in range(10):
            try:
                temp.replace(path)
                return
            except PermissionError:
                if attempt == 9:
                    raise
                time.sleep(.05)


def read_record(kind, ident):
    if not re.fullmatch(r'[a-f0-9]{32}', ident):
        raise KeyError('record not found')
    path = WORKSPACE / kind / (ident + '.json')
    if not path.resolve().is_relative_to(WORKSPACE.absolute()) or path.is_symlink():
        raise KeyError('record not found')
    try:
        with IO_LOCK:
            return json.loads(path.read_text(encoding='utf-8'))
    except FileNotFoundError:
        raise KeyError('record not found') from None


def normalize(data, path, raw, current):
    if not isinstance(data, dict):
        raise ValueError('report must be an object')
    run = data.get('run') if isinstance(data.get('run'), dict) else {}
    declared = data.get('kb_version')
    nested = run.get('kb_version') or (data.get('snapshot') or {}).get('kb_version')
    conflict = bool(declared and nested and declared != nested)
    version = declared if isinstance(declared, str) and declared and not conflict else None
    rows = data.get('results', [])
    if not isinstance(rows, list) or any(not isinstance(row, dict) for row in rows):
        raise ValueError('invalid results')
    checks = {}
    for field in FIELDS:
        values = [row[field] for row in rows if type(row.get(field)) is bool]
        checks[field] = {'passed': sum(values), 'evaluated': len(values),
                         'ratio': sum(values) / len(values) if values else None}
    durations = sorted(row['elapsed_ms'] for row in rows
        if type(row.get('elapsed_ms')) in (int, float)
        and math.isfinite(row['elapsed_ms']) and row['elapsed_ms'] >= 0)
    categories = {}
    for row in rows:
        category = str(row.get('category') or '未分类')
        c = categories.setdefault(category, {'total': 0, 'pass': 0, 'evaluated': 0})
        c['total'] += 1
        values = [row.get(field) for field in ('route_ok', 'hit_ok', 'reject_ok', 'status_ok', 'body_ok')]
        # Unknown route, malformed expectations cannot silently become a pass.
        if type(row.get('route_ok')) is bool and all(v is None or type(v) is bool for v in values):
            c['evaluated'] += 1
            c['pass'] += all(v is not False for v in values)
    gate = []
    if isinstance(data.get('checks'), list):
        for check in data['checks']:
            if isinstance(check, dict):
                gate.append({k: check[k] for k in ('check', 'passed', 'sample', 'hits', 'hit_rate',
                    'sop_count', 'sop_sources', 'chunks', 'filled_rate', 'count', 'scanned', 'sampled') if k in check})
    kind = 'publication_gate' if gate else 'development_regression'
    source = path.relative_to(PROJECT).as_posix() if path.is_relative_to(PROJECT) else 'configured-reports/' + path.name
    sha = hashlib.sha256(raw).hexdigest()
    ident = digest({'source': source, 'sha256': sha})[:32]
    modified = datetime.fromtimestamp(path.stat().st_mtime, timezone.utc).isoformat()
    safe_rows = [{k: row[k] for k in ('id', 'query', 'category', 'route', 'status', *FIELDS,
                         'elapsed_ms', 'error_kind') if k in row} for row in rows]
    return {'id': ident, 'file': path.name, 'source': source, 'sha256': sha,
        'kb_version': version, 'declared_version': declared, 'attribution': 'conflict' if conflict else
            ('current' if version == current else 'historical' if version else 'unattributed'),
        'version_basis': '报告显式声明；历史执行绑定不追认' if version else '版本缺失或冲突，未从文件名推断',
        'kind': kind, 'created_at': run.get('created_at'), 'modified_at': modified,
        'run_id': data.get('run_id') or run.get('run_id'),
        'dataset': str(data.get('dataset') or '').replace('\\', '/').rsplit('/', 1)[-1] or None,
        'dataset_sha256': run.get('dataset_sha256'), 'split': run.get('split') or '未记录',
        'llm_mode': run.get('llm_mode') or '未记录', 'scope': run.get('scope') or
            ('入库结构门禁，不能证明SOP内容支持性或问答语义正确' if gate else '历史开发诊断，覆盖范围未完整记录'),
        'body_check': run.get('body_check') or '未记录正文判据，不能追认与新报告相同',
        'purpose': '结构门禁记录' if gate else '开发回归记录，非生产SLA或独立质量验收',
        'independent_evaluation': NOTE, 'independent_verified': False,
        'sample_count': len(rows), 'checks': checks, 'by_category': categories,
        'latency': {'sample_count': len(durations), 'p95_ms': durations[math.ceil(len(durations)*.95)-1] if durations else None},
        'gate_checks': gate, 'results': safe_rows}


def reports(settings, current):
    roots = [Path(settings.reports_dir), PROJECT / 'reports', WORKSPACE / 'reports']
    found, warnings, seen = [], [], set()
    for directory in roots:
        for pattern in ('eval_*.json', 'quality_*.json'):
            for path in sorted(directory.glob(pattern)):
                resolved = path.resolve()
                if resolved in seen:
                    continue
                seen.add(resolved)
                if path.is_symlink() or not resolved.is_relative_to(directory.resolve()):
                    warnings.append({'file': path.name, 'reason': '链接越界，未读取'}); continue
                try:
                    if path.stat().st_size > 16 * 1024 * 1024:
                        raise ValueError('report too large')
                    raw = path.read_bytes()
                    found.append(normalize(json.loads(raw.decode('utf-8-sig')), resolved, raw, current))
                except (ValueError, OSError, TypeError, AttributeError):
                    warnings.append({'file': path.name, 'reason': '报告损坏或格式不兼容，未参与统计'})
    found.sort(key=lambda row: (row['created_at'] or row['modified_at'], row['id']), reverse=True)
    return found, warnings


def public_plan(plan):
    return {k: plan[k] for k in ('id', 'kb_version', 'created_at', 'expires_at', 'dataset',
        'dataset_sha256', 'case_count', 'scope', 'mode', 'independent_verified', 'binding_sha256')}


def binding(settings):
    from qa_core.ingest.binding import version_binding
    paths = ['qa_core/quality/evaluation_workspace.py', 'qa_core/api/route_admin.py',
             'qa_core/application/qa_usecase.py', 'qa_core/retrieval/supported_searcher.py',
             'qa_core/retrieval/supported_evidence_v2.py']
    return digest({'read_binding': version_binding(settings), 'code': {
        name: hashlib.sha256((PROJECT / name).read_bytes()).hexdigest() for name in paths}})


def cases_for(usecase, version):
    from qa_core.security.policy import AccessScope
    scope = AccessScope.build(roles=['ops'])
    docs = usecase.searcher.recommended_faqs(['ops'], version, scope=scope)
    if not docs or len(docs) > 40:
        raise ValueError('当前可读FAQ不在1至40条受控范围，需单独设计评测')
    cases = [{'id': str(doc.id), 'query': doc.question, 'answer': doc.answer, 'source': doc.source,
              'source_id': doc.source_id} for doc in docs]
    if any(not c['query'] or len(c['query']) > 500 or not c['answer'] for c in cases):
        raise ValueError('FAQ回归输入不兼容')
    if any(not c['source'] or not c['source_id'] for c in cases) or len({c['id'] for c in cases}) != len(cases):
        raise ValueError('FAQ来源身份或编号不完整')
    return sorted(cases, key=lambda row: row['id'])


def prepare(settings, usecase, version, ctx):
    cases = cases_for(usecase, version)
    plan = {'id': uuid.uuid4().hex, 'kb_version': version, 'created_at': now(),
        'expires_at': time.time() + 900, 'dataset': 'current-readable-faq-self-regression',
        'dataset_sha256': digest(cases), 'case_count': len(cases), 'mode': 'no_llm',
        'scope': 'default租户ops可读FAQ原句；真值来自同库原文，仅检查路由、引用、原文直出。没有文档生成、改写、拒答及独立质量覆盖。',
        'independent_verified': False, 'binding_sha256': binding(settings),
        'actor': ctx.user_id, 'actor_scope': ctx.scope.fingerprint, 'cases': cases}
    write(WORKSPACE / 'plans' / (plan['id'] + '.json'), plan)
    return public_plan(plan)


class ForbiddenModel:
    def __getattr__(self, name):
        raise RuntimeError('evaluation model access forbidden')


def faq_body_intact(actual, expected, source):
    """Compare the whole FAQ body after restoring documented footnote labels.

    Source footnotes [n] are displayed as 〔原文注n〕 to distinguish them from
    outer knowledge citations. No whitespace, paragraphs or other text is dropped.
    """
    prefix = '【结论】依据标准排查 FAQ：\n'
    suffix = '\n【引用】[1] ' + source
    if not actual.startswith(prefix) or not actual.endswith(suffix):
        return False
    body = actual[len(prefix):-len(suffix)]
    restored = re.sub(r'〔原文注(\d{1,2})〕', r'[\1]', body)
    return restored == expected


def job_state(ident):
    state = read_record('jobs', ident)
    if state['state'] in ('queued', 'running') and state.get('instance') != INSTANCE:
        state = dict(state, state='interrupted', error='应用已重启，旧任务不会自动重跑；请重新准备计划。')
    return {k: value for k, value in state.items() if k != 'instance'}


def recent_jobs():
    states = []
    for path in sorted((WORKSPACE / 'jobs').glob('*.json'), key=lambda p: p.stat().st_mtime, reverse=True)[:20]:
        try:
            states.append(job_state(path.stem))
        except (KeyError, ValueError, OSError):
            continue
    return states


def execute(settings, usecase, version, ctx, ident):
    if not LOCK.acquire(blocking=False):
        raise ValueError('已有评测执行中，请等待完成')
    try:
        plan = read_record('plans', ident)
        if (plan.get('mode') != 'no_llm' or plan.get('independent_verified') is not False
            or not 1 <= plan.get('case_count', 0) <= 40 or len(plan.get('cases', [])) != plan['case_count']
            or digest(plan['cases']) != plan['dataset_sha256']):
            raise ValueError('计划内容摘要或零模型范围已变化，请重新准备')
        if plan['actor'] != ctx.user_id or plan['actor_scope'] != ctx.scope.fingerprint:
            raise ValueError('计划只能由相同账号及访问范围执行')
        if plan['kb_version'] != version or plan['expires_at'] < time.time() or plan['binding_sha256'] != binding(settings):
            raise ValueError('版本、代码、配置或计划有效期已变化，请重新准备')
        if digest(cases_for(usecase, version)) != plan['dataset_sha256']:
            raise ValueError('可读FAQ或撤回范围已变化，请重新准备')
        if (WORKSPACE / 'jobs' / (ident + '.json')).exists():
            raise ValueError('计划已执行，重新运行需要新计划')
        state = {'id': ident, 'state': 'queued', 'instance': INSTANCE, 'kb_version': version,
                 'case_count': plan['case_count'], 'completed': 0, 'created_at': now(),
                 'requested_by': ctx.user_id, 'paid_calls': 0, 'report_id': None, 'error': None}
        write(WORKSPACE / 'jobs' / (ident + '.json'), state)
        thread = threading.Thread(target=_run, args=(settings, usecase, plan, state), daemon=True)
        thread.start()
        return job_state(ident)
    except Exception:
        LOCK.release()
        raise


def _run(settings, live, plan, state):
    from qa_core.application.qa_usecase import QaUseCase
    from qa_core.security.policy import AccessScope
    jobpath = WORKSPACE / 'jobs' / (plan['id'] + '.json')
    rows = []
    deadline = time.monotonic() + 300
    try:
        uc = QaUseCase(cfg=live.cfg, searcher=live.searcher, llm=ForbiddenModel())
        uc.settings = settings
        state.update(state='running', started_at=now()); write(jobpath, state)
        for case in plan['cases']:
            if time.monotonic() > deadline:
                raise TimeoutError('evaluation deadline exceeded')
            if uc._active_kb_version() != plan['kb_version'] or binding(settings) != plan['binding_sha256']:
                raise ValueError('active version or binding changed during run')
            t0 = time.perf_counter()
            result = uc.ask(case['query'], user_id='controlled-evaluation', roles=['ops'],
                scope=AccessScope.build(roles=['ops']), kb_version=plan['kb_version'],
                session_id='evaluation-' + plan['id'] + '-' + case['id'], use_llm=False, persist=False)
            diag = result.get('diagnostics') or {}
            if diag.get('answer_mode') != 'citation' or diag.get('kb_version') != plan['kb_version']:
                raise ValueError('evaluation result binding differs')
            rows.append({'id': case['id'], 'query': case['query'], 'category': 'faq_original',
                'route': result.get('route'), 'status': result.get('status'),
                'route_ok': result.get('route') == 'faq_exact', 'status_ok': result.get('status') == 'ANSWERED',
                'hit_ok': len(result.get('sources') or []) == 1 and any(source.get('source_id') == case['source_id'] and source.get('source') == case['source']
                              for source in result.get('sources') or []),
                'body_ok': faq_body_intact(result.get('answer') or '', case['answer'], case['source']), 'reject_ok': None,
                'degraded': diag.get('degraded'), 'elapsed_ms': round((time.perf_counter()-t0)*1000, 1)})
            state['completed'] = len(rows); write(jobpath, state)
        if (uc._active_kb_version() != plan['kb_version'] or binding(settings) != plan['binding_sha256']
            or digest(cases_for(live, plan['kb_version'])) != plan['dataset_sha256']):
            raise ValueError('FAQ read scope changed during run')
        report = {'kb_version': plan['kb_version'], 'run_id': plan['id'], 'dataset': plan['dataset'],
            'run': {'created_at': now(), 'kb_version': plan['kb_version'], 'split': 'development_self_regression',
                    'dataset_sha256': plan['dataset_sha256'], 'scope': plan['scope'], 'llm_mode': 'no_llm',
                    'binding_sha256': plan['binding_sha256'], 'session_policy': 'isolated_no_persistence',
                    'body_check': '完整FAQ正文精确比较；仅将〔原文注n〕还原为[n]，不忽略空白、段落或其他文字',
                    'frozen_independence': 'not_claimed', 'generation_quality_assessed': False}, 'results': rows}
        path = WORKSPACE / 'reports' / ('eval_' + plan['id'] + '.json')
        write(path, report)
        normalized = normalize(report, path, path.read_bytes(), plan['kb_version'])
        state.update(state='completed', report_id=normalized['id'], finished_at=now())
    except Exception as error:
        positions = [{'file': Path(frame.filename).name, 'line': frame.lineno, 'function': frame.name}
                     for frame in traceback.extract_tb(error.__traceback__)[-5:]]
        state.update(state='failed', error='执行失败（' + type(error).__name__ + '）；请检查依赖/版本绑定并重新准备计划。',
                     error_positions=positions, finished_at=now())
    finally:
        try:
            if state['state'] == 'failed':
                write(WORKSPACE / 'failures' / (plan['id'] + '.json'), {
                    'id': plan['id'], 'kb_version': plan['kb_version'], 'completed_results': rows,
                    'binding_sha256': plan['binding_sha256'], 'error': state['error']})
            write(jobpath, state)
        finally:
            LOCK.release()
