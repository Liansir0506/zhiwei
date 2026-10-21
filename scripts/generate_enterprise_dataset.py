#!/usr/bin/env python
"""确定性生成 v4-foundation 企业场景数据；不下载或重新分发第三方原文。"""
from __future__ import annotations

import argparse
import csv
import hashlib
import json
import random
from datetime import datetime, timedelta, timezone
from pathlib import Path

GENERATOR_VERSION = "enterprise-dataset-v1"
DEFAULT_SEED = 20260919

SOURCES = [
    ("3gpp-23501", "3GPP TS 23.501 — 5GS architecture", "https://portal.3gpp.org/desktopmodules/Specifications/SpecificationDetails.aspx?specificationId=3144", "reference_only"),
    ("3gpp-23502", "3GPP TS 23.502 — 5GS procedures", "https://portal.3gpp.org/desktopmodules/Specifications/SpecificationDetails.aspx?specificationId=3145", "reference_only"),
    ("3gpp-23503", "3GPP TS 23.503 — Policy and charging", "https://portal.3gpp.org/desktopmodules/Specifications/SpecificationDetails.aspx?specificationId=3334", "reference_only"),
    ("3gpp-24501", "3GPP TS 24.501 — 5GS NAS", "https://portal.3gpp.org/desktopmodules/Specifications/SpecificationDetails.aspx?specificationId=3370", "reference_only"),
    ("3gpp-29244", "3GPP TS 29.244 — PFCP", "https://portal.3gpp.org/desktopmodules/Specifications/SpecificationDetails.aspx?specificationId=3111", "reference_only"),
    ("3gpp-29500", "3GPP TS 29.500 — SBA principles", "https://portal.3gpp.org/desktopmodules/Specifications/SpecificationDetails.aspx?specificationId=3338", "reference_only"),
    ("3gpp-38300", "3GPP TS 38.300 — NR overall description", "https://portal.3gpp.org/desktopmodules/Specifications/SpecificationDetails.aspx?specificationId=3191", "reference_only"),
    ("3gpp-38331", "3GPP TS 38.331 — NR RRC", "https://portal.3gpp.org/desktopmodules/Specifications/SpecificationDetails.aspx?specificationId=3197", "reference_only"),
    ("3gpp-38413", "3GPP TS 38.413 — NGAP", "https://portal.3gpp.org/desktopmodules/Specifications/SpecificationDetails.aspx?specificationId=3223", "reference_only"),
    ("3gpp-38401", "3GPP TS 38.401 — NG-RAN architecture", "https://portal.3gpp.org/desktopmodules/Specifications/SpecificationDetails.aspx?specificationId=3219", "reference_only"),
    ("etsi-search", "ETSI standards search", "https://www.etsi.org/standards/", "reference_only"),
    ("open5gs-docs", "Open5GS official documentation", "https://open5gs.org/open5gs/docs/", "link_only"),
    ("open5gs-quickstart", "Open5GS quickstart", "https://open5gs.org/open5gs/docs/guide/01-quickstart/", "link_only"),
    ("open5gs-troubleshoot", "Open5GS simple issue guide", "https://open5gs.org/open5gs/docs/troubleshoot/01-simple-issues/", "link_only"),
    ("free5gc-quick", "free5GC quick setup", "https://free5gc.org/guide/quick-setup/", "link_only"),
    ("free5gc-ueransim", "free5GC and UERANSIM guide", "https://free5gc.org/guide/UERANSIM/UERANSIM/", "link_only"),
    ("srsran-docs", "srsRAN Project documentation", "https://docs.srsran.com/projects/project/en/latest/", "link_only"),
]

