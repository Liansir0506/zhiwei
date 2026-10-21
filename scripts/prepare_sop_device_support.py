"""Build a separate sealed offline review; no storage writes, admission or LLM."""
import argparse
import copy
import csv
import html
from pathlib import Path

from qa_core.quality.support_review import audit_sops, verify_quote, hashed
from scripts.sop_device_support_content import CONTRACT, DEVICES, REPAIRS, SOURCES
from scripts.prepare_legacy_projection import read, write, digest, seal, verify_seal
from scripts import stage_content_quality_merge as current

OUTPUT = current.ROOT / 'data/candidates/sop-device-support-review-r2'


def worksheet(spec):
    text = [f"# {spec['title']}（待审项目自编记录单）", '',
            '范围：仅用于已有授权记录的通用取证与交接；不提供操作命令或统一阈值，不是完整现场SOP。',
            '出处：项目自编资料清单；外部来源仅支持文末列出的有限事实。实际站点值全部待提供。', '',
            '## 适用对象与来源', '',
            '填写站点/对象、厂家、型号、软件版本、部署制式/拓扑；未知写“缺失”，未部署字段写“不适用”并记录理由。',
            '每条证据填写采集人/授权范围、原始记录位置及SHA、时间与时区、时间窗、单位、采样方法和可比基线；缺失不是零值。', '',
            '## 专题记录项', '', *['- '+f+'：待提供。' for f in spec['fields']], '',
            '## 条件判断与反例', '', spec['branches'], spec['counter'], '',
            '## 厂家/现场依据缺口', '', *['- '+f+'：待提供匹配版本文档、章节、适用条件与可用授权。' for f in spec['manual']], '',
            '## 停止与交接', '',
            '对象/版本不明、时间或单位不可比、证据矛盾或无权读取时，停止选择处置分支，记录缺项并交对应责任人员复核。',
            '采集仅使用现有且获准读取的记录；新增测量、切换、重启、配置、拆接或电源操作须另有匹配型号的审批方案。',
            '交接记录：待确认假设、相反证据、缺失项、责任人、工单/变更号、下一次复核条件。实际批准动作、停止/回退/恢复判据尚未提供。', '',
            '## 外部有限支持', '']
    for ref in spec['refs']:
        src = SOURCES[ref]
        text.extend([f"- [{src['title']}]({src['url']})；章节：{src['section']}。{src['fact']}{src['boundary']}"])
    if not spec['refs']:
        text.append('没有指定BBU厂家来源；全部记录项为项目自编建议，不声明厂家事实。')
    return '\n'.join(text)+'\n'


