"""Prepare grouped legacy review and project explicit human approval locally.

No database, vector, model or publication operations. The frozen pending package
stays immutable. An editable receipt is a declaration, not reviewer authentication.
"""
import argparse
import copy
import html
import json
from pathlib import Path
import re

from qa_core.ingest.source_identity import validate_mapping
from qa_core.quality.review_contract import bind_pending, check_approval, review_digest
from scripts.build_legacy_identity_review import digest, validate_package


PROTOCOL = "legacy-group-review-v1"
SCOPE = "isolated_local_demo_only"
GROUPS = {"faq": "模拟FAQ", "fault_case": "模拟工单", "guide": "模拟实验",
          "sop": "模拟SOP", "manual": "自编参考卡"}


def read(path):
    return json.loads(Path(path).read_text(encoding="utf-8"))


def write(path, value):
    Path(path).write_text(json.dumps(value, ensure_ascii=False, sort_keys=True, indent=2) + "\n", encoding="utf-8")


def separate(root, output):
    root, output = Path(root).resolve(), Path(output).resolve()
    if output.exists() or output.is_relative_to(root) or root.is_relative_to(output):
        raise ValueError("output must be new and separate from review inputs")
    return root, output


def seal(root):
    write(root / "artifact-hashes.json", {
        p.relative_to(root).as_posix(): digest(p) for p in root.rglob("*")
        if p.is_file() and p != root / "artifact-hashes.json"})


def verify_seal(root):
    hashes = read(root / "artifact-hashes.json")
    actual = {p.relative_to(root).as_posix() for p in root.rglob("*")
              if p.is_file() and p != root / "artifact-hashes.json"}
    if set(hashes) != actual:
        raise ValueError("artifact roster changed")
    for name, expected in hashes.items():
        path = (root / name).resolve()
        if not path.is_relative_to(root) or digest(path) != expected:
            raise ValueError("artifact bytes changed")


def make_plan(package, audit):
    validate_package(package)
    sources = read(package / "source-proposals.json")["entries"]
    if audit.get("read_only") is not True:
        raise ValueError("read-only domain audit required")
    domains = {r["filename"]: r for r in audit["rows"]}
    registered = {e["filename"] for e in sources if e.get("existing_registration")}
    if len(domains) != len(audit["rows"]) or set(domains) != registered:
        raise ValueError("domain permission roster differs from registered sources")
    entries, conflicts = [], []
    for source in sources:
        name, meta = source["filename"], source["original_scope_metadata"]
        row = domains.get(name)
        if row:
            domain = row["domain"]
            for key in ("asset_id", "tenant_id", "filename", "source_system", "source_id"):
                if domain.get(key) != source["existing_registration"].get(key):
                    raise ValueError("domain identity disagrees with frozen registration")
            differences = {k: {"domain": domain.get(k), "raw": meta.get(k)}
                           for k in ("tenant_id", "dept_id", "security_level", "visibility", "role_scope")
                           if domain.get(k) != meta.get(k)}
            if differences != row["differences"] or set(differences) - {"dept_id"}:
                raise ValueError("unresolved non-department access conflict")
            if differences:
                if differences["dept_id"] != {"domain": None, "raw": "public"}:
                    raise ValueError("unsupported department reconciliation")
                conflicts.append({"filename": name, "differences": differences,
                                  "choices": ["use_domain_empty", "use_raw_public", "pending"]})
        kind = source["legacy_ledger"]["doc_type"]
        if kind not in GROUPS:
            raise ValueError("unknown review group")
        entries.append({"filename": name, "group": kind, "source_sha256": source["sha256"],
                        "proposal_binding_sha256": review_digest(source),
                        "source_system": source["source_system"], "source_id": source["proposed_source_id"],
                        "asset_id": source["proposed_asset_id"], "scope_metadata": meta,
                        "parsed_chunks": source["parsed_chunks"], "exact_traced_rows": source["exact_traced_rows"],
                        "review_status": "pending"})
    shared = []
    for group in read(package / "review-summary.json")["shared_existing_source_ids"]:
        value = {k: group[k] for k in ("tenant_id", "source_system", "source_id", "filenames")}
        shared.append({**value, "group_binding_sha256": review_digest(value),
                       "meaning": "withdrawal applies to every file sharing tenant/system/source_id"})
    counts = [{"group": k, "label": label, "files": sum(e["group"] == k for e in entries),
               "chunks": sum(e["exact_traced_rows"] for e in entries if e["group"] == k)}
              for k, label in GROUPS.items()]
    return {"protocol": PROTOCOL, "package_sha256": digest(package / "artifact-hashes.json"),
            "scope": SCOPE, "target_environment": "test", "groups": counts, "entries": entries,
            "department_conflicts": conflicts, "shared_withdrawal_groups": shared,
            "recommended_filenames": [e["filename"] for e in entries if e["scope_metadata"].get("synthetic") is True],
            "review_checklist": ["正文、来源身份与对应文件", "租户、部门、角色和生效时间",
                                 "模拟声明、适用范围与许可声明", "共享来源ID的共同撤回范围"],
            "limitations": ["仅隔离本地演示；不授权原ACTIVE导入或发布", "不证明事实、许可或生产操作质量",
                            "不转为独立frozen评测；不包括BFD/NETCONF或Lab待审资料"],
            "human_approved_sources": 0}


