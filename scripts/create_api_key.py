#!/usr/bin/env python
"""创建 API Key。key 明文只在创建时打印一次，库里存 sha256。

用法：
  python scripts/create_api_key.py --user ops01 --roles ops,admin           # 自动生成 key
  python scripts/create_api_key.py --user ops01 --roles ops --key my-key    # 指定 key
"""
from __future__ import annotations

import argparse
import secrets
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from qa_core.api.auth import create_api_key


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--user", required=True)
    parser.add_argument("--roles", default="ops", help="逗号分隔，如 ops,admin")
    parser.add_argument("--tenant", default="default")
    parser.add_argument("--dept", default=None)
    parser.add_argument("--device-types", default="", help="设备域，逗号分隔；空表示不限制")
    parser.add_argument("--security-levels", default="", help="允许密级，逗号分隔；空按角色上限")
    parser.add_argument("--key", default=None, help="不传则随机生成")
    args = parser.parse_args()

    raw_key = args.key or f"tk-{secrets.token_hex(16)}"
    roles = [r.strip() for r in args.roles.split(",") if r.strip()]
    device_types = [v.strip() for v in args.device_types.split(",") if v.strip()]
    security_levels = [v.strip() for v in args.security_levels.split(",") if v.strip()]
    create_api_key(raw_key, args.user, roles, args.tenant, args.dept,
                   device_types, security_levels)
    print(f"API Key 已创建（只显示这一次，请妥善保存）：\n  user:  {args.user}"
          f"\n  tenant: {args.tenant}\n  dept:   {args.dept or '-'}"
          f"\n  devices: {device_types or '-'}\n  security: {security_levels or '角色上限'}"
          f"\n  roles: {roles}\n  key:   {raw_key}")


if __name__ == "__main__":
    main()
