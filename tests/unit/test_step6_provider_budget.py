"""No network: the acceptance harness must enforce the approved paid ceiling."""
from concurrent.futures import ThreadPoolExecutor
import json
import httpx
import pytest
from scripts.verify_step6_generation import ProviderBudget


def request():
    return httpx.Request("POST", "https://api.deepseek.com/v1/chat/completions",
                         headers={"Authorization": "Bearer must-not-be-recorded"}, content=b"private query")


def test_budget_survives_reopening_and_counts_retry_attempts(tmp_path):
    path = tmp_path / "ledger.json"
    guard = ProviderBudget(path, 4)
    for _ in range(3):
        guard(request())
    reopened = ProviderBudget(path, 4)
    reopened(request())
    with pytest.raises(RuntimeError, match="budget exhausted"):
        reopened(request())
    saved = json.loads(path.read_text())
    assert len(saved["attempts"]) == 4
    assert "must-not-be-recorded" not in path.read_text() and "private query" not in path.read_text()


def test_concurrent_attempts_cannot_exceed_budget(tmp_path):
    guard = ProviderBudget(tmp_path / "ledger.json", 4)
    def attempt(_):
        try:
            guard(request())
            return True
        except RuntimeError:
            return False
    with ThreadPoolExecutor(max_workers=8) as pool:
        assert sum(pool.map(attempt, range(12))) == 4


def test_unapproved_endpoint_cannot_consume_budget(tmp_path):
    path = tmp_path / "ledger.json"
    guard = ProviderBudget(path, 4)
    with pytest.raises(AssertionError):
        guard(httpx.Request("POST", "https://example.com/chat/completions"))
    assert json.loads(path.read_text())["attempts"] == []
