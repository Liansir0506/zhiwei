"""Offline coverage drafts: no ingestion metadata, DB, embeddings or model calls."""
import argparse
from collections import Counter
import hashlib
import html
import json
from pathlib import Path
import re

ROOT = Path(__file__).resolve().parents[1]
CHECKED = '2026-10-10'
SOURCES = {
    'ng_transport': ('ETSI TS 138 412 V18.0.0 / Release 18', 'https://www.etsi.org/deliver/etsi_ts/138400_138499/138412/18.00.00_60/ts_138412v180000p.pdf', '第7页：SCTP与NGAP目的端口；动态增加的关联可采用其他协商端口。'),
    'ngap': ('ETSI TS 138 413 V18.8.0 / Release 18', 'https://www.etsi.org/deliver/etsi_ts/138400_138499/138413/18.08.00_60/ts_138413v180800p.pdf', '9.2.6.1—9.2.6.3，尤其第180页的NG SETUP FAILURE字段。'),
    'pfcp_port': ('IANA PFCP service registry', 'https://www.iana.org/assignments/service-names-port-numbers?search=8805', 'pfcp / 8805 / udp；8805 tcp为Reserved，不是PFCP。'),
    'pfcp': ('ETSI TS 129 244 V19.3.0 / Release 19', 'https://www.etsi.org/deliver/etsi_ts/129200_129299/129244/19.03.00_60/ts_129244v190300p.pdf', '4.1协议栈与N4范围、4.2.1—4.2.3：UDP，请求目的端口8805，本地分配请求源端口及响应端口关系。'),
    'ngap_port': ('IANA NGAP service registry', 'https://www.iana.org/assignments/service-names-port-numbers?search=38412', 'NGAP SCTP端口注册；不据端口注册推导所有部署规则。'),
    'ue_power': ('ETSI TS 138 101-1 V17.21.0 / Release 17 / FR1 SA', 'https://www.etsi.org/deliver/etsi_ts/138100_138199/13810101/17.21.00_60/ts_13810101v172100p.pdf', '6.2.1功率等级表、6.2.2/6.2.3功率回退、6.2.4配置最大输出功率。草案未抄数值表。'),
    'rrc_a3': ('ETSI TS 138 331 V18.8.0 / Release 18', 'https://www.etsi.org/deliver/etsi_ts/138300_138399/138331/18.08.00_60/ts_138331v180800p.pdf', '5.5.4.4 Event A3，第262页；测量报告条件不等于现网优化处方。'),
    'oran_timing': ('srsRAN Project O-RAN 7.2 RU Guide', 'https://docs.srsran.com/projects/project/en/latest/tutorials/source/oranRU/source/', '教程以GPS同步PTP grandmaster向RU/DU提供时钟；是特定实验架构，不是任意BBU/SPN厂家手册。'),
    'optical': ('Cisco Catalyst 9000 fiber troubleshooting', 'https://www.cisco.com/c/en/us/support/docs/switches/catalyst-9200-switch-stack/222738-troubleshoot-fiber-links-on-catalyst-900.html', 'DOM支持条件、接口状态、收发光功率及模块阈值。限Catalyst 9000，不能当SPN设备通用阈值。'),
    'vswr': ('Anritsu Understanding Cable and Antenna Analysis', 'https://www.anritsu.com/en-us/test-measurement/solutions/en-us/understanding-cable-and-antenna-analysis?tmlink=quick-mega', 'Return Loss/VSWR与DTF章节；匹配指标与故障定位区别，DTF绝对幅度不能替代回损验收。'),
    'interference': ('Anritsu Interference Hunting', 'https://www.anritsu.com/en-in/test-measurement/technologies/interference-hunting', '扫频架构可能漏掉瞬态信号，频谱捕获方式需匹配信号特征；不构成站点干扰根因判断。'),
}


def spec(title, kind, pattern, refs, facts, questions, missing, proposal):
    return dict(title=title, draft_kind=kind, lexical_pattern=pattern, source_refs=refs,
                supported_facts=facts, clarification_questions=questions,
                missing_evidence=missing, proposed_question=proposal)


