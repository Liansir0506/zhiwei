"""Execute only the approved original local STAGED candidate; never release.

An explicit original-target guard, fresh audit, insert-only identities and separate
collections protect legacy knowledge. Docker lifecycle belongs to the caller.
"""
import argparse
from collections import Counter
from datetime import datetime, timezone
import json
import os
from pathlib import Path

import sqlalchemy as sa

from qa_core.config.settings import Settings, get_settings
from scripts.original_citation_candidate import CANDIDATE, REVIEW, VERSION, preflight, validate_candidate
from scripts.prepare_original_citation_review import TARGET, audit_original, runtime_settings, sql_snapshot
from scripts.prepare_legacy_projection import digest, read, write

ROOT = Path(__file__).resolve().parents[1]
REPORT = ROOT / '.runtime/test/reports/original-citation-staged-20261009'


def configure(report=REPORT):
    base = runtime_settings()
    values = base.model_dump()
    values.update(doc_collection=TARGET['candidate_collections'][0], faq_collection=TARGET['candidate_collections'][1],
                  data_dir=str(CANDIDATE), reports_dir=str(Path(report).resolve() / 'quality'),
                  api_host='127.0.0.1', api_port=18013, auth_required=True, rate_limit_qps=100,
                  generation_enabled=False, semantic_cache_enabled=False, agent_enabled=False,
                  workflow_evidence_enabled=True, llm_api_key='', llm_base_url='http://127.0.0.1:1/disabled',
                  redis_url='redis://127.0.0.1:6379/14', embedding_device='cpu', rerank_device='cpu')
    settings = Settings(_env_file=None, **values)
    validate_target(settings)
    # All fields supplied to both current-process singleton and future HTTP children.
    os.environ.update({name.upper(): 'true' if value is True else 'false' if value is False else str(value)
                       for name, value in settings.model_dump().items()})
    os.environ.update(TOKENIZERS_PARALLELISM='false', OMP_NUM_THREADS='4', MKL_NUM_THREADS='4',
                      HF_HUB_OFFLINE='1', TRANSFORMERS_OFFLINE='1')
    get_settings.cache_clear()
    if get_settings().model_dump() != settings.model_dump():
        raise ValueError('execution settings differ from explicit original candidate profile')
    return settings


def validate_target(settings):
    url = sa.engine.make_url(settings.mysql_dsn)
    if (settings.app_env != 'dev' or url.host not in {'127.0.0.1', 'localhost'}
            or url.port != 3306 or url.database != 'rag_kb'
            or settings.milvus_uri not in {'http://localhost:19530', 'http://127.0.0.1:19530'}
            or [settings.doc_collection, settings.faq_collection] != TARGET['candidate_collections']
            or settings.generation_enabled or settings.llm_api_key or settings.agent_enabled
            or not settings.auth_required or not settings.workflow_evidence_enabled
            or settings.api_host != '127.0.0.1' or settings.api_port != 18013
            or settings.redis_url != 'redis://127.0.0.1:6379/14'):
        raise ValueError('exact approved original local STAGED target required; original collections/entry forbidden')


def asset_values(name, meta, checksum, approved_at):
    from qa_core.domain.models import KnowledgeAsset
    from qa_core.storage.domain_dao import _db_values
    model = KnowledgeAsset(asset_id=meta['asset_id'], tenant_id=meta['tenant_id'], dept_id=meta['dept_id'],
        filename=name, doc_type=meta['doc_type'], source_system=meta['source_system'], source_id=meta['source_id'],
        source_version=meta['source_version'], security_level=meta['security_level'], visibility=meta['visibility'],
        role_scope=meta['role_scope'], status=meta['status'], effective_from=meta['effective_from'],
        effective_to=meta['effective_to'], kb_version=VERSION, checksum=checksum, reviewed_by=None,
        approved_at=approved_at)
    values = _db_values(model)
    for key in ('effective_from', 'effective_to', 'approved_at'):
        if values[key] is not None:
            values[key] = values[key].astimezone(timezone.utc).replace(tzinfo=None)
    return values


def insert_missing_assets(conn, entries, metadata, candidate, receipt):
    inserted = []
    approved_at = datetime.fromisoformat(receipt['recorded_at_utc'])
    for entry in entries:
        if entry['existing_registration_action'] != 'insert_new_after_approval':
            continue
        name = entry['filename']
        values = asset_values(name, metadata[name], digest(Path(candidate) / 'raw' / name), approved_at)
        if conn.execute(sa.text('SELECT 1 FROM knowledge_assets WHERE asset_id=:id FOR UPDATE'),
                        {'id': values['asset_id']}).first():
            raise ValueError('asset identity conflict; no upsert or overwrite')
        columns = list(values)
        conn.execute(sa.text('INSERT INTO knowledge_assets (' + ','.join('`'+key+'`' for key in columns) +
                             ') VALUES (' + ','.join(':'+key for key in columns) + ')'), values)
        inserted.append(values['asset_id'])
    if len(inserted) != 256 or len(set(inserted)) != 256:
        raise ValueError('exact 256 missing identities required')
    return inserted


