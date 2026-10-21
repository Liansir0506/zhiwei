"""Build a bound, unapproved development ruler; never register or ingest sources."""
from __future__ import annotations

import argparse
from collections import Counter
import hashlib
import json
from pathlib import Path
import re
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from qa_core.quality.review_contract import audit_groups, bind_pending, check_approval


def encode(value):
    return (json.dumps(value, ensure_ascii=False, sort_keys=True, indent=2, allow_nan=False) + "\n").encode()


def sha(data):
    return hashlib.sha256(data).hexdigest()


def lab_records():
    """Explicit authored inputs, not captured traffic or vendor metric definitions."""
    inputs = {
        "amf": {"v09": {"errors": [100, 180], "requests": [1000, 1400]},
                "v10": [{"errors": 60, "requests": 200}, {"errors": 40, "requests": 200}]},
        "nrf": {"local_now_s": 120, "remote_expiry_s": 115, "remote_minus_local_s": -10},
        "upf": {"ingress": 1000, "matched": 800, "rule_drop": 40, "queue_drop": 10, "total_drop": 60},
        "smf": [{"tx": "A", "policy": [0, 20], "control": [25, 225]},
                {"tx": "B", "policy": [0, 180], "control": [185, 205]}],
    }
    a, n, u, s = (inputs[k] for k in ("amf", "nrf", "upf", "smf"))
    outputs = {
        "amf_v09_delta_error_ratio": (a["v09"]["errors"][1] - a["v09"]["errors"][0]) /
                                     (a["v09"]["requests"][1] - a["v09"]["requests"][0]),
        "amf_v10_window_error_ratios": [r["errors"] / r["requests"] for r in a["v10"]],
        "nrf_local_expiry_s": n["remote_expiry_s"] - n["remote_minus_local_s"],
        "nrf_remaining_s": n["remote_expiry_s"] - n["remote_minus_local_s"] - n["local_now_s"],
        "upf_rule_matched_ratio": u["rule_drop"] / u["matched"],
        "upf_queue_ingress_ratio": u["queue_drop"] / u["ingress"],
        "upf_unattributed_drop": u["total_drop"] - u["rule_drop"] - u["queue_drop"],
        "smf_durations_ms": [{"tx": r["tx"], "policy": r["policy"][1] - r["policy"][0],
                              "control": r["control"][1] - r["control"][0]} for r in s],
    }
    return {"origin": "AI-authored deterministic fixture, not executed network telemetry",
            "inputs": inputs, "computed_outputs": outputs}


