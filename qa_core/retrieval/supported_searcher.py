"""Scoped FAQ subject recall supplements semantic top-k; it never grants access."""
from qa_core.retrieval.searcher import Searcher
from qa_core.retrieval.supported_evidence_v2 import primary_topics,relevant_docs


class SupportedFaqSearcher(Searcher):
    def search_faq(self,query,roles,kb_version,top_k=5,tenant_id='default',scope=None):
        docs=super().search_faq(query,roles,kb_version,top_k=top_k,tenant_id=tenant_id,scope=scope)
        if any(d.question==query for d in docs) or not primary_topics(query):return docs
        # The catalog applies the same tenant/role/effective-time/withdrawal
        # policy. Supplemental subjects still pass the normal rerank threshold.
        supported=relevant_docs(query,self.catalog_docs(roles,kb_version,tenant_id=tenant_id,scope=scope,kind='faq'))
        seen={d.id for d in docs}
        for doc in supported[:20]:
            if doc.id in seen:continue
            doc.dense_score=doc.fusion_score=0.0
            docs.append(doc);seen.add(doc.id)
        return docs
