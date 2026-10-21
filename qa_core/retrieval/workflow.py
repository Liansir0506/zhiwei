"""Bounded SOP evidence assembly; no execution or new rank thresholds."""
import copy
import re
import unicodedata
from dataclasses import dataclass
from qa_core.rewrite.entities import faq_ids
from qa_core.schemas.retrieval import RetrievedDoc

MAX_WORKFLOW_CHARS = 6000
MAX_WORKFLOW_ROWS = 64
REQUIRED_WORKFLOW_FIELDS = {
    "id", "text", "source", "doc_type", "kb_version", "source_version", "asset_id", "step_index",
    "tenant_id", "dept_id", "visibility", "allowed_roles", "role_scope", "security_level",
    "status", "effective_from", "effective_to",
}


def normalized(value):
    return re.sub(r"\s+", "", unicodedata.normalize("NFKC", value)).casefold()


def is_workflow_query(query):
    if faq_ids(query) or normalized(query).rstrip("?？。") in {"sop是什么", "sop的定义", "sop的含义", "流程是什么", "步骤是什么"}:
        return False
    if "实验" in query and not re.search(r"SOP|处置流程", query, re.I):
        return False
    return bool(re.search(r"流程|步骤|(?<![A-Za-z0-9])SOP(?![A-Za-z0-9])|操作票|应急预案", query, re.I)
                or (re.search(r"ALM-[A-Z0-9]+(?:-[A-Z0-9]+)+", query, re.I)
                    and re.search(r"排除项|停止条件|恢复验证|验证恢复.*采样证据", query))
                or (re.search(r"ALM-[A-Z0-9]+(?:-[A-Z0-9]+)+", query, re.I)
                    and re.search(r"(?:如何|怎么|怎样|应该).*恢复", query))
                or ("取证" in query and "排除" in query and "恢复" in query))


def production_requested(query):
    # Explicit production requests cannot be fulfilled or clarified using lab SOPs.
    # Remove common negative declarations; this remains a conservative lexical guard.
    query = re.sub(r"非生产|不是生产|不用于生产", "", query)
    return bool(re.search(r"真实生产|生产设备|生产环境|生产网络|现网|真实设备", query))


def declared_lab_versions(text):
    declaration = re.search(r"(?<!不)适用[:：]([^\n]+)", text)
    # Exclusion clauses and counterexamples are not applicability declarations.
    positive = re.split(r"不适用", declaration[1])[0] if declaration else text
    return {v.casefold() for v in re.findall(r"lab-\d{4}\.\d{2}", positive, re.I)}


def lab_version_applicable(query, text):
    requested = {v.casefold() for v in re.findall(r"lab-\d{4}\.\d{2}", query, re.I)}
    declared = declared_lab_versions(text)
    if declared and production_requested(query):
        return False
    if requested or declared:
        return bool(requested and declared and requested <= declared)
    return True


def lab_source_applicable(query, text):
    if not lab_version_applicable(query, text):
        return False
    if not re.search(r"lab-\d{4}\.\d{2}", query, re.I):
        return True
    declaration = re.search(r"(?<!不)适用[:：]([^\n]+)", text)
    positive = re.split(r"不适用", declaration[1])[0] if declaration else text.split("步骤", 1)[0]
    equipment = r"(?<![A-Z0-9])(?:AMF|SMF|NRF|UPF|PCF|UDM|AUSF|NSSF|GNODEB|ENODEB)(?![A-Z0-9])"
    requested = set(re.findall(equipment, query.upper()))
    declared = set(re.findall(equipment, positive.upper()))
    return not requested or requested <= declared


def applicable(query, text):
    qv = {v.casefold() for v in re.findall(r"lab-\d{4}\.\d{2}", query, re.I)}
    dv = {v.casefold() for v in re.findall(r"lab-\d{4}\.\d{2}", text, re.I)}
    if qv or dv:
        if dv and production_requested(query):
            return False
        if len(qv) != 1 or qv != dv:
            return False
        condition = re.search(r"条件[:：]([^；;\n]+)", text)
        if not condition or normalized(condition[1]) not in normalized(query):
            return False  # conservative v1: unknown/paraphrased lab conditions need clarification
    requested = set(re.findall(r"ALM-[A-Z0-9]+(?:-[A-Z0-9]+)+", query.upper()))
    if len(requested) > 1:
        return False
    if requested and not requested <= set(re.findall(r"ALM-[A-Z0-9]+(?:-[A-Z0-9]+)+", text.upper())):
        return False
    return True


