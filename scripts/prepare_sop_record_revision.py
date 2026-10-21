"""Prepare exact revision drafts only, leaving admitted STAGED bytes and stores intact."""
import copy
import html
import json
from pathlib import Path
from scripts.sop_record_revision_content import request
from scripts.prepare_sop_support_alignment import ROOT,load_verified
from scripts.prepare_sop_evidence_drafts import OUTPUT as OLD_DRAFTS
from scripts.build_sop_evidence_candidate import CANDIDATE,validate_inputs
from scripts.execute_sop_evidence_stage import RECEIPT
from scripts.prepare_legacy_projection import read,write,digest,seal,verify_seal
from qa_core.indexing.chunkers.sop_evidence_guide import chunk_evidence_guide
from qa_core.indexing.parsers.parsers import parse_dispatch
from qa_core.indexing.parsers.locations import locate_chunks
from qa_core.indexing.parsers.sanitize import sanitize
from scripts.rebuild_kb import normalize_text

OUTPUT=ROOT/'data/candidates/sop-record-revision-r2'
EXPOSURE=ROOT/'.runtime/quality/question-intake/development-exposure/sop-record-revision-r2'
MISSING={'change_records_missing','dependency_records_missing','comparison_missing','alarm_records_missing'}


def revised_body(original,extensions):
    if original.count('## 第二步：先核对可比性和关联条件')!=1:raise ValueError('recognized guide section required')
    newline='\r\n' if '\r\n' in original else '\n'
    lines=[f'{i}. **{item["id"]}**：{item["description"]}' for i,item in enumerate(extensions,4)]
    addition='## 补充专题取证项（修订待审）'+newline+newline.join(lines)+newline
    return original.replace('## 第二步：先核对可比性和关联条件',addition+'## 第二步：先核对可比性和关联条件')


def validate_package(package,raw):
    entries=package['entries'];counts={2:0,4:0,5:0,7:0}
    if len(entries)!=20 or len({e['filename'] for e in entries})!=20:raise ValueError('twenty distinct revision guides required')
    for e in entries:
        path=Path(raw)/e['filename']
        if not path.is_file() or digest(path)!=e['body_sha256'] or e['review_status']!='pending':raise ValueError('exact pending body binding required')
        for x in e['extensions']:
            if x['old_step'] not in counts or len(x['requested_fields'])<4 or not x['subject'] or x['value'] is not None:
                raise ValueError('specific record contract without field values required')
            if x['site_evidence_verified'] is not False or x['action_authorized'] is not False:raise ValueError('record request grants no field truth or action')
            if x['description'] not in path.read_bytes().decode('utf-8'):raise ValueError('extension not present in source')
            counts[x['old_step']]+=1
        if e['source_identity_proposal']['source_version']!='draft-r2' or e['identity_registered'] is not False:
            raise ValueError('new source revision remains unregistered')
    if counts!={2:11,4:9,5:10,7:19}:raise ValueError('exact 49 identified omissions required')
    if any(package.get(k) is not False for k in ('ingest_ready','staged_created','published','outbound_authorized','full_sop_approved')):
        raise ValueError('draft preparation cannot authorize ingestion or field use')
    if package.get('independent_denominator')!=0:raise ValueError('development text is not independent truth')
    return counts