NEW_NOTES = [
    ("manual_lab_amf_metric_versions.md", "ALM-AMF-REG", "AMF", "amf", "public", "public",
     """# AMF：累计计数与窗口计数的版本差异

项目自编模拟观测记录，非厂商指标定义、真实设备采样或生产操作建议。数据由 fixtures.json 中 amf 输入构造，计算可复现。
适用：AMF；Lab-2026.09 与 Lab-2026.10 指标模拟器；不适用其他版本。
Lab-2026.09 约定 errors/requests 是累计计数：t0=(100,1000)，t1=(180,1400)。
Lab-2026.10 约定 errors/requests 是每窗口计数：W1=(60,200)，W2=(40,200)。
09版区间失败比例=(180-100)/(1400-1000)=20%；180/1400混入历史数据，不能代表该区间。
10版窗口失败比例分别为60/200=30%、40/200=20%；不得对窗口值再作差分。
若累计计数从180降到20，必须先核对重启/重置边界；负增量不等于负故障率，不跨重置点计算。
两种计数模式不能仅凭字段名识别；缺少软件版本、模式声明或时间窗口时先追问，不选择公式。
这组数据只有失败计数，没有入口队列与鉴权时延，不能据此确定入口积压或鉴权依赖慢。
只读验证：保留模式声明、两次采样、窗口长度和重置标记；本记录不授权配置修改。
""",
     ["Lab-2026.09 AMF模拟器errors/requests累计采样从100/1000到180/1400，这个区间失败比例应怎样算，直接180/1400为什么不适用？",
      "Lab-2026.10 AMF窗口计数W1=60/200、W2=40/200，需要先差分吗？累计计数从180降到20又应该核对什么？"],
     ["09版区间失败比例=", "10版窗口失败比例分别为", "若累计计数从180降到20", "这组数据只有失败计数"]),
    ("manual_lab_nrf_clock_evidence.md", "ALM-NRF-DISC", "NRF", "nrf", "public", "public",
     """# NRF：远端时钟与有效期证据

项目自编模拟租约记录，非NRF协议规定、真实工单或生产操作建议。输入见 fixtures.json 的 nrf；只描述本模拟器。
适用：NRF；Lab-2026.10 租约模拟器；不适用Lab-2026.09或真实系统。
原始采样：本地现在120s；远端到期字段115s；已校准远端减本地时钟偏移=-10s。
统一时钟后，本地到期=115-(-10)=125s，剩余=125-120=5s；不能直接用115<120判为已到期。
反例：若已校准偏移=0s，则本地到期115s，在本地120s已经到期；偏移数值改变结论。
缺少偏移、单位或采样时刻时，时钟域不能比较；先补校准证据，不能判定注册失效。
记录到期时间并不证明网络可达；可达性须另有同一时间窗内的连接成功/失败证据。
只有同一租约ID、校准偏移和本地时间均明确时才能作上述计算；不能拼接另一租约的采样。
只读验证：保存租约ID、远端原始字段、偏移校准记录与本地采样；没有重注册或网络配置修改动作。
""",
     ["Lab-2026.10 NRF租约模拟器：本地120s、远端到期115s、远端减本地偏移-10s，这条租约已经到期了吗？需要怎样对齐时钟？",
      "Lab-2026.10 NRF同一租约，本地120s、远端到期115s；已校准偏移0s与偏移未知分别能得出什么结论，能否说明网络可达？"],
     ["统一时钟后，本地到期=", "反例：若已校准偏移=0s", "缺少偏移、单位或采样时刻时", "记录到期时间并不证明网络可达"]),
    ("manual_lab_upf_counter_denominators.md", "ALM-UPF-DROP", "UPF", "upf", "role_restricted", "public",
     """# UPF：丢包分母与未归因残差

项目自编模拟计数器记录，非UPF规范、厂商计数器或真实流量。输入见 fixtures.json 的 upf。
适用：UPF；Lab-2026.10 窗口计数模拟器；全部计数属于同一窗口、同一模拟实例。
原始窗口：入口1000包；规则匹配800包；规则丢弃40包；队列丢弃10包；总丢弃60包。
规则丢弃占匹配包=40/800=5%；占入口包=40/1000=4%，两者回答不同问题，不能交换分母。
队列丢弃占入口包=10/1000=1%；没有队列入队计数，无法计算队列内部丢弃比例。
未归因残差=60-40-10=10包；仅解释了50/60的丢弃，不应把全部丢包归因于规则或队列。
不同窗口、不同实例或重置前后计数不能相减；先核对实例ID、采样窗口、计数模式及重置标记。
Lab-2026.09累计模式需有起止采样才可计算增量；本单窗口记录不足以推算09版区间比例。
只读验证：保留原始计数与分母定义；未归因部分需补其他丢弃阶段证据，本记录不授权改规则或重启设备。
""",
     ["Lab-2026.10 UPF同一窗口入口1000、匹配800、规则丢弃40、队列丢弃10、总丢弃60；规则占匹配比例和队列占入口比例分别多少？",
      "Lab-2026.10 UPF总丢弃60、规则40、队列10，是否已经解释全部丢包？为什么40/800与40/1000不能混用，跨窗口计数可否相减？"],
     ["规则丢弃占匹配包=", "队列丢弃占入口包=", "未归因残差=", "不同窗口、不同实例或重置前后计数"]),
    ("manual_lab_smf_transaction_alignment.md", "ALM-SMF-PDU", "SMF", "smf", "department_restricted", "lab-a",
     """# SMF：逐事务对齐时延证据

项目自编模拟事件序列，非PDU协议定时规则、真实链路追踪或生产建议。输入见 fixtures.json 的 smf。
适用：SMF；Lab-2026.10 事务时延模拟器；时刻单位ms且使用同一个单调时钟。
事务A：策略阶段0至20ms；控制阶段25至225ms；事务B：策略阶段0至180ms；控制阶段185至205ms。
事务A策略耗时20ms、控制耗时200ms；事务B策略耗时180ms、控制耗时20ms，两个事务的慢阶段不同。
不能把A的策略结束20ms与B的控制结束205ms拼成同一事务；必须按事务ID关联，同一时钟与版本对齐。
阶段均值不能代表每个事务：两笔策略平均100ms、控制平均110ms，平均值掩盖两种分支。
阶段之间间隔均为5ms，本记录没有给出间隔原因；也没有吞吐、队列或重试证据，不据此推导根因。
缺少事务ID、单调时钟或某阶段边界时，先补事件，不从墙钟变化或另一事务补齐。
只读验证：保留两笔完整事件、事务ID、时钟域和软件版本；本记录不授权重试或修改策略。
""",
     ["Lab-2026.10 SMF事务A策略0到20ms、控制25到225ms；事务B策略0到180ms、控制185到205ms，各事务慢在哪个阶段？",
      "Lab-2026.10 SMF两笔事务策略平均100ms、控制平均110ms，为什么不能代表每笔慢阶段？A策略结束20ms和B控制结束205ms能否拼接？"],
     ["事务A策略耗时20ms", "不能把A的策略结束20ms", "阶段均值不能代表每个事务", "阶段之间间隔均为5ms"]),
]