@dataclass
class WorkflowResult:
    packet: RetrievedDoc | None
    ready: bool
    reason: str
    question: str = ""


CLARIFICATION_QUESTIONS = {
    "ambiguous_conditions": "您实际观测到的是哪一种症状？请只确认一个分支，并补充设备类型和软件版本。",
    "missing_workflow_context": "请补充设备类型、软件版本和当前实际观测症状；不要仅根据告警名称选择处置流程。",
}


def missing_context(query, text):
    lab = {v.lower() for v in re.findall(r"lab-\d{4}\.\d{2}", text, re.I)}
    if not lab: return False
    if production_requested(query): return False
    qv = {v.lower() for v in re.findall(r"lab-\d{4}\.\d{2}", query, re.I)}
    if qv and qv != lab: return False
    if not qv and re.search(r"(?i)(?<![a-z0-9])v(?:ersion)?\s*\d|版本\s*\d", query): return False
    equipment_pattern = r"(?<![A-Z0-9])(?:AMF|SMF|NRF|UPF|PCF|UDM|AUSF|NSSF|GNODEB|ENODEB)(?![A-Z0-9])"
    equipment = set(re.findall(equipment_pattern, query.upper()))
    # Real SOP titles may omit equipment; the explicit applicability declaration
    # in the header is authoritative, not incidental negative mentions.
    header = text.split("步骤", 1)[0].upper()
    target = set(re.findall(equipment_pattern, header))
    if equipment and not equipment <= target: return False
    alarms = set(re.findall(r"ALM-[A-Z0-9]+(?:-[A-Z0-9]+)+", query.upper()))
    if len(alarms) > 1 or (alarms and not alarms <= set(re.findall(r"ALM-[A-Z0-9]+(?:-[A-Z0-9]+)+", header))): return False
    condition = re.search(r"条件[:：]([^；;\n]+)", text)
    return not equipment or not qv or bool(condition and normalized(condition[1]) not in normalized(query))


def assemble_sop(chunks, seed):
    if not chunks or len(chunks) > MAX_WORKFLOW_ROWS:
        return WorkflowResult(None, False, "missing_or_oversized_source")
    def identity_of(doc):
        return (doc.source, doc.asset_id, doc.kb_version, doc.source_version, doc.tenant_id,
                doc.dept_id, doc.security_level, tuple(sorted(doc.role_scope)), doc.status,
                doc.effective_from, doc.effective_to)
    identity = identity_of(seed)
    if not seed.asset_id or not seed.source_version:
        return WorkflowResult(None, False, "missing_version_identity")
    by_step = {}
    for chunk in chunks:
        if identity_of(chunk) != identity:
            return WorkflowResult(None, False, "mixed_source_or_version")
        steps = chunk.extra.get("step_index")
        if not isinstance(steps, list) or len(steps) != 1 or type(steps[0]) is not int:
            return WorkflowResult(None, False, "invalid_step_metadata")
        number = steps[0]
        if number < 0 or number in by_step:
            return WorkflowResult(None, False, "duplicate_or_invalid_step")
        actual = [int(n) for n in re.findall(r"步骤\s*(\d+)", chunk.text)]
        if (number and actual != [number]) or (not number and actual):
            return WorkflowResult(None, False, "step_text_metadata_mismatch")
        by_step[number] = chunk
    numbers = sorted(by_step)
    if len(numbers) < 3 or numbers != list(range(numbers[-1] + 1)):
        return WorkflowResult(None, False, "missing_header_or_step_gap")
    if seed.id not in {c.id for c in chunks}:
        return WorkflowResult(None, False, "seed_withdrawn")
    text = "\n\n".join(by_step[n].text for n in numbers)
    if len(text) > MAX_WORKFLOW_CHARS:
        return WorkflowResult(None, False, "context_budget_exceeded")
    controls = {"approval": bool(re.search(r"审批|批准|授权", text)),
                "rollback": bool(re.search(r"回滚|回退|恢复快照|撤销.*实验", text)),
                "stop": bool(re.search(r"停止|转人工", text))}
    if re.search(r"(?:无需|不需要|不用|跳过|免于).{0,5}(?:审批|批准|授权)", text):
        controls["approval"] = False
    if re.search(r"(?:无法|不能|不支持).{0,5}(?:回滚|回退)|(?:回滚|回退).{0,5}不可用", text):
        controls["rollback"] = False
    ready = all(controls.values())
    packet = copy.deepcopy(by_step[0])
    packet.text = text
    packet.rerank_score = seed.rerank_score
    packet.fusion_score = seed.fusion_score
    packet.extra.update(workflow_evidence=True, workflow_ready=ready,
                        workflow_guard_reason="complete" if ready else "missing_safety_controls",
                        workflow_controls=controls, workflow_steps=numbers[1:],
                        child_chunk_ids=[by_step[n].id for n in numbers])
    from qa_core.schemas.evidence import citation_fields
    packet.extra["child_evidence"] = [citation_fields(by_step[n]) for n in numbers]
    return WorkflowResult(packet, ready, packet.extra["workflow_guard_reason"])


