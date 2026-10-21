"""Start, inspect and stop the approved local demo; never publish or ingest."""
import argparse
from contextlib import contextmanager
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import secrets
import socket
import subprocess
import sys
import time

import httpx
import psutil
import sqlalchemy as sa
from dotenv import dotenv_values
from qa_core.config.settings import Settings, get_settings
from scripts.execute_legacy_release import APPROVED_PLAN_SHA256, validate_plan
from scripts.prepare_legacy_projection import digest, read
from scripts.stage_legacy_candidate import validate_base, validate_target

PROJECT = Path(__file__).resolve().parents[1]
ROOT = PROJECT / ".runtime/test/legacy-demo"
PLAN = PROJECT / ".runtime/test/reports/legacy-release-readiness-20261008/readiness/release-plan.json"
DATABASE = "test_legacy_stage_fb36d947ab67"
VERSION = "test-legacy-demo-r1"
SERVER_MODULE = "scripts.legacy_demo_server"


def write(path, value):
    path = Path(path)
    temp = path.with_suffix(path.suffix + ".tmp")
    temp.write_text(json.dumps(value, ensure_ascii=False, indent=2), encoding="utf-8")
    temp.replace(path)


def build_profile(port):
    if not 1024 <= port <= 65535 or port in {8010, 18010, 18020}:
        raise ValueError("choose a separate local demo port (default 18011)")
    # Constructor values for ALL fields prevent inherited shell/.env overrides.
    values = {name: field.default for name, field in Settings.model_fields.items()}
    values.update({k.lower(): v for k, v in dotenv_values(PROJECT / ".env.test").items()
                   if k.lower() in values and v is not None})
    base = Settings(_env_file=None, **values)
    validate_base(base)
    plan = validate_plan(PLAN, APPROVED_PLAN_SHA256)
    if (plan["database"], plan["version"], plan["doc_collection"], plan["faq_collection"]) != (
            DATABASE, VERSION, DATABASE + "_doc", DATABASE + "_faq"):
        raise ValueError("approved storage binding changed")
    values = base.model_dump()
    for name in ("models_dir", "embedding_model_path", "reranker_model_path", "intent_model_path"):
        # Absolute embedding path spelling is part of the immutable read binding.
        if not Path(values[name]).is_absolute():
            values[name] = str((PROJECT / values[name]).resolve())
    values.update(mysql_dsn=sa.engine.make_url(base.mysql_dsn).set(database=DATABASE).render_as_string(hide_password=False),
                  doc_collection=DATABASE + "_doc", faq_collection=DATABASE + "_faq",
                  data_dir=str(ROOT / "test-data"), reports_dir=str(Path(plan["gate_report"]).parent),
                  api_host="127.0.0.1", api_port=port, auth_required=True, rate_limit_qps=100,
                  semantic_cache_enabled=False, workflow_evidence_enabled=True,
                  generation_enabled=False, agent_enabled=True, llm_api_key="", llm_base_url="http://127.0.0.1:1/disabled",
                  embedding_device="cpu", rerank_device="cpu")
    settings = Settings(_env_file=None, **values)
    validate_profile(settings)
    return values


def validate_profile(settings):
    validate_target(settings, DATABASE)
    if (settings.generation_enabled or settings.llm_api_key or settings.semantic_cache_enabled
            or not settings.workflow_evidence_enabled or settings.api_host != "127.0.0.1"
            or settings.llm_base_url != "http://127.0.0.1:1/disabled"
            or settings.redis_url != "redis://127.0.0.1:16379/15"
            or Path(settings.data_dir).resolve() != ROOT / "test-data"
            or settings.embedding_device != "cpu" or settings.rerank_device != "cpu"):
        raise ValueError("local demo must keep citation mode, authentication and isolated storage")
    if settings.api_port in {8010, 18010, 18020} or not 1024 <= settings.api_port <= 65535:
        raise ValueError("requires a separate local demo port")


def profile_environment(profile):
    env = dict(os.environ)
    for name in Settings.model_fields:
        env.pop(name.upper(), None)
    env.update({name.upper(): str(value).lower() if isinstance(value, bool) else str(value)
                for name, value in profile.items()})
    env.update(APP_ENV_FILE=os.devnull, TOKENIZERS_PARALLELISM="false", OMP_NUM_THREADS="4", MKL_NUM_THREADS="4")
    return env


def apply_profile(profile):
    settings = Settings(_env_file=None, **profile)
    validate_profile(settings)
    os.environ.update(profile_environment(profile))
    get_settings.cache_clear()
    return get_settings()


