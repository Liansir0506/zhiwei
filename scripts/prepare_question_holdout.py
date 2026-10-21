"""Prepare a separate sealed, empty known-corpus question-holdout handoff.

Offline only: no private question/gold reads, model calls, store writes or freeze.
"""
import html
from pathlib import Path

from qa_core.quality.independent_readiness import digest
from qa_core.quality.question_holdout import POLICY, roster, screen, template
from scripts.prepare_legacy_projection import read, write, seal, verify_seal, digest as file_digest

ROOT = Path(__file__).resolve().parents[1]
PREVIOUS = ROOT/'data/candidates/independent-quality-preparation-r2'
CORPUS = ROOT/'data/candidates/content-quality-merged-r2'
OUTPUT = ROOT/'data/candidates/known-corpus-question-holdout-r1'
UNIT = ROOT/'.runtime/question-holdout-unit-r1.xml'
VERSION = 'local-content-quality-r1'
USER_SCOPE = '已有独立命题和审核人员，沿用现有语料做新问题留出'
USER_ALIASES = '不愿透露，你拟造即可'

HANDOFF = '''# 现有语料的新问题留出：人工作业单

适用范围：local-content-quality-r1、default租户ops只读、本地演示语料。已知来源与主题复用是本次明确范围；这不是来源/正文/主题独立，也不证明设备现场或生产质量。此前来源组隔离策略和封存包保持原样。

## 角色与隐私

用户确认已有独立命题和审核人员，并选择不透露姓名。使用“命题保管人 A”和“真值审核人 B”作为职责占位代号，不编造真人、机构、审核签名、保管凭据或审核完成时间。人工需确认两者是不同实际人员，均未参与相关问答调参；代号不能证明这一点。开发助手不接收未来问句与真值原件。

## A：命题和保管

1. 在开发工作区外保管题集及真值；不得提交到仓库、聊天、公开候选目录或发给开发助手计算哈希。source-roster.json提供306份封存现库资产身份和正文族SHA；source_ids字段使用这里的asset_id精确值，非文件名或可能共用的source_id。只使用这些已准入来源，不把BFD/NETCONF、设备记录单或上传pending启用。
2. 根据原文可支持的范围命题。允许同来源和主题下的新问法，但不是旧FAQ原句、推荐题、旧开发/冻结题的格式变化或同模板改写。模板族必须覆盖同义表达与问题结构，不为绕过曝光筛查改名。人工核查聊天、截图、源码和历史开发接触；曝光登记不是完整清单，低相似度或哈希不同不能证明新题。
3. 问题模板族为分组单位；同组不得一部分给开发、一部分留出。已有曝光模板族整组排除。共同来源、正文或主题本身不传播问句曝光，仍登记以说明结果覆盖边界。缺失模板族/保管信息先退回，不能默认为独立。
4. 独立保管query、truth、evidence三份原件及版本化manifest。问句原始UTF-8 SHA和canonicalization.md所述格式归一SHA同时计算；真值和证据manifest按精确原始字节SHA。公开交接只填写intake-manifest-template.json中的哈希、完整关联键、预期answer/clarify/refuse和ops范围，不放问句/答案/手册正文。不得在此封存目录填表；保管人另存副本，交接只提交元数据。

## B：另一轮人工真值和语义审核

5. 在保管环境核对题目未用于开发，并审核模板族与历史曝光的语义关系。审核每题truth必须包含：预期answer/clarify/refuse；必要结论与可选结论原子；每个必要原子的具体资产、文件SHA、版本、行/片段位置和支持原文；适用条件、缺失前提、应追问项、禁止断言/处置；允许引用及租户角色。必要原子不得由模型裁判自动生成或代替人工。
6. 现有200份SOP缺具体审批、动作、停止/回退/恢复依据。若题目要求完整现场处置，真值应按证据要求澄清/拒绝越界动作，不将模板十步视为可执行安全流程。设备通用取证FAQ不支持型号相关门限或指令；缺匹配手册/现场证据应明确缺口。项目自编/模拟内容只能支持本地演示语料范围。
7. 真值审核记录同时绑定题集、truth、evidence文件SHA，实际审核人分别以A/B代号标识，保存UTC时间、另一人复核与未参与调参声明、保管隔离凭据；只交接receipt SHA和元数据。身份/保管核验由具体可信人工流程完成，离线工具不会认证匿名字符串或签发批准。待实际完成后才能把custody状态声明为submitted，不能填写approved自批。

## 冻结前任务（尚未执行）

8. 运行哈希/模板筛查，再由人工核查完整曝光、语义模板和真值。筛查拒绝已曝光问句、旧模板、重复问句、同问题模板关联传播、历史/pending来源、来源与正文族不一致、角色/版本/绑定篡改。静态通过仍需人工核验，不等于冻结或质量通过。
9. 确定真实题数、模板组数、answer/clarify/refuse分层和来源/正文/主题覆盖；当前这些值为空，技术上限500题不是统计样本数建议。共同来源/模板的相关性需披露，不把每题当独立来源样本。样本量、阈值和缺项规则须在看系统输出前签定。
10. 锁定题集/真值/证据、完整语料/ACL/撤回快照、代码/配置/模型/参数、评分规则SHA、运行次数、超时与报告归属。当前baseline-binding仅离线资料准备，未查询实时ACTIVE/撤回，不是运行快照。版本、权限、来源字节、题集或运行配置变化需要重新绑定，不能沿用旧冻结。
11. 运行方案待定。可先讨论仅检索/路由检查；文档生成质量必须另含真实生成验证，不能以no_llm结果替代。现有预算和历史外发许可不自动覆盖未来私藏题/真值，具体外发内容、次数和费用先形成可审核方案。本包无冻结/运行执行器，无模型外发许可。

## 评分和报告约束

12. 首次运行后由保管审核人逐题对照truth，报告必要结论支持率、必要证据覆盖率、引用可定位/支持率、answer/clarify/refuse预期符合率以及越界动作、权限泄漏；每项明确判定方式、分子/分母及适用题数。必要结论全满足且无禁止断言才算该题通过；引用数量不是答案正确率。分母必须来自已审核并冻结的真实清单；当前quality_metrics=null，冻结题数0。
13. 错答、拒答错误、无答案、超时或服务失败保留在已签定分母和失败记录中；不得看结果后删题、改真值或缩分母。若授权/撤回/绑定变更使运行无效，应停止并报告无效运行，不隐蔽丢弃失败。评分细则、不可判定处理和置信说明须事先签定。
14. 题目/真值/结果一旦用于定位、提示词/阈值/召回修复，整问题模板组转开发；再次独立留出需新的未曝光题组、人工审核和新绑定。共同来源仍为已知语料，不能通过改名再声称来源独立。历史版本报告不冒充当前版本结果。

下一交接：A/B在保管环境完成真实命题与审核，只提供填好的哈希元数据和保管审核凭据的承诺；不要发送问句或答案。待核验后再准备具体冻结和运行方案。本步只完成空白工程流程，未执行独立评测、未补齐厂家资料或完整SOP、未切换8010。
'''


