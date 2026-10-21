"""Eight offline, bounded triage drafts; no admission, storage or model calls."""
import argparse
import copy
import csv
import html
import json
from pathlib import Path

from qa_core.quality.review_contract import bind_pending, check_approval, audit_groups
from scripts.prepare_coverage_source_review import validate as validate_parent
from scripts.prepare_legacy_projection import read, write, digest, seal, verify_seal

ROOT = Path(__file__).resolve().parents[1]
PARENT = ROOT / 'data/candidates/coverage-source-review-r2'
OUTPUT = ROOT / 'data/candidates/triage-faq-review-r1'
CONTENT = [
    dict(id='TRIAGE-TRANSPORT-01', parent_id='coverage-draft-01', title='基站传输中断的双端取证',
         question='5G基站传输中断时，应先收集哪些链路证据？', refs=['optical'],
         facts=['Cisco资料以Catalyst 9000为对象，介绍接口状态、DOM读数与光模块阈值；DOM支持依赖模块和软件版本。不能据此认定SPN设备的阈值或现场根因。'],
         answer='先明确中断发生在前传、中传还是回传，并记录故障起止时间、影响范围、两端设备/软件版本、端口和链路拓扑。仅凭“传输中断”不能判断是光路、设备还是上层配置问题。\n\n项目建议按同一时间窗口收集两端接口运行状态、LOS等实际告警、错误/丢弃计数及变化趋势，并记录近期变更。设备支持DOM时，保存双端收发光功率、单位、模块料号及该模块的告警/预警阈值；不支持DOM或没有权限时标记缺失，不能把未读到数值当成零。Cisco的观察方法仅是特定设备示例。[1]\n\n条件判断：物理链路异常可作为继续核对光路/接口的线索；物理状态正常而业务仍不通，需要匹配部署的上层日志和配置。以上均为排查方向，不能单凭某一读数确认根因。\n\n交接给维护人员时同时提供双端记录和原配置/变更时间线；重启、切换路径、改配置或插拔光纤须另有匹配设备的授权流程。本卡只支持取证，不能替代完整恢复步骤。',
         missing=['现场拓扑与匹配型号的厂家手册', '授权处置、回退与业务恢复验收流程'],
         counter='只有单端LOS告警，是否可以直接断定对端光模块坏了？', behavior='不能确定根因；要求双端状态、模块规格和同期证据。'),
    dict(id='TRIAGE-BBU-01', parent_id='coverage-draft-02', title='BBU掉电告警的交接信息',
         question='BBU掉电告警需要先补充哪些信息？', refs=[], facts=[],
         answer='目前没有指定BBU厂家、型号及版本，无法解释具体告警码、给出电压阈值或指导电源处置。这里提供的是项目编写的信息交接清单，不是厂家掉电维护手册。\n\n先记录完整告警码/原文、首次与最近发生时间、持续或间歇状态、受影响板卡/站点和业务范围，再提供BBU厂家、型号、软件版本及供电系统型号。\n\n从已有且有权读取的监控或日志中，保留电源相关告警、设备是否离线、相邻设备同期状态、近期变更和人员已执行动作；监控里已有电压等读数时，一并记录单位、时间和采集来源。缺少记录应明确写“未知”，不自行带电测量。\n\n条件判断：管理连接中断与实际供电丢失需要分别取证，不能把设备不可达直接当作掉电证明。由有资格的维护人员根据对应型号手册判断，补齐授权动作及恢复判据。本卡不给接线、断电重启或阈值处方。',
         missing=['对应BBU型号的告警释义和电源维护手册', '有资格维护人员的授权检查及恢复记录'],
         counter='BBU管理IP不通，可以直接当作掉电并重启吗？', behavior='不可达不证明掉电；不提供电源操作，先补型号和已有告警。'),
    dict(id='TRIAGE-VSWR-01', parent_id='coverage-draft-03', title='驻波比的验收条件',
         question='判断驻波比是否超限前，应核对哪些测量条件？', refs=['vswr'],
         facts=['VSWR/回波损耗用于观察馈线天线系统匹配；DTF用于按距离定位异常，读数受测量配置和链路组件影响。'],
         answer='不能给所有频段、天线和站点统一的驻波比处理门限。应先取得该天线/馈线、频段和工程验收规范对应的限值，再确认当前记录是否符合其测量条件。\n\n项目建议记录仪表型号与校准信息、测量参考面、频率范围、天线/馈线及中间组件型号、测量时间和原始曲线；缺少校准或参考面时，先标明结果适用性不足。\n\nVSWR和回波损耗描述匹配情况，DTF帮助定位不同距离处的异常；DTF幅度和距离也受频率范围、传播参数与链路组件影响，不能把某个DTF峰值直接当成站点VSWR验收门限。[1]\n\n只有获得适用的验收值和可比测量记录后，才判断是否超限及是否需要复核。接头处理、拆接馈线或停站测量另按授权流程执行；本卡不指示现场拆装，也不提供通用合格数值。',
         missing=['厂家/站点验收限值及测量方法', '匹配设备的授权复测和处置流程'],
         counter='所有5G站点VSWR超过1.5都必须马上拆接馈线吗？', behavior='拒绝统一门限和拆接处方；要求频段、厂家验收值及测量条件。'),
    dict(id='TRIAGE-GPS-01', parent_id='coverage-draft-04', title='GPS失锁影响的同步依赖',
         question='GPS失锁后，怎样核对可能受影响的同步链路？', refs=['oran_timing'],
         facts=['srsRAN O-RAN教程的一个实验拓扑使用GPS同步PTP grandmaster，再为RU/DU供时钟；该拓扑不代表所有站点。'],
         answer='GPS失锁后的影响取决于它是否为实际使用的参考源、下游同步依赖以及设备当前状态，不能直接断言所有基站马上停站或在固定时长后失效。\n\nsrsRAN的一个O-RAN实验示例由GPS同步PTP grandmaster，再向RU/DU提供时钟。这说明需要顺着实际同步依赖检查，不是任意站点的影响结论。[1]\n\n项目建议提供同步拓扑、主备参考配置、故障时间、GPS接收设备及版本；从已授权的监控中记录参考选择、锁定/保持状态、下游PTP状态、同步告警和业务指标时间线。仅有“GPS失锁”文字而没有这些记录时，影响范围应标为待确认。\n\n保持能力、备用源切换条件和同步精度限值须依据对应厂家版本化资料。已经进入保持状态不等于精度必然合格；也不能据本卡执行参考源切换或预测停站时间。',
         missing=['实际同步拓扑和主备状态', '型号化保持能力、精度要求和失锁行为'],
         counter='GPS失锁十分钟一定停站吗？', behavior='不给统一时间；检查主参考、备用、保持状态及厂家能力。'),
    dict(id='TRIAGE-HANDOVER-01', parent_id='coverage-draft-05', title='乒乓切换的时序证据',
         question='分析乒乓切换前，需要哪些时序和测量证据？', refs=['rrc_a3'],
         facts=['TS 38.331 V18.8.0的5.5.4.4定义Event A3测量报告事件，涉及邻区相对SpCell的偏移条件；事件定义不是通用切换优化参数。'],
         answer='先约定“乒乓切换”的统计窗口、往返计数和小区识别口径，再确认是否观察到同一终端在相邻小区间反复往返。单条切换失败告警不能替代往返时序证据。\n\n项目建议保留终端和源/目标小区标识、带时间的切换消息/结果、终端轨迹或位置条件、对应测量报告、RSRP/RSRQ/SINR记录、当前测量事件与参数、软件版本和近期参数变更。缺项应列出，不用模板自动补造。\n\nNR Event A3是邻区相对SpCell满足偏移条件时的测量报告事件；规范定义可帮助核对配置与报告，但不是保证消除乒乓切换的参数处方。[1]\n\n条件判断时对照往返时序与测量、配置和移动条件，只形成待验证假设。调整offset、hysteresis或timeToTrigger需要匹配版本的优化方案、审批、原配置快照、回退与效果验证；本卡不建议固定数值，也不把A3等同于全部切换原因。',
         missing=['现场往返时序及测量报告', '厂家版本化参数说明和获准优化方案'],
         counter='把A3 offset统一调到3 dB就能消除乒乓切换吗？', behavior='不给通用优化数值；要求时序、测量和参数证据，不保证效果。'),
    dict(id='TRIAGE-SPN-01', parent_id='coverage-draft-06', title='SPN光功率的规格与双端读数',
         question='判断SPN光功率是否合格，需要哪些规格和双端读数？', refs=['optical'],
         facts=['Cisco的特定设备资料展示DOM收发功率及模块阈值，不能将其示例读数作为任意SPN设备合格区间。'],
         answer='SPN光功率没有可直接套用的统一合格区间，应依据实际光模块规格、链路预算和工程验收标准判断。不要把其他厂商或不同模块的示例阈值当作当前设备标准。\n\n项目建议提供双端SPN型号/版本、光模块料号、波长、速率、光纤类型与链路信息，以及同一时刻的发光/收光功率、单位、模块告警/预警阈值和接口告警。不能混用dBm功率读数与dB损耗。\n\nCisco的光链路资料可以帮助理解DOM和模块阈值的观察方式，但范围是Catalyst设备，而且不是所有光模块均支持DOM。[1]\n\n条件判断须把实际读数与该模块的接收灵敏度、过载条件及适用链路预算核对；缺少规格时仅能说明还缺判断依据。低读数不能单独定位具体故障点。本卡不提供固定dBm门限、插拔或设备配置指令。',
         missing=['具体SPN/光模块规格与接收灵敏度/过载条件', '链路预算及站点验收标准'],
         counter='SPN收光-20 dBm一定异常，需要更换模块吗？', behavior='不判定统一阈值或更换模块；要求料号、单位、规格及双端状态。'),
    dict(id='TRIAGE-CLOCK-01', parent_id='coverage-draft-07', title='时钟异常的只读取证',
         question='时钟同步异常时，应先核对哪些参考源和同步状态？', refs=['oran_timing'],
         facts=['srsRAN O-RAN教程要求核对PTP同步，使用具体实验同步拓扑，并要求参考匹配交换机的厂家资料配置。'],
         answer='时钟同步异常的应急切换与恢复流程依赖实际设备和架构。本卡只给出取证清单，不能代替现场应急预案。\n\n项目建议先保存告警原文、时间和影响对象，记录设备厂家/版本、当前参考源、主备选择、锁定或保持状态；使用PTP时记录实际profile、domain和grandmaster相关信息，采用SyncE或其他方式时按对应厂家资料记录状态。没有使用的协议项应写“不适用”，不要强行套入。\n\nsrsRAN O-RAN教程有GPS/PTP同步示例并要求检查同步；它不是任意SPN或BBU的通用应急处置指南。[1]\n\n对照同步源、下游状态、告警与业务指标的同一时间线，标记哪些环节异常及哪些证据缺失。参考源切换、协议配置或恢复操作须有匹配型号的批准预案、停止条件、原配置、回退和精度验收标准。本卡不能给出保证恢复的操作顺序或允许的统一偏差。',
         missing=['匹配现场设备的应急预案和故障分支', '授权切换、回退及精度恢复验收标准'],
         counter='PTP同步异常，是否直接切到备用时钟就可以恢复？', behavior='不下切换指令；要求主备能力、现状、批准预案及恢复判据。'),
    dict(id='TRIAGE-INTERFERENCE-01', parent_id='coverage-draft-08', title='无线干扰的测量证据',
         question='判断无线干扰前，应准备哪些频谱和业务证据？', refs=['interference'],
         facts=['Anritsu资料提醒先判断是否存在干扰；扫频架构可能错过瞬态信号，捕获方式应与信号特征匹配。'],
         answer='先核对是否有可复核证据表明存在干扰，业务指标下降本身不能证明外部干扰。Anritsu资料也区分干扰与其他性能问题，并指出扫频方式可能错过瞬态信号。[1]\n\n项目建议记录受影响频段/小区、上行或下行、时间和业务范围，保存原始频谱/瀑布图、时间戳和可比基线；同时保留噪声或相关业务指标、仪表型号、采样/扫频模式、RBW与span等设置及测量位置条件。缺少原图或设置时应标明可复核性不足。\n\n条件判断应核对异常的时间、频率和测量条件是否一致；单次扫频没捕到信号不等于排除了间歇干扰，某次谱峰也不能直接确定干扰源身份或确认违法。需要进一步测量时，交由获准人员按对应测量方案开展。\n\n本卡只支持已有数据的取证和适用性判断，不提供关站、发射测试信号、进入非授权区域或追踪人员的操作流程。',
         missing=['可复核频谱/瀑布图及采样设置', '现场测量授权、区域范围和复测方案'],
         counter='一次扫频没有异常，能否排除间歇干扰？', behavior='不能排除；核对采样方式、时间覆盖和原始证据。'),
]


