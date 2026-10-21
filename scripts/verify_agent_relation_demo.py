"""Verify the approved domain admission against real isolated storage and UI.

No LLM or passage embedding. Approved domain rows persist; owned keys/API/Edge
are cleaned even when a regression fails. A committed admission is never retried.
"""
import sys
from pathlib import Path
PROJECT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT))
import hashlib
import json
import secrets
import shutil
import subprocess
import time
from concurrent.futures import ThreadPoolExecutor
import httpx
import sqlalchemy as sa
from scripts import legacy_demo_runtime as rt
from scripts.execute_agent_domain_admission import validate_receipt, batches, ALLOW
from scripts.prepare_agent_domain_review import load, digest
from scripts.stage_legacy_candidate import protected_snapshot
from qa_core.api.auth import create_api_key, hash_key
from qa_core.storage.domain_dao import TABLE_KEYS
from qa_core.application.domain_context_service import PRIVATE_FIELDS

REPORT = PROJECT / '.runtime/test/reports/agent-relation-implemented-20261009'
PACKAGE = PROJECT / 'data/candidates/agent-domain-review-r1'
RECEIPT = PROJECT / 'data/candidates/agent-domain-review-r1.receipt.approved.json'

def main():
    browser_only = '--browser-only' in sys.argv
    REPORT.mkdir(parents=True, exist_ok=True)
    receipt = load(RECEIPT); candidate = validate_receipt(PACKAGE, receipt)
    if (rt.load_state() or {}).get('status', 'stopped') != 'stopped':
        raise RuntimeError('an existing runtime must be stopped before this owned regression')
    settings = rt.apply_profile(rt.build_profile(18011))
    engine = sa.create_engine(settings.mysql_dsn, pool_pre_ping=True)
    base_values = {name: field.default for name, field in rt.Settings.model_fields.items()}
    base_values.update({k.lower(): v for k,v in rt.dotenv_values(PROJECT / '.env.test').items() if k.lower() in base_values and v is not None})
    base = rt.Settings(_env_file=None, **base_values)
    owned_keys, state = [], None
    profile_dir = REPORT / 'edge-domain-profile'
    summary = {'passed':False, 'usage':'dev/regression', 'paid_llm_calls':0,
        'candidate_passage_embeddings':0, 'independent_frozen_cases':0,
        'original_storage_accessed':False, 'approved_domain_rows_persist':True}
    def write(name, value): rt.write(REPORT / (name + '.json'), value)
    def canonical_hash(value):
        return hashlib.sha256(json.dumps(value, ensure_ascii=False, sort_keys=True, default=str, separators=(',',':')).encode()).hexdigest()
    def snapshot():
        protected = protected_snapshot(settings)
        with engine.connect() as conn:
            names = set(sa.inspect(conn).get_table_names())
            # Full existing knowledge, content-location, publication and withdrawal rows.
            tables = sorted(n for n in names if n in {'knowledge_assets','tenants','kb_versions'}
                or n.startswith(('ingest_','withdraw','version_manifest','chunk_location','doc_location','file_record')))
            protected['sql_fingerprints'] = {}
            for table in tables:
                rows = [dict(r) for r in conn.exec_driver_sql(f'SELECT * FROM `{table}`').mappings()]
                rows.sort(key=lambda r:json.dumps(r,sort_keys=True,default=str))
                protected['sql_fingerprints'][table] = {'rows':len(rows),'sha256':canonical_hash(rows)}
        return protected
    def extra_snapshot():
        # These complete-table checks start in the verification window. The
        # admission itself additionally validates the frozen publication twice.
        result={}
        with engine.connect() as conn:
            for table in ('evidence_locations','kb_version_manifests','knowledge_source_withdrawals','departments','users','user_departments'):
                rows=[dict(r) for r in conn.exec_driver_sql(f'SELECT * FROM `{table}`').mappings()]
                rows.sort(key=lambda r:json.dumps(r,sort_keys=True,default=str))
                result[table]={'rows':len(rows),'sha256':canonical_hash(rows)}
        return result
    def verify_domain():
        with engine.connect() as conn:
            for table, records in batches(candidate):
                for record in records:
                    keys = TABLE_KEYS[table]
                    where = ' AND '.join(f'`{k}`=:{k}' for k in keys)
                    actual = dict(conn.execute(sa.text(f'SELECT * FROM `{table}` WHERE {where}'),{k:record[k] for k in keys}).mappings().one())
                    for k,v in record.items():
                        if isinstance(v,str) and k in {'attributes_json','payload_json','labels_json','role_scope'}:
                            assert json.loads(actual[k]) == json.loads(v), (table,k)
                        else: assert actual[k] == v, (table,k)
            return {t:conn.exec_driver_sql(f'SELECT COUNT(*) FROM `{t}`').scalar_one() for t in ALLOW}
    def new_key(label, **scope):
        raw = secrets.token_urlsafe(32)
        create_api_key(raw, state['actor'] + '-' + label, ['ops'], **scope)
        owned_keys.append((raw, state['actor'] + '-' + label))
        return raw
    try:
        # Read-only verification of the already committed admission; never import.
        before = {'own':snapshot(), 'shared':protected_snapshot(base)}
        write('verification-storage-before', before)
        admission=load(PROJECT/'.runtime/test/reports/agent-domain-executed-20261009/admission.json')
        assert admission['status']=='committed' and admission['candidate_sha256']==digest(candidate) and admission['receipt_sha256']==digest(receipt)
        counts = verify_domain()
        assert counts == admission['domain_after']
        write('domain-exact', {'passed':True,'rows':16,'counts':counts,'all_business_fields_equal_to_approved_candidate':True})
        extra_before=extra_snapshot();write('verification-sql-before',extra_before)
        print('Approved admission present; exact 16 business rows verified without a repeated import.', flush=True)
        sys.argv = ['legacy_demo_runtime','start']; rt.main()
        state = rt.load_state()
        key = (rt.ROOT / 'api-key.txt').read_text(encoding='utf-8')
        scoped = new_key('domain', dept_id='ran-ops', device_types=['AMF'], security_levels=['internal'])
        foreign = new_key('foreign', tenant_id='foreign')
        public = new_key('public', security_levels=['public'])
        dept = new_key('dept', dept_id='ran-ops')
        dtype = new_key('dtype', device_types=['AMF'])
        with httpx.Client(base_url=state['url'], timeout=180, trust_env=False) as client:
            def req(method, url, raw=key, **kwargs):
                response = client.request(method,url,headers={'X-API-Key':raw},**kwargs)
                response.raise_for_status(); return response.json()
            health = req('GET','/health'); assert health['agent_enabled'] and not health['generation_enabled']; write('health',health)
            req('POST','/kb/search-evidence', json={'query':'ALM-AMF-REG 常见原因','top_n':1})
            write('warmup',{'passed':True,'local_query_models_only':True,'paid_llm_calls':0})
            print('Owned API ready and local models warmed.', flush=True)
            if browser_only:
                prior=load(REPORT / "summary.json")
                assert prior["passed"] and prior["api_cases"]>=19
                responses=load(REPORT / "real-api-responses.json")
            else:
                contexts = {}
                for kind, ident in [('alarms','SIM-ALM-000001'),('devices','SIM-DEV-0001')]:
                    data = req('GET',f'/domain/{kind}/{ident}/context',scoped)
                    assert len(data['knowledge'])==1 and data['knowledge'][0]['asset_id']==candidate['prerequisites']['knowledge_assets'][0]['asset_id']
                    assert len(data['monitoring'])==len(data['work_orders'])==len(data['changes'])==1
                    for group in ('device','alarm','alarms','monitoring','work_orders','changes','knowledge'):
                        rows=data.get(group,[]); rows=[rows] if isinstance(rows,dict) else rows
                        assert all(not (set(row)&PRIVATE_FIELDS) for row in rows if row)
                    contexts[kind] = data
                write('real-domain-contexts',contexts)
                responses=[]
                def diagnose(case, body, raw=scoped, expected=None, reason=None):
                    data=req('POST','/agent/diagnose',raw,json=body)
                    responses.append({'case':case,'http_status':200,'body':body,'response':data})
                    write('real-api-responses',responses)
                    if expected: assert data['status']==expected, (case,data['status'],data['stop_reason'])
                    if reason: assert data['stop_reason']==reason, (case,data['stop_reason'])
                    if data['status']!='ANSWERED': assert not data['observations'] and not data['sources']
                    print(case+': '+data['status']+'/'+data['stop_reason'],flush=True)
                    return data
                for case, body in [('A1-alarm',{'query':'ALM-AMF-REG 常见原因','alarm_id':'SIM-ALM-000001'}),
                                   ('A2-device',{'query':'ALM-AMF-REG 常见原因','device_id':'SIM-DEV-0001'})]:
                    data=diagnose(case,body,expected='ANSWERED',reason='evidence_ready')
                    assert data['mode']=='domain' and data['kb_version']==rt.VERSION
                    assert data['retrieval_scope']['strategy']=='related_assets'
                    assert all(s['document_id']==candidate['prerequisites']['knowledge_assets'][0]['asset_id'] for s in data['sources'])
                    assert len(data['observations'])==5 and 1<=len(data['sources'])<=3 and data['budget']['calls_used']<=8
                    assert data['budget']['calls_used']==len(data['trace'])==2+2*len(data['sources'])
                    assert data['answer'].startswith('以下为可核对的知识摘录，尚不足以确认现场根因')
                    for obs in data['observations']:
                        assert obs['synthetic'] and obs['retrieved_at'] and obs['observation_ref']['call_id']==data['trace'][0]['call_id']
                        assert not (set(obs['fields'])&PRIVATE_FIELDS)
                        if obs['observation_ref']['entity_type'] in ('device','changes'):assert obs['recorded_at'] is None and obs['freshness']=='unknown'
                        else:assert obs['recorded_at'] and obs['freshness']=='snapshot'
                    for source in data['sources']:
                        actual=req('GET','/kb/evidence/'+source['evidence_id'],scoped)
                        assert actual['text']==source['text'] and actual['source_revision']==source['source_revision']=='v4-foundation-r3'
                        assert actual['kb_version']==rt.VERSION and actual['synthetic']
                linked=candidate['prerequisites']['knowledge_assets'][0]['asset_id']
                for label,query in [('R03-explicit','请核对关联工单 SIM-TT-00001 的原文'),('R03-implicit','核对关联工单原文')]:
                    data=diagnose(label,{'query':query,'alarm_id':'SIM-ALM-000001'},expected='ANSWERED',reason='evidence_ready')
                    assert data['retrieval_scope']['strategy']=='linked_work_order' and data['retrieval_scope']['ticket_id']=='SIM-TT-00001'
                    assert len(data['sources'])==1 and data['budget']['calls_used']==4
                    original=data['sources'][0]
                    assert original['document_id']==linked and original['source']=='SIM-TT-00001'
                    assert original['location']['matches'][0][0]['record']==1 and original['location']['matches'][0][0]['line_start']==2
                    assert original['scores']['rerank'] is None
                    assert req('GET','/kb/evidence/'+original['evidence_id'],scoped)['text']==original['text']
                diagnose('R04-authorized-root-no-relations',{'query':'ALM-SMF-PDU 常见原因','device_id':'SIM-DEV-0002'},key,expected='NO_EVIDENCE',reason='related_evidence_unavailable')
                diagnose('R06-same-file-unlinked-ticket',{'query':'核对关联工单 SIM-TT-00002 原文','alarm_id':'SIM-ALM-000001'},expected='NO_EVIDENCE',reason='linked_record_unavailable')
                diagnose('R06-foreign-requested-ticket',{'query':'核对关联工单 OTHER-TICKET 原文','alarm_id':'SIM-ALM-000001'},expected='NO_EVIDENCE',reason='linked_record_unavailable')
                diagnose('R19-knowledge-SOP',{'query':'ALM-AMF-REG SOP'},expected='NO_EVIDENCE',reason='missing_safety_controls')
                data=diagnose('R21-knowledge-compatible',{'query':'ALM-AMF-REG 常见原因'},expected='ANSWERED',reason='evidence_ready')
                assert data['mode']=='knowledge' and data['retrieval_scope'] is None
                diagnose('A3-mismatch',{'query':'注册失败原因','alarm_id':'SIM-ALM-000001','device_id':'SIM-DEV-0002'},expected='NEEDS_CLARIFICATION',reason='entity_mismatch')
                for label,raw,body in [
                    ('A4-combined',scoped,{'query':'会话失败原因','device_id':'SIM-DEV-0002'}),
                    ('A4-department',dept,{'query':'会话失败原因','device_id':'SIM-DEV-0002'}),
                    ('A4-device-type',dtype,{'query':'会话失败原因','device_id':'SIM-DEV-0002'}),
                    ('A5-foreign',foreign,{'query':'注册失败原因','alarm_id':'SIM-ALM-000001'}),
                    ('A6-public',public,{'query':'注册失败原因','alarm_id':'SIM-ALM-000001'})]:
                    diagnose(label,body,raw,expected='NO_EVIDENCE',reason='entity_unavailable')
                diagnose('A7-clarify',{'query':'现场注册失败诊断'},expected='NEEDS_CLARIFICATION',reason='missing_entity')
                diagnose('A8-SOP',{'query':'ALM-AMF-REG SOP','alarm_id':'SIM-ALM-000001'},expected='NO_EVIDENCE',reason='related_evidence_unavailable')
                data=diagnose('A9-unsafe',{'query':'重启出现ALM-AMF-REG的设备','alarm_id':'SIM-ALM-000001'},expected='OUT_OF_SCOPE',reason='unsafe_request')
                assert data['budget']['calls_used']==0
                revoking=new_key('revoke',dept_id='ran-ops',device_types=['AMF'],security_levels=['internal'])
                actor=owned_keys[-1][1]
                with ThreadPoolExecutor(max_workers=1) as pool:
                    future=pool.submit(req,'POST','/agent/diagnose',revoking,json={'query':'ALM-AMF-REG 常见原因','alarm_id':'SIM-ALM-000001'})
                    deadline=time.monotonic()+15
                    while time.monotonic()<deadline:
                        with engine.connect() as conn:
                            started=conn.execute(sa.text("SELECT COUNT(*) FROM permission_audit WHERE actor=:actor AND action='agent_diagnose' AND resource LIKE '%/start'"),{'actor':actor}).scalar_one()
                        if started:break
                        if future.done():raise AssertionError('revocation request finished before observed start')
                        time.sleep(.05)
                    assert started, 'revocation run never accepted'
                    with engine.begin() as conn:
                        assert conn.execute(sa.text('UPDATE api_keys SET enabled=0 WHERE key_hash=:h AND user_id=:actor'),{'h':hash_key(revoking),'actor':actor}).rowcount==1
                    revoked=future.result(timeout=120)
                    assert revoked['stop_reason']=='access_changed' and not revoked['observations'] and not revoked['sources']
                    responses.append({'case':'A10-owned-key-revoked-after-start','http_status':200,'response':revoked});write('real-api-responses',responses)
                print('Permission, safety and accepted-run key revocation checks passed.',flush=True)
        params={'url':state['url'],'key':scoped,'version':rt.VERSION,'edge':r'C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe',
                'profile':str(profile_dir),'reportDir':str(REPORT),'debugPort':19319}
        browser=subprocess.run(['node','tests/browser/agent_relation_smoke.js'],input=json.dumps(params),text=True,encoding='utf-8',capture_output=True,
            timeout=240,cwd=PROJECT,creationflags=subprocess.CREATE_NO_WINDOW)
        (REPORT/'browser.log').write_text(browser.stdout+browser.stderr,encoding='utf-8')
        print(browser.stdout+browser.stderr,flush=True)
        assert browser.returncode==0,'actual domain browser regression failed; see browser.log'
        after={'own':snapshot(),'shared':protected_snapshot(base)};write('verification-storage-after',after)
        assert before==after, 'existing knowledge/shared storage changed'
        extra_after=extra_snapshot();write('verification-sql-after',extra_after)
        assert extra_before==extra_after, 'protected SQL changed during verification'
        assert verify_domain()==counts
        rt.verify_active(settings)
        with engine.connect() as conn:
            admission_rows=conn.execute(sa.text("SELECT actor,payload_json FROM audit_log WHERE action='domain_admission'")).mappings().all()
            assert len(admission_rows)==1 and json.loads(admission_rows[0]['payload_json'])['candidate_sha256']==digest(candidate)
        summary.update(passed=True,rows_inserted=0,relation_retrieval_implemented=True,real_domain_data=True,real_knowledge_retrieval=True,
            api_cases=len(responses),browser=load(REPORT/'browser.json'),knowledge_fingerprints_unchanged=True,
            shared_test_unchanged=True,domain_counts=counts,persistent_admission_audit=True)
    except Exception as exc:
        summary['verification_error']=str(exc)
        raise
    finally:
        with engine.begin() as conn:
            for raw,actor in owned_keys:
                conn.execute(sa.text('DELETE FROM api_keys WHERE key_hash=:h AND user_id=:actor'),{'h':hash_key(raw),'actor':actor})
        if state:
            assert rt.load_state()['instance']==state['instance'], 'runtime ownership changed'
            rt.stop(state,engine)
        with engine.connect() as conn:enabled=conn.exec_driver_sql('SELECT COUNT(*) FROM api_keys WHERE enabled=1').scalar_one()
        assert enabled==0
        engine.dispose()
        if profile_dir.exists():
            assert profile_dir.resolve().parent==REPORT.resolve() and profile_dir.name=='edge-domain-profile'
            for _ in range(40):
                try:shutil.rmtree(profile_dir);break
                except PermissionError:time.sleep(.25)
            assert not profile_dir.exists()
        summary.update(server_stopped=(rt.load_state() or {}).get('status')=='stopped',enabled_keys=enabled,
            key_file_removed=not (rt.ROOT/'api-key.txt').exists(),edge_profile_removed=not profile_dir.exists())
        write('summary',summary)
    print(json.dumps(summary,ensure_ascii=False))

if __name__=='__main__':main()
