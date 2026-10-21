from unittest.mock import Mock

import pytest

from scripts import run_eval


def payload():
    return {"cases": [{"id": label, "query": "q", "split": label}
                      for label in ("dev", "regression", "frozen")]}


def test_default_selects_only_dev():
    assert [c["id"] for c in run_eval.select_cases(payload())] == ["dev"]


@pytest.mark.parametrize("split", ["frozen", "all"])
def test_frozen_requires_explicit_final_acceptance(split):
    with pytest.raises(ValueError, match="final-acceptance"):
        run_eval.select_cases(payload(), split)
    assert run_eval.select_cases(payload(), split, final_acceptance=True)


def test_legacy_unlabeled_dataset_must_be_explicit_regression():
    data = {"cases": [{"id": "old", "query": "q"}]}
    with pytest.raises(ValueError, match="regression"):
        run_eval.select_cases(data)
    assert run_eval.select_cases(data, "regression") == data["cases"]


def test_cli_blocks_frozen_before_touching_storage(monkeypatch, tmp_path):
    import json
    file = tmp_path / "cases.json"
    file.write_text(json.dumps(payload()), encoding="utf-8")
    storage = Mock(side_effect=AssertionError("must not access storage"))
    monkeypatch.setattr(run_eval, "_assert_version_registered", storage)
    with pytest.raises(SystemExit) as exc:
        run_eval.main(["--dataset", str(file), "--split", "frozen"])
    assert exc.value.code == 2
    storage.assert_not_called()


def test_config_hash_is_repeatable_and_contains_no_secrets():
    from qa_core.application.qa_usecase import QaUseCase
    case = QaUseCase(searcher=object())
    first = run_eval._config_metadata(case)
    assert first["sha256"] == run_eval._config_metadata(case)["sha256"]
    assert "llm_api_key" not in first["settings"]
    case.settings = case.settings.model_copy(update={"evidence_min_coverage": 0.7})
    assert first["sha256"] != run_eval._config_metadata(case)["sha256"]


def test_no_llm_does_not_enable_generation_even_for_rejection_cases(monkeypatch, tmp_path):
    import json
    from qa_core.application.qa_usecase import NO_EVIDENCE, QaUseCase

    data = {"kb_version": "v-test", "cases": [{"id": "J1", "query": "q", "split": "dev",
            "category": "reject", "expect_route": "retrieval", "expect_reject": True}]}
    file = tmp_path / "cases.json"
    file.write_text(json.dumps(data), encoding="utf-8")
    case = QaUseCase(searcher=object())
    asked = Mock(return_value={"route": "retrieval", "answer": NO_EVIDENCE, "sources": [],
                                "diagnostics": {"degraded": False, "timings_ms": {"total": 1}}})
    monkeypatch.setattr(case, "ask", asked)
    monkeypatch.setattr(run_eval, "QaUseCase", lambda: case)
    monkeypatch.setattr(run_eval, "_assert_version_registered", lambda v: None)
    monkeypatch.setattr(run_eval, "_report_path", lambda *a: tmp_path / "report.json")
    monkeypatch.setattr(run_eval, "_git_metadata", lambda: {})
    with pytest.raises(SystemExit) as exc:
        run_eval.main(["--dataset", str(file), "--no-llm"])
    assert exc.value.code == 0
    assert asked.call_args.kwargs["use_llm"] is False
    assert asked.call_args.kwargs["persist"] is False
    report = json.loads((tmp_path / "report.json").read_text(encoding="utf-8"))
    assert report["complete"] is False
    assert report["run"]["llm_mode"] == "no_llm"
    assert len(report["run"]["dataset_sha256"]) == 64