def render_review(package, plan):
    # Static escaped HTML works from disk, with no scripts, credentials or network.
    rows = []
    conflicts = {c["filename"] for c in plan["department_conflicts"]}
    for group in plan["groups"]:
        rows.append(f'<h2 id="group-{group["group"]}">{html.escape(group["label"])}：{group["files"]}文件 / {group["chunks"]}片段</h2>')
        for entry in plan["entries"]:
            if entry["group"] != group["group"]:
                continue
            name = entry["filename"]
            body = (package / "raw" / name).read_text(encoding="utf-8-sig")
            metadata = json.dumps(entry, ensure_ascii=False, indent=2)
            extra = " · 部门差异待定" if name in conflicts else ""
            rows.append(f'<details class="source"><summary>{html.escape(name)} · pending{extra}</summary>'
                        f'<h3>身份、范围与来源声明</h3><pre>{html.escape(metadata)}</pre>'
                        f'<h3>冻结完整原文</h3><pre>{html.escape(body)}</pre></details>')
    table = '<table><thead><tr><th>审核组</th><th>文件</th><th>片段</th><th>当前决定</th></tr></thead><tbody>'
    for group in plan["groups"]:
        table += (f'<tr><td><a href="#group-{group["group"]}">{html.escape(group["label"])}</a></td>'
                  f'<td>{group["files"]}</td><td>{group["chunks"]}</td><td>待人工审核</td></tr>')
    table += '</tbody></table>'
    concerns = ('<details><summary>参考卡待决项：部门声明和共同撤回</summary>'
                '<p>20份参考卡：原领域部门为空，文件声明为public。需明确采用空部门或public，或继续待审。'
                '其余权限不变。以下同一来源ID涉及两份独立文件，来源撤回会共同生效：</p><ul>')
    for group in plan["shared_withdrawal_groups"]:
        concerns += f'<li>{html.escape(group["source_id"])}：{html.escape("、".join(group["filenames"]))}</li>'
    concerns += '</ul></details>'
    return ('<!doctype html><html lang="zh-CN"><meta charset="utf-8"><title>原库分组审核</title>'
            '<style>body{max-width:1100px;margin:32px auto;font-family:system-ui;line-height:1.6}'
            'pre{white-space:pre-wrap;overflow-wrap:anywhere;background:#f4f6f8;padding:16px}'
            'summary{cursor:pointer;padding:12px}details{border:1px solid #ddd;margin:8px 0}'
            'table{width:100%;border-collapse:collapse}th,td{text-align:left;padding:10px;border-bottom:1px solid #ddd}</style>'
            '<h1>原库分组审核 · 全部pending</h1><p>逐份展开核对冻结原文及身份/范围。'
            '推荐先审核256份明确标记为模拟的资料；20份参考卡及其部门差异、三组共同撤回关系继续待审。'
            '审核范围仅隔离本地演示，批准凭据另行记录。</p>'
            + table + concerns + '<p>模拟资料适用于本地功能演示；可追溯性不证明事实、许可或生产操作质量。'
            '不纳入独立冻结评测，BFD/NETCONF与Lab继续待审。</p>' + ''.join(rows) + '</html>')


