"""Prepare an empty human holdout intake and known-development exposure registry.

No new questions/truth are authored; no admission, freezing, model or store writes.
Only already-public project evaluation material is read. Private runtime credentials,
conversation stores, provider logs and external human-held test files are not read.
"""
import csv
import html
import json
from pathlib import Path
from qa_core.quality.independent_readiness import extract_records,inventory,sha,digest,intake_template,screen_intake
from scripts.audit_holdout_readiness import canonical_query
from scripts.prepare_legacy_projection import read,write,seal,digest as file_digest,verify_seal
from scripts import stage_content_quality_merge as current

ROOT=current.ROOT
OUTPUT=ROOT/'data/candidates/independent-quality-preparation-r2'
LEGACY=Path('F:/heima/myproject/eval_sets/v4-r3-tiered.json')
PREVIOUS=ROOT/'data/candidates/sop-device-support-review-r2'
SOP_REPORT=ROOT/'.runtime/test/reports/sop-device-support-step2-20261013'

WORKFLOW='''# 独立质量人工交接流程（准备态）

当前独立冻结分母0。本次产物是已知曝光登记和空白交接模板，没有新增问题/真值，没有执行评测。

1. 先确定评测范围：本模板采用source/body/topic/template连通组隔离。现有开发组不得拆分进frozen。现库来源已公开给开发且排除于本策略；若将来只做“已知语料的新问题留出”，须另定明确范围，不能称来源独立。本策略不自行放宽。
2. 来源先具体准入：新资料的正文、身份、许可、适用条件、租户/角色、撤回与版本应先审核并进入隔离候选。BFD/NETCONF、Lab与新设备记录单等pending不能因“测试需要”被启用。新资料不代表独立题已具备。
3. 独立命题与真值：由未参与相关开发调参的人保管问句与答案。在开发工作区之外保存原件和原始证据，不提交给开发助手查看/改写。人工审核必要/可选原文证据、适用条件、澄清/拒答/权限负例，记录出题者、另一审核者及具体审核凭据。项目自编示例和LLM评分不能替代人工真值。
4. 公开交接只给承诺：为每题填写问句原始及格式归一化SHA、真值SHA、证据manifest SHA、完整source/body/topic/template键、角色范围和审核保管凭据。问句归一算法在canonicalization.md列明。空字段、哈希、未暴露声明和人工身份字符串均不能单独证明独立性。
5. 先筛再人工核验：离线筛查拒绝已曝光问句、声明关联及传递关联；格式差异被规范化，语义改写仍须独立人员复核。来源/正文/主题/模板键不得为了绕过筛查重新命名。截图、聊天、测试源码和未枚举别名仍是覆盖缺口，不能由低相似度追认未暴露。
6. 冻结前锁定：题集原件、真值、证据、语料版本/权限/撤回状态、参数和代码/配置应有完整SHA与独立保管凭据；明确分母、必要证据评分规则、运行次数、预算及报告归属。实际冻结和运行需要具体方案与授权，本准备流程没有执行器。
7. 最后运行与报告：隔离执行，禁止调参流程读取保管真值，记录每题实际结果与缺项。不将FAQ原句回归、五项结构门禁或零题算成独立质量。没有审核分母时指标为null而不是100%。模型外发/费用仍按具体授权范围，当前为0次。
8. 一次曝光后降级：若题目、真值或同组来源被用于定位、修复、挑选阈值/提示词，整组转dev并记时间/原因；不把同题改写当新frozen。不覆盖旧封存结果，新代码/语料运行需新绑定。未知曝光先阻断。

本页和离线JSON仅验证文件完整性及声明链接；不鉴别人类审核者、不核验真实保管隔离、语义独立或签发准入/冻结许可。需要人类下一步提供新来源与独立命题/审核人员，目前不要求提前付费或切换知识版本。
'''


