"""Reviewed, content-bound identity mappings. Never infer identities from filenames."""
import hashlib
import json
import re
from pathlib import Path


def validate_mapping(root, manifest, metadata):
    root = Path(root).resolve()
    if manifest.get("protocol") != "reviewed-source-map-v1":
        raise ValueError("来源映射协议不支持")
    entries = manifest.get("entries")
    if not isinstance(entries, list) or not entries:
        raise ValueError("来源映射不能为空")
    overrides, hashes = {}, set()
    for entry in entries:
        name = entry.get("filename", "")
        if not name or Path(name).name != name or "/" in name or "\\" in name:
            raise ValueError("来源映射只允许目录内文件名")
        path = (root / name).resolve()
        if path.parent != root or not path.is_file() or name in overrides:
            raise ValueError("来源映射文件缺失、越界或重复")
        checksum = hashlib.sha256(path.read_bytes()).hexdigest()
        if entry.get("sha256") != checksum:
            raise ValueError("来源映射正文摘要不匹配，必须重新审核")
        if checksum in hashes:
            raise ValueError("同一候选中重复正文可能冲突入库账本，请拆分或去重后重新审核")
        hashes.add(checksum)
        approval = entry.get("approval", {})
        if (approval.get("status") != "approved" or not approval.get("reviewer")
                or not approval.get("proposer") or approval["reviewer"] == approval["proposer"]
                or not str(approval.get("evidence", "")).strip()):
            raise ValueError("缺少独立审核人、提议人或来源依据；待审核映射不可使用")
        tenant, system, source_id = (entry.get(k, "") for k in ("tenant_id", "source_system", "source_id"))
        if (not tenant or len(tenant) > 64 or not re.fullmatch(r"[A-Za-z0-9_.-]{1,64}", system)
                or not source_id.strip() or len(source_id) > 128
                or any(ord(ch) < 32 for ch in tenant + source_id)):
            raise ValueError("来源稳定身份无效")
        prior = metadata.get(name)
        if not isinstance(prior, dict) or prior.get("tenant_id") != tenant:
            raise ValueError("来源映射不得猜测或改变原租户边界")
        for key, value in (("source_system", system), ("source_id", source_id)):
            if prior.get(key) and prior[key] != value:
                raise ValueError("已有稳定来源身份不可重命名；需另行核对撤回与来源注册")
        overrides[name] = {**prior, "source_system": system, "source_id": source_id}
    if set(overrides) != set(metadata):
        raise ValueError("映射必须覆盖完整元数据清单，不能部分补写后直接导入")
    return overrides


def verify_upload_replacement(client, records, settings, version, tenant, source_id):
    """Validate every old asset before deleting any same-filename ingestion rows."""
    for record in records:
        asset = record.get("asset_id")
        if not asset:
            raise ValueError("旧资产身份不明，禁止上传覆盖")
        collection = settings.faq_collection if record["doc_type"] == "faq" else settings.doc_collection
        fields = {f["name"] for f in client.describe_collection(collection_name=collection)["fields"]}
        if not {"asset_id", "tenant_id", "source_system", "source_id"} <= fields:
            raise ValueError("旧集合缺稳定身份，禁止上传覆盖")
        base = f"kb_version == {json.dumps(version)} and asset_id == {json.dumps(asset)}"
        expected = (f"tenant_id == {json.dumps(tenant)} and source_system == \"manual_upload\" "
                    f"and source_id == {json.dumps(source_id)}")
        if (not client.query(collection_name=collection, filter=base, output_fields=["id"],
                             limit=1, consistency_level="Strong")
                or client.query(collection_name=collection, filter=f"({base}) and not ({expected})",
                                output_fields=["id"], limit=1, consistency_level="Strong")):
            raise ValueError("同名旧资料不属于当前租户及稳定来源，禁止覆盖；请使用新文件名或独立候选")
