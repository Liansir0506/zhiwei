"""Preserve guide type in the released paragraph policy, dedicated process only."""
from contextlib import contextmanager
from unittest.mock import patch

from qa_core.indexing.chunkers.paragraph_budget import chunk_manual_supported
from qa_core.indexing.chunkers.supported_rebuild import supported_manual_rebuild


def chunk_evidence_guide(text,tokenizer,**metadata):
    chunks=chunk_manual_supported(text,tokenizer,**metadata)
    for c in chunks:c.doc_type='guide';c.metadata['doc_type']='guide'
    return chunks


@contextmanager
def evidence_guide_rebuild(tokenizer):
    from scripts import rebuild_kb
    with supported_manual_rebuild(tokenizer),patch.dict(rebuild_kb.TEXT_CHUNKERS,
        guide=lambda text,**metadata:chunk_evidence_guide(text,tokenizer,**metadata)):
        yield