def source_catalog():
    result=[];metadata=read(current.CANDIDATE/'raw/meta.json');seen=set()
    for name,meta in sorted(metadata.items()):
        p=current.CANDIDATE/'raw'/name
        text=p.read_text(encoding='utf-8-sig')
        # A heading with a sequence number must not make identical bodies independent.
        body='\n'.join(text.splitlines()[1:]) if text.startswith('# ') else text
        aliases=sorted({str(v) for v in [name,meta.get('source_id'),meta.get('asset_id')] if v})
        seen.add(p)
        result.append(dict(filename=name,path=str(p),is_current=True,asset_id=meta.get('asset_id'),source_id=meta.get('source_id'),
            source_version=meta.get('source_version'),doc_type=meta.get('doc_type'),
            file_sha256=file_digest(p),canonical_body_sha256=sha(canonical_query(body)),
            metadata_sha256=digest(meta),blocking_aliases=aliases,
            disposition='current_public_developer_accessible_corpus_excluded_by_source_group_policy'))
    # Source-only pending cards have no questions yet, but their public factual
    # bodies still cannot be silently reused as independent sources.
    for p in sorted((ROOT/'data/candidates').rglob('*')):
        if p.suffix.lower() not in {'.md','.csv'} or p.parent.name not in {'raw','drafts'} or p in seen:continue
        if p.resolve()!=p or p.stat().st_size>32*1024*1024:raise ValueError('unsafe public source path or size')
        meta_path=p.parent/'meta.json';meta=read(meta_path).get(p.name,{}) if meta_path.exists() else {}
        aliases={p.name,p.relative_to(ROOT).as_posix()}
        aliases.update(str(meta[k]) for k in ('source_id','asset_id') if meta.get(k))
        source_review=p.parent.parent/'source-review.json'
        if source_review.exists():
            for s in read(source_review).get('sources',[]):
                if s.get('filename')==p.name and s.get('source_id_proposal'):aliases.add(s['source_id_proposal'])
        text=p.read_text(encoding='utf-8-sig');body='\n'.join(text.splitlines()[1:]) if text.startswith('# ') else text
        result.append(dict(filename=p.name,path=str(p),is_current=False,file_sha256=file_digest(p),
            canonical_body_sha256=sha(canonical_query(body)),metadata_sha256=digest(meta),
            blocking_aliases=sorted(aliases),disposition='public_historical_or_pending_source_only_not_admitted_by_this_audit'))
    return result


def input_paths():
    paths=[]
    for root in [ROOT/'eval_sets',ROOT/'data/candidates',ROOT/'.runtime/evaluations/plans',ROOT/'.runtime/evaluations/reports']:
        if root.exists():
            for p in root.rglob('*'):
                if p.suffix.lower() not in {'.json','.csv'} or p.name=='artifact-hashes.json':continue
                if p.is_relative_to(OUTPUT):continue
                if p.resolve()!=p or not p.resolve().is_relative_to(root.resolve()):
                    raise ValueError('public evaluation input contains a link or escaped path')
                if p.stat().st_size>32*1024*1024:raise ValueError('public evaluation input exceeds bound')
                paths.append(p)
    if not LEGACY.is_file():raise ValueError('explicit historical primary-repo evaluation file unavailable')
    paths.append(LEGACY)
    # This coverage file contains the user-visible recommended questions and known gaps.
    paths.append(ROOT/'.runtime/test/reports/page-p5-20261010/coverage.json')
    return sorted(set(paths),key=str)


def collect():
    records=[];inputs=[]
    for p in input_paths():
        ident=p.relative_to(ROOT).as_posix() if p.is_relative_to(ROOT) else 'primary-repo/eval_sets/'+p.name
        checksum=file_digest(p)
        if p.suffix=='.csv':
            with p.open(encoding='utf-8-sig',newline='') as f:payload=list(csv.DictReader(f))
        else:payload=json.loads(p.read_text(encoding='utf-8-sig'))
        found=extract_records(payload,ident,checksum)
        inputs.append(dict(id=ident,path=str(p),sha256=checksum,query_occurrences=len(found),
            classification='already_public_developer_material',future_private_holdout_read=False))
        records.extend(found)
    sources=source_catalog()
    seen={i['path'] for i in inputs}
    for source in sources:
        if source['path'] not in seen:
            p=Path(source['path']);inputs.append(dict(id=p.relative_to(ROOT).as_posix(),path=str(p),
                sha256=source['file_sha256'],query_occurrences=0,classification='public_source_body_hash_only',
                future_private_holdout_read=False));seen.add(source['path'])
    inputs.sort(key=lambda i:i['id'])
    return inventory(records,inputs,sources,coverage_notes=[
        '输入清单覆盖现有eval_sets、公开候选JSON/CSV、受控回归计划/报告及推荐覆盖检查；未知文件不推断为未曝光。',
        '旧frozen标签和未暴露字段不追认：公开开发文件中题目一律排除。输入次数不是唯一题数，也不是质量分母。',
        '上一步r1/r2各28道反例重复保留，按原文件SHA登记；归一重复不增加独立题数。',
        '当前306份来源均按本策略排除；来源别名映射只作阻断，不能据此追认语义/权限等价。',
        '已公开的历史/pending原件与新设备草稿正文另登记阻断；包括只有来源卡而没有问题的BFD/NETCONF，未作准入。',
        '截图、聊天记录、任意测试源码字面量/会话存储和语义改写未完整枚举；人工曝光核查仍必需。',
        '没有读取任何未来人工私藏题集或真值；该清单不认证保管身份或未暴露声明。'])