SPECS = [
    spec('基站传输中断：资料收集草案', 'partial_context', r'传输中断|链路中断|LOS|光功率', ['optical'],
         ['Cisco的光链路排查资料覆盖接口和光模块监测。该来源只能支持光链路只读观察的概念，不能证明当前5G传输中断根因，也不能支持完整SPN处置流程。'],
         ['是前传、中传还是回传？请提供两端设备型号、版本、接口、故障时间和现有告警/计数。'],
         ['匹配设备版本的厂家传输手册、实际拓扑及双端日志', '故障分支、动作前提、授权维护窗口、停止条件、回退和恢复判据'],
         '5G基站传输中断时需要先收集哪些设备和链路资料？'),
    spec('BBU掉电：厂家资料待补', 'blocked_source', r'BBU|掉电|电源故障', [], [],
         ['请提供BBU厂家/型号、软件版本、完整告警码和电源系统型号。'],
         ['对应型号的告警释义与电源维护手册', '由有资格人员提供的授权检查、处置及恢复记录；不能以通用NR切换资料替代'],
         None),
    spec('驻波比：阈值适用条件草案', 'partial_context', r'驻波|VSWR|回波损耗', ['vswr'],
         ['VSWR/回波损耗描述馈线天线系统匹配；是否合格应按天线与站点工程规格判断，不能给所有站点统一处理阈值。', 'DTF用于定位已发现的匹配异常；其绝对幅度受多项测量条件影响，不能直接替代回波损耗验收。'],
         ['频段、天线/馈线型号、厂家验收限值、仪表校准条件及测量参考面是什么？'],
         ['本站点或厂家VSWR验收值与测量条件', '匹配型号的复测/处理流程和验收记录'],
         '判断驻波比是否需要处理前，应核对哪些验收条件？'),
    spec('GPS失锁：同步依赖草案', 'partial_context', r'GPS|GNSS|失锁|同步', ['oran_timing'],
         ['srsRAN O-RAN教程中的GPS参考用于同步PTP grandmaster，再向RU/DU供时钟。影响判断需要先确认实际同步链路；此实验示例不证明任何特定生产站点失锁后的业务后果。'],
         ['GPS是否为当前主参考？使用PTP还是其他同步方式？有无备用参考、保持状态及厂家规定的保持能力？'],
         ['实际同步拓扑、设备版本、主备选择和锁定状态', '厂家保持/失锁告警行为与精度要求；不编造失锁后停站时间'],
         'GPS失锁时应核对哪些同步依赖和设备状态？'),
    spec('乒乓切换：测量报告取证草案', 'partial_context', r'乒乓|切换失败|A3|time.to.trigger', ['rrc_a3'],
         ['NR Event A3描述邻区相对SpCell偏移后的测量事件。可以核对事件配置、滞回及触发时间与测量报告，但标准事件定义并不提供通用的乒乓切换优化参数。'],
         ['提供往返小区、时间窗口、终端轨迹、测量报告和当前事件参数；“乒乓”的统计窗口与计数口径是什么？'],
         ['真实往返切换时序与测量数据、厂家版本化参数说明', '优化方案、审批、原配置快照、回退与效果验证；不能用NR切换失败模板替代'],
         '分析乒乓切换前需要哪些测量报告和配置证据？'),
    spec('SPN光功率：设备阈值待补草案', 'partial_context', r'SPN|光功率|收光|DOM', ['optical'],
         ['Cisco资料展示了DOM收发功率以及模块的高低告警/预警阈值。它只说明应按模块规格核对读数，不能把示例数值作为SPN光功率合格范围。'],
         ['SPN设备和光模块料号、波长、速率、双端收发功率、单位和模块告警阈值是什么？'],
         ['实际SPN设备与光模块规格、接收灵敏度/过载值、链路预算与验收标准'],
         '判断SPN光功率偏低时应提供哪些模块规格和测量值？'),
    spec('时钟同步异常：流程缺口草案', 'partial_context', r'时钟|同步异常|PTP|SyncE', ['oran_timing'],
         ['srsRAN O-RAN实验配置需要检查PTP同步和参考时钟。该资料只能支持同步架构核对，不能形成任意设备的应急切换或恢复流程。'],
         ['设备型号/版本、同步源、PTP profile/域、当前锁定状态和故障时间是什么？'],
         ['匹配现场架构的厂家应急预案与故障分支', '授权、备份/回退、停止条件和同步精度恢复判据；不能仅补“审批/回滚”字样使门禁通过'],
         '时钟同步异常时应先补充哪些设备和同步链路信息？'),
    spec('干扰排查：测量条件草案', 'partial_context', r'干扰|噪声|频谱', ['interference'],
         ['扫频式频谱分析在某一时刻只观察部分频率范围，可能漏掉瞬态信号。采样方式与被测信号时域特征需要匹配；这不是当前干扰类型或来源的证据。'],
         ['受影响频段/小区、时段、上行或下行、频谱/瀑布图、仪表型号和采样设置是什么？'],
         ['可复核的频谱与业务指标、噪声基线和现场观测', '获准的测量流程、人员/区域范围、停止条件与复测验收；不提供擅自关站或追踪人员操作'],
         '开始无线干扰排查前应准备哪些测量证据？'),
    spec('NG Setup Failure：协议事实草案', 'protocol_fact', r'NG.?Setup|NGAP|PLMN|TAI', ['ngap'],
         ['NG SETUP FAILURE由AMF发送到NG-RAN节点；Cause为必选字段，Time to Wait和Criticality Diagnostics为可选字段。', '项目取证建议：区分已收到失败消息与未建成传输关联/响应缺失，读取实际Cause再核对对应配置和日志。“常见原因”没有现场统计支撑时不按频率排序，也不直接归因为AMF注册成功率下降。'],
         ['实际Cause是什么？是否带Time to Wait或Criticality Diagnostics？请提供脱敏消息和双方软件版本。'],
         ['实际失败消息与Cause对应的现场配置/日志；缺失时只能解释协议字段，不能判定根因'],
         'NG SETUP FAILURE应先查看哪些协议字段？'),
    spec('N2/N4：协议与端口草案', 'protocol_fact', r'\bN2\b|\bN4\b|38412|8805|PFCP|NGAP', ['ng_transport', 'ngap_port', 'pfcp', 'pfcp_port'],
         ['5G SA的NG控制面使用NGAP/SCTP，NGAP注册目的端口为38412；TS 38.412允许动态增加的SCTP关联使用38412或协商的其他端口。', 'N4的PFCP注册目的端口是UDP 8805；IANA的8805/TCP为Reserved，不能说PFCP使用TCP 8805。端口说明不等于具体网络的防火墙/部署变更方案。'],
         ['是否为5G SA的N2控制面和SMF—UPF的N4？部署是否使用额外协商关联或代理？'],
         ['如需网络配置方案，仍需部署拓扑、实际监听/关联和对应Release的厂家说明'],
         '5G SA的N2控制面与N4分别使用什么协议和注册端口？'),
    spec('UE最大发射功率：规格范围草案', 'protocol_fact', r'发射功率|功率等级|Power.Class|PCMAX', ['ue_power'],
         ['TS 38.101-1的FR1 SA最大输出功率按频段和UE功率等级规定，配置最大输出功率还受对应功率回退及配置条件影响。不能把一个数值当成所有终端、FR1/FR2和所有配置的最大功率。'],
         ['终端制式、FR1/FR2、频段、UE功率等级、SA/其他配置与测量条件是什么？'],
         ['具体终端能力及对应频段/配置的功率表；FR2需另找对应规范，不能套FR1表'],
         '查询NR FR1 SA终端最大发射功率需要哪些规格条件？'),
]