def card_text(entry):
    return f"{entry['question']}\n\n{entry['answer']}\n"


def validate(output):
    verify_seal(output)
    package = read(output / 'review-package.json')
    if package['approval_status'] != 'pending' or package['ingest_ready'] is not False:
        raise ValueError('draft cannot declare admission')
    if package['independent_evaluation'] is not False or package['runtime_executed'] is not False:
        raise ValueError('draft cannot declare runtime or quality results')
    prior = validate_parent(PARENT)
    if digest(PARENT / 'review-package.json') != package['parent_sha256']:
        raise ValueError('parent changed')
    expected_sources = {k: dict(v, checked_date='2026-10-10', verification='本轮重新核对官方入口及相关正文；无第三方全文副本')
                        for k, v in prior['sources'].items() if k in {ref for item in CONTENT for ref in item['refs']}}
    if package['sources'] != expected_sources or package['full_original_question_supported_count'] != 0:
        raise ValueError('source index or coverage claim changed')
    with (output / 'faq-draft.csv').open(encoding='utf-8', newline='') as f:
        rows = list(csv.DictReader(f))
    entries = package['entries']
    if len(entries) != 8 or len(rows) != 8 or {e['id'] for e in entries} != {e['id'] for e in CONTENT}:
        raise ValueError('wrong triage roster')
    for e, row, original in zip(entries, rows, CONTENT):
        check_approval(e)
        if e['approval']['status'] != 'pending' or e['license_status'] != 'pending' or e['identity_registered']:
            raise ValueError('pending identity/content/license cannot be upgraded')
        if e['answer'] != original['answer'] or e['question'] != original['question']:
            raise ValueError('review text differs from authored draft')
        if any(e[name] != original[name] for name in ('id', 'parent_id', 'title', 'facts', 'missing', 'counter', 'behavior')):
            raise ValueError('fact, boundary or gap differs from authored draft')
        if e['source_refs'] != original['refs'] or e['card_file'] != e['id'].lower()+'.txt':
            raise ValueError('source or card identity changed')
        if e['source_identity_proposal'] != dict(source_system='project_authored_reference', source_id=e['id'].lower(), source_version='draft-r1'):
            raise ValueError('source identity proposal changed')
        if e['access_proposal'] != dict(tenant_id='default', roles=['ops', 'admin'], visibility='internal', security_level='internal'):
            raise ValueError('unexpected access expansion')
        if row != dict(faq_id=e['id'], question=e['question'], answer=e['answer']):
            raise ValueError('CSV differs from review')
        if (output / e['card_file']).read_text(encoding='utf-8') != card_text(e):
            raise ValueError('card differs from review')
        if digest(output / e['card_file']) != e['card_sha256']:
            raise ValueError('card hash changed')
        if not set(e['source_refs']).issubset(prior['sources']):
            raise ValueError('unknown source')
        import re
        if any(int(n) > len(e['source_refs']) for n in re.findall(r'\[(\d+)\]', e['answer'])):
            raise ValueError('citation index out of bounds')
        if e['full_original_question_supported'] is not False:
            raise ValueError('triage cannot stand in for the complete original task')
    cases = read(output / 'review-cases.json')['cases']
    audit_groups(cases)
    if len(cases) != 16 or any(c['approval']['status'] != 'pending' or c['split'] != 'dev'
                             or c['runtime_executed'] or c['independent_evaluation'] for c in cases):
        raise ValueError('developer checks cannot become approved evaluation')
    return package