def parents_valid():
    verify_seal(PREVIOUS)
    verify_seal(CORPUS)
    prior = read(PREVIOUS/'baseline-binding.json')
    registry = read(PREVIOUS/'exposure-registry.json')
    if prior['version'] != VERSION or prior['corpus_seal_sha256'] != file_digest(CORPUS/'artifact-hashes.json'):
        raise ValueError('sealed corpus binding changed')
    if prior['metadata_sha256'] != file_digest(CORPUS/'raw/meta.json') or digest(registry['inputs']) != prior['exposure_inputs_sha256']:
        raise ValueError('metadata or exposure roster changed')
    for i in registry['inputs']:
        p = Path(i['path'])
        if p.resolve() != p or file_digest(p) != i['sha256']:
            raise ValueError('known public exposure input drift')
    return registry, prior


def render(sources, result):
    esc = lambda s: html.escape(str(s), quote=True)
    counts = {k: sum(s['doc_type'] == k for s in sources) for k in sorted({s['doc_type'] for s in sources})}
    return '''<!doctype html><html lang="zh-CN"><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>现有语料新问题留出交接</title><style>body{font:16px/1.65 system-ui;background:#f3f6fa;color:#1c2b42;margin:0}main{max-width:1050px;margin:auto;padding:24px}section,details{background:white;border:1px solid #d7dfed;border-radius:10px;padding:18px;margin:14px 0}h1{font-size:26px}code,p,li{overflow-wrap:anywhere}a{color:#315ebd}.warn{color:#a33b16}summary{cursor:pointer;font-weight:650}</style><main>
<h1>现有语料的新问题留出</h1><section id="status"><strong>冻结题数 0 · 尚无真实题集和审核凭据 · 未运行评测</strong>
<p class="warn">允许共享已知来源与主题；本范围不具备来源独立性，不证明现场设备或生产质量。</p>
<p>用户选择沿用现库，由独立命题和另一审核人员准备新问题；姓名不公开。代号“命题保管人 A”“真值审核人 B”是角色占位，人工身份隔离、保管与真值尚未核验。</p>
<p>现库306份封存来源供出题定位；题目只交接哈希承诺，问句与真值由人工在开发工作区外保管。pending资料未准入。</p>
<p id="actions"><a href="human-handoff.md">A/B具体任务单</a> · <a href="intake-manifest-template.json">空白哈希交接模板</a> · <a href="source-roster.json">现库精确资产与正文族</a> · <a href="canonicalization.md">归一算法</a> · <a href="intake-screen.json">空模板阻断</a> · <a href="readiness.json">冻结前条件</a> · <a href="baseline-binding.json">离线绑定</a></p></section>
<section id="policy"><h2>允许与阻断的边界</h2><p>共享现有已准入来源/正文/主题允许；source_ids必须使用清单中的asset_id，正文族必须准确对应。历史来源、pending草稿和旧文件名别名不作为准入依据。</p><p>已曝光问句、旧问题模板、格式重复和同问题模板关联题组阻断。不能靠改名模板或改写旧题取得新留出资格；语义关系仍需人工复核。</p><p>此前source/body/topic/template来源组隔离保持封存，本包是另定范围，没有放宽旧门禁。</p></section>
<section id="roles"><h2>人工交接顺序</h2><ol><li>A在保管环境命题、维护问题模板族和原始证据。</li><li>B逐题核验必要/可选结论、引用位置、条件与澄清/拒答范围，并复核语义曝光。</li><li>只交接哈希元数据和保管审核凭据承诺，校验后再核验实际人员与保管。</li><li>锁定真实分母、评分、配置/权限/撤回和具体运行方案，再进入冻结审批。</li></ol></section>
<details id="limits" open><summary>验收缺口与评测限制</summary><p>尚缺真实题集、真值、审核凭据、语义曝光核验、实时权限/撤回及运行绑定、样本量和评分阈值。代号与64位哈希不代表已审核。指标为null，不显示100%。</p><p>曝光覆盖仍不完整：截图、聊天、任意测试源码和语义改写需人工核查。现有200份SOP尚缺完整安全处置依据；通用设备FAQ不提供型号阈值。不得把十步模板作可执行现场真值。</p><p>没有冻结、运行、生成或自批按钮；本包不授权未来题集向模型外发，原8010未切换。</p></details>
<section><h2>封存现库定位</h2><p>''' + esc(counts) + '''</p><p>本包只读取公开封存资料，新增题目/真值/模型调用0；不改变来源、向量、角色权限和准入。完整A/B任务、评分及失败分母规则见任务单。</p></section></main></html>'''