def admit(settings, metadata, candidate, report):
    plan = read(REVIEW / 'proposal.json')
    receipt = read(Path(candidate) / 'approval-receipt.json')
    engine = sa.create_engine(settings.mysql_dsn, pool_pre_ping=True)
    try:
        with engine.connect().execution_options(isolation_level='SERIALIZABLE') as conn:
            acquired = conn.execute(sa.text('SELECT GET_LOCK(:name,0)'), {'name': 'rag_kb_publication'}).scalar()
            conn.commit()
            if acquired != 1:
                raise ValueError('candidate/publication write busy')
            try:
                with conn.begin():
                    old = [dict(row) for row in conn.execute(sa.text('SELECT * FROM knowledge_assets ORDER BY asset_id')).mappings()]
                    if len(old) != 20:
                        raise ValueError('original asset roster drifted before admission')
                    if conn.execute(sa.text('SELECT 1 FROM kb_versions WHERE version=:v FOR UPDATE'), {'v': VERSION}).first():
                        raise ValueError('candidate version collision; no repeat admission')
                    inserted = insert_missing_assets(conn, plan['entries'], metadata, candidate, receipt)
                    after_old = [dict(row) for row in conn.execute(sa.text('SELECT * FROM knowledge_assets '
                        'WHERE kb_version=:v ORDER BY asset_id'), {'v': 'v4-foundation-r3'}).mappings()]
                    if after_old != old:
                        raise ValueError('existing reference registrations changed')
                    payload = {'protocol': 'original-citation-admission-v1', 'candidate_version': VERSION,
                               'proposal_sha256': digest(REVIEW / 'proposal.json'),
                               'receipt_sha256': digest(Path(candidate) / 'approval-receipt.json'),
                               'candidate_manifest_sha256': digest(Path(candidate) / 'artifact-hashes.json'),
                               'scope': plan['scope'], 'admitted_files': 276, 'new_identities': inserted,
                               'preserved_reference_assets': [row['asset_id'] for row in old],
                               'department_resolution': receipt['department_resolution'],
                               'joint_withdrawal_groups': receipt['acknowledged_withdrawal_groups'],
                               'original_release_authorized': False,
                               'reviewed_by_sql': None,
                               'reviewer_note': 'Human chat decision recorded by receipt/audit; no synthetic user created.'}
                    conn.execute(sa.text('INSERT INTO audit_log(action,actor,payload_json) VALUES(:action,:actor,:p)'),
                        {'action': 'knowledge_admission', 'actor': 'codex-approved-local-cli',
                         'p': json.dumps(payload, ensure_ascii=False)})
                write(Path(report) / 'admission.json', {'committed': True, 'rows_inserted': 256,
                    'existing_reference_assets_updated': False, 'insert_only': True, 'payload': payload})
            finally:
                conn.execute(sa.text('SELECT RELEASE_LOCK(:name)'), {'name': 'rag_kb_publication'})
                conn.commit()
    finally:
        engine.dispose()


def verify_sop_sources(settings, report):
    from pymilvus import MilvusClient
    from scripts.migrate_legacy_collections import read_rows
    from qa_core.retrieval.searcher import Searcher
    from qa_core.retrieval.filters import _to_doc
    from qa_core.retrieval.workflow import assemble_sop
    from qa_core.security.policy import AccessScope
    client = MilvusClient(uri=settings.milvus_uri, timeout=120)
    try:
        rows = read_rows(client, settings.doc_collection)
        grouped = {}
        for row in rows:
            if row['doc_type'] == 'sop':
                grouped.setdefault(row['asset_id'], []).append(row)
        if len(grouped) != 200:
            raise ValueError('200 approved SOP sources required')
        searcher = Searcher(client=client, settings=settings, embedder=object(), cache=object())
        results = []
        for asset, stored in sorted(grouped.items()):
            seed = _to_doc({'id': stored[0]['id'], 'entity': stored[0]})
            docs = searcher.fetch_sop_source(seed, ['admin'], VERSION, scope=AccessScope.build(roles=['admin']))
            wanted = {row['id']: row for row in stored}
            if ({doc.id for doc in docs} != set(wanted) or len(docs) != 11
                    or any(doc.text != wanted[doc.id]['text'] or doc.source_version != 'v4-foundation-r3' for doc in docs)):
                raise ValueError('whole SOP source text/revision/count mismatch')
            packet = assemble_sop(docs, seed)
            if packet.packet is None or packet.ready or packet.reason != 'missing_safety_controls':
                raise ValueError('legacy SOP safety refusal changed')
            results.append({'asset_id': asset, 'chunks': len(docs), 'workflow_reason': packet.reason})
        write(Path(report) / 'sop-sources.json', {'passed': True, 'sources': len(results),
              'chunks': sum(r['chunks'] for r in results), 'workflow_ready_sources': 0, 'results': results})
    finally:
        client.close()