def prepare_review(package, audit_path, output):
    package, output = separate(package, output)
    audit = read(audit_path)
    plan = make_plan(package, audit)
    output.mkdir(parents=True)
    write(output / "domain-permission-audit.json", audit)
    write(output / "review-plan.json", plan)
    (output / "review.html").write_text(render_review(package, plan), encoding="utf-8")
    seal(output)
    # Editable template is deliberately OUTSIDE the sealed review directory.
    template = {"protocol": "legacy-projection-receipt-v1", "plan_sha256": digest(output / "review-plan.json"),
                "review_artifacts_sha256": digest(output / "artifact-hashes.json"),
                "candidate_version": "test-legacy-demo-r1", "scope": SCOPE, "target_environment": "test",
                "approved_filenames": [], "department_resolution": "pending",
                "acknowledged_withdrawal_groups": [], "reviewer_kind": "human_user_chat_confirmation",
                "user_message": "", "approval": {"status": "pending", "proposer": "", "reviewer": "", "evidence": ""}}
    return plan, bind_pending(template)


def load_plan(package, review):
    verify_seal(review)
    plan = read(review / "review-plan.json")
    if plan != make_plan(package, read(review / "domain-permission-audit.json")):
        raise ValueError("review plan no longer matches frozen inputs")
    return plan


def approved_inputs(package, review, receipt_path):
    package, review = Path(package).resolve(), Path(review).resolve()
    plan = load_plan(package, review)
    receipt = read(receipt_path)
    check_approval(receipt, required=True)
    if (receipt.get("protocol") != "legacy-projection-receipt-v1"
            or receipt.get("scope") != SCOPE or receipt.get("target_environment") != "test"
            or receipt.get("reviewer_kind") != "human_user_chat_confirmation"
            or not str(receipt.get("user_message", "")).strip()
            or receipt.get("plan_sha256") != digest(review / "review-plan.json")
            or receipt.get("review_artifacts_sha256") != digest(review / "artifact-hashes.json")):
        raise ValueError("explicit content-bound isolated human receipt required")
    version = receipt.get("candidate_version", "")
    if not re.fullmatch(r"test-legacy-[A-Za-z0-9_-]{1,48}", version):
        raise ValueError("new isolated candidate version required")
    selected = receipt.get("approved_filenames")
    entries = {e["filename"]: e for e in plan["entries"]}
    if (not isinstance(selected, list) or not selected or any(not isinstance(n, str) for n in selected)
            or len(selected) != len(set(selected)) or not set(selected) <= set(entries)):
        raise ValueError("explicit nonempty unique approved source selection required")
    conflict_selected = set(selected) & {c["filename"] for c in plan["department_conflicts"]}
    resolution = receipt.get("department_resolution")
    if resolution not in {"pending", "use_domain_empty", "use_raw_public"} or (conflict_selected and resolution == "pending"):
        raise ValueError("selected department conflicts need explicit resolution")
    acknowledgments = receipt.get("acknowledged_withdrawal_groups")
    groups = {g["group_binding_sha256"]: g for g in plan["shared_withdrawal_groups"]}
    if (not isinstance(acknowledgments, list) or any(not isinstance(k, str) for k in acknowledgments)
            or len(acknowledgments) != len(set(acknowledgments)) or not set(acknowledgments) <= set(groups)):
        raise ValueError("unknown withdrawal group acknowledgment")
    required = {key for key, group in groups.items() if set(selected) & set(group["filenames"])}
    if not required <= set(acknowledgments):
        raise ValueError("shared withdrawal scope must be explicitly acknowledged")
    evidence = receipt["approval"]["evidence"] + "; receipt_sha256=" + digest(receipt_path)
    metadata, mapping = {}, []
    for name in selected:
        entry = entries[name]
        meta = copy.deepcopy(entry["scope_metadata"])
        meta.update(source_id=entry["source_id"], asset_id=entry["asset_id"], doc_type=entry["group"],
                    status="active", review_status="human_chat_approved")
        if name in conflict_selected and resolution == "use_domain_empty":
            meta["dept_id"] = ""  # Milvus string representation of domain NULL.
        metadata[name] = meta
        item = bind_pending({"filename": name, "sha256": entry["source_sha256"], "tenant_id": meta["tenant_id"],
                             "source_system": entry["source_system"], "source_id": entry["source_id"]})
        item["approval"].update(status="approved", proposer=receipt["approval"]["proposer"],
                                reviewer=receipt["approval"]["reviewer"], evidence=evidence)
        check_approval(item, required=True)
        mapping.append(item)
    source_map = {"protocol": "reviewed-source-map-v1", "entries": mapping}
    # Validate against original frozen bytes before creating any output.
    validate_mapping(package / "raw", source_map, metadata)
    intent = {"version": version, "intended_status": "STAGED",
          "database_version_created": False, "target_environment": "test", "scope": SCOPE,
          "approved_files": len(selected), "pending_files": len(entries) - len(selected),
          "plan_sha256": receipt["plan_sha256"], "review_artifacts_sha256": receipt["review_artifacts_sha256"],
          "department_resolution": resolution, "published": False,
          "required_ingest_options": ["strict_metadata", "reviewed_source_map", "record_locations"],
          "limitations": plan["limitations"]}
    return receipt, metadata, source_map, intent