def prepare():
    if OUTPUT.exists() or OUTPUT.resolve() != OUTPUT:
        raise ValueError('new separate output required; never overwrite sealed artifacts')
    registry, prior = parents_valid()
    sources = roster(registry)
    code = ['qa_core/quality/question_holdout.py', 'scripts/prepare_question_holdout.py']
    baseline = dict(version=VERSION, policy=POLICY,
        prior_preparation_seal_sha256=file_digest(PREVIOUS/'artifact-hashes.json'),
        corpus_seal_sha256=prior['corpus_seal_sha256'], metadata_sha256=prior['metadata_sha256'],
        exposure_registry_sha256=file_digest(PREVIOUS/'exposure-registry.json'),
        source_roster_sha256=digest(sources), code_sha256={p:file_digest(ROOT/p) for p in code},
        scope='offline preparation only; live ACTIVE/ACL/withdrawal/configuration not checked')
    binding = digest(baseline)
    manifest = template(VERSION, binding)
    result = screen(manifest, registry, sources, VERSION, binding)
    OUTPUT.mkdir(parents=True)
    write(OUTPUT/'baseline-binding.json', dict(**baseline,binding_sha256=binding))
    write(OUTPUT/'source-roster.json', sources)
    write(OUTPUT/'intake-manifest-template.json', manifest)
    write(OUTPUT/'intake-screen.json', result)
    write(OUTPUT/'scope-decision.json',dict(user_scope_message=USER_SCOPE, user_alias_preference=USER_ALIASES,
        policy=POLICY, source_independent=False, roles_are_placeholders=True,
        custody_verified=False, truth_approved=False, freeze_authorized=False, run_authorized=False,
        paid_calls_authorized=False, new_data_admitted=False))
    write(OUTPUT/'readiness.json',dict(state='awaiting_human_commitments_and_review',
        actual_case_count=0, verified_question_holdout_frozen=0, source_independent=False,
        quality_metrics=None, proposed_sample_size=None, scoring_thresholds=None,
        requirements=[dict(item=i,state='pending') for i in (
            'distinct_actual_A_B_custody_and_no_development_tuning', 'human_held_new_questions_gold_evidence',
            'complete_question_template_exposure_and_semantic_review', 'human_truth_and_applicability_review',
            'predeclared_sample_groups_denominators_scoring_and_failure_rules',
            'live_corpus_acl_withdrawal_code_config_and_model_binding',
            'concrete_freeze_run_and_outbound_budget_scope')]))
    (OUTPUT/'human-handoff.md').write_text(HANDOFF,encoding='utf-8')
    (OUTPUT/'canonicalization.md').write_bytes((PREVIOUS/'canonicalization.md').read_bytes())
    (OUTPUT/'review.html').write_text(render(sources,result),encoding='utf-8')
    (OUTPUT/'unit-verification.xml').write_bytes(UNIT.read_bytes())
    seal(OUTPUT)
    return validate()


