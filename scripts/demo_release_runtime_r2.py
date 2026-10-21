"""r2 受控修订运行时：问答覆盖度修复与拒答降级的审定发布层。

边界（与 r1 的关系）：
- 知识层不变：仍以 local-demo-release-r1 为唯一 ACTIVE，沿用同一候选投影与质量封存；
- 代码修订：qa_usecase.py（覆盖度 + 拒答降级）经本层重新审定并快照哈希；
- 校验替代：本层 code_bindings 为历史各层（merged / content-quality / demo-release）
  绑定文件的并集加本层工具链；不再运行历史层的陈旧哈希校验（替换而非取消防护面）；
- 兼容停止：受控迁移期允许 r2 链识别并停止 r1 服务器壳实例（其余身份校验不变）；
- 未激活（r2 选择文件不存在）时：完整委托旧运行时（demo_release_runtime），
  保证应用前、回退后的启动/停止能力不变。
"""
import json
from datetime import datetime
from pathlib import Path

import psutil
import sqlalchemy as sa

from scripts import original_local_runtime as owned
from scripts import stage_demo_release as stage
from scripts.prepare_legacy_projection import digest, read, verify_seal

PROJECT = owned.PROJECT
REVIEW = PROJECT / '.runtime/test/reports/demo-release-review-r2-20261016'
PREVIOUS_REVIEW = PROJECT / '.runtime/test/reports/demo-release-review-20261015'
SELECTION = owned.ROOT / 'demo-release-selection-r2.json'
VERSION = 'local-demo-release-r2'
KNOWLEDGE_VERSION = 'local-demo-release-r1'
OLD = 'local-demo-release-r1'
USER_SCOPE = 'demo_release_r2_runtime_amendment'
# receipt 记录用户的批准原话（本次对话："按照这些点进行调整"）；修订明细见 REVIEW/amendment.json。
MODES = {'generation': '按照这些点进行调整'}

SERVER_SHELLS = ('scripts.demo_release_local_server', 'scripts.demo_release_local_server_r2')


def base_settings():
    """与 owned.build_profile() 的净语义一致的基准设置。

    历史 merged 层仅额外设置 data_dir/reports_dir，且被本层 profile() 覆盖（净影响为零）；
    本层 code_bindings 覆盖历史层全集，故不调用历史层的陈旧逐文件校验。
    """
    from scripts.build_original_rollback_baseline import settings_for
    from scripts.original_generation import generation_settings as _generation_settings
    settings = settings_for().model_copy(update={'api_port': 8010, 'redis_url': 'redis://127.0.0.1:6379/0'})
    settings = _generation_settings(settings, PROJECT, owned.ROOT)
    return settings


def profile(mode):
    if mode not in MODES:
        raise ValueError('explicit mode required')
    generating = mode == 'generation'
    settings = base_settings().model_copy(update=dict(
        doc_collection=stage.DOC, faq_collection=stage.FAQ,
        data_dir=str(stage.CANDIDATE), reports_dir=str(stage.REPORT / 'quality'),
        semantic_cache_enabled=False, generation_enabled=generating, agent_enabled=False))
    if not generating:
        settings = settings.model_copy(update=dict(llm_api_key='', llm_base_url='http://127.0.0.1:1/disabled'))
    return settings


def validate_receipt(receipt, plan, plan_sha):
    mode = receipt.get('mode')
    expected = dict(decision='approved', reviewer_kind='human_user_chat_confirmation',
                    user_message=MODES.get(mode), scope=USER_SCOPE, plan_sha256=plan_sha,
                    version=VERSION, mode=mode, new_material_outbound_approved=False,
                    independent_quality_approved=False, recorded_at_utc=receipt.get('recorded_at_utc'))
    if (mode not in MODES or receipt != expected or not expected['recorded_at_utc']
            or plan.get('version') != VERSION or plan.get('previous_active') != OLD
            or plan.get('validation_paid_calls') != 0 or plan.get('agent_enabled') is not False
            or plan.get('knowledge_unchanged') is not True):
        raise ValueError('exact r2 amendment receipt required')
    stamp = datetime.fromisoformat(expected['recorded_at_utc'])
    if stamp.utcoffset() is None or stamp.utcoffset().total_seconds() != 0:
        raise ValueError('UTC approval timestamp required')


