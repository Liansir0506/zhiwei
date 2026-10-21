"""创建 FAQ / DOC 两个 Milvus Collection（字段级 schema 见 docs/DATA_MODEL.md）。

要点：
- sparse 字段由 Milvus 2.5 服务端 BM25 Function 从 text 自动生成（客户端不算 sparse）；
- text 开启 analyzer（中文），BM25 分词走服务端 jieba；
- network_tech 作为 partition_key；
- 重复执行幂等（已存在则跳过，--recreate 删除重建）。
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from pymilvus import CollectionSchema, DataType, Function, FunctionType, MilvusClient

ANALYZER = {"type": "chinese"}


def _common_fields(schema: CollectionSchema) -> None:
    schema.add_field("id", DataType.VARCHAR, is_primary=True, max_length=64)
    schema.add_field("text", DataType.VARCHAR, max_length=8192,
                     enable_analyzer=True, analyzer_params=ANALYZER)
    schema.add_field("dense", DataType.FLOAT_VECTOR, dim=1024)
    schema.add_field("sparse", DataType.SPARSE_FLOAT_VECTOR)
    schema.add_field("kb_version", DataType.VARCHAR, max_length=32)
    schema.add_field("network_tech", DataType.VARCHAR, max_length=16, is_partition_key=True)
    schema.add_field("doc_type", DataType.VARCHAR, max_length=16)
    schema.add_field("equipment_type", DataType.VARCHAR, max_length=32, default_value="")
    schema.add_field("vendor", DataType.VARCHAR, max_length=32, default_value="")
    schema.add_field("alarm_code", DataType.VARCHAR, max_length=32, default_value="")
    schema.add_field("source", DataType.VARCHAR, max_length=256, default_value="")
    schema.add_field("tenant_id", DataType.VARCHAR, max_length=32, default_value="default")
    schema.add_field("dept_id", DataType.VARCHAR, max_length=64, default_value="public")
    schema.add_field("dataset_id", DataType.VARCHAR, max_length=32, default_value="telecom_ops")
    schema.add_field("visibility", DataType.VARCHAR, max_length=32, default_value="public")
    schema.add_field("allowed_roles", DataType.JSON)
    schema.add_field("role_scope", DataType.JSON)
    schema.add_field("security_level", DataType.VARCHAR, max_length=16, default_value="internal")
    schema.add_field("quality", DataType.VARCHAR, max_length=8, default_value="high")
    schema.add_field("device_type", DataType.VARCHAR, max_length=64, default_value="")
    schema.add_field("source_system", DataType.VARCHAR, max_length=64, default_value="manual_upload")
    schema.add_field("source_id", DataType.VARCHAR, max_length=128, default_value="")
    schema.add_field("source_version", DataType.VARCHAR, max_length=64, default_value="")
    schema.add_field("effective_from", DataType.VARCHAR, max_length=32, default_value="")
    schema.add_field("effective_to", DataType.VARCHAR, max_length=32, default_value="")
    schema.add_field("status", DataType.VARCHAR, max_length=16, default_value="active")
    schema.add_field("ticket_id", DataType.VARCHAR, max_length=64, default_value="")
    schema.add_field("cmdb_id", DataType.VARCHAR, max_length=64, default_value="")
    schema.add_field("change_id", DataType.VARCHAR, max_length=64, default_value="")
    schema.add_field("asset_id", DataType.VARCHAR, max_length=64, default_value="")
    schema.add_field("origin_type", DataType.VARCHAR, max_length=32, default_value="unknown")
    schema.add_field("synthetic", DataType.BOOL, default_value=False)
    schema.add_field("generator_version", DataType.VARCHAR, max_length=32, default_value="")
    schema.add_field("generation_seed", DataType.VARCHAR, max_length=32, default_value="")
    schema.add_field("review_status", DataType.VARCHAR, max_length=32, default_value="unreviewed")
    schema.add_field("license_status", DataType.VARCHAR, max_length=32, default_value="unknown")
    schema.add_field("evidence_refs", DataType.JSON)
    schema.add_function(Function(
        name="text_bm25",
        function_type=FunctionType.BM25,
        input_field_names=["text"],
        output_field_names=["sparse"],
    ))


def _doc_extra(schema: CollectionSchema) -> None:
    schema.add_field("clause", DataType.VARCHAR, max_length=128, default_value="")
    schema.add_field("parent_id", DataType.VARCHAR, max_length=64, default_value="")
    schema.add_field("step_index", DataType.JSON)


def _faq_extra(schema: CollectionSchema) -> None:
    schema.add_field("question", DataType.VARCHAR, max_length=512)
    schema.add_field("answer", DataType.VARCHAR, max_length=4096)


def _index_params(client: MilvusClient):
    index_params = client.prepare_index_params()
    index_params.add_index(field_name="dense", index_type="HNSW", metric_type="COSINE",
                           params={"M": 16, "efConstruction": 200})
    index_params.add_index(field_name="sparse", index_type="AUTOINDEX", metric_type="BM25")
    return index_params


def build(client: MilvusClient, name: str, faq: bool, recreate: bool = False) -> bool:
    if client.has_collection(name):
        if not recreate:
            print(f"[skip] {name} 已存在")
            return False
        client.drop_collection(name)
        print(f"[drop] {name} 已删除")
    schema = client.create_schema(auto_id=False, enable_dynamic_field=False)
    _common_fields(schema)
    (_faq_extra if faq else _doc_extra)(schema)
    client.create_collection(name, schema=schema, index_params=_index_params(client))
    print(f"[create] {name} ok")
    return True


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--recreate", action="store_true", help="删除重建")
    args = parser.parse_args()

    from qa_core.config.settings import get_settings

    settings = get_settings()
    client = MilvusClient(uri=settings.milvus_uri, timeout=30)
    build(client, settings.doc_collection, faq=False, recreate=args.recreate)
    build(client, settings.faq_collection, faq=True, recreate=args.recreate)
    print("collections:", client.list_collections())


if __name__ == "__main__":
    main()