def validate():
    verify_seal(OUTPUT)
    registry, prior = parents_valid()
    b = read(OUTPUT/'baseline-binding.json')
    binding = b.pop('binding_sha256')
    if digest(b) != binding or b['policy'] != POLICY or b['version'] != VERSION:
        raise ValueError('offline policy binding changed')
    if b['prior_preparation_seal_sha256'] != file_digest(PREVIOUS/'artifact-hashes.json') or b['corpus_seal_sha256'] != prior['corpus_seal_sha256'] or b['metadata_sha256'] != prior['metadata_sha256'] or b['exposure_registry_sha256'] != file_digest(PREVIOUS/'exposure-registry.json'):
        raise ValueError('parent, corpus, metadata or registry changed')
    for p, checksum in b['code_sha256'].items():
        if file_digest(ROOT/p) != checksum:
            raise ValueError('preparation code changed')
    sources = read(OUTPUT/'source-roster.json')
    if digest(sources) != b['source_roster_sha256'] or sources != roster(registry):
        raise ValueError('exact corpus roster changed')
    result = screen(read(OUTPUT/'intake-manifest-template.json'),registry,sources,VERSION,binding)
    if result != read(OUTPUT/'intake-screen.json'):
        raise ValueError('screen result changed')
    return dict(seal_valid=True, source_assets=len(sources), policy=POLICY,
        new_questions=0, human_truth_files=0, verified_question_holdout_frozen=0,
        verified_source_independent_frozen=0, quality_metrics=None, model_calls=0)


if __name__ == '__main__':
    import argparse
    import json
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('--validate',action='store_true');a=p.parse_args()
    print(json.dumps(validate() if a.validate else prepare(),ensure_ascii=True))
