"""Short-lived authenticated original STAGED HTTP/Edge preview; no publication."""
import argparse
import base64
import copy
import json
import os
from pathlib import Path
import secrets
import socket
import subprocess
import sys
import tempfile
import time

import httpx
import sqlalchemy as sa

from scripts.original_citation_candidate import CANDIDATE, VERSION, validate_candidate
from scripts.stage_original_citation_candidate import REPORT, configure, validate_target
from scripts.prepare_legacy_projection import read, write


def available_port():
    with socket.socket() as connection:
        connection.bind(('127.0.0.1', 0))
        return connection.getsockname()[1]


def main():
    validate_candidate()
    settings = configure()
    validate_target(settings)
    output = REPORT / 'browser-preview'
    if output.exists():
        raise ValueError('new preview report required')
    with socket.socket() as connection:
        if connection.connect_ex(('127.0.0.1', 18013)) == 0:
            raise ValueError('owned preview port occupied; refuse replace')
    from qa_core.storage.kb_dao import KbDao
    from qa_core.storage.version_manifest import verify_read_binding
    dao = KbDao(settings.mysql_dsn)
    if dao.get_version(VERSION)['status'] != 'STAGED' or not verify_read_binding(settings, VERSION):
        raise ValueError('bound STAGED candidate required')
    output.mkdir()
    samples = read(REPORT / 'evidence-api.json')['samples']
    if len(samples) != 5:
        raise ValueError('five source-group citations required')
    metadata = read(CANDIDATE / 'raw/meta.json')
    groups_by_asset = {meta['asset_id']: meta['doc_type'] for meta in metadata.values()}
    for sample in samples:
        sample['source_group'] = groups_by_asset[sample['evidence']['document_id']]
    if [sample['source_group'] for sample in samples] != ['faq', 'fault_case', 'guide', 'sop', 'manual']:
        raise ValueError('five intended source groups must match approved asset identities')
    from qa_core.api.auth import create_api_key, hash_key
    keys = {}
    server, log = None, None
    try:
        for role in ('admin', 'ops', 'foreign', 'public', 'revoked'):
            key = secrets.token_urlsafe(24)
            create_api_key(key, 'original-citation-' + role + '-' + secrets.token_hex(6),
                ['ops'] if role == 'ops' else ['admin'], tenant_id='other' if role == 'foreign' else 'default',
                security_levels=['public'] if role == 'public' else None)
            keys[role] = key
        with dao.engine.begin() as conn:
            conn.execute(sa.text('UPDATE api_keys SET enabled=0 WHERE key_hash=:h'), {'h': hash_key(keys['revoked'])})
        log = (output / 'server.log').open('w', encoding='utf-8')
        child = dict(os.environ, LLM_API_KEY='', GENERATION_ENABLED='false')
        server = subprocess.Popen([sys.executable, '-m', 'uvicorn', 'app:app', '--host', '127.0.0.1',
            '--port', '18013', '--log-level', 'warning'], env=child, stdout=log, stderr=log,
            creationflags=subprocess.CREATE_NO_WINDOW if os.name == 'nt' else 0)
        address = 'http://127.0.0.1:18013'
        with httpx.Client(base_url=address, timeout=180, trust_env=False) as http:
            deadline = time.monotonic() + 45
            while time.monotonic() < deadline:
                try:
                    if http.get('/', timeout=2).status_code == 200:
                        break
                except httpx.TransportError:
                    pass
                if server.poll() is not None:
                    raise RuntimeError('owned preview server exited')
                time.sleep(.15)
            else:
                raise RuntimeError('preview startup deadline exceeded')
            results = []
            for sample in samples:
                ref = sample['evidence']
                response = http.get(ref['source_url'], headers={'X-API-Key': keys['admin']})
                if response.status_code != 200 or response.json() != ref:
                    raise ValueError('real HTTP citation differs from verified original')
                statuses = {'admin': response.status_code, 'no_key': http.get(ref['source_url']).status_code}
                for role in ('ops', 'foreign', 'public', 'revoked'):
                    statuses[role] = http.get(ref['source_url'], headers={'X-API-Key': keys[role],
                        'X-User-Roles': 'admin', 'X-Tenant-ID': 'default'}).status_code
                if statuses != {'admin': 200, 'no_key': 401, 'ops': 404, 'foreign': 404, 'public': 404, 'revoked': 401}:
                    raise ValueError('STAGED auth/tenant/security/revocation boundary failed')
                from qa_core.schemas.evidence import decode_reference
                identity = decode_reference(ref['evidence_id'])
                altered_status = {}
                for index, field, value in ((3, 'asset', 'another-asset'), (4, 'revision', 'another-revision'),
                                             (5, 'body_digest', '0' * 64), (0, 'version', 'nonexistent-release')):
                    changed = list(identity)
                    changed[index] = value
                    token = base64.urlsafe_b64encode(json.dumps(changed, ensure_ascii=False,
                        separators=(',', ':')).encode()).decode().rstrip('=')
                    status = http.get('/kb/evidence/' + token, headers={'X-API-Key': keys['admin']}).status_code
                    if status != 404:
                        raise ValueError('tampered citation identity/body/version must fail closed')
                    altered_status[field] = status
                results.append({'group': sample['source_group'], 'stored_doc_type': sample['doc_type'], 'reference': ref, 'statuses': statuses,
                                'tampered_identity_statuses': altered_status})
            write(output / 'real-http-citations.json', {'passed': True, 'source_groups': 5, 'cases': results})
            # All 20 cards are real Strong reads, then real HTTP re-reads of their bound references.
            from pymilvus import MilvusClient
            from qa_core.retrieval.searcher import Searcher
            from qa_core.security.policy import AccessScope
            from qa_core.schemas.evidence import evidence_of
            expected = read(REPORT / 'expected-rows.json')
            reference_assets = {meta['asset_id']: name for name, meta in metadata.items() if meta['doc_type'] == 'manual'}
            client = MilvusClient(uri=settings.milvus_uri, timeout=120)
            try:
                searcher = Searcher(client=client, settings=settings, embedder=object(), cache=object())
                cards = []
                for asset, name in sorted(reference_assets.items()):
                    row = next(row for row in expected.values() if row['asset_id'] == asset)
                    doc = searcher.fetch_evidence_chunk(row['id'], VERSION, scope=AccessScope.build(roles=['admin']))
                    if doc is None:
                        raise ValueError('reference card unreadable')
                    ref = evidence_of(doc)
                    response = http.get(ref['source_url'], headers={'X-API-Key': keys['admin']})
                    if response.status_code != 200 or response.json() != ref or ref['synthetic']:
                        raise ValueError('reference card HTTP text/location/origin mismatch')
                    cards.append({'filename': name, 'asset_id': asset, 'status': 200,
                                  'source_revision': ref['source_revision'], 'location': ref['location']})
                if len(cards) != 20:
                    raise ValueError('all 20 project-written reference cards required')
                write(output / 'reference-cards.json', {'passed': True, 'cards': cards})
            finally:
                client.close()
            query = {'query': 'AMF注册成功率下降，排查时先核对哪些信息？', 'kb_version': VERSION, 'top_n': 5}
            response = http.post('/kb/search-evidence', json=query, headers={'X-API-Key': keys['admin']})
            if response.status_code != 200 or response.json().get('status') != 'EVIDENCE_FOUND':
                raise ValueError('fresh real HTTP candidate retrieval failed')
            retrieval = response.json()
            for ref in retrieval['evidence']:
                original = http.get(ref['source_url'], headers={'X-API-Key': keys['admin']})
                if original.status_code != 200 or original.json()['text'] != ref['text']:
                    raise ValueError('HTTP retrieved reference could not be reread')
            sop = http.post('/kb/search-evidence', json={'query': 'ALM-AMF-REG SOP 处置流程',
                'kb_version': VERSION, 'top_n': 5}, headers={'X-API-Key': keys['admin']})
            if sop.status_code != 200 or sop.json().get('reason') != 'missing_safety_controls':
                raise ValueError('legacy workflow safety refusal changed in real HTTP')
            write(output / 'real-http-retrieval.json', {'passed': True, 'development_probe_only': True,
                  'retrieval': retrieval, 'sop_safety_response': sop.json(), 'paid_calls': 0})
        temp_root = (Path.cwd() / '.runtime/test').resolve()
        with tempfile.TemporaryDirectory(prefix='original-citation-edge-', dir=temp_root) as profile:
            if not Path(profile).resolve().is_relative_to(temp_root):
                raise ValueError('browser profile outside intended temporary root')
            payload = {'edge': 'C:/Program Files (x86)/Microsoft/Edge/Application/msedge.exe', 'profile': profile,
                'debugPort': available_port(), 'url': address, 'key': keys['admin'], 'version': VERSION,
                'sources': [{**sample['evidence'], 'doc_id': sample['evidence']['chunk_id'],
                             'doc_type': sample['doc_type'], 'score': .8} for sample in samples], 'reportDir': str(output)}
            run = subprocess.run(['node', 'tests/browser/original_citation_staged_smoke.js'], input=json.dumps(payload),
                encoding='utf-8', text=True, capture_output=True, timeout=75,
                creationflags=subprocess.CREATE_NO_WINDOW if os.name == 'nt' else 0)
            (output / 'browser.log').write_text(run.stdout + run.stderr, encoding='utf-8')
            if run.returncode:
                raise RuntimeError('candidate browser preview failed; inspect browser.log')
        result = {'passed': True, 'status': 'STAGED', 'source_groups': 5, 'direct_citation_http_cases': 50,
                  'reference_cards_reread': 20, 'real_retrieval_references_reread': len(retrieval['evidence']),
                  'whole_sop_safety_refusal_preserved': True, 'generation_enabled': False, 'paid_calls': 0,
                  'browser_ui_answer_is_controlled_probe': True, 'original_release_authorized': False,
                  'temporary_profile_removed': True}
    finally:
        if server is not None:
            server.terminate()
            try:
                server.wait(timeout=15)
            except subprocess.TimeoutExpired:
                server.kill()
                server.wait(timeout=5)
        if log is not None:
            log.close()
        with dao.engine.begin() as conn:
            for key in keys.values():
                conn.execute(sa.text('DELETE FROM api_keys WHERE key_hash=:h'), {'h': hash_key(key)})
        dao.engine.dispose()
    write(output / 'summary.json', {**result, 'temporary_keys_removed': True, 'api_stopped': True})
    print('Real original STAGED HTTP/Edge citations passed; owned API/Keys/profile cleaned', flush=True)


if __name__ == '__main__':
    main()
