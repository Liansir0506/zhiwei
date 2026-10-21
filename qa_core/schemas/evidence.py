"""Stable, content-bound references; IDs grant no access by themselves."""
import base64
import hashlib
import json


def evidence_of(doc, collection_kind="doc"):
    revision = doc.source_version
    document = doc.asset_id
    body = doc.answer if collection_kind == "faq" else doc.text
    identity = [doc.kb_version, collection_kind, doc.id, document, revision,
                hashlib.sha256(body.encode()).hexdigest()]
    token = base64.urlsafe_b64encode(json.dumps(identity, ensure_ascii=False,
        separators=(",", ":")).encode()).decode().rstrip("=")
    steps = doc.extra.get("step_index")
    location = {"kind": "step", "steps": steps} if steps else (
        {"kind": "section", "section": doc.clause} if doc.clause else {"kind": "legacy", "available": False})
    location = doc.extra.get("source_location") or location
    return {"evidence_id": token, "chunk_id": doc.id, "document_id": document,
            "source_revision": revision, "kb_version": doc.kb_version,
            "source": doc.source, "text": body, "location": location,
            "scores": {"dense": doc.dense_score, "sparse": doc.sparse_score,
                       "fusion": doc.fusion_score, "rerank": doc.rerank_score},
            "source_url": f"/kb/evidence/{token}", "synthetic": doc.synthetic}


def decode_reference(token):
    if len(token) > 2048:
        raise ValueError("invalid reference")
    try:
        value = json.loads(base64.b64decode(token + "=" * (-len(token) % 4),
                                           altchars=b"-_", validate=True))
    except Exception as exc:
        raise ValueError("invalid reference") from exc
    if (not isinstance(value, list) or len(value) != 6
            or not all(isinstance(v, str) and v and len(v) <= 512 for v in value)
            or value[1] not in {"doc", "faq"}):
        raise ValueError("invalid reference")
    return value


def citation_fields(doc):
    if not doc.asset_id or not doc.source_version or doc.extra.get("workflow_evidence"):
        return {}
    result = evidence_of(doc, "faq" if doc.doc_type == "faq" else "doc")
    if len(result["evidence_id"]) > 2048:
        return {}
    return {key: result[key] for key in ("evidence_id", "chunk_id", "document_id",
                                        "source_revision", "location", "source_url")}