def project(package, review, receipt_path, output):
    package, output = separate(package, output)
    separate(review, output)
    receipt, metadata, source_map, intent = approved_inputs(package, review, receipt_path)
    output.mkdir(parents=True)
    raw = output / "raw"; raw.mkdir()
    for name in metadata:
        (raw / name).write_bytes((package / "raw" / name).read_bytes())
    write(raw / "meta.json", metadata)
    write(output / "source-map.json", source_map)
    write(output / "approval-receipt.json", receipt)
    write(output / "candidate-intent.json", intent)
    seal(output)
    validate_projection(package, review, output)
    return {"approved_files": intent["approved_files"], "pending_files": intent["pending_files"],
            "version": intent["version"], "local_projection_only": True, "db_writes": 0, "vector_writes": 0,
            "paid_llm_calls": 0, "active_changed": False}


def validate_projection(package, review, candidate):
    candidate = Path(candidate).resolve()
    verify_seal(candidate)
    _, metadata, mapping, intent = approved_inputs(package, review, candidate / "approval-receipt.json")
    if (read(candidate / "raw/meta.json") != metadata or read(candidate / "source-map.json") != mapping
            or read(candidate / "candidate-intent.json") != intent):
        raise ValueError("projected scope/identity differs from explicit human approval")
    for name, meta in metadata.items():
        if digest(candidate / "raw" / name) != digest(Path(package) / "raw" / name):
            raise ValueError("projected original bytes changed")
    if {p.name for p in (candidate / "raw").iterdir()} != set(metadata) | {"meta.json"}:
        raise ValueError("projected source roster changed")
    validate_mapping(candidate / "raw", mapping, metadata)
    return {"binding_valid": True, "approved_files": len(metadata), "scope": SCOPE,
            "database_version_created": False, "published": False}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--package", required=True)
    parser.add_argument("--review", required=True)
    parser.add_argument("--audit")
    parser.add_argument("--receipt-template")
    parser.add_argument("--receipt")
    parser.add_argument("--output")
    parser.add_argument("--validate-projection")
    args = parser.parse_args()
    if args.validate_projection:
        if args.receipt or args.output or args.audit or args.receipt_template:
            parser.error("projection verification needs only package/review/validate-projection")
        result = validate_projection(args.package, args.review, args.validate_projection)
    elif args.receipt:
        if not args.output or args.audit or args.receipt_template:
            parser.error("projection requires receipt/output and an existing review")
        result = project(args.package, args.review, args.receipt, args.output)
    else:
        if not args.audit or not args.receipt_template or args.output:
            parser.error("review preparation requires audit and new receipt-template")
        template_path = Path(args.receipt_template).resolve()
        review = Path(args.review).resolve()
        if template_path.exists() or template_path.is_relative_to(review):
            parser.error("receipt template must be new and outside sealed review")
        plan, template = prepare_review(args.package, args.audit, args.review)
        template_path.parent.mkdir(parents=True, exist_ok=True)
        write(template_path, template)
        result = {"review_files": len(plan["entries"]), "human_approved_sources": 0,
                  "recommended_simulated_files": len(plan["recommended_filenames"]), "db_writes": 0,
                  "vector_writes": 0, "paid_llm_calls": 0}
    print(json.dumps(result, ensure_ascii=False))


if __name__ == "__main__":
    main()
