"""Test-only local HTTP SSE app; never imports model credentials or real stores."""
import threading

from fastapi import FastAPI

from qa_core.api import route_qa
from qa_core.api.auth import AuthContext, get_auth_context

app = FastAPI()
app.include_router(route_qa.router)
app.dependency_overrides[get_auth_context] = lambda: AuthContext("transport-test", ["ops"], "api_key")
route_qa._enforce_rate_limit = lambda ctx: None
route_qa.audit_access = lambda *args, **kwargs: None

release = threading.Event()
lock = threading.Lock()
started = 0
exited = 0


class FakeUseCase:
    def ask_stream(self, *args, **kwargs):
        global started, exited
        with lock:
            started += 1
        try:
            yield {"event": "start", "data": {"kb_version": "v1"}}
            release.wait(15)
            yield {"event": "end", "data": {"status": "ANSWERED", "answer": "test"}}
        finally:
            with lock:
                exited += 1


route_qa.get_usecase = lambda: FakeUseCase()


@app.get("/test/stats")
def stats():
    with lock:
        return {"started": started, "exited": exited,
                "availableSlots": route_qa._sse_slots._value}


@app.post("/test/release")
def release_workers():
    release.set()
    return {"released": True}