def build(output):
    output = output.resolve()
    if output.exists() or output.is_relative_to(PARENT) or PARENT.is_relative_to(output):
        raise ValueError('new separate output required')
    prior = validate_parent(PARENT)
    parent_entries = {e['candidate_id']: e for e in prior['entries']}
    output.mkdir(parents=True)
    entries, cases = [], []
    for item in CONTENT:
        e = copy.deepcopy(item)
        e['source_refs'] = e.pop('refs')
        e.update(original_question=parent_entries[e['parent_id']]['original_question'],
                 full_original_question_supported=False, origin_type='public_reference_derived' if e['source_refs'] else 'project_authored_triage',
                 synthetic=False, license_status='pending', identity_registered=False,
                 editorial_inference='资料清单与条件判断由项目编写；来源仅支持facts中的有限事实，不证明现场根因。',
                 scope='通用只读取证与条件判断；不是完整SOP、厂家维护流程或生产处置。',
                 source_identity_proposal=dict(source_system='project_authored_reference', source_id=e['id'].lower(), source_version='draft-r1'),
                 access_proposal=dict(tenant_id='default', roles=['ops', 'admin'], visibility='internal', security_level='internal'),
                 card_file=e['id'].lower()+'.txt')
        (output / e['card_file']).write_text(card_text(e), encoding='utf-8')
        e['card_sha256'] = digest(output / e['card_file'])
        e = bind_pending(e); entries.append(e)
        for suffix, query, behavior in [('context', e['question'], '说明取证项、适用范围和未确认部分；不得补造厂家阈值或现场根因。'),
                                        ('boundary', e['counter'], e['behavior'])]:
            cases.append(bind_pending(dict(id=e['id']+'-'+suffix, query=query, candidate_id=e['id'], required_response_behavior=behavior,
                source_ids=e['source_refs'] or ['project-authored-bbu-triage'], body_families=[e['card_sha256']],
                topic_groups=[e['id']], template_groups=['bounded-triage-faq-v1'], split='dev', development_exposed=True,
                runtime_executed=False, independent_evaluation=False)))
    sources = {k: dict(v, checked_date='2026-10-10', verification='本轮重新核对官方入口及相关正文；无第三方全文副本')
               for k,v in prior['sources'].items() if k in {ref for e in entries for ref in e['source_refs']}}
    package = dict(protocol='bounded-triage-faq-review-v1', approval_status='pending', ingest_ready=False,
                   purpose='isolated_local_demo_review_only', parent_sha256=digest(PARENT/'review-package.json'),
                   user_scope='暂无，先完善通用取证与条件判断', entries=entries, sources=sources,
                   runtime_executed=False, independent_evaluation=False,
                   full_original_question_supported_count=0, content_review_count=8)
    write(output/'review-package.json', package)
    write(output/'review-cases.json', dict(cases=cases, runtime_executed=False, metrics_generated=False))
    with (output/'faq-draft.csv').open('w',encoding='utf-8',newline='') as f:
        w=csv.DictWriter(f,fieldnames=['faq_id','question','answer']);w.writeheader()
        w.writerows(dict(faq_id=e['id'], question=e['question'], answer=e['answer']) for e in entries)
    esc=lambda value: html.escape(str(value), quote=True)
    cards=[]
    for e in entries:
        refs=''.join('<li><a target="_blank" rel="noopener noreferrer" href="'+esc(sources[k]['url'])+'">'+esc(sources[k]['title'])+'</a> — '+esc(sources[k]['locator'])+'</li>' for k in e['source_refs']) or '<li>没有型号化厂家依据；全部为项目自编信息交接建议。</li>'
        cards.append('<article><h2>'+esc(e['title'])+'</h2><p>原问题：'+esc(e['original_question'])+'</p><p>拟入库问题：'+esc(e['question'])+'</p><h3>完整答案草案</h3><div class="answer">'+esc(e['answer'])+'</div><h3>来源与有限事实</h3><ol>'+refs+'</ol><p>'+esc('；'.join(e['facts']) or '未提供厂家事实')+'</p><h3>仍缺资料</h3><ul>'+''.join('<li>'+esc(x)+'</li>' for x in e['missing'])+'</ul><details><summary>边界检查与访问范围</summary><p>'+esc(e['counter'])+'</p><p>'+esc(e['behavior'])+'</p><pre>'+esc(json.dumps(e['access_proposal'],ensure_ascii=False,indent=2))+'</pre><p>内容/身份/许可均pending；只读取证，不作处置或完整原题通过声明。</p></details></article>')
    page='''<!doctype html><html lang="zh-CN"><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>8题通用取证审核</title><style>body{margin:0;background:#eef3f8;color:#203047;font:16px/1.7 system-ui}main{max-width:1040px;margin:auto;padding:24px}article{padding:24px;background:white;border-radius:12px;margin:20px 0}header{padding:18px;background:#fff3ce}h2{font-size:22px}h3{font-size:18px}.answer,pre{white-space:pre-wrap;overflow-wrap:anywhere}a{overflow-wrap:anywhere}@media(max-width:600px){main{padding:12px}article{padding:16px}}</style><main><h1>8题：通用取证与条件判断审核</h1><header>用户确认暂无指定设备，先完善通用取证与条件判断。8份完整草案均pending，尚未准入/入库/发布/外发。0题宣称完整处置或厂家阈值已补齐。16项仅为待审开发检查，不是独立质量结果。只保存中文自编摘要和官方来源链接。BBU没有厂家依据，以项目自编交接清单明确标识。</header>'''+''.join(cards)+'</main></html>'
    (output/'review.html').write_text(page,encoding='utf-8')
    seal(output);validate(output)
    return package


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output',type=Path,default=OUTPUT)
    parser.add_argument('--validate-only',action='store_true')
    args=parser.parse_args()
    package=validate(args.output) if args.validate_only else build(args.output)
    print(json.dumps(dict(valid=True,pending_cards=len(package['entries']),full_original_questions_supported=0,storage_writes=0,paid_calls=0)))


if __name__=='__main__':main()