def owned_process(state):
    """Fail closed on reused PIDs or modified launch identity; never kill by port."""
    if not state.get("pid"):
        return None
    try:
        process = psutil.Process(state["pid"])
        if not process.is_running() or process.status() == psutil.STATUS_ZOMBIE:
            return None
        command = process.cmdline()
        def argument(name):
            return command[command.index(name) + 1] if name in command and command.index(name) + 1 < len(command) else None
        if (abs(process.create_time() - state["created_at"]) > .01 or SERVER_MODULE not in command
                or argument("--instance") != state["instance"]
                or argument("--profile") != str(ROOT / "profile.json")):
            raise ValueError("PID belongs to a different process; refusing to stop it")
        return process
    except psutil.NoSuchProcess:
        return None


def load_state():
    return read(ROOT / "state.json") if (ROOT / "state.json").exists() else None


def state_profile(state):
    path = ROOT / "profile.json"
    if digest(path) != state["profile_sha256"]:
        raise ValueError("runtime profile changed; refusing a different storage target")
    return apply_profile(read(path))


def inspect(state):
    if not state or state.get("status") == "stopped":
        return {"status": "stopped", "version": VERSION, "generation_enabled": False}
    state_profile(state)
    process = owned_process(state)
    if not process:
        return {"status": "stale", "version": VERSION, "cleanup_required": True}
    response = httpx.get(state["url"] + "/demo/runtime", timeout=5, trust_env=False)
    response.raise_for_status()
    if response.json() != {"instance": state["instance"], "version": VERSION, "generation_enabled": False}:
        raise ValueError("HTTP entry does not belong to this runtime")
    key_path = ROOT / "api-key.txt"
    if not key_path.is_file():
        raise ValueError("runtime key file missing; stop and restart the owned entry")
    key = key_path.read_text(encoding="utf-8")
    if hashlib.sha256(key.encode()).hexdigest() != state["key_hash"]:
        raise ValueError("runtime key file does not match this instance")
    versions = httpx.get(state["url"] + "/kb/versions", headers={"X-API-Key": key}, timeout=5, trust_env=False)
    versions.raise_for_status()
    active = [v["version"] for v in versions.json()["versions"] if v["status"] == "ACTIVE"]
    if active != [VERSION]:
        raise ValueError("runtime no longer reads the approved ACTIVE version")
    return {"status": "running", "url": state["url"], "pid": process.pid, "version": VERSION,
            "generation_enabled": False, "key_file": str(ROOT / "api-key.txt"), "profile": str(ROOT / "profile.json")}


def revoke_key(engine, state):
    if not state.get("key_hash"):
        return
    with engine.begin() as conn:
        conn.execute(sa.text("DELETE FROM api_keys WHERE key_hash=:h AND user_id=:u AND tenant_id='default'"),
                     {"h": state["key_hash"], "u": state["actor"]})


def stop(state, engine):
    if not state or state.get("status") == "stopped":
        return inspect(None)
    state_profile(state)
    process = owned_process(state)
    if process:
        # Windows venv python.exe is a redirector; the real API runs in its child.
        # Only terminate descendants carrying our exact module/token/profile.
        children = []
        for child in process.children(recursive=True):
            try:
                child_state = dict(state, pid=child.pid, created_at=child.create_time())
                if owned_process(child_state):
                    children.append(child_state)
            except (ValueError, psutil.NoSuchProcess):
                continue
        for identity in [*reversed(children), state]:
            target = owned_process(identity)
            if not target:
                continue
            try:
                target.terminate()
            except psutil.NoSuchProcess:
                continue
            try:
                target.wait(timeout=30)
            except psutil.TimeoutExpired:
                target = owned_process(identity)
                if target:
                    target.kill(); target.wait(timeout=10)
    # Durable deletion must succeed before deleting the only recovery record.
    revoke_key(engine, state)
    (ROOT / "api-key.txt").unlink(missing_ok=True)
    stopped = {"status": "stopped", "version": VERSION, "generation_enabled": False,
               "stopped_at_utc": datetime.now(timezone.utc).isoformat(),
               "instance": state["instance"], "server_stopped": True, "owned_key_deleted": True}
    write(ROOT / "state.json", stopped)
    return stopped


def verify_active(settings):
    from qa_core.quality.publication import verify_publication
    plan = validate_plan(PLAN, APPROVED_PLAN_SHA256)
    if digest(Path(plan["gate_report"])) != plan["gate_report_sha256"]:
        raise ValueError("approved gate report changed")
    engine = sa.create_engine(settings.mysql_dsn, pool_pre_ping=True)
    try:
        with engine.connect() as conn:
            versions = [dict(r) for r in conn.execute(sa.text("SELECT version,status FROM kb_versions ORDER BY version")).mappings()]
            if versions != [{"version": VERSION, "status": "ACTIVE"}]:
                raise ValueError("requires the already-published sole ACTIVE; never creates or publishes a version")
            report = conn.execute(sa.text("SELECT quality_report_path FROM kb_versions WHERE version=:v"), {"v": VERSION}).scalar_one()
            jobs = conn.execute(sa.text("SELECT COUNT(*) FROM ingest_jobs WHERE status NOT IN ('success','failed','cancelled')")).scalar_one()
            if jobs or report != plan["gate_report"]:
                raise ValueError("unfinished ingestion or changed approved gate binding")
        verify_publication(settings, VERSION, report)
    finally:
        engine.dispose()


