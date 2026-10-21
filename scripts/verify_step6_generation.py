"""Explicitly budgeted real SSE regression on approved STAGED protocol notes.

Run with APP_ENV_FILE=.env.test and --paid-max 4 after user authorization.
Counts provider attempts, including retries, before sending; never logs credentials.
"""
import argparse
import hashlib
import json
import os
import secrets
import socket
import subprocess
import sys
import threading
import time
from pathlib import Path
from urllib.parse import urlparse

import httpx
import sqlalchemy as sa

ROOT = Path(__file__).resolve().parents[1]
VERSION = "v4-protocol-dev-r1"


class ProviderBudget:
    """Persistent attempt counter; re-opening a report cannot reset paid budget."""
    def __init__(self, ledger, limit):
        self.ledger, self.limit = Path(ledger), limit
        assert 1 <= limit <= 4
        self.attempts = json.loads(self.ledger.read_text())["attempts"] if self.ledger.exists() else []
        assert len(self.attempts) <= limit
        self.lock, self.started = threading.Lock(), time.monotonic()
        self._save()

    def _save(self):
        self.ledger.write_text(json.dumps({"limit": self.limit, "attempts": self.attempts}), encoding="utf-8")

    def __call__(self, request):
        with self.lock:
            if len(self.attempts) >= self.limit:
                raise RuntimeError("authorized provider budget exhausted")
            assert request.method == "POST" and request.url.host == "api.deepseek.com"
            self.attempts.append({"attempt": len(self.attempts) + 1, "elapsed_s": round(time.monotonic() - self.started, 3)})
            self._save()


def acceptance_app():
    """Child factory; the guard is installed before accepting any QA requests."""
    from qa_core.config.settings import get_settings
    from qa_core.api.route_qa import get_usecase
    from app import app
    settings = get_settings()
    assert settings.is_test and settings.doc_collection == "test_protocol_r1_doc"
    assert settings.auth_required and not settings.semantic_cache_enabled
    assert urlparse(settings.llm_base_url).hostname == "api.deepseek.com"
    ledger = Path(os.environ["STEP6_PROVIDER_LEDGER"])
    limit = int(os.environ["STEP6_PROVIDER_LIMIT"])
    get_usecase().llm._client.event_hooks["request"].append(ProviderBudget(ledger, limit))
    return app


