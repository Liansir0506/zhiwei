"""Owned, current-profile Worker. It never claims legacy or unreviewed jobs."""
import argparse
import json
import os
import shutil
import subprocess
import sys
import threading
import time
import uuid
from pathlib import Path
import psutil
import sqlalchemy as sa
from qa_core.ingest import workspace as ws
from qa_core.ingest.jobs import IngestJobDao
from qa_core.ingest.binding import digest, execution_binding
from qa_core.ingest.write_guard import candidate_write, WriteBusy
from scripts.platform_entry import exclusive


def owned(state):
    if not state or not state.get('pid'): return None
    try:
        process = psutil.Process(state['pid'])
        command = process.cmdline()
        if (abs(process.create_time()-state['created_at']) > .1 or process.cwd() != str(ws.PROJECT)
            or 'scripts.workspace_ingest_worker' not in command or state['token'] not in command):
            raise ValueError('Worker进程归属不符，保留其他进程')
        return process
    except psutil.NoSuchProcess: return None


def state_file(): return ws.safe_path('worker.json')


def load_state():
    try: return ws.read(state_file())
    except FileNotFoundError: return None


def base_profile():
    # r2 受控修订（2026-10-16）后，本进程锚定当前受控运行时（r2 层）；
    # r2 未激活时由该层回退历史受控链（回滚场景），并要求存在已批准的发布选择。
    from scripts import demo_release_runtime_r2 as runtime
    base = runtime.selected_settings()
    if not runtime.SELECTION.exists():
        from scripts import demo_release_runtime as legacy
        if not legacy.SELECTION.exists():
            raise ValueError('需要已批准的当前本地发布配置')
    return base


def apply_worker_profile(base, selected):
    """Separate process profile: preserve binding, restrict all writable paths."""
    from qa_core.config.settings import Settings, get_settings
    from qa_core.ingest.binding import version_binding
    expected = base.model_copy(update={'data_dir': selected.data_dir, 'reports_dir': selected.reports_dir,
        'generation_enabled': False, 'agent_enabled': False, 'llm_api_key': '',
        'llm_base_url': 'http://127.0.0.1:1/disabled'})
    if selected.model_dump() != expected.model_dump() or version_binding(base) != version_binding(selected):
        raise ValueError('Worker只能沿用当前集合/模型并禁用生成和业务操作')
    for value in (selected.data_dir, selected.reports_dir):
        if not Path(value).resolve().is_relative_to(ws.ROOT.absolute()): raise ValueError('Worker写目录必须位于独立工作区')
    ws.safe_path()
    for name in Settings.model_fields: os.environ.pop(name.upper(), None)
    os.environ.pop('LLM_VALIDATION_BUDGET_FILE', None)
    os.environ.update({name.upper():str(value).lower() if isinstance(value,bool) else str(value)
        for name,value in selected.model_dump().items()})
    os.environ.update(APP_ENV_FILE=os.devnull,TOKENIZERS_PARALLELISM='false',OMP_NUM_THREADS='4',MKL_NUM_THREADS='4',
        HF_HUB_OFFLINE='1',TRANSFORMERS_OFFLINE='1',APP_SCENARIOS_DIR=str(ws.PROJECT/'scenarios'))
    get_settings.cache_clear()
    if get_settings().model_dump()!=selected.model_dump(): raise ValueError('Worker进程配置注入不一致')


class WorkerBindingChanged(ValueError): pass


def verify_start_binding(base, started_binding):
    if digest(ws.profile_binding(base)) != started_binding: raise WorkerBindingChanged()


def validate_job(job, record, base, started_binding):
    payload = job['payload_json']
    if payload.get('workspace_protocol') != ws.PROTOCOL or job['tenant_id'] != 'default' or job['job_type'] != 'upload':
        raise ValueError('任务不属于专用工作区')
    verify_start_binding(base, started_binding)
    if payload.get('workspace_upload_id') != record['id']: raise ValueError('上传任务身份不匹配')
    candidate = ws.get_candidate(record['kb_version']); ws.verify_candidate(base, candidate, writable=True)
    if record['job_id'] != job['job_id'] or job['kb_version'] != record['kb_version']:
        raise ValueError('任务与候选/上传绑定不匹配')
    selected = ws.settings_for(base, candidate)
    if (payload.get('candidate_binding_sha256') != digest(candidate['binding'])
        or payload.get('execution_binding') != execution_binding(selected, 'upload', payload)
        or payload.get('sha256') != record['sha256'] or payload.get('source_id') != record['source_id']
        or payload.get('filename') != record['filename']):
        raise ValueError('当前集合/模型/任务配置不兼容')
    return candidate, selected


