"""MilvusClient 进程级单例（按 uri）+ 断线重连。

pymilvus 内部连接别名与实例绑定，跨线程（TestClient/asyncio）场景下偶发
ConnectionNotExistException：此时强制重建单例并重试一次。
"""
from __future__ import annotations

import threading

from pymilvus import MilvusClient
from pymilvus.exceptions import ConnectionNotExistException

_lock = threading.Lock()
_clients: dict[str, MilvusClient] = {}


def get_milvus_client(uri: str, timeout: int = 30) -> MilvusClient:
    with _lock:
        if uri not in _clients:
            _clients[uri] = MilvusClient(uri=uri, timeout=timeout)
        return _clients[uri]


def reset_milvus_client(uri: str) -> None:
    with _lock:
        _clients.pop(uri, None)


def query_with_retry(uri: str, collection_name: str, **kwargs) -> list[dict]:
    """闲置断连自愈的 query：ConnectionNotExist 时重建客户端重试一次。

    供 route_admin 等非 Searcher 调用方使用（Searcher 自带 _call 自愈）。
    """
    return read_with_retry(uri, "query", collection_name=collection_name, **kwargs)


def read_with_retry(uri: str, operation: str, **kwargs):
    """At most one reconnect for missing connections; never retry mutations or other errors."""
    if operation not in {"query", "describe_collection"}:
        raise ValueError("only supported read operations may be retried")
    for attempt in range(2):
        client = get_milvus_client(uri)
        try:
            return getattr(client, operation)(**kwargs)
        except ConnectionNotExistException:
            if attempt == 1:
                raise
            # Do not evict a healthy replacement installed by another caller,
            # and do not close the URI-shared underlying connection.
            with _lock:
                if _clients.get(uri) is client:
                    _clients.pop(uri, None)