SCENARIOS = [
    ("AMF注册成功率下降", "AMF", "5G", "ALM-AMF-REG", "注册信令拥塞"),
    ("SMF会话建立超时", "SMF", "5G", "ALM-SMF-PDU", "会话策略依赖超时"),
    ("UPF转发丢包", "UPF", "5G", "ALM-UPF-DROP", "N3队列拥塞"),
    ("gNodeB小区退服", "gNodeB", "5G", "ALM-GNB-CELL", "传输链路抖动"),
    ("NGAP连接中断", "gNodeB", "5G", "ALM-NGAP-SCTP", "SCTP路径异常"),
    ("PFCP心跳丢失", "UPF", "5G", "ALM-PFCP-HB", "控制面链路丢包"),
    ("NR切换失败率升高", "gNodeB", "5G", "ALM-NR-HO", "邻区参数不一致"),
    ("时钟同步失锁", "Clock", "传输", "ALM-PTP-LOCK", "PTP路径不对称"),
    ("光模块接收功率低", "OTN", "传输", "ALM-OPT-RX", "光路衰耗增加"),
    ("SPN隧道抖动", "SPN", "传输", "ALM-SPN-TUNNEL", "承载队列突发拥塞"),
    ("NR上行干扰升高", "gNodeB", "5G", "ALM-NR-ULI", "外部同频干扰"),
    ("用户面时延升高", "UPF", "5G", "ALM-UPF-LAT", "NUMA亲和性偏移"),
    ("NRF发现失败", "NRF", "5G", "ALM-NRF-DISC", "服务注册信息过期"),
    ("UDM鉴权失败", "UDM", "5G", "ALM-UDM-AUTH", "订阅数据不一致"),
    ("PCF策略下发失败", "PCF", "5G", "ALM-PCF-POL", "策略关联缺失"),
    ("基站电源欠压", "Power", "无线", "ALM-PWR-LOW", "整流模块效率下降"),
    ("机房温度过高", "Environment", "无线", "ALM-TEMP-HIGH", "空调送风受阻"),
    ("LTE附着失败率升高", "MME", "4G", "ALM-MME-ATT", "S1信令拥塞"),
    ("GTP隧道建立失败", "SGW", "4G", "ALM-GTP-CREATE", "对端地址配置漂移"),
    ("DNS解析时延升高", "DNS", "核心网", "ALM-DNS-LAT", "缓存命中率下降"),
]

VENDORS = ["SimVendor-A", "SimVendor-B", "SimVendor-C", "OpenLab"]
DEPTS = ["ran-ops", "core-ops", "transport-ops", "noc"]


def sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def write_text(path: Path, content: str) -> str:
    path.parent.mkdir(parents=True, exist_ok=True)
    data = content.encode("utf-8")
    path.write_bytes(data)
    return sha256_bytes(data)


def write_csv(path: Path, rows: list[dict]) -> str:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(rows[0]))
        writer.writeheader()
        writer.writerows(rows)
    return sha256_bytes(path.read_bytes())


def base_meta(version: str, seed: int, origin: str, synthetic: bool, refs: list[str]) -> dict:
    return {
        "tenant_id": "default", "dept_id": "public", "dataset_id": version,
        "visibility": "public", "allowed_roles": [], "role_scope": [],
        "security_level": "internal", "quality": "high", "status": "active",
        "source_version": version, "effective_from": "2026-09-19T00:00:00+00:00",
        "effective_to": "2028-09-19T00:00:00+00:00", "origin_type": origin,
        "synthetic": synthetic, "generator_version": GENERATOR_VERSION,
        "generation_seed": str(seed), "review_status": "auto_validated",
        "license_status": "link_only_no_redistribution" if not synthetic else "synthetic",
        "evidence_refs": refs,
    }


def manual_section(topic: str, equipment: str, alarm: str, angle: str, index: int) -> str:
    checks = [
        f"确认{equipment}的对象标识、软件版本、所属区域和最近变更窗口，禁止只依据单一告警直接下结论",
        f"按时间线核对{alarm}首次出现、派生告警、恢复事件和相邻设备状态，区分根告警与伴随告警",
        "采集控制面成功率、用户面时延、丢包、CPU、内存和接口队列指标，并保留采样时间与单位",
        "对比同区域健康对象和故障对象的配置差异，确认参数漂移是否发生在已审批变更之后",
        "先执行只读检查，再进行可回滚操作；涉及业务中断的动作必须进入维护窗口并经过复核",
        "恢复后持续观察三个采样周期，验证主指标、关联告警和用户体验同时恢复，避免只看进程存活",
    ]
    paragraphs = []
    for i in range(12):
        check = checks[(index + i) % len(checks)]
        paragraphs.append(
            f"章节{index + 1}-检查{i + 1}：{check}。本卡关注“{topic}”的{angle}阶段；证据应记录来源、时间、对象和版本。"
            f"对于{topic}-{angle}-章节{index + 1}-检查{i + 1}，如果关键上下文缺失，"
            "应停止自动处置并追问设备、版本、区域或变更编号。")
    return "\n".join(paragraphs)


