"""Offline design and development baseline for relation-scoped Agent retrieval.

Consumes the approved candidate and completed 17.15 reports. It does not query
storage, initialize models, create approvals or implement the proposed behavior.
"""
from __future__ import annotations
import argparse
import csv
import hashlib
import html
import json
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from scripts.execute_agent_domain_admission import validate_receipt
from scripts.prepare_agent_domain_review import load, digest, file_hash
from qa_core.indexing.parsers.parsers import parse_dispatch
from qa_core.indexing.chunkers.registry import chunk_ticket
from qa_core.indexing.parsers.locations import locate_chunks
from qa_core.schemas.evidence import decode_reference

ROOT=Path(__file__).resolve().parents[1]
PACKAGE=ROOT/'data/candidates/agent-domain-review-r1'
RECEIPT=ROOT/'data/candidates/agent-domain-review-r1.receipt.approved.json'
HISTORICAL=ROOT/'.runtime/test/reports/agent-domain-executed-20261009'
PREFLIGHT=ROOT/'.runtime/test/reports/legacy-staged-20261007-181025/preflight.json'

def baseline(responses, contexts, candidate):
    allowed={(r['asset_id'],r['source_version']) for r in candidate['prerequisites']['knowledge_assets']}
    cases=[]
    for case,kind in [('A1-alarm','alarms'),('A2-device','devices')]:
        found=[r for r in responses if r.get('case')==case]
        if len(found)!=1 or found[0]['http_status']!=200:raise ValueError('missing unique completed historical case')
        result=found[0]['response'];context=contexts[kind]
        context_assets={(r['asset_id'],r['source_version']) for r in context['knowledge']}
        if context_assets!=allowed or context.get('truncated') or result['kb_version']!=candidate['target']['version']:
            raise ValueError('historical context or ACTIVE identity mismatch')
        if result['status']!='ANSWERED' or result['mode']!='domain' or len(result['observations'])!=5 or not result['sources']:
            raise ValueError('historical baseline is not a completed real domain case')
        sources=[]
        for source in result['sources']:
            identity=decode_reference(source['evidence_id'])
            if identity[0]!=result['kb_version'] or identity[3:5]!=[source['document_id'],source['source_revision']] or identity[5]!=hashlib.sha256(source['text'].encode()).hexdigest():
                raise ValueError('historical citation content/identity mismatch')
            sources.append({'document_id':source['document_id'],'source_revision':source['source_revision'],
                'source':source['source'],'chunk_id':source['chunk_id'],'location':source['location'],
                'inside_authorized_related_asset':(source['document_id'],source['source_revision']) in allowed})
        cases.append({'case':case,'run_id':result['run_id'],'query':found[0]['body'],'sources':sources,
            'tools_used':result['budget']['calls_used'],'associated_assets':[list(pair) for pair in sorted(context_assets)]})
    all_sources=[r for c in cases for r in c['sources']]
    return {'status':'historical_completed_17.15_not_reexecuted','usage':'dev/regression',
        'independent_frozen':0,'cases':cases,'total_citations':len(all_sources),
        'associated_asset_citations':sum(s['inside_authorized_related_asset'] for s in all_sources),
        'record_precision_evaluated':False,'note':'counts are relationship alignment, not semantic correctness or root-cause quality'}

