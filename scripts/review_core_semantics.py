"""AI-assisted offline semantic revision; not approval, retrieval or production advice."""
import argparse
import copy
import json
from pathlib import Path

from scripts.build_core_source_review import build, validate_package
from scripts.prepare_stage4_review import contained_file, digest
from qa_core.quality.review_contract import bind_pending, audit_groups, check_approval


# Explicit per-branch reasoning written after reading all twelve original SOPs.
NOTES = {
    "lab.amf.registration.ingress": "入口排队增长与鉴权稳定共同支持入口分支；按时间对齐请求量/排队/鉴权，不能只凭注册失败排除鉴权故障；只减少实验注入，不给生产限流参数。",
    "lab.amf.registration.auth": "入口稳定且鉴权变慢支持依赖分支；核对依赖响应和超时与队列的同一时间窗；恢复模拟器而非调整入口限流，注册超时减少并不等于所有业务恢复。",
    "lab.smf.pdu.policy": "策略超时且控制链路正常支持策略分支；关联标识用于避免混入其他会话；只恢复策略模拟器，不能由此建议重建真实会话。",
    "lab.smf.pdu.control": "策略正常且控制心跳丢失支持控制链路分支；心跳丢失需结合可达性取证，不单凭心跳推断根因；仅撤销实验注入，不修改策略。",
    "lab.upf.drop.queue": "入口突增/队列增长/规则未变化三者一起支持拥塞分支；规则快照与时序用于条件排除，不代表排除所有丢包原因；减少注入不是生产容量规划结论。",
    "lab.upf.drop.rule": "稳定队列与规则变化后的丢包支持规则分支；时序相关还不是生产因果证明；需已记录且可恢复的沙箱快照，不以限速掩盖规则问题。",
    "lab.nrf.discovery.expiry": "请求可达且目标记录过期支持记录分支；必须对齐有效期、请求时间与返回内容；仅重注册对应模拟实例，不可批量注销真实服务。",
    "lab.nrf.discovery.network": "有效记录与连接超时支持链路分支；连接事件和路径取证是必要条件，不能把所有发现失败都归于网络；撤销网络注入而非注销服务。",
    "lab.udm.auth.dependency": "订阅快照存在且查询超时支持依赖分支；历史快照存在不保证当前记录仍有效，需时间对齐确认；恢复依赖，不新建订阅。",
    "lab.udm.auth.subscription": "查询完成且预置订阅不存在支持缺失分支；核对脱敏标识防止查错对象；仅恢复预置模拟记录，严禁据此写真实用户数据。",
    "lab.ngap.sctp.path": "配置快照一致且路径丢包支持路径分支；需对齐两端快照、丢包与中断时间；撤销实验丢包，不修改生产对端地址。",
    "lab.ngap.sctp.peer": "路径可达且地址偏离批准快照支持配置分支；必须确认快照批准与适用对象；仅恢复沙箱地址快照，不重启整机。",
}

ROLES = [
    ("scope", 3, "沙箱及非生产边界是安全回答必要前提"),
    ("applicability", 5, "条件与版本共同确定分支，不从告警码独自推因"),
    ("negative_condition", 6, "另一分支只在明确相反条件下不适用"),
    ("read_only_evidence", 8, "完整取证及排除对象，不能只报结论"),
    ("stop_or_escalate", 10, "包含混合症状、版本不符、证据不足的停止条件"),
    ("bounded_action", 12, "沙箱动作及不可采取的替代动作"),
    ("approval_and_rollback", 12, "人工审批、操作前快照与恶化回退是不同义务，不合并成一个词"),
    ("recovery_validation", 14, "恢复信号、三个实验采样周期和未恢复转人工需一起保留"),
]


def verify_hashes(root):
    root = Path(root).resolve()
    hashes = json.loads((root / "artifact-hashes.json").read_text(encoding="utf-8"))
    for name, expected in hashes.items():
        if digest(contained_file(root, name).read_bytes()) != expected:
            raise ValueError("review package artifact changed")
    return digest((root / "artifact-hashes.json").read_bytes())


