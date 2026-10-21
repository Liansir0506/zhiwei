"""Project and preflight the explicitly approved original local STAGED candidate.

This module is offline. It does not mint approval, connect to storage or publish.
"""
import argparse
from collections import Counter
import copy
import hashlib
import json
from pathlib import Path

from qa_core.ingest.source_identity import validate_mapping
from qa_core.quality.review_contract import bind_pending, check_approval
from scripts import prepare_original_citation_review as review_module
from scripts.prepare_legacy_projection import digest, read, seal, separate, verify_seal, write
from scripts.stage_legacy_candidate import prepare_chunks
from scripts.rebuild_kb import dedup_key, to_milvus_row

ROOT = Path(__file__).resolve().parents[1]
REVIEW = review_module.OUTPUT
RECEIPT = ROOT / 'data/candidates/original-citation-local-r1.receipt.approved.json'
CANDIDATE = ROOT / 'data/candidates/original-citation-local-r1'
VERSION = review_module.TARGET['candidate_version']


def approved_inputs(review, receipt_path):
    review = Path(review).resolve()
    review_module.validate(review)
    plan = read(review / 'proposal.json')
    receipt = read(receipt_path)
    check_approval(receipt, required=True)
    if (receipt.get('protocol') != 'original-citation-receipt-v1'
            or receipt.get('proposal_sha256') != digest(review / 'proposal.json')
            or receipt.get('review_manifest_sha256') != digest(review / 'artifact-hashes.json')
            or receipt.get('target') != plan['target'] or receipt.get('scope') != plan['scope']
            or receipt.get('reviewer_kind') != 'human_user_chat_confirmation'
            or not str(receipt.get('user_message', '')).strip()
            or receipt.get('department_resolution') != plan['department_resolution']
            or receipt.get('acknowledged_withdrawal_groups') !=
               [group['group_binding_sha256'] for group in plan['shared_withdrawal_groups']]
            or receipt.get('original_release_authorized') is not False):
        raise ValueError('exact original STAGED content-bound human receipt required')
    names = receipt.get('approved_filenames')
    expected_names = [entry['filename'] for entry in plan['entries']]
    if names != expected_names or len(set(names)) != 276:
        raise ValueError('this receipt must explicitly cover the reviewed 276-file roster')
    source_entries = {entry['filename']: entry for entry in
                      read(review_module.PACKAGE / 'source-proposals.json')['entries']}
    metadata, mapping = {}, []
    for entry in plan['entries']:
        name = entry['filename']
        meta = copy.deepcopy(entry['proposed_metadata'])
        meta.update(doc_type=source_entries[name]['legacy_ledger']['doc_type'],
                    status='active', review_status='human_chat_approved')
        metadata[name] = meta
        mapped = bind_pending({'filename': name, 'sha256': entry['source_sha256'],
                               'tenant_id': meta['tenant_id'], 'source_system': meta['source_system'],
                               'source_id': meta['source_id']})
        mapped['approval'].update(status='approved', proposer=receipt['approval']['proposer'],
                                  reviewer=receipt['approval']['reviewer'],
                                  evidence=receipt['approval']['evidence'] + '; receipt_sha256=' + digest(receipt_path))
        check_approval(mapped, required=True)
        mapping.append(mapped)
    source_map = {'protocol': 'reviewed-source-map-v1', 'entries': mapping}
    validate_mapping(review_module.PACKAGE / 'raw', source_map, metadata)
    intent = {'protocol': 'original-citation-candidate-v1', 'version': VERSION, 'intended_status': 'STAGED',
              'scope': plan['scope'], 'target': plan['target'], 'approved_files': 276,
              'source_revision': review_module.VERSION, 'proposal_sha256': digest(review / 'proposal.json'),
              'review_manifest_sha256': digest(review / 'artifact-hashes.json'),
              'database_version_created': False, 'published': False,
              'original_active_changed': False, 'original_release_authorized': False,
              'limitations': plan['acceptance_limits'] + plan['not_authorized']}
    return receipt, metadata, source_map, intent


