"""A health refresh must leave an existing real query connection usable."""
from pymilvus import MilvusClient
from qa_core.config.settings import get_settings
from qa_core.storage.connections import check_milvus


def test_health_probe_does_not_close_existing_client():
    # Some unit probes deliberately mutate the cached Settings object.
    get_settings.cache_clear()
    settings=get_settings()
    assert settings.is_test
    client=MilvusClient(uri=settings.milvus_uri,timeout=30)
    try:
        before=client.query(collection_name=settings.doc_collection,filter='',output_fields=['id'],limit=1)
        for _ in range(3):
            assert check_milvus(settings.milvus_uri).startswith('ok')
            assert client.query(collection_name=settings.doc_collection,filter='',output_fields=['id'],limit=1)==before
    finally:
        client.close()