def reference(path, quote, *, whole_line=False):
    lines = path.read_text(encoding="utf-8").splitlines()
    line = next((i for i, text in enumerate(lines, 1) if quote in text), None)
    if line is None:
        raise ValueError(f"missing evidence: {path.name}: {quote}")
    return {"source": path.name, "file_sha256": sha(path.read_bytes()), "line": line,
            "quote": lines[line-1] if whole_line else quote}


def make_case(case_id, query, sources, refs, *, kind="answer", status="ANSWERED", context=None, negatives=None):
    return bind_pending({
        "id": case_id, "query": query, "split": "dev", "development_exposed": True,
        "case_kind": kind, "source_match": "exact", "expect_route": "retrieval", "expect_status": status,
        "expect_hit": [s["filename"] for s in sources] if status == "ANSWERED" else [],
        "hard_negatives": negatives or [], "required_evidence": refs,
        "request_context": context or {"tenant_id": "default", "roles": ["ops"], "dept_ids": ["lab-a"]},
        "source_ids": [s["source_id_proposal"] for s in sources],
        "body_families": [s["file_sha256"] for s in sources],
        "topic_groups": sorted({s["topic_group"] for s in sources}),
        "template_groups": sorted({s["template_group"] for s in sources}),
        "label_status": "AI_proposed_not_human_approved",
        "terminal_contract": "Expected QA status only; retrieval baseline does not measure API/generation terminal status.",
    })


