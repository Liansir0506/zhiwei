# -*- coding: utf-8 -*-
"""原子应用 r2 修订（代码修订 + 装配链切换 + review 封存 + receipt）。

用法（项目根目录）：
    PYTHONPATH=. .venv/Scripts/python.exe scripts/apply_demo_release_r2.py --check
    PYTHONPATH=. .venv/Scripts/python.exe scripts/apply_demo_release_r2.py --apply

步骤：前置检查 → 记录并备份 → 应用修订代码/测试 → 部署装配链 → 跑契约测试
      → 生成 review（封存）→ 写 receipt → 输出结果与后续指引。

说明：
- qa_usecase.py 为 r1 审定绑定文件；本脚本将其替换为 WORK 目录中的修订版并由 r2
  review 重新快照哈希——这是本修订发布的唯一代码变更。
- 未激活（receipt 不存在）时 r2 链完整委托旧运行时；失败可用 rollback_r2.py 反向恢复。
"""
import json
import os
import shutil
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
WORK = ROOT / '.runtime/test/reports/demo-release-r2-20261016'
BACKUP = WORK / 'backup'
STATE = WORK / 'apply-state.json'
QA_PATH = ROOT / 'qa_core/application/qa_usecase.py'
REPAIRED = WORK / 'qa_usecase.repaired.py'
TEST_PATH = ROOT / 'tests/unit/test_stage1_qa_guards.py'
TEST_REPAIRED = WORK / 'test_stage1_qa_guards.repaired.py'
STABILITY_SRC = WORK / 'test_coverage_stability.py'
STABILITY_DST = ROOT / 'tests/unit/test_coverage_stability.py'
SELECTION = ROOT / '.runtime/original-local/demo-release-selection-r2.json'
REVIEW = ROOT / '.runtime/test/reports/demo-release-review-r2-20261016'

OLD_STOP_BLOCK = '''    # 停止器必须与“运行中的入口”匹配：先按当前发布版（demo_release_runtime）停；
    # 若运行中进程属于旧内容质量入口（如回滚后），该步会因身份不符而失败，再回退旧停止器。
    & $pythonPath -m scripts.demo_release_runtime stop
    if ($LASTEXITCODE -ne 0) {
        & (Join-Path $PSScriptRoot 'stop_content_quality.ps1')
        if ($LASTEXITCODE -ne 0) { throw 'Owned stop failed; preserve runtime state.' }
    }'''
NEW_STOP_BLOCK = '''    # r2 修订后：停止器唯一指向 r2 运行时；r2 链兼容识别 r1 壳实例（受控迁移），
    # 历史入口的停止器不再适用。
    & $pythonPath -m scripts.demo_release_runtime_r2 stop
    if ($LASTEXITCODE -ne 0) { throw 'Owned stop failed; preserve runtime state.' }'''

OLD_PROBE_BLOCK = '''$health    = Get-HttpCode "$baseUrl/health"
$dashboard = '000'
if ($health -eq '200') { $dashboard = Get-HttpCode "$baseUrl/dashboard/summary" }

$state = 'stopped'
if ($health -eq '200') {
    if ($dashboard -eq '404') { $state = 'stale' } else { $state = 'running' }
}

if ($Check) {
    Log "health=$health dashboard=$dashboard state=$state"
    exit 0
}'''
NEW_PROBE_BLOCK = '''$health    = Get-HttpCode "$baseUrl/health"
$dashboard = '000'
# 版本指纹：/admin/version-workspace/summary 为 r2 优化轮新增路由；404 表示运行的是
# 更早的代码，需要停旧启新以加载修复与 r2 修订（401/200 视为最新版）。
$fingerprint = '000'
if ($health -eq '200') {
    $dashboard = Get-HttpCode "$baseUrl/dashboard/summary"
    $fingerprint = Get-HttpCode "$baseUrl/admin/version-workspace/summary"
}

$state = 'stopped'
if ($health -eq '200') {
    if ($dashboard -eq '404' -or $fingerprint -eq '404') { $state = 'stale' } else { $state = 'running' }
}

if ($Check) {
    Log "health=$health dashboard=$dashboard fingerprint=$fingerprint state=$state"
    exit 0
}'''

EDITS = [
    ('scripts/platform_entry.py',
     'from scripts import demo_release_runtime as release',
     'from scripts import demo_release_runtime_r2 as release'),
    ('scripts/platform_runtime_bootstrap.py',
     'from scripts import demo_release_runtime as release',
     'from scripts import demo_release_runtime_r2 as release'),
    ('scripts/start_platform_runtime.ps1',
     'from scripts import demo_release_runtime as r',
     'from scripts import demo_release_runtime_r2 as r'),
    ('scripts/start_platform_runtime.ps1',
     '& $pythonPath -m scripts.demo_release_runtime stop',
     '& $pythonPath -m scripts.demo_release_runtime_r2 stop'),
    ('scripts/stop_platform.ps1', OLD_STOP_BLOCK, NEW_STOP_BLOCK),
    ('scripts/dev_readonly_instance.py',
     'from scripts import demo_release_runtime as release',
     'from scripts import demo_release_runtime_r2 as release'),
    ('scripts/launch_platform.ps1', OLD_PROBE_BLOCK, NEW_PROBE_BLOCK),
    ('scripts/launch_platform.ps1',
     'Log \'检测到平台正在运行改动前的旧版本（缺少“总览看板”等新功能）。\'',
     'Log \'检测到平台正在运行改动前的旧版本（缺少本轮修复与 r2 修订）。\''),
]


def digest(path):
    import hashlib
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def read_keep(path):
    raw = Path(path).read_bytes()
    return raw.decode('utf-8-sig'), raw.startswith(b'\xef\xbb\xbf')


