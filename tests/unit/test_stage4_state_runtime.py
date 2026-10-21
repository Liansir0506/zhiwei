"""Real use case/Searcher/workflow with in-memory storage, not Milvus or LLM quality eval."""
import json
import re
from pathlib import Path
from unittest.mock import Mock

import pytest

from qa_core.application.qa_usecase import QaUseCase
from qa_core.indexing.chunkers.registry import chunk_sop
from qa_core.retrieval.searcher import Searcher
from qa_core.security.withdrawals import WithdrawalPolicy
from qa_core.storage.kb_dao import KbDao
from scripts.review_core_semantics import validate
from qa_core.retrieval.workflow import applicable, missing_context, is_workflow_query

ROOT = Path(__file__).resolve().parents[2]
CANDIDATE = ROOT / "data/candidates/v4-quality-r1-final"
PACKAGE = ROOT / "data/candidates/stage4-semantic-review-r2"
PATHS = json.loads((PACKAGE / "state-paths.json").read_text(encoding="utf-8"))["cases"]
POSITIVES = json.loads((PACKAGE / "semantic-workbook.json").read_text(encoding="utf-8"))["cases"]


class MemoryClient:
    """Only the equality filters used by these public/internal fixtures are simulated.

    This does not validate Milvus expression evaluation or arbitrary RBAC/ABAC.
    """
    def __init__(self, rows):
        self.rows, self.filters = rows, []

    def describe_collection(self, **kwargs):
        return {"fields": [{"name": name} for name in self.rows[0]]}

    def selected(self, expression):
        self.filters.append(expression)
        conditions = dict(re.findall(r'(tenant_id|kb_version|source_version|source|asset_id) == "([^"]+)"', expression))
        assert 'security_level in ["internal", "public"]' in expression
        assert 'status == "active"' in expression
        assert 'doc_type in ["sop"]' in expression
        return [row for row in self.rows if all(row.get(key) == value for key, value in conditions.items())]

    def search(self, **kwargs):
        return [[{"id": row["id"], "entity": row, "distance": .9}
                 for row in self.selected(kwargs["filter"])][:kwargs["limit"]]]

    def query(self, **kwargs):
        return self.selected(kwargs["filter"])[:kwargs["limit"]]


class EmptyCache:
    def get(self, *a): return None
    def get_with_lock(self, *a): return None
    def set(self, *a): pass


def setup_usecase(monkeypatch, case, *, empty=False):
    metadata = json.loads((CANDIDATE / "raw/meta.json").read_text(encoding="utf-8"))
    parent = next(c for c in POSITIVES if c["id"] == case.get("parent_case_id", case["id"]))
    rows = []
    for filename in [parent["positive_source"], *parent["condition_negatives"]]:
        meta = metadata[filename]
        for index, chunk in enumerate(chunk_sop((CANDIDATE / "raw" / filename).read_text(encoding="utf-8"), filename)):
            rows.append({**meta, **chunk.metadata, "id": f"memory:{filename}:{index}", "text": chunk.text,
                         "asset_id": filename, "kb_version": "stage4-memory", "source_version": "stage4-memory"})
    llm = Mock()
    llm.chat.side_effect = AssertionError("rejection must not generate")
    llm.chat_stream.side_effect = AssertionError("rejection must not generate")
    # Substitute ledger IO only; all withdrawal checks remain real.
    monkeypatch.setattr(WithdrawalPolicy, "entries", lambda *a: set())
    monkeypatch.setattr(KbDao, "asset_chunk_count", lambda owner, asset, version: sum(r["asset_id"] == asset for r in rows))
    client = MemoryClient(rows)
    if empty:
        client.search = lambda **kw: [[]]
    embedder = Mock(); embedder.embed_query.return_value = [0., 1.]
    uc = QaUseCase(llm=llm)
    uc.settings = uc.settings.model_copy(update={"workflow_evidence_enabled": True, "semantic_cache_enabled": False})
    uc.intent_model_path = ""  # Real rules/gateway; no trained model dependency.
    searcher = Searcher(client=client, embedder=embedder, cache=EmptyCache(), settings=uc.settings)
    uc._searcher = searcher
    ranker = Mock()
    def rank(query, docs, top_n):
        for doc in docs: doc.rerank_score = .9
        return docs[:top_n]
    ranker.rerank.side_effect = rank
    monkeypatch.setattr("qa_core.rerank.reranker.get_reranker", lambda *a: ranker)
    return uc, client, llm


