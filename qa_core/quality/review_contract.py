"""Offline label-review binding and declared-link split audit, not reviewer authentication."""
import hashlib
import json


LINK_FIELDS = ("source_ids", "body_families", "topic_groups", "template_groups")


def review_digest(item):
    payload = {k: v for k, v in item.items() if k != "approval"}
    return hashlib.sha256(json.dumps(payload, ensure_ascii=False, sort_keys=True,
                                    separators=(",", ":"), allow_nan=False).encode()).hexdigest()


def check_approval(item, *, required=False):
    approval = item.get("approval", {})
    if approval.get("binding_sha256") != review_digest(item):
        raise ValueError("review binding changed; re-review required")
    status = approval.get("status")
    if status not in {"pending", "approved", "rejected"}:
        raise ValueError("invalid approval status")
    if required and status != "approved":
        raise ValueError("pending/rejected item cannot be used for metrics or ingestion")
    if status == "approved" and (not approval.get("proposer") or not approval.get("reviewer")
            or approval["proposer"] == approval["reviewer"] or not str(approval.get("evidence", "")).strip()):
        raise ValueError("independent reviewer and review evidence required")


def bind_pending(item):
    item["approval"] = {"status": "pending", "proposer": "", "reviewer": "", "evidence": "",
                        "binding_sha256": review_digest(item)}
    return item


def audit_groups(cases):
    ids = [case["id"] for case in cases]
    if not ids or len(ids) != len(set(ids)):
        raise ValueError("empty cases or duplicate case ID")
    parent = dict(zip(ids, ids))
    def find(key):
        while parent[key] != key:
            parent[key] = parent[parent[key]]
            key = parent[key]
        return key
    links = {}
    for case in cases:
        check_approval(case)
        if case.get("split") not in {"dev", "regression", "frozen"} or type(case.get("development_exposed")) is not bool:
            raise ValueError("split and exposure declaration required")
        for field in LINK_FIELDS:
            values = case.get(field)
            if not isinstance(values, list) or not values or any(not isinstance(v, str) or not v for v in values):
                raise ValueError("all source/body/topic/template links must be declared")
            for value in values:
                key = (field, value)
                if key in links:
                    parent[find(case["id"])] = find(links[key])
                else:
                    links[key] = case["id"]
    groups = {}
    for case in cases:
        groups.setdefault(find(case["id"]), []).append(case)
    result = []
    for members in groups.values():
        splits = {c["split"] for c in members}
        if len(splits) != 1:
            raise ValueError("linked source/body/topic/template group crosses splits")
        if any(c["development_exposed"] for c in members) and splits != {"dev"}:
            raise ValueError("exposed connected group must remain dev")
        result.append({"case_ids": sorted(c["id"] for c in members), "split": members[0]["split"],
                       "development_exposed": any(c["development_exposed"] for c in members)})
    return sorted(result, key=lambda group: group["case_ids"])
