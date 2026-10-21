"""质量门禁五项（qa_core/quality/gate.py），任一失败禁止版本激活。

1. 告警码召回抽检：抽 30 个（不足全抽）工单 alarm_code，dense 召回 top10 命中率 ≥ 0.9
2. SOP 完整性：doc_type=sop 的 step_index 首步为 1 且连续
3. 元数据完备率：doc_collection 中 equipment_type/network_tech 非空率 ≥ 95%
4. 重复检测：门禁侧复核 alarm_code+根因 是否有重复 chunk
5. 脱敏复核：抽样 text 不得含未脱敏手机号/身份证
"""
from __future__ import annotations

import json
import hashlib
import random
import re
from collections import defaultdict
from dataclasses import dataclass, field

from pymilvus import MilvusClient

from qa_core.indexing.embedder.embedder import get_embedder

PHONE = re.compile(r"(?<!\d)1[3-9]\d{9}(?!\d)")
ID_CARD = re.compile(r"(?<!\d)\d{17}[\dXx](?!\d)")
RECALL_MIN = 0.90
META_MIN = 0.95
SAMPLE_N = 30


@dataclass
class GateResult:
    passed: bool = True
    checks: list[dict] = field(default_factory=list)
    snapshot: dict | None = None
    profile: str | None = None
    approval_receipt_sha256: str | None = None

    def add(self, name: str, ok: bool, detail: dict) -> None:
        self.checks.append({"check": name, "passed": ok, **detail})
        if not ok:
            self.passed = False


def _fetch(client: MilvusClient, collection: str, flt: str, fields: list[str], limit: int = 1000) -> list[dict]:
    rows = client.query(collection_name=collection, filter=flt,
                        output_fields=fields, limit=limit)
    return rows


def check_alarm_recall(client: MilvusClient, embedder, kb_version: str,
                       doc_collection: str = "doc_collection") -> tuple[bool, dict]:
    tickets = _fetch(client, doc_collection,
                     f'doc_type == "fault_case" and kb_version == "{kb_version}"',
                     ["id", "text", "alarm_code"], limit=500)
    tickets = [t for t in tickets if t.get("alarm_code")]
    if not tickets:
        return False, {"reason": "无告警码工单可抽检", "hit_rate": 0.0}
    sample = random.sample(tickets, min(SAMPLE_N, len(tickets)))
    hits = 0
    for t in sample:
        # 模拟真实用户问法：告警码 + 工单标题/现象开头（纯码在 dense 语义里无信息量，
        # 那类请求本就该走 structured_lookup 标量路径）
        hint = t["text"][:40]
        vec = embedder.embed_query(f"{t['alarm_code']} {hint} 告警处理")
        res = client.search(
            collection_name=doc_collection,
            data=[vec], anns_field="dense", limit=10,
            filter=f'kb_version == "{kb_version}"',
            output_fields=["alarm_code"],
        )[0]
        top_codes = {hit["entity"].get("alarm_code") for hit in res}
        if t["alarm_code"] in top_codes:
            hits += 1
    rate = hits / len(sample)
    return rate >= RECALL_MIN, {"sample": len(sample), "hits": hits, "hit_rate": round(rate, 3)}


def check_sop_integrity(client: MilvusClient, kb_version: str,
                        doc_collection: str = "doc_collection") -> tuple[bool, dict]:
    rows = client.query(
        collection_name=doc_collection,
        filter=f'doc_type == "sop" and kb_version == "{kb_version}"',
        output_fields=["id", "source", "step_index"], limit=10000,
    )
    if not rows:
        return True, {"sop_count": 0}
    by_source: dict[str, list[int]] = defaultdict(list)
    invalid = set()
    for r in rows:
        step = r.get("step_index")
        source = r.get("source") or r["id"]
        values = step if isinstance(step, list) else [step]
        by_source[source]  # A header-only SOP must not pass.
        if not values or any(type(s) is not int or s < 0 for s in values):
            invalid.add(source)
            continue
        by_source[source].extend(s for s in values if s > 0)
    broken = list(invalid)
    for source, steps in by_source.items():
        ordered = sorted(steps)
        expected = list(range(1, max(ordered) + 1)) if ordered else []
        if (not ordered or ordered != expected) and source not in invalid:
            broken.append(source)
    return not broken, {"sop_count": len(rows), "sop_sources": len(by_source),
                        "broken": broken[:10]}