def start(profile, engine):
    previous = load_state()
    if previous and previous.get("status") != "stopped":
        existing = inspect(previous)
        if existing["status"] == "running":
            if profile["api_port"] != int(previous["url"].rsplit(":", 1)[1]):
                raise ValueError("already running on another port; stop it before changing the port")
            return existing
        stop(previous, engine)
    settings = apply_profile(profile)
    # Reject port conflicts before creating credentials or writing runtime config.
    with socket.socket() as probe:
        probe.bind(("127.0.0.1", profile["api_port"]))
    verify_active(settings)
    write(ROOT / "profile.json", profile)
    instance = secrets.token_hex(16)
    key = secrets.token_urlsafe(32)
    actor = "legacy-demo-ops-" + instance
    from qa_core.api.auth import create_api_key, hash_key
    state = {"status": "starting", "instance": instance, "actor": actor, "key_hash": hash_key(key),
             "profile_sha256": digest(ROOT / "profile.json"), "pid": None,
             "url": "http://127.0.0.1:" + str(profile["api_port"])}
    write(ROOT / "state.json", state)
    server = None
    try:
        (ROOT / "api-key.txt").write_text(key, encoding="utf-8")
        create_api_key(key, actor, ["ops"], tenant_id="default")
        options = {"creationflags": subprocess.CREATE_NO_WINDOW | subprocess.DETACHED_PROCESS} if os.name == "nt" else {"start_new_session": True}
        with (ROOT / "server.log").open("a", encoding="utf-8") as log:
            server = subprocess.Popen([sys.executable, "-m", SERVER_MODULE, "--instance", instance,
                "--profile", str(ROOT / "profile.json")], cwd=PROJECT, env=profile_environment(profile),
                stdin=subprocess.DEVNULL, stdout=log, stderr=log, **options)
        state.update(pid=server.pid, created_at=psutil.Process(server.pid).create_time())
        write(ROOT / "state.json", state)
        deadline = time.monotonic() + 120
        while time.monotonic() < deadline:
            if server.poll() is not None:
                raise RuntimeError("demo entry exited; inspect the local server.log")
            try:
                result = inspect(state)
                health = httpx.get(state["url"] + "/health", timeout=5, trust_env=False).json()
                versions = httpx.get(state["url"] + "/kb/versions", headers={"X-API-Key": key}, timeout=5, trust_env=False)
                versions.raise_for_status()
                if health["status"] != "ok" or health["generation_enabled"] is not False:
                    raise RuntimeError("demo dependencies are not healthy or citation mode is disabled")
                if not any(v["version"] == VERSION and v["status"] == "ACTIVE" for v in versions.json()["versions"]):
                    raise RuntimeError("authenticated ACTIVE binding mismatch")
                state["status"] = "running"
                write(ROOT / "state.json", state)
                return result
            except httpx.TransportError:
                time.sleep(.4)
        raise RuntimeError("demo entry did not become ready within 120 seconds")
    except BaseException:
        if server and not state.get("pid"):
            server.terminate(); server.wait(timeout=10)
        stop(state, engine)
        raise


@contextmanager
def command_lock(engine):
    with engine.connect() as conn:
        name = "legacy_demo_runtime_fb36d947ab67"
        if conn.execute(sa.text("SELECT GET_LOCK(:name,0)"), {"name": name}).scalar_one() != 1:
            raise ValueError("another demo lifecycle command is running")
        try:
            yield
        finally:
            conn.execute(sa.text("SELECT RELEASE_LOCK(:name)"), {"name": name})


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", choices=("start", "status", "stop"))
    parser.add_argument("--port", type=int, default=18011)
    args = parser.parse_args()
    ROOT.mkdir(parents=True, exist_ok=True)
    if args.action == "status":
        print(json.dumps(inspect(load_state()), ensure_ascii=False, indent=2)); return
    state = load_state()
    if args.action == "stop" and (not state or state.get("status") == "stopped"):
        print(json.dumps(inspect(None), ensure_ascii=False, indent=2)); return
    profile = build_profile(args.port) if args.action == "start" else read(ROOT / "profile.json")
    settings = apply_profile(profile)
    engine = sa.create_engine(settings.mysql_dsn, pool_pre_ping=True)
    try:
        with command_lock(engine):
            result = start(profile, engine) if args.action == "start" else stop(load_state(), engine)
        print(json.dumps(result, ensure_ascii=False, indent=2))
    finally:
        engine.dispose()


if __name__ == "__main__":
    try:
        main()
    except Exception as error:
        # DSNs/keys may occur in underlying driver exceptions; keep stdout/log safe.
        print("Legacy demo command failed (" + type(error).__name__ + "). Check storage readiness, port and runtime state.", file=sys.stderr)
        sys.exit(1)