def render(registry,screen):
    esc=lambda v:html.escape(str(v),quote=True);s=registry['summary']
    inputs=''.join(f"<tr><td>{esc(i['id'])}</td><td>{i['query_occurrences']}</td><td><code>{esc(i['sha256'])}</code></td></tr>" for i in registry['inputs'] if i['query_occurrences'])
    duplicates=''.join(f"<li>{d['occurrences']}次；归一句SHA <code>{d['canonical_query_sha256']}</code></li>" for d in registry['duplicate_examples'])
    groups=''.join(f"<tr><td>{g['record_count']}</td><td>{esc(', '.join(g['declared_splits']))}</td><td>开发关联组；不得拆进冻结</td><td><code>{g['id']}</code></td></tr>" for g in sorted(registry['groups'],key=lambda g:-g['record_count'])[:20])
    notes=''.join('<li>'+esc(n)+'</li>' for n in registry['coverage_notes'])
    return f"""<!doctype html><html lang='zh-CN'><meta charset='utf-8'><meta name='viewport' content='width=device-width,initial-scale=1'>
<title>独立质量准备与曝光登记</title><style>body{{font:16px/1.65 system-ui;color:#1c2b42;background:#f3f6fa;margin:0}}main{{max-width:1100px;margin:auto;padding:24px}}section,details{{background:white;border:1px solid #d7dfed;border-radius:10px;padding:18px;margin:14px 0}}h1{{font-size:26px}}.warn{{color:#a33b16}}code,td,li{{overflow-wrap:anywhere}}.table{{overflow:auto}}table{{border-collapse:collapse;min-width:620px}}td,th{{border:1px solid #d9e1ee;padding:10px;text-align:left}}a{{color:#315ebd}}summary{{font-weight:650;cursor:pointer}}</style><main>
<h1>独立质量评测：准备态</h1><section id='status'><strong>独立冻结分母 0 · 未执行评测 · 指标暂无</strong>
<p class='warn'>现有题目均属于已曝光或开发可访问资料。旧“frozen”标签、FAQ自回归、结构门禁及新增公开反例不能作为独立结果。</p>
<p>{s['input_files']}份输入；{s['query_occurrences']}条问句出现记录；{s['unique_canonical_queries']}个归一问句；{s['connected_groups']}个声明/归一关联组。出现次数不等于评测分母。</p>
<p>当前{s['current_sources']}份知识来源按来源组隔离策略排除，另登记公开历史/pending原件，总计{s['public_source_occurrences']}份来源出现记录；{s['incomplete_declared_link_occurrences']}条问句记录缺部分声明关联。完整曝光覆盖及语义独立尚未确认。</p>
<p id='actions'><a href='human-handoff.md'>人工保管与真值流程</a> · <a href='intake-manifest-template.json' download>空白交接模板</a> · <a href='case-manifest-schema.json'>每题承诺字段</a> · <a href='canonicalization.md'>问句归一规则</a> · <a href='exposure-registry.json'>完整曝光SHA登记</a> · <a href='intake-screen.json'>真实空模板阻断结果</a></p>
<p>模板没有问句/答案，没有自批或执行按钮。填写哈希不授予冻结、运行、准入、外发或付费许可。</p></section>
<section><h2>下一步需要的人工作业</h2><ol><li>确定新来源、匹配资料及具体准入，旧pending不自动启用。</li><li>独立人员在开发工作区之外命题、保管真值并由另一人核对证据/适用范围。</li><li>仅交接哈希承诺及完整来源/正文/主题/模板键，先筛曝光、再核验语义独立与保管凭据。</li><li>绑定版本/权限/参数/代码和评分分母，具体冻结与运行方案单独审核。</li></ol></section>
<details id='coverage'><summary>覆盖边界与仍缺条件</summary><ul>{notes}</ul><p>当前空模板阻断：没有新人工样本、保管身份/人工真值/语义独立未核验、冻结未授权。</p></details>
<details id='groups'><summary>最大20个关联组（不得跨开发/冻结拆分）</summary><div class='table'><table><tr><th>记录数</th><th>历史标签</th><th>实际去向</th><th>组SHA</th></tr>{groups}</table></div></details>
<details id='duplicates'><summary>归一问句重复示例（仅SHA，不显示未来题目）</summary><ul>{duplicates}</ul></details>
<details id='inputs'><summary>有问句的输入文件与精确SHA</summary><div class='table'><table><tr><th>来源</th><th>出现记录</th><th>原文件SHA</th></tr>{inputs}</table></div></details>
<p>本步资料只用于开发规划。知识版本/权限/原文/向量不变；新题与真值0，模型/付费请求0。没有独立质量通过结论。</p></main></html>"""


