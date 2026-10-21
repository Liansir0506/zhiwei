"""Offline literal review of the known legacy SOP template, never admission.

Unknown or edited prose stays unreviewed. A successful template match proves only
which statements the file contains; it cannot establish operational safety.
"""
from collections import defaultdict
import hashlib
import json
from pathlib import Path
import re

ACTIONS = ('确认影响范围', '核对最近变更', '采集关键指标', '检查上下游依赖',
           '验证配置一致性', '执行低风险处置', '检查派生告警', '验证业务恢复',
           '观察三个周期', '记录复盘项')


def hashed(value):
    return hashlib.sha256(json.dumps(value, ensure_ascii=False, sort_keys=True,
                                     default=str).encode('utf-8')).hexdigest()


def source_path(raw, name):
    if not isinstance(name, str) or not re.fullmatch(r'sop_operation_\d{3}\.md', name):
        raise ValueError('invalid legacy SOP filename')
    root = Path(raw).absolute()
    path = root / name
    if root.resolve() != root or path.resolve() != path or not path.is_file():
        raise ValueError('SOP source path is not a direct, regular legacy file')
    return path


def quote(text, phrase, checksum):
    matches = [(i, line) for i, line in enumerate(text.splitlines(), 1) if phrase in line]
    if len(matches) != 1:
        raise ValueError('quote requires a unique full source line')
    number, line = matches[0]
    return dict(line=number, quote=line, file_sha256=checksum)


def audit_sops(raw, metadata, version):
    entries, groups = [], defaultdict(list)
    for name, meta in sorted(metadata.items()):
        if meta.get('doc_type') != 'sop':
            continue
        path = source_path(raw, name)
        body_bytes = path.read_bytes()
        text = body_bytes.decode('utf-8')
        checksum = hashlib.sha256(body_bytes).hexdigest()
        lines = text.splitlines()
        steps = list(re.finditer(r'^步骤 (\d+)：(.+)$', text, re.M))
        condition_match = re.search(r'针对(.+?)记录对象、版本、时间和前后值', text)
        condition = condition_match[1] if condition_match else ''
        equipment, alarm = meta.get('equipment_type', ''), meta.get('alarm_code', '')
        expected = '\n\n'.join(
            f'步骤 {i}：执行{action}。针对{condition}记录对象、版本、时间和前后值；证据不足时停止并转人工复核。'
            for i, action in enumerate(ACTIONS, 1))
        expected_header = f"适用：{equipment}/{meta.get('network_tech', '')}；模拟告警：{alarm}。"
        actual = text.split('\n', 1)[1] if len(lines) > 1 else ''
        # Windows-authored sources may use CRLF; bind original bytes separately.
        # Only line endings are normalized for the exact known prose comparison.
        recognized = (bool(condition) and actual.replace('\r\n','\n').strip() == expected_header+'\n\n'+expected
                      and [int(s[1]) for s in steps] == list(range(1, 11)))
        entry = dict(filename=name, title=lines[0] if lines else '', file_sha256=checksum,
                     body_sha256=hashlib.sha256(actual.encode('utf-8')).hexdigest(),
                     metadata_sha256=hashed(meta), asset_id=meta.get('asset_id'), kb_version=version,
                     source_version=meta.get('source_version'), equipment=equipment, alarm=alarm,
                     condition_asserted_by_template=condition, scope_snapshot=meta,
                     recognized_template=recognized, executable=False,
                     review_kind='ai_literal_support_audit', human_semantic_approved=False,
                     runtime_alias_approved=False, findings=[])
        if not recognized:
            entry['findings'] = [dict(role='full_source', status='unreviewed_changed_or_unknown_template')]
        else:
            roles = [
                ('applicability', 'device_declared_software_version_missing', '适用：'),
                ('branch_evidence', 'condition_asserted_no_observation_or_alternative_branch', '步骤 3：'),
                ('bounded_action', 'placeholder_not_an_instruction', '步骤 6：'),
                ('approval', 'absent_in_exact_full_body', None),
                ('rollback', 'absent_in_exact_full_body', None),
                ('stop', 'evidence_missing_guard_present_other_abort_conditions_missing', '步骤 1：'),
                ('recovery', 'verification_named_criteria_and_measurement_missing', '步骤 8：'),
                ('observation', 'three_cycles_without_duration_or_pass_criterion', '步骤 9：'),
            ]
            entry['findings'] = [dict(role=role, status=status,
                evidence=quote(text, phrase, checksum) if phrase else
                dict(full_body_sha256=entry['body_sha256'], line_start=2, line_end=len(lines),
                     method='exact_known_template_comparison_not_keyword_approval'))
                for role, status, phrase in roles]
            if lines[0].startswith('# LTE') and '/5G' in expected_header:
                entry['findings'].append(dict(role='network_applicability',
                    status='lte_title_conflicts_with_5g_applicability_requires_semantic_review',
                    evidence=quote(text, '适用：', checksum), title_evidence=quote(text, '# LTE', checksum)))
        entries.append(entry)
        groups[entry['body_sha256']].append(entry)
    families = []
    for body_hash, members in sorted(groups.items()):
        families.append(dict(body_sha256=body_hash, representative=members[0]['filename'],
            equipment=members[0]['equipment'], alarm=members[0]['alarm'],
            condition=members[0]['condition_asserted_by_template'],
            filenames=[e['filename'] for e in members], file_count=len(members),
            all_metadata_equal=len({e['metadata_sha256'] for e in members}) == 1,
            approved_runtime_aliases=[], executable=False))
    return dict(protocol='legacy-sop-literal-support-audit-v1', version=version,
        summary=dict(files=len(entries), body_families=len(families),
            known_template_files=sum(e['recognized_template'] for e in entries),
            changed_or_unknown_files=sum(not e['recognized_template'] for e in entries),
            network_applicability_conflicts=sum(any(f['role']=='network_applicability'
                for f in e['findings']) for e in entries),
            executable_approved=0, human_semantic_approved=0, independent_frozen=0),
        entries=entries, families=families,
        scope='Source-specific literal audit; neither human truth nor independent quality or operational approval')


def verify_quote(path, evidence):
    data = Path(path).read_bytes()
    lines = data.decode('utf-8').splitlines()
    if (hashlib.sha256(data).hexdigest() != evidence['file_sha256']
            or type(evidence['line']) is not int or not 1 <= evidence['line'] <= len(lines)
            or lines[evidence['line']-1] != evidence['quote']):
        raise ValueError('source quote or exact file binding changed')
    return True
