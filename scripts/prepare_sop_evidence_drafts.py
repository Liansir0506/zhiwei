"""Write a separate pending review with twenty concrete observation guides."""
import html
from pathlib import Path

from scripts import stage_content_quality_merge as current
from scripts.prepare_sop_device_support import OUTPUT as PARENT,validate as validate_parent
from scripts.sop_device_support_content import REPAIRS
from scripts.sop_evidence_draft_content import SPECS
from scripts.prepare_legacy_projection import read,write,digest,seal,verify_seal
from qa_core.quality.independent_readiness import extract_records

ROOT=current.ROOT
OUTPUT=ROOT/'data/candidates/sop-evidence-drafts-r1'
EXPOSURE=ROOT/'.runtime/quality/question-intake/development-exposure/sop-evidence-drafts-r1'


def body(entry,spec):
    title,fields,primary,counter=spec
    return '\n'.join([
        '# '+entry['title'], '',
        '项目自编待审取证指南。只核对已有且获准读取的记录；不是完整现场SOP，不提供设备操作命令或合格阈值。',
        '## 适用对象与证据来源',
        f"专题：{entry['equipment']} / {entry['alarm']}；旧家族制式声明：{entry['network_tech']}。实际部署必须另行核对。",
        '填写对象、厂家、型号、软件版本、实际部署及记录读取的授权范围。未知写缺失；制式或对象不符时停止套用。',
        f"旧模板把“{entry['hypothesis']}”写作原因；本指南只将其作为待验证假设。告警名称、单一计数或出现先后本身不能证明原因。",
        '来源是项目封存的支持性审查与自编观察建议，没有新增厂家手册或当前现场数值，不声明外部事实已经确认。',
        '## 第一步：整理具体专题记录',
        *[f'{i}. **{ident}**：{text}。' for i,(ident,text) in enumerate(fields,1)],
        '每项同时保存：原始记录位置与SHA、对象/版本、采集人及授权范围、时间和时区、起止观察窗、单位、采样或日志口径、计数重置点、可比基线。订阅/会话标识须按已有权限脱敏，不采集Key、密码或鉴权材料。',
        '记录状态只能写已记录、缺失、矛盾、不适用。缺失不等于0；不适用须写部署理由并交人复核，不填造监测值。',
        '## 第二步：先核对可比性和关联条件',
        '核对各项是否对应同一对象、匹配软件版本、同一请求/会话/接口或明确对象关系，观察窗是否重叠，单位和采集方法是否可比。不能只凭两项同时出现就建立因果。',
        '身份、部署、记录权限、原始出处或可比性不足时停止选分支，保留缺项与矛盾记录。',
        '## 第三步：支持记录分支（仅供假设复核）',
        *['- '+text+'。' for text in primary],
        f'只有上述关联条件均有匹配原记录，并且相反分支未成立，才交接“{entry["hypothesis"]}待复核”。这不确认根因，也不批准处置。',
        '## 第四步：相反记录分支',
        *['- '+text+'。' for text in counter],
        REPAIRS[entry['hypothesis']][1]+'。',
        '相反记录存在时，停止沿用旧模板原因，交接对应证据和另一复核方向。支持与相反记录同时成立时保留两边材料并停止选择；两边均不支持时结论仍为未知。',
        '## 停止与交接',
        REPAIRS[entry['hypothesis']][2]+'。',
        '出现无权读取、对象/版本不明、部署不符、时间/单位不可比、缺关键记录、记录矛盾、敏感资料越界时，停止分支选择。读取中若发现需新增测量或变更，不继续执行，交值班/对应领域责任角色复核。',
        '交接应列：影响对象和时间、已取得的原始证据、待验证假设、相反证据、缺项/不适用理由、责任角色、工单或变更号、下一次复核条件。没有指定真实人员，责任角色不构成人员认证或审批。',
        '## 现场执行与恢复仍缺的依据',
        '厂家、型号、软件版本适用手册及章节：待提供。设备门限、单位和采集解释：待提供。',
        '逐动作授权与执行者、影响边界、批准变更、可恢复原配置/备份、停止触发、逐动作回退及失败升级方式：待提供。',
        '业务/告警/精度恢复指标、采样方法、真实分母、观察时长与复发条件：待提供；不造统一周期或合格值。',
        '因此本指南只能补取证与交接，完整处置/回退/恢复流程仍未通过。新正文、身份及访问范围全部pending，不沿用旧资料批准或自动发布。',''])


