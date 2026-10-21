"""Offline FAQ revision drafts and complete-statement manual chunk proposals."""
from collections import defaultdict
import csv
import difflib
import hashlib
import html
import json
import re

from scripts import original_local_runtime as runtime
from scripts.prepare_legacy_projection import read,write,digest,seal,verify_seal
from qa_core.quality.review_contract import bind_pending,check_approval
from qa_core.indexing.chunkers.paragraph_budget import chunk_manual_supported
from qa_core.indexing.parsers.locations import locate_chunks

ROOT=runtime.PROJECT
OUTPUT=ROOT/'data/candidates/content-quality-review-r1'
BASELINE=ROOT/'.runtime/test/reports/merged-protocol-release-20261010'
RAW=ROOT/'data/candidates/merged-protocol-release-r2/raw'


def normalize_family(answer):
    text=re.sub(r'SIM-FAQ-\d+','SIM-FAQ-<variant>',answer)
    return re.sub(r'验证指标编号为\d+','验证指标编号为<variant>',text)


def build():
    if OUTPUT.exists():raise ValueError('new review output required')
    verify_seal(BASELINE);verify_seal(RAW.parent)
    rows=read(BASELINE/'expected-rows.json');base=runtime.build_profile()
    families=defaultdict(list)
    for row in rows.values():
        if row.get('doc_type')=='faq' and row.get('synthetic'):
            families[normalize_family(row['answer'])].append(row)
    assert len(families)==20 and sum(map(len,families.values()))==1000
    OUTPUT.mkdir(parents=True);entries=[]
    for i,(family,members) in enumerate(sorted(families.items()),1):
        members.sort(key=lambda r:r['source']);sample=members[0]
        topic=re.search(r'再核对(.+?)的时间线',sample['answer']).group(1)
        cause=re.search(r'候选根因是(.+?)，验证指标',sample['answer']).group(1)
        question=f'{topic}时，应先核对哪些上下文和证据？'
        answer=(f'本卡用于模拟情境中的证据核对。出现“{topic}”时，先确认设备标识、软件版本、区域、时间窗口及近期变更，'
                '再记录同一时间线的告警、关联指标和采样时间/单位，区分已观察事实与待验证假设。\n\n'
                f'原模拟资料给出的排查假设是“{cause}”，这不是已确认根因，也不能仅凭告警名称直接采用。'
                '验证时需要实际指标名称、取值、时间、对象和可回查的记录；模拟编号不能代替这些证据。\n\n'
                '缺少对应日志、配置或指标时，应说明缺项并继续收集上下文。本卡仅支持只读信息核对，'
                '不提供重启、配置修改、生产阈值或完整恢复步骤。')
        numbers=sorted({int(n) for r in members for n in re.findall(r'验证指标编号为(\d+)',r['answer'])})
        e=bind_pending(dict(id=f'FAQ-REVISION-{i:02}',question=question,answer=answer,
            origin_type='synthetic',synthetic=True,license_status='pending',identity_registered=False,
            source_identity_proposal=dict(source_system='project_authored_synthetic_revision',source_id=f'faq-family-{i:02}',source_version='draft-r1'),
            access_proposal=dict(tenant_id='default',roles=['ops','admin'],visibility='internal',security_level='internal'),
            origin_row_ids=[r['id'] for r in members],origin_sources=[r['source'] for r in members],
            family_sha256=hashlib.sha256(family.encode()).hexdigest(),observed_variant_count=len(members),
            distinct_original_answers=len({r['answer'] for r in members}),metric_reference_numbers=numbers,
            variant_difference='案例身份和指标引用编号不同；不是字节相同的重复，不能直接删除或按相似度吞并。',
            editorial_change='将原模拟假设与通用取证建议分开；拟以20项主题卡替代新版本中的1000项模板，不修改旧库。'))
        entries.append(e)
    write(OUTPUT/'faq-revision-review.json',dict(entries=entries,approval_status='pending',ingest_ready=False,
        baseline_sha256=digest(BASELINE/'expected-rows.json'),current_rows=1000,proposed_rows=20,
        replacement_is_new_version_only=True,old_versions_preserved=True,publication=False,outbound=False,independent_evaluation=False))
    with (OUTPUT/'faq-revision-draft.csv').open('w',encoding='utf-8',newline='') as f:
        w=csv.DictWriter(f,fieldnames=['faq_id','question','answer']);w.writeheader()
        w.writerows(dict(faq_id=e['id'],question=e['question'],answer=e['answer']) for e in entries)
    from transformers import AutoTokenizer
    tokenizer=AutoTokenizer.from_pretrained(base.embedding_model_path,local_files_only=True)
    sources=sorted({r['source'] for r in rows.values() if r['doc_type']=='manual'})
    proposals=[];blocked=[]
    for source in sources:
        path=RAW/source;old=[r for r in rows.values() if r['doc_type']=='manual' and r['source']==source]
        raw_hash=digest(path);text=path.read_text(encoding='utf-8-sig')
        try:chunks=chunk_manual_supported(text,tokenizer,source=source)
        except ValueError as error:
            blocked.append(dict(source=source,reason=str(error)));continue
        locations=locate_chunks(path,chunks)
        assert all(p is not None and not p['ambiguous'] for p in locations),source
        assert all(len(tokenizer.encode(c.text,add_special_tokens=True))<=450 for c in chunks)
        proposal=dict(source=source,original_sha256=raw_hash,old_chunks=len(old),proposed_chunks=len(chunks),
            old_without_chapter=sum(not r.get('clause') for r in old),
            old_nonterminal_ends=sum(not re.search(r'[。！？.!?][”"\']?$',r['text'].strip()) for r in old),
            original_metadata_unchanged=True,
            chunks=[dict(text=c.text,clause=c.metadata.get('clause',''),location=p,
                         token_count=len(tokenizer.encode(c.text,add_special_tokens=True))) for c,p in zip(chunks,locations)])
        proposals.append(proposal)
        assert digest(path)==raw_hash
    write(OUTPUT/'manual-chunk-proposals.json',dict(sources=proposals,blocked_sources=blocked,
        original_content_unchanged=True,ingested=False,policy='tokenizer-paragraph-v1; complete statements <=450 tokens, oversize units reject',
        limitation='这是原有自编/模拟资料的边界修复，不补造新的规范事实、厂家阈值或完整SOP。'))
    # Keep the frozen host untouched; materialize the exact proposed import diff.
    path=ROOT/'qa_core/application/qa_usecase.py';old=path.read_text(encoding='utf-8')
    previous='from qa_core.retrieval.relevance import relevant_docs'
    assert old.count(previous)==1
    revised=old.replace(previous,'from qa_core.retrieval.supported_evidence import relevant_docs')
    patch=''.join(difflib.unified_diff(old.splitlines(True),revised.splitlines(True),fromfile='a/qa_core/application/qa_usecase.py',tofile='b/qa_core/application/qa_usecase.py'))
    (OUTPUT/'qa-integration.patch').write_text(patch,encoding='utf-8')
    write(OUTPUT/'integration-plan.json',dict(existing_usecase_sha256=digest(path),patch_sha256=digest(OUTPUT/'qa-integration.patch'),
        candidate_module_sha256=digest(ROOT/'qa_core/retrieval/supported_evidence.py'),
        chunk_module_sha256=digest(ROOT/'qa_core/indexing/chunkers/paragraph_budget.py'),
        current_runtime_untouched=True,manual_policy_requires_explicit_rebuild_adapter=True,
        release_binding_update_required=True,publication_authorized=False,outbound_authorized=False))
    esc=lambda x:html.escape(str(x),quote=True)
    cards=[]
    for e in entries:
        cards.append('<article><h2>'+esc(e['question'])+'</h2><p>原组：'+str(e['observed_variant_count'])+'条；不同答案：'+str(e['distinct_original_answers'])+'条；拟保留1项主题卡。</p><div class="answer">'+esc(e['answer'])+'</div><details><summary>实际差异与保留依据</summary><p>'+esc(e['variant_difference'])+'</p><p>原模拟来源：'+esc(', '.join(e['origin_sources']))+'</p><p>指标引用编号：'+esc(e['metric_reference_numbers'])+'</p><p>内容/身份/内部范围/许可pending，未入库或外发。</p></details></article>')
    summary=dict(faq_families=len(entries),original_synthetic_faqs=1000,proposed_faqs=20,manual_sources=len(proposals),
        blocked_manual_sources=len(blocked),old_manual_chunks=sum(p['old_chunks'] for p in proposals),
        proposed_manual_chunks=sum(p['proposed_chunks'] for p in proposals),
        missing_original_chapter=sum(p['old_without_chapter'] for p in proposals),
        old_nonterminal_ends=sum(p['old_nonterminal_ends'] for p in proposals),
        source_content_changed=False,storage_writes=0,paid_calls=0)
    write(OUTPUT/'summary.json',summary)
    page='''<!doctype html><html lang="zh-CN"><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>FAQ与文档支持性修订审核</title><style>body{margin:0;background:#eff3f8;color:#203047;font:16px/1.7 system-ui}main{max-width:1040px;margin:auto;padding:24px}header{padding:18px;background:#fff3ce}article{padding:24px;background:white;border-radius:12px;margin:20px 0}.answer,pre{white-space:pre-wrap;overflow-wrap:anywhere}h2{font-size:21px}@media(max-width:600px){main{padding:12px}article{padding:16px}}</style><main><h1>FAQ与文档支持性：修订审核</h1><header>1000条模拟FAQ有不同案例/指标身份，不能当作相同答案直接删除。本页拟在新版本中用20项自编模拟主题卡替代，旧版本与原记录保留；新正文全部pending。文档只准备完整语句/章节切块，原字节不改。未发布、未外发、没有独立质量结论。</header><h2>分块与来源定位</h2><pre>'''+esc(json.dumps(summary,ensure_ascii=False,indent=2))+'</pre><p>完整原文切块及定位：manual-chunk-proposals.json；超预算原子段落须人工拆分，不静默截断。</p>'+''.join(cards)+'</main></html>'
    (OUTPUT/'review.html').write_text(page,encoding='utf-8')
    seal(OUTPUT);verify_seal(OUTPUT)
    for entry in entries:check_approval(entry)
    print(json.dumps(summary,ensure_ascii=False))


if __name__=='__main__':build()