def revise(candidate, prior):
    candidate, prior = Path(candidate).resolve(), Path(prior).resolve()
    prior_hash = verify_hashes(prior)
    validate_package(candidate, prior)
    base = build(candidate)
    source_map = {s["filename"]: s for s in base["source-proposals.json"]["entries"]}
    cases, paths = [], []
    for original in base["label-workbook.json"]["cases"]:
        item = copy.deepcopy(original)
        source = source_map[item["positive_source"]]
        lines = contained_file(candidate / "raw", item["positive_source"]).read_text(encoding="utf-8").splitlines()
        negative_name = item["condition_negatives"][0]
        negative_lines = contained_file(candidate / "raw", negative_name).read_text(encoding="utf-8").splitlines()
        condition = lines[4].split("条件：", 1)[1].split("；分支：", 1)[0]
        opposite = negative_lines[4].split("条件：", 1)[1].split("；分支：", 1)[0]
        if not all(token in lines[11] for token in ("人工审批", "快照", "恶化", "恢复快照")):
            raise ValueError("missing full safety obligations")
        if opposite not in lines[5] or condition not in negative_lines[5] or condition not in item["query"]:
            raise ValueError("branch conditions do not support conditional negative")
        def support(line, filename=item["positive_source"], body=lines):
            return {"source": filename, "file_sha256": source_map[filename]["sha256"],
                    "line": line, "quote": body[line - 1]}
        item["prior_evidence_review"] = [{"unit_id": unit["unit_id"],
            "decision": "retain_claim_expand_context" if index < 5 else "replace_keyword_only_support",
            "reason": "主张必要，但需完整版本/条件语句" if index < 5 else "孤立词不能证明审批、停止或回退条件",
            "original_support_options": unit["support_options"]}
            for index, unit in enumerate(item["evidence_units"])]
        item["evidence_units"] = [{"unit_id": f"{item['id']}-R2-{role}", "role": role,
            "required_proposal": True, "necessity_reason": reason, "support_options": [support(line)]}
            for role, line, reason in ROLES]
        item["semantic_review"] = {"author_kind": "AI_assisted_not_human_approval", "status": "pending_human_review",
            "branch_reasoning": NOTES[source["proposed_source_id"]],
            "negative_reasoning": f"本题给出：{condition}；另一分支要求：{opposite}。只对本题完整条件判为负例；混合或未知条件不能排除它。",
            "negative_support": support(5, negative_name, negative_lines),
            "optional_evidence": ["标题和每步重复的网元/告警/条件前缀不是额外独立证据；适用条件已单独计入"],
            "sufficiency": "仅足以描述该沙箱的四步方案，不证明当前故障根因、动作已获批准或业务已经恢复；不得输出执行命令或生产参数。",
            "unresolved": ["三个采样周期的具体时长和基线阈值未定义", "没有审批记录/快照可恢复性实测或真实生产适用依据"]}
        item["expected_state_proposal"] = "ANSWERED"
        item["answer_contract"] = {"context": "authorized complete Lab-2026.09 scenario with complete evidence",
            "mode": "read_only_plan_not_action_execution", "must_cover": [r[0] for r in ROLES],
            "must_not": ["声称已执行/已批准/已恢复", "把模拟步骤用于生产", "发明时间/阈值/命令"]}
        item["negative_review_status"] = "AI_condition_review_pending_human"
        bind_pending(item); cases.append(item)
        # These are scenario fixtures, not newly independent questions or runtime successes.
        for kind, query, state, context, reason in [
            ("clarification", f"Lab-2026.09 沙箱出现 {item['topic_groups'][0]}，应该怎么恢复？", "NEEDS_CLARIFICATION",
             {"branch_conditions": "missing", "evidence_fixture": "both_authorized_branches"},
             f"追问版本/症状和观测：{condition}，还是{opposite}；不先选分支"),
            ("no_evidence", item["query"], "NO_EVIDENCE",
             {"evidence_fixture": "empty_after_filtering"}, "条件虽齐全但没有可引用证据，不能凭题面编步骤"),
            ("permissions", item["query"], "NO_EVIDENCE",
             {"request_scope": {"tenant_id": "stage4-foreign-tenant", "roles": ["ops"]},
              "source_scope": source["scope_metadata"], "evidence_fixture": "only_default_tenant_sources"},
             "跨租户证据不可见；合法认证问答返回无证据，不虚构 PERMISSION_DENIED 终态或泄露隐藏文档"),
            ("production_boundary", item["query"].replace("Lab-2026.09 沙箱", "真实生产设备"), "NO_EVIDENCE",
             {"environment": "production", "evidence_fixture": "synthetic_lab_only"},
             "现有材料仅适用沙箱，缺真实版本与批准操作依据；不能输出生产恢复方案"),
        ]:
            paths.append(bind_pending({"id": f"{item['id']}-{kind}", "parent_case_id": item["id"],
                "query": query, "stratum": kind, "split": "dev", "development_exposed": True,
                **{f: copy.deepcopy(item[f]) for f in ("source_ids", "body_families", "topic_groups", "template_groups")},
                "fixture_context": context, "expected_state_proposal": state, "expected_behavior": reason,
                "answerable_retrieval_denominator": False, "expected_action_execution": False,
                "expected_citations": [], "label_review_status": "AI_assisted_pending_human"}))
    groups = audit_groups(cases + paths)
    return {"semantic-workbook.json": {"protocol": "core-semantic-review-v2", "metrics_eligible": False,
                "prior_package_sha256": prior_hash, "cases": cases},
        "state-paths.json": {"protocol": "dev-state-fixtures-v1", "runtime_verified": False, "cases": paths},
        "split-audit.json": {"connected_groups": groups, "note": "declared links, not semantic similarity detector"},
        "summary.json": {"reviewed_cases": len(cases), "old_atoms_preserved": 96, "revised_claim_units": 96,
            "keyword_support_replacements": 36, "state_fixtures": len(paths), "connected_groups": len(groups),
            "approved_cases": 0, "frozen_cases": 0, "runtime_verified": False, "ingestion_run": False}}


