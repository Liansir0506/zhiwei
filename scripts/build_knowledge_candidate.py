"""Build an immutable, offline data candidate; never change a live KB or old labels.

This is synthetic lab knowledge, NOT vendor procedures or measured production data.
Generated artifacts are deliberately not executable evaluation input.
"""
from __future__ import annotations

import argparse
import copy
import hashlib
import json
import re
import sys
from collections import defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
from scripts.calibrate_eval_set import SCOPE_FIELDS, canonical_body, load_catalog

VERSION = "v4-quality-r1"

# Each pair deliberately has a different discriminating observation and action.
# No commands, real thresholds, manufacturer versions, or external authority invented.
BRANCHES = [
    ("AMF", "ALM-AMF-REG", "注册失败", [
        ("ingress", "入口积压", "入口队列增长而鉴权依赖响应稳定", "分时段对齐注册请求量、入口排队时间和鉴权响应时间", "排除鉴权依赖超时", "只在沙箱回放中降低请求注入速率，观察入口排队是否回落", "入口排队回落且注册成功比例恢复"),
        ("auth", "鉴权依赖超时", "入口队列稳定而鉴权依赖响应变慢", "对齐鉴权依赖响应时间、超时事件和入口队列", "排除入口积压", "只在沙箱恢复鉴权依赖模拟器的正常响应，不调整入口限流", "鉴权响应恢复且注册超时减少"),
    ]),
    ("SMF", "ALM-SMF-PDU", "会话建立超时", [
        ("policy", "策略依赖超时", "策略请求超时而用户面控制链路响应正常", "检查策略请求关联标识、响应时间和用户面控制链路事件", "排除用户面控制链路中断", "只在沙箱恢复策略依赖模拟器，不重建用户面会话", "策略请求完成且会话建立恢复"),
        ("control", "用户面控制链路中断", "策略响应正常而用户面控制链路心跳丢失", "对齐心跳丢失、控制链路可达性和策略响应", "排除策略依赖超时", "只在沙箱撤销控制链路故障注入，不修改策略内容", "心跳恢复且会话建立恢复"),
    ]),
    ("UPF", "ALM-UPF-DROP", "转发丢包", [
        ("queue", "队列拥塞", "入口流量突增且队列占用增长，转发规则未变化", "记录入口速率、队列占用、丢包计数和转发规则快照", "排除转发规则漂移", "只在沙箱降低流量注入速率，不刷新转发规则", "队列占用下降且丢包减少"),
        ("rule", "转发规则漂移", "队列占用稳定且丢包始于规则快照变化", "对齐规则快照差异、变更时间和分流丢包计数", "排除队列拥塞", "审批后只在沙箱恢复已记录的规则快照，不通过限速掩盖问题", "规则差异消失且受影响流恢复"),
    ]),
    ("NRF", "ALM-NRF-DISC", "服务发现失败", [
        ("expiry", "注册记录过期", "网络请求可达但目标服务注册记录已过有效期", "读取服务记录有效期、发现请求时间和请求返回内容", "排除发现链路不可达", "只在沙箱重新注册对应模拟服务实例，不更改网络路由", "发现返回包含有效的目标实例"),
        ("network", "发现链路不可达", "目标服务注册记录有效但发现请求连接超时", "检查请求连接事件、路径可达性和目标记录有效期", "排除注册记录过期", "只在沙箱撤销网络故障注入，不批量注销服务", "请求连接恢复且发现返回正常"),
    ]),
    ("UDM", "ALM-UDM-AUTH", "鉴权失败", [
        ("subscription", "模拟订阅缺失", "查询请求正常完成但模拟订阅记录不存在", "核对脱敏订阅标识、查询结果和订阅数据快照", "排除查询依赖不可用", "审批后只在沙箱恢复预置模拟订阅，不写真实用户数据", "订阅查询返回预置记录且模拟鉴权恢复"),
        ("dependency", "查询依赖不可用", "模拟订阅快照存在但查询请求超时", "对齐查询超时、依赖可达性和订阅快照时间", "排除模拟订阅缺失", "只在沙箱恢复查询依赖，不创建重复订阅", "查询请求完成且模拟鉴权恢复"),
    ]),
    ("gNodeB", "ALM-NGAP-SCTP", "连接中断", [
        ("path", "传输路径异常", "两端配置快照一致但传输路径出现丢包", "对齐两端配置快照、路径丢包和连接中断时间", "排除对端配置漂移", "只在沙箱撤销路径丢包注入，不修改对端地址", "路径丢包消失且连接恢复"),
        ("peer", "对端配置漂移", "传输路径可达但对端地址与批准快照不一致", "核对对端地址、批准变更快照和路径可达性", "排除传输路径异常", "审批后只在沙箱恢复对端地址快照，不重启整台设备", "对端地址与快照一致且连接恢复"),
    ]),
]


def sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def encode(value) -> bytes:
    return (json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True) + "\n").encode()


def dedup_key(item: dict) -> str:
    # Do not merge across scope, lifecycle, versions, equipment or provenance.
    names = (*SCOPE_FIELDS, "equipment_type", "vendor", "network_tech", "origin_type", "synthetic")
    identity = {k: item["full_metadata"].get(k) for k in names}
    if item["full_metadata"].get("synthetic") is not True:
        identity["evidence_refs"] = item["full_metadata"].get("evidence_refs")
    return sha(encode({"body": item["body_sha256"], "metadata": identity}))


def build(raw: Path, manifest_path: Path, output: Path) -> dict:
    raw, output = raw.resolve(), output.resolve()
    if output == raw or output in raw.parents or raw in output.parents:
        raise ValueError("candidate output must be separate from source corpus")
    if output.exists():
        raise ValueError("refuse to overwrite any existing candidate directory")
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    catalog, source_hashes = load_catalog(raw, manifest)
    old_meta = json.loads((raw / "meta.json").read_text(encoding="utf-8"))
    artifacts, metadata, mappings = {}, {}, []
    families = defaultdict(list)
    for name in sorted(old_meta):
        if name.startswith("sop_") and name.endswith(".md"):
            item = {**catalog[name], "full_metadata": old_meta[name]}
            families[dedup_key(item)].append(name)
        else:
            artifacts[f"raw/{name}"] = (raw / name).read_bytes()
            metadata[name] = copy.deepcopy(old_meta[name])
    for members in sorted(families.values(), key=lambda m: m[0]):
        kept = members[0]
        artifacts[f"raw/{kept}"] = (raw / kept).read_bytes()
        metadata[kept] = copy.deepcopy(old_meta[kept])
        # r3 synthetic SOP link IDs rotate independently of body. They are not
        # verified support; preserve them as provenance, not distinct knowledge.
        # Never merge different refs for non-synthetic material.
        metadata[kept]["evidence_refs"] = sorted({ref for member in members
                                                  for ref in old_meta[member].get("evidence_refs", [])})
        metadata[kept]["external_reference_support_verified"] = False
        mappings.append({"retained": kept, "removed_from_candidate_only": members[1:],
                         "body_sha256": catalog[kept]["body_sha256"],
                         "original_metadata": {member: old_meta[member] for member in members},
                         "scope_checked": True, "evaluation_aliases_approved": False})

    review_cases = []
    for equipment, alarm, topic, branches in BRANCHES:
        names = [f"sop_lab_{alarm.lower().replace('-', '_')}_{b[0]}.md" for b in branches]
        for i, (_, cause, observation, check, exclude, action, recovery) in enumerate(branches):
            name, negative = names[i], names[1 - i]
            context = f"{equipment}；模拟告警 {alarm}；Lab-2026.09；条件：{observation}；分支：{cause}。"
            text = (f"# {topic}：{cause}沙箱分支\n\n"
                    "项目自编模拟材料，不是厂商规范、真实工单或生产操作建议。仅适用 Lab-2026.09 沙箱；真实设备需核对内部批准流程。\n\n"
                    f"适用：{context}\n不适用：{branches[1-i][2]}。上下文不足时先追问，不根据同一告警码推断原因。\n\n"
                    f"步骤 1：{context}只读取证：{check}；{exclude}。\n\n"
                    f"步骤 2：{context}确认排除项和设备版本；混合症状、版本不符或证据不足时停止并转人工，不执行本分支。\n\n"
                    f"步骤 3：{context}{action}。操作前需人工审批并保存沙箱快照；恶化时立即撤销实验动作并恢复快照。\n\n"
                    f"步骤 4：{context}恢复验证：{recovery}；连续观察三个实验采样周期，记录单位与采样时间；未恢复则停止实验并转人工。\n")
            artifacts[f"raw/{name}"] = text.encode()
            metadata[name] = {
                "tenant_id": "default", "dept_id": "public", "visibility": "public",
                "allowed_roles": [], "role_scope": [], "security_level": "internal",
                "status": "active", "effective_from": "2026-09-27T00:00:00+00:00",
                "effective_to": "2028-09-27T00:00:00+00:00", "equipment_type": equipment,
                "device_type": equipment, "network_tech": "5G", "vendor": "OpenLab",
                "alarm_code": alarm, "origin_type": "synthetic_lab", "synthetic": True,
                "license_status": "project_authored", "evidence_refs": [], "quality": "high",
                "review_status": "ai_draft", "source_system": "lab_scenario",
                "generator_version": "knowledge-quality-v1", "generation_seed": "20260927",
                "applicable_lab_version": "Lab-2026.09",
            }
            query = f"Lab-2026.09 沙箱中，{equipment}出现{alarm}，{observation}，应如何取证、排除另一原因并安全验证恢复？"
            atoms = [observation, check, exclude, action, recovery, "人工审批", "停止", "快照"]
            quotes = []
            for atom in atoms:
                line = next(n for n, value in enumerate(text.splitlines(), 1) if atom in value)
                quotes.append({"quote": atom, "source": name, "line": line, "file_sha256": sha(text.encode())})
            review_cases.append({"id": f"DQ{i+1:02d}-{alarm}", "split": "dev", "query": query,
                                 "proposed_labels": {"expect_hit": name, "hard_negatives": [negative]},
                                 "negative_kind": "same_alarm_inapplicable_condition",
                                 "required_evidence": quotes, "human_approved": False,
                                 "group_id": alarm, "applicable_lab_version": "Lab-2026.09"})

    for name, meta in metadata.items():
        meta["parent_source_version"] = meta.get("source_version", "v4-foundation-r3")
        meta.update(dataset_id=VERSION, source_version=VERSION)
        if meta.get("origin_type") == "public_reference_derived":
            # These are self-authored cards, NOT fetched or verified normative text.
            meta.update(origin_type="project_authored_card", synthetic=True,
                        license_status="project_authored", external_reference_support_verified=False)
    artifacts["raw/meta.json"] = encode(metadata)
    generated_manifest = {"version": VERSION, "status": "candidate_not_published",
                          "parent_manifest_sha256": sha(manifest_path.read_bytes()),
                          "files": [{"filename": p.removeprefix("raw/"), "sha256": sha(data),
                                     "origin_type": metadata[p.removeprefix("raw/")]["origin_type"]}
                                    for p, data in sorted(artifacts.items()) if p != "raw/meta.json"]}
    artifacts["manifests/dataset_manifest.json"] = encode(generated_manifest)
    artifacts["manifests/dedup-map.json"] = encode(mappings)
    source_catalog = manifest_path.parent / "sources.json"
    if source_catalog.exists():
        artifacts["manifests/sources.json"] = source_catalog.read_bytes()
    artifacts["evaluation/review-cases.json"] = encode({
        "kb_version": VERSION, "status": "draft_not_executable", "review_cases": review_cases,
        "metrics_eligible": False, "independent_frozen": False,
        "note": "AI-authored dev probes; corpus and probes co-designed, not independent evaluation."})
    report = {
        "version": VERSION, "status": "offline_candidate_complete_not_indexed",
        "old_files": len(old_meta), "candidate_files": len(metadata),
        "old_sop_files": sum(len(v) for v in families.values()),
        "retained_old_sop_files": len(families), "added_differentiated_sop_files": len(review_cases),
        "removed_duplicate_copies_in_candidate": sum(len(v)-1 for v in families.values()),
        "required_quote_count": sum(len(c["required_evidence"]) for c in review_cases),
        "old_source_sha256": source_hashes, "new_hit_at_5": None, "old_hit_at_5": 0.827,
        "index_changed": False, "online_parameters_changed": False,
        "security_schema_verified": False, "human_approved": False,
        "remaining": ["human semantic review", "isolated full-schema candidate index",
                      "same-ruler real retrieval comparison", "independent evaluation and release acceptance"],
    }
    # Validate in memory before the first write.
    for case in review_cases:
        positive = artifacts["raw/" + case["proposed_labels"]["expect_hit"]].decode()
        negative = artifacts["raw/" + case["proposed_labels"]["hard_negatives"][0]].decode()
        if canonical_body(positive) == canonical_body(negative):
            raise ValueError("hard negative duplicates positive")
        for quote in case["required_evidence"]:
            if quote["quote"] not in positive.splitlines()[quote["line"]-1]:
                raise ValueError("invalid quote location")
    if any(sha((raw / name).read_bytes()) != digest for name, digest in source_hashes.items()):
        raise ValueError("source changed during generation")
    artifacts["comparison.json"] = encode(report)
    artifacts["artifact-hashes.json"] = encode({p: sha(data) for p, data in sorted(artifacts.items())})
    for path, data in artifacts.items():
        target = output / path
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(data)
    return report


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--raw", type=Path, required=True)
    parser.add_argument("--manifest", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    report = build(args.raw, args.manifest, args.output)
    print(json.dumps({k: v for k, v in report.items() if k != "old_source_sha256"}, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