def render(package):
    esc=lambda v:html.escape(str(v),quote=True)
    cards=[]
    for e in package['entries']:
        s=SPECS[e['hypothesis']]
        search=' '.join([e['title'],e['equipment'],e['alarm'],e['hypothesis'],e['network_tech']])
        cards.append(f"<details class='draft' data-search='{esc(search)}'><summary>{esc(e['title'])} · pending</summary>"
            f"<p>对照旧家族10份，仅为审查来源关系，不授权资产/权限合并。正文 <code>{e['body_sha256']}</code></p>"
            f"<p><a href='drafts/{esc(e['filename'])}'>完整取证指南</a></p><h3>具体记录项</h3><ul>"+
            ''.join(f'<li><code>{esc(k)}</code>：{esc(v)}</li>' for k,v in s[1])+'</ul><h3>支持记录</h3><ul>'+
            ''.join(f'<li>{esc(v)}</li>' for v in s[2])+'</ul><h3>相反记录</h3><ul>'+
            ''.join(f'<li>{esc(v)}</li>' for v in s[3])+f"</ul><p>{esc(REPAIRS[e['hypothesis']][2])}；缺审批/回退/恢复依据，不执行处置。</p>"
            f"<details><summary>200旧来源中的本组10份绑定</summary><ul>"+
            ''.join(f"<li>{esc(m['filename'])} — {esc(m['asset_id'])} — <code>{m['file_sha256']}</code></li>" for m in e['parent_members'])+'</ul></details></details>')
    return '''<!doctype html><html lang="zh-CN"><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>SOP取证分支待审正文</title>
<style>body{font:16px/1.65 system-ui;color:#20314c;background:#f3f6fb;margin:0}main{max-width:1060px;padding:24px;margin:auto}section,details.draft{background:white;border:1px solid #dce4f0;border-radius:10px;padding:18px;margin:14px 0}h1{font-size:26px}summary{font-weight:600;cursor:pointer}input{font:inherit;box-sizing:border-box;padding:10px;max-width:100%}a{color:#255bbd}p,li,code{overflow-wrap:anywhere}code{font-size:13px}.warn{color:#914018}@media(max-width:500px){main{padding:12px}section,details.draft{padding:14px}}</style><main>
<h1>SOP取证与条件分支：20份待审正文</h1><section id="scope"><p>覆盖200份旧SOP的20正文家族；新指南pending，准入/完整现场SOP批准均0。</p><p class="warn">只使用已有授权记录，先核对具体证据，再交接支持或相反分支。没有指定厂家型号/软件版本资料，不编造设备门限、动作命令或恢复周期。</p><p>来源是项目自编建议；匹配手册、现场证据和人工语义审核仍缺。LTE家族保留4G声明，不能套用NR/5G。</p><p>独立题集/真值/冻结0；开发预览不验证真实授权、原始内容或根因，不批准操作。</p><p><a href="review-package.json">具体正文/身份/权限提案</a> · <a href="development-preview-cases.json">公开开发预览案例</a> · <a href="parent-bindings.json">200份原件绑定</a></p></section>
<label>筛选专题 <input id="search" type="search" placeholder="如 AMF、LTE、光路"></label><p id="matches"></p>'''+''.join(cards)+'''
<section id="limits"><p>新正文的default租户ops/admin内部范围只是提案，未登记身份、未入库、未STAGED、未发布或授权新增模型外发。原旧库及20家族的旧批准保持。完整处置、停止/回退/恢复依据待补；“有证据”不等于原因确认或动作批准。</p></section></main>
<script>const cards=[...document.querySelectorAll('.draft')];function filter(){const q=document.getElementById('search').value.trim().toLowerCase();let n=0;for(const c of cards){c.hidden=!c.dataset.search.toLowerCase().includes(q);if(!c.hidden)n++}document.getElementById('matches').textContent='匹配 '+n+' / 20 个专题'}document.getElementById('search').addEventListener('input',filter);filter();</script></html>'''


def simulated_packet(spec):
    return dict(authorized=True,object_id='SIM-OBJECT',manufacturer='SIM-MAKER',model='SIM-MODEL',software_version='SIM-R1',
        deployment_matches=True,primary_supported=True,counter_supported=False,
        records={k:dict(state='recorded',object_id='SIM-OBJECT',software_version='SIM-R1',
            source_ref='simulation://'+k,sha256='a'*64,unit='模拟记录',comparable=True,
            window_start='2026-10-14T00:00:00+08:00',window_end='2026-10-14T00:05:00+08:00',
            observed_at='2026-10-14T00:02:00+08:00') for k,_ in spec[1]})


