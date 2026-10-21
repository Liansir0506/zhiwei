"""真实加载 bert_intent_classifier_v1（灰区兜底模型的可用性验证）。"""
import time

import pytest

from qa_core.config.settings import get_settings
from qa_core.intent import model_classifier


@pytest.mark.component
def test_intent_model_loads_and_classifies():
    # 不能硬编码 "models/bert_intent_classifier_v1"：worktree 下 ./models 为空，
    # 模型实体在数据目录。路径必须走 settings，才能随 .env.test 的配置生效。
    model_path = get_settings().intent_model_path
    probs = model_classifier._get_state(model_path, "cpu")
    assert not probs["failed"] and probs["model"] is not None

    start = time.perf_counter()
    decision = model_classifier.classify(
        "5G基站传输中断的排查步骤是什么", model_path, "cpu",
        timeout_ms=2000)
    elapsed = time.perf_counter() - start
    assert decision is not None and decision.source == "model"
    assert elapsed < 5.0  # CPU BERT 单句应秒级内
