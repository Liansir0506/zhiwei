"""Read-only full-profile handoff preparation for the existing approved 8010 pair.

No activation, rollback, admission, credential issuance or model calls here.
Preparing a recovery profile does not authorize executing a live rollback.
"""
from pathlib import Path
import time
import uuid
import sqlalchemy as sa
from fastapi import HTTPException
from qa_core.ingest.binding import digest, version_binding
from qa_core.quality import publication
from qa_core.quality.version_workspace import now, GAPS
from qa_core.storage.version_manifest import verify_read_binding

PROTOCOL = 'original-runtime-handoff-v1'
CODE = ('qa_core/quality/runtime_handoff.py', 'qa_core/api/route_version_workspace.py',
        'scripts/platform_entry.py', 'scripts/start_platform.ps1', 'scripts/start_platform_runtime.ps1',
        'scripts/start_platform_observer.ps1', 'scripts/start_workspace_worker_background.ps1',
        'static/version-workspace.js')
RECOVERY_STEPS = [
    {'phase': 'before_stop', 'failure': '预检、批准或配置绑定失败', 'response': '保留现有完整服务和ACTIVE；不停止、不切换'},
    {'phase': 'after_stop_before_commit', 'failure': '服务已停但版本事务未提交', 'response': '核对唯一ACTIVE仍是原版；使用该版本全部配置恢复，轮换自有ops凭据并核验'},
    {'phase': 'after_commit_before_ready', 'failure': '目标已提交但应用预热或验收失败', 'response': '先停止已鉴定的目标服务，再以旧完整配置重验报告与撤回，通过受控鉴权事务回滚，恢复旧服务并验收'},
    {'phase': 'commit_response_unknown', 'failure': 'HTTP或回执失败，提交结果未知', 'response': '以SQL唯一ACTIVE与审计为准；禁止盲目重放，匹配真实版本的完整配置'},
    {'phase': 'recovery_failed', 'failure': '旧报告失效、回滚或恢复再次失败', 'response': '停止自有不匹配服务，保留实际指针、选择文件和恢复日志；明确未恢复，不声称回退成功'},
    {'phase': 'post_commit_cache_failed', 'failure': '事务已提交但缓存或文件回执失败', 'response': '报告已提交和后处理失败，以SQL账本为准；清理双方缓存后再开放问答，不误报未发布'},
]

def attest_jobs(unfinished, affected):
    # Old, incompatible pending jobs remain quarantined. A read-only preparation
    # must not cancel them or launch a legacy Worker merely to empty the queue.
    if any(j['kb_version'] in affected or j['status'] != 'pending' for j in unfinished):
        raise HTTPException(409, '关联版本存在未完成任务或有任务正在执行；不能准备完整服务交接')
    return [dict(job_id=j['job_id'],version=j['kb_version'],status=j['status'],
                 disposition='quarantined_history_not_claimed_or_modified') for j in unfinished]