def validate(candidate, prior, package, *, require_approved=False):
    verify_hashes(package)
    expected = revise(candidate, prior)
    for name, value in expected.items():
        actual = json.loads(contained_file(Path(package), name).read_text(encoding="utf-8"))
        # This revision is AI-only. No implied promotion or approval export.
        if actual != value:
            raise ValueError("semantic revision differs from reviewed proposal")
        for case in actual.get("cases", []):
            check_approval(case, required=require_approved)
    return {"binding_valid": True, **expected["summary.json"]}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--candidate", required=True); parser.add_argument("--prior", required=True)
    parser.add_argument("--output"); parser.add_argument("--validate-package")
    parser.add_argument("--require-approved", action="store_true")
    args = parser.parse_args()
    if args.validate_package:
        if args.output: parser.error("validation cannot write output")
        print(json.dumps(validate(args.candidate, args.prior, args.validate_package, require_approved=args.require_approved)))
        return
    if not args.output or args.require_approved: parser.error("generation needs output; approval flag is validation only")
    output = Path(args.output).resolve()
    roots = [Path(args.candidate).resolve(), Path(args.prior).resolve()]
    if output.exists() or any(output.is_relative_to(r) or r.is_relative_to(output) for r in roots):
        raise ValueError("output must be new and separate")
    payload = revise(*roots)
    output.mkdir(parents=True, exist_ok=False)
    hashes = {}
    for name, item in payload.items():
        data = (json.dumps(item, ensure_ascii=False, sort_keys=True, indent=2) + "\n").encode()
        (output / name).write_bytes(data); hashes[name] = digest(data)
    (output / "artifact-hashes.json").write_text(json.dumps(hashes, indent=2), encoding="utf-8")
    print(json.dumps(payload["summary.json"]))


if __name__ == "__main__": main()
