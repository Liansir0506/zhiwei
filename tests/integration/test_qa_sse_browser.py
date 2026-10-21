"""Isolated localhost/Edge smoke for SSE UI; no model call in this test."""
import json
import os
import shutil
import socket
import subprocess
import sys
import time
import uuid
from pathlib import Path
from unittest.mock import Mock

import httpx
import pytest
from pymilvus import MilvusClient
from sqlalchemy import text

from qa_core.api.auth import create_api_key, hash_key
from qa_core.config.settings import get_settings
from qa_core.retrieval.searcher import Searcher
from qa_core.schemas.evidence import evidence_of
from qa_core.security.policy import AccessScope
from qa_core.storage.kb_dao import KbDao


def free_port():
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


def test_sse_page_real_http_and_terminal_display(tmp_path):
    edge = Path("C:/Program Files (x86)/Microsoft/Edge/Application/msedge.exe")
    if not edge.is_file() or not shutil.which("node"):
        pytest.skip("Edge and Node required for browser acceptance")
    s = get_settings()
    assert s.is_test and s.doc_collection == "test_protocol_r1_doc"
    assert KbDao(s.mysql_dsn).get_version("v1")["status"] == "ACTIVE"
    root = Path(__file__).resolve().parents[2]
    token = uuid.uuid4().hex
    key = "stage5-sse-browser-" + token
    create_api_key(key, "stage5-browser-" + token, ["admin"])
    port, debug_port = free_port(), free_port()
    screenshot = root / ".runtime/test/reports" / f"stage5-sse-browser-{token[:12]}.png"
    screenshot.parent.mkdir(parents=True, exist_ok=True)
    env = {**os.environ, "APP_ENV_FILE": str(root / ".env.test"), "APP_ENV": "test",
           "AUTH_REQUIRED": "true", "DOC_COLLECTION": "test_protocol_r1_doc",
           "FAQ_COLLECTION": "test_protocol_r1_faq", "PYTHONIOENCODING": "utf-8"}
    server = None
    try:
        milvus = MilvusClient(uri=s.milvus_uri, timeout=15)
        try:
            row = milvus.query(collection_name=s.doc_collection,
                filter='source == "http_503_note.md" and kb_version == "v4-protocol-dev-r1"',
                output_fields=["id"], limit=1, consistency_level="Strong")[0]
            doc = Searcher(client=milvus, embedder=Mock(), cache=Mock(), settings=s).fetch_evidence_chunk(
                row["id"], "v4-protocol-dev-r1", scope=AccessScope.build(roles=["admin"]))
            assert doc is not None
            source = {**evidence_of(doc), "doc_id": doc.id, "doc_type": doc.doc_type,
                      "score": .8}
        finally:
            milvus.close()
        server = subprocess.Popen([sys.executable, "-B", "-m", "uvicorn", "app:app",
            "--host", "127.0.0.1", "--port", str(port)], cwd=root, env=env,
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
        url = f"http://127.0.0.1:{port}"
        with httpx.Client(timeout=3, trust_env=False) as http:
            deadline = time.monotonic() + 25
            while time.monotonic() < deadline:
                try:
                    if http.get(url + "/").status_code == 200: break
                except httpx.TransportError: pass
                if server.poll() is not None: pytest.fail("isolated API exited")
                time.sleep(.1)
            else: pytest.fail("isolated API readiness exceeded 25 seconds")
        run = subprocess.run(["node", str(root / "tests/browser/sse_page_smoke.js")],
            input=json.dumps({"edge": str(edge), "profile": str(tmp_path / "edge-profile"),
                              "debugPort": debug_port, "url": url, "key": key,
                              "evidence": source,
                              "screenshot": str(screenshot)}),
            text=True, encoding="utf-8", capture_output=True, timeout=100,
            cwd=root)
        assert run.returncode == 0, run.stderr
        assert "real SSE" in run.stdout
        assert screenshot.is_file() and screenshot.stat().st_size > 1000
    finally:
        if server:
            server.terminate()
            server.wait(timeout=15)
        with KbDao(s.mysql_dsn).engine.begin() as conn:
            conn.execute(text("DELETE FROM api_keys WHERE key_hash=:h"), {"h": hash_key(key)})
