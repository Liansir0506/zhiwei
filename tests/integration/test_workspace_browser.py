"""Workspace UI through real HTTP, MySQL, protocol evidence and isolated Edge.

Only answer terminal frames are controlled. No paid model call or publication.
"""
import json
import os
import shutil
import socket
import subprocess
import sys
import time
import uuid
from datetime import datetime
from pathlib import Path
from unittest.mock import Mock

import httpx
import pytest
import sqlalchemy as sa
from pymilvus import MilvusClient

from qa_core.api.auth import create_api_key, hash_key
from qa_core.config.settings import get_settings
from qa_core.domain.models import Alarm, Device, EnterpriseSnapshot
from qa_core.retrieval.searcher import Searcher
from qa_core.schemas.evidence import evidence_of
from qa_core.security.policy import AccessScope
from qa_core.storage.domain_dao import DomainDao
from qa_core.storage.kb_dao import KbDao


def free_port():
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


def test_workspace_evidence_context_permissions_and_responsive_layout(tmp_path):
    edge = Path("C:/Program Files (x86)/Microsoft/Edge/Application/msedge.exe")
    if not edge.is_file() or not shutil.which("node"):
        pytest.skip("Edge and Node required for workspace browser acceptance")
    settings = get_settings()
    assert settings.is_test and settings.doc_collection == "test_protocol_r1_doc"
    assert settings.faq_collection == "test_protocol_r1_faq"
    root = Path(__file__).resolve().parents[2]
    token = uuid.uuid4().hex[:12]
    report_dir = root / ".runtime/test/reports" / ("workspace-browser-" + token)
    report_dir.mkdir(parents=True)
    dao = KbDao(settings.mysql_dsn)
    device, alarm = "workspace-device-" + token, "workspace-alarm-" + token
    keys = {role: "workspace-" + role + "-" + token for role in ("admin", "ops", "foreign")}
    server = None
    with dao.engine.connect() as conn:
        before = list(conn.execute(sa.text("SELECT version,status FROM kb_versions ORDER BY version")))
    try:
        DomainDao(settings.mysql_dsn).upsert_snapshot(EnterpriseSnapshot(
            devices=[Device(device_id=device, tenant_id="default", name="联调测试网关 <不解析HTML>",
                            device_type="gateway", vendor="模拟厂商", model="UI-fixture",
                            software_version="fixture-1.0")],
            alarms=[Alarm(alarm_id=alarm, tenant_id="default", device_id=device,
                          alarm_code="ALM-1201", source_system="mock-workspace-fixture",
                          occurred_at=datetime.now())],
        ))
        for role, key in keys.items():
            create_api_key(key, "workspace-" + role + "-" + token,
                           ["admin" if role == "foreign" else role],
                           tenant_id="foreign-" + token if role == "foreign" else "default")
        milvus = MilvusClient(uri=settings.milvus_uri, timeout=15)
        try:
            row = milvus.query(collection_name=settings.doc_collection,
                filter='source == "http_503_note.md" and kb_version == "v4-protocol-dev-r1"',
                output_fields=["id"], limit=1, consistency_level="Strong")[0]
            doc = Searcher(client=milvus, embedder=Mock(), cache=Mock(), settings=settings).fetch_evidence_chunk(
                row["id"], "v4-protocol-dev-r1", scope=AccessScope.build(roles=["admin"]))
            assert doc is not None
            source = {**evidence_of(doc), "doc_id": doc.id, "doc_type": doc.doc_type, "score": .8}
        finally:
            milvus.close()
        api_port = free_port()
        env = {**os.environ, "APP_ENV_FILE": str(root / ".env.test"), "APP_ENV": "test",
               "AUTH_REQUIRED": "true", "DOC_COLLECTION": "test_protocol_r1_doc",
               "FAQ_COLLECTION": "test_protocol_r1_faq", "PYTHONIOENCODING": "utf-8"}
        server = subprocess.Popen([sys.executable, "-B", "-m", "uvicorn", "app:app",
                                  "--host", "127.0.0.1", "--port", str(api_port)],
                                 cwd=root, env=env, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                                 creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
        url = f"http://127.0.0.1:{api_port}"
        with httpx.Client(timeout=3, trust_env=False) as http:
            for _ in range(250):
                try:
                    if http.get(url + "/").status_code == 200:
                        break
                except httpx.TransportError:
                    pass
                if server.poll() is not None:
                    pytest.fail("workspace API exited")
                time.sleep(.1)
            else:
                pytest.fail("workspace API readiness exceeded 25 seconds")
        result = subprocess.run(["node", str(root / "tests/browser/workspace_smoke.js")],
            input=json.dumps({"edge": str(edge), "profile": str(tmp_path / "edge-profile"),
                "debugPort": free_port(), "url": url, "keys": keys, "device": device, "alarm": alarm,
                "evidence": source, "reportDir": str(report_dir)}),
            text=True, encoding="utf-8", capture_output=True, timeout=170, cwd=root)
        assert result.returncode == 0, result.stderr
        report = json.loads(result.stdout.strip())
        assert report["passed"] and report["paid_model_calls"] == 0
        for name in report["screenshots"]:
            assert (report_dir / name).stat().st_size > 1000
        (report_dir / "result.json").write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
        with dao.engine.connect() as conn:
            assert list(conn.execute(sa.text("SELECT version,status FROM kb_versions ORDER BY version"))) == before
    finally:
        if server:
            server.terminate()
            server.wait(timeout=15)
        with dao.engine.begin() as conn:
            for key in keys.values():
                conn.execute(sa.text("DELETE FROM api_keys WHERE key_hash=:h"), {"h": hash_key(key)})
            # Remove only this run's conversations/feedback; never touch existing business data.
            session_ids = [r[0] for r in conn.execute(sa.text("SELECT session_id FROM sessions WHERE user_id LIKE :u"),
                                                    {"u": "workspace-%-" + token})]
            for session in session_ids:
                conn.execute(sa.text("DELETE FROM feedback WHERE message_id IN (SELECT message_id FROM messages WHERE session_id=:s)"), {"s": session})
                conn.execute(sa.text("DELETE FROM messages WHERE session_id=:s"), {"s": session})
                conn.execute(sa.text("DELETE FROM sessions WHERE session_id=:s"), {"s": session})
            conn.execute(sa.text("DELETE FROM alarms WHERE alarm_id=:a"), {"a": alarm})
            conn.execute(sa.text("DELETE FROM devices WHERE device_id=:d"), {"d": device})
        dao.engine.dispose()
