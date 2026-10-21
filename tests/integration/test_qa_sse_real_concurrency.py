"""Opt-in two-request DeepSeek SSE observation; not a capacity benchmark."""
import asyncio
import json
import os
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


def test_two_real_deepseek_sse_requests_once():
    if os.getenv("RUN_STAGE5_REAL_CONCURRENCY") != "1":
        pytest.skip("explicit opt-in required for two paid DeepSeek calls")
    assert os.getenv("LLM_API_KEY"), "LLM_API_KEY required; never log it"
    settings = get_settings()
    assert settings.is_test and settings.doc_collection == "test_protocol_r1_doc"
    assert settings.faq_collection == "test_protocol_r1_faq"
    dao = KbDao(settings.mysql_dsn)
    assert dao.get_version("v1")["status"] == "ACTIVE"
    assert dao.get_version("v4-protocol-dev-r1")["status"] == "STAGED"
    root = Path(__file__).resolve().parents[2]
    approved = json.loads((root / "data/candidates/stage4-protocol-approved-r2/eval-dev.json")
        .read_text(encoding="utf-8"))
    selected = [next(item for item in approved["cases"] if item["id"] == case_id)
        for case_id in ("PN-http-429-1", "PN-dns-negative-ttl-1")]
    assert all(item["approval"]["status"] == "approved" and item["split"] == "dev"
        for item in selected)
    token = uuid.uuid4().hex
    key = "stage5-dual-" + token
    create_api_key(key, "stage5-dual-" + token, ["admin"])
    port = free_port()
    env = {**os.environ, "APP_ENV_FILE": str(root / ".env.test"), "APP_ENV": "test",
        "AUTH_REQUIRED": "true", "DOC_COLLECTION": "test_protocol_r1_doc",
        "FAQ_COLLECTION": "test_protocol_r1_faq", "SEMANTIC_CACHE_ENABLED": "false",
        "PYTHONIOENCODING": "utf-8"}
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

        async def observe():
            start = asyncio.Event()

            async def one(item):
                async with httpx.AsyncClient(timeout=httpx.Timeout(130), trust_env=False) as http:
                    await start.wait()
                    begin = time.perf_counter()
                    result = {"caseId": item["id"], "httpStatus": None, "firstBodyMs": None,
                        "totalMs": None, "tokenEvents": 0, "sourceCount": 0,
                        "status": None, "kbVersion": None, "errorCode": None}
                    async with http.stream("POST", url + "/qa/sse",
                        headers={"X-API-Key": key}, json={"query": item["query"],
                        "session_id": "dual-" + token + "-" + item["id"],
                        "kb_version": "v4-protocol-dev-r1"}) as response:
                        result["httpStatus"] = response.status_code
                        if response.status_code != 200:
                            return result
                        name = None
                        async for line in response.aiter_lines():
                            if line.startswith("event: "):
                                name = line[7:]
                            elif line.startswith("data: "):
                                data = json.loads(line[6:])
                                if name == "token" and data.get("text"):
                                    result["tokenEvents"] += 1
                                    if result["firstBodyMs"] is None:
                                        result["firstBodyMs"] = round((time.perf_counter() - begin) * 1000)
                                elif name == "sources":
                                    result["sourceCount"] = len(data.get("sources") or [])
                                elif name == "done":
                                    result["status"] = data.get("status")
                                    result["kbVersion"] = data.get("kb_version")
                                elif name == "error":
                                    result["errorCode"] = data.get("code")
                    result["totalMs"] = round((time.perf_counter() - begin) * 1000)
                    return result

            tasks = [asyncio.create_task(one(item)) for item in selected]
            await asyncio.sleep(.05)
            start.set()
            return await asyncio.gather(*tasks)

        outcomes = asyncio.run(observe())
        report = {"scope": "two simultaneous requests, one run, isolated STAGED dev data",
            "semanticCacheEnabled": False, "requests": outcomes,
            "notP95OrCapacity": True}
        path = root / ".runtime/test/reports/stage5-real-dual-sse-r1.json"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
        assert all(r["httpStatus"] == 200 and r["status"] == "ANSWERED"
            and r["kbVersion"] == "v4-protocol-dev-r1" and r["tokenEvents"] > 0
            and r["sourceCount"] > 0 for r in outcomes), report
    finally:
        if server:
            server.terminate()
            server.wait(timeout=15)
        with dao.engine.begin() as conn:
            conn.execute(text("DELETE FROM api_keys WHERE key_hash=:h"), {"h": hash_key(key)})
    assert dao.get_version("v1")["status"] == "ACTIVE"
    assert dao.get_version("v4-protocol-dev-r1")["status"] == "STAGED"
