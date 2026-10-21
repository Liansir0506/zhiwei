"""Execute the explicitly approved, isolated first release and clean its temporary entry."""
import argparse
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import secrets
import subprocess
import sys
import tempfile
import time

import httpx
import sqlalchemy as sa
from qa_core.config.settings import get_settings
from qa_core.quality.publication import _digest
from scripts.prepare_legacy_projection import digest, read, write
from scripts.prepare_legacy_release import report_parent
from scripts.stage_legacy_candidate import configure, protected_snapshot, validate_base, validate_target
from scripts.verify_legacy_staged_preview import port
from scripts.migrate_legacy_collections import fingerprint, read_rows

APPROVED_PLAN_SHA256 = "448782b14ba1663e0b20b55c9fe95a28ca932d0fa5dc781a851fd8c953882e54"


def validate_plan(path, supplied_sha):
    if supplied_sha != APPROVED_PLAN_SHA256 or digest(path) != supplied_sha:
        raise ValueError("requires the exact plan approved in this chat; refusing drift")
    plan = read(path)
    if (plan.get("scope") != "isolated_local_demo_only" or plan.get("version") != "test-legacy-demo-r1"
            or plan.get("status_before") != "STAGED" or plan.get("active_before") is not None
            or plan.get("rollback_target") is not None or plan.get("published") is not False
            or plan.get("proposed_action") != {"endpoint": "/kb/activate", "method": "POST",
                                               "body": {"version": "test-legacy-demo-r1"}}):
        raise ValueError("approved action must remain the exact isolated first activation")
    return plan


