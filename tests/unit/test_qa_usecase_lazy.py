from unittest.mock import patch

from qa_core.application.qa_usecase import QaUseCase


def test_searcher_is_initialized_lazily():
    """构造问答用例时不应加载 embedding/Milvus；首次检索时才创建。"""
    sentinel = object()
    with patch("qa_core.application.qa_usecase.Searcher", return_value=sentinel) as factory:
        usecase = QaUseCase()
        factory.assert_not_called()

        assert usecase.searcher is sentinel
        factory.assert_called_once_with(settings=usecase.settings)


def test_injected_searcher_is_reused():
    sentinel = object()
    usecase = QaUseCase(searcher=sentinel)

    assert usecase.searcher is sentinel
