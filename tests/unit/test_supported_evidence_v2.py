from qa_core.retrieval.supported_evidence_v2 import relevant_docs,primary_topics
from qa_core.schemas.retrieval import RetrievedDoc


def test_spn_device_name_does_not_imply_optical_topic():
    optical=RetrievedDoc('optical','光功率规格',.8,doc_type='faq',question='判断SPN光功率需要哪些规格？',answer='SPN收光需核对规格。')
    switching=RetrievedDoc('switching','SPN保护倒换',.8,doc_type='faq',question='SPN保护倒换时应核对什么？',answer='SPN链路光功率可能相关。')
    assert relevant_docs('SPN收光-20 dBm一定异常吗？',[switching,optical])==[optical]
    assert primary_topics('SPN时钟同步异常')==['clock']


def test_optical_statement_does_not_replace_named_los_subject():
    assert primary_topics('单端LOS是否能证明光模块坏了？')==['transport']
    assert primary_topics('SPN接收功率-20 dBm是否超限？')==['optical']


def test_clock_subject_alone_cannot_support_named_ptp_protocol():
    generic=RetrievedDoc('generic','时钟源丢失',.8,doc_type='faq',question='SPN时钟源丢失如何核对？',answer='收集告警。')
    specific=RetrievedDoc('ptp','PTP时钟同步异常',.8,doc_type='faq',question='时钟异常如何核对？',answer='PTP参考源需核对主备状态，不能直接切换。')
    assert relevant_docs('PTP同步异常是否直接切换备用时钟？',[generic,specific])==[specific]


def test_protocol_hypothesis_does_not_support_backup_switch_action():
    hypothesis=RetrievedDoc('hypothesis','时钟同步失步',.8,doc_type='faq',question='时钟失步应核对什么？',answer='PTP路径不对称是待验证假设。')
    assert relevant_docs('PTP同步异常直接切备用就能恢复吗？',[hypothesis])==[]
    assert relevant_docs('PTP时钟失步可能涉及什么？',[hypothesis])==[hypothesis]


def test_general_interference_case_does_not_support_intermittent_exclusion():
    generic=RetrievedDoc('generic','无线干扰导致指标下降。',.8,doc_type='fault_case')
    scoped=RetrievedDoc('scoped','干扰取证',.8,doc_type='faq',question='无线干扰如何取证？',answer='扫频可能错过瞬态信号，单次观测不能排除间歇干扰。')
    assert relevant_docs('一次扫频正常就能排除间歇干扰吗？',[generic,scoped])==[scoped]
