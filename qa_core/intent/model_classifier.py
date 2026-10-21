"""模型通道（兜底）：bert_intent_classifier_v1，标签 FAQ_QUERY/KNOWLEDGE_QUERY/FOLLOW_UP。

定位（对接第 05 章 rule_score/model_score 思路）：模型**只在规则灰区触发**，
输出两个信号——是否 FAQ（faq_exact 候选）、是否多轮跟进（requires_rewrite）。
超时/加载失败/标签不匹配一律降级为 model_fallback，绝不阻塞主链路。

模型文件缺失、加载或推理超时会返回带原因的 model_fallback。
请求等待有上限，底层 Torch 任务不能强停；运行期间拒绝新任务排队。
"""
from __future__ import annotations

import concurrent.futures
import threading
from pathlib import Path

from qa_core.schemas.intent import IntentDecision

LABEL_MAP = {"FAQ_QUERY": "faq", "KNOWLEDGE_QUERY": "knowledge", "FOLLOW_UP": "follow_up"}

_lock = threading.Lock()
_states: dict[tuple[str, str], dict] = {}
# 单一执行槽：超时任务不能强停，但也不能无限排队或重复加载。
_pool = concurrent.futures.ThreadPoolExecutor(max_workers=1, thread_name_prefix="intent")
_slot = threading.BoundedSemaphore(1)


def _state_for(model_path: str, device: str) -> dict:
    key = (str(Path(model_path).resolve()), device)
    with _lock:
        return _states.setdefault(key, {"model": None, "labels": [], "failed": False,
                                       "status": "not_loaded", "error_type": None,
                                       "load_lock": threading.Lock()})


def readiness(model_path: str, device: str = "cpu") -> dict:
    """不加载模型、不等待加载锁，健康接口可区分未加载/加载/预热/就绪/失败。"""
    state = _state_for(model_path, device)
    return {"status": state["status"], "ready": state["status"] == "ready",
            "error_type": state["error_type"]}


class IntentModelClassifier:
    def __init__(self, model_path: str, device: str = "cpu", max_length: int = 64):
        import json
        from pathlib import Path

        import torch
        from transformers import AutoModelForSequenceClassification, AutoTokenizer

        labels_file = Path(model_path) / "intent_labels.json"
        meta = json.loads(labels_file.read_text(encoding="utf-8"))
        self.labels = meta["labels"]
        self.device = device
        self.tokenizer = AutoTokenizer.from_pretrained(model_path)
        self.model = AutoModelForSequenceClassification.from_pretrained(model_path).to(device).eval()
        self.max_length = max_length
        self.torch = torch

    def predict(self, query: str) -> dict[str, float]:
        """返回 {语义意图: softmax 概率}，含 follow_up。"""
        inputs = self.tokenizer(query, return_tensors="pt", truncation=True,
                                max_length=self.max_length).to(self.device)
        with self.torch.no_grad():
            logits = self.model(**inputs).logits[0]
        probs = self.torch.softmax(logits, dim=-1)
        return {LABEL_MAP.get(lb, lb.lower()): float(p) for lb, p in zip(self.labels, probs)}


def _get_state(model_path: str, device: str) -> dict:
    state = _state_for(model_path, device)
    with state["load_lock"]:
        if state["model"] is None and not state["failed"]:
            state["status"] = "loading"
            try:
                cls = IntentModelClassifier(model_path, device)
                state["model"] = cls
                state["labels"] = cls.labels
                state["status"] = "loaded"
            except Exception as exc:  # noqa: BLE001
                state["failed"] = True
                state["status"] = "failed"
                state["error_type"] = type(exc).__name__
        return state


def _predict(query: str, model_path: str, device: str) -> dict[str, float] | None:
    state = _get_state(model_path, device)
    if state["failed"]:
        return None
    state["status"] = "warming" if state["status"] != "ready" else "ready"
    try:
        probs = state["model"].predict(query)
        if not probs or any(label not in {"faq", "knowledge", "follow_up"} for label in probs):
            raise ValueError("unexpected intent labels")
        state["status"] = "ready"
        state["error_type"] = None
        return probs
    except Exception as exc:
        state["status"] = "failed"
        state["error_type"] = type(exc).__name__
        raise


def _submit(query: str, model_path: str, device: str):
    if not _slot.acquire(blocking=False):
        return None
    try:
        future = _pool.submit(_predict, query, model_path, device)
    except Exception:
        _slot.release()
        raise
    future.add_done_callback(lambda _: _slot.release())
    return future


def warmup(model_path: str, device: str = "cpu"):
    """显式预热，不经规则网关；返回 Future，调用方可核验真实就绪。"""
    return _submit("设备偶尔掉电需要关注一下", model_path, device)


def _fallback(reason: str) -> IntentDecision:
    return IntentDecision("unknown", "retrieval", "model_fallback", 0.0,
                          f"意图模型 {reason}，灰区默认 retrieval")


def classify(query: str, model_path: str, device: str = "cpu",
             timeout_ms: int = 200) -> IntentDecision | None:
    """灰区仲裁用。返回 IntentDecision（source=model / model_fallback）或 None（禁用/失败）。"""
    before = readiness(model_path, device)
    if _state_for(model_path, device)["failed"]:
        return _fallback("load_failed:" + str(before["error_type"]))
    if timeout_ms <= 0:
        return _fallback("invalid_timeout")
    try:
        future = _submit(query, model_path, device)
        if future is None:
            return _fallback("busy:" + before["status"])
        # 冷加载与首轮推理均计入时限；不在请求出口 shutdown(wait=True)。
        probs = future.result(timeout=timeout_ms / 1000)
    except concurrent.futures.TimeoutError:
        return _fallback("inference_timeout" if before["ready"] else "cold_start_timeout")
    except Exception as exc:  # noqa: BLE001
        return _fallback("inference_failed:" + type(exc).__name__)
    if probs is None:
        return _fallback("load_failed:" + str(readiness(model_path, device)["error_type"]))

    faq_p = probs.get("faq", 0.0)
    follow_p = probs.get("follow_up", 0.0)
    if faq_p >= 0.60:
        return IntentDecision("faq", "faq_exact", "model", faq_p,
                              f"模型判定 FAQ (p={faq_p:.2f})")
    return IntentDecision("knowledge", "retrieval", "model", max(probs.values()),
                          f"模型判定知识问答 follow_up_p={follow_p:.2f}",
                          requires_rewrite=follow_p >= 0.5)
