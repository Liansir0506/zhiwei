"""Independent writable candidates; only exact admitted source copies may queue.

New uploads remain quarantined. This module does not grant admission, publish,
delete sources or change the approved runtime profile.
"""
from __future__ import annotations
import hashlib
import json
import re
import threading
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path
import sqlalchemy as sa
from fastapi import HTTPException
from qa_core.ingest.binding import version_binding, execution_binding, digest
from qa_core.ingest.write_guard import candidate_write

PROJECT = Path(__file__).resolve().parents[2]
ROOT = PROJECT / '.runtime/ingest-workspace'
PROTOCOL = 'admitted-upload-workspace-v1'
IO_LOCK = threading.RLock()
POLICY = 'strict-location-supported-paragraph-v1'


def now():
    return datetime.now(timezone.utc).isoformat()


def safe_path(*parts):
    target = ROOT.joinpath(*parts).absolute()
    if ROOT.resolve() != ROOT.absolute() or not target.resolve().is_relative_to(ROOT.absolute()):
        raise ValueError('工作区路径越界或包含链接')
    return target


def atomic(path, value):
    if not path.resolve().is_relative_to(ROOT.absolute()):
        raise ValueError('工作区路径越界')
    safe_path()
    with IO_LOCK:
        path.parent.mkdir(parents=True, exist_ok=True)
        temporary = path.with_name(path.name + '.' + uuid.uuid4().hex + '.tmp')
        temporary.write_text(json.dumps(value, ensure_ascii=False, indent=2), encoding='utf-8')
        for attempt in range(10):
            try:
                temporary.replace(path); return
            except PermissionError:
                if attempt == 9: raise
                time.sleep(.05)


def read(path):
    if not path.resolve().is_relative_to(ROOT.absolute()):
        raise ValueError('工作区路径越界')
    with IO_LOCK:
        return json.loads(path.read_text(encoding='utf-8'))


def profile_binding(settings):
    code = ['scripts/rebuild_kb.py', 'qa_core/ingest/workspace.py',
        'scripts/workspace_ingest_worker.py', 'qa_core/ingest/jobs.py',
        'qa_core/ingest/source_identity.py', 'qa_core/ingest/write_guard.py',
        'qa_core/storage/version_manifest.py', 'qa_core/indexing/chunkers/supported_rebuild.py',
        'qa_core/indexing/chunkers/paragraph_budget.py', 'qa_core/indexing/parsers/parsers.py']
    source = Path(settings.data_dir)
    return {'version_binding': version_binding(settings), 'policy': POLICY,
        'source_seal_sha256': hashlib.sha256((source / 'artifact-hashes.json').read_bytes()).hexdigest(),
        'source_metadata_sha256': hashlib.sha256((source / 'raw/meta.json').read_bytes()).hexdigest(),
        'source_map_sha256': hashlib.sha256((source / 'source-map.json').read_bytes()).hexdigest(),
        'code': {name: hashlib.sha256((PROJECT / name).read_bytes()).hexdigest() for name in code}}


def get_candidate(version):
    if not re.fullmatch(r'local-work-[a-f0-9]{12}', version):
        raise HTTPException(409, '请选择本工作区创建的可写候选；历史或封存候选不可上传')
    try:
        data = read(safe_path('candidates', version, 'candidate.json'))
    except FileNotFoundError:
        raise HTTPException(404, '工作区候选不存在') from None
    if data.get('version') != version or data.get('protocol') != PROTOCOL:
        raise ValueError('候选身份不匹配')
    return data


def verify_candidate(settings, candidate, *, writable=False):
    if candidate.get('binding') != profile_binding(settings):
        raise HTTPException(409, '候选与当前集合、模型、准入包或处理代码不兼容，请创建新候选')
    engine = sa.create_engine(settings.mysql_dsn)
    try:
        with engine.connect() as conn:
            row = conn.execute(sa.text('SELECT status FROM kb_versions WHERE version=:v'), {'v': candidate['version']}).first()
        if row is None or (writable and row[0] != 'STAGED'):
            raise HTTPException(409, '候选未登记或已发布/归档，不允许写入')
        from qa_core.storage.version_manifest import verify_read_binding
        verify_read_binding(settings, candidate['version'])
        return row[0]
    finally:
        engine.dispose()


