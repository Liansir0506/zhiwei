from types import SimpleNamespace

from qa_core.retrieval.searcher import Searcher
from qa_core.security.policy import AccessScope


class _Client:
    def __init__(self):
        self.filters = []

    def describe_collection(self, collection_name):
        names = {"id", "text", "dense", "sparse", "visibility", "allowed_roles",
                 "role_scope", "tenant_id", "dept_id", "security_level", "device_type",
                 "kb_version", "doc_type", "network_tech"}
        return {"fields": [{"name": name} for name in names]}

    def search(self, **kwargs):
        self.filters.append(kwargs["filter"])
        return [[]]


class _Embedder:
    def embed_query(self, query):
        return [0.0, 1.0]


class _Cache:
    def __init__(self):
        self.keys = []

    def get(self, *args):
        return None

    def set(self, *args):
        self.keys.append(args)

    def get_with_lock(self, *args):
        return None


def _settings():
    return SimpleNamespace(
        milvus_uri="unused", embedding_model_path="unused", embedding_device="cpu",
        redis_url="unused", scenario="telecom_ops", cache_emb_ttl=1,
        cache_ret_ttl=1, cache_faq_ttl=1, top_k_dense=5, dense_ef=8,
        doc_collection="docs", faq_collection="faqs", fusion_method="weighted")


def test_all_recall_routes_receive_abac_filter_before_candidates(monkeypatch):
    from qa_core.security.withdrawals import WithdrawalPolicy
    monkeypatch.setattr(WithdrawalPolicy, "entries", lambda *a: set())
    client = _Client()
    searcher = Searcher(client=client, embedder=_Embedder(), settings=_settings(), cache=_Cache())
    scope = AccessScope.build(tenant_id="tenant-a", roles=["ops"], dept_ids=["netops"],
                              device_types=["gNodeB"],
                              requested_security_levels=["public", "internal"])

    searcher.search_docs(["链路告警"], ["ops"], "v1", scope=scope)

    assert len(client.filters) == 2  # dense + sparse
    for expr in client.filters:
        assert 'tenant_id == "tenant-a"' in expr
        assert 'dept_id in ["netops"]' in expr
        assert 'security_level in ["internal", "public"]' in expr
        assert 'device_type == "" or device_type in ["gNodeB"]' in expr
