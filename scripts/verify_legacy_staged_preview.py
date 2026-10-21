"""Short-lived local HTTP/Edge preview of real approved STAGED citations, no LLM."""
import argparse
import json
import os
from pathlib import Path
import secrets
import socket
import subprocess
import sys
import tempfile
import time

import httpx
import sqlalchemy as sa

from qa_core.config.settings import get_settings
from scripts.prepare_legacy_projection import digest, read, validate_projection, write
from scripts.stage_legacy_candidate import configure, validate_base


def port():
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0)); return s.getsockname()[1]


def main():
    p = argparse.ArgumentParser(description=__doc__)
    for name in ("stage-report", "candidate", "package", "review"):
        p.add_argument("--" + name, required=True)
    args = p.parse_args()
    base = get_settings(); validate_base(base)
    stage = Path(args.stage_report).resolve(); candidate = Path(args.candidate).resolve()
    validate_projection(args.package, args.review, candidate)
    environment = read(stage / "environment.json")
    if (environment.get("candidate") != str(candidate)
            or environment.get("candidate_artifacts_sha256") != digest(candidate / "artifact-hashes.json")
            or environment.get("version") != "test-legacy-demo-r1" or environment.get("scope") != "isolated_local_demo_only"):
        raise ValueError("requires this exact approved isolated stage")
    output = stage / "browser-preview"
    if output.exists():
        raise ValueError("refuse browser report overwrite")
    settings = configure(base, stage, environment["database"])
    if (settings.doc_collection, settings.faq_collection) != (environment["doc_collection"], environment["faq_collection"]):
        raise ValueError("collection binding mismatch")
    from qa_core.storage.kb_dao import KbDao
    dao = KbDao(settings.mysql_dsn)
    if dao.get_version(environment["version"])["status"] != "STAGED":
        raise ValueError("requires STAGED preview, never ACTIVE")
    from qa_core.storage.version_manifest import verify_read_binding
    if not verify_read_binding(settings, environment["version"]):
        raise ValueError("requires bound candidate manifest")
    output.mkdir()
    refs = read(stage / "evidence-api.json")["samples"]
    if len(refs) != 4:
        raise ValueError("four approved source-group previews required")
    from qa_core.api.auth import create_api_key, hash_key
    key = secrets.token_urlsafe(24)
    create_api_key(key, "legacy-browser-" + secrets.token_hex(6), ["admin"])
    server, log = None, None
    try:
        address = "http://127.0.0.1:" + str(port())
        child = dict(os.environ, LLM_API_KEY="")
        log = (output / "server.log").open("w", encoding="utf-8")
        server = subprocess.Popen([sys.executable, "-m", "uvicorn", "app:app", "--host", "127.0.0.1",
            "--port", address.rsplit(":", 1)[1], "--log-level", "warning"], env=child,
            stdout=log, stderr=log, creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0)
        with httpx.Client(timeout=4, trust_env=False) as http:
            deadline = time.monotonic() + 25
            while time.monotonic() < deadline:
                try:
                    if http.get(address + "/").status_code == 200:
                        break
                except httpx.TransportError:
                    pass
                if server.poll() is not None:
                    raise RuntimeError("isolated preview server exited")
                time.sleep(.1)
            else:
                raise RuntimeError("isolated preview startup exceeded 25 seconds")
            for entry in refs:
                ref = entry["evidence"]
                response = http.get(address + ref["source_url"], headers={"X-API-Key": key})
                if response.status_code != 200 or response.json() != ref:
                    raise ValueError("real HTTP source differs from verified STAGED citation")
                if http.get(address + ref["source_url"]).status_code != 401:
                    raise ValueError("real HTTP source lacks authentication")
        temp_root = Path(".runtime/test").resolve()
        with tempfile.TemporaryDirectory(prefix="legacy-preview-profile-", dir=temp_root) as profile:
            # Check the resolved target before TemporaryDirectory's recursive cleanup.
            if not Path(profile).resolve().is_relative_to(temp_root):
                raise ValueError("browser profile outside intended test directory")
            payload = {"edge": "C:/Program Files (x86)/Microsoft/Edge/Application/msedge.exe", "profile": profile,
                "debugPort": port(), "url": address, "key": key, "version": environment["version"],
                "sources": [{**e["evidence"], "doc_id": e["evidence"]["chunk_id"], "doc_type": e["doc_type"],
                             "score": .8} for e in refs], "reportDir": str(output)}
            run = subprocess.run(["node", "tests/browser/legacy_staged_smoke.js"], input=json.dumps(payload),
                encoding="utf-8", text=True, capture_output=True, timeout=65)
            (output / "browser.log").write_text(run.stdout + run.stderr, encoding="utf-8")
            if run.returncode:
                raise RuntimeError("STAGED browser preview failed; inspect browser.log")
        result = {"passed": True, "real_http_sources": 4, "unauthenticated_sources_denied": 4,
                  "paid_llm_calls": 0, "llm_key_available_to_server": False, "version": environment["version"],
                  "status": "STAGED", "temporary_profile_removed": True, "temporary_key_removed": True}
    finally:
        if server:
            server.terminate()
            try:
                server.wait(timeout=15)
            except subprocess.TimeoutExpired:
                server.kill(); server.wait(timeout=5)
        if log:
            log.close()
        with dao.engine.begin() as conn:
            conn.execute(sa.text("DELETE FROM api_keys WHERE key_hash=:h"), {"h": hash_key(key)})
        dao.engine.dispose()
    write(output / "summary.json", result)
    print(json.dumps(result))


if __name__ == "__main__":
    main()
