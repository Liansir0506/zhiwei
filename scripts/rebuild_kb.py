"""离线入库流水线：扫描 data/raw → 幂等检查 → 解析 → 切分 → 脱敏 → 向量化 → 装载。

用法：
  python scripts/rebuild_kb.py --kb-version v1 [--mode full|incremental]

断点续跑：以 (file_hash, kb_version) 为幂等键，status=done 的文件跳过；
失败文件停在原状态并记录 error_msg，重跑只处理未 done 的文件。

doc_type 从文件名前缀推断：<doc_type>_xxx.<ext>，如 fault_case_tickets.csv、manual_bbhu.md。
"""
from __future__ import annotations

import argparse
import hashlib
import json
import re
import sys
import unicodedata
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from qa_core.config.settings import get_settings
from qa_core.domain.models import normalize_knowledge_metadata
from qa_core.indexing.chunkers.base import Chunk
from qa_core.indexing.chunkers.registry import (
    chunk_manual,
    chunk_sop,
    chunk_standard,
    chunk_ticket,
    chunk_faq,
)
from qa_core.indexing.embedder.embedder import get_embedder
from qa_core.indexing.parsers.parsers import parse_dispatch
from qa_core.indexing.parsers.sanitize import sanitize
from qa_core.storage.kb_dao import KbDao

TEXT_CHUNKERS = {"manual": chunk_manual, "guide": chunk_manual,
                 "standard": chunk_standard, "sop": chunk_sop}
TABLE_CHUNKERS = {"fault_case": chunk_ticket, "faq": chunk_faq}

PROMPT_INJECTION_MARKS = ("忽略以上指令", "忽略之前", "you are now", "system:")

DEFAULT_META = {
    "tenant_id": "default",
    "dept_id": "public",
    "dataset_id": "telecom_ops",
    "visibility": "public",
    "allowed_roles": [],
    "role_scope": [],
    "security_level": "internal",
    "quality": "high",
    "source_system": "manual_upload",
    "status": "active",
    "origin_type": "legacy",
    "synthetic": False,
    "generator_version": "",
    "generation_seed": "",
    "review_status": "legacy_reviewed",
    "license_status": "local",
    "evidence_refs": [],
}