def render(package, audit, output):
    esc = lambda x: html.escape(str(x), quote=True)
    blocks = []
    for family in audit['families']:
        rep = next(e for e in audit['entries'] if e['filename']==family['representative'])
        repair = family['repair_specification']
        rows = ''.join(f"<tr><td>{esc(f['role'])}</td><td>{esc(f['status'])}</td><td>{esc(f.get('evidence',{}).get('quote','完整已识别正文中缺少此项'))}</td></tr>" for f in rep['findings'])
        members = ''.join(f"<li><code>{esc(e['filename'])}</code> — {esc(e['file_sha256'])}</li>" for e in audit['entries'] if e['body_sha256']==family['body_sha256'])
        search=' '.join([rep['title'],family['equipment'],family['alarm'],family['condition'],*family['filenames']])
        blocks.append(f"<details class='family' data-search='{esc(search)}'><summary>{esc(rep['title'].lstrip('# '))} · {family['file_count']}份 · 不可执行</summary>"
            f"<p>模板声称原因：{esc(family['condition'])}；没有现场证明。</p><p>应补取证：{esc(repair['observations'])}</p>"
            f"<p>相反分支：{esc(repair['counter_branch'])}</p><p>{esc(repair['action_boundary'])}</p>"
            f"<div class='table'><table><tr><th>支持角色</th><th>实际状态</th><th>原文证据</th></tr>{rows}</table></div>"
            f"<p>正文SHA：<code>{esc(family['body_sha256'])}</code>；同正文不代表同权限，不批准别名。</p>"
            f"<details><summary>逐文件SHA（{family['file_count']}）</summary><ul>{members}</ul></details></details>")
    cards = ''.join(f"<details class='device' data-search='{esc(e['title']+' '+e['faq_id'])}'><summary>{esc(e['title'])} · pending</summary><p>当前FAQ：{esc(e['faq_id'])}，仅支持部分取证。</p>"
                   f"<pre>{esc((output/'drafts'/e['filename']).read_text(encoding='utf-8'))}</pre></details>" for e in package['entries'])
    sources = ''.join(f"<li><a href='{esc(s['url'])}'>{esc(s['title'])}</a>：{esc(s['fact'])} {esc(s['boundary'])}</li>" for s in package['sources'].values())
    return """<!doctype html><html lang='zh-CN'><meta charset='utf-8'><meta name='viewport' content='width=device-width,initial-scale=1'>
<title>SOP与设备支持性审核</title><style>body{font:16px/1.6 system-ui;background:#f3f6fa;color:#17243b;margin:0}main{max-width:1120px;margin:auto;padding:24px}h1{font-size:26px}.notice,details{background:white;border:1px solid #d6dfed;border-radius:10px;padding:16px;margin:14px 0}summary{cursor:pointer;font-weight:650}pre{white-space:pre-wrap;font:inherit}code{overflow-wrap:anywhere}.table{overflow:auto}table{border-collapse:collapse;min-width:650px}td,th{padding:10px;border:1px solid #dce3ee;vertical-align:top}input{font:inherit;padding:8px;max-width:100%;box-sizing:border-box}li{overflow-wrap:anywhere}.warn{color:#a13d19}a{color:#315ccc}</style><main>
<h1>SOP与设备支持性审核 · local-content-quality-r1</h1>
<div class='notice'><strong>200份SOP / 20正文家族 / 8份设备记录单待审</strong><p class='warn'>完整可执行SOP通过0；LTE原文及元数据均为4G，不应套用NR/5G流程。独立人工真值分母0。本页不是生产验收或资料准入决定。</p>
<p>已有8份FAQ正文及权限保持；新记录单pending，不入库、不发布、不发给模型。没有指定厂家手册时，不补造门限或处置命令。</p>
<p><a href='sop-support-audit.json'>逐文件证据JSON</a> · <a href='device-review-package.json'>具体待审正文/身份/范围</a> · <a href='support-contract.md'>完整支持性补齐契约</a> · <a href='device-evidence-request.csv'>待补资料CSV</a> · <a href='development-review-cases.json'>开发反例（非独立评测）</a></p></div>
<label>筛选专题/来源 <input id='search' type='search' placeholder='如 LTE、光功率、SMF'></label><p id='matches'></p><h2>旧SOP逐家族审查与修订规格</h2>
""" + ''.join(blocks) + "<h2>设备证据记录单（8份新草稿）</h2>" + cards + "<h2>有限事实来源</h2><ul>" + sources + """</ul><p>2026-10-13重新核对官方正文；未复制全文/命令/门限表。观测建议属于项目自编推断，来源范围不能扩展为当前现场结论。</p>
</main><script>const all=[...document.querySelectorAll('.family,.device')];function filter(){const q=document.getElementById('search').value.trim().toLowerCase();let n=0;for(const e of all){e.hidden=!e.dataset.search.toLowerCase().includes(q);if(!e.hidden)n++}document.getElementById('matches').textContent='匹配 '+n+' / '+all.length+' 个专题'}document.getElementById('search').addEventListener('input',filter);filter();</script></html>"""