class BoundedPassageEmbedder:
    """Same real model/normalization; one passage per forward pass."""
    def __init__(self, embedder): self.embedder, self.model = embedder, embedder.model
    def embed_passages(self, texts): return self.embedder.embed_passages(texts, batch_size=1)


def execute(job, dao, worker_id, base, started_binding):
    from scripts.rebuild_kb import run_rebuild, IngestCancelled
    from qa_core.indexing.embedder.embedder import get_embedder
    from qa_core.indexing.chunkers.supported_rebuild import supported_manual_rebuild
    record = ws.get_upload(job['payload_json']['workspace_upload_id'])
    candidate, selected = validate_job(job, record, base, started_binding)
    staged = ws.safe_path('uploads', record['id'], record['filename'])
    if not staged.is_file(): raise FileNotFoundError('暂存文件缺失')
    checksum = __import__('hashlib').sha256(staged.read_bytes()).hexdigest()
    approved = ws.admission(base, record['filename'], record['source_id'], checksum)
    if checksum != record['sha256'] or not approved or approved != record['admission']:
        raise ValueError('暂存字节或具体准入已变化，拒绝写入')
    apply_worker_profile(base, selected)  # This dedicated process only.
    with candidate_write(record['kb_version'], settings=selected):
        raw = ws.safe_path('candidates', record['kb_version'], 'sources', record['id'], 'raw')
        raw.mkdir(parents=True, exist_ok=True)
        target = raw / record['filename']
        shutil.copyfile(staged, target)
        ws.atomic(raw / 'meta.json', {record['filename']: approved['metadata']})
        mapping = raw.parent / 'source-map.json'
        ws.atomic(mapping, {'protocol': 'reviewed-source-map-v1', 'entries': [approved['source_entry']]})
        dao.update_progress(job['job_id'], worker_id, stage='loading_models', done=0, total=1)
        embedder = get_embedder(selected.embedding_model_path, selected.embedding_device)
        def progress(stage, done, total, stats):
            dao.update_progress(job['job_id'], worker_id, stage=stage, done=done, total=total, stats=stats)
        with supported_manual_rebuild(embedder.model.tokenizer):
            stats = run_rebuild(record['kb_version'], 'incremental', raw, paths=[target],
                strict_metadata=True, record_locations=True, tokenizer_chunking=True,
                reviewed_source_map=mapping, embedder_override=BoundedPassageEmbedder(embedder), progress=progress,
                should_cancel=lambda: dao.cancel_requested(job['job_id']))
        if stats.get('failed'): raise RuntimeError('文件入库失败；请查看候选账本')
        from qa_core.cache.redis_cache import CacheLayer
        CacheLayer(selected.redis_url).invalidate_version(record['kb_version'])
        return dict(stats, admission='inherited_exact_original', policy=ws.POLICY,
                    ledger_url='/admin/ingest-workspace/uploads/' + record['id'])


