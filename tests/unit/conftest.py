"""Unit tests use an explicit in-memory withdrawal ledger, never a live MySQL DB.

Tests of the real ledger/transaction belong to tests/integration. Individual unit
tests may override entries to inject withdrawal, outage or recheck behavior.
"""
import pytest

from qa_core.security.withdrawals import WithdrawalPolicy


@pytest.fixture(autouse=True)
def unit_withdrawal_ledger(monkeypatch):
    # Paid local opt-in and its live counter never affect unit-test defaults.
    from scripts import original_local_runtime
    monkeypatch.setattr(original_local_runtime, '_generation_settings', lambda base, *args: base)
    monkeypatch.delenv('LLM_VALIDATION_BUDGET_FILE', raising=False)
    ledger = {}
    monkeypatch.setattr(WithdrawalPolicy, "entries", lambda owner, tenant: set(ledger.get(tenant, set())))
    return ledger
