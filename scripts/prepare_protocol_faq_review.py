"""Prepare three content-bound FAQ drafts for review, without storage/model access."""
import argparse
import copy
import csv
import html
import io
from pathlib import Path
import uuid

from qa_core.quality.review_contract import bind_pending, check_approval, audit_groups
from scripts.prepare_coverage_source_review import ROOT, read, write, sha, validate as validate_parent
from scripts.prepare_legacy_projection import seal, verify_seal

PARENT = ROOT / 'data/candidates/coverage-source-review-r2'
OUTPUT = ROOT / 'data/candidates/protocol-faq-review-r1'
CONTENT = [
    dict(parent_id='coverage-draft-09', id='DRAFT-NGAP-01', title='NG SETUP FAILURE字段与原因核对',
         question='NG Setup Failure常见原因', refs=['ngap'],
         answer='不能只凭“NG Setup Failure”判定原因。若已收到NG SETUP FAILURE，它由AMF发给NG-RAN节点：Cause必选，Time to Wait及Criticality Diagnostics可选。[1]\n\n先读实际Cause的类别和值。例如Unknown PLMN or SNPN表示AMF未识别NG-RAN提供的PLMN或SNPN；Control processing overload表示控制处理过载。这是原因值释义，不是本次故障根因或出现频率排序。[1]\n\n建议提供脱敏失败消息、双方版本和同一时刻日志，按Cause核对对应配置；若只是SCTP建链失败或超时且没有失败消息，不能声称已收到NG SETUP FAILURE。缺少实际消息时只能解释协议，不能确定原因或给出处置指令。',
         scope='5G NGAP失败消息的解释；TS 38.413 V18.8.0。',
         exclusions=['不以AMF注册成功率下降替代NG SETUP FAILURE原因', '不把没有收到响应当成已收到FAILURE', '不自动重试或重启网元'],
         facts=[('失败消息方向及字段必选/可选性', 'ngap', '9.2.6.3，第180页'),
                ('Unknown PLMN or SNPN与Control processing overload原因值释义', 'ngap', '9.3.1.2，第209及212页')],
         inference='按消息/日志核对原因的建议由项目编写；不声称官方常见原因频率或已确认现场根因。'),
    dict(parent_id='coverage-draft-10', id='DRAFT-N2N4-01', title='5G SA N2/N4协议与目的端口',
         question='N2和N4接口用什么协议端口', refs=['ng_transport', 'pfcp', 'ngap_port', 'pfcp_port'],
         answer='在5G SA场景，N2控制面（NG-RAN—AMF）的NGAP使用SCTP，注册目的端口为38412。AMF动态增加额外SCTP关联时，规范允许38412或动态端口；不能说所有N2关联始终固定38412。[1][3]\n\nN4控制协议PFCP使用UDP。请求目的端口为8805，请求源端口由发送方本地分配；响应目的端口取对应请求的源端口，响应源端口取对应请求的目的端口。因此不能要求PFCP每个报文的源/目的端口都为8805。[2][4]\n\n8805/TCP在IANA中为Reserved，不是PFCP。以上是协议及目的端口说明，实际网络配置还需核对Release、监听地址、额外关联和部署拓扑，不能直接据此生成防火墙变更方案。',
         scope='5G SA的N2控制面和N4控制协议；NG传输Release 18、PFCP Release 19分别核对，不声明统一Release兼容验收。',
         exclusions=['不用于N3用户面或LTE S1协议回答', '不把SCTP当TCP/UDP', '不把注册目的端口当所有报文双端口'],
         facts=[('NGAP/SCTP注册目的端口与额外动态关联例外', 'ng_transport', '第7章，第7页'),
                ('N4协议栈和PFCP请求/响应端口', 'pfcp', '4.1、4.2.1—4.2.3，第26—27页'),
                ('NGAP/PFCP端口注册项', 'ngap_port', 'ngap / 38412 / sctp'),
                ('PFCP UDP项与TCP Reserved项', 'pfcp_port', 'pfcp / 8805 / udp；8805 tcp Reserved')],
         inference='部署配置核对为项目建议；没有提供业务写操作或现场放通规则。'),
    dict(parent_id='coverage-draft-11', id='DRAFT-UEPOWER-01', title='NR FR1 SA终端最大功率与澄清条件',
         question='终端最大发射功率是多少', refs=['ue_power'],
         answer='不能为所有终端给出同一个最大功率。请先确认制式、FR1/FR2、频段、功率等级和单载波/CA/UL-MIMO等配置。[1]\n\n以TS 38.101-1 V17.21.0的NR FR1 SA表6.2.1-1为例：n1的Power Class 3标称最大输出功率为23 dBm，容差±2 dB；n78的Power Class 2表列值为26 dBm，容差+2/-3 dB。这些是指定条件下的功率等级表值，不是任意终端的实际发射功率或始终可达到的功率。[1]\n\n还需核对该UE能力、P-Max、适用占空比条件及MPR/A-MPR等功率回退；配置最大输出功率按6.2.4判断。FR2、载波聚合或其他配置不能直接套用这个单载波示例。条件缺失时先补规格，不根据终端名称猜功率等级。[1]',
         scope='NR FR1 SA、TS 38.101-1 V17.21.0 / Release 17；仅两个已核对表值示例，不覆盖所有组合。',
         exclusions=['不推广为所有UE均23 dBm', '不套用于FR2、LTE或不同载波组合', '不把表值等同现场瞬时实测或无需回退的可配置值'],
         facts=[('n1 PC3和n78 PC2表值与容差', 'ue_power', '表6.2.1-1，第215—216页'),
                ('非默认功率等级的配置条件及功率回退', 'ue_power', '6.2.1—6.2.4，第216—217页及对应条款')],
         inference='先澄清规格为项目问答设计；表值示例不替代终端能力和完整功率条件计算。'),
]