def create_candidate(settings, actor, label):
    version = 'local-work-' + uuid.uuid4().hex[:12]
    binding = profile_binding(settings)
    root = safe_path('candidates', version)
    root.mkdir(parents=True, exist_ok=False)
    (root / 'raw').mkdir()
    record = {'protocol': PROTOCOL, 'version': version, 'label': label, 'created_by': actor,
              'created_at': now(), 'tenant_id': 'default', 'binding': binding,
              'scope': '隔离本地演示候选；新资料待具体审核，不自动启用或发布'}
    with candidate_write(version, settings=settings):
        from qa_core.storage.version_manifest import bind_candidate
        bind_candidate(settings, version)
        atomic(root / 'candidate.json', record)
    return record


def settings_for(settings, candidate):
    # Same database, collections, model and identity pipeline, writable paths only.
    return settings.model_copy(update={'data_dir': str(safe_path('candidates', candidate['version'])),
        'reports_dir': str(safe_path('reports', candidate['version'])),
        'generation_enabled': False, 'agent_enabled': False, 'llm_api_key': '',
        'llm_base_url': 'http://127.0.0.1:1/disabled'})


def admission(settings, filename, source_id, checksum):
    """Inherit a concrete admitted original without inventing a new approval."""
    source = Path(settings.data_dir)
    metadata = json.loads((source / 'raw/meta.json').read_text(encoding='utf-8'))
    manifest = json.loads((source / 'source-map.json').read_text(encoding='utf-8'))
    meta = metadata.get(filename)
    entry = next((item for item in manifest.get('entries', []) if item.get('filename') == filename), None)
    if not meta or not entry or meta.get('source_id') != source_id or entry.get('source_id') != source_id:
        return None
    if meta.get('tenant_id') != 'default' or entry.get('tenant_id') != 'default' or entry.get('sha256') != checksum:
        return None
    original = source / 'raw' / filename
    if not original.is_file() or hashlib.sha256(original.read_bytes()).hexdigest() != checksum:
        return None
    from qa_core.ingest.source_identity import validate_mapping
    validate_mapping(source / 'raw', {'protocol': 'reviewed-source-map-v1', 'entries': [entry]}, {filename: meta})
    return {'metadata': meta, 'source_entry': entry,
        'inherited_seal_sha256': hashlib.sha256((source / 'artifact-hashes.json').read_bytes()).hexdigest(),
        'scope': '沿用相同原文、身份、访问范围及既有具体准入；不批准新正文'}


def get_upload(ident):
    if not re.fullmatch(r'[a-f0-9]{32}', ident):
        raise HTTPException(404, '上传记录不存在')
    try:
        record = read(safe_path('uploads', ident, 'upload.json'))
    except FileNotFoundError:
        raise HTTPException(404, '上传记录不存在') from None
    if record.get('id') != ident or record.get('protocol') != PROTOCOL:
        raise ValueError('上传身份不匹配')
    return record


def queue(settings, record):
    candidate = get_candidate(record['kb_version'])
    verify_candidate(settings, candidate, writable=True)
    path = safe_path('uploads', record['id'], record['filename'])
    if not path.is_file() or hashlib.sha256(path.read_bytes()).hexdigest() != record['sha256']:
        raise HTTPException(409, '暂存文件缺失或字节已变化，不能排队/重试')
    approved = admission(settings, record['filename'], record['source_id'], record['sha256'])
    if not approved or approved != record['admission']:
        raise HTTPException(409, '具体准入未验证或已变化，新资料必须先审核')
    selected = settings_for(settings, candidate)
    payload = {'workspace_protocol': PROTOCOL, 'workspace_upload_id': record['id'],
        'candidate_binding_sha256': digest(candidate['binding']), 'filename': record['filename'],
        'source_id': record['source_id'], 'sha256': record['sha256'],
        'strict_metadata': True, 'record_locations': True, 'tokenizer_chunking': True,
        'doc_collection': selected.doc_collection, 'faq_collection': selected.faq_collection}
    payload['execution_binding'] = execution_binding(selected, 'upload', payload)
    from qa_core.ingest.jobs import IngestJobDao
    dao = IngestJobDao(settings.mysql_dsn)
    try:
        with candidate_write(record['kb_version'], settings=settings):
            job, created = dao.create(job_type='upload', tenant_id='default', actor=record['created_by'],
                kb_version=record['kb_version'], payload=payload, max_attempts=1,
                idempotency_key=digest({'workspace_upload_id': record['id'], 'payload': payload}))
            record.update(job_id=job['job_id'], state='queued')
            atomic(safe_path('uploads', record['id'], 'upload.json'), record)
        return job, created
    finally:
        dao.engine.dispose()


