"""Prepare the exact r2 decision, never admit or overwrite any knowledge source."""
import html
import json
from scripts.prepare_sop_record_revision import ROOT, OUTPUT as DRAFTS, CANDIDATE as OLD_CANDIDATE
from scripts.prepare_sop_evidence_admission import OUTPUT as PREVIOUS_REVIEW
from scripts.prepare_legacy_projection import read, write, digest, seal, verify_seal
from qa_core.quality.sop_record_revision_review import validate_revision
from qa_core.quality.sop_record_admission import make_plan, receipt_template, TARGET
from scripts.sop_record_stage_profile import profile, STAGE

OUTPUT = ROOT/'data/candidates/sop-record-admission-review-r2'
CODE = ('qa_core/quality/sop_record_admission.py', 'scripts/prepare_sop_record_admission.py',
        'scripts/sop_record_stage_profile.py', 'scripts/build_sop_record_candidate.py')


def expected_plan():
    for source in (DRAFTS, PREVIOUS_REVIEW, OLD_CANDIDATE):
        verify_seal(source)
    package = read(DRAFTS/'review-package.json')
    validate_revision(package, DRAFTS/'drafts', OLD_CANDIDATE/'raw')
    return make_plan(package, read(PREVIOUS_REVIEW/'admission-plan.json'),
        digest(DRAFTS/'artifact-hashes.json'), digest(PREVIOUS_REVIEW/'artifact-hashes.json'))


def load_verified():
    verify_seal(OUTPUT)
    plan = read(OUTPUT/'admission-plan.json')
    if plan != expected_plan() or read(OUTPUT/'code-bindings.json') != {name:digest(ROOT/name) for name in CODE}:
        raise ValueError('prepared r2 source or implementation drift')
    return plan


def render(plan):
    esc = lambda value: html.escape(str(value), quote=True)
    cards = []
    for e in plan['entries']:
        contracts = ''.join('<li>'+esc(x['description'])+'</li>' for x in e['extensions'])
        cards.append(f'<details class="entry"><summary>{esc(e["id"])} · {esc(e["title"])} · 待审</summary>'
            f'<p>来源身份：<code>{esc(json.dumps(e["source_identity"],ensure_ascii=False))}</code></p>'
            '<p>default租户；ops/admin；内部级；role_restricted；无部门附加限制；类型guide。</p>'
            f'<p>旧正文SHA：<code>{e["previous_body_sha256"]}</code></p><p>新正文SHA：<code>{e["body_sha256"]}</code></p>'
            f'<p>整项绑定：<code>{e["approval"]["binding_sha256"]}</code></p><ol>{contracts}</ol>'
            f'<pre>{esc((DRAFTS/"drafts"/e["filename"]).read_bytes().decode("utf-8"))}</pre></details>')
    return '''<!doctype html><html lang="zh-CN"><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>20份修订指南准入审核</title><style>body{font:16px/1.7 system-ui;margin:0;background:#f3f6fb;color:#20314c}main{max-width:1080px;margin:auto;padding:24px}section,details.entry{background:white;border:1px solid #dce4f1;border-radius:10px;padding:18px;margin:16px 0}summary{cursor:pointer;font-weight:600}p,code,a{overflow-wrap:anywhere}pre{white-space:pre-wrap;overflow-wrap:anywhere;font:inherit}h1{font-size:26px}@media(max-width:500px){main{padding:12px}section,details.entry{padding:14px}ol{padding-left:22px}}</style><main><h1>20份draft-r2修订指南 · 具体准入待审</h1>
<section id="scope"><p>本次审核20份完整新正文、draft-r2来源修订、稳定身份及default租户ops/admin内部访问，仅用于新隔离本地训练STAGED候选。</p>
<p>原60组取证项追加49组（11变更、9依赖、10配置、19派生告警），共109组；原段落保留。请求字段没有实际读数、厂家阈值、现场动作或根因结论。</p>
<p id="target">新目标：test-sop-records-r2；MySQL 13306/test_sop_records_r2；Milvus 29530/test_sop_records_r2_doc及test_sop_records_r2_faq。仅20指南、预检180片段；未创建数据库、集合、身份或版本。生成、Agent和缓存关闭，强鉴权，不启动旧Worker。</p>
<p id="limits">原r1指南和8010保持；不迁移旧200 SOP、不合并整库、不发布/激活/切换、不授权DeepSeek外发、完整现场SOP或独立质量。正文中的待审声明保留，后续真实决定另记回执。</p>
<p>旧20份原件的批准只对应draft-r1精确字节，不能覆盖本次新增正文与新目标。以下逐份展示完整新正文和准确身份。</p>
<a href="admission-plan.json">完整绑定计划</a> · <a href="receipt-template.json">空白待定回执</a> · <a href="compatibility.json">候选兼容检查</a></section>'''+''.join(cards)+'</main></html>'


def prepare():
    if OUTPUT.exists():
        raise ValueError('new immutable review directory required')
    plan = expected_plan(); settings = profile()
    preflight = read(DRAFTS/'chunk-preflight.json')
    if (preflight['files'] != 20 or preflight['chunks'] != 180 or not preflight['locations_verified'] or
            not preflight['guide_type_preserved'] or preflight['embeddings_created'] != 0):
        raise ValueError('exact r2 chunk preflight required')
    compatibility = dict(target=TARGET, files=20, preflight_chunks=180, role_scope=['ops','admin'],
        doc_type='guide', source_version='draft-r2', model_downloads=0, embeddings_created=0,
        preflight_seal_sha256=digest(DRAFTS/'artifact-hashes.json'),
        new_profile_validated=True, new_candidate_path=str(STAGE/'candidate'),
        ingestion_adapter='evidence_guide_rebuild', quality='local_demo', new_quality_field_capacity=16,
        required_vector_dimension=1024, old_default_quality_field_unchanged=True,
        worker_started=False, ordinary_gate_required_after_ingestion=True, publication_eligible=False,
        database_created=False, source_identity_registered=False, new_staged=False, paid_calls=0)
    OUTPUT.mkdir()
    write(OUTPUT/'admission-plan.json',plan); write(OUTPUT/'receipt-template.json',receipt_template(plan))
    write(OUTPUT/'compatibility.json',compatibility)
    (OUTPUT/'review.html').write_text(render(plan),encoding='utf-8')
    write(OUTPUT/'code-bindings.json',{name:digest(ROOT/name) for name in CODE})
    seal(OUTPUT); load_verified()
    return dict(prepared=True, pending=20, extensions=49, chunks=180, new_admissions=0, new_staged=0, paid_calls=0)


if __name__ == '__main__':
    print(json.dumps(prepare()))
