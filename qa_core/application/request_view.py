"""A lightweight per-request view; never reconfigure the shared Searcher."""
from copy import copy, deepcopy
from functools import wraps
from inspect import isgeneratorfunction


def request_scoped(func):
    def view_of(owner):
        from qa_core.retrieval.searcher import Searcher
        view = copy(owner)
        view.settings = deepcopy(owner.settings)
        view.cfg = deepcopy(owner.cfg)
        if isinstance(owner._searcher, Searcher):
            from qa_core.ingest.binding import version_binding
            if version_binding(owner._searcher.settings) != version_binding(view.settings):
                raise ValueError("已初始化检索器与请求读取配置不一致，禁止复用")
            view._searcher = copy(owner._searcher)
            view._searcher.settings = view.settings
        return view
    if isgeneratorfunction(func):
        @wraps(func)
        def stream(owner, *args, **kwargs):
            yield from func(view_of(owner), *args, **kwargs)
        return stream
    @wraps(func)
    def sync(owner, *args, **kwargs):
        return func(view_of(owner), *args, **kwargs)
    return sync