def project(review=REVIEW, receipt_path=RECEIPT, output=CANDIDATE):
    output = Path(output).resolve()
    separate(review_module.PACKAGE, output)
    separate(review, output)
    receipt, metadata, mapping, intent = approved_inputs(review, receipt_path)
    output.mkdir(parents=True)
    raw = output / 'raw'
    raw.mkdir()
    for name in metadata:
        (raw / name).write_bytes((review_module.PACKAGE / 'raw' / name).read_bytes())
    write(raw / 'meta.json', metadata)
    write(output / 'source-map.json', mapping)
    write(output / 'approval-receipt.json', receipt)
    write(output / 'candidate-intent.json', intent)
    seal(output)
    return validate_candidate(output, review)


def validate_candidate(candidate=CANDIDATE, review=REVIEW):
    candidate = Path(candidate).resolve()
    verify_seal(candidate)
    _, metadata, mapping, intent = approved_inputs(review, candidate / 'approval-receipt.json')
    if (read(candidate / 'raw/meta.json') != metadata or read(candidate / 'source-map.json') != mapping
            or read(candidate / 'candidate-intent.json') != intent
            or {p.name for p in (candidate / 'raw').iterdir()} != set(metadata) | {'meta.json'}):
        raise ValueError('candidate identity/scope/roster differs from explicit approval')
    for name in metadata:
        if digest(candidate / 'raw' / name) != digest(review_module.PACKAGE / 'raw' / name):
            raise ValueError('candidate source bytes changed')
    validate_mapping(candidate / 'raw', mapping, metadata)
    return {'validated': True, 'sources': 276, 'scope': intent['scope'],
            'original_import_authorized': True, 'original_release_authorized': False,
            'candidate_manifest_sha256': digest(candidate / 'artifact-hashes.json')}


def preflight(candidate, tokenizer):
    candidate = Path(candidate).resolve()
    validate_candidate(candidate)
    metadata = read(candidate / 'raw/meta.json')
    seen, expected, locations, file_counts, duplicates = set(), {}, {}, {}, 0
    for name, meta in sorted(metadata.items()):
        path = candidate / 'raw' / name
        chunks, unique = prepare_chunks(path, meta, tokenizer), []
        for chunk in chunks:
            scope = {key: chunk.metadata.get(key) for key in (
                'tenant_id', 'dept_id', 'visibility', 'allowed_roles', 'role_scope', 'security_level',
                'status', 'source_version', 'effective_from', 'effective_to')}
            key = dedup_key(chunk) + json.dumps(scope, sort_keys=True, ensure_ascii=False)
            if key in seen:
                duplicates += 1
            else:
                seen.add(key)
                unique.append(chunk)
        from qa_core.indexing.parsers.locations import locate_chunks
        positions = locate_chunks(path, unique)
        if not unique or len(positions) != len(unique) or any(pos is None or pos['ambiguous'] for pos in positions):
            raise ValueError('full unambiguous source locations required: ' + name)
        checksum = digest(path)
        for index, (chunk, position) in enumerate(zip(unique, positions)):
            identity = hashlib.sha256(json.dumps([VERSION, meta['tenant_id'], meta['asset_id'], checksum,
                index, chunk.text], ensure_ascii=False, separators=(',', ':')).encode()).hexdigest()[:32]
            if identity in expected:
                raise ValueError('candidate chunk identity collision')
            expected[identity] = to_milvus_row(chunk, identity, [], VERSION)
            locations[identity] = position
        file_counts[name] = len(unique)
    return metadata, expected, locations, {'files': len(metadata), 'chunks': len(expected),
        'duplicates': duplicates, 'file_counts': file_counts,
        'file_groups': dict(Counter(meta['doc_type'] for meta in metadata.values())),
        'stored_doc_types': dict(Counter(row['doc_type'] for row in expected.values())),
        'all_source_locations_unambiguous': True, 'tokenizer_chunking': True,
        'paid_calls': 0, 'embedding_calls': 0}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--project', action='store_true')
    parser.add_argument('--candidate', type=Path, default=CANDIDATE)
    args = parser.parse_args()
    print(json.dumps(project(output=args.candidate) if args.project else validate_candidate(args.candidate), ensure_ascii=False))


if __name__ == '__main__':
    main()