def record_roster(path, expected):
    path=Path(path)
    if file_hash(path)!=expected['checksum']:raise ValueError('approved source bytes changed')
    with path.open(encoding='utf-8-sig',newline='') as stream:
        reader=csv.DictReader(stream,strict=True);fields=reader.fieldnames or []
        if len(fields)!=len(set(fields)) or not {'ticket_id','title','phenomenon','root_cause','resolution'}<=set(fields):
            raise ValueError('missing or duplicate CSV identity headers')
        rows=list(reader)
    if not rows or any(None in r or any(v is None for v in r.values()) for r in rows):raise ValueError('malformed CSV shape')
    tickets=[r['ticket_id'] for r in rows]
    if any(not t for t in tickets) or len(tickets)!=len(set(tickets)):raise ValueError('duplicate/missing ticket identity')
    chunks=chunk_ticket(parse_dispatch('fault_case',path));locations=locate_chunks(path,chunks)
    records=[]
    for i,(chunk,location) in enumerate(zip(chunks,locations)):
        if not location or location['ambiguous'] or len(location['matches'])!=1 or len(location['matches'][0])!=1:
            raise ValueError('exact record location is unavailable or ambiguous')
        span=location['matches'][0][0];row=rows[span['record']-1]
        # Existing strict ingestion stamps ticket_id from source. That is valid
        # only for this source when source agrees with the row ticket identity.
        if chunk.metadata['source']!=row['ticket_id']:raise ValueError('ingested source fallback is not a valid ticket identity')
        records.append({'ticket_id':row['ticket_id'],'record':span['record'],'line_start':span['line_start'],
            'line_end':span['line_end'],'chunk_text_sha256_before_pipeline_normalization':hashlib.sha256(chunk.text.encode()).hexdigest(),
            'csv_row_sha256':digest(row),'source_sha256':location['file_sha256'],
            'asset_id':expected['asset_id'],'source_revision':expected['source_version'],'chunk_index':i})
    return {'status':'offline_source_reconstruction_not_live_vector_read','records':records,
        'csv_records':len(rows),'reconstructed_chunks':len(chunks),'unique_ticket_ids':len(tickets),
        'no_tokenizer_or_embedding_initialized':True,'locations_unambiguous':True}

def design(candidate):
    return {'protocol':'agent-relation-plan-v1','status':'designed_not_implemented','usage':'dev/regression',
        'target':{key:candidate['target'][key] for key in ('database','version')},'independent_frozen':0,'public_request_fields':['query','alarm_id','device_id'],
        'modes':[
            {'name':'knowledge','trigger':'no entity ID','restriction':'existing scoped general knowledge search'},
            {'name':'related_assets','trigger':'ordinary domain diagnosis','restriction':'OR of authorized (asset_id,source_revision) pairs inside the existing tenant/ACL/ACTIVE/lifecycle filter',
             'empty':'NO_EVIDENCE/related_evidence_unavailable; do not broaden to global search'},
            {'name':'linked_work_order','trigger':'explicit request for associated work-order original; a query ticket ID must match an already authorized work_order relation',
             'restriction':'OR of exact (asset_id,source_revision,ticket_id) triples; bounded strong scalar lookup, not relevance ranking',
             'selection':'zero authorized linked work orders -> NO_EVIDENCE; one -> select it; more than one without an exact authorized query ID -> NEEDS_CLARIFICATION',
             'publication':'content-bound original chunks only, complete selected record, unambiguous CSV location; no reconstructed token/text'}],
        'invariants':[
            'derive constraints from fresh authorized typed edges, never from tool arguments or arbitrary query asset IDs',
            'edges and assets must match the run ACTIVE, tenant, revision, effective interval and role/device/department/security scope',
            'never turn an asset-file relation into a record relation except through an authorized work_order edge',
            'asset/revision/ticket constraints stay hard in Dense and BM25 and any soft-filter retry',
            'candidate cache includes canonical pair/triple roster and constraint revision; cached candidates are intersected again',
            'fresh edge/asset/context fingerprint is checked before every tool and before publication; changes clear observations and sources',
            'existing manifest, withdrawal, key revocation, source-body hash and final source rereads remain mandatory',
            'empty constraint is deny-all for domain modes, distinct from omitted constraint for knowledge mode',
            'existing citation text and identity token remain unchanged; additive server-derived retrieval explanation is optional'],
        'bounds':{'authorized_entities':81,'distinct_assets':20,'work_orders':20,'target_triples':20,
            'references':3,'tools':10,'total_seconds':90,'tool_seconds':30,'result_bytes':32768,'total_bytes':131072,
            'excess_policy':'fail closed on incomplete/truncated target roster or >3 required record chunks; do not pick an arbitrary prefix'},
        'workflow_policy':'domain SOP uses related SOP assets only; this candidate has no linked SOP so related_evidence_unavailable. Knowledge SOP keeps missing_safety_controls; disposable linked unsafe-SOP fixture must still refuse unsafe SOP.',
        'record_integrity':'current approved file has 50 records/50 historical indexed chunks. Future split records need all parts, bypass parent dedup for exact lookup, and reject partial/ambiguous/over-budget records.',
        'record_query_forms':['核对关联工单原文','请核对关联工单 SIM-TT-00001 的原文'],
        'record_query_rules':'activate exact mode only for an explicit original-record request. Parse an optional strict entity ID after the work-order noun; multiple IDs clarify, an unauthorized ID denies, unsupported selector syntax clarifies. Never silently substitute the sole authorized ticket for a different requested ID.',
        'live_precondition_for_next_batch':'read actual DOC schema and authorized root row ticket_id/source/asset/revision/location before implementation regression; this offline roster cannot prove current vector row availability',
        'implementation_order':[
            {'files':['qa_core/storage/domain_dao.py','qa_core/application/domain_context_service.py','qa_core/application/agent_tools.py'],
             'change':'bounded typed target resolver preserves edge provenance; fresh relation/context fingerprint in Agent guard'},
            {'files':['qa_core/retrieval/searcher.py','qa_core/application/evidence_service.py'],
             'change':'private canonical pair/triple constraints applied before both recalls, retry and cache; bounded exact-record lookup'},
            {'files':['qa_core/application/agent_diagnose.py','qa_core/schemas/agent.py','static/agent-workspace.js'],
             'change':'deterministic explicit-record selection, complete original read and additive explanation/clarification; public request fields stay strict'},
            {'files':['tests/unit/test_readonly_agent.py','tests/integration/test_readonly_agent_domain.py','tests/browser/agent_domain_smoke.js'],
             'change':'disposable fault fixtures and real scoped read regression; report baseline comparison by mode, not semantic score'}],
        'rollout':'code change only first; existing approved DB/vector/ACTIVE are reused for authorized read regression. Any new schema/re-embedding/content approval is a separate proposal.',
        'next_batch':'implement related_assets and linked_work_order with the above constraints in existing Agent; first unit/disposable fixtures then real unchanged approved-storage regression',
        'execution':{'database_connected':False,'containers_started':False,'api_started':False,'models_initialized':False,
            'paid_llm_calls':0,'passage_embeddings':0,'rows_imported':0,'production_code_changed':False}}