def build(output, parent):
    if output.exists():
        raise ValueError("refuse overwrite; use a new package directory")
    raw = output / "raw"
    raw.mkdir(parents=True)
    previous = json.loads((parent / "evaluation/review-cases.json").read_text(encoding="utf-8"))
    parent_meta = json.loads((parent / "raw/meta.json").read_text(encoding="utf-8"))
    registry, cases = [], []
    for original in previous["review_cases"]:
        name = original["proposed_labels"]["expect_hit"]
        path = raw / name
        path.write_bytes((parent / "raw" / name).read_bytes())
        source_id = parent_meta[name].get("source_id") or "lab-sop:" + path.stem
        source = bind_pending({
            "filename": name, "source_id_proposal": source_id, "file_sha256": sha(path.read_bytes()),
            "origin_type": "synthetic", "synthetic": True, "source_system": "project_authored_lab",
            "license_status": "synthetic_not_vendor_material", "review_status": "pending", "registration_verified": False,
            "source_version": "Lab-2026.09", "topic_group": original["group_id"],
            "template_group": "lab-sop-branch-v1", "source_scope": {
                "tenant_id": "default", "visibility": "public", "role_scope": [], "allowed_roles": [],
                "dept_id": "public", "security_level": "internal", "status": "draft",
                "effective_from": "2026-09-19T00:00:00+00:00", "effective_to": "2028-09-19T00:00:00+00:00"},
            "parent_file": (parent / "raw" / name).relative_to(ROOT).as_posix(),
            "parent_sha256": sha((parent / "raw" / name).read_bytes()),
            "information_delta": "Retained paired conditional lab branch; no new independent source claim.",
        })
        registry.append(source)
        refs = [reference(path, atom["quote"]) for atom in original["required_evidence"]]
        n = len(registry)
        cases.append(make_case(f"S3-{n:02d}-flow", original["query"], [source], refs,
                               negatives=original["proposed_labels"]["hard_negatives"]))
        equipment = re.search(r"适用：([^；]+)", path.read_text(encoding="utf-8"))[1]
        condition = re.search(r"条件：([^；\n]+)", path.read_text(encoding="utf-8"))[1]
        # A different evidence task, still the same exposed source/template group.
        q = f"{equipment} Lab-2026.09 {original['group_id']}，{condition}；本分支的排除项、停止条件和验证恢复所需采样证据分别是什么？"
        cases.append(make_case(f"S3-{n:02d}-evidence", q, [source], refs,
                               negatives=original["proposed_labels"]["hard_negatives"]))
        cases.append(make_case(f"S3-{n:02d}-missing", f"{equipment} Lab-2026.09 {original['group_id']}，只有告警码，恢复SOP应该选哪个分支？",
                               [source], [reference(path, "上下文不足时先追问")],
                               kind="clarification", status="NEEDS_CLARIFICATION"))
    fixtures = lab_records()
    (output / "fixtures.json").write_bytes(encode(fixtures))
    for index, (name, topic, equipment, fixture_key, visibility, dept, text, questions, quotes) in enumerate(NEW_NOTES, 13):
        path = raw / name
        path.write_text(text, encoding="utf-8", newline="\n")
        source = bind_pending({
            "filename": name, "source_id_proposal": f"lab-observation:{fixture_key}-v1",
            "file_sha256": sha(path.read_bytes()), "origin_type": "synthetic", "synthetic": True,
            "source_system": "project_authored_lab", "license_status": "synthetic_not_vendor_material",
            "review_status": "pending", "registration_verified": False, "source_version": "Lab-2026.10",
            "topic_group": topic, "template_group": f"lab-observation-{fixture_key}-v1",
            "fixture_key": fixture_key, "fixture_sha256": sha((output / "fixtures.json").read_bytes()),
            "source_scope": {"tenant_id": "default", "visibility": visibility, "dept_id": dept,
                "role_scope": ["ops"] if visibility == "role_restricted" else [],
                "allowed_roles": ["ops"] if visibility == "role_restricted" else [],
                "security_level": "internal", "status": "draft",
                "effective_from": "2026-10-07T00:00:00+00:00", "effective_to": "2028-10-07T00:00:00+00:00"},
            "information_delta": {"amf": "Cumulative vs window counters, reset boundary, missing root-cause telemetry",
                                  "nrf": "Clock conversion with numeric counterexample; lease validity vs reachability",
                                  "upf": "Denominator semantics and unassigned loss residual",
                                  "smf": "Per-transaction stage timings vs averages and invalid cross-transaction joins"}[fixture_key],
        })
        registry.append(source)
        refs = [reference(path, quote, whole_line=True) for quote in quotes]
        related = [s["filename"] for s in registry[:12] if s["topic_group"] == topic]
        for j, query in enumerate(questions, 1):
            cases.append(make_case(f"S3-{index:02d}-calc{j}", query, [source], refs, negatives=related))
        cases.append(make_case(f"S3-{index:02d}-version", f"{equipment} Lab-2026.11：能直接沿用这份Lab-2026.10模拟记录的计算和结论吗？",
                               [source], [reference(path, "适用：")], kind="version_boundary", status="NO_EVIDENCE",
                               negatives=[name]))
        context = [{"tenant_id": "other", "roles": ["ops"], "dept_ids": ["lab-a"]},
                   {"tenant_id": "default", "roles": ["guest"], "dept_ids": []},
                   {"tenant_id": "default", "roles": ["auditor"], "dept_ids": ["lab-a"]},
                   {"tenant_id": "default", "roles": ["ops"], "dept_ids": ["lab-b"]}][index-13]
        cases.append(make_case(f"S3-{index:02d}-permission", questions[0], [source], [], kind="permission",
                               status="NO_EVIDENCE", context=context, negatives=[name]))
    package = {"protocol": "step3-unapproved-dev-diagnostic-v1", "metrics_eligible": False,
               "independent_frozen": False, "corpus_registration_verified": False,
               "kb_version": "offline-step3-lab-v1", "parent_kb_version": "v4-quality-r1",
               "approval_scope": "Draft local diagnostic only; no ingestion/publication authorization",
               "cases": cases}
    (output / "sources.json").write_bytes(encode({"sources": registry}))
    (output / "cases.json").write_bytes(encode(package))
    (output / "groups.json").write_bytes(encode({"groups": audit_groups(cases), "independent_frozen_cases": 0,
        "exposure": "All cases co-designed with corpus and exposed in this development run.",
        "reason": "Conservative source/body/topic/template union; do not random-split paraphrases."}))
    (output / "pending-review.json").write_bytes(encode({
        "bfd_netconf": {"status": "pending", "decision_date": "2026-10-07",
            "user_decision": "保留pending，继续沙箱开发资料与基线", "registered": False, "indexed": False,
            "pending_items": ["BFD directional formula, Down/Init/Up applicability, Demand/Echo boundaries",
                "NETCONF lock scope/release, advertised candidate capability and commit preconditions",
                "2010/2011 IETF license applicability to locally authored demo summaries"],
            "review_entry": "docs/STAGE4_BFD_NETCONF_SOURCE_REVIEW_2026-09-29.md"},
        "this_package": "All source and case bindings pending; no invented human reviewer; original 108 approvals untouched."}))
    (output / "README.md").write_text(
        "# 新步骤3开发资料与尺子\n\n"
        "16个知识单元：保留12个已暴露沙箱条件分支，新增4个可复算观测记录；52题全部dev。"
        "原始输入与计算结果见fixtures.json，来源ID提案/权限/原文哈希见sources.json，题目和行号见cases.json。\n\n"
        "来源与真值均为AI草稿，approval=pending。绑定沿用review_contract；本包不能交给run_eval、Worker或发布门禁。"
        "本地只读诊断使用独立脚本，不代表正式已审质量成绩。BFD/NETCONF不进入本包。\n\n"
        "groups.json按来源、正文、主题和模板连通组划分；全部开发暴露，没有独立frozen。"
        "题数不能当独立样本量，跨版本/权限题不进入回答命中率分母。\n\n"
        "无LLM诊断固定包、只读test向量快照与代码/配置哈希，只计算检索和字面证据；API终态、"
        "真实BM25/融合、生成支持性未测。权限只是本地诊断过滤夹具，不替代现有真实存储安全验收。\n",
        encoding="utf-8", newline="\n")
    (output / "artifact-hashes.json").write_bytes(encode({
        str(p.relative_to(output)).replace("\\", "/"): sha(p.read_bytes())
        for p in sorted(output.rglob("*")) if p.is_file()}))
    return verify(output)