def prepare():
    validate_parent();verify_seal(current.CANDIDATE)
    if OUTPUT.exists() or EXPOSURE.exists() or OUTPUT.resolve()!=OUTPUT or not OUTPUT.is_relative_to(ROOT/'data/candidates'):
        raise ValueError('new separate candidate and exposure paths required')
    audit=read(PARENT/'sop-support-audit.json');assert len(audit['families'])==20 and set(SPECS)==set(REPAIRS)
    OUTPUT.mkdir();(OUTPUT/'drafts').mkdir();entries=[];cases=[];members=[]
    for f in sorted(audit['families'],key=lambda f:f['representative']):
        spec=SPECS[f['condition']];idx=int(f['representative'].split('_')[-1].split('.')[0])
        group=[e for e in audit['entries'] if e['filename'] in f['filenames']]
        assert len(group)==10
        network={e['scope_snapshot']['network_tech'] for e in group};assert len(network)==1
        entry=dict(id=f'SOP-EVIDENCE-{idx:03}',filename=f'guide_sop_evidence_{idx:03}.md',
            title=f"{f['equipment']} {spec[0]}取证与交接指南",equipment=f['equipment'],alarm=f['alarm'],
            hypothesis=f['condition'],network_tech=next(iter(network)),parent_body_sha256=f['body_sha256'],
            parent_members=[{k:e[k] for k in ('filename','asset_id','file_sha256','metadata_sha256')} for e in group],
            review_status='pending',license_status='pending',identity_registered=False,operational_ready=False,
            full_procedure_supported=False,outward_authorized=False,
            source_identity_proposal=dict(source_system='project_authored_sop_evidence',source_id=f'sop-evidence-{idx:03}',
                source_version='draft-r1',asset_id=f'pending-sop-evidence-{idx:03}'),
            access_proposal=dict(tenant_id='default',roles=['ops','admin'],visibility='internal',security_level='internal'),
            doc_type_proposal='guide',manufacturer_document=None,site_values=None)
        path=OUTPUT/'drafts'/entry['filename'];path.write_text(body(entry,spec),encoding='utf-8')
        entry['body_sha256']=digest(path);entries.append(entry);members.extend(entry['parent_members'])
        question=f"针对{entry['equipment']}的{entry['alarm']}，哪些记录支持复核{entry['hypothesis']}，哪些提示相反分支？"
        cases.append(dict(id=entry['id'],question=question,specification_key=entry['hypothesis'],
            packet=simulated_packet(spec),development_exposed=True,human_truth=False,
            source_ids=[entry['source_identity_proposal']['asset_id'],*[m['asset_id'] for m in entry['parent_members']]],
            body_families=[entry['body_sha256'],entry['parent_body_sha256']],topic_groups=[entry['id']],
            template_groups=['sop-evidence-draft-r1-'+entry['parent_body_sha256']]))
    package=dict(protocol='sop-evidence-drafts-review-v1',review_status='pending',entries=entries,
        parent_review_seal_sha256=digest(PARENT/'artifact-hashes.json'),current_seal_sha256=digest(current.CANDIDATE/'artifact-hashes.json'),
        new_admissions=0,ingest_ready=False,staged_created=False,published=False,paid_calls=0,human_gold=False,
        intended_use='isolated_local_demo_only_if_concretely_admitted',external_fact_claims_added=False)
    write(OUTPUT/'parent-bindings.json',members);write(OUTPUT/'review-package.json',package)
    write(OUTPUT/'development-preview-cases.json',dict(cases=cases,simulation_only=True,independent_frozen=0))
    (OUTPUT/'review.html').write_text(render(package),encoding='utf-8');seal(OUTPUT);validate()
    EXPOSURE.mkdir(parents=True)
    dataset=OUTPUT/'development-preview-cases.json'
    write(EXPOSURE/'exposure.json',dict(protocol='development-exposure-overlay-v1',dataset_path=str(dataset),dataset_sha256=digest(dataset),
        source_holdout_preparation_seal=digest(ROOT/'data/candidates/known-corpus-question-holdout-r1/artifact-hashes.json'),
        records=extract_records(read(dataset),str(dataset.relative_to(ROOT)),digest(dataset)),
        all_development_exposed=True,human_gold=False,independent_cases=0))
    seal(EXPOSURE);verify_seal(PARENT);verify_seal(current.CANDIDATE)
    return dict(pending_drafts=20,parent_sources=200,paid_calls=0,report=str(OUTPUT/'review.html'))


def validate():
    verify_seal(OUTPUT);validate_parent();verify_seal(current.CANDIDATE)
    p=read(OUTPUT/'review-package.json')
    assert p['parent_review_seal_sha256']==digest(PARENT/'artifact-hashes.json') and p['current_seal_sha256']==digest(current.CANDIDATE/'artifact-hashes.json')
    assert p['review_status']=='pending' and not p['ingest_ready'] and len(p['entries'])==20
    original=read(PARENT/'sop-support-audit.json');by_name={e['filename']:e for e in original['entries']};seen=[]
    for e in p['entries']:
        assert e['review_status']=='pending' and not e['identity_registered'] and not e['full_procedure_supported']
        path=OUTPUT/'drafts'/e['filename'];assert path.resolve()==path and digest(path)==e['body_sha256']
        assert path.read_text(encoding='utf-8')==body(e,SPECS[e['hypothesis']])
        for m in e['parent_members']:
            assert all(by_name[m['filename']][k]==v for k,v in m.items());seen.append(m['filename'])
    assert len(seen)==len(set(seen))==200
    return True


if __name__=='__main__':
    import json
    print(json.dumps(prepare(),ensure_ascii=True))
