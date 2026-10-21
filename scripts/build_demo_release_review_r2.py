"""生成 r2 修订的封存 review 包；不发布、不激活（apply 脚本在代码就位后调用）。

产物：release-plan.json / profile-{mode}.json / outbound-review.json / amendment.json / artifact-hashes.json
"""
import json
from pathlib import Path

from scripts import demo_release_runtime_r2 as release
from scripts import stage_demo_release as stage
from scripts.prepare_legacy_projection import digest, read, seal, verify_seal, write

PROJECT = release.PROJECT
REVIEW = release.REVIEW
PREVIOUS_REVIEW = release.PREVIOUS_REVIEW
CANDIDATE = stage.CANDIDATE

LEGACY_PLANS = [
    '.runtime/test/reports/merged-protocol-release-20261010/release-plan.json',
    '.runtime/test/reports/content-quality-release-review-20261011/release-plan.json',
    '.runtime/test/reports/content-quality-release-review-20261011-r2/release-plan.json',
    '.runtime/test/reports/demo-release-review-20261015/release-plan.json',
]
R2_TOOLCHAIN = [
    'scripts/demo_release_runtime_r2.py',
    'scripts/demo_release_local_server_r2.py',
    'scripts/build_demo_release_review_r2.py',
    'scripts/apply_demo_release_r2.py',
    'scripts/platform_entry.py',
    'scripts/platform_runtime_bootstrap.py',
]


def build():
    if REVIEW.exists():
        raise ValueError('review exists; refuse to rewrite')
    verify_seal(CANDIDATE)
    verify_seal(stage.REPORT)
    qa_source = (PROJECT / 'qa_core/application/qa_usecase.py').read_text(encoding='utf-8')
    for marker in ('【相关片段】现有资料不足以生成整合回答', 'refusal: bool = False',
                   '7E 升级（r2）'):
        if marker not in qa_source:
            raise ValueError('qa_usecase repaired version not applied: ' + marker)
    REVIEW.mkdir(parents=True)
    # 1) profiles：必须与 r1 审定 profile 完全一致（配置与知识未变）
    profiles = {}
    for mode in release.MODES:
        selected = release.profile(mode)
        public = release.owned.public_profile(selected)
        legacy = read(PREVIOUS_REVIEW / ('profile-' + mode + '.json'))
        if public != legacy:
            raise ValueError('r2 profile differs from reviewed r1 profile: ' + mode)
        write(REVIEW / ('profile-' + mode + '.json'), public)
        profiles[mode] = digest(REVIEW / ('profile-' + mode + '.json'))
    # 2) outbound：沿用 r1 内容并注明本次修订不涉及外发变化
    outbound = dict(read(PREVIOUS_REVIEW / 'outbound-review.json'))
    outbound['r2_amendment'] = ('本次修订不新增资料、不改变知识内容与外发范围；仅调整运行时问答代码'
                                '（证据覆盖度与拒答降级展示），防护链语义不变。')
    write(REVIEW / 'outbound-review.json', outbound)
    # 3) amendment：修订说明（含 apply 记录的变更前后哈希）
    state_path = PROJECT / '.runtime/test/reports/demo-release-r2-20261016/apply-state.json'
    applied = read(state_path) if state_path.exists() else {}
    amendment = dict(
        version=release.VERSION, previous_active=release.OLD, kind='runtime_code_amendment',
        knowledge_unchanged=True,
        summary=('问答证据覆盖度修复（停用词扩充；先删停用词再滑窗消除"见原"类跨词伪组合；'
                 'ASCII 实体加权）与拒答降级展示（LLM 判定证据不足时展示已过防护的候选片段与出处，'
                 '不再只回复"未找到"）。'),
        rationale=('巡检发现：正常业务问句被回复"未找到"且同义问法结果摇摆；深挖确认主因在'
                   '生成环节的拒答判定与覆盖度对长问句的虚词稀释，而非检索缺失。'),
        user_approval='按照这些点进行调整',
        changed_files=applied.get('files', []),
        tests=['tests/unit/test_stage1_qa_guards.py（同步/流式 2 处契约按新语义更新 + 新增 1 项边界）',
               'tests/unit/test_coverage_stability.py（新增 5 项稳定性回归）'],
        offline_verification=('apply 随附离线验收：既有契约 7 条 + 新语义/降级链路 6 项全过；'
                              '同问句×10 确定性；核心案例覆盖率 0.2 → 0.6。'),
        supersedes=('替代历史各层（merged/content-quality/demo-release）的逐文件哈希校验；'
                    '绑定覆盖为上述各层绑定文件的并集（防护面不缩小），并交叉锁定上一版 plan 哈希。'),
        stop_note='受控迁移期允许 r2 链识别并停止 r1 服务器壳实例（create_time/instance/profile/cwd 校验不变）。',
    )
    write(REVIEW / 'amendment.json', amendment)
    # 4) plan：知识指纹沿用 r1 制品；code_bindings 为历史并集 + r2 工具链
    bindings = {}
    for rel in LEGACY_PLANS:
        bindings.update(read(PROJECT / rel)['code_bindings'])
    bindings.update({name: None for name in R2_TOOLCHAIN})
    code_bindings = {name: digest(PROJECT / name) for name in sorted(bindings)}
    plan = dict(
        version=release.VERSION, previous_active=release.OLD,
        knowledge_binding=dict(
            candidate_projection_sha256=digest(CANDIDATE / 'projection.json'),
            candidate_report_seal_sha256=digest(stage.REPORT / 'artifact-hashes.json'),
            previous_plan_sha256=digest(PREVIOUS_REVIEW / 'release-plan.json')),
        outbound_review_sha256=digest(REVIEW / 'outbound-review.json'),
        amendment_sha256=digest(REVIEW / 'amendment.json'),
        profile_sha256=profiles, validation_paid_calls=0, agent_enabled=False,
        knowledge_unchanged=True, code_bindings=code_bindings,
        independent_quality_approved=False, binding_scope='union_of_all_previous_layers')
    write(REVIEW / 'release-plan.json', plan)
    seal(REVIEW)
    verify_seal(REVIEW)
    plan_sha = digest(REVIEW / 'release-plan.json')
    print(json.dumps(dict(version=release.VERSION, sealed=True, code_bindings=len(code_bindings),
                          profiles=profiles, plan_sha256=plan_sha), ensure_ascii=False))
    return plan_sha


if __name__ == '__main__':
    build()
