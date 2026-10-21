from unittest.mock import Mock,patch
from qa_core.retrieval.supported_searcher import SupportedFaqSearcher
from qa_core.retrieval.searcher import Searcher
from qa_core.schemas.retrieval import RetrievedDoc


def doc(identity,question):
    return RetrievedDoc(identity,question,0.8,doc_type='faq',question=question,answer='只读取证，不直接归因。',dense_score=0.8)


def test_subject_supplement_uses_same_scope_without_boosting_similarity():
    searcher=object.__new__(SupportedFaqSearcher);scope=object()
    unrelated=doc('unrelated','BBU掉电应核对什么？');supported=doc('supported','传输中断应收集哪些证据？')
    searcher.catalog_docs=Mock(return_value=[unrelated,supported])
    with patch.object(Searcher,'search_faq',return_value=[unrelated]):
        result=searcher.search_faq('只有单端LOS，是否能直接归因？',['admin'],'candidate',tenant_id='tenant',scope=scope)
    assert result==[unrelated,supported]
    assert supported.dense_score==supported.fusion_score==0
    searcher.catalog_docs.assert_called_once_with(['admin'],'candidate',tenant_id='tenant',scope=scope,kind='faq')


def test_exact_question_and_unknown_subject_do_not_expand_recall():
    searcher=object.__new__(SupportedFaqSearcher);searcher.catalog_docs=Mock()
    exact=doc('exact','传输中断应收集哪些证据？')
    with patch.object(Searcher,'search_faq',return_value=[exact]):
        assert searcher.search_faq(exact.question,['admin'],'candidate')==[exact]
        assert searcher.search_faq('HTTP 503的Retry-After有什么作用？',['admin'],'candidate')==[exact]
    searcher.catalog_docs.assert_not_called()


def test_already_recalled_identity_is_not_duplicated():
    searcher=object.__new__(SupportedFaqSearcher);supported=doc('same','传输中断如何取证？')
    searcher.catalog_docs=Mock(return_value=[supported])
    with patch.object(Searcher,'search_faq',return_value=[supported]):
        assert searcher.search_faq('单端LOS能判断故障吗？',['admin'],'candidate')==[supported]
    assert supported.dense_score==0.8