CASE_SPECS = [
    (0, 'NG Setup Failure常见原因', '解释消息字段并索取实际Cause，不确定根因、不排行常见原因'),
    (0, '收到NG SETUP FAILURE，Cause是unknown-PLMN-or-SNPN，代表什么？', '解释原因值，不直接改PLMN或断定配置错误'),
    (0, 'SCTP一直没有连上，是否就是NG SETUP FAILURE？', '区分传输未建链与收到失败消息，不杜撰Cause'),
    (0, 'NG SETUP FAILURE必须带Time to Wait吗？', '说明字段可选，不推导任何重试可自动执行'),
    (1, '5G SA N2和N4协议及注册目的端口是什么？', 'NGAP/SCTP 38412、PFCP/UDP请求目的端口8805，并说明范围'),
    (1, 'PFCP请求源端口一定也是8805吗？响应发往哪里？', '请求源端口本地分配；响应按对应请求端口返回'),
    (1, 'AMF动态增加SCTP关联也只能38412吗？', '说明额外动态关联例外，不套用于任意关联'),
    (1, 'N3、LTE S1或PFCP TCP 8805也能套用这张卡吗？', '拒绝跨接口泛化；说明本卡不覆盖N3/S1，TCP 8805为Reserved'),
    (2, '终端最大发射功率是多少？', '先澄清制式、频段、功率等级和配置；不能直接答所有UE为23 dBm'),
    (2, 'NR FR1 SA n1 PC3的表列最大功率是多少？', '给23 dBm与±2 dB容差，区分标称表值和配置/实际功率'),
    (2, 'n78 PC2是不是永远可以发26 dBm？', '给表列值并说明能力、配置、占空比和回退限制，不能承诺永远达到'),
    (2, 'FR2或CA也可以直接套n1 PC3的23 dBm吗？', '不套用，要求对应规范/组合条件，不外推表值'),
]


