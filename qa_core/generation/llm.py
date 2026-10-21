"""LLM client（OpenAI 兼容 / DeepSeek）：非流式 + 流式，重试 1 次，引用模式兜底标记。

注意：deepseek-v4-flash 是思考模型，流式 delta 分 reasoning_content 与 content，
只转发 content（思考过程不给用户）。max_tokens 必须给足（默认 1024），
否则思考耗尽额度导致 content 为空——验证时踩过。
"""
from __future__ import annotations

import json
from urllib.parse import urlsplit

import httpx


class LLMError(Exception):
    pass


class LLMClient:
    def __init__(self, base_url: str, api_key: str, model: str,
                 connect_timeout_s: float = 10.0, read_timeout_s: float = 30.0,
                 max_tokens: int = 1024, temperature: float = 0.1,
                 thinking_disabled: bool | None = None):
        self.base_url = base_url.rstrip("/")
        self.api_key = api_key
        self.model = model
        self.max_tokens = max_tokens
        self.temperature = temperature
        # Grounded, short RAG answers need visible content within the output budget.
        self.thinking_disabled = (urlsplit(self.base_url).hostname == 'api.deepseek.com'
            and model in {'deepseek-flash', 'deepseek-v4-pro'}) if thinking_disabled is None else thinking_disabled
        self.total_calls = 0
        self.total_prompt_tokens = 0
        self.total_completion_tokens = 0
        self._client = httpx.Client(timeout=httpx.Timeout(read_timeout_s, connect=connect_timeout_s))

    def _acc_usage(self, data: dict) -> None:
        u = data.get("usage") or {}
        self.total_calls += 1
        self.total_prompt_tokens += int(u.get("prompt_tokens") or 0)
        self.total_completion_tokens += int(u.get("completion_tokens") or 0)

    def _headers(self) -> dict:
        from qa_core.generation.request_budget import reserve_validation_request
        reserve_validation_request()
        return {"Authorization": f"Bearer {self.api_key}", "Content-Type": "application/json"}

    def chat(self, messages: list[dict], max_tokens: int | None = None,
             retries: int = 1) -> str:
        payload = {"model": self.model, "messages": messages,
                   "max_tokens": max_tokens or self.max_tokens,
                   "temperature": self.temperature, "stream": False}
        if self.thinking_disabled:
            payload['thinking'] = {'type': 'disabled'}
        last_exc: Exception | None = None
        for _ in range(retries + 1):
            try:
                r = self._client.post(f"{self.base_url}/chat/completions",
                                      headers=self._headers(), json=payload)
                r.raise_for_status()
                data = r.json()
                self._acc_usage(data)
                content = data["choices"][0]["message"].get("content") or ""
                if not content.strip():
                    raise LLMError("LLM 返回空 content（思考耗尽 max_tokens 或服务异常）")
                return content
            except Exception as exc:  # noqa: BLE001
                last_exc = exc
        raise LLMError(f"LLM 调用失败: {type(last_exc).__name__}: {last_exc}") from last_exc

    def chat_stream(self, messages: list[dict], max_tokens: int | None = None):
        """SSE 流式：yield content delta。异常抛 LLMError。"""
        payload = {"model": self.model, "messages": messages,
                   "max_tokens": max_tokens or self.max_tokens,
                   "temperature": self.temperature, "stream": True}
        if self.thinking_disabled:
            payload['thinking'] = {'type': 'disabled'}
        try:
            with self._client.stream("POST", f"{self.base_url}/chat/completions",
                                     headers=self._headers(), json=payload) as resp:
                resp.raise_for_status()
                for line in resp.iter_lines():
                    if not line.startswith("data:"):
                        continue
                    data = line[len("data:"):].strip()
                    if data == "[DONE]":
                        return
                    try:
                        chunk = json.loads(data)
                        delta = chunk["choices"][0].get("delta", {})
                        piece = delta.get("content")
                        if piece:
                            yield piece
                    except (json.JSONDecodeError, KeyError, IndexError):
                        continue
        except Exception as exc:
            raise LLMError(f"LLM 流式调用失败: {type(exc).__name__}") from exc
