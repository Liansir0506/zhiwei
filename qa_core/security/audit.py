"""权限审计：失败不阻断主业务，但数据最小化保存查询哈希而非原文。"""
from __future__ import annotations

import hashlib
import json

import sqlalchemy as sa


def audit_access_checked(dsn: str, *, actor: str, tenant_id: str, auth_source: str,
                 action: str, resource: str, decision: str, reason: str = "",
                 roles: list[str] | tuple[str, ...] | None = None, scope: dict | None = None,
                 query: str | None = None) -> bool:
    """Return actual commit success without exposing SQL errors or credentials."""
    query_hash = hashlib.sha256(query.encode()).hexdigest() if query else None
    engine = None
    try:
        from qa_core.storage.read_engine import read_engine
        engine = read_engine(dsn)
        with engine.begin() as conn:
            conn.execute(sa.text(
                "INSERT INTO permission_audit (actor, tenant_id, auth_source, action, resource, "
                "decision, reason, roles_json, scope_json, query_hash) "
                "VALUES (:actor, :tenant, :source, :action, :resource, :decision, :reason, "
                ":roles, :scope, :query_hash)"),
                {"actor": actor[:64], "tenant": tenant_id[:64], "source": auth_source[:16],
                 "action": action[:64], "resource": resource[:128], "decision": decision,
                 "reason": reason[:256], "roles": json.dumps(list(roles or []), ensure_ascii=False),
                 "scope": json.dumps(scope or {}, ensure_ascii=False), "query_hash": query_hash})
        return True
    except Exception:  # noqa: BLE001
        return False
    finally:
        if engine is not None:
            engine.dispose()


def audit_access(dsn: str, **kwargs) -> None:
    """QA callers retain best-effort semantics."""
    audit_access_checked(dsn, **kwargs)