def compare_plans(approved, fresh):
    if ({k: v for k, v in approved.items() if k != "prepared_at_utc"}
            != {k: v for k, v in fresh.items() if k != "prepared_at_utc"}):
        raise ValueError("fresh first-release preflight differs from the approved plan")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    for name in ("plan", "approved-plan-sha256", "stage-report", "report-dir"):
        parser.add_argument("--" + name, required=True)
    parser.add_argument("--execute", action="store_true")
    args = parser.parse_args()
    if not args.execute:
        raise ValueError("explicit --execute is required; this is a publication operation")
    plan = validate_plan(Path(args.plan), args.approved_plan_sha256)
    stage = Path(args.stage_report).resolve(); root = Path(args.report_dir).resolve()
    candidate = Path(read(stage / "environment.json")["candidate"]).resolve()
    if root.exists() or "test" not in str(root).lower() or root.is_relative_to(candidate):
        raise ValueError("requires new separate test execution artifacts")
    base = get_settings(); validate_base(base)
    root.mkdir(parents=True)
    write(root / "authorization.json", {"user_reply": "批准", "client_date": "2026-10-08",
        "basis": "explicit approval of the concrete first-release review page in this chat",
        "plan_sha256": args.approved_plan_sha256, "version": plan["version"], "database": plan["database"],
        "scope": plan["scope"], "original_or_shared_active_change_authorized": False,
        "withdraw_real_sources_authorized": False, "paid_llm_calls_authorized": False,
        "first_release_has_no_existing_rollback_target": True, "human_identity_authenticated": False})
    # Repeat the complete read-only preflight before making keys or starting an entry.
    run = subprocess.run([sys.executable, "-m", "scripts.prepare_legacy_release", "--stage-report", str(stage),
        "--report-dir", str(root / "preflight")], text=True, encoding="utf-8", capture_output=True, timeout=180)
    (root / "preflight.log").write_text(run.stdout + run.stderr, encoding="utf-8")
    if run.returncode:
        raise RuntimeError("fresh read-only preflight failed; publication not attempted")
    compare_plans(plan, read(root / "preflight/release-plan.json"))
    shared_before = protected_snapshot(base)
    settings = configure(base, stage, plan["database"])
    os.environ["REPORTS_DIR"] = str(report_parent(stage, plan["gate_report"]))
    get_settings.cache_clear(); settings = get_settings(); validate_target(settings, plan["database"])
    if (settings.doc_collection, settings.faq_collection) != (plan["doc_collection"], plan["faq_collection"]):
        raise ValueError("approved collection binding changed")
    engine = sa.create_engine(settings.mysql_dsn, pool_pre_ping=True)
    from qa_core.api.auth import create_api_key, hash_key
    from qa_core.cache.redis_cache import CacheLayer
    from pymilvus import MilvusClient
    keys, server, log, cache = {}, None, None, None
    address = "http://127.0.0.1:" + str(port())
    actor = "legacy-release-executor-" + secrets.token_hex(6)
    cache_sig = secrets.token_hex(12)
    refs = read(stage / "evidence-api.json")["samples"]
    result = None
    try:
        if len(refs) != 4:
            raise ValueError("four approved source group references required")
        for role in ("admin", "ops", "foreign"):
            key = secrets.token_urlsafe(24); keys[role] = key
            create_api_key(key, actor if role == "admin" else actor + "-" + role,
                           ["ops"] if role == "ops" else ["admin"], tenant_id="other" if role == "foreign" else "default")
        cache = CacheLayer(settings.redis_url)
        cache.set("ret", plan["version"], "release-check", cache_sig, {"probe": cache_sig})
        if cache.get("ret", plan["version"], "release-check", cache_sig) != {"probe": cache_sig}:
            raise ValueError("cache not available; refusing activation before commit")
        child = dict(os.environ, LLM_API_KEY="", OMP_NUM_THREADS="4", MKL_NUM_THREADS="4")
        log = (root / "server.log").open("w", encoding="utf-8")
        server = subprocess.Popen([sys.executable, "-m", "uvicorn", "app:app", "--host", "127.0.0.1",
            "--port", address.rsplit(":", 1)[1], "--lifespan", "off", "--log-level", "warning"], env=child,
            stdout=log, stderr=log, creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0)
        with httpx.Client(timeout=120, trust_env=False) as http:
            deadline = time.monotonic() + 25
            while time.monotonic() < deadline:
                try:
                    if http.get(address + "/", timeout=2).status_code == 200: break
                except httpx.TransportError: pass
                if server.poll() is not None: raise RuntimeError("temporary release server exited")
                time.sleep(.1)
            else: raise RuntimeError("temporary server did not start; no publication attempted")
            for role, expected in (("ops", 403), ("foreign", 403), (None, 401)):
                headers = {} if role is None else {"X-API-Key": keys[role], "X-User-Roles": "admin", "X-Tenant-ID": "default"}
                denied = http.post(address + "/kb/activate", json=plan["proposed_action"]["body"], headers=headers)
                if denied.status_code != expected: raise AssertionError("publication authentication refusal failed")
            staged = http.get(address + refs[0]["evidence"]["source_url"], headers={"X-API-Key": keys["ops"]})
            if staged.status_code != 404: raise AssertionError("ops unexpectedly previews STAGED")
            response = http.post(address + "/kb/activate", json=plan["proposed_action"]["body"], headers={"X-API-Key": keys["admin"]})
            write(root / "publication-response.json", {"status_code": response.status_code, "body": response.json(),
                                                       "received_at_utc": datetime.now(timezone.utc).isoformat()})
            if response.status_code != 200: raise AssertionError("publication response failed; inspect durable execution state")
            activated = response.json()
            if (activated.get("activated") != plan["version"] or activated.get("archived") is not None
                    or activated.get("cache_invalidation_ok") is not True
                    or cache.get("ret", plan["version"], "release-check", cache_sig) is not None):
                raise AssertionError("publication committed but cache or pointer result failed; stop temporary entry")
            checks = []
            for entry in refs:
                ref = entry["evidence"]
                statuses = {"no_key": http.get(address + ref["source_url"]).status_code}
                for role in ("admin", "ops", "foreign"):
                    got = http.get(address + ref["source_url"], headers={"X-API-Key": keys[role],
                        "X-User-Roles": "admin", "X-Tenant-ID": "default"})
                    statuses[role] = got.status_code
                    if role != "foreign" and (got.status_code != 200 or got.json() != ref):
                        raise AssertionError("ACTIVE source text/location/identity drift")
                if statuses != {"no_key": 401, "admin": 200, "ops": 200, "foreign": 404}:
                    raise AssertionError("ACTIVE evidence authentication boundary failed")
                checks.append({"doc_type": entry["doc_type"], "chunk_id": ref["chunk_id"], "statuses": statuses})
            write(root / "evidence-http.json", {"passed": True, "sources": checks})
            flow = http.post(address + "/kb/search-evidence", headers={"X-API-Key": keys["ops"]},
                json={"query": "ALM-AMF-REG SOP", "kb_version": plan["version"], "top_n": 5})
            if flow.status_code != 200 or flow.json().get("status") != "NO_EVIDENCE" or flow.json().get("evidence"):
                raise AssertionError("unsafe legacy workflow unexpectedly returned executable evidence")
            write(root / "workflow-http.json", {"status_code": flow.status_code, "result": flow.json(), "paid_llm_calls": 0})
        browser_root = root / "browser"; browser_root.mkdir()
        temp_root = Path(".runtime/test").resolve()
        with tempfile.TemporaryDirectory(prefix="legacy-active-profile-", dir=temp_root) as profile:
            if not Path(profile).resolve().is_relative_to(temp_root): raise ValueError("unsafe profile cleanup target")
            payload = {"edge": "C:/Program Files (x86)/Microsoft/Edge/Application/msedge.exe", "profile": profile,
                "debugPort": port(), "url": address, "key": keys["ops"], "version": plan["version"], "status": "ACTIVE",
                "sources": [{**e["evidence"], "doc_id": e["evidence"]["chunk_id"], "doc_type": e["doc_type"], "score": .8} for e in refs],
                "reportDir": str(browser_root)}
            browser = subprocess.run(["node", "tests/browser/legacy_staged_smoke.js"], input=json.dumps(payload),
                text=True, encoding="utf-8", capture_output=True, timeout=65)
            (browser_root / "browser.log").write_text(browser.stdout + browser.stderr, encoding="utf-8")
            if browser.returncode: raise AssertionError("ACTIVE ops browser verification failed; close temporary entry")
        client = MilvusClient(uri=settings.milvus_uri, timeout=120)
        try:
            docs = read_rows(client, settings.doc_collection); faqs = read_rows(client, settings.faq_collection)
            expected = read(stage / "network-label-repair/summary.json")
            if (fingerprint(docs) != expected["doc_fingerprint"] or fingerprint(faqs) != expected["after_faq_fingerprint"]):
                raise AssertionError("knowledge/vector/access data changed on publication")
            from qa_core.retrieval.filters import _to_doc
            from qa_core.retrieval.searcher import Searcher
            from qa_core.retrieval.workflow import assemble_sop
            seed_row = next(row for row in docs if row["id"] == refs[-1]["evidence"]["chunk_id"])
            seed = _to_doc({"id": seed_row["id"], "entity": seed_row})
            searcher = Searcher(client=client, embedder=object(), cache=object(), settings=settings)
            expanded = searcher.fetch_sop_source(seed, ["ops"], plan["version"])
            packet = assemble_sop(expanded, seed)
            if len(expanded) != 11 or packet.ready or packet.reason != "missing_safety_controls":
                raise AssertionError("full-source workflow refusal changed")
        finally: client.close()
        with engine.connect() as conn:
            versions = [dict(r) for r in conn.execute(sa.text("SELECT version,status FROM kb_versions ORDER BY version")).mappings()]
            audit = [dict(r) for r in conn.execute(sa.text("SELECT action,actor,payload_json FROM audit_log WHERE action='activate'")).mappings()]
            withdrawals = conn.execute(sa.text("SELECT COUNT(*) FROM knowledge_source_withdrawals")).scalar_one()
        if versions != [{"version": plan["version"], "status": "ACTIVE"}] or len(audit) != 1 or withdrawals:
            raise AssertionError("durable pointer/audit/withdrawal state differs from approved action")
        audit_payload = audit[0]["payload_json"]
        if isinstance(audit_payload, str): audit_payload = json.loads(audit_payload)
        if (audit[0]["actor"] != actor or audit_payload.get("from") is not None
                or audit_payload.get("to") != plan["version"] or _digest(audit_payload["snapshot"]) != plan["snapshot_sha256"]):
            raise AssertionError("activation audit did not bind authenticated executor and approved snapshot")
        shared_after = protected_snapshot(base)
        if shared_after != shared_before: raise AssertionError("shared test default knowledge changed")
        write(root / "audit.json", audit)
        result = {"passed": True, "version": plan["version"], "database": plan["database"], "status": "ACTIVE",
            "published": True, "files": 256, "chunks": 4390, "scope": plan["scope"], "plan_sha256": args.approved_plan_sha256,
            "gate_and_plan_reverified": True, "authenticated_ops_four_sources": True, "workflow_safety_refusal_preserved": True,
            "workflow_http_reason": flow.json().get("reason"), "browser_verified": True, "cache_invalidation_ok": True,
            "authenticated_executor_audit_verified": True, "candidate_knowledge_vectors_access_unchanged": True,
            "shared_test_knowledge_unchanged": True, "original_storage_accessed": False, "withdrawn_sources": 0,
            "rollback_target": None, "paid_llm_calls": 0, "candidate_passage_embeddings": 0,
            "independent_quality_verified": False, "pending_reference_files": 20}
    finally:
        if server:
            server.terminate()
            try: server.wait(timeout=15)
            except subprocess.TimeoutExpired: server.kill(); server.wait(timeout=5)
        if log: log.close()
        with engine.begin() as conn:
            for key in keys.values(): conn.execute(sa.text("DELETE FROM api_keys WHERE key_hash=:h"), {"h": hash_key(key)})
        if cache and cache.client is not None:
            cache.client.delete(cache._key("ret", plan["version"], "release-check", cache_sig))
        with engine.connect() as conn:
            final_versions = [dict(r) for r in conn.execute(sa.text("SELECT version,status FROM kb_versions ORDER BY version")).mappings()]
            enabled = conn.execute(sa.text("SELECT COUNT(*) FROM api_keys WHERE enabled=1")).scalar_one()
        write(root / "execution-state.json", {"versions": final_versions, "enabled_keys": enabled,
            "temporary_server_stopped": server is None or server.poll() is not None,
            "publication_committed": any(r["status"] == "ACTIVE" for r in final_versions),
            "automatic_version_rollback_performed": False, "no_existing_rollback_target": True})
        engine.dispose()
    if not result or enabled: raise AssertionError("execution incomplete or temporary keys remain")
    write(root / "summary.json", {**result, "temporary_server_stopped": True, "temporary_keys_removed": True,
                                   "temporary_browser_profile_removed": True, "llm_key_available_to_server": False})
    print(json.dumps({"passed": True, "status": "ACTIVE", "database": plan["database"], "temporary_entry_closed": True}))


if __name__ == "__main__":
    main()