class Preparer:
    def __init__(self, workspace):
        if workspace.sandbox:
            raise HTTPException(409, '完整8010交接只在原配置准备，不能使用演练配置代替')
        self.ws = workspace

    def profiles(self):
        from scripts import content_quality_release_runtime as release
        try:
            current = release.selected_settings()  # Revalidates existing concrete receipt and frozen code.
        except ValueError as error:
            # r2 代码修订替换了 qa_usecase.py 等文件，历史 cq 链的逐文件绑定必然失效；
            # 这不是“配置读不到”，而是完整交接只能在对应代码修订下准备。
            raise HTTPException(409,
                '历史 cq 发布链的代码绑定在当前修订下已失效（r2 修订替换了其绑定文件）；'
                '完整交接只能在对应代码修订运行时准备，或先按发布记录回滚。原始原因：'
                + str(error)[:200]) from None
        if current.model_dump() != self.ws.settings.model_dump():
            raise HTTPException(409, '当前服务配置与既有具体批准不一致')
        return release, {release.VERSION: current, release.OLD: release.BASE}

    def records(self):
        with self.ws.engine() as engine, engine.connect() as conn:
            versions = [dict(r) for r in conn.execute(sa.text('SELECT version,status,quality_report_path FROM kb_versions ORDER BY version')).mappings()]
            unfinished = [dict(r) for r in conn.execute(sa.text("SELECT job_id,kb_version,status FROM ingest_jobs WHERE status NOT IN ('success','failed','cancelled') ORDER BY job_id")).mappings()]
        return versions, unfinished

    def inspect_profile(self, release, version, settings, row):
        from scripts.prepare_legacy_projection import verify_seal, digest as file_digest
        verify_seal(Path(settings.data_dir))
        manifest = verify_read_binding(settings, version)
        report = publication.load_bound_report(settings, version, row['quality_report_path'])
        if not manifest or report.get('profile') or report['snapshot'] != manifest.get('snapshot'):
            raise HTTPException(409, '完整服务版本缺少匹配的完整五项门禁与持久快照')
        with self.ws.client() as client:
            snapshot = publication.capture_snapshot(settings, version, client)
        if snapshot != report['snapshot']:
            raise HTTPException(409, '版本实时数据、向量或权限已变化，不能沿用旧门禁')
        from qa_core.security.withdrawals import assert_publishable_sources
        assert_publishable_sources(settings, snapshot['sources'])
        return dict(version=version, status=row['status'], profile=release.owned.public_profile(settings),
            full_configuration_sha256=digest(settings.model_dump()), settings_field_count=len(settings.model_dump()),
            durable_binding=version_binding(settings), snapshot=snapshot, snapshot_sha256=digest(snapshot),
            source_package_seal_sha256=file_digest(Path(settings.data_dir) / 'artifact-hashes.json'),
            report_sha256=file_digest(row['quality_report_path']), gate_checks=report['checks'],
            server_module='scripts.content_quality_local_server' if version == release.VERSION else release.ORIGINAL_SERVER,
            credentials='仅在原配置与执行进程内读取；交接产物不含API Key或数据库凭据')

    def inspect(self, target):
        release, profiles = self.profiles()
        rows, unfinished = self.records()
        indexed = {r['version']: r for r in rows}
        active = [r['version'] for r in rows if r['status'] == 'ACTIVE']
        if active != [release.VERSION] or indexed.get(release.OLD, {}).get('status') != 'ARCHIVED':
            raise HTTPException(409, '要求当前唯一ACTIVE与已批准的上一完整ARCHIVED，不能自动猜测配置')
        if target not in indexed:
            raise HTTPException(404, '目标版本不存在')
        quarantined = attest_jobs(unfinished, {*profiles, target})
        paired = {version: self.inspect_profile(release, version, settings, indexed[version]) for version, settings in profiles.items()}
        runtime = release.owned.inspect()
        if runtime['status'] != 'running':
            raise HTTPException(409, '原8010尚未按完整获准配置运行，请先恢复统一入口')
        blockers = []
        if quarantined:
            blockers.append('另有未完成历史任务保持隔离；不启动旧Worker、不删除任务，实际切换前须核对领取与绑定边界')
        target_facts = dict(version=target, status=indexed[target]['status'])
        if target == release.VERSION:
            blockers.append('目标就是当前ACTIVE，没有新的完整内容变更；本次仅准备当前服务与上一完整版本的恢复交接')
        elif target == release.OLD:
            blockers.append('上一版本可用其完整配置核验；准备恢复方案不代表已授权立即回滚8010')
        else:
            target_facts['profile_mapped'] = False
            blockers.append('目标没有具体获准的完整服务配置映射；不能把当前配置直接套到新候选')
            try:
                delta = self.ws.inspection(target)
                target_facts.update(candidate_kind=delta['candidate_kind'], source_count=len(delta['sources']),
                                    blockers=delta['blockers'], pending_count=sum(r['admission'] == 'pending' for r in delta['uploads']))
                if delta['candidate_kind'] == 'upload_delta':
                    blockers.append('上传候选是增量，尚不具备完整整库门禁与服务发布条件')
            except HTTPException as error:
                target_facts['inspection_status'] = error.status_code
                blockers.append('目标完整来源或读取配置未通过；先完成对应具体候选审核')
        from scripts.prepare_legacy_projection import digest as file_digest
        selection = release.owned.load_state()
        # Bind process identity rather than URL alone; never serialize its Key/hash/file.
        owned = release.owned.owned_process(selection)
        if owned is None:
            raise HTTPException(409, '原应用进程归属核验失败')
        return dict(protocol=PROTOCOL, target=target_facts, current_version=release.VERSION, recovery_version=release.OLD,
            profiles=paired, runtime_identity={'pid': owned.pid, 'created_at': owned.create_time(),
                'instance': selection['instance'], 'url': runtime['url']},
            current_selection_sha256=file_digest(release.SELECTION),
            quarantined_legacy_jobs=quarantined,
            full_handoff_steps=['锁内重新核验账号、具体批准、双方完整配置/实时快照及唯一ACTIVE',
                '暂停匹配当前配置的Worker并核验无未完成任务；只停止鉴定归属的服务',
                '经强鉴权提交版本事务与SQL审计，同时安装目标完整服务配置和读取实现',
                'pythonw隐藏启动、依赖预热与有界等待；验收身份、权限、推荐和所有FAQ原文，零付费',
                '仅启动绑定新配置的新Worker与观察器；旧Worker不能直接复用',
                '按具体失败阶段恢复完整配置、版本、缓存及登录凭据，核对账本和实际ACTIVE'],
            recovery_matrix=RECOVERY_STEPS, execution_blockers=blockers, execution_authorized=False,
            live_pointer_mutations=0, paid_calls=0, new_admissions=0, gaps=GAPS,
            code_bindings={name: file_digest(release.PROJECT / name) for name in CODE})

    def prepare(self, target, actor):
        # Shared advisory lock prevents ingestion/publication changes during preparation.
        with self.ws.publication_lock():
            inspection = self.inspect(target)
        record = dict(id=uuid.uuid4().hex, actor=actor, created_at=now(), expires_at=time.time() + 900,
            state='prepared_for_review', scope='original_8010_full_configuration_preparation_only',
            inspection=inspection, fingerprint=digest(inspection), execution_authorized=False)
        return self.ws.write('runtime-handoffs', record)

    def revalidate(self, ident, actor):
        previous = self.ws.read('runtime-handoffs', ident)
        if previous['actor'] != actor:
            raise HTTPException(403, '只能重新核验本人准备的完整交接')
        if previous['expires_at'] < time.time():
            raise HTTPException(409, '交接已超过15分钟，请重新准备；旧报告可读但不能沿用')
        with self.ws.publication_lock():
            current = self.inspect(previous['inspection']['target']['version'])
        if digest(current) != previous['fingerprint']:
            raise HTTPException(409, '版本、权限、报告、配置、代码或运行归属已变化，请重新准备')
        return dict(id=ident, state='revalidated', fingerprint=previous['fingerprint'],
                    execution_authorized=False, paid_calls=0, live_pointer_mutations=0)
