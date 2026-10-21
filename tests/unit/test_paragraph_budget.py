import pytest
from qa_core.indexing.chunkers.paragraph_budget import chunk_manual_supported


class Characters:
    def encode(self,text,**kwargs):return list(text)+[0,0]


def test_complete_statements_and_nested_chapter_are_kept():
    text='# 主主题\n\n## 核对阶段\n\n第一项完整检查。\n第二项完整检查。\n第三项完整检查。\n'
    chunks=chunk_manual_supported(text,Characters(),max_tokens=21)
    assert len(chunks)==2
    assert [c.text for c in chunks]==['第一项完整检查。\n第二项完整检查。','第三项完整检查。']
    assert all(c.metadata['clause']=='主主题/核对阶段' for c in chunks)
    assert all(c.text.endswith('。') for c in chunks)


def test_long_atomic_statement_rejected_instead_of_truncated():
    with pytest.raises(ValueError,match='editorial split'):
        chunk_manual_supported('# 主题\n\n'+'完整事实'*40+'。',Characters(),max_tokens=25)


def test_wrapped_paragraph_cannot_be_split_mid_statement():
    with pytest.raises(ValueError,match='editorial split'):
        chunk_manual_supported('这是尚未结束的句子\n继续同一个句子直到末尾。',Characters(),max_tokens=16)


def test_source_identity_and_unmodified_statement_text():
    text='# 时钟\n\n## PTP\n\n只读取证，不执行切换。\n'
    chunk=chunk_manual_supported(text,Characters(),source='clock.md')[0]
    assert chunk.text=='只读取证，不执行切换。'
    assert chunk.metadata['source']=='clock.md'
    assert chunk.metadata['clause']=='时钟/PTP'


def test_preface_is_preserved_without_an_invented_heading():
    chunks=chunk_manual_supported('项目声明。\n\n# 主题\n\n核对证据。',Characters())
    assert chunks[0].text=='项目声明。' and chunks[0].metadata['clause']==''
    assert chunks[1].metadata['clause']=='主题'