def upload(settings, ctx, version, filename, source_id, content):
    if not re.fullmatch(r'[A-Za-z0-9_][A-Za-z0-9_.-]{0,179}', filename) or not re.match(r'^(manual|faq|fault_case|sop|guide|standard)_', filename, re.I):
        raise HTTPException(400, '英文文件名必须含类型前缀，不能包含目录')
    if Path(filename).suffix.lower() not in {'.md', '.csv', '.txt', '.docx', '.pdf', '.xlsx'}:
        raise HTTPException(400, '不支持的文件格式')
    if not source_id.strip() or len(source_id) > 128 or any(ord(ch) < 32 for ch in source_id):
        raise HTTPException(400, '需提供1至128字符的稳定来源ID')
    if not content or len(content) > 20*1024*1024:
        raise HTTPException(400, '文件需非空且不超过20MB')
    candidate = get_candidate(version); verify_candidate(settings, candidate, writable=True)
    checksum = hashlib.sha256(content).hexdigest()
    ident = digest({'version': version, 'filename': filename, 'source_id': source_id, 'sha256': checksum})[:32]
    path = safe_path('uploads', ident, 'upload.json')
    if path.exists():
        old = get_upload(ident)
        if old['admission'] and not old.get('job_id'): queue(settings, old)
        return old, False
    approved = admission(settings, filename, source_id, checksum)
    record = {'protocol': PROTOCOL, 'id': ident, 'kb_version': version, 'filename': filename,
        'sha256': checksum, 'source_id': source_id, 'created_at': now(), 'created_by': ctx.user_id,
        'tenant_id': 'default', 'state': 'awaiting_review' if not approved else 'ready',
        'admission': approved, 'job_id': None}
    with candidate_write(version, settings=settings, invalidate_report=False), IO_LOCK:
        if path.exists(): return get_upload(ident), False
        path.parent.mkdir(parents=True, exist_ok=True)
        body = safe_path('uploads', ident, filename)
        body.write_bytes(content)
        atomic(path, record)
    if approved: queue(settings, record)
    return record, True


def public_upload(record, job=None):
    result = {k: record[k] for k in ('id', 'kb_version', 'filename', 'sha256', 'source_id',
        'created_at', 'created_by', 'tenant_id', 'state', 'job_id')}
    result['admission_status'] = 'inherited_approved' if record['admission'] else 'pending'
    result['admission_note'] = record['admission']['scope'] if record['admission'] else '新原文或身份未获具体准入，仅暂存，未进入向量/问答'
    if job:
        result['job'] = {k: job.get(k) for k in ('job_id', 'status', 'stage', 'attempt_count', 'max_attempts',
            'progress_done', 'progress_total', 'worker_id', 'heartbeat_at', 'error_message', 'stats_json')}
    return result


def uploads():
    found = []
    for path in safe_path('uploads').glob('*/upload.json'):
        try: found.append(get_upload(path.parent.name))
        except (ValueError, HTTPException, OSError): continue
    return sorted(found, key=lambda row: row['created_at'], reverse=True)


def ledger(settings, version):
    get_candidate(version)
    engine = sa.create_engine(settings.mysql_dsn)
    try:
        with engine.connect() as conn:
            rows = [dict(row) for row in conn.execute(sa.text('SELECT filename,file_hash,asset_id,doc_type,status,chunk_count,error_msg '
                'FROM ingest_records WHERE kb_version=:v ORDER BY id'), {'v': version}).mappings()]
        for row in rows:
            error = row.pop('error_msg')
            row['error_kind'] = error.split(':', 1)[0] if error and re.fullmatch(r'[A-Za-z_]+', error.split(':',1)[0]) else '未记录' if error else None
        return rows
    finally:
        engine.dispose()


def worker_status():
    try: state = read(safe_path('worker.json'))
    except (OSError, ValueError): return {'status': 'stopped', 'message': '专用Worker尚未启动'}
    state = {k: state.get(k) for k in ('status', 'pid', 'binding_sha256', 'heartbeat_at', 'message', 'last_job')}
    try:
        age = (datetime.now(timezone.utc)-datetime.fromisoformat(state['heartbeat_at'])).total_seconds()
    except (ValueError, TypeError): age = 999
    if age > 45: state.update(status='stale', message='Worker心跳已过期，队列不会被宣称处理中')
    return state
