"""Explicit process-scoped integration for complete-paragraph candidate rebuilds.

Released registry and runtime bindings remain unchanged. Use in a dedicated
offline process only: the existing rebuild dictionary is temporarily patched.
"""
from contextlib import contextmanager
from unittest.mock import patch

from qa_core.indexing.chunkers.paragraph_budget import chunk_manual_supported
from qa_core.indexing.chunkers.token_budget import bounded_chunks


@contextmanager
def supported_manual_rebuild(tokenizer):
    from scripts import rebuild_kb
    original_dedup_key=rebuild_kb.dedup_key

    def identity_key(chunk):
        import json
        key=original_dedup_key(chunk)
        if chunk.metadata.get('chunking_policy')=='tokenizer-paragraph-v1':
            key+=json.dumps([chunk.metadata.get(k) for k in
                ('source_system','source_id','source_version','source','clause')],ensure_ascii=False)
        return key

    def manual(text, **metadata):
        return chunk_manual_supported(text, tokenizer, **metadata)

    def preserve(chunks, active_tokenizer, *, max_tokens=450, atomic_tokens=1200):
        result=[]
        for chunk in chunks:
            if chunk.metadata.get('chunking_policy')=='tokenizer-paragraph-v1':
                if (len(active_tokenizer.encode(chunk.text,add_special_tokens=True))>max_tokens
                    or len(chunk.text.encode('utf-8'))>7900):
                    raise ValueError('normalized complete paragraph exceeds budget; refusing truncation')
                result.append(chunk)
            else:
                result.extend(bounded_chunks([chunk],active_tokenizer,
                    max_tokens=max_tokens,atomic_tokens=atomic_tokens))
        return result

    with patch.dict(rebuild_kb.TEXT_CHUNKERS,manual=manual,guide=manual), \
         patch('qa_core.indexing.chunkers.token_budget.bounded_chunks',preserve), \
         patch('scripts.rebuild_kb.dedup_key',identity_key):
        yield
