from unittest.mock import Mock
import pytest
import psutil
from qa_core.config.settings import Settings
from scripts import legacy_demo_runtime as runtime


def test_profile_ignores_inherited_production_and_paid_settings(monkeypatch):
    for key, value in {"MYSQL_DSN": "mysql+pymysql://bad:secret@original:3306/rag_kb", "AUTH_REQUIRED": "false",
                       "GENERATION_ENABLED": "true", "LLM_API_KEY": "do-not-use", "APP_ENV": "prod"}.items():
        monkeypatch.setenv(key, value)
    profile = runtime.build_profile(18011)
    settings = Settings(_env_file=None, **profile)
    runtime.validate_profile(settings)
    env = runtime.profile_environment(profile)
    assert env["GENERATION_ENABLED"] == "false" and env["LLM_API_KEY"] == ""
    assert env["APP_ENV"] == "test" and settings.doc_collection == runtime.DATABASE + "_doc"


@pytest.mark.parametrize("field,value", [("generation_enabled", True), ("auth_required", False),
    ("workflow_evidence_enabled", False), ("semantic_cache_enabled", True), ("llm_api_key", "secret"),
    ("llm_base_url", "https://api.deepseek.com/v1"), ("api_host", "0.0.0.0"),
    ("redis_url", "redis://127.0.0.1:16379/0"), ("doc_collection", "doc_collection"),
    ("data_dir", "data"), ("embedding_device", "cuda"), ("api_port", 8010)])
def test_profile_drift_fails_closed(field, value):
    profile = runtime.build_profile(18011); profile[field] = value
    with pytest.raises(ValueError):
        runtime.validate_profile(Settings(_env_file=None, **profile))


@pytest.mark.parametrize("port", [0, 8010, 18010, 18020, 65536])
def test_original_and_shared_ports_cannot_be_reused(port):
    with pytest.raises(ValueError):
        runtime.build_profile(port)


@pytest.mark.parametrize("change", ["time", "module", "instance", "profile"])
def test_reused_or_unowned_pid_is_never_returned(monkeypatch, change):
    state = {"pid": 345, "created_at": 100., "instance": "owned"}
    process = Mock()
    process.is_running.return_value = True; process.status.return_value = "running"
    process.create_time.return_value = 101. if change == "time" else 100.
    process.cmdline.return_value = ["python", "-m", "other" if change == "module" else runtime.SERVER_MODULE,
        "--instance", "foreign" if change == "instance" else "owned", "--profile",
        "other" if change == "profile" else str(runtime.ROOT / "profile.json")]
    monkeypatch.setattr(runtime.psutil, "Process", lambda pid: process)
    with pytest.raises(ValueError, match="different process"):
        runtime.owned_process(state)
    process.terminate.assert_not_called(); process.kill.assert_not_called()


def test_gone_process_can_be_cleaned_without_killing(monkeypatch):
    def missing(pid): raise psutil.NoSuchProcess(pid)
    monkeypatch.setattr(runtime.psutil, "Process", missing)
    assert runtime.owned_process({"pid": 345}) is None


def test_stop_cleans_owned_windows_child_without_killing_other_descendant(monkeypatch, tmp_path):
    monkeypatch.setattr(runtime, "ROOT", tmp_path)
    monkeypatch.setattr(runtime, "state_profile", Mock())
    state = {"pid": 1, "instance": "owned", "key_hash": "h", "actor": "actor"}
    root, child, other = Mock(pid=1), Mock(pid=2), Mock(pid=3)
    root.children.return_value = [child, other]
    child.create_time.return_value = other.create_time.return_value = 100.
    def owned(identity):
        if identity["pid"] == 3: raise ValueError("unowned worker")
        return {1: root, 2: child}[identity["pid"]]
    monkeypatch.setattr(runtime, "owned_process", owned)
    revoke = Mock(); monkeypatch.setattr(runtime, "revoke_key", revoke)
    (tmp_path / "api-key.txt").write_text("secret")
    stopped = runtime.stop(state, Mock())
    root.terminate.assert_called_once(); child.terminate.assert_called_once()
    other.terminate.assert_not_called(); other.kill.assert_not_called()
    assert stopped["owned_key_deleted"] and not (tmp_path / "api-key.txt").exists()
    revoke.assert_called_once()


def test_failed_key_revoke_keeps_recovery_record_and_key_file(monkeypatch, tmp_path):
    monkeypatch.setattr(runtime, "ROOT", tmp_path)
    monkeypatch.setattr(runtime, "state_profile", Mock())
    monkeypatch.setattr(runtime, "owned_process", lambda state: None)
    monkeypatch.setattr(runtime, "revoke_key", Mock(side_effect=RuntimeError("storage unavailable")))
    state = {"pid": 1, "instance": "owned", "key_hash": "h", "actor": "actor", "status": "running"}
    runtime.write(tmp_path / "state.json", state)
    (tmp_path / "api-key.txt").write_text("secret")
    with pytest.raises(RuntimeError): runtime.stop(state, Mock())
    assert runtime.read(tmp_path / "state.json") == state
    assert (tmp_path / "api-key.txt").read_text() == "secret"