def free_port():
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--paid-max", type=int, required=True, choices=[4])
    parser.add_argument("--report-dir", required=True)
    parser.add_argument("--citations-only", action="store_true", help="read stored sources; never repeat generation")
    args = parser.parse_args()
    output = Path(args.report_dir).resolve()
    output.mkdir(parents=True, exist_ok=True)
    report = output / "generation.json"
    if not args.citations_only and (report.exists() or (output / "provider-ledger.json").exists()):
        raise ValueError("refuse report overwrite or accidental paid rerun")
    from dotenv import dotenv_values
    from qa_core.config.settings import get_settings
    from qa_core.api.auth import create_api_key, hash_key
    from qa_core.storage.kb_dao import KbDao
    from qa_core.quality.protocol_gate import approved_sources
    settings = get_settings()
    assert settings.is_test and sa.engine.make_url(settings.mysql_dsn).database == "rag_kb_test"
    _, receipt_sha = approved_sources()
    dao = KbDao(settings.mysql_dsn)
    assert dao.get_version(VERSION)["status"] == "STAGED"
    def versions():
        with dao.engine.connect() as conn:
            return [dict(r) for r in conn.execute(sa.text(
                "SELECT version,status,quality_report_path FROM kb_versions ORDER BY version")).mappings()]
    before = versions()
    private = dotenv_values(ROOT / ".env.runtime")
    assert private.get("LLM_API_KEY"), "private runtime credential missing"
    model = private.get("LLM_MODEL") or settings.llm_model
    base = private.get("LLM_BASE_URL") or settings.llm_base_url
    assert urlparse(base).hostname == "api.deepseek.com"
    cases = json.loads((ROOT / "data/candidates/stage4-protocol-approved-r2/eval-dev.json").read_text(encoding="utf-8"))["cases"]
    first = next(c for c in cases if c["id"] == "PN-http-503-1")
    follow = {**first, "id": "HTTP503-followup-dev", "query": "那能根据它自动重放生产写请求吗？"}
    selected = [first, follow,
                next(c for c in cases if c["expect_hit"] == "http_429_note.md"),
                next(c for c in cases if "dns" in c["expect_hit"].lower())]
    key, actor = secrets.token_urlsafe(32), "step6-generation-" + secrets.token_hex(6)
    create_api_key(key, actor, ["admin"])
    env = {**os.environ, "APP_ENV_FILE": str(ROOT / ".env.test"), "APP_ENV": "test",
           "AUTH_REQUIRED": "true", "SEMANTIC_CACHE_ENABLED": "false",
           "DOC_COLLECTION": "test_protocol_r1_doc", "FAQ_COLLECTION": "test_protocol_r1_faq",
           "LLM_API_KEY": private["LLM_API_KEY"], "LLM_MODEL": model, "LLM_BASE_URL": base,
           "HF_HUB_OFFLINE": "1", "TRANSFORMERS_OFFLINE": "1", "PYTHONIOENCODING": "utf-8",
           "STEP6_PROVIDER_LIMIT": str(args.paid_max), "STEP6_PROVIDER_LEDGER": str(output / "provider-ledger.json")}
    result = {"budget": args.paid_max, "model": model, "version": VERSION,
              "approval_receipt_sha256": receipt_sha, "independent_quality_evaluation": False,
              "token_usage": None, "p95_ms": None, "samples": []}
    if args.citations_only:
        result = json.loads(report.read_text(encoding="utf-8"))
        assert result["provider_attempts"] == len(json.loads((output / "provider-ledger.json").read_text())["attempts"])

    def read_citations(sample, http):
        # SSE sources is a separate frame; done carries only the final answer.
        sources = next((e["data"]["sources"] for e in sample["events"] if e["event"] == "sources"), [])
        sample["sources"], sample["citation_reads"] = sources, []
        for source in sources:
            link = source.get("source_url")
            if link:
                assert link.startswith("/kb/evidence/")
                read = http.get(link, headers={"X-API-Key": key})
                sample["citation_reads"].append({"source": source.get("source"), "http_status": read.status_code,
                    "body": read.json() if read.status_code == 200 else None})
        sample["expected_source_readable"] = any(r["source"] == sample["expected_source"] and r["http_status"] == 200 for r in sample["citation_reads"])
    server = None
    try:
        port = free_port()
        server = subprocess.Popen([sys.executable, "-B", "-m", "uvicorn",
            "scripts.verify_step6_generation:acceptance_app", "--factory", "--host", "127.0.0.1", "--port", str(port)],
            cwd=ROOT, env=env, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
        with httpx.Client(base_url=f"http://127.0.0.1:{port}", timeout=120, trust_env=False) as http:
            deadline = time.monotonic() + 45
            while time.monotonic() < deadline:
                try:
                    if http.get("/").status_code == 200:
                        break
                except httpx.TransportError:
                    pass
                if server.poll() is not None:
                    raise RuntimeError("isolated API exited before readiness")
                time.sleep(.2)
            else:
                raise RuntimeError("isolated API readiness timeout")
            if args.citations_only:
                for sample in result["samples"]:
                    read_citations(sample, http)
            for index, case in enumerate([] if args.citations_only else selected):
                sample = {"id": case["id"], "question": case["query"], "expected_source": case["expect_hit"],
                          "first_answer_token_ms": None, "events": [], "citation_reads": []}
                result["samples"].append(sample)
                started = time.perf_counter()
                prior = len(json.loads((output / "provider-ledger.json").read_text())["attempts"])
                label, data = None, []
                with http.stream("POST", "/qa/sse", headers={"X-API-Key": key},
                        json={"query": case["query"], "kb_version": VERSION,
                              "session_id": actor + ("-503" if index < 2 else f"-{index}")}) as response:
                    sample["http_status"] = response.status_code
                    response.raise_for_status()
                    for line in response.iter_lines():
                        if line.startswith("event:"):
                            label = line[6:].strip()
                        elif line.startswith("data:"):
                            data.append(line[5:].strip())
                        elif not line and label and data:
                            payload = json.loads("\n".join(data))
                            if label == "token" and sample["first_answer_token_ms"] is None:
                                sample["first_answer_token_ms"] = round((time.perf_counter() - started) * 1000, 1)
                            if label == "error":
                                payload = {"status": payload.get("status"), "code": payload.get("code"), "message": "omitted to avoid provider details"}
                            sample["events"].append({"event": label, "data": payload})
                            label, data = None, []
                sample["total_ms"] = round((time.perf_counter() - started) * 1000, 1)
                sample["provider_attempts"] = len(json.loads((output / "provider-ledger.json").read_text())["attempts"]) - prior
                final = next((e["data"] for e in sample["events"] if e["event"] == "done"), {})
                sample["final"] = final
                read_citations(sample, http)
                report.write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    except Exception as exc:
        result["runner_error_class"] = type(exc).__name__
    finally:
        if server:
            server.terminate()
            server.wait(timeout=15)
        with dao.engine.begin() as conn:
            conn.execute(sa.text("DELETE FROM api_keys WHERE key_hash=:h"), {"h": hash_key(key)})
        result["versions_unchanged"] = versions() == before
        ledger = output / "provider-ledger.json"
        result["provider_attempts"] = len(json.loads(ledger.read_text())["attempts"]) if ledger.exists() else 0
        assert result["provider_attempts"] <= args.paid_max
        result["transport_and_citations_passed"] = len(result["samples"]) == 4 and all(
            c.get("final", {}).get("status") == "ANSWERED" and c.get("expected_source_readable") for c in result["samples"])
        report.write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({k: result[k] for k in ("provider_attempts", "versions_unchanged", "transport_and_citations_passed")}))
    if not result["transport_and_citations_passed"] or not result["versions_unchanged"]:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