def validate_review():
    verify_seal(REVIEW)
    verify_seal(stage.CANDIDATE)
    verify_seal(stage.REPORT)
    plan = read(REVIEW / 'release-plan.json')
    knowledge = plan.get('knowledge_binding') or {}
    if (knowledge.get('candidate_projection_sha256') != digest(stage.CANDIDATE / 'projection.json')
            or knowledge.get('candidate_report_seal_sha256') != digest(stage.REPORT / 'artifact-hashes.json')
            or knowledge.get('previous_plan_sha256') != digest(PREVIOUS_REVIEW / 'release-plan.json')
            or plan['outbound_review_sha256'] != digest(REVIEW / 'outbound-review.json')):
        raise ValueError('r2 knowledge binding changed')
    for name, checksum in plan['code_bindings'].items():
        path = (PROJECT / name).resolve()
        if not path.is_relative_to(PROJECT) or digest(path) != checksum:
            raise ValueError('r2 code binding changed: ' + name)
    return plan


def selected_settings():
    if not SELECTION.exists():
        from scripts import demo_release_runtime as legacy
        return legacy.selected_settings()
    plan = validate_review()
    receipt = read(SELECTION)
    validate_receipt(receipt, plan, digest(REVIEW / 'release-plan.json'))
    selected = profile(receipt['mode'])
    if digest(REVIEW / ('profile-' + receipt['mode'] + '.json')) != plan['profile_sha256'][receipt['mode']]:
        raise ValueError('reviewed mode profile changed')
    if owned.public_profile(selected) != read(REVIEW / ('profile-' + receipt['mode'] + '.json')):
        raise ValueError('current mode profile differs')
    return selected


def validate_profile(settings):
    if settings.model_dump() != selected_settings().model_dump():
        raise ValueError('exact r2 runtime profile required')


def verify_active(settings, engine):
    """知识层仍为 r1（r2 只修订运行时代码），并复核其发布快照。"""
    from qa_core.quality.publication import verify_publication
    with engine.connect() as conn:
        rows = conn.execute(sa.text(
            "SELECT version,quality_report_path FROM kb_versions WHERE status='ACTIVE'")).all()
    if len(rows) != 1 or rows[0][0] != KNOWLEDGE_VERSION:
        raise ValueError('r2 amendments keep local-demo-release-r1 as the unique ACTIVE knowledge version')
    verify_publication(settings, *rows[0])
    return KNOWLEDGE_VERSION


def owned_process_tolerant(state):
    """受控迁移期：接受 r1/r2 两个服务器壳名；其余身份校验（create_time/instance/profile/cwd）不变。"""
    if not state or not state.get('pid'):
        return None
    try:
        process = psutil.Process(state['pid'])
        if not process.is_running() or process.status() == psutil.STATUS_ZOMBIE:
            return None
        command = process.cmdline()

        def argument(name):
            return command[command.index(name) + 1] if name in command and command.index(name) + 1 < len(command) else None

        if (abs(process.create_time() - state['created_at']) > .01
                or not any(shell in command for shell in SERVER_SHELLS)
                or argument('--instance') != state['instance']
                or argument('--profile') != str(owned.ROOT / 'profile.json')
                or Path(process.cwd()).resolve() != PROJECT):
            raise ValueError('PID identity mismatch; refusing to stop unrelated process')
        return process
    except psutil.NoSuchProcess:
        return None


def install_adapter():
    if not SELECTION.exists():
        from scripts import demo_release_runtime as legacy
        legacy.install_adapter()
        return
    selected_settings()
    owned.build_profile = selected_settings
    owned.validate_profile = validate_profile
    owned.verify_active = verify_active
    owned.owned_process = owned_process_tolerant
    owned.SERVER_MODULE = 'scripts.demo_release_local_server_r2'


def main():
    install_adapter()
    owned.main()


if __name__ == '__main__':
    try:
        main()
    except Exception as error:
        import sys
        print('R2 runtime command failed (' + type(error).__name__ + '); inspect approved bindings and owned state.',
              file=sys.stderr)
        sys.exit(1)
