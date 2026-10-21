"""Small RFC-summary dev proposals, never auto-approved or frozen."""
import argparse
import json
from pathlib import Path

from scripts.prepare_stage4_review import digest, contained_file
from qa_core.quality.review_contract import bind_pending, audit_groups
from qa_core.quality.eval_review import validate_review_payload, export_review


CATALOG = [
    ("http_503_note.md", "http-503", "https://www.rfc-editor.org/rfc/rfc9110.html", ["15.6.4", "10.2.3"], [
        ("监控接口返回 HTTP 503，能否仅据此断定是过载？响应一定有 Retry-After 吗？", 5),
        ("HTTP 响应的 Retry-After 写为 120 是什么时间单位？除了数字还能采用哪种格式？", 6)]),
    ("http_429_note.md", "http-429", "https://www.rfc-editor.org/rfc/rfc6585.html", ["4"], [
        ("运维 API 返回 HTTP 429，协议表达了什么？是否必须给出等待多久？", 5),
        ("客户端收到 HTTP 429，能直接认定服务端按 IP 计数吗？规范是否统一限定了计数范围？", 6)]),
    ("dns_negative_cache_note.md", "dns-negative-ttl", "https://www.rfc-editor.org/rfc/rfc2308.html", ["3", "5"], [
        ("按 RFC 2308 基础规则，SOA TTL=600 秒而 MINIMUM=180 秒，否定回答的起始缓存寿命如何计算？", 5),
        ("排查 DNS 时发现负缓存 TTL 已降到零，还能复用原来的否定回答吗？这是否保证新查询成功？", 6)]),
    ("tls_early_data_note.md", "tls-early-replay", "https://www.rfc-editor.org/rfc/rfc8446.html", ["E.5"], [
        ("请求通过 TLS 1.3 的 0-RTT 发送，已经加密是否就能排除重放导致的重复处理？", 5),
        ("评审接口能否安全接受 TLS 早期数据时，幂等是否已经构成充分保证？", 6)]),
]


def build(root):
    root = Path(root).resolve()
    sources, cases = [], []
    for filename, topic, url, sections, questions in CATALOG:
        path = contained_file(root / "raw", filename)
        text = path.read_text(encoding="utf-8"); lines = text.splitlines()
        checksum = digest(path.read_bytes()); source_id = "project.protocol-note." + topic
        sources.append(bind_pending({"filename": filename, "source_id_proposal": source_id,
            "sha256": checksum, "text": text, "tenant_id": "default", "source_system": "project_rfc_summary",
            "origin_type": "AI_authored_paraphrase", "normative_original": False, "external_url": url,
            "external_sections": sections, "external_read_date": "2026-09-27",
            "external_verification": "AI_read_not_human_verified_or_archived_full_RFC",
            "registration_status": "proposal_only", "scope_metadata": {"tenant_id": "default",
                "visibility": "public", "security_level": "internal", "role_scope": [], "allowed_roles": [],
                "dept_id": "public", "status": "active"},
            "license_note": "original project Chinese paraphrase with attribution; no full RFC copied"}))
        for index, (query, line) in enumerate(questions, 1):
            # Include the limitation sentence even if it repeats a boundary claim.
            evidence = [{"source": filename, "file_sha256": checksum, "line": n, "quote": lines[n - 1]}
                        for n in sorted({line, 7})]
            cases.append(bind_pending({"id": f"PN-{topic}-{index}", "query": query, "category": "rag_evidence",
                "stratum": "protocol_semantics", "expect_route": "retrieval", "expect_status": "ANSWERED",
                "expect_hit": filename, "source_match": "exact", "hard_negatives": [],
                "split": "dev", "development_exposed": True, "source_ids": [source_id],
                "body_families": [checksum], "topic_groups": [topic], "template_groups": [f"{topic}-question-{index}"],
                "evidence_units": evidence, "request_context": {"tenant_id": "default", "roles": ["ops"]},
                "review_status": "AI_proposal_pending_human", "label_basis": "source text before retrieval; no model output used",
                "independence": "not_claimed; authored and exposed during development",
                "legacy_overlap": "DNS domain overlaps existing template guides; other topics not equivalent to six lab workflows"}))
    payload = {"protocol": "eval-review-candidate-v1", "metrics_eligible": False,
        "sources": sources, "cases": cases}
    validate_review_payload(payload)
    return payload


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", required=True); parser.add_argument("--output", required=True)
    parser.add_argument("--export-version")
    parser.add_argument("--review-package", help="separately human-reviewed copy; generation never approves")
    args = parser.parse_args()
    output = Path(args.output).resolve()
    if output.exists(): raise ValueError("never overwrite a review/export artifact")
    payload = build(args.root)
    if args.review_package:
        reviewed = json.loads(Path(args.review_package).read_text(encoding="utf-8"))
        validate_review_payload(reviewed)
        for field in ("sources", "cases"):
            expected = [{k: v for k, v in row.items() if k != "approval"} for row in payload[field]]
            actual = [{k: v for k, v in row.items() if k != "approval"} for row in reviewed[field]]
            if expected != actual: raise ValueError("reviewed content changed; new revision required")
        payload = reviewed
    if args.export_version: payload = export_review(payload, args.export_version)
    # Approval checks finish before any new output is written.
    data = json.dumps(payload, ensure_ascii=False, sort_keys=True, indent=2) + "\n"
    output.parent.mkdir(parents=True, exist_ok=True); output.write_text(data, encoding="utf-8")
    print(json.dumps({"sources": len(payload["sources"]), "cases": len(payload["cases"]),
        "approved_cases": sum(c["approval"]["status"] == "approved" for c in payload["cases"])}))


if __name__ == "__main__": main()