def acceptance_cases(asset):
    root={'query':'ALM-AMF-REG 常见原因','alarm_id':'SIM-ALM-000001'}
    exact={'query':'请核对关联工单 SIM-TT-00001 的原文','alarm_id':'SIM-ALM-000001'}
    rows=[
        ('R01','related_assets',root,'ANSWERED; all returned sources use linked asset/revision, no claim all citations are SIM-TT-00001'),
        ('R02','related_assets',{'query':root['query'],'device_id':'SIM-DEV-0001'},'same file constraints as R01'),
        ('R03','linked_work_order',exact,'ANSWERED; selected authorized ticket SIM-TT-00001, CSV record1 line2, all original chunks and fresh source reads; current file should have1 source/4tools'),
        ('R04','related_assets',{'query':'ALM-SMF-PDU 常见原因','device_id':'SIM-DEV-0002'},'NO_EVIDENCE/related_evidence_unavailable for valid unrestricted ops: no relation -> no global fallback'),
        ('R05','linked_work_order',{'fixture':'multiple permitted linked tickets, none selected'},'NEEDS_CLARIFICATION; no arbitrary first ticket'),
        ('R06','linked_work_order',{'query':'核对关联工单 SIM-TT-00002 原文','alarm_id':'SIM-ALM-000001'},'NO_EVIDENCE/linked_record_unavailable: a same-file ticket is not an authorized related ticket'),
        ('R07','linked_work_order',{'fixture':'allowed triples A/rev1/T1 and B/rev2/T2; row A/rev1/T2'},'reject Cartesian-product match'),
        ('R08','related_assets',{'fixture':'empty target roster and high scoring unrelated global result'},'deny all, no recall without hard constraint'),
        ('R09','linked_work_order',{'fixture':'ticket_id schema missing or values empty/mismatch source'},'NO_EVIDENCE/linked_record_unavailable, no string-substring guess'),
        ('R10','related_assets',{'fixture':'linked asset in another version/expired/withdrawn'},'fail closed before emitting sources'),
        ('R11','related_assets',{'fixture':'cached candidates for a different edge/asset roster'},'separate canonical cache key; intersect candidates again'),
        ('R12','related_assets',{'fixture':'soft type filter returns0 and retry is enabled'},'hard pair constraints survive retry in Dense and BM25'),
        ('R13','related_assets',{'fixture':'edge/context/asset scope changes after initial reads'},'NO_EVIDENCE/relation_changed; no observations/sources; disposable DB only'),
        ('R14','related_assets',{'fixture':'key revoked, ACTIVE/manifest/withdrawal/content digest changes'},'existing fresh guards and final rereads still stop; use owned key/disposable lifecycle fixture'),
        ('R15','linked_work_order',{'fixture':'split record3 required parts, parent dedup would keep1'},'fetch all3 parts; incomplete or >3 required chunks refuse rather than partial answer'),
        ('R16','linked_work_order',{'fixture':'duplicate/ambiguous or missing original CSV location'},'NO_EVIDENCE/record_location_unavailable; no invented row'),
        ('R17','related_assets',{'fixture':'too many assets/edges/tickets or truncated context'},'NO_EVIDENCE/relationship_scope_incomplete; no arbitrary partial roster'),
        ('R18','related_assets',{'query':'ALM-AMF-REG SOP','alarm_id':'SIM-ALM-000001'},'NO_EVIDENCE/related_evidence_unavailable; candidate links only case CSV, no linked SOP'),
        ('R19','knowledge',{'query':'ALM-AMF-REG SOP'},'NO_EVIDENCE/missing_safety_controls remains; linked unsafe-SOP disposable fixture also refuses'),
        ('R20','related_assets',{'fixture':'foreign tenant/department/device/public scope/wrong entity pair'},'existing permissions and entity mismatch stop without partial data'),
        ('R21','knowledge',{'query':'ALM-AMF-REG 常见原因'},'knowledge mode remains ordinary scoped search; no implied domain record'),
        ('R22','browser',{'widths':[1280,390]},'relation-bound original vs related-file explanations, unknown observation time, original reread, clarification/reset late-response suppression'),
        ('R23','cleanup',{'verification':'existing approved storage only; owned API/keys/profile'},'knowledge/ACL/vector/ACTIVE/256assets/domain counts unchanged; clean temporary processes/keys; no paid model')]
    return {'status':'planned_not_executed','usage':'dev/regression','independent_frozen':0,
        'linked_asset_id':asset['asset_id'],'cases':[{'id':i,'mode':m,'input':q,'expected':e} for i,m,q,e in rows]}

