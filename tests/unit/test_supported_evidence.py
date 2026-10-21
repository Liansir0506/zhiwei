import pytest

from qa_core.retrieval.supported_evidence import relevant_docs
from qa_core.schemas.retrieval import RetrievedDoc


def faq(question, answer):
    return RetrievedDoc(question,question,.8,doc_type='faq',question=question,answer=answer)


@pytest.mark.parametrize('query,target,noise',[
    ('所有5G站点VSWR超过1.5都必须马上拆接馈线吗？','判断驻波比是否超限前，应核对哪些条件？','无线干扰频谱取证'),
    ('只有单端LOS告警，是否可以直接断定对端光模块坏了？','5G基站传输中断时，应收集哪些链路证据？','GPS失锁同步影响'),
    ('SPN收光-20 dBm一定异常，需要更换模块吗？','判断SPN光功率是否合格，需要哪些规格？','时钟同步异常需要哪些参考源状态？'),
    ('PTP同步异常，是否直接切到备用时钟就可以恢复？','时钟同步异常时，应核对哪些参考源？','GPS失锁后同步链路的影响'),
    ('一次扫频没有异常，能否排除间歇干扰？','判断无线干扰前，应准备哪些频谱证据？','基站传输中断时的链路证据'),
])
def test_cross_references_do_not_establish_subject(query,target,noise):
    correct=faq(target,'请核对对应证据，不能确认现场根因。')
    incidental=faq(noise,'其他场景也可能涉及VSWR、GPS、SPN、光功率、PTP、时钟、干扰及传输中断。')
    assert relevant_docs(query,[incidental,correct])==[correct]


def test_protocol_followup_keeps_body_only_port_facts():
    correct=faq('N2和N4接口用什么协议端口','PFCP UDP 8805，请求源端口本地分配；SCTP 38412。')
    wrong=faq('NG Setup Failure字段','消息可能缺少SCTP传输；不提供PFCP端口解释。')
    assert relevant_docs('PFCP请求源端口8805吗',[wrong,correct])==[correct]


def test_named_failure_procedure_takes_priority_over_transport_context():
    setup=faq('NG Setup Failure字段','SCTP未连上，不证明收到NG SETUP FAILURE。')
    ports=faq('N2和N4接口协议端口','SCTP 38412，PFCP UDP 8805。')
    assert relevant_docs('SCTP一直没有连上，是否就是NG SETUP FAILURE？',[ports,setup])==[setup]


def test_explicit_two_subject_comparison_requires_both():
    gps=faq('GPS失锁影响','GPS失锁对PTP的影响需核对拓扑。')
    clock=faq('时钟同步异常','PTP时钟异常；GPS实验例仅作参考。')
    assert relevant_docs('GPS失锁与PTP时钟异常有什么区别',[gps,clock])==[gps,clock]
    assert relevant_docs('GPS失锁与PTP时钟异常有什么区别',[gps])==[]


def test_shared_conjunction_is_not_a_comparison():
    correct=faq('SPN光功率的规格和读数','SPN光功率与模块规格对应。')
    noise=faq('GPS失锁影响','此处不提供SPN光功率标准。')
    assert relevant_docs('判断SPN光功率需要哪些规格和读数',[noise,correct])==[correct]


def test_band_and_power_class_require_actual_body_support():
    correct=faq('终端发射功率条件','FR1 n78 PC2 26 dBm；FR2不能套值。')
    wrong=faq('终端发射功率条件','FR1 n1 PC3的适用条件。')
    assert relevant_docs('n78 PC2永远26 dBm吗',[wrong,correct])==[correct]


def test_unknown_subject_preserves_existing_filter_and_order():
    docs=[faq('HTTP响应码说明','HTTP 503服务不可用。')]
    assert relevant_docs('HTTP 503是什么意思',docs)==docs
