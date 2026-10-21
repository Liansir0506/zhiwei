import json
from pathlib import Path
from unittest.mock import Mock

import numpy as np
import pytest

from scripts.build_step3_dev_pack import ROOT, build, encode, sha
from scripts.compare_step4_retrieval import load_fixed, RankCache
from qa_core.schemas.retrieval import RetrievedDoc


@pytest.fixture
def inputs(tmp_path):
    package, baseline = tmp_path / "package", tmp_path / "baseline"
    build(package, ROOT / "data/candidates/v4-quality-r1-final")
    baseline.mkdir()
    (baseline / "snapshot-rows.json").write_bytes(encode([{"id": "one"}]))
    np.save(baseline / "snapshot-vectors.npy", np.ones((1,1024), dtype="float32"), allow_pickle=False)
    files = {"package_manifest_sha256": package / "artifact-hashes.json", "cases_sha256": package / "cases.json",
        "sources_sha256": package / "sources.json", "snapshot_rows_sha256": baseline / "snapshot-rows.json",
        "snapshot_vectors_sha256": baseline / "snapshot-vectors.npy"}
    (baseline / "protocol.json").write_bytes(encode({"inputs": {key: sha(file.read_bytes()) for key,file in files.items()}}))
    (baseline / "cases.jsonl").write_bytes(b"{}\n")
    (baseline / "summary.json").write_bytes(encode({"protocol_sha256": sha((baseline/'protocol.json').read_bytes()),
        "cases_jsonl_sha256": sha((baseline/'cases.jsonl').read_bytes())}))
    return package, baseline


@pytest.mark.parametrize("file", ["snapshot-rows.json", "snapshot-vectors.npy", "cases.jsonl"])
def test_changed_snapshot_or_history_is_rejected(inputs, file):
    package, baseline = inputs
    with (baseline / file).open("ab") as handle:
        handle.write(b"changed")
    with pytest.raises(ValueError, match="changed"):
        load_fixed(package, baseline)


def test_valid_fixed_files_keep_the_same_question_denominator(inputs):
    package, baseline = inputs
    _, _, cases, _ = load_fixed(package, baseline)
    assert len(cases) == 52
    assert sum(c["case_kind"] == "answer" for c in cases) == 32


def test_rank_cache_does_not_reuse_changed_body_or_query():
    model = Mock()
    def rank(query, docs, top_n):
        for doc in docs:
            doc.rerank_score = len(doc.text)/100
        return docs
    model.rerank.side_effect = rank
    cache = RankCache(model)
    first = RetrievedDoc("id", "original", .8)
    assert cache.rerank("q", [first])[0].rerank_score == .08
    cache.rerank("q", [RetrievedDoc("id", "original", .8)])
    assert model.rerank.call_count == 1
    changed = cache.rerank("q", [RetrievedDoc("id", "different body", .8)])[0]
    assert changed.rerank_score == .14
    cache.rerank("different query", [first])
    assert model.rerank.call_count == 3