def write_keep(path, text, bom):
    data = text.encode('utf-8')
    if bom:
        data = b'\xef\xbb\xbf' + data
    Path(path).write_bytes(data)


def check():
    problems = []
    if SELECTION.exists():
        problems.append('r2 receipt 已存在：修订已激活（如需重做请先运行 rollback_r2.py）。')
    if REVIEW.exists():
        problems.append('r2 review 目录已存在：请先运行 rollback_r2.py 或人工核对。')
    for path in (REPAIRED, TEST_REPAIRED, STABILITY_SRC):
        if not path.exists():
            problems.append('缺少变更集文件：' + str(path.relative_to(ROOT)))
    qa_text = QA_PATH.read_text(encoding='utf-8')
    if '【相关片段】现有资料不足以生成整合回答' in qa_text and '7E 升级（r2）' in qa_text:
        problems.append('qa_usecase.py 已是修订版（可能已应用；如需重做请先运行 rollback_r2.py）。')
    return problems


def apply():
    problems = check()
    if problems:
        print('前置检查未通过：')
        for item in problems:
            print(' -', item)
        return 1

    # 1) 记录修订前状态（幂等；用于 review 的 amended_files 与回滚）
    if not STATE.exists():
        files = [str(QA_PATH.relative_to(ROOT)), str(TEST_PATH.relative_to(ROOT))]
        files += sorted({name for name, _old, _new in EDITS})
        state = {
            'created_at_utc': datetime.now(timezone.utc).isoformat(),
            'user_approval': '按照这些点进行调整',
            'files': [{'path': name, 'before_sha256': digest(ROOT / name)} for name in files],
        }
        BACKUP.mkdir(parents=True, exist_ok=True)
        for name in files:
            target = BACKUP / name
            target.parent.mkdir(parents=True, exist_ok=True)
            if not target.exists():
                shutil.copyfile(ROOT / name, target)
        STATE.write_text(json.dumps(state, ensure_ascii=False, indent=1), encoding='utf-8')
        print('已记录修订前状态与备份：', STATE.relative_to(ROOT))
    else:
        print('复用已有 apply-state（幂等续跑）')

    # 2) 应用修订代码与测试
    shutil.copyfile(REPAIRED, QA_PATH)
    print('已应用修订：qa_core/application/qa_usecase.py')
    shutil.copyfile(TEST_REPAIRED, TEST_PATH)
    print('已更新契约测试：tests/unit/test_stage1_qa_guards.py')
    shutil.copyfile(STABILITY_SRC, STABILITY_DST)
    print('已新增稳定性回归：tests/unit/test_coverage_stability.py')

    # 3) 部署装配链（每处替换：旧串必须存在；已替换则跳过）
    for name, old, new in EDITS:
        path = ROOT / name
        text, bom = read_keep(path)
        if new in text and old not in text:
            print('装配链已就位（跳过）：', name)
            continue
        if old not in text:
            print('[严重] 装配链锚点缺失，无法继续：', name, '|', old[:60].replace('\n', ' '))
            print('请运行 rollback_r2.py 恢复后再试。')
            return 1
        write_keep(path, text.replace(old, new, 1), bom)
        print('装配链已切换：', name)

    # 4) 跑契约测试（应用后的生产代码 + 新测试）
    env = {**os.environ, 'PYTHONPATH': '.', 'APP_ENV_FILE': '.env.test'}
    result = subprocess.run(
        [sys.executable, '-m', 'pytest', 'tests/unit/test_stage1_qa_guards.py',
         'tests/unit/test_coverage_stability.py', '-q', '--no-header', '-p', 'no:warnings'],
        cwd=ROOT, env=env, capture_output=True, text=True, encoding='utf-8')
    tail = (result.stdout or '')[-400:]
    print('--- 契约测试 ---')
    print(tail)
    if result.returncode != 0:
        print('[严重] 契约测试未通过；请运行 rollback_r2.py 恢复后再排查。')
        return 1

    # 5) 生成 review（封存）——此时全部文件已就位
    sys.path.insert(0, str(ROOT))
    from scripts import build_demo_release_review_r2 as builder
    plan_sha = builder.build()

    # 6) 写 receipt（用户批准原话；UTC 时间戳）
    receipt = dict(
        decision='approved', reviewer_kind='human_user_chat_confirmation',
        user_message='按照这些点进行调整', scope='demo_release_r2_runtime_amendment',
        plan_sha256=plan_sha, version='local-demo-release-r2', mode='generation',
        new_material_outbound_approved=False, independent_quality_approved=False,
        recorded_at_utc=datetime.now(timezone.utc).isoformat())
    SELECTION.write_text(json.dumps(receipt, ensure_ascii=False, indent=1), encoding='utf-8')
    print()
    print('r2 修订已应用并封存：')
    print(' - review:', REVIEW.relative_to(ROOT))
    print(' - receipt:', SELECTION.relative_to(ROOT))
    print(' - plan_sha256:', plan_sha)
    print()
    print('下一步：运行 scripts/stop_platform.ps1 停止旧实例，再运行 scripts/start_platform.ps1')
    print('（或双击桌面“停止/启动运维平台.bat”）加载 r2 修订；启动后可用 Python 版本核对。')
    return 0


if __name__ == '__main__':
    mode = sys.argv[1] if len(sys.argv) > 1 else '--check'
    if mode == '--check':
        issues = check()
        print('检查完成：', '无阻塞项，可执行 --apply' if not issues else '存在问题：')
        for item in issues:
            print(' -', item)
        sys.exit(1 if issues else 0)
    if mode == '--apply':
        sys.exit(apply())
    print('用法: apply_demo_release_r2.py --check|--apply')
    sys.exit(2)