def run(token):
    base = base_profile()
    apply_worker_profile(base, base.model_copy(update={'data_dir': str(ws.ROOT),
        'reports_dir': str(ws.safe_path('reports')), 'generation_enabled': False,
        'agent_enabled': False, 'llm_api_key': '', 'llm_base_url': 'http://127.0.0.1:1/disabled'}))
    import torch
    torch.set_num_threads(4)
    with exclusive(ws.safe_path('worker.lock')):
        worker_id = 'workspace:' + token
        state = {'status': 'ready', 'pid': os.getpid(), 'created_at': psutil.Process().create_time(),
            'token': token, 'binding_sha256': digest(ws.profile_binding(base)), 'heartbeat_at': ws.now(),
            'message': '专用Worker就绪，仅领取相同配置及具体准入的工作区任务', 'last_job': None}
        ws.atomic(state_file(), state)
        def heartbeat():
            while True:
                state['heartbeat_at'] = ws.now(); ws.atomic(state_file(), state); time.sleep(10)
        threading.Thread(target=heartbeat, daemon=True).start()
        dao = IngestJobDao(base.mysql_dsn)
        while True:
            try:
                # Pin loaded code/config, not just a fresh hash read from disk.
                verify_start_binding(base_profile(), state['binding_sha256'])
                # Candidate/upload roster is the allowlist. No global claim/recovery.
                for record in reversed(ws.uploads()):
                    if not record['admission'] or not record['job_id']: continue
                    job = dao.get(record['job_id'], 'default')
                    if not job or job['payload_json'].get('workspace_protocol') != ws.PROTOCOL: continue
                    if job['status'] == 'running' and job['worker_id'] != worker_id:
                        with dao.engine.begin() as conn:
                            conn.execute(sa.text("UPDATE ingest_jobs SET status='failed',stage='interrupted',finished_at=NOW(), "
                                "error_message='专用Worker已重启，需人工重试' WHERE job_id=:id AND worker_id=:old AND status='running'"),
                                {'id': job['job_id'], 'old': job['worker_id']})
                        continue
                    if job['status'] not in ('pending', 'retry_wait'): continue
                    try: validate_job(job, record, base, state['binding_sha256'])
                    except (ValueError, OSError, __import__('fastapi').HTTPException): continue
                    claimed = dao.claim_next(worker_id, job_id=job['job_id'])
                    if not claimed: continue
                    state.update(status='busy', last_job=job['job_id'])
                    try:
                        result = execute(claimed, dao, worker_id, base, state['binding_sha256']); dao.finish(job['job_id'], worker_id, result)
                    except WriteBusy: dao.defer_busy(job['job_id'], worker_id)
                    except Exception as error:
                        from scripts.rebuild_kb import IngestCancelled
                        if isinstance(error, IngestCancelled): dao.cancelled(job['job_id'], worker_id)
                        else:
                            detail = getattr(error, 'detail', '')
                            safe = detail if isinstance(detail, str) and detail and len(detail) < 180 else (
                                '暂存文件缺失，请恢复原文件后手动重试' if isinstance(error, FileNotFoundError)
                                else '请检查暂存文件、准入及候选配置，修复后重试')
                            dao.fail(job['job_id'], worker_id, type(error).__name__ + ': ' + safe)
                    finally:
                        # The API already holds its own query/rerank models.
                        # Do not keep a second BGE copy resident while idle.
                        from qa_core.indexing.embedder.embedder import reset_embedder
                        reset_embedder(); __import__('gc').collect()
                    state['status'] = 'ready'
                state.update(status='ready', message='专用Worker就绪，仅领取相同配置及具体准入的工作区任务')
            except Exception as error:
                state.update(status='unavailable', message='启动绑定已变化，已暂停领取；停止后按当前配置重新启动' if isinstance(error,WorkerBindingChanged)
                    else 'Worker依赖或工作区不可用（' + type(error).__name__ + '），未领取其他队列')
            time.sleep(2)


def start():
    with exclusive(ws.safe_path('lifecycle.lock')):
        state = load_state(); process = owned(state)
        if process:
            expected = digest(ws.profile_binding(base_profile()))
            if state['binding_sha256'] != expected: raise ValueError('运行中的Worker绑定已过期，先停止后再启动')
            print(json.dumps({'status': 'running', 'pid': process.pid})); return
        token = uuid.uuid4().hex
        options = {'creationflags':subprocess.CREATE_NO_WINDOW|subprocess.DETACHED_PROCESS} if os.name=='nt' else {'start_new_session':True}
        # Explicitly detach stdin/stdout/stderr; a background child must not hold
        # the caller's captured PowerShell/Node pipes open after startup.
        with ws.safe_path('worker.log').open('a',encoding='utf-8') as output, ws.safe_path('worker.error.log').open('a',encoding='utf-8') as errors:
            child = subprocess.Popen([sys.executable,'-m','scripts.workspace_ingest_worker','run','--token',token],
                cwd=ws.PROJECT,stdin=subprocess.DEVNULL,stdout=output,stderr=errors,close_fds=True,**options)
        for _ in range(120):
            state = load_state()
            if state and state.get('token') == token and owned(state):
                print(json.dumps({'status': 'running', 'pid': state['pid']})); return
            if child.poll() is not None: raise RuntimeError('专用Worker启动后退出，请查看独立日志')
            time.sleep(1)
        raise TimeoutError('专用Worker启动未确认；请查看工作区Worker日志')


def stop():
    with exclusive(ws.safe_path('lifecycle.lock')):
        state = load_state(); process = owned(state)
        if process: process.terminate(); process.wait(timeout=15)
        if state:
            state.update(status='stopped', heartbeat_at=ws.now(), message='专用Worker已停止，排队任务保持')
            ws.atomic(state_file(), state)
        print(json.dumps({'status': 'stopped'}))


def main():
    parser = argparse.ArgumentParser(); parser.add_argument('action', choices=['run', 'start', 'stop', 'status']); parser.add_argument('--token')
    args = parser.parse_args()
    if args.action == 'run':
        if not args.token or not __import__('re').fullmatch(r'[a-f0-9]{32}', args.token): raise ValueError('Worker归属token无效')
        run(args.token)
    elif args.action == 'start': start()
    elif args.action == 'stop': stop()
    else: print(json.dumps(ws.worker_status(), ensure_ascii=False))


if __name__ == '__main__':
    try: main()
    except Exception as error:
        print('Workspace Worker failed (' + type(error).__name__ + '); inspect current bindings and owned state.', file=sys.stderr); sys.exit(1)