def execute(report=REPORT):
    report = Path(report).resolve()
    if report.exists():
        raise ValueError('new execution report required; existing durable candidate must be inspected, not repeated')
    validate_candidate()
    # This audit uses the legacy collection profile even though subsequent writes use new names.
    print('Auditing actual original protected stores before any writes', flush=True)
    before = audit_original()
    settings = configure(report)
    from transformers import AutoTokenizer
    tokenizer = AutoTokenizer.from_pretrained(settings.embedding_model_path, local_files_only=True)
    print('Strict local parser/tokenizer preflight', flush=True)
    metadata, expected, positions, plan = preflight(CANDIDATE, tokenizer)
    report.mkdir(parents=True)
    write(report / 'original-before.json', before)
    write(report / 'preflight.json', plan)
    write(report / 'expected-rows.json', expected)
    write(report / 'expected-locations.json', positions)
    write(report / 'environment.json', {'database': 'rag_kb', 'version': VERSION,
        'doc_collection': settings.doc_collection, 'faq_collection': settings.faq_collection,
        'candidate': str(CANDIDATE), 'candidate_manifest_sha256': digest(CANDIDATE / 'artifact-hashes.json'),
        'scope': 'original_local_demo_staged_only', 'original_release_authorized': False,
        'generation_enabled': False, 'preview_port': 18013, 'preview_redis_db': 14})
    print(json.dumps({'preflight_files': plan['files'], 'preflight_chunks': plan['chunks'],
                      'stored_doc_types': plan['stored_doc_types']}), flush=True)
    from pymilvus import MilvusClient
    from scripts.init_collections import build
    from qa_core.storage.location_dao import LocationDao
    client = MilvusClient(uri=settings.milvus_uri, timeout=120)
    try:
        if any(client.has_collection(name) for name in TARGET['candidate_collections']):
            raise ValueError('new candidate collection collision; no overwrite')
        # Admission commits once and never changes the original 20 registrations.
        admit(settings, metadata, CANDIDATE, report)
        build(client, settings.doc_collection, False)
        build(client, settings.faq_collection, True)
    finally:
        client.close()
    engine = sa.create_engine(settings.mysql_dsn)
    try:
        LocationDao(engine).ensure_schema()
    finally:
        engine.dispose()
    import torch
    torch.set_num_threads(4)
    from scripts.rebuild_kb import run_rebuild
    def progress(stage, done, total, stats):
        write(report / 'progress.json', {'stage': stage, 'completed': done, 'total': total, 'stats': stats})
        print(json.dumps({'stage': stage, 'files': done, 'total': total, 'failed': stats['failed']}), flush=True)
    stats = run_rebuild(VERSION, raw_dir=CANDIDATE / 'raw', strict_metadata=True,
        reviewed_source_map=CANDIDATE / 'source-map.json', record_locations=True, tokenizer_chunking=True, progress=progress)
    write(report / 'ingest.json', stats)
    if stats['failed'] or stats['done'] != 276:
        raise ValueError('candidate ingest incomplete; preserve STAGED and reports, do not publish')
    print('Verifying every stored row/location, five fresh gates and authenticated citations', flush=True)
    from scripts.stage_legacy_candidate import verify_candidate
    result = verify_candidate(settings, metadata, expected, positions, report,
                              version=VERSION, groups=('faq', 'fault_case', 'guide', 'sop', 'manual'))
    if not result['gate_passed']:
        raise ValueError('fresh ordinary gate failed; preserve STAGED and diagnostic report')
    verify_sop_sources(settings, report)
    write(report / 'stage-summary.json', {**result, 'files': 276, 'chunks': len(expected),
          'new_assets': 256, 'preserved_reference_assets': 20, 'sop_sources_checked': 200,
          'paid_calls': 0, 'original_active_changed': False, 'original_release_authorized': False})
    print('Original local candidate STAGED validation passed; publication not performed', flush=True)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--execute', action='store_true')
    parser.add_argument('--report-dir', type=Path, default=REPORT)
    args = parser.parse_args()
    if not args.execute:
        parser.error('explicit --execute required for the approved candidate only')
    execute(args.report_dir)


if __name__ == '__main__':
    main()