@pytest.mark.parametrize("case", PATHS, ids=lambda c: c["id"])
@pytest.mark.parametrize("stream", [False, True], ids=["sync", "stream"])
def test_state_contract_through_real_orchestration(monkeypatch, case, stream):
    uc, client, llm = setup_usecase(monkeypatch, case, empty=case["stratum"] == "no_evidence")
    tenant = case["fixture_context"].get("request_scope", {}).get("tenant_id", "default")
    kwargs = {"roles": ["ops"], "tenant_id": tenant, "kb_version": "stage4-memory", "persist": False}
    if stream:
        events = list(uc.ask_stream(case["query"], **kwargs))
        result = next(e["data"] for e in events if e["event"] == "end")
        sources = next(e["data"]["sources"] for e in events if e["event"] == "sources")
        assert not any(e["event"] == "delta" for e in events)
    else:
        result = uc.ask(case["query"], **kwargs)
        sources = result["sources"]
    assert result["status"] == case["expected_state_proposal"]
    assert sources == []
    assert not llm.mock_calls
    if case["stratum"] == "permissions":
        assert client.filters and all(f'tenant_id == "{tenant}"' in expr for expr in client.filters)
        assert "步骤 1" not in result["answer"]


@pytest.mark.parametrize("case", POSITIVES, ids=lambda c: c["id"])
@pytest.mark.parametrize("stream", [False, True], ids=["sync", "stream"])
def test_complete_authorized_plan_is_not_blanket_refused(monkeypatch, case, stream):
    uc, client, llm = setup_usecase(monkeypatch, case)
    if stream:
        llm.chat_stream.side_effect = None
        llm.chat_stream.return_value = iter(["沙箱只读取证说明 [1]"])
        events = list(uc.ask_stream(case["query"], roles=["ops"], kb_version="stage4-memory", persist=False))
        result = next(e["data"] for e in events if e["event"] == "end")
        sources = next(e["data"]["sources"] for e in events if e["event"] == "sources")
        assert result["status"] in {"ANSWERED", "DEGRADED"}
        llm.chat_stream.assert_called_once(); llm.chat.assert_not_called()
        # Assert actual supplied evidence, not quality of the canned model response.
        prompt = str(llm.chat_stream.call_args)
        assert "恢复快照" in prompt and "三个实验采样周期" in prompt
    else:
        result = uc.ask(case["query"], roles=["ops"], kb_version="stage4-memory", persist=False, use_llm=False)
        sources = result["sources"]
        assert result["status"] == "ANSWERED"
        assert "恢复快照" in result["answer"] and "三个实验采样周期" in result["answer"]
        assert not llm.mock_calls
    assert len(sources) == 1
    assert sources[0]["source"] == case["positive_source"]
    assert sources[0]["workflow_steps"] == [1, 2, 3, 4]
    assert len(sources[0]["child_chunk_ids"]) == 5


def test_fixture_package_has_not_changed():
    assert validate(CANDIDATE, ROOT / "data/candidates/stage4-core-label-review-r1", PACKAGE)["binding_valid"]


@pytest.mark.parametrize("prefix", ["真实生产设备", "生产环境", "现网", "真实设备"])
def test_explicit_production_not_answered_or_clarified_even_with_lab_version(prefix):
    text = "适用：AMF；Lab-2026.09；条件：队列增长；分支：入口。"
    query = prefix + " AMF Lab-2026.09 队列增长 SOP"
    assert not applicable(query, text)
    assert not missing_context(query, text)


def test_nonproduction_declaration_keeps_authorized_lab_scope():
    assert applicable("非生产沙箱 AMF Lab-2026.09 队列增长 SOP", "Lab-2026.09；条件：队列增长；")


@pytest.mark.parametrize("query", ["AMF ALM-SMF-PDU SOP", "UPF SOP", "ALM-AMF-REG 和 ALM-SMF-PDU SOP"])
def test_wrong_device_or_alarm_not_clarified_from_other_source(query):
    text = "# 入口积压\n适用：AMF；ALM-AMF-REG；Lab-2026.09；条件：队列增长；"
    assert not missing_context(query, text)


def test_alarm_recovery_question_is_workflow_but_plain_lookup_is_not():
    assert is_workflow_query("ALM-AMF-REG 应该怎么恢复？")
    assert not is_workflow_query("ALM-AMF-REG 是什么意思？")


def test_unit_ledger_is_explicit_and_can_inject_withdrawal(unit_withdrawal_ledger):
    from qa_core.security.withdrawals import identity_hash
    unit_withdrawal_ledger["default"] = {identity_hash("default", "wiki", "removed")}
    assert not WithdrawalPolicy(Mock()).sources_allowed(
        [{"tenant_id": "default", "source_system": "wiki", "source_id": "removed"}], "default")