def validate(output):
    verify_seal(output)
    package = read(output / 'review-package.json')
    if package['protocol'] != 'protocol-faq-review-v1' or package['status'] != 'pending' or package['ingest_ready']:
        raise ValueError('not a pending review')
    entries = package['entries']
    if len(entries) != 3 or {e['id'] for e in entries} != {e['id'] for e in CONTENT}:
        raise ValueError('wrong FAQ roster')
    with (output / 'faq-draft.csv').open(encoding='utf-8', newline='') as f:
        csv_rows = list(csv.DictReader(f))
    if len(csv_rows) != 3:
        raise ValueError('wrong CSV roster')
    for entry, row in zip(entries, csv_rows):
        check_approval(entry)
        if entry['approval']['status'] != 'pending' or entry['license_status'] != 'pending' or entry['identity_registered']:
            raise ValueError('draft cannot be admitted')
        if row != {'faq_id': entry['id'], 'question': entry['question'], 'answer': entry['answer']}:
            raise ValueError('FAQ body differs from reviewed text')
        if any(ref not in package['sources'] for ref in entry['source_refs']):
            raise ValueError('missing cited source')
        if sha(output / entry['card_file']) != entry['card_sha256']:
            raise ValueError('card text changed')
        import re
        if any(int(n) > len(entry['source_refs']) for n in re.findall(r'\[(\d+)\]', entry['answer'])):
            raise ValueError('citation index out of bounds')
    cases = read(output / 'review-cases.json')['cases']
    audit_groups(cases)
    if len(cases) != 12 or any(c['approval']['status'] != 'pending' or c['split'] != 'dev' for c in cases):
        raise ValueError('development cases cannot become approved or frozen')
    return package


