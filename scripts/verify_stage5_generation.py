"""One bounded real DeepSeek smoke on approved STAGED test notes; no publication."""
from __future__ import annotations

import argparse
import hashlib
import json
import time
from pathlib import Path

from qa_core.application.qa_usecase import QaUseCase
from qa_core.config.environment import validate_isolated_environment
from qa_core.config.settings import get_settings
from qa_core.quality.eval_review import validate_export


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dataset", type=Path, required=True)
    parser.add_argument("--report", type=Path, required=True)
    args = parser.parse_args(argv)
    if args.report.exists(): raise ValueError("refuse to overwrite smoke report")
    s = get_settings()
    problems = validate_isolated_environment(s, "test")
    if (problems or s.doc_collection != "test_protocol_r1_doc"
            or s.faq_collection != "test_protocol_r1_faq"):
        raise ValueError("requires approved isolated protocol profile")
    if not s.llm_api_key or not s.llm_base_url.startswith("https://api.deepseek.com/"):
        raise ValueError("DeepSeek key or approved API endpoint unavailable")
    payload = json.loads(args.dataset.read_text(encoding="utf-8"))
    validate_export(payload)
    case = next(c for c in payload["cases"] if c["id"] == "PN-http-503-1")
    if payload["kb_version"] != "v4-protocol-dev-r1":
        raise ValueError("unexpected knowledge version")
    report = {"purpose": "one_real_model_development_smoke_not_quality_acceptance",
              "dataset_sha256": hashlib.sha256(args.dataset.read_bytes()).hexdigest(),
              "case_id": case["id"], "query": case["query"],
              "kb_version": payload["kb_version"], "model": s.llm_model,
              "model_key_present": True, "token_usage": None,
              "persist_session": False, "published": False,
              "events": [], "answer": None, "status": None, "sources": [],
              "first_answer_token_ms": None, "total_ms": None,
              "error_kind": None}
    t0 = time.perf_counter()
    try:
        uc = QaUseCase()
        for event in uc.ask_stream(case["query"], roles=["ops"],
                                   session_id="stage5-once-" + hashlib.sha256(
                                       args.dataset.read_bytes()).hexdigest()[:12],
                                   tenant_id="default", kb_version=payload["kb_version"],
                                   persist=False):
            kind, data = event["event"], event.get("data") or {}
            report["events"].append(kind)
            if kind == "delta" and report["first_answer_token_ms"] is None:
                report["first_answer_token_ms"] = round((time.perf_counter() - t0) * 1000, 1)
            if kind == "sources": report["sources"] = [x.get("source") for x in data.get("sources", [])]
            if kind == "end":
                report["answer"] = data.get("answer")
                report["status"] = data.get("status")
                report["degraded"] = data.get("degraded")
                report["model_generation_ms"] = (data.get("retrieval") or {}).get("generation_ms")
            if kind == "error":
                report["status"] = "FAILED"
                report["error_kind"] = data.get("code")
    except Exception as exc:
        # Never serialize exception text: HTTP/proxy errors can contain private URLs.
        report["status"] = "FAILED"
        report["error_kind"] = type(exc).__name__
    finally:
        report["total_ms"] = round((time.perf_counter() - t0) * 1000, 1)
        args.report.parent.mkdir(parents=True, exist_ok=True)
        args.report.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps({k: report[k] for k in ("case_id", "status", "degraded",
          "first_answer_token_ms", "total_ms", "sources", "error_kind") if k in report}, ensure_ascii=False))


if __name__ == "__main__":
    main()