def prepare():
    if OUTPUT.exists() or OUTPUT.resolve()!=OUTPUT:raise ValueError('new separate preparation directory required')
    for p in [current.CANDIDATE,current.REPORT,PREVIOUS,SOP_REPORT]:verify_seal(p)
    registry=collect()
    baseline=dict(version=current.VERSION,corpus_seal_sha256=file_digest(current.CANDIDATE/'artifact-hashes.json'),
        metadata_sha256=file_digest(current.CANDIDATE/'raw/meta.json'),
        prior_support_review_sha256=file_digest(PREVIOUS/'artifact-hashes.json'),
        exposure_inputs_sha256=digest(registry['inputs']),scope='offline preparation, not full run/freeze binding')
    binding=digest(baseline);template=intake_template(current.VERSION,binding)
    screen=screen_intake(template,registry,current.VERSION,binding)
    assert screen['state']=='blocked' and screen['verified_independent_frozen']==0
    OUTPUT.mkdir(parents=True)
    write(OUTPUT/'baseline-binding.json',dict(**baseline,binding_sha256=binding))
    write(OUTPUT/'exposure-registry.json',registry)
    write(OUTPUT/'intake-manifest-template.json',template)
    write(OUTPUT/'intake-screen.json',screen)
    write(OUTPUT/'case-manifest-schema.json',dict(purpose='field specification only; no real cases',
        id='human-assigned ID',query_sha256='SHA256 UTF-8 exact question held by custodian',
        canonical_query_sha256='SHA256 of documented canonical_query output',
        truth_sha256='SHA256 of exact human truth bytes held by custodian',
        evidence_manifest_sha256='SHA256 of versioned source/line/required-optional atom evidence file',
        source_ids=['exact source identity, not a renamed development source'],
        body_families=['canonical factual-body digest/family, not a renamed old family'],
        topic_groups=['human-reviewed complete synonym/topic family'],template_groups=['human-reviewed complete question template family'],
        development_exposed=False,expected_kind='answer / clarify / refuse',request_context=dict(tenant_id='default',roles=['ops']),
        note='schema description is not a case; hash/identity strings are unverified declarations'))
    (OUTPUT/'canonicalization.md').write_text('''# 格式归一承诺算法

canonical_query来自scripts.audit_holdout_readiness：先Unicode NFKC，再casefold，删除Unicode分类首字母P/S/Z/C的字符，保留其他字符；将结果UTF-8编码再计算SHA256。原始问句同时保留UTF-8 SHA。空归一结果不能入册。

该算法只发现格式差异，不识别所有同义改写、语义相似、上下文差异或事实族。人工保管人独立计算，开发助手不得读取未来真实题集以代算。哈希声明必须由保管审核流程核验，不能用任意64位字符串自批。真值/证据manifest按精确原始字节SHA绑定，不用这个问句算法。
''',encoding='utf-8')
    (OUTPUT/'human-handoff.md').write_text(WORKFLOW,encoding='utf-8')
    (OUTPUT/'review.html').write_text(render(registry,screen),encoding='utf-8')
    seal(OUTPUT)
    for p in [current.CANDIDATE,current.REPORT,PREVIOUS,SOP_REPORT]:verify_seal(p)
    print(json.dumps(registry['summary'],ensure_ascii=True))


def validate():
    verify_seal(OUTPUT);registry=read(OUTPUT/'exposure-registry.json');binding=read(OUTPUT/'baseline-binding.json')
    if binding['corpus_seal_sha256']!=file_digest(current.CANDIDATE/'artifact-hashes.json'):raise ValueError('corpus binding drift')
    for i in registry['inputs']:
        if file_digest(Path(i['path']))!=i['sha256']:raise ValueError('known exposure input changed; new audit required')
    if digest(registry['inputs'])!=binding['exposure_inputs_sha256']:raise ValueError('exposure input binding drift')
    screen=screen_intake(read(OUTPUT/'intake-manifest-template.json'),registry,current.VERSION,binding['binding_sha256'])
    if screen!=read(OUTPUT/'intake-screen.json'):raise ValueError('intake screen changed')
    return dict(bindings_valid=True,verified_independent_frozen=0,metrics_eligible=False)


if __name__=='__main__':
    import argparse
    parser=argparse.ArgumentParser(description=__doc__);parser.add_argument('--validate',action='store_true');args=parser.parse_args()
    if args.validate:print(json.dumps(validate()))
    else:prepare()
