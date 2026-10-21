"""Apply only the exact approved 16-row domain candidate to its isolated DB."""
from __future__ import annotations
import argparse
import json
import sys
from datetime import datetime, timezone
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
import sqlalchemy as sa
from qa_core.domain.models import EnterpriseSnapshot
from qa_core.storage.domain_dao import TABLE_KEYS
from scripts.prepare_agent_domain_review import validate_package,load,file_hash,digest
from scripts import legacy_demo_runtime as rt

PROTOCOL='agent-domain-admission-v1'
ALLOW=('departments','users','user_departments','devices','monitoring_metrics','alarms','work_orders','change_records','knowledge_relations')
EMPTY=('devices','monitoring_metrics','alarms','work_orders','change_records','knowledge_relations')

def validate_receipt(package,receipt):
    candidate=load(Path(package)/'candidate.json')
    source=Path(candidate['binding']['source_snapshot']['path'])
    projection=Path(candidate['binding']['approved_projection']['path'])
    candidate=validate_package(package,source,projection)
    required={'protocol','status','scope','candidate_sha256','artifact_manifest_sha256','target','record_count','evidence','approved_at_utc'}
    if set(receipt)!=required or receipt['protocol']!=PROTOCOL or receipt['status']!='approved' or receipt['scope']!='isolated_local_demo_only':
        raise ValueError('requires a separate explicit domain approval receipt')
    if (receipt['candidate_sha256']!=digest(candidate) or receipt['artifact_manifest_sha256']!=file_hash(Path(package)/'artifact-hashes.json')
            or receipt['target']!=candidate['target'] or receipt['record_count']!=16 or candidate['record_count']!=16):
        raise ValueError('approval is not bound to these exact 16 rows and artifacts')
    if (receipt['evidence'].get('answer')!='批准16条，仅限隔离本地演示（推荐）'
            or receipt['evidence'].get('question_item_id')!='["request_user_input_async","call_0773e14b44634178aa4503ded1c3a758",0]'):
        raise ValueError('requires the actual domain admission decision, not the old knowledge approval')
    if datetime.fromisoformat(receipt['approved_at_utc']).utcoffset() is None:
        raise ValueError('approval timestamp requires an explicit timezone')
    if candidate['snapshot']['tenants'] or candidate['snapshot']['knowledge_assets']:
        raise ValueError('never writes existing tenant or knowledge assets')
    return candidate

def utc_values(model):
    values=model.model_dump()
    for key,value in list(values.items()):
        if isinstance(value,datetime):
            if value.utcoffset() is None: raise ValueError('business timestamp requires UTC conversion')
            values[key]=value.astimezone(timezone.utc).replace(tzinfo=None)
        elif isinstance(value,(dict,list)):values[key]=json.dumps(value,ensure_ascii=False,allow_nan=False)
    return values

def batches(candidate):
    snapshot=EnterpriseSnapshot.model_validate_json(json.dumps(candidate['snapshot']),strict=True)
    return [(table,[utc_values(row) for row in getattr(snapshot,'changes' if table=='change_records' else table)]) for table in ALLOW]

def check_target(settings,candidate):
    url=sa.engine.make_url(settings.mysql_dsn)
    if (not settings.is_test or not settings.auth_required or settings.generation_enabled or url.database!='test_legacy_stage_fb36d947ab67'
            or url.host!='127.0.0.1' or url.port!=13306 or settings.milvus_uri!='http://127.0.0.1:29530'
            or settings.doc_collection!=url.database+'_doc' or settings.faq_collection!=url.database+'_faq'
            or candidate['target']['database']!=url.database or candidate['target']['version']!=rt.VERSION):
        raise ValueError('requires exact approved isolated storage and disabled generation')

def preconditions(conn,candidate):
    versions=[dict(row) for row in conn.execute(sa.text('SELECT version,status FROM kb_versions ORDER BY version FOR UPDATE')).mappings()]
    if versions!=[{'version':rt.VERSION,'status':'ACTIVE'}]:raise ValueError('ACTIVE changed')
    tenant=conn.execute(sa.text("SELECT status FROM tenants WHERE tenant_id='default' FOR UPDATE")).scalar_one_or_none()
    if tenant!='active':raise ValueError('existing default tenant unavailable')
    required=candidate['prerequisites']['knowledge_assets'][0]
    asset=conn.execute(sa.text('SELECT * FROM knowledge_assets WHERE asset_id=:id FOR UPDATE'),{'id':required['asset_id']}).mappings().one_or_none()
    if asset is None:raise ValueError('approved knowledge asset missing')
    for key,value in required.items():
        actual=asset.get(key)
        if key=='role_scope' and isinstance(actual,str):actual=json.loads(actual)
        if actual!=value:raise ValueError('approved asset identity, content or access fields changed: '+key)
    return {table:conn.exec_driver_sql(f'SELECT COUNT(*) FROM `{table}`').scalar_one() for table in ALLOW}