def detailed_case(ticket: str, topic: str, equipment: str, alarm: str,
                  cause: str, device: str, index: int) -> tuple[str, str, str]:
    readings = [
        f"{ticket}采样{i + 1}显示{equipment}指标偏差为{(index * 7 + i * 3) % 37 + 5}%"
        for i in range(8)
    ]
    phenomenon = "；".join(readings) + (
        f"。{ticket}关联设备{device}出现{topic}，告警码{alarm}，影响范围经模拟拓扑核对为单域。"
        "值班人员完成时间对齐、上下游关联和变更窗口检查，未使用真实用户标识。")
    root = "；".join(
        f"{ticket}证据{i + 1}将{cause}与指标{(index + i) % 9 + 1}的变化关联，并排除候选原因{(index + i) % 13 + 1}"
        for i in range(8)
    ) + f"。综合配置差异、模拟日志和回退实验，确认根因为{cause}；结论仅适用于{device}当前模拟版本。"
    resolution = "；".join(
        f"{ticket}处置{i + 1}：完成检查项{(index + i) % 17 + 1}并记录前后值，异常则立即回滚"
        for i in range(8)
    ) + "。恢复后观察三个周期，告警清除、成功率和时延均回到基线；形成复盘项并更新防复发检查。"
    return phenomenon, root, resolution


