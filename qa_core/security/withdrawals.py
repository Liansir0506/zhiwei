"""Fresh monotonic source withdrawal checks; unknown legacy identities fail closed."""
import hashlib
import json
from functools import wraps, lru_cache
from inspect import signature

import sqlalchemy as sa
from qa_core.schemas.retrieval import RetrievalResult


def identity_hash(tenant, system, source_id):
    return hashlib.sha256(json.dumps([tenant, system, source_id], ensure_ascii=False,
                                    separators=(",", ":")).encode()).hexdigest()


def allowed_source(source, tenant, entries):
    if not entries:
        return True  # Existing untouched tenants retain legacy behavior.
    field = source.get if isinstance(source, dict) else lambda key, default="": getattr(source, key, default)
    system, source_id = field("source_system", ""), field("source_id", "")
    if field("tenant_id", tenant) != tenant or not system or not source_id:
        return False
    return identity_hash(tenant, system, source_id) not in entries


class WithdrawalPolicy:
    def __init__(self, settings):
        self.settings = settings

    def entries(self, tenant):
        # Reuse connections, never cache ledger rows: every call starts a fresh
        # transaction and sees committed withdrawals, including in-flight streams.
        with _ledger_engine(self.settings.mysql_dsn).connect() as conn:
            return set(conn.execute(sa.text(
                "SELECT identity_hash FROM knowledge_source_withdrawals WHERE tenant_id=:t"),
                {"t": tenant}).scalars())

    def filter_docs(self, docs, tenant):
        entries = self.entries(tenant)
        return [doc for doc in docs if allowed_source(doc, tenant, entries)]

    def sources_allowed(self, sources, tenant):
        entries = self.entries(tenant)
        # An evidence-backed cache with no identity/source cannot be safely reused.
        return (bool(sources) or not entries) and all(allowed_source(s, tenant, entries) for s in sources)


@lru_cache(maxsize=16)
def _ledger_engine(dsn):
    return sa.create_engine(dsn, pool_pre_ping=True)


def withdrawal_checked(func):
    parameters = signature(func)
    @wraps(func)
    def wrapped(owner, *args, **kwargs):
        bound = parameters.bind(owner, *args, **kwargs)
        scope = bound.arguments.get("scope")
        tenant = scope.tenant_id if scope is not None else bound.arguments.get("tenant_id", "default")
        result = func(owner, *args, **kwargs)
        policy = WithdrawalPolicy(owner.settings)
        if isinstance(result, RetrievalResult):
            result.docs = policy.filter_docs(result.docs, tenant)
            result.after_dedup = len(result.docs)
            return result
        if isinstance(result, list):
            return policy.filter_docs(result, tenant)
        if result is not None and not policy.filter_docs([result], tenant):
            return None
        return result
    return wrapped


def assert_publishable_sources(settings, sources):
    by_tenant = {}
    for tenant, system, source_id in sources:
        by_tenant.setdefault(tenant, []).append({"tenant_id": tenant, "source_system": system, "source_id": source_id})
    policy = WithdrawalPolicy(settings)
    for tenant, rows in by_tenant.items():
        entries = policy.entries(tenant)
        if any(not allowed_source(row, tenant, entries) for row in rows):
            raise ValueError("候选包含已撤回或无法确认稳定身份的来源，禁止发布")