def read(path):
    return json.loads(path.read_text(encoding='utf-8'))


def write(path, value):
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def validate(output):
    hashes = read(output / 'artifact-hashes.json')
    files = {p.relative_to(output).as_posix() for p in output.rglob('*') if p.is_file() and p.name != 'artifact-hashes.json'}
    if files != set(hashes):
        raise ValueError('artifact roster mismatch')
    for name, expected in hashes.items():
        p = (output / name).resolve()
        if not p.is_relative_to(output.resolve()) or sha(p) != expected:
            raise ValueError('artifact modified')
    package = read(output / 'review-package.json')
    if len(package['entries']) != 11 or package['approval_status'] != 'pending':
        raise ValueError('wrong roster or approval')
    for row in package['entries']:
        if row['approval_status'] != 'pending' or row['license_status'] != 'pending' or row['ingest_ready']:
            raise ValueError('draft cannot be admitted')
        if any(ref not in package['sources'] for ref in row['source_refs']):
            raise ValueError('unknown source')
        if row['draft_kind'] == 'blocked_source' and row['supported_facts']:
            raise ValueError('unsupported fact')
        if not row['missing_evidence'] or not row['scope_exclusions']:
            raise ValueError('missing scope limits')
    return package


def build(output, raw, baseline):
    if output.exists():
        raise ValueError('refuse to overwrite an existing review')
    if output.resolve().is_relative_to(raw.resolve()) or raw.resolve().is_relative_to(output.resolve()):
        raise ValueError('review must be separate from raw sources')
    rows = [r for r in read(baseline)['rows'] if r['group'] == 'old_static']
    if len(rows) != 11 or any(r['status'] != 'NO_EVIDENCE' for r in rows):
        raise ValueError('unexpected P5 baseline')
    files = sorted(p for p in raw.iterdir() if p.suffix in {'.md', '.csv'})
    if len(files) != 276:
        raise ValueError('unexpected local source roster')
    frozen = {p.name: sha(p) for p in files}
    texts = {p.name: p.read_text(encoding='utf-8-sig') for p in files}
    entries = []
    for i, (prior, item) in enumerate(zip(rows, SPECS), 1):
        row = dict(item, candidate_id=f'coverage-draft-{i:02}', original_question=prior['query'],
                   baseline_status=prior['status'], approval_status='pending', license_status='pending',
                   ingest_ready=False, origin_type='public_reference_derived' if item['source_refs'] else 'unresolved',
                   source_revision_proposal='draft-r1', authority_identity_registered=False,
                   scope_exclusions=['不用于生产处置', '不替代厂家版本化手册或站点验收', '不是完整SOP', '未准入，不进入FAQ/推荐/答案'],
                   editorial_note='中文自编摘要；supported_facts中的项目建议为推论，不冒充规范要求。')
        matches = []
        pattern = re.compile(row['lexical_pattern'], re.I)
        for name, text in texts.items():
            lines = [(n, line[:250]) for n, line in enumerate(text.splitlines(), 1) if pattern.search(line)]
            if lines:
                matches.append(dict(filename=name, sha256=frozen[name], matching_physical_lines=len(lines),
                                    examples=[dict(physical_line=n, excerpt=line) for n, line in lines[:2]]))
        row['local_lexical_audit'] = dict(matching_files=len(matches),
            matches=matches, conclusion='仅词面定位，不证明原文支持问题、权限、检索召回或流程完整性；CSV行号不是逻辑记录号。')
        entries.append(row)
    if frozen != {p.name: sha(p) for p in files}:
        raise ValueError('raw sources changed during scan')
    output.mkdir(parents=True)
    sources = {k: dict(title=v[0], url=v[1], locator=v[2], checked_date=CHECKED,
                       verification='官方入口及相关正文已联网核对；动态网页未保存全文',
                       license_status='pending', stored_content='仅来源索引与中文自编草案，无第三方全文') for k, v in SOURCES.items()}
    package = dict(protocol='coverage-source-drafts-v1', created_date=CHECKED, approval_status='pending',
                   purpose='isolated_local_demo_review_only', target_kb_version=None, ingest_ready=False,
                   independent_evaluation=False, baseline_file=str(baseline), baseline_sha256=sha(baseline),
                   local_raw_root=str(raw), local_raw_sha256=frozen, sources=sources, entries=entries,
                   summary=dict(Counter(e['draft_kind'] for e in entries)),
                   next='优先复核N2/N4、NGAP字段、UE规格范围草案；其余收集设备手册/阈值/完整流程。审批后另建绑定来源身份和范围的修订候选，不能直接导入本包。')
    write(output / 'review-package.json', package)
    esc = lambda s: html.escape(str(s), quote=True)
    def lis(items):
        return '<ul>' + ''.join('<li>' + esc(x) + '</li>' for x in items) + '</ul>'
    cards = []
    for row in entries:
        links = ''.join('<li><a href="' + esc(sources[k]['url']) + '" target="_blank" rel="noopener noreferrer">' + esc(sources[k]['title']) + '</a> — ' + esc(sources[k]['locator']) + '</li>' for k in row['source_refs'])
        samples = row['local_lexical_audit']['matches'][:5]
        cards.append('<article><h2>' + esc(row['candidate_id'] + ' ' + row['title']) + '</h2><p>原问题：' + esc(row['original_question']) + '</p><p>类型：' + esc(row['draft_kind']) + '；人工准入/许可：pending；可入库：否</p><h3>可支持的草案事实</h3>' + (lis(row['supported_facts']) if row['supported_facts'] else '<p>无；等待匹配型号的厂家资料。</p>') + '<h3>提问范围建议（未上架）</h3><p>' + esc(row['proposed_question'] or '不创建FAQ') + '</p><h3>需要澄清</h3>' + lis(row['clarification_questions']) + '<h3>仍缺依据</h3>' + lis(row['missing_evidence']) + '<h3>来源与版本</h3><ul>' + links + '</ul><details><summary>本地词面审计：' + str(row['local_lexical_audit']['matching_files']) + '份有相关词，不等于可支持答案</summary><pre>' + esc(json.dumps(samples, ensure_ascii=False, indent=2)) + '</pre></details></article>')
    page = '<!doctype html><html lang="zh-CN"><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>11题资料补齐审核草案</title><style>body{font:16px/1.7 system-ui;margin:0;background:#eef2f6;color:#203047}main{max-width:1040px;margin:auto;padding:24px}article{background:white;padding:24px;border-radius:12px;margin:20px 0}h2{font-size:21px}h3{font-size:17px}pre{white-space:pre-wrap;overflow-wrap:anywhere}a{overflow-wrap:anywhere}header{background:#fff6dc;padding:20px}</style><main><h1>11题资料补齐：审核草案</h1><header>全部pending，尚未准入或入库。3份协议/规格事实草案、7份部分依据/条件收集草案、1份BBU厂家资料缺口。旧11题在当前库仍NO_EVIDENCE；本页不证明已能回答。仅保存自编摘要与官方来源索引，不保存第三方规范全文。事实核对、许可、设备范围和来源身份须分别审核；流程不得靠补安全词过门禁。本页无批准按钮、无外发、无自动写入。</header>' + ''.join(cards) + '</main></html>'
    (output / 'review.html').write_text(page, encoding='utf-8')
    write(output / 'artifact-hashes.json', {p.name: sha(p) for p in output.iterdir() if p.is_file()})
    validate(output)
    print(json.dumps(dict(package=str(output), summary=package['summary'], raw_files_unchanged=len(frozen),
                          approval_status='pending', paid_requests=0, storage_writes=0), ensure_ascii=False))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', type=Path, default=ROOT / 'data/candidates/coverage-source-review-r2')
    parser.add_argument('--raw-root', type=Path, default=Path('F:/heima/myproject/data/raw/v4-foundation-r3'))
    parser.add_argument('--baseline', type=Path, default=ROOT / '.runtime/test/reports/page-p5-20261010/coverage.json')
    parser.add_argument('--validate-only', action='store_true')
    args = parser.parse_args()
    if args.validate_only:
        package = validate(args.output)
        print(json.dumps(dict(valid=True, pending_entries=len(package['entries']), ingest_ready=False)))
    else:
        build(args.output, args.raw_root, args.baseline)


if __name__ == '__main__':
    main()