def build():
    matrix=load_verified();plan,_,_,_=validate_inputs(RECEIPT)
    if OUTPUT.exists() or EXPOSURE.exists():raise ValueError('new draft/exposure package only')
    OUTPUT.mkdir(parents=True);(OUTPUT/'drafts').mkdir()
    old={e['filename']:e for e in plan['entries']};entries=[]
    for e in matrix['entries']:
        topic=int(e['id'].split('-')[-1]);missing=[r['old_step'] for r in e['steps'] if r['text_status'] in MISSING]
        extensions=[request(topic,step) for step in missing]
        original=(CANDIDATE/'raw'/e['guide_source']).read_bytes().decode('utf-8')
        body=revised_body(original,extensions);path=OUTPUT/'drafts'/e['guide_source'];path.write_bytes(body.encode('utf-8'))
        identity=copy.deepcopy(old[e['guide_source']]['source_identity']);identity['source_version']='draft-r2'
        entries.append(dict(id=e['id'],filename=e['guide_source'],title=e['title'],equipment=e['equipment'],
            previous_body_sha256=e['guide_sha256'],body_sha256=digest(path),extensions=extensions,
            revision_scope='append concrete record contracts; preserve all old paragraphs',review_status='pending',license_status='pending',
            source_identity_proposal=identity,access_proposal=copy.deepcopy(old[e['guide_source']]['access_proposal']),
            identity_registered=False,operational_ready=False,root_cause_confirmed=False,action_authorized=False,full_sop_approved=False))
    package=dict(protocol='sop-record-revision-r2',entries=entries,record_contracts_before=60,extensions=49,record_contracts_after=109,
        ingest_ready=False,staged_created=False,published=False,outbound_authorized=False,full_sop_approved=False,
        new_admissions=0,independent_denominator=0,human_gold=False,paid_calls=0,
        previous_candidate_seal_sha256=digest(CANDIDATE/'artifact-hashes.json'))
    validate_package(package,OUTPUT/'drafts');write(OUTPUT/'review-package.json',package)
    from scripts.sop_evidence_stage_profile import profile
    from transformers import AutoTokenizer
    tokenizer=AutoTokenizer.from_pretrained(profile().embedding_model_path,local_files_only=True)
    preflight=[]
    for e in entries:
        path=OUTPUT/'drafts'/e['filename'];chunks=chunk_evidence_guide(parse_dispatch('guide',path),tokenizer,source=e['filename'])
        for c in chunks:c.text=normalize_text(sanitize(c.text)[0])
        locations=locate_chunks(path,chunks)
        assert chunks and all(c.doc_type=='guide' for c in chunks)
        assert all(loc and not loc['ambiguous'] for loc in locations)
        assert all(len(tokenizer.encode(c.text,add_special_tokens=True))<=450 and len(c.text.encode())<=7900 for c in chunks)
        preflight.append(dict(id=e['id'],filename=e['filename'],chunks=len(chunks),max_tokens=max(len(tokenizer.encode(c.text,add_special_tokens=True)) for c in chunks),
             body_sha256=digest(path),source_locations=locations))
    write(OUTPUT/'chunk-preflight.json',dict(files=20,chunks=sum(e['chunks'] for e in preflight),sources=preflight,locations_verified=True,
        guide_type_preserved=True,embeddings_created=0,paid_calls=0))
    cards=[];esc=lambda x:html.escape(str(x),quote=True)
    for e in entries:
        cards.append(f'<details class="revision"><summary>{esc(e["title"])} · {len(e["extensions"])}项补充 · 待审</summary>'
            f'<p>旧正文SHA：{e["previous_body_sha256"]}；新正文SHA：{e["body_sha256"]}</p><p>来源修订draft-r2；default租户ops/admin内部范围提案，未登记或入库。</p>'+
            ''.join(f'<section><h3>{esc(x["category"])} · {esc(x["subject"])}</h3><p>{esc(x["description"])}</p></section>' for x in e['extensions'])+
            f'<details><summary>完整修订正文与准确来源身份</summary><p>{esc(json.dumps(e["source_identity_proposal"],ensure_ascii=False))}</p><pre>{esc((OUTPUT/"drafts"/e["filename"]).read_bytes().decode("utf-8"))}</pre></details></details>')
    page='''<!doctype html><html lang="zh-CN"><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>指南专题记录修订审核</title><style>body{font:16px/1.7 system-ui;color:#20314c;background:#f3f6fb;margin:0}main{max-width:1100px;margin:auto;padding:24px}section,details.revision{background:white;padding:18px;margin:16px 0;border:1px solid #dce4f1;border-radius:10px}p,pre{overflow-wrap:anywhere}pre{white-space:pre-wrap;font:inherit}summary{cursor:pointer;font-weight:600}@media(max-width:500px){main{padding:12px}}</style><main><h1>20指南专题记录修订 · 待审</h1><section id="scope"><p>第一步补齐49项文字取证缺口：11变更、9依赖、10配置、19派生告警关系。取证合同60→109，均没有填造读数、厂家门限或现场授权。</p><p>原20指南正文原字节保留，在独立修订中追加具体记录请求。新正文及draft-r2来源修订尚未准入，没有入库/新STAGED、替换当前指南、发布或模型外发。完整处置/回退/恢复及独立质量仍缺。</p><a href="review-package.json">准确正文/身份/内部范围提案</a> · <a href="chunk-preflight.json">真实本地分块预检</a></section>'''+''.join(cards)+'</main></html>'
    (OUTPUT/'review.html').write_text(page,encoding='utf-8')
    # Reuse already exposed questions only to attach new revision/template lineage.
    dataset=read(OLD_DRAFTS/'development-preview-cases.json')
    by_id={e['id']:e for e in entries}
    for case in dataset['cases']:
        links=case.setdefault('group_links',{});links.setdefault('template_groups',[]).append('sop-record-revision-r2-'+by_id[case['id']]['body_sha256'])
        links.setdefault('body_families',[]).append(by_id[case['id']]['body_sha256'])
    write(OUTPUT/'existing-development-cases.json',dataset)
    write(OUTPUT/'code-bindings.json',{n:digest(ROOT/n) for n in ('scripts/sop_record_revision_content.py','scripts/prepare_sop_record_revision.py')})
    seal(OUTPUT);verify_seal(OUTPUT)
    from qa_core.quality.independent_readiness import extract_records
    EXPOSURE.mkdir(parents=True);path=OUTPUT/'existing-development-cases.json'
    write(EXPOSURE/'exposure.json',dict(protocol='development-exposure-overlay-v1',dataset_path=str(path),dataset_sha256=digest(path),
        source_holdout_preparation_seal=digest(ROOT/'data/candidates/known-corpus-question-holdout-r1/artifact-hashes.json'),
        records=extract_records(dataset,str(path.relative_to(ROOT)),digest(path)),all_development_exposed=True,human_gold=False,independent_cases=0))
    seal(EXPOSURE);load_verified();verify_seal(CANDIDATE)
    print(json.dumps(dict(pending_drafts=20,extensions=49,record_contracts=109,preflight_chunks=sum(e['chunks'] for e in preflight),new_ingestions=0,paid_calls=0)))


if __name__=='__main__':build()
