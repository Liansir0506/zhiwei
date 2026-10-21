# -*- coding: utf-8 -*-
"""回滚 r2 修订（反向恢复；要求平台已停止——先运行 stop_platform.ps1）。

恢复备份文件、移除 receipt 与 review（备份与记录保留，便于审计与再次应用）。
"""
import json
import shutil
import sys
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
WORK = ROOT / '.runtime/test/reports/demo-release-r2-20261016'
BACKUP = WORK / 'backup'
STATE = WORK / 'apply-state.json'
SELECTION = ROOT / '.runtime/original-local/demo-release-selection-r2.json'
REVIEW = ROOT / '.runtime/test/reports/demo-release-review-r2-20261016'
STABILITY_DST = ROOT / 'tests/unit/test_coverage_stability.py'


def main():
    if not STATE.exists():
        print('无 apply-state，无需回滚。')
        return 0
    state = json.loads(STATE.read_text(encoding='utf-8'))
    restored = []
    for item in state['files']:
        name = item['path']
        source = BACKUP / name
        if source.exists():
            shutil.copyfile(source, ROOT / name)
            restored.append(name)
    if STABILITY_DST.exists():
        STABILITY_DST.unlink()
        restored.append('(removed) tests/unit/test_coverage_stability.py')
    if SELECTION.exists():
        SELECTION.unlink()
        restored.append('(removed) .runtime/original-local/demo-release-selection-r2.json')
    if REVIEW.exists():
        shutil.rmtree(REVIEW)
        restored.append('(removed) .runtime/test/reports/demo-release-review-r2-20261016')
    record = WORK / 'rollback-record.json'
    record.write_text(json.dumps(dict(
        rolled_back_at_utc=datetime.now(timezone.utc).isoformat(),
        restored=restored,
        note='备份与 apply-state 保留；如需再次应用请重新运行 apply（--check 会复核）。',
    ), ensure_ascii=False, indent=1), encoding='utf-8')
    print('已回滚：')
    for item in restored:
        print(' -', item)
    print('记录：', record.relative_to(ROOT))
    return 0


if __name__ == '__main__':
    sys.exit(main())
