"""Prepare a concrete twenty-guide decision and real offline chunk/trace preflight."""
import html
import json

from scripts.prepare_sop_evidence_drafts import ROOT,OUTPUT as DRAFTS,validate as validate_drafts
from scripts.prepare_legacy_projection import read,write,digest,seal,verify_seal
from qa_core.quality.sop_admission import make_plan,receipt_template,TARGET
from qa_core.indexing.chunkers.sop_evidence_guide import chunk_evidence_guide

OUTPUT=ROOT/'data/candidates/sop-evidence-admission-review-r1'
REPORT=ROOT/'.runtime/test/reports/sop-evidence-admission-20261014'


def render(plan):
    esc=lambda x:html.escape(str(x),quote=True);cards=[]
    for e in plan['entries']:
        text=(DRAFTS/'drafts'/e['filename']).read_text(encoding='utf-8')
        cards.append(f"<details class='entry'><summary>{esc(e['id'])} · {esc(e['title'])} · pending</summary>"
            f"<p>来源身份：<code>{esc(json.dumps(e['source_identity'],ensure_ascii=False))}</code></p>"
            f"<p>访问：default租户，ops/admin角色，内部级，role_restricted；无部门额外限制。指南guide，仅取证与交接。</p>"
            f"<p>原件字节SHA：<code>{esc(e['body_sha256'])}</code>；完整项绑定：<code>{esc(e['approval']['binding_sha256'])}</code></p>"
            f"<p>原10资产仅作审查关联，不迁移/合并资产或权限。正文中的pending是撰写时状态，后续准入另记凭据，原字节不回写。</p><pre>{esc(text)}</pre></details>")
    return '''<!doctype html><html lang="zh-CN"><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>20份指南具体准入审核</title><style>body{font:16px/1.65 system-ui;background:#f3f6fb;color:#20314c;margin:0}main{max-width:1080px;margin:auto;padding:24px}section,details.entry{background:white;border:1px solid #dde5f0;border-radius:10px;padding:18px;margin:16px 0}summary{cursor:pointer;font-weight:600}pre{white-space:pre-wrap;font:inherit}p,code,a{overflow-wrap:anywhere}a{color:#245ec3}h1{font-size:26px}@media(max-width:500px){main{padding:12px}section,details.entry{padding:14px}}</style><main>
<h1>20份取证指南：具体准入审核</h1><section id="scope"><p>本次只审核新20份原字节正文、来源身份、default租户ops/admin内部范围及项目自编本地演示使用声明。</p>
<p id="target">批准后拟创建全新隔离 STAGED：test-sop-evidence-r1；MySQL test_sop_evidence_r1 / 13306；Milvus 29530的test_sop_evidence_r1_doc、test_sop_evidence_r1_faq。仅20指南，不复制原306资料或旧200 SOP，不替换现有资料。</p>
<p>原8010/ACTIVE和现有只读库不变；不启动旧绑定Worker。新目标生成及Agent关闭、强鉴权、禁止角色头覆盖、禁用缓存；未创建数据库/集合或写入正文。</p>
<p id="limits">不批准完整现场SOP、厂家门限、真实根因、生产动作、独立质量或新DeepSeek外发。未批准发布、激活或切换8010；完整恢复/回退条件仍缺。</p>
<p>下面逐项显示准确身份、访问与完整正文，全部pending。拟准入只限项目自编取证建议在隔离本地演示中使用，不证明外部规范事实或生产适用性。</p>
<p><a href="admission-plan.json">完整目标与20项内容绑定</a> · <a href="receipt-template.json">空白待定凭据</a> · <a href="chunk-preflight.json">实际本地分块与位置核对</a></p></section>'''+''.join(cards)+'''</main></html>'''


def prepare():
    if OUTPUT.exists():raise ValueError('separate new review required')
    validate_drafts();package=read(DRAFTS/'review-package.json')
    plan=make_plan(package,digest(DRAFTS/'artifact-hashes.json'))
    from scripts import content_quality_release_runtime as rt
    rt.install_adapter()
    settings=rt.selected_settings();rt.owned.apply_profile(settings)
    from transformers import AutoTokenizer
    from qa_core.indexing.parsers.parsers import parse_dispatch
    from qa_core.indexing.parsers.sanitize import sanitize
    from qa_core.indexing.parsers.locations import locate_chunks
    from scripts.rebuild_kb import normalize_text
    tokenizer=AutoTokenizer.from_pretrained(settings.embedding_model_path,local_files_only=True)
    rows=[]
    for e in plan['entries']:
        path=DRAFTS/'drafts'/e['filename'];chunks=chunk_evidence_guide(parse_dispatch('guide',path),tokenizer,source=e['filename'])
        for c in chunks:c.text=normalize_text(sanitize(c.text)[0])
        locations=locate_chunks(path,chunks)
        assert chunks and all(c.doc_type=='guide' and c.metadata['doc_type']=='guide' for c in chunks)
        assert all(loc is not None and not loc['ambiguous'] for loc in locations)
        assert all(len(tokenizer.encode(c.text,add_special_tokens=True))<=450 and len(c.text.encode('utf-8'))<=7900 for c in chunks)
        rows.append(dict(id=e['id'],filename=e['filename'],body_sha256=digest(path),
            chunks=[dict(text=c.text,doc_type=c.doc_type,clause=c.metadata['clause'],
                         tokens=len(tokenizer.encode(c.text,add_special_tokens=True)),location=loc) for c,loc in zip(chunks,locations)]))
    OUTPUT.mkdir();write(OUTPUT/'admission-plan.json',plan);write(OUTPUT/'receipt-template.json',receipt_template(plan))
    write(OUTPUT/'chunk-preflight.json',dict(sources=rows,files=20,total_chunks=sum(len(r['chunks']) for r in rows),
        model_downloads=0,llm_calls=0,embeddings_created=0,locations_verified=True,guide_type_preserved=True,
        database_created=False,source_identity_registered=False,source_bytes_unchanged=True))
    (OUTPUT/'review.html').write_text(render(plan),encoding='utf-8');seal(OUTPUT);verify_seal(OUTPUT);validate_drafts()
    return dict(pending=20,chunk_preflight_passed=True,total_chunks=sum(len(r['chunks']) for r in rows),
        new_admissions=0,new_staged=0,paid_calls=0,review=str(OUTPUT/'review.html'))


if __name__=='__main__':print(json.dumps(prepare(),ensure_ascii=True))
