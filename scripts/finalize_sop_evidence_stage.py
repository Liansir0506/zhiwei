"""Seal actual STAGED admission/ingestion and honest, exposed evidence regression."""
import html
import json

from scripts.execute_sop_evidence_stage import REPORT,VERSION,RECEIPT
from scripts.build_sop_evidence_candidate import CANDIDATE,validate_inputs
from scripts.prepare_sop_evidence_admission import ROOT,OUTPUT as REVIEW
from scripts.prepare_legacy_projection import read,write,digest,seal,verify_seal


def main():
    if (REPORT/'artifact-hashes.json').exists():raise ValueError('report sealed')
    plan,receipt,metadata,_=validate_inputs(RECEIPT);verify_seal(CANDIDATE);verify_seal(REVIEW)
    stage=read(REPORT/'state.json');assert stage['state']=='completed' and stage['status']=='STAGED'
    assert read(REPORT/'before.json')==read(REPORT/'after.json')
    http_folders=[p for p in REPORT.glob('http-*') if p.is_dir() and read(p/'state.json')['state']=='completed'];assert len(http_folders)==1
    http=http_folders[0];outcome=read(http/'state.json');cleanup=read(http/'key-cleanup.json')
    assert outcome['http_checks']==67 and outcome['retrieval_total']==outcome['retrieval_topic_matches']==20
    assert cleanup==dict(created=4,removed=4,remaining=0,scope='new isolated database only')
    post=read(REPORT/'post-http-verification.json');assert post['passed'] and post['counts']['api_keys']==0
    evidence=read(REPORT/'evidence-checks.json');assert evidence['all_admin_reads']==160 and evidence['ops_guest_foreign_sources']==20
    gates=read(REPORT/'gate-scope.json');assert not gates['ordinary_publication_gate_passed']
    codes=('scripts/execute_sop_evidence_stage.py','scripts/verify_sop_stage_http.py','scripts/finalize_sop_evidence_stage.py')
    write(REPORT/'code-bindings.json',{n:digest(ROOT/n) for n in codes})
    write(REPORT/'chat-authorization.json',dict(user_message=receipt['user_message'],scope=receipt['scope'],
        receipt_sha256=digest(RECEIPT),recorded_at_utc=receipt['recorded_at_utc'],
        question_item_id='["request_user_input_async","call_c459fb65b2b74524afcaf69c4771b25c",0]',
        publication_authorized=False,outbound_authorized=False,independent_quality_approved=False))
    summary=dict(state='completed',version=VERSION,status='STAGED',admitted_sources=20,real_embedding_chunks=160,
        embedding_dimensions=1024,guide_type_preserved=True,source_locations_verified=160,role_boundary_sources=20,
        http_checks=67,development_retrieval_queries=20,development_topic_source_matches=20,
        independent_frozen=0,human_truth_approved=False,quality_metrics_independent=None,
        ordinary_publication_gate_passed=False,full_sop_approved=False,activated=False,published=False,
        generation_enabled=False,agent_enabled=False,new_business_rows=0,new_domain_relations=0,
        actual_receipt_sha256=digest(RECEIPT),candidate_seal_sha256=digest(CANDIDATE/'artifact-hashes.json'),
        pending_parent_review_unchanged=True,original_and_readonly_protected_snapshots_unchanged=True,
        original_keys_and_pids_unchanged=True,temporary_validation_keys_created=4,temporary_validation_keys_removed=4,
        qa_requests=0,paid_calls=0,network_preview_server_started=False,http_attempt=http.name,
        collection_schema_adjustment='New collections only: quality VARCHAR capacity 16 preserves approved local_demo value; frozen schema source unchanged',
        limitations=['Guide-only candidate; not full FAQ/work-order/SOP corpus or complete site SOP',
            'Ordinary publication gate remains failed due to missing fault-case denominator; no activation authorized',
            '20 exposed source-matching queries do not evaluate human answer truth or independent quality',
            'Real HTTP handlers via TestClient; no persistent preview server or browser against this backend',
            'No generation quality or long-term availability assessed'])
    write(REPORT/'summary.json',summary)
    rows=[]
    for e in plan['entries']:
        count=sum(r['source']==e['filename'] for r in read(REPORT/'stored-rows.json'))
        rows.append(f"<tr><td>{html.escape(e['id'])}</td><td>{html.escape(e['title'])}</td><td>{count}</td><td>guide / done</td></tr>")
    page=f'''<!doctype html><html lang="zh-CN"><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>20指南隔离STAGED实际验收</title>
<style>body{{font:16px/1.7 system-ui;background:#f3f6fb;color:#20314c;margin:0}}main{{max-width:1080px;margin:auto;padding:24px}}section{{background:white;padding:20px;margin:16px 0;border-radius:10px}}h1{{font-size:26px}}p,li,a{{overflow-wrap:anywhere}}a{{color:#245cbd}}.scroll{{overflow:auto}}table{{width:100%;min-width:680px;border-collapse:collapse}}td,th{{padding:10px;border-bottom:1px solid #dce5ef}}@media(max-width:500px){{main{{padding:12px}}section{{padding:14px}}}}</style><main>
<h1>20份指南已准入并真实入库：STAGED</h1><section id="result"><p>已根据用户准确回复“{html.escape(receipt['user_message'])}”记录独立批准凭据。新库test_sop_evidence_r1，版本test-sop-evidence-r1，20来源身份、160个真实1024维归一化BGE向量，FAQ0。没有复制旧资料、激活或发布。</p>
<p>20文件账本done、严格来源映射和manifest匹配；160原文/位置重读相符，20来源ops/admin可读、guest及异租户不可读。原8010/ACTIVE、只读库及先前隔离集合原文/向量/权限、环境/预算、原Key/PID前后保持。</p>
<p><a href="summary.json">实际结果与边界</a> · <a href="{html.escape(RECEIPT.as_uri(),quote=True)}">真实聊天批准凭据</a> · <a href="ingestion.json">真实入库账本统计</a> · <a href="evidence-checks.json">160逐片段原文核对</a></p></section>
<section id="http"><h2>实际权限与检索回归</h2><p>67项真实FastAPI HTTP处理器检查通过，数据来自新SQL/Milvus。20/20公开开发题命中对应来源并实际引用再读；目录20指南，STAGED仅admin预览，ops/guest及角色头覆盖被拒绝，异租户内容隐藏，完整处置流程返回无证据。</p>
<p>4个临时验证Key仅在新库创建，结束全部删除，剩余0。未启动网络预览服务，也未把内存夹具响应或静态页面当成线上浏览器结果。没有QA生成请求或付费模型调用；本地真实嵌入/检索/重排已执行。</p>
<a href="{http.name}/checks.json">67 HTTP结果</a> · <a href="{http.name}/retrieval.json">20公开开发检索及引用</a> · <a href="{http.name}/key-cleanup.json">临时Key清理</a> · <a href="post-http-verification.json">HTTP后保护与STAGED核对</a></section>
<section id="gate"><h2>普通发布门禁仍未通过</h2><p>alarm_recall失败：纯指南库没有fault_case工单分母。SOP完整性显示SOP0、重复工单检查扫描0，均不代表完整流程或实际去重质量通过。元数据160与脱敏160检查通过。保留普通结果，没有跳过或改写门禁，不激活。</p>
<p>完整现场SOP、厂家型号/软件版本/阈值、批准动作与回退/恢复仍缺；独立人工题/真值/冻结0。这20道公开开发题只核对主题来源，不证明答案真值、生成质量、独立指标或长期可用。</p><a href="gate-scope.json">真实普通门禁及适用范围</a></section>
<section><h2>新来源清单</h2><div class="scroll"><table><tr><th>来源</th><th>具体指南</th><th>实际片段</th><th>类型/账本</th></tr>{''.join(rows)}</table></div><p>草稿与原件投影保留生成时pending/未创建数据库的历史快照，实际批准/入库由本报告和外部凭据佐证，不回写封存包。审批人员字段未伪造实名认证；reviewed_by为NULL，准入审计绑定准确聊天凭据。</p>
<p>新集合quality字段容量设为16以保留批准的local_demo文字，旧默认8及冻结schema源码不改，没有截短或修改已审元数据。source/asset身份保留具体批准字符串，名称前缀不代表当前准入状态。</p></section></main></html>'''
    (REPORT/'review.html').write_text(page,encoding='utf-8')
    seal(REPORT);verify_seal(REPORT);verify_seal(CANDIDATE);verify_seal(REVIEW)
    print(json.dumps(dict(state='sealed',sources=20,chunks=160,http_checks=67,topic_source_matches=20,status='STAGED',ordinary_gate_passed=False,paid_calls=0,report=str(REPORT/'review.html'))))


if __name__=='__main__':main()
