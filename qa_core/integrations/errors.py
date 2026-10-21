"""Adapter 标准错误，屏蔽上游系统的差异。"""
from __future__ import annotations


class AdapterError(RuntimeError):
    def __init__(self, source: str, code: str, message: str,
                 *, retryable: bool, status_code: int | None = None):
        super().__init__(message)
        self.source = source
        self.code = code
        self.message = message
        self.retryable = retryable
        self.status_code = status_code

    def to_dict(self) -> dict:
        return {"source": self.source, "code": self.code, "message": self.message,
                "retryable": self.retryable, "status_code": self.status_code}


class AdapterUnavailable(AdapterError):
    def __init__(self, source: str, message: str, status_code: int | None = None):
        super().__init__(source, "UPSTREAM_UNAVAILABLE", message,
                         retryable=True, status_code=status_code)


class AdapterContractError(AdapterError):
    def __init__(self, source: str, message: str):
        super().__init__(source, "CONTRACT_INVALID", message, retryable=False)


class AdapterRejected(AdapterError):
    def __init__(self, source: str, message: str, status_code: int):
        super().__init__(source, "UPSTREAM_REJECTED", message,
                         retryable=False, status_code=status_code)
