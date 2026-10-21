"""组件连接检查（阶段3）：MySQL / Redis / Milvus / 模型目录。

返回统一格式："ok" 或 "error: <原因>"。任何组件失败不抛异常，
由调用方决定 degraded 语义——/health 必须总是可返回。
"""
from __future__ import annotations

from pathlib import Path

from sqlalchemy import create_engine, text


def check_mysql(dsn: str) -> str:
    try:
        engine = create_engine(dsn, pool_pre_ping=True, connect_args={"connect_timeout": 3})
        with engine.connect() as conn:
            conn.execute(text("SELECT 1"))
        engine.dispose()
        return "ok"
    except Exception as exc:  # noqa: BLE001
        return f"error: {type(exc).__name__}: {exc}"[:200]


def check_redis(url: str) -> str:
    try:
        import redis as redis_lib

        client = redis_lib.Redis.from_url(url, socket_connect_timeout=3, socket_timeout=3)
        pong = client.ping()
        client.close()
        return "ok" if pong else "error: ping returned False"
    except Exception as exc:  # noqa: BLE001
        return f"error: {type(exc).__name__}: {exc}"[:200]


def check_milvus(uri: str) -> str:
    # MilvusClient instances may share the URI's underlying SDK connection.
    # A health probe must own an explicit alias so closing it cannot interrupt reads.
    import uuid
    alias = "rag_health_" + uuid.uuid4().hex
    connections = None
    try:
        from pymilvus import connections, utility
        connections.connect(alias=alias, uri=uri, timeout=5)
        version = utility.get_server_version(using=alias, timeout=5)
        return f"ok ({version})"
    except Exception as exc:  # noqa: BLE001
        return f"error: {type(exc).__name__}: {exc}"[:200]
    finally:
        if connections is not None:
            try:
                connections.remove_connection(alias)
            except Exception:
                pass


def check_models(paths: list[str]) -> str:
    missing = [p for p in paths if not Path(p).is_dir()]
    if not paths:
        return "error: no model paths configured"
    return "ok" if not missing else f"error: missing {missing}"


def check_all(settings) -> dict[str, str]:
    """启动时/强制刷新时全量检查。"""
    model_paths = [settings.embedding_model_path, settings.reranker_model_path]
    return {
        "mysql": check_mysql(settings.mysql_dsn),
        "redis": check_redis(settings.redis_url),
        "milvus": check_milvus(settings.milvus_uri),
        "models": check_models(model_paths),
    }
