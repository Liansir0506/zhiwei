import threading
import time
import uuid

from qa_core.intent import model_classifier as mod


def model_path():
    return "fake-model-" + uuid.uuid4().hex


def install_model(monkeypatch, *, load_gate=None, predict_gate=None):
    class Fake:
        labels = ["FAQ_QUERY", "KNOWLEDGE_QUERY", "FOLLOW_UP"]

        def __init__(self, *args):
            if load_gate:
                assert load_gate.wait(3)

        def predict(self, query):
            if predict_gate:
                assert predict_gate.wait(3)
            return {"faq": 0.8, "knowledge": 0.1, "follow_up": 0.1}
    monkeypatch.setattr(mod, "IntentModelClassifier", Fake)


def wait_ready(path):
    deadline = time.monotonic() + 3
    while time.monotonic() < deadline:
        if mod.readiness(path)["ready"]:
            # 完成回调需先归还执行槽。
            if mod._slot.acquire(blocking=False):
                mod._slot.release()
                return
        time.sleep(0.005)
    raise AssertionError("model did not become ready")


def test_cold_load_has_bounded_wait_and_no_unbounded_queue(monkeypatch):
    path, gate = model_path(), threading.Event()
    install_model(monkeypatch, load_gate=gate)
    try:
        start = time.monotonic()
        first = mod.classify("query", path, timeout_ms=30)
        assert time.monotonic() - start < 0.4
        assert "cold_start_timeout" in first.reason
        assert mod.readiness(path)["status"] == "loading"
        second = mod.classify("query", path, timeout_ms=30)
        assert "busy" in second.reason
    finally:
        gate.set()
        wait_ready(path)
    assert mod.classify("query", path, timeout_ms=500).source == "model"


def test_inference_timeout_returns_without_waiting_for_thread(monkeypatch):
    path = model_path()
    install_model(monkeypatch)
    assert mod.warmup(path).result(timeout=2)
    gate = threading.Event()
    mod._state_for(path, "cpu")["model"].predict = lambda q: gate.wait(3) and {"knowledge": 1.0}
    try:
        start = time.monotonic()
        result = mod.classify("query", path, timeout_ms=30)
        assert time.monotonic() - start < 0.4
        assert "inference_timeout" in result.reason
    finally:
        gate.set()
        wait_ready(path)


def test_failed_path_does_not_poison_another_path(monkeypatch):
    def fail(*args):
        raise FileNotFoundError("fake")
    monkeypatch.setattr(mod, "IntentModelClassifier", fail)
    broken = model_path()
    assert "load_failed:FileNotFoundError" in mod.classify("q", broken, timeout_ms=500).reason
    assert mod.readiness(broken)["status"] == "failed"
    install_model(monkeypatch)
    working = model_path()
    assert mod.classify("q", working, timeout_ms=500).source == "model"
    assert mod.readiness(working)["ready"]


def test_explicit_warmup_really_predicts(monkeypatch):
    path = model_path()
    install_model(monkeypatch)
    assert mod.readiness(path)["status"] == "not_loaded"
    assert mod.warmup(path).result(timeout=2)
    assert mod.readiness(path) == {"status": "ready", "ready": True, "error_type": None}
