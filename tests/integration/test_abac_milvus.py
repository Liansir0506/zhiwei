"""在隔离 Milvus 中验证 ABAC 标量过滤发生在候选返回之前。"""
from __future__ import annotations

import pytest
from pymilvus import DataType, MilvusClient

from qa_core.config.settings import get_settings
from qa_core.retrieval.filters import build_filter_expr
from qa_core.security.policy import AccessScope


@pytest.mark.integration
def test_milvus_abac_excludes_cross_tenant_department_and_security_records():
    client = MilvusClient(uri=get_settings().milvus_uri)
    name = "test_abac_security_probe"
    if client.has_collection(name):
        client.drop_collection(name)
    schema = client.create_schema(auto_id=False, enable_dynamic_field=False)
    schema.add_field("id", DataType.VARCHAR, is_primary=True, max_length=64)
    schema.add_field("dense", DataType.FLOAT_VECTOR, dim=2)
    schema.add_field("visibility", DataType.VARCHAR, max_length=32)
    schema.add_field("allowed_roles", DataType.JSON)
    schema.add_field("role_scope", DataType.JSON)
    schema.add_field("tenant_id", DataType.VARCHAR, max_length=64)
    schema.add_field("dept_id", DataType.VARCHAR, max_length=64)
    schema.add_field("security_level", DataType.VARCHAR, max_length=16)
    schema.add_field("device_type", DataType.VARCHAR, max_length=64)
    schema.add_field("kb_version", DataType.VARCHAR, max_length=32)
    indexes = client.prepare_index_params()
    indexes.add_index("dense", index_type="AUTOINDEX", metric_type="COSINE")
    client.create_collection(name, schema=schema, index_params=indexes)
    try:
        rows = [
            {"id": "allowed", "dense": [1.0, 0.0], "visibility": "public",
             "allowed_roles": [], "role_scope": [], "tenant_id": "tenant-a",
             "dept_id": "public", "security_level": "internal",
             "device_type": "gNodeB", "kb_version": "v1"},
            {"id": "other-tenant", "dense": [1.0, 0.0], "visibility": "public",
             "allowed_roles": [], "role_scope": [], "tenant_id": "tenant-b",
             "dept_id": "public", "security_level": "internal",
             "device_type": "gNodeB", "kb_version": "v1"},
            {"id": "other-dept", "dense": [1.0, 0.0], "visibility": "department_restricted",
             "allowed_roles": [], "role_scope": [], "tenant_id": "tenant-a",
             "dept_id": "core", "security_level": "internal",
             "device_type": "gNodeB", "kb_version": "v1"},
            {"id": "restricted", "dense": [1.0, 0.0], "visibility": "role_restricted",
             "allowed_roles": ["admin"], "role_scope": ["admin"], "tenant_id": "tenant-a",
             "dept_id": "netops", "security_level": "restricted",
             "device_type": "gNodeB", "kb_version": "v1"},
        ]
        client.insert(name, rows)
        # Milvus insert 默认是异步持久化；全量套件负载下立即 query 可能尚不可见。
        # 显式 flush/load，避免把一致性窗口误判为 ABAC 过滤失败。
        client.flush(name)
        client.load_collection(name)
        scope = AccessScope.build(tenant_id="tenant-a", roles=["ops"], dept_ids=["netops"],
                                  device_types=["gNodeB"])
        expr = build_filter_expr(list(scope.roles), "v1", tenant_id=scope.tenant_id,
                                 dept_ids=scope.dept_ids, security_levels=scope.security_levels,
                                 device_types=scope.device_types)
        result = client.query(name, filter=expr, output_fields=["id"], limit=20)
        assert {row["id"] for row in result} == {"allowed"}
    finally:
        if client.has_collection(name):
            client.drop_collection(name)
