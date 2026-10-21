from types import SimpleNamespace
from unittest.mock import Mock


def test_first_model_imports_do_not_race_intent_thread(monkeypatch):
    import app
    import threading
    from qa_core.api import route_qa
    from qa_core.intent import model_classifier
    from qa_core.rerank import reranker
    events=[]
    uc=SimpleNamespace(intent_model_path='intent',settings=SimpleNamespace(
        embedding_device='cpu',reranker_model_path='rerank',rerank_device='cpu'),
        searcher=SimpleNamespace(_embed_query_cached=lambda q:events.append('embedding')))
    monkeypatch.setattr(route_qa,'get_usecase',lambda:uc)
    monkeypatch.setattr(reranker,'get_reranker',lambda *a:SimpleNamespace(rerank=lambda *a,**k:events.append('rerank')))
    def intent_warmup(*a):
        assert events==['embedding','rerank'], 'intent may not race first model imports'
        events.append('intent'); return SimpleNamespace(result=lambda **k:events.append('ready'))
    monkeypatch.setattr(model_classifier,'warmup',intent_warmup)
    monkeypatch.setattr(model_classifier,'readiness',lambda *a:{'status':'ready'})
    monkeypatch.setattr(threading,'Thread',lambda target,**k:SimpleNamespace(start=target))
    app._warmup_models()
    assert events==['embedding','rerank','intent','ready']