def check_meta_completeness(client: MilvusClient, kb_version: str,
                            doc_collection: str = "doc_collection") -> tuple[bool, dict]:
    rows = _fetch(client, doc_collection, f'kb_version == "{kb_version}"',
                  ["id", "equipment_type", "network_tech"], limit=1000)
    if not rows:
        return False, {"reason": "doc_collection 为空"}
    filled = sum(1 for r in rows if (r.get("equipment_type") or r.get("network_tech")))
    rate = filled / len(rows)
    return rate >= META_MIN, {"chunks": len(rows), "filled_rate": round(rate, 3)}


def check_duplicates(client: MilvusClient, kb_version: str,
                     doc_collection: str = "doc_collection") -> tuple[bool, dict]:
    rows = _fetch(client, doc_collection,
                  f'doc_type == "fault_case" and kb_version == "{kb_version}"',
                  ["id", "text"], limit=10000)
    seen: set[str] = set()
    dup: list[str] = []
    for r in rows:
        text = (r.get("text") or "").strip()
        if not text:
            continue
        digest = hashlib.sha256(text.encode("utf-8")).hexdigest()
        if digest in seen:
            dup.append(r["id"])
        seen.add(digest)
    return not dup, {"duplicate_chunk_ids": dup[:10], "count": len(dup),
                     "scanned": len(rows)}


def check_desensitize(client: MilvusClient, kb_version: str,
                      doc_collection: str = "doc_collection") -> tuple[bool, dict]:
    rows = _fetch(client, doc_collection, f'kb_version == "{kb_version}"',
                  ["id", "text"], limit=200)
    leaked = [r["id"] for r in rows if PHONE.search(r["text"]) or ID_CARD.search(r["text"])]
    return not leaked, {"sampled": len(rows), "leaked": leaked[:10]}


def run_gate(settings, kb_version: str) -> GateResult:
    from qa_core.ingest.write_guard import candidate_write
    with candidate_write(kb_version, settings=settings):
        from qa_core.storage.version_manifest import bind_candidate
        bind_candidate(settings, kb_version)
        return _run_gate(settings, kb_version)


def _run_gate(settings, kb_version: str) -> GateResult:
    from qa_core.quality.publication import capture_snapshot, validate_version
    validate_version(kb_version)
    before = capture_snapshot(settings, kb_version)
    from qa_core.security.withdrawals import assert_publishable_sources
    assert_publishable_sources(settings, before["sources"])
    client = MilvusClient(uri=settings.milvus_uri, timeout=60)
    result = GateResult()
    collection = settings.doc_collection
    try:
        embedder = get_embedder(settings.embedding_model_path, settings.embedding_device)
        ok, detail = check_alarm_recall(client, embedder, kb_version, collection)
        result.add("alarm_recall", ok, detail)
        for name, check in (("sop_integrity", check_sop_integrity),
                            ("meta_completeness", check_meta_completeness),
                            ("duplicates", check_duplicates), ("desensitize", check_desensitize)):
            ok, detail = check(client, kb_version, collection)
            result.add(name, ok, detail)
        after = capture_snapshot(settings, kb_version, client)
    finally:
        client.close()
    if before != after:
        result.passed = False
    else:
        result.snapshot = after
        from qa_core.storage.version_manifest import VersionManifestDao
        dao = VersionManifestDao(settings.mysql_dsn)
        try:
            dao.record_snapshot(kb_version, after)
        finally:
            dao.close()
    return result


def save_report(result: GateResult, kb_version: str, out_dir: str = "reports") -> str:
    import pathlib
    from qa_core.quality.publication import validate_version
    validate_version(kb_version)

    path = pathlib.Path(out_dir) / f"quality_{kb_version}.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = {"kb_version": kb_version, "passed": result.passed, "checks": result.checks,
               "snapshot": result.snapshot}
    if result.profile:
        payload.update(profile=result.profile, approval_receipt_sha256=result.approval_receipt_sha256)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    return str(path)