def insert_rows(conn,rows):
    for table,records in rows:
        if table not in ALLOW:raise ValueError('table not authorized')
        for record in records:
            columns=list(record)
            keys=TABLE_KEYS[table]
            where=' AND '.join(f'`{key}`=:{key}' for key in keys)
            if conn.execute(sa.text(f'SELECT 1 FROM `{table}` WHERE {where} FOR UPDATE'),{k:record[k] for k in keys}).first():
                raise ValueError('candidate ID conflict; no upsert or overwrite: '+table)
            sql=f'INSERT INTO `{table}` ('+', '.join(f'`{c}`' for c in columns)+') VALUES ('+', '.join(':'+c for c in columns)+')'
            conn.execute(sa.text(sql),record)

def execute(settings,candidate,receipt,report):
    check_target(settings,candidate)
    report=Path(report);report.mkdir(parents=True,exist_ok=True)
    outcome=report/'admission.json'
    if outcome.exists():raise ValueError('admission record already exists; do not repeat durable import')
    rt.verify_active(settings)
    engine=sa.create_engine(settings.mysql_dsn,pool_pre_ping=True)
    try:
        with engine.connect().execution_options(isolation_level='SERIALIZABLE') as conn:
            lock='agent_domain_admission_fb36d947ab67'
            if conn.execute(sa.text('SELECT GET_LOCK(:name,0)'),{'name':lock}).scalar_one()!=1:raise ValueError('admission already running')
            conn.commit()
            try:
                with conn.begin():
                    before=preconditions(conn,candidate)
                    if any(before[table] for table in EMPTY):raise ValueError('domain tables no longer empty; requires a new preflight decision')
                    rows=batches(candidate)
                    insert_rows(conn,rows)
                    after={table:conn.exec_driver_sql(f'SELECT COUNT(*) FROM `{table}`').scalar_one() for table in ALLOW}
                    if any(after[table]-before[table]!=len(records) for table,records in rows):raise ValueError('unexpected inserted row counts')
                    payload={'protocol':PROTOCOL,'candidate_sha256':digest(candidate),'receipt_sha256':digest(receipt),'scope':receipt['scope'],'counts':{table:len(records) for table,records in rows}}
                    conn.execute(sa.text('INSERT INTO audit_log (action,actor,payload_json) VALUES (:action,:actor,:payload)'),
                        {'action':'domain_admission','actor':'codex-approved-local-cli','payload':json.dumps(payload,ensure_ascii=False)})
                # Durable result is recorded immediately after the transaction; no retry.
                rt.write(outcome,{'status':'committed','candidate_sha256':digest(candidate),'receipt_sha256':digest(receipt),
                    'database':candidate['target']['database'],'version':rt.VERSION,'rows_inserted':16,'domain_before':before,'domain_after':after,
                    'insert_only':True,'existing_tenants_assets_updated':False,'business_time_storage':'UTC-naive','persistent_domain_audit':True})
            finally:
                conn.execute(sa.text('SELECT RELEASE_LOCK(:name)'),{'name':lock});conn.commit()
        rt.verify_active(settings)
    finally:engine.dispose()
    return load(outcome)

def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--package',required=True,type=Path);parser.add_argument('--receipt',required=True,type=Path)
    parser.add_argument('--apply',action='store_true');parser.add_argument('--report',type=Path,default=Path('.runtime/test/reports/agent-domain-executed-20261009'))
    args=parser.parse_args();receipt=load(args.receipt);candidate=validate_receipt(args.package,receipt)
    if not args.apply:
        print(json.dumps({'validated':True,'domain_approved':True,'written':False,'record_count':16,'candidate_sha256':digest(candidate)}));return
    settings=rt.apply_profile(rt.build_profile(18011))
    result=execute(settings,candidate,receipt,args.report)
    print(json.dumps(result,ensure_ascii=False))

if __name__=='__main__':main()