def file_hash_of(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def normalize_text(text: str) -> str:
    """全半角与空白归一（在线输入管道同款，入库侧先做一遍）。"""
    text = unicodedata.normalize("NFKC", text)
    return re.sub(r"[ \t]+", " ", text).strip()


def guess_doc_type(filename: str) -> str:
    known = ("fault_case", "faq", "manual", "standard", "guide", "sop")
    for t in sorted(known, key=len, reverse=True):  # fault_case 优先于 faq 无冲突，但长前缀优先更稳
        if filename.startswith(t + "_"):
            return t
    raise ValueError(f"文件名必须以知识源类型开头（{'/'.join(sorted(known))}），实际: {filename}")


def resolve_doc_type(filename, metadata=None, *, strict_metadata=False):
    """Keep approved filenames stable; explicit types require strict metadata."""
    explicit = (metadata or {}).get("doc_type") if strict_metadata else None
    if explicit:
        if explicit not in {"fault_case", "faq", "manual", "standard", "guide", "sop"}:
            raise ValueError("严格元数据知识类型无效")
        try:
            named = guess_doc_type(filename)
        except ValueError:
            return explicit
        if named != explicit:
            raise ValueError("文件名前缀与严格元数据知识类型冲突")
        return named
    return guess_doc_type(filename)


def to_milvus_row(chunk: Chunk, chunk_id: str, vector: list[float], kb_version: str) -> dict:
    meta = {**DEFAULT_META, **chunk.metadata}
    meta.update(normalize_knowledge_metadata(meta))
    text = normalize_text(chunk.text)
    for mark in PROMPT_INJECTION_MARKS:
        if mark in text.lower():
            raise ValueError(f"chunk {chunk_id} 含疑似注入标记: {mark}")
    row = {
        "id": chunk_id,
        "text": text[:8000],
        "dense": vector,
        "kb_version": kb_version,
        # sparse 由服务端 BM25 Function 生成，不传
    }
    for k in ("doc_type", "equipment_type", "vendor", "alarm_code", "source",
              "tenant_id", "dept_id", "dataset_id", "visibility", "security_level",
              "quality", "device_type", "source_system", "source_id", "source_version",
              "effective_from", "effective_to", "status", "ticket_id", "cmdb_id",
              "change_id", "asset_id", "origin_type", "generator_version",
              "generation_seed", "review_status", "license_status"):
        row[k] = str(meta.get(k, "") or "")
    row["synthetic"] = bool(meta.get("synthetic", False))
    evidence_refs = meta.get("evidence_refs") or []
    row["evidence_refs"] = evidence_refs if isinstance(evidence_refs, list) else []
    roles = meta.get("allowed_roles") or []
    row["allowed_roles"] = roles if isinstance(roles, list) else []
    role_scope = meta.get("role_scope") or roles
    row["role_scope"] = role_scope if isinstance(role_scope, list) else []
    row["network_tech"] = str(meta.get("network_tech") or "")  # partition_key 必须有值
    if "question" in meta:  # faq 行
        row["question"] = str(meta["question"] or "")[:512]
        row["answer"] = str(meta["answer"] or "")[:4096]
    else:  # doc 行：无默认值的字段必须显式给值
        row["clause"] = str(meta.get("clause") or "")[:128]
        row["parent_id"] = str(meta.get("parent_id") or "")[:64]
        row["step_index"] = meta.get("step_index") or []
    return row


def dedup_key(chunk: Chunk) -> str:
    """按规范化前的完整 chunk 内容去重；相同告警码不是重复工单的充分条件。"""
    return f"txt:{hashlib.sha256(chunk.text.encode()).hexdigest()}"


def _collection_field_names(client, collection: str) -> set[str] | None:
    """滚动升级兼容：旧 collection 缺少第2步字段时仅写其已存在字段。"""
    try:
        desc = client.describe_collection(collection_name=collection)
        fields = desc.get("fields", []) if isinstance(desc, dict) else []
        names = {f.get("name") for f in fields if isinstance(f, dict) and f.get("name")}
        return names or None
    except Exception:  # noqa: BLE001
        return None


from qa_core.ingest.write_guard import guarded_write


@guarded_write
def delete_file_chunks(client, dao: KbDao, filename: str, kb_version: str,
                       doc_collection: str | None = None,
                       faq_collection: str | None = None) -> dict:
    """从指定版本删除某文档的全部 chunks 与入库记录（增量更新/删除的"删旧"半边）。

    - 文本/FAQ 类：按 source == filename 直接删；
    - 工单类：source 是工单号而非文件名，复现切分+去重后按 chunk id 精确反删；
    返回 {chunks_deleted, records_deleted}。
    """
    import hashlib

    stats = {"chunks_deleted": 0, "records_deleted": 0}
    settings = get_settings()
    doc_collection = doc_collection or settings.doc_collection
    faq_collection = faq_collection or settings.faq_collection
    records = dao.get_records_by_filename(filename, kb_version)
    for rec in records:
        fh, doc_type = rec["file_hash"], rec["doc_type"]
        if rec.get("asset_id") and "asset_id" in (_collection_field_names(
            client, faq_collection if doc_type == "faq" else doc_collection) or set()
        ):
            collection = faq_collection if doc_type == "faq" else doc_collection
            expr = f'asset_id == {json.dumps(rec["asset_id"])} and kb_version == {json.dumps(kb_version)}'
            res = client.delete(collection_name=collection, filter=expr)
            client.flush(collection)
            stats["chunks_deleted"] += int(res.get("delete_count", 0) or 0)
            stats["records_deleted"] += dao.delete_record(fh, kb_version)
            continue
        if doc_type == "fault_case":
            path = Path(settings.data_dir) / "raw" / filename
            if path.exists():
                chunks = chunk_rows(doc_type, parse_dispatch(doc_type, path))
                seen, unique = set(), []
                for c in chunks:
                    k = dedup_key(c)
                    if k not in seen:
                        seen.add(k)
                        unique.append(c)
                ids = [hashlib.sha256(f"{fh}:{i}:{c.text}".encode()).hexdigest()[:32]
                       for i, c in enumerate(unique)]
                id_list = ",".join(f'"{i}"' for i in ids)
                expr = f"id in [{id_list}] and kb_version == {json.dumps(kb_version)}"
            else:
                raise ValueError("缺少资产身份及旧工单原文件，拒绝仅删账本")
        else:
            collection = faq_collection if doc_type == "faq" else doc_collection
            src_escaped = filename.replace('"', '\\"')
            expr = f'source == "{src_escaped}" and kb_version == "{kb_version}"'
            collection = None if not expr else collection
        if doc_type == "fault_case":
            if expr:
                for coll in (doc_collection,):
                    try:
                        res = client.delete(collection_name=coll, filter=expr)
                        stats["chunks_deleted"] += int(res.get("delete_count", 0) or 0) if isinstance(res, dict) else 0
                    except Exception as exc:  # noqa: BLE001
                        raise RuntimeError("候选向量删除失败，保留入库记录") from exc
        else:
            try:
                res = client.delete(collection_name=collection, filter=expr)
                stats["chunks_deleted"] += int(res.get("delete_count", 0) or 0) if isinstance(res, dict) else 0
                client.flush(collection)
            except Exception as exc:  # noqa: BLE001
                raise RuntimeError("候选向量删除失败，保留入库记录") from exc
        stats["records_deleted"] += dao.delete_record(fh, kb_version)
    if stats["chunks_deleted"]:
        client.flush(doc_collection)
    return stats


@guarded_write
def process_file(client, dao: KbDao, embedder, path: Path, kb_version: str,
                 seen_keys: set[str], stats: dict, extra_meta: dict | None = None,
                 *, strict_metadata: bool = False, record_locations: bool = False,
                 tokenizer_chunking: bool = False) -> None:
    fh = file_hash_of(path)
    filename = path.name
    asset_id = (extra_meta or {}).get("asset_id") or "kb-" + hashlib.sha256(json.dumps(
        [kb_version, (extra_meta or {}).get("tenant_id", "default"), filename, fh],
        separators=(",", ":")).encode()).hexdigest()[:32]

    if dao.file_done(fh, kb_version, asset_id):
        stats["skipped"] += 1
        return

    try:
        doc_type = resolve_doc_type(filename, extra_meta, strict_metadata=strict_metadata)
        dao.upsert_record(fh, filename, doc_type, kb_version, asset_id=asset_id)
    except ValueError as exc:
        print(f"[failed] {filename}: {exc}")
        stats["failed"] += 1
        return
    try:
        dao.set_status(fh, kb_version, "parsed")
        parsed = parse_dispatch(doc_type, path)

        dao.set_status(fh, kb_version, "chunked")
        if doc_type in TABLE_CHUNKERS:
            chunks = TABLE_CHUNKERS[doc_type](parsed)
        else:
            chunks = TEXT_CHUNKERS[doc_type](
                parsed, source=filename,
                **({"standard_no": filename, "version": kb_version} if doc_type == "standard" else {}),
            )

        # 表格类（FAQ/工单）行内无 source 列时补文件名，保证来源可追溯
        for c in chunks:
            if not c.metadata.get("source"):
                c.metadata["source"] = filename

        # sidecar 元数据合并（不覆盖行内已有值）
        for c in chunks:
            c.metadata.setdefault("asset_id", asset_id)
            c.metadata.setdefault("source_version", kb_version)
            if doc_type == "fault_case":
                c.metadata.setdefault("ticket_id", c.metadata.get("source") or "")
        if extra_meta:
            for c in chunks:
                for k, v in extra_meta.items():
                    row_network = (doc_type in TABLE_CHUNKERS and k == "network_tech"
                                   and bool(c.metadata.get(k)))
                    if strict_metadata and not row_network and k not in {"source", "doc_type", "parent_id", "step_index", "question", "answer"}:
                        c.metadata[k] = v
                    else:
                        c.metadata.setdefault(k, v)

        # 表格类行内已脱敏；文本类在 chunk 级补一遍
        chunks = [c if doc_type in TABLE_CHUNKERS else _resanitize(c) for c in chunks]
        if tokenizer_chunking:
            from qa_core.indexing.chunkers.token_budget import bounded_chunks
            # Count the exact normalized passage that will be stored, not a pre-NFKC version.
            for chunk in chunks:
                chunk.text = normalize_text(chunk.text)
            tokenizer = getattr(getattr(embedder, "model", None), "tokenizer", None)
            if tokenizer is None:
                from transformers import AutoTokenizer
                tokenizer = AutoTokenizer.from_pretrained(get_settings().embedding_model_path, local_files_only=True)
            model_limit = getattr(getattr(embedder, "model", None), "max_seq_length", 1200)
            model_limit = model_limit if isinstance(model_limit, int) else 1200
            chunks = bounded_chunks(chunks, tokenizer, max_tokens=min(450, model_limit),
                                    atomic_tokens=min(1200, model_limit))

        # 重复检测：重复 chunk 直接丢弃
        unique_chunks: list[Chunk] = []
        dup = 0
        for c in chunks:
            key = dedup_key(c)
            if strict_metadata:
                # Candidate migration must not deduplicate across access boundaries.
                scope = {k: c.metadata.get(k) for k in ("tenant_id", "dept_id", "visibility",
                         "allowed_roles", "role_scope", "security_level", "status",
                         "source_version", "effective_from", "effective_to")}
                key += json.dumps(scope, sort_keys=True, ensure_ascii=False)
            if key in seen_keys:
                dup += 1
                continue
            seen_keys.add(key)
            unique_chunks.append(c)
        stats["duplicates"] += dup
        if not unique_chunks:
            dao.set_status(fh, kb_version, "done", chunk_count=0)
            return

        dao.set_status(fh, kb_version, "embedded")
        texts = [c.text for c in unique_chunks]
        vectors = embedder.embed_passages(texts)

        rows = []
        for i, (c, vec) in enumerate(zip(unique_chunks, vectors)):
            chunk_id = hashlib.sha256(json.dumps(
                [kb_version, c.metadata.get("tenant_id", "default"), c.metadata.get("asset_id", ""),
                 fh, i, c.text], ensure_ascii=False, separators=(",", ":")).encode()).hexdigest()[:32]
            rows.append(to_milvus_row(c, chunk_id, vec, kb_version))

        collection = (get_settings().faq_collection if doc_type == "faq"
                      else get_settings().doc_collection)
        supported = _collection_field_names(client, collection)
        if supported:
            rows = [{k: v for k, v in row.items() if k in supported} for row in rows]
        # Strict candidate runs can resume after a crash between insert and DAO done.
        (client.upsert if strict_metadata else client.insert)(collection_name=collection, data=rows)
        if record_locations:
            from qa_core.indexing.parsers.locations import locate_chunks
            from qa_core.storage.location_dao import LocationDao
            from qa_core.retrieval.filters import _to_doc
            positions = locate_chunks(path, unique_chunks)
            if file_hash_of(path) != fh:
                raise ValueError("source changed while recording locations")
            locations = LocationDao(dao.engine)
            locations.ensure_schema()
            for row, position in zip(rows, positions):
                if position:
                    locations.save(_to_doc({"id": row["id"], "entity": row}), position)

        dao.set_status(fh, kb_version, "loaded", chunk_count=len(rows))
        # 元数据完备率与 SOP 校验在质量门禁做；此处仅标记完成
        dao.set_status(fh, kb_version, "done", chunk_count=len(rows))
        stats["done"] += 1
        # 小文件逐个 flush 的固定成本很高；每 10 个成功文件批量封段，任务结束前再强制 flush。
        if stats["done"] % 10 == 0:
            client.flush(collection)
    except Exception as exc:  # noqa: BLE001
        dao.set_status(fh, kb_version, "failed", error_msg=f"{type(exc).__name__}: {exc}"[:500])
        stats["failed"] += 1
        print(f"[failed] {filename}: {exc}")


def _resanitize(chunk: Chunk) -> Chunk:
    text, _counts = sanitize(chunk.text)
    chunk.text = text
    return chunk


class IngestCancelled(RuntimeError):
    """由持久化任务的取消标记触发，在文件边界安全停止。"""


@guarded_write
def run_rebuild(kb_version: str, mode: str = "incremental", raw_dir: str | Path | None = None,
                *, paths: list[Path] | None = None, progress=None, should_cancel=None,
                strict_metadata: bool = False, embedder_override=None,
                record_locations: bool = False, tokenizer_chunking: bool = False,
                metadata_override: dict | None = None, reviewed_source_map: str | Path | None = None) -> dict:
    """可被 CLI 与独立 Worker 共用的入库主体。

    仍以 (file_hash, kb_version) 保证文件级幂等；progress 在每个文件完成后更新，
    should_cancel 在文件边界检查，避免中断一次 Milvus 写入的中间状态。
    """
    settings = get_settings()
    source_dir = Path(raw_dir) if raw_dir else Path(settings.data_dir) / "raw"
    sidecar_path = source_dir / "meta.json"
    sidecar = json.loads(sidecar_path.read_text(encoding="utf-8")) if sidecar_path.is_file() else {}
    if reviewed_source_map is not None:
        if not strict_metadata or metadata_override:
            raise ValueError("审核映射必须严格入库，且禁止额外元数据覆盖")
        from qa_core.ingest.source_identity import validate_mapping
        sidecar = validate_mapping(source_dir, json.loads(Path(reviewed_source_map).read_text(encoding="utf-8")), sidecar)
    from pymilvus import MilvusClient

    client = MilvusClient(uri=settings.milvus_uri, timeout=60)
    if strict_metadata:
        required = {"tenant_id", "dept_id", "visibility", "allowed_roles", "role_scope",
                    "security_level", "status", "source_version", "effective_from", "effective_to"}
        for collection in (settings.doc_collection, settings.faq_collection):
            missing = required - (_collection_field_names(client, collection) or set())
            if missing:
                client.close()
                raise ValueError(f"candidate schema incomplete: {collection}: {sorted(missing)}")
    dao = KbDao(settings.mysql_dsn)
    dao.ensure_version(kb_version, settings.scenario)
    from qa_core.storage.version_manifest import bind_candidate
    bind_candidate(settings, kb_version)
    embedder = embedder_override or get_embedder(settings.embedding_model_path, settings.embedding_device)
    files = (sorted(paths) if paths is not None else
             sorted(p for p in source_dir.iterdir()
                    if p.is_file() and not p.name.startswith(".") and p.name != "meta.json"))
    if reviewed_source_map is not None and {p.name for p in files} != set(sidecar):
        client.close()
        raise ValueError("审核映射与实际入库文件清单不一致")
    stats = {"done": 0, "skipped": 0, "failed": 0, "duplicates": 0,
             "files": len(files), "mode": mode, "kb_version": kb_version}
    seen: set[str] = set()
    if progress:
        progress("loading_models", 0, len(files), stats)
    for index, path in enumerate(files, 1):
        if should_cancel and should_cancel():
            raise IngestCancelled("cancel requested")
        process_file(client, dao, embedder, path, kb_version, seen, stats,
                     extra_meta={**sidecar.get(path.name, {}), **(metadata_override or {})}, strict_metadata=strict_metadata,
                     record_locations=record_locations, tokenizer_chunking=tokenizer_chunking)
        if progress:
            progress("indexing", index, len(files), stats)
    # 在线质量门禁依赖强一致统计；所有文件完成后统一确保两个集合可见。
    client.flush(settings.doc_collection)
    client.flush(settings.faq_collection)
    return stats


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--kb-version", required=True)
    parser.add_argument("--mode", choices=["full", "incremental"], default="incremental")
    parser.add_argument("--record-locations", action="store_true",
                        help="为新入库文本资料保存内容绑定的原文位置，不重建旧库")
    parser.add_argument("--tokenizer-chunking", action="store_true", help="新资料采用真实 tokenizer 限长")
    parser.add_argument("--strict-metadata", action="store_true", help="严格应用候选元数据")
    parser.add_argument("--reviewed-source-map", help="完整、正文摘要绑定且已独立审核的来源映射 JSON")
    parser.add_argument("--raw-dir", default=None,
                        help="原始文档目录；默认使用 DATA_DIR/raw")
    args = parser.parse_args()

    stats = run_rebuild(args.kb_version, args.mode, args.raw_dir, record_locations=args.record_locations,
                        tokenizer_chunking=args.tokenizer_chunking, strict_metadata=args.strict_metadata,
                        reviewed_source_map=args.reviewed_source_map)
    print(json.dumps(stats, ensure_ascii=False, indent=2))
    if stats["failed"]:
        sys.exit(2)


if __name__ == "__main__":
    main()
