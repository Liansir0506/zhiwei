"""Local read-only dev instance for UI/接口验证；不触碰 8010 的运行态与凭据。

用法（普通终端或后台任务）：
    PYTHONPATH=. .venv/Scripts/python.exe scripts/dev_readonly_instance.py 8011
用途：在改动前端或后端后，用真实浏览器/HTTP 验收新行为，而不重启用户正在使用的 8010。
说明：只读读取已发布快照与库表；不写 state.json、不改知识版本、不发外网。
"""
import sys
from scripts import demo_release_runtime_r2 as release

release.install_adapter()
settings = release.selected_settings()
from scripts.original_local_runtime import apply_profile
apply_profile(settings)

import uvicorn
from app import app

port = int(sys.argv[1]) if len(sys.argv) > 1 else 8011
print('dev instance on', port, '| doc collection:', settings.doc_collection, flush=True)
uvicorn.run(app, host='127.0.0.1', port=port, log_level='warning', access_log=False)