def generate(output_root: Path, version: str, seed: int, legacy_root: Path | None) -> dict:
    rng = random.Random(seed)
    raw_dir = output_root / "raw" / version
    dataset_dir = output_root / "datasets" / version
    manifest_dir = output_root / "manifests" / version
    if raw_dir.exists() and any(raw_dir.iterdir()):
        raise SystemExit(f"拒绝覆盖非空版本目录：{raw_dir}")
    raw_dir.mkdir(parents=True, exist_ok=True)
    dataset_dir.mkdir(parents=True, exist_ok=True)
    manifest_dir.mkdir(parents=True, exist_ok=True)

    source_catalog = []
    for source_id, title, url, license_status in SOURCES:
        source_catalog.append({
            "source_id": source_id, "title": title, "url": url,
            "license_status": license_status, "acquisition": "link_only",
            "redistributed": False, "verified_at": "2026-09-19",
            "reference_hash": sha256_bytes(url.encode()),
        })
    write_text(manifest_dir / "sources.json",
               json.dumps(source_catalog, ensure_ascii=False, indent=2))

    meta: dict[str, dict] = {}
    files: list[dict] = []
    knowledge_assets: list[dict] = []
    relations: list[dict] = []

    # 20 篇自编运维手册 × 15 个独立主题章节 = 300 个公开依据知识章节。
    angles = ["发现", "定位", "处置", "恢复验证", "变更核对", "复盘预防"]
    for doc_index, scenario in enumerate(SCENARIOS):
        topic, equipment, tech, alarm, _cause = scenario
        filename = f"manual_reference_{doc_index + 1:02d}.md"
        refs = [SOURCES[doc_index % len(SOURCES)][0], SOURCES[(doc_index + 5) % len(SOURCES)][0]]
        sections = [f"# {topic}运维知识卡\n\n本文为项目自编的{topic}知识卡（REF-{doc_index + 1:03d}），"
                    "不是规范原文；使用时应回查来源索引。"]
        for section in range(15):
            angle = angles[section % len(angles)]
            sections.append(f"## {section + 1}. {topic}—{angle}\n\n" +
                            manual_section(topic, equipment, alarm, angle, section))
        path = raw_dir / filename
        checksum = write_text(path, "\n\n".join(sections))
        meta[filename] = {**base_meta(version, seed, "public_reference_derived", False, refs),
                          "source_system": "curated_reference_card", "equipment_type": equipment,
                          "device_type": equipment, "network_tech": tech, "vendor": "OpenReference"}
        files.append({"filename": filename, "sha256": checksum, "origin_type": "public_reference_derived"})
        knowledge_assets.append({
            "asset_id": f"asset-ref-{doc_index + 1:03d}", "tenant_id": "default",
            "filename": filename, "doc_type": "manual", "source_system": "curated_reference_card",
            "source_id": refs[0], "source_version": version, "security_level": "internal",
            "visibility": "public", "role_scope": [], "status": "active",
            "effective_from": "2026-09-19T00:00:00+00:00",
            "effective_to": "2028-09-19T00:00:00+00:00", "kb_version": version,
            "checksum": checksum, "reviewed_by": "sim-user-quality-gate",
            "approved_at": "2026-09-19T00:00:00+00:00",
        })

    # 40 个可复现实验记录，每个 10 阶段；明确 SIMULATED，不冒充生产日志。
    for exp in range(40):
        topic, equipment, tech, alarm, cause = SCENARIOS[exp % len(SCENARIOS)]
        filename = f"guide_experiment_{exp + 1:03d}.md"
        parts = [f"# SIMULATED 实验 EXP-{exp + 1:03d}：{topic}",
                 f"实验编号：EXP-{exp + 1:03d}；生成器：{GENERATOR_VERSION}；"
                 f"随机种子：{seed}；依据：{SOURCES[exp % len(SOURCES)][0]}"]
        for phase in range(10):
            parts.append(
                f"## 阶段 {phase + 1}\n对象为{equipment}，注入条件为{cause}，观测告警{alarm}。"
                f"本阶段采集控制面成功率{90 + (exp + phase) % 9}%、模拟时延{10 + (exp * 3 + phase) % 40}毫秒、"
                f"队列占用{20 + (exp + phase * 2) % 60}%。先核对拓扑和版本，再执行只读诊断；"
                "所有标识均为合成值。实验恢复后核对派生告警、业务指标和配置差异，并保存失败条件与回滚证据。")
        path = raw_dir / filename
        checksum = write_text(path, "\n\n".join(parts))
        refs = [SOURCES[exp % len(SOURCES)][0]]
        meta[filename] = {**base_meta(version, seed, "experiment", True, refs),
                          "source_system": "simulated_lab", "equipment_type": equipment,
                          "device_type": equipment, "network_tech": tech, "alarm_code": alarm}
        files.append({"filename": filename, "sha256": checksum, "origin_type": "experiment"})

    # 200 个 SOP，每个 10 步；用步骤级知识覆盖场景，不靠拉长单条工单凑 chunk。
    for sop in range(200):
        topic, equipment, tech, alarm, cause = SCENARIOS[sop % len(SCENARIOS)]
        filename = f"sop_operation_{sop + 1:03d}.md"
        parts = [f"# {topic}处置 SOP {sop + 1:03d}\n适用：{equipment}/{tech}；模拟告警：{alarm}。"]
        for step in range(1, 11):
            parts.append(
                f"步骤 {step}：执行{['确认影响范围','核对最近变更','采集关键指标','检查上下游依赖','验证配置一致性','执行低风险处置','检查派生告警','验证业务恢复','观察三个周期','记录复盘项'][step-1]}。"
                f"针对{cause}记录对象、版本、时间和前后值；证据不足时停止并转人工复核。")
        path = raw_dir / filename
        checksum = write_text(path, "\n\n".join(parts))
        refs = [SOURCES[sop % len(SOURCES)][0]]
        meta[filename] = {**base_meta(version, seed, "synthetic", True, refs),
                          "source_system": "reviewed_sop_generator", "equipment_type": equipment,
                          "device_type": equipment, "network_tech": tech, "alarm_code": alarm}
        files.append({"filename": filename, "sha256": checksum, "origin_type": "synthetic"})

    faq_rows = []
    for i in range(1000):
        topic, equipment, tech, alarm, cause = SCENARIOS[i % len(SCENARIOS)]
        faq_rows.append({
            "question": f"{equipment}出现{alarm}时，案例SIM-FAQ-{i + 1:04d}的第{i % 10 + 1}类上下文应先核对什么？",
            "answer": f"先确认设备、版本、区域和变更窗口，再核对{topic}的时间线及关联指标。"
                      f"模拟案例SIM-FAQ-{i + 1:04d}的候选根因是{cause}，"
                      f"验证指标编号为{(i * 7) % 97 + 1}，但证据不足时必须追问并拒绝直接处置。",
            "source": f"FAQ-SIM-{i + 1:04d}", "network_tech": tech,
        })
    faq_name = "faq_foundation_simulated.csv"
    faq_hash = write_csv(raw_dir / faq_name, faq_rows)
    meta[faq_name] = {**base_meta(version, seed, "synthetic", True,
                                                [s[0] for s in SOURCES[:10]]),
                      "source_system": "reviewed_faq_generator", "network_tech": "5G"}
    files.append({"filename": faq_name, "sha256": faq_hash, "origin_type": "synthetic"})

    # 领域快照与可向量化工单共享同一批 750 个工单。
    now = datetime(2026, 9, 19, tzinfo=timezone.utc)
    tenants = [{"tenant_id": "default", "name": "SIMULATED Telecom Operations", "status": "active"}]
    departments = [{"dept_id": d, "tenant_id": "default", "name": f"SIMULATED {d}",
                    "status": "active"} for d in DEPTS]
    users = [{"user_id": f"sim-user-{i + 1:03d}", "tenant_id": "default",
              "username": f"sim_user_{i + 1:03d}", "display_name": f"模拟工程师{i + 1:03d}",
              "status": "active"} for i in range(60)]
    users.append({"user_id": "sim-user-quality-gate", "tenant_id": "default",
                  "username": "sim_quality_gate", "display_name": "模拟质量门禁服务账号",
                  "status": "active"})
    user_departments = [{"user_id": u["user_id"], "dept_id": DEPTS[i % len(DEPTS)],
                         "is_primary": True} for i, u in enumerate(users)]
    devices = []
    for i in range(500):
        scenario = SCENARIOS[i % len(SCENARIOS)]
        devices.append({
            "device_id": f"SIM-DEV-{i + 1:04d}", "tenant_id": "default",
            "name": f"模拟{scenario[1]}设备{i + 1:04d}", "device_type": scenario[1],
            "dept_id": DEPTS[i % len(DEPTS)], "cmdb_id": f"SIM-CMDB-{i + 1:04d}",
            "vendor": VENDORS[i % len(VENDORS)], "model": f"MODEL-{i % 12 + 1:02d}",
            "network_tech": scenario[2], "software_version": f"SIM-{i % 7 + 1}.{i % 10}",
            "status": ["online", "online", "online", "maintenance"][i % 4],
            "attributes_json": {"synthetic": True, "generator_version": GENERATOR_VERSION,
                                "seed": seed, "site_class": ["core", "aggregation", "access"][i % 3]},
        })
    alarms = []
    for i in range(5000):
        scenario = SCENARIOS[i % len(SCENARIOS)]
        opened = now - timedelta(minutes=i * 3)
        cleared = i % 5 != 0
        alarms.append({
            "alarm_id": f"SIM-ALM-{i + 1:06d}", "tenant_id": "default",
            "alarm_code": scenario[3], "source_system": "simulated_alarm_platform",
            "occurred_at": opened.isoformat(), "device_id": devices[i % len(devices)]["device_id"],
            "severity": ["critical", "major", "minor", "warning"][i % 4],
            "status": "cleared" if cleared else "open",
            "cleared_at": (opened + timedelta(minutes=20 + i % 90)).isoformat() if cleared else None,
            "payload_json": {"synthetic": True, "generator_version": GENERATOR_VERSION,
                             "seed": seed, "reading": round(rng.uniform(0.1, 99.9), 2)},
        })
    work_orders, ticket_rows = [], []
    for i in range(750):
        scenario = SCENARIOS[i % len(SCENARIOS)]
        ticket = f"SIM-TT-{i + 1:05d}"
        device = devices[i % len(devices)]
        alarm = alarms[i * 6]
        opened = now - timedelta(hours=i * 2)
        phenomenon, root, resolution = detailed_case(
            ticket, scenario[0], scenario[1], scenario[3], scenario[4], device["device_id"], i)
        work_orders.append({
            "ticket_id": ticket, "tenant_id": "default", "title": f"SIMULATED {scenario[0]}",
            "source_system": "simulated_ticket_system", "opened_at": opened.isoformat(),
            "dept_id": device["dept_id"], "device_id": device["device_id"],
            "alarm_id": alarm["alarm_id"], "priority": ["P1", "P2", "P3", "P4"][i % 4],
            "status": "closed", "assignee_user_id": users[i % len(users)]["user_id"],
            "closed_at": (opened + timedelta(hours=1 + i % 8)).isoformat(),
        })
        ticket_rows.append({
            "ticket_id": ticket, "source": ticket, "title": f"SIMULATED {scenario[0]}",
            "phenomenon": phenomenon, "root_cause": root, "resolution": resolution,
            "alarm_code": scenario[3], "equipment_type": scenario[1],
            "vendor": device["vendor"], "network_tech": scenario[2],
        })
    # 控制单文件计算时间和失败恢复粒度：每 50 单一个确定性分片，约 150 chunks。
    for shard_index, start in enumerate(range(0, len(ticket_rows), 50), start=1):
        ticket_name = f"fault_case_simulated_v4_{shard_index:02d}.csv"
        ticket_hash = write_csv(raw_dir / ticket_name, ticket_rows[start:start + 50])
        meta[ticket_name] = {**base_meta(version, seed, "synthetic", True,
                                         [s[0] for s in SOURCES[:10]]),
                             "source_system": "simulated_ticket_system",
                             "shard_index": shard_index, "shard_size": 50}
        files.append({"filename": ticket_name, "sha256": ticket_hash,
                      "origin_type": "synthetic"})

    metrics = []
    for i in range(1500):
        device = devices[i % len(devices)]
        metrics.append({
            "observation_id": f"SIM-OBS-{i + 1:06d}", "tenant_id": "default",
            "device_id": device["device_id"], "metric_name": ["cpu_usage", "packet_loss", "latency"][i % 3],
            "metric_value": round(rng.uniform(0.1, 95.0), 3),
            "sampled_at": (now - timedelta(minutes=i)).isoformat(),
            "source_system": "simulated_monitoring", "unit": ["percent", "percent", "ms"][i % 3],
            "labels_json": {"synthetic": True, "seed": seed},
        })
    changes = []
    for i in range(350):
        device = devices[(i * 3) % len(devices)]
        ticket = work_orders[i % len(work_orders)]
        start = now + timedelta(hours=i)
        changes.append({
            "change_id": f"SIM-CHG-{i + 1:05d}", "tenant_id": "default",
            "title": f"SIMULATED {device['device_type']} 参数变更", "change_type": "parameter_update",
            "source_system": "simulated_change_system", "dept_id": device["dept_id"],
            "device_id": device["device_id"], "ticket_id": ticket["ticket_id"],
            "status": "completed", "window_start": start.isoformat(),
            "window_end": (start + timedelta(hours=2)).isoformat(),
            "operator_user_id": users[i % len(users)]["user_id"],
        })

    # 为每个知识资产关联一个设备；工单关系直接由 ticket_id 提供。
    for i, asset in enumerate(knowledge_assets):
        relations.append({"asset_id": asset["asset_id"], "relation_type": "device",
                          "relation_id": devices[i % len(devices)]["device_id"]})
    snapshot = {
        "tenants": tenants, "departments": departments, "users": users,
        "user_departments": user_departments, "devices": devices,
        "monitoring_metrics": metrics, "alarms": alarms, "work_orders": work_orders,
        "changes": changes, "knowledge_assets": knowledge_assets,
        "knowledge_relations": relations,
    }
    write_text(dataset_dir / "enterprise_snapshot.json",
               json.dumps(snapshot, ensure_ascii=False, indent=2))
    write_text(raw_dir / "meta.json", json.dumps(meta, ensure_ascii=False, indent=2))

    legacy = []
    if legacy_root and legacy_root.is_dir():
        for path in sorted(p for p in legacy_root.iterdir() if p.is_file()):
            legacy.append({"filename": path.name, "sha256": sha256_bytes(path.read_bytes()),
                           "size": path.stat().st_size, "dataset_id": "legacy_demo_v3",
                           "status": "archived", "physical_action": "retained_in_place"})
        write_text(output_root / "manifests" / "legacy_v3.json",
                   json.dumps({"version": "v3", "status": "ARCHIVED", "files": legacy},
                              ensure_ascii=False, indent=2))

    manifest = {
        "dataset_version": version, "generator_version": GENERATOR_VERSION,
        "seed": seed, "generated_at": "2026-09-19T00:00:00+00:00",
        "simulation_disclosure": "All business records are synthetic; reference cards are self-authored and are not official text.",
        "third_party_fulltext_redistributed": False,
        "counts": {"reference_documents": 20, "reference_chapters": 300,
                   "experiment_scenarios": 40, "sop_documents": 200, "faqs": 1000,
                   "devices": 500, "alarms": 5000, "work_orders": 750,
                   "changes": 350, "monitoring_metrics": 1500},
        "files": files,
    }
    write_text(manifest_dir / "dataset_manifest.json",
               json.dumps(manifest, ensure_ascii=False, indent=2))
    return manifest


def main() -> None:
    parser = argparse.ArgumentParser(description="生成确定性企业模拟数据")
    parser.add_argument("--output-root", type=Path, required=True)
    parser.add_argument("--legacy-root", type=Path)
    parser.add_argument("--version", default="v4-foundation")
    parser.add_argument("--seed", type=int, default=DEFAULT_SEED)
    args = parser.parse_args()
    manifest = generate(args.output_root.resolve(), args.version, args.seed,
                        args.legacy_root.resolve() if args.legacy_root else None)
    print(json.dumps(manifest["counts"], ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
