# -*- coding: utf-8 -*-
"""覆盖度与拒答降级的稳定性回归（r2 交付的一部分）。

钉死三件事：
1. 同问句重复判定结果完全一致（消除"时好时坏"的确定性）；
2. 长问句虚词/跨词伪组合不再稀释覆盖率（"见原"类伪词不产生）；
3. 域外问题仍被拦；实体权重让"实体命中 + 内容命中"稳定通过。
"""
from qa_core.application.qa_usecase import _content_tokens, evidence_covers
from qa_core.schemas.retrieval import RetrievedDoc


def doc(text, doc_type='manual'):
    return RetrievedDoc('d1', text, 0.9, rerank_score=0.9, source='manual.md',
                        kb_version='v-test', doc_type=doc_type)


def faq(answer):
    return RetrievedDoc('faq', '接口说明', 0.8, doc_type='faq', question='接口说明', answer=answer)


AMF_FAQ = faq('AMF注册成功率下降时，应先核对注册信令拥塞、传输链路与时延类上下文，再判断根因。')


def test_same_question_is_deterministic_over_ten_runs():
    query = 'AMF 注册失败常见原因有哪些？'
    first = evidence_covers(query, [AMF_FAQ], 0.15)
    assert first[0]
    for _ in range(10):
        assert evidence_covers(query, [AMF_FAQ], 0.15) == first


def test_long_question_phrases_do_not_dilute_coverage():
    # 修复前："失败常见原因" 滑窗产生 "败常"/"见原" 等跨词伪组合（分母膨胀、噪声拉低覆盖）。
    tokens = _content_tokens('失败常见原因有哪些')
    assert '见原' not in tokens and '败常' not in tokens and '因有' not in tokens
    assert '失败' in tokens and '哪些' not in tokens


def test_entities_carry_weight_but_do_not_bypass_coverage():
    assert evidence_covers('AMF 注册失败常见原因', [AMF_FAQ], 0.15)[0]
    assert not evidence_covers('AMF 注册失败常见原因', [doc('BBU 掉电处置流程说明')], 0.15)[0]


def test_out_of_domain_questions_still_blocked():
    for query in ('今天天气怎么样', '股票行情走势分析', '给我讲个笑话'):
        assert not evidence_covers(query, [AMF_FAQ], 0.15)[0]


def test_threshold_semantics_unchanged():
    assert evidence_covers('SMF', [doc('')], 0.15) == (False, 0)
    assert evidence_covers('什么', [doc('')], 0.15) == (True, 1)
    assert evidence_covers('SMF', [], 0) == (True, 1)