def prepare():
    receipt=load(RECEIPT);candidate=validate_receipt(PACKAGE,receipt)
    summary=load(HISTORICAL/'summary.json');admission=load(HISTORICAL/'admission.json')
    if (not summary['passed'] or summary['api_cases']!=12 or admission['candidate_sha256']!=digest(candidate)
            or admission['receipt_sha256']!=digest(receipt) or admission['rows_inserted']!=16):raise ValueError('requires completed exact 17.15 admission and regression')
    asset=candidate['prerequisites']['knowledge_assets'][0]
    roster=record_roster(Path(candidate['binding']['approved_projection']['path'])/'raw'/asset['filename'],asset)
    historical_preflight=load(PREFLIGHT)
    if roster['csv_records']!=50 or roster['reconstructed_chunks']!=historical_preflight['file_counts'][asset['filename']]:raise ValueError('source reconstruction differs from historical ingestion count')
    witness=[r for r in roster['records'] if r['ticket_id']==candidate['binding']['csv_record']['ticket_id']]
    if len(witness)!=1 or witness[0]['record']!=1 or witness[0]['csv_row_sha256']!=candidate['binding']['csv_record']['sha256']:raise ValueError('approved linked record witness changed')
    inputs=[PACKAGE/'candidate.json',PACKAGE/'artifact-hashes.json',RECEIPT,HISTORICAL/'summary.json',HISTORICAL/'admission.json',
        HISTORICAL/'real-api-responses.json',HISTORICAL/'real-domain-contexts.json',PREFLIGHT,
        Path(candidate['binding']['approved_projection']['path'])/'raw'/asset['filename']]
    code=['qa_core/application/agent_tools.py','qa_core/application/agent_diagnose.py','qa_core/application/domain_context_service.py',
        'qa_core/storage/domain_dao.py','qa_core/retrieval/searcher.py','qa_core/application/evidence_service.py','qa_core/indexing/chunkers/registry.py',
        'qa_core/indexing/parsers/locations.py','scripts/rebuild_kb.py','scripts/stage_legacy_candidate.py','qa_core/schemas/evidence.py','qa_core/schemas/agent.py',
        'scripts/prepare_agent_relation_plan.py']
    binding={'inputs':{str(p):file_hash(p) for p in inputs},'code':{name:file_hash(ROOT/name) for name in code}}
    return {'plan.json':design(candidate),'historical-baseline.json':baseline(load(HISTORICAL/'real-api-responses.json'),load(HISTORICAL/'real-domain-contexts.json'),candidate),
        'record-roster.json':roster,'acceptance-cases.json':acceptance_cases(asset),'input-bindings.json':binding}

