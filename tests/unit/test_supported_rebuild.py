import pytest
from qa_core.indexing.chunkers.supported_rebuild import supported_manual_rebuild
from scripts import rebuild_kb


class Characters:
    def encode(self,text,**kwargs):return list(text)+[0,0]


def test_rebuild_registry_is_scoped_and_restored_after_failure():
    original=dict(rebuild_kb.TEXT_CHUNKERS)
    with pytest.raises(RuntimeError):
        with supported_manual_rebuild(Characters()):
            chunk=rebuild_kb.TEXT_CHUNKERS['guide']('# 时钟\n\n## 核对\n\n完整事实。',source='guide.md')[0]
            assert chunk.metadata['clause']=='时钟/核对'
            assert chunk.text=='完整事实。'
            raise RuntimeError('simulate failed candidate')
    assert rebuild_kb.TEXT_CHUNKERS==original


def test_budget_step_refuses_normalized_statement_truncation():
    with supported_manual_rebuild(Characters()):
        chunk=rebuild_kb.TEXT_CHUNKERS['manual']('# 核对\n\n完整事实。')[0]
        from qa_core.indexing.chunkers.token_budget import bounded_chunks
        assert bounded_chunks([chunk],Characters(),max_tokens=20)[0] is chunk
        with pytest.raises(ValueError,match='refusing truncation'):
            bounded_chunks([chunk],Characters(),max_tokens=3)


def test_same_statement_in_distinct_chapters_keeps_both_identities():
    original=rebuild_kb.dedup_key
    with supported_manual_rebuild(Characters()):
        first=rebuild_kb.TEXT_CHUNKERS['manual']('# 章节一\n\n核对证据。',source='manual.md')[0]
        second=rebuild_kb.TEXT_CHUNKERS['manual']('# 章节二\n\n核对证据。',source='manual.md')[0]
        assert first.text==second.text
        assert rebuild_kb.dedup_key(first)!=rebuild_kb.dedup_key(second)
    assert rebuild_kb.dedup_key is original
