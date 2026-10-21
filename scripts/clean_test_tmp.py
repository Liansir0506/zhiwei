#!/usr/bin/env python
"""清理 pytest 临时目录（.test-tmp / .test-cache）。

背景：pytest.ini 固定了 `addopts = -q --basetemp=.test-tmp` 与 `cache_dir = .test-cache`。
basetemp 会随每次运行累积历史子目录，实测长到 564 个文件后有两个后果：

1. 占空间（约 16MB，还在涨）；
2. 部分宿主环境对批量删除有阈值保护，pytest 在每次运行前 rmtree basetemp 时会被拦截，
   表现为用到 tmp_path 的用例在 setup 阶段报错
   （首个抛 SystemExit，后续级联成 assert not self._finalizers），
   看起来像测试失败，实际与被测代码无关。

用法：
    python scripts/clean_test_tmp.py            # 预览，不删除
    python scripts/clean_test_tmp.py --yes      # 确认后真正删除

只处理白名单内的目录，且必须显式 --yes 才会删除。
"""
from __future__ import annotations

import argparse
import shutil
from pathlib import Path

# 白名单：仅清理 pytest 自身产物，不使用通配符、不做递归猜测
TARGETS = (".test-tmp", ".test-cache")


def _scan(path: Path) -> tuple[int, int]:
    files = 0
    size = 0
    for p in path.rglob("*"):
        if p.is_file():
            files += 1
            try:
                size += p.stat().st_size
            except OSError:
                pass
    return files, size


def main() -> None:
    ap = argparse.ArgumentParser(description="清理 pytest 临时目录")
    ap.add_argument("--root", default=None, help="项目根目录，默认为脚本所在目录的上级")
    ap.add_argument("--yes", action="store_true", help="真正执行删除（默认只预览）")
    args = ap.parse_args()

    root = Path(args.root).resolve() if args.root else Path(__file__).resolve().parents[1]
    print("项目根目录: %s" % root)

    total_files = 0
    total_size = 0
    for name in TARGETS:
        target = root / name
        if not target.exists():
            print("  [跳过] %s 不存在" % name)
            continue
        if not target.is_dir():
            print("  [跳过] %s 不是目录，不处理" % name)
            continue
        files, size = _scan(target)
        total_files += files
        total_size += size
        print("  [%s] %s: %d 个文件, %.1f MB"
              % ("已删除" if args.yes else "将删除", name, files, size / 1048576))
        if args.yes:
            shutil.rmtree(target, ignore_errors=True)
            print("           删除后仍存在: %s" % target.exists())

    mb = total_size / 1048576
    if not args.yes:
        print("\n预览完成：共 %d 个文件、%.1f MB。" % (total_files, mb))
        print("确认无误后加 --yes 执行删除。")
        print("提示：后续跑 pytest 可用 --basetemp 指定一个全新目录，避免再次累积：")
        print("      python -m pytest --basetemp=%TEMP%/rag-pytest")
        print("      注意 pytest 每次运行都会 rmtree 该目录，同一目录第二次用仍会触发累积清理。")
    else:
        print("\n清理完成：释放 %.1f MB。" % mb)


if __name__ == "__main__":
    main()