def prepare(output=OUTPUT):
    global OUTPUT
    OUTPUT = Path(output).absolute()
    protected = [current.CANDIDATE, current.REPORT, current.REVIEW,
                 current.ROOT/'data/candidates/triage-faq-review-r1']
    if (OUTPUT.exists() or OUTPUT.resolve()!=OUTPUT or not OUTPUT.is_relative_to(current.ROOT/'data/candidates')
            or any(OUTPUT.is_relative_to(p) or p.is_relative_to(OUTPUT) for p in protected)):
        raise ValueError('new, separate, unlinked candidate review directory required')
    for p in protected:
        verify_seal(p)
    metadata = read(current.CANDIDATE/'raw/meta.json')
    audit = audit_sops(current.CANDIDATE/'raw', metadata, current.VERSION)
    if (audit['summary']['files']!=200 or audit['summary']['body_families']!=20
            or audit['summary']['known_template_files']!=200
            or audit['summary']['network_applicability_conflicts']!=0
            or {f['condition'] for f in audit['families']}!=set(REPAIRS)):
        raise ValueError('expected frozen source roster or template family changed; manual review required')
    for family in audit['families']:
        observations, counter, action = REPAIRS[family['condition']]
        family['repair_specification'] = dict(observations=observations, counter_branch=counter,
            action_boundary=action, status='project_authored_pending_semantic_review',
            missing='匹配型号/软件版本、真实证据、具体批准动作及停止/回退/恢复条件',
            operational_ready=False)
    for e in audit['entries']:
        for finding in e['findings']:
            if 'quote' in finding.get('evidence',{}):
                verify_quote(current.CANDIDATE/'raw'/e['filename'], finding['evidence'])
    faq_rows = {}
    for name, meta in metadata.items():
        if name.startswith('faq_triage_'):
            with (current.CANDIDATE/'raw'/name).open(encoding='utf-8', newline='') as f:
                row = next(csv.DictReader(f))
            faq_rows[row['faq_id']] = dict(filename=name, file_sha256=digest(current.CANDIDATE/'raw'/name),
                                          metadata_sha256=hashed(meta),
                                          question=row['question'], full_procedure_supported=False)
    if set(faq_rows)!={d['faq_id'] for d in DEVICES}:
        raise ValueError('approved bounded triage FAQ identity changed')
    OUTPUT.mkdir(parents=True)
    (OUTPUT/'drafts').mkdir()
    entries = []
    for spec in DEVICES:
        e = copy.deepcopy(spec)
        filename=e['id'].lower()+'.md'
        (OUTPUT/'drafts'/filename).write_text(worksheet(spec),encoding='utf-8')
        e.update(filename=filename, body_sha256=digest(OUTPUT/'drafts'/filename),
                 approval_status='pending', identity_registered=False, full_procedure_supported=False,
                 origin_type='project_authored_evidence_worksheet', license_status='pending',
                 source_identity_proposal=dict(source_system='project_authored_support',source_id=e['id'].lower(),source_version='draft-r1'),
                 access_proposal=dict(tenant_id='default',roles=['ops','admin'],visibility='internal',security_level='internal'),
                 current_faq_binding=faq_rows[e['faq_id']], site_values=None, manufacturer_document=None,
                 intended_use='isolated_local_demo_only_if_concretely_admitted', outward_authorized=False)
        entries.append(e)
    sources={k:{**v,'verified_date':'2026-10-13','verification':'official_body_and_exact_section_checked',
                     'fact_summary_project_authored':True,'current_site_applicability_confirmed':False} for k,v in SOURCES.items()}
    package=dict(protocol='sop-device-support-review-v1', approval_status='pending', ingest_ready=False,
        reviewer_kind='ai_agent', human_semantic_approved=False, independent_evaluation=False,
        version=current.VERSION, base_seal_sha256=digest(current.CANDIDATE/'artifact-hashes.json'),
        source_audit_sha256=None, entries=entries, sources=sources,
        db_writes=0,vector_writes=0,paid_calls=0,published=False)
    write(OUTPUT/'sop-support-audit.json',audit)
    package['source_audit_sha256']=digest(OUTPUT/'sop-support-audit.json')
    write(OUTPUT/'device-review-package.json',package)
    (OUTPUT/'support-contract.md').write_text(CONTRACT,encoding='utf-8')
    with (OUTPUT/'device-evidence-request.csv').open('w',encoding='utf-8-sig',newline='') as f:
        w=csv.DictWriter(f,fieldnames=['topic_id','title','required_document','manufacturer','model','software_version',
             'document_version','section','site_evidence','reviewer','status']);w.writeheader()
        for e in entries:
            for manual in e['manual']:
                w.writerow(dict(topic_id=e['id'],title=e['title'],required_document=manual,status='missing'))
    cases=[dict(id=e['id']+'-boundary', topic_id=e['id'], question=e['counter'],
        required_behavior=e['branches'], split='dev', development_exposed=True, runtime_executed=False,
        human_truth_approved=False, independent_evaluation=False) for e in entries]
    cases.extend(dict(id='SOP-'+f['representative']+'-counter',question=f['repair_specification']['counter_branch'],
        source_body_sha256=f['body_sha256'],required_behavior='不要按模板原因选择处置；说明缺证据及相反分支',
        split='dev',development_exposed=True,runtime_executed=False,human_truth_approved=False,independent_evaluation=False)
        for f in audit['families'])
    write(OUTPUT/'development-review-cases.json',dict(cases=cases,independent_frozen=0,quality_metrics_publishable=False))
    (OUTPUT/'review.html').write_text(render(package,audit,OUTPUT),encoding='utf-8')
    seal(OUTPUT)
    for p in protected:verify_seal(p)
    return dict(sop_files=200,body_families=20,applicability_conflicts=0,pending_device_worksheets=8,
                complete_procedures_approved=0,paid_calls=0,db_writes=0,output=str(OUTPUT))


def validate(output=OUTPUT):
    output=Path(output)
    verify_seal(output);verify_seal(current.CANDIDATE)
    package=read(output/'device-review-package.json');audit=read(output/'sop-support-audit.json')
    if (package['approval_status']!='pending' or package['ingest_ready'] or len(package['entries'])!=8
            or package['base_seal_sha256']!=digest(current.CANDIDATE/'artifact-hashes.json')
            or package['source_audit_sha256']!=digest(output/'sop-support-audit.json')):
        raise ValueError('pending review binding changed')
    fresh=audit_sops(current.CANDIDATE/'raw',read(current.CANDIDATE/'raw/meta.json'),current.VERSION)
    for old, new in zip(audit['entries'],fresh['entries']):
        if old!=new:raise ValueError('source audit decision drift')
    if len(audit['entries'])!=len(fresh['entries']):raise ValueError('source roster drift')
    for e in package['entries']:
        if (e['approval_status']!='pending' or e['identity_registered'] or e['outward_authorized']
                or e['body_sha256']!=digest(output/'drafts'/e['filename']) or e['full_procedure_supported']):
            raise ValueError('unapproved content or source binding changed')
    return dict(pending=True,source_bindings_valid=True,approved_complete_procedures=0)


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__);parser.add_argument('--validate',action='store_true')
    args=parser.parse_args()
    print(__import__('json').dumps(validate() if args.validate else prepare(),ensure_ascii=True))
