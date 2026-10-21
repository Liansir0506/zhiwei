"""Opt-in paid model/browser E2E on approved isolated STAGED data only."""
import json
import os
import shutil
import socket
import subprocess
import sys
import time
import uuid
from pathlib import Path

import httpx
import pytest
from sqlalchemy import text

from qa_core.api.auth import create_api_key, hash_key
from qa_core.config.settings import get_settings
from qa_core.storage.kb_dao import KbDao


def free_port():
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


def test_one_real_deepseek_sse_browser_citation(tmp_path):
    if os.getenv("RUN_STAGE5_DEEPSEEK_E2E") != "1":
        pytest.skip("explicit opt-in required for paid DeepSeek call")
    edge = Path("C:/Program Files (x86)/Microsoft/Edge/Application/msedge.exe")
    if not edge.is_file() or not shutil.which("node"):
        pytest.skip("Edge and Node required")
    assert os.getenv("LLM_API_KEY"), "LLM_API_KEY required; never log it"
    settings = get_settings()
    assert settings.is_test and settings.doc_collection == "test_protocol_r1_doc"
    dao = KbDao(settings.mysql_dsn)
    assert dao.get_version("v1")["status"] == "ACTIVE"
    assert dao.get_version("v4-protocol-dev-r1")["status"] == "STAGED"
    root = Path(__file__).resolve().parents[2]
    token = uuid.uuid4().hex
    key = "stage5-real-browser-" + token
    create_api_key(key, "stage5-real-browser-" + token, ["admin"])
    port, debug_port = free_port(), free_port()
    screenshot = root / ".runtime/test/reports" / f"stage5-real-sse-browser-{token[:12]}.png"
    report = root / ".runtime/test/reports/stage5-real-sse-browser-r1.json"
    screenshot.parent.mkdir(parents=True, exist_ok=True)
    env = {**os.environ, "APP_ENV_FILE": str(root / ".env.test"), "APP_ENV": "test",
           "AUTH_REQUIRED": "true", "DOC_COLLECTION": "test_protocol_r1_doc",
           "FAQ_COLLECTION": "test_protocol_r1_faq", "PYTHONIOENCODING": "utf-8"}
    server = None
    try:
        server = subprocess.Popen([sys.executable, "-B", "-m", "uvicorn", "app:app",
            "--host", "127.0.0.1", "--port", str(port)], cwd=root, env=env,
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
        url = f"http://127.0.0.1:{port}"
        with httpx.Client(timeout=3, trust_env=False) as http:
            deadline = time.monotonic() + 30
            while time.monotonic() < deadline:
                try:
                    if http.get(url + "/").status_code == 200:
                        break
                except httpx.TransportError:
                    pass
                if server.poll() is not None:
                    pytest.fail("isolated API exited")
                time.sleep(.1)
            else:
                pytest.fail("isolated API readiness exceeded 30 seconds")
        run = subprocess.run(["node", str(root / "tests/browser/sse_deepseek_e2e.js")],
            input=json.dumps({"edge": str(edge), "profile": str(tmp_path / "edge-profile"),
                "debugPort": debug_port, "url": url, "key": key,
                "version": "v4-protocol-dev-r1",
                "question": "监控接口返回 HTTP 503，能否仅据此断定是过载？响应一定有 Retry-After 吗？",
                "screenshot": str(screenshot)}),
            text=True, encoding="utf-8", capture_output=True, timeout=150, cwd=root)
        assert run.returncode == 0, run.stderr
        outcome = json.loads(run.stdout.strip())
        assert outcome["passed"] and screenshot.is_file() and screenshot.stat().st_size > 1000
        report.write_text(json.dumps({**outcome, "screenshot": str(screenshot)},
            ensure_ascii=False, indent=2), encoding="utf-8")
    finally:
        if server:
            server.terminate()
            server.wait(timeout=15)
        with dao.engine.begin() as conn:
            conn.execute(text("DELETE FROM api_keys WHERE key_hash=:h"), {"h": hash_key(key)})
    assert dao.get_version("v1")["status"] == "ACTIVE"
    assert dao.get_version("v4-protocol-dev-r1")["status"] == "STAGED"