def verify(output):
    hashes = json.loads((output / "artifact-hashes.json").read_text(encoding="utf-8"))
    for name, digest in hashes.items():
        path = (output / name).resolve()
        if not path.is_relative_to(output.resolve()) or sha(path.read_bytes()) != digest:
            raise ValueError(f"artifact hash mismatch: {name}")
    sources = json.loads((output / "sources.json").read_text(encoding="utf-8"))["sources"]
    payload = json.loads((output / "cases.json").read_text(encoding="utf-8"))
    if payload["metrics_eligible"] or payload["independent_frozen"]:
        raise ValueError("draft cannot become an approved or independent dataset")
    if len({s["source_id_proposal"] for s in sources}) != len(sources):
        raise ValueError("duplicate source identity proposal")
    for source in sources:
        check_approval(source)
        if source["approval"]["status"] != "pending":
            raise ValueError("this diagnostic package must remain pending")
    for case in payload["cases"]:
        check_approval(case)
        if case["approval"]["status"] != "pending":
            raise ValueError("labels must remain pending")
        for atom in case["required_evidence"]:
            path = output / "raw" / atom["source"]
            if atom["file_sha256"] != sha(path.read_bytes()) or atom["quote"] not in path.read_text(encoding="utf-8").splitlines()[atom["line"]-1]:
                raise ValueError(f"evidence changed: {case['id']}")
    groups = audit_groups(payload["cases"])
    return {"knowledge_units": len(sources), "new_observation_units": 4, "cases": len(payload["cases"]),
            "case_kinds": dict(Counter(c["case_kind"] for c in payload["cases"])),
            "connected_groups": len(groups), "independent_frozen_cases": 0, "approval": "pending"}


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--output", type=Path, default=ROOT / "data/candidates/step3-lab-dev-r1")
    p.add_argument("--parent", type=Path, default=ROOT / "data/candidates/v4-quality-r1-final")
    p.add_argument("--verify", action="store_true")
    args = p.parse_args()
    print(json.dumps(verify(args.output) if args.verify else build(args.output, args.parent), ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