def build(output, parent):
    if output.exists():
        raise ValueError('refuse to overwrite reviewed drafts')
    if output.resolve().is_relative_to(parent.resolve()) or parent.resolve().is_relative_to(output.resolve()):
        raise ValueError('keep parent package separate')
    prior = validate_parent(parent)
    selected = {e['candidate_id']: e for e in prior['entries'] if e['draft_kind'] == 'protocol_fact'}
    if set(selected) != {item['parent_id'] for item in CONTENT}:
        raise ValueError('unexpected parent scope')
    output.mkdir(parents=True)
    entries = []
    for item in CONTENT:
        entry = dict(id=item['id'], title=item['title'], parent_id=item['parent_id'], question=item['question'],
                     original_question=selected[item['parent_id']]['original_question'], answer=item['answer'],
                     source_refs=item['refs'], scope=item['scope'], exclusions=item['exclusions'],
                     fact_support=[dict(fact=f[0], source_ref=f[1], locator=f[2]) for f in item['facts']],
                     editorial_inference=item['inference'], license_status='pending', identity_registered=False,
                     origin_type='public_reference_derived', synthetic=False,
                     source_identity_proposal=dict(source_system='project_authored_reference',
                         source_id='draft-' + str(uuid.uuid5(uuid.NAMESPACE_URL, 'local-demo-protocol-faq/' + item['id'])),
                         source_revision='draft-r1', status='unregistered_proposal',
                         withdrawal='按项目自编卡独立身份撤回；外部依据修订/撤回依赖待人工确认'),
                     access_proposal=dict(tenant_id='default', dept_id='public', visibility='internal',
                         allowed_roles=['ops', 'admin'], security_level='internal', review_status='pending'),
                     missing_admission=['事实与适用范围人工复核', '摘要使用与许可复核', '稳定来源身份/撤回依赖及访问范围复核', '具体目标STAGED与绑定准入凭据'],
                     prohibited_effects=['不得在当前ACTIVE导入或覆盖FAQ', '不得上架推荐', '不得将其他8项或BFD/NETCONF转批准'])
        entry['card_file'] = item['id'].lower() + '.json'
        write(output / entry['card_file'], entry)
        entry['card_sha256'] = sha(output / entry['card_file'])
        entries.append(bind_pending(entry))
    sources = {k: copy.deepcopy(prior['sources'][k]) for entry in entries for k in entry['source_refs']}
    sources['ngap']['locator'] += ' 本轮补核9.3.1.2，第209/212页原因值释义。'
    sources['ue_power']['locator'] = '本轮补核6.2.1表6.2.1-1，第215—216页n1 PC3 / n78 PC2两例及容差；6.2.1配置条件、6.2.2—6.2.4回退。'
    package = dict(protocol='protocol-faq-review-v1', status='pending', ingest_ready=False,
                   purpose='isolated_local_demo_review_only', target_kb_version=None, independent_quality_passed=False,
                   parent_package=str(parent), parent_sha256=sha(parent / 'review-package.json'),
                   preparation='中文自编事实摘要；官方原文及表格未存储，外部API/模型调用0',
                   existing_scope_extended=False, sources=sources, entries=entries,
                   next='对具体3份正文、许可、身份与访问范围人工复核；获准后才另建严格STAGED候选与隔离问答回归。')
    write(output / 'review-package.json', package)
    with (output / 'faq-draft.csv').open('w', encoding='utf-8', newline='') as f:
        writer = csv.DictWriter(f, fieldnames=['faq_id', 'question', 'answer'])
        writer.writeheader()
        writer.writerows({k: e['id' if k == 'faq_id' else k] for k in writer.fieldnames} for e in entries)
    cases = []
    for i, (index, query, behavior) in enumerate(CASE_SPECS, 1):
        e = entries[index]
        cases.append(bind_pending(dict(id=f'PROTOCOL-REVIEW-{i:02}', query=query,
            candidate_id=e['id'], required_response_behavior=behavior,
            source_ids=e['source_refs'], body_families=[e['card_sha256']], topic_groups=[e['id']],
            template_groups=['authored-protocol-faq-v1'], development_exposed=True, split='dev',
            runtime_executed=False, independent_evaluation=False,
            label_basis='作者根据自编卡与规范制定的待审语义检查项，未由模型输出倒推；不是线上状态预测或已通过问答评测')))
    write(output / 'review-cases.json', dict(cases=cases, runtime_executed=False, metrics_generated=False))
    esc = lambda x: html.escape(str(x), quote=True)
    sections = []
    for e in entries:
        links = ''.join('<li>[' + str(i) + '] <a href="' + esc(sources[k]['url']) + '" target="_blank" rel="noopener noreferrer">' + esc(sources[k]['title']) + '</a>：' + esc(sources[k]['locator']) + '</li>' for i, k in enumerate(e['source_refs'], 1))
        rows = ''.join('<tr><td>' + esc(c['query']) + '</td><td>' + esc(c['required_response_behavior']) + '</td></tr>' for c in cases if c['candidate_id'] == e['id'])
        sections.append('<article><h2>' + esc(e['title']) + '</h2><p>FAQ问题：' + esc(e['question']) + '</p><p>范围：' + esc(e['scope']) + '</p><h3>完整答案草案</h3><div class="answer">' + esc(e['answer']) + '</div><h3>引用来源</h3><ol>' + links + '</ol><h3>适用性与反例检查项（未执行问答）</h3><table><tr><th>问题</th><th>预期语义行为</th></tr>' + rows + '</table><h3>项目建议与事实区别</h3><p>' + esc(e['editorial_inference']) + '</p><details><summary>身份/访问范围/许可待审提案</summary><pre>' + esc(str(dict(identity=e['source_identity_proposal'], access=e['access_proposal'], missing=e['missing_admission'], binding=e['approval']['binding_sha256']))) + '</pre></details></article>')
    page = '<!doctype html><html lang="zh-CN"><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>三项FAQ完整答案审核</title><style>body{font:16px/1.7 system-ui;background:#eff3f8;color:#203047;margin:0}main{max-width:1060px;margin:auto;padding:24px}article{background:white;padding:24px;border-radius:12px;margin:20px 0}.answer,pre{white-space:pre-wrap;overflow-wrap:anywhere}header{background:#fff3cf;padding:18px}table{width:100%;border-collapse:collapse;table-layout:fixed}td,th{border:1px solid #ddd;padding:10px;overflow-wrap:anywhere}a{overflow-wrap:anywhere}h2{font-size:23px}h3{font-size:18px}@media(max-width:600px){main{padding:12px}article{padding:16px}}</style><main><h1>三项FAQ：完整答案审核候选</h1><header>全部pending，未入库/上架/外发模型。每项正文、来源身份、访问范围和许可须明确审核；此前256/20份演示资料批准不覆盖新正文。12条为已暴露的待审开发检查项，未运行问答，不能计为质量通过。仅保存中文自编摘要和来源索引。</header>' + ''.join(sections) + '</main></html>'
    (output / 'review.html').write_text(page, encoding='utf-8')
    seal(output)
    validate(output)
    print('Prepared 3 pending FAQ bodies and 12 pending semantic review cases; no DB/model access')


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', type=Path, default=OUTPUT)
    parser.add_argument('--parent', type=Path, default=PARENT)
    parser.add_argument('--validate-only', action='store_true')
    args = parser.parse_args()
    if args.validate_only:
        validate(args.output)
        print('Validated 3 pending FAQ bodies; not admitted or evaluated')
    else:
        build(args.output, args.parent)


if __name__ == '__main__':
    main()
