"""Verify live ledger and revision content, then seal actual approved STAGED evidence."""
import json
import numpy as np
import sqlalchemy as sa
import xml.etree.ElementTree as ET
from scripts.execute_sop_record_stage import REPORT,VERSION,RECEIPT
from scripts.build_sop_record_candidate import CANDIDATE,validate_inputs
from scripts.prepare_sop_record_admission import ROOT,OUTPUT as REVIEW,load_verified
from scripts.sop_record_stage_profile import profile
from scripts.prepare_legacy_projection import read,write,digest,seal,verify_seal
from scripts.verify_sop_guide_preview import protected
from scripts.migrate_legacy_collections import read_rows,fingerprint
from qa_core.quality.version_workspace import Workspace
from qa_core.storage.kb_dao import KbDao
from qa_core.storage.version_manifest import verify_read_binding
from scripts.rebuild_kb import normalize_text
from qa_core.indexing.parsers.sanitize import sanitize


def main():
    if (REPORT/'artifact-hashes.json').exists():raise ValueError('never reseal an execution report')
    plan,receipt,metadata,_=validate_inputs(RECEIPT);load_verified();verify_seal(CANDIDATE)
    stage=read(REPORT/'state.json');assert stage['state']=='completed' and stage['status']=='STAGED'
    suites=ET.parse(REPORT/'unit-tests.xml').getroot().findall('testsuite')
    assert sum(int(s.attrib['tests']) for s in suites)==106
    assert all(int(s.attrib['failures'])==int(s.attrib['errors'])==int(s.attrib['skipped'])==0 for s in suites)
    before=read(REPORT/'before.json');assert before==read(REPORT/'after.json')
    selected=profile();engine=sa.create_engine(selected.mysql_dsn)
    try:
        counts={}
        with engine.connect() as conn:
            for table in ('knowledge_assets','ingest_records','evidence_locations','api_keys','alarms','devices','work_orders','change_records','knowledge_relations'):
                counts[table]=conn.exec_driver_sql('SELECT COUNT(*) FROM `'+table+'`').scalar_one()
            assert counts==dict(knowledge_assets=20,ingest_records=20,evidence_locations=180,api_keys=0,
                alarms=0,devices=0,work_orders=0,change_records=0,knowledge_relations=0)
            assert conn.exec_driver_sql("SELECT COUNT(*) FROM ingest_records WHERE status='done'").scalar_one()==20
            assert conn.exec_driver_sql("SELECT COUNT(*) FROM kb_versions WHERE status='STAGED'").scalar_one()==1
            assert conn.exec_driver_sql("SELECT COUNT(*) FROM kb_versions WHERE status='ACTIVE'").scalar_one()==0
            assets=conn.exec_driver_sql('SELECT asset_id,filename,source_version,checksum,reviewed_by,approved_at FROM knowledge_assets').mappings().all()
            by_name={e['filename']:e for e in plan['entries']}
            for a in assets:
                entry=by_name[a['filename']]
                assert a['asset_id']==entry['source_identity']['asset_id'] and a['source_version']=='draft-r2'
                assert a['checksum']==entry['body_sha256'] and a['reviewed_by'] is None and a['approved_at'] is not None
    finally:engine.dispose()
    kb=KbDao(selected.mysql_dsn);version=kb.get_version(VERSION)
    assert version['status']=='STAGED' and verify_read_binding(selected,VERSION)
    gates=read(REPORT/'gate-scope.json');assert gates['ordinary_publication_gate_passed'] is False
    assert protected()==before['previous_guides_and_runtime']
    with Workspace(selected).client() as client:
        names=[n for n in client.list_collections() if n not in (selected.doc_collection,selected.faq_collection)]
        prior={n:dict(rows=len(rows),sha256=fingerprint(rows)) for n in names if (rows:=read_rows(client,n)) is not None}
        assert prior==before['isolated_prior_vectors']
        rows=read_rows(client,selected.doc_collection);assert len(rows)==180
        assert not read_rows(client,selected.faq_collection)
        contracts=[]
        for entry in plan['entries']:
            material=[r for r in rows if r['source']==entry['filename']]
            text='\n'.join(r['text'] for r in material)
            for x in entry['extensions']:
                expected_fields=[normalize_text(sanitize(field)[0]) for field in x['requested_fields']]
                assert x['id'] in text and all(field in text for field in expected_fields)
                contracts.append(dict(id=x['id'],source=entry['filename'],source_version='draft-r2',
                    exact_contract_id_stored=True,all_requested_fields_stored=True,field_values_supplied=False,
                    site_evidence_verified=False,action_authorized=False))
        assert len(contracts)==49
    write(REPORT/'stored-record-contracts.json',dict(count=49,records_after=109,checks=contracts))
    evidence=read(REPORT/'evidence-checks.json')
    assert evidence['all_admin_reads']==180 and evidence['ops_guest_foreign_sources']==20
    http_dirs=[p for p in REPORT.glob('http-*') if p.is_dir() and read(p/'state.json')['state']=='completed']
    assert len(http_dirs)==1
    http=http_dirs[0];outcome=read(http/'state.json');cleanup=read(http/'key-cleanup.json')
    assert outcome['http_checks']==67 and outcome['retrieval_total']==outcome['retrieval_topic_matches']==20
    assert cleanup==dict(created=4,removed=4,remaining=0,scope='new isolated database only')
    write(REPORT/'post-http-verification.json',dict(passed=True,counts=counts,original_and_previous_guides_unchanged=True,
        all_preexisting_vectors_unchanged=True,new_stage_status='STAGED',ordinary_gate_passed=False,paid_calls=0))
    write(REPORT/'chat-authorization.json',dict(user_message=receipt['user_message'],recorded_at_utc=receipt['recorded_at_utc'],
        question_item_id='["request_user_input_async","call_6f8519f00b204fcf8ea9e3907362b929",0]',
        receipt_sha256=digest(RECEIPT),scope=receipt['scope'],exact_target=receipt['target'],
        no_human_gold_review=True,publication_authorized=False,outbound_authorized=False))
    summary=dict(state='completed',version=VERSION,status='STAGED',admitted_sources=20,real_embedding_chunks=180,
        embedding_dimensions=1024,record_extensions_stored=49,record_contracts_after=109,source_locations_verified=180,
        http_checks=67,exposed_development_queries=20,topic_source_matches=20,independent_denominator=0,
        human_gold=False,ordinary_publication_gate_passed=False,full_sop_approved=False,generation_enabled=False,
        agent_enabled=False,published=False,activated=False,original_8010_unchanged=True,
        previous_r1_guides_and_all_preexisting_sql_and_vectors_unchanged=True,original_and_business_keys_and_pids_unchanged=True,
        new_business_rows=0,new_domain_relations=0,temporary_keys_created=4,temporary_keys_removed=4,
        qa_requests=0,paid_calls=0,network_preview_server_started=False,live_backend_browser_test=False,
        candidate_seal_sha256=digest(CANDIDATE/'artifact-hashes.json'),receipt_sha256=digest(RECEIPT),
        old_pending_packages_unchanged=True,unit_regression_tests_passed=106,
        schema_adjustment='Only new collection quality capacity 16; approved local_demo preserved',
        next='Separate read-only preview and updated SOP support alignment; no activation or outbound authorization')
    write(REPORT/'summary.json',summary)
    code=('scripts/execute_sop_record_stage.py','scripts/verify_sop_record_stage_http.py','scripts/finalize_sop_record_stage.py')
    write(REPORT/'code-bindings.json',{name:digest(ROOT/name) for name in code})
    page=f'''<!doctype html><html lang="zh-CN"><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>修订指南STAGED实际验收</title><style>body{{font:16px/1.7 system-ui;margin:0;background:#f3f6fb;color:#20314c}}main{{max-width:1000px;margin:auto;padding:24px}}section{{background:white;padding:20px;margin:16px 0;border:1px solid #dce4f1;border-radius:10px}}p,a{{overflow-wrap:anywhere}}</style><main><h1>20份draft-r2修订指南已真实入库 · STAGED</h1>
<section><p>用户明确批准后，已创建新test_sop_records_r2数据库及独立两集合，版本test-sop-records-r2。20来源、20文件done、180个真实1024维归一化BGE向量及位置相符；49新增专题记录及全部请求字段已在真实存储核对，取证合同109组。</p><p>保留准确字节、稳定来源和draft-r2，default ops/admin内部访问；approved_at来自真实聊天回执，reviewed_by为空，不拟造实名审核人员。</p><a href="summary.json">实际结果</a> · <a href="ingestion.json">入库进度与统计</a> · <a href="stored-record-contracts.json">49项真实存储核对</a> · <a href="chat-authorization.json">具体批准依据</a></section>
<section><p>180逐片段原文/位置和20来源权限核对通过；67实际FastAPI处理器HTTP检查、20/20已暴露开发题主题来源匹配并引用再读通过。4临时验证Key结束全部删除，新库Key0。HTTP使用TestClient连接真实新SQL/Milvus，未启动持久网络预览，也未将静态审核浏览器验收当成新库在线浏览器结果。</p><a href="evidence-checks.json">180原文核对</a> · <a href="{http.name}/checks.json">67 HTTP检查</a> · <a href="{http.name}/retrieval.json">20公开开发题</a> · <a href="post-http-verification.json">完成后保护核对</a></section>
<section><p>普通发布门禁未过：纯指南库没有fault_case工单召回分母；SOP0/工单重复检查0不证明完整SOP或去重质量。元数据和脱敏180通过，保留原报告，未修改失败结果或激活版本。独立质量分母0，完整现场SOP、设备阈值、实际取证读数和现场动作授权仍缺。</p><a href="gate-scope.json">实际普通门禁及范围</a></section>
<section><p>原8010、业务及r1指南入口、所有既有SQL/知识权限向量、环境/预算、Key/PID前后不变。生成和Agent关闭、QA/付费0、未合并/发布/切换/新增模型外发。本次20指南仍不在原8010中；下一项可接独立只读预览与新支持性对齐。</p><p>原待审封包、准备验收和原始候选投影维持历史快照，真实批准及新STAGED由回执、账本和本报告证明，不回写旧产物。</p></section></main></html>'''
    (REPORT/'review.html').write_bytes(page.encode('utf-8'))
    seal(REPORT);verify_seal(REPORT);verify_seal(REVIEW);verify_seal(CANDIDATE)
    print(json.dumps(dict(state='sealed',sources=20,chunks=180,contracts=109,http_checks=67,topic_matches=20,
        status='STAGED',ordinary_gate_passed=False,paid_calls=0)))


if __name__=='__main__':main()
