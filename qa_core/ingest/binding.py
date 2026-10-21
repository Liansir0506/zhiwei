"""Public-safe configuration identities; never serialize credentials or model bytes."""
import hashlib
import json

PIPELINE_REVISION = "candidate-pipeline-v4-source-identity"


def digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(",", ":")).encode()).hexdigest()


def version_binding(settings):
    return {"protocol": "version-binding-v1", "pipeline_revision": PIPELINE_REVISION,
            "milvus_sha256": digest(getattr(settings, "milvus_uri", "")),
            "doc_collection": getattr(settings, "doc_collection", ""),
            "faq_collection": getattr(settings, "faq_collection", ""),
            "scenario": getattr(settings, "scenario", ""),
            "embedding_path_sha256": digest(getattr(settings, "embedding_model_path", "")),
            "chunk_identity": "version-tenant-asset-content-v1"}


def execution_binding(settings, job_type, payload):
    upload = job_type == "upload"
    return {"version_binding": version_binding(settings),
            "strict_metadata": upload or bool(payload.get("strict_metadata")),
            "record_locations": upload or bool(payload.get("record_locations")),
            "tokenizer_chunking": upload or bool(payload.get("tokenizer_chunking"))}