def review(values):
    counts=values['historical-baseline.json']
    blocks=''.join('<details><summary>'+html.escape(name)+'</summary><pre>'+html.escape(json.dumps(value,ensure_ascii=False,indent=2))+'</pre></details>' for name,value in values.items())
    return ('<!doctype html><html lang="zh-CN"><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">'
        '<title>只读Agent关联检索方案</title><style>body{font:15px/1.7 system-ui;max-width:980px;margin:auto;padding:24px;color:#18304d}pre{white-space:pre-wrap;overflow-wrap:anywhere;background:#f3f6fb;padding:16px}details{margin:16px 0;padding:12px;border:1px solid #ddd;border-radius:8px}</style>'
        f'<h1>关联文件检索与精确工单原文核对</h1><p>设计已完成，运行行为尚未实现。本页是开发方案，不是准入/发布或独立质量报告。</p><p>17.15历史两次实体诊断共{counts["total_citations"]}份引用，关联文件内{counts["associated_asset_citations"]}份；未重跑API。当前关联CSV有50条记录，指定工单为SIM-TT-00001/记录1。</p>'
        '<p>复用已有标量字段、内容绑定引用和原文定位；限定关联资料并重验关系/权限，保持既有知识、向量和ACTIVE。付费模型0、DB/容器/API/模型启动0。</p>'+blocks+'</html>')

def validate_output(directory):
    directory=Path(directory);expected=prepare();expected['review.html']=review(expected)
    hashes=load(directory/'artifact-hashes.json')
    if set(hashes)!=set(expected):raise ValueError('plan artifact roster changed')
    for name,value in expected.items():
        if file_hash(directory/name)!=hashes[name]:raise ValueError('plan artifact hash changed')
        actual=(directory/name).read_text(encoding='utf-8') if name.endswith('.html') else load(directory/name)
        if actual!=value:raise ValueError('plan differs from rederived inputs or design')
    return {'passed':True,'status':'design_and_historical_baseline_only','cases_planned':len(expected['acceptance-cases.json']['cases']),
        'historical_citations':expected['historical-baseline.json']['total_citations'],'associated_asset_citations':expected['historical-baseline.json']['associated_asset_citations'],
        'source_records':expected['record-roster.json']['csv_records'],'live_validation':False,'production_code_changed':False,
        'database_connected':False,'containers_started':False,'models_initialized':False,'paid_llm_calls':0,'passage_embeddings':0,'independent_frozen':0}

def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output',type=Path,default=ROOT/'.runtime/test/reports/agent-relation-plan-20261009')
    parser.add_argument('--validate-only',action='store_true');args=parser.parse_args()
    if args.validate_only:print(json.dumps(validate_output(args.output),ensure_ascii=False));return
    if args.output.exists():raise ValueError('refuse to overwrite an existing design package; validate it instead')
    values=prepare();values['review.html']=review(values);args.output.mkdir(parents=True)
    for name,value in values.items():
        (args.output/name).write_text(value if isinstance(value,str) else json.dumps(value,ensure_ascii=False,indent=2),encoding='utf-8')
    (args.output/'artifact-hashes.json').write_text(json.dumps({name:file_hash(args.output/name) for name in values},indent=2),encoding='utf-8')
    print(json.dumps(validate_output(args.output),ensure_ascii=False))

if __name__=='__main__':main()