def retrieve_workflow(searcher, reranker, query, roles, kb_version, *, tenant_id="default", scope=None, min_score=0.3, constraint=None):
    options = {'constraint': constraint} if constraint is not None else {}
    recalled = searcher.search_docs([query], roles, kb_version, doc_types=["sop"],
                                    strict_doc_types=True, tenant_id=tenant_id, scope=scope, **options)
    candidates = [d for d in recalled.docs if d.doc_type == "sop" and applicable(query, d.text)][:10]
    conditions = {normalized(m[1]) for d in candidates
                  if (m := re.search(r"条件[:：]([^；;\n]+)", d.text))}
    if len(conditions) > 1:
        verified_conditions = set()
        for seed in candidates[:3]:
            try:
                fresh = assemble_sop(searcher.fetch_sop_source(seed, roles, kb_version,
                    tenant_id=tenant_id, scope=scope), seed)
                if fresh.ready and applicable(query, fresh.packet.text):
                    match = re.search(r"条件[:：]([^；;\n]+)", fresh.packet.text)
                    if match: verified_conditions.add(normalized(match[1]))
            except Exception:
                continue
        return WorkflowResult(None, False, "ambiguous_conditions",
            CLARIFICATION_QUESTIONS["ambiguous_conditions"] if len(verified_conditions) > 1 else "")
    if not candidates:
        potential = [d for d in recalled.docs if d.doc_type == "sop"][:10]
        for seed in reranker.rerank(query, potential, top_n=3):
            if (seed.rerank_score or 0) < min_score: continue
            try:
                source = searcher.fetch_sop_source(seed, roles, kb_version, tenant_id=tenant_id, scope=scope)
                verified = assemble_sop(source, seed)
            except Exception:
                continue
            if verified.ready and missing_context(query, verified.packet.text):
                return WorkflowResult(None, False, "missing_workflow_context", CLARIFICATION_QUESTIONS["missing_workflow_context"])
    ranked = reranker.rerank(query, candidates, top_n=5)
    failure = WorkflowResult(None, False, "no_applicable_sop")
    sources = set()
    for seed in ranked:
        if seed.source in sources:
            continue
        sources.add(seed.source)
        if len(sources) > 3:
            break
        if (seed.rerank_score or 0) < min_score:
            continue
        try:
            chunks = searcher.fetch_sop_source(seed, roles, kb_version, tenant_id=tenant_id, scope=scope)
        except Exception:
            failure = WorkflowResult(None, False, "source_permission_or_schema_check_failed")
            continue
        result = assemble_sop(chunks, seed)
        if result.packet is not None and not applicable(query, result.packet.text):
            result = WorkflowResult(None, False, "source_applicability_changed")
        if result.ready:
            return result
        if failure.packet is None:
            failure = result
    return failure
