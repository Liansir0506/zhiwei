"""FastAPI 入口。阶段3：lifespan 启动时探测各组件并缓存到 app.state（/health 可 refresh 实时复查）。"""
from __future__ import annotations

from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

from qa_core.api.route_health import router as health_router
from qa_core.api.route_qa import router as qa_router
from qa_core.api.route_kb import router as kb_router
from qa_core.api.route_alerts import router as alerts_router
from qa_core.api.route_metrics import router as metrics_router
from qa_core.api.route_admin import router as admin_router
from qa_core.api.route_domain import router as domain_router
from qa_core.api.route_dashboard import router as dashboard_router
from qa_core.api.route_ops_resources import router as ops_resources_router
from qa_core.api.route_query import router as query_router
from qa_core.api.route_tickets import router as tickets_router
from qa_core.api.route_kbfix import router as kbfix_router
from qa_core.api.route_stats import router as stats_router
from qa_core.api.route_integrations import router as integrations_router
from qa_core.api.route_ingest import router as ingest_router
from qa_core.api.route_ingest_workspace import router as ingest_workspace_router
from qa_core.api.route_evidence import router as evidence_router
from qa_core.api.route_agent import router as agent_router
from qa_core.api.route_business_workspace import router as business_workspace_router
from qa_core.api.route_version_workspace import router as version_workspace_router
from qa_core.config.settings import get_settings
from qa_core.storage.connections import check_all


@asynccontextmanager
async def lifespan(app: FastAPI):
    settings = get_settings()
    # 启动时全量探测一次；失败不阻断启动（/health 返回 degraded 并可 refresh 复查）
    try:
        app.state.health_components = check_all(settings)
    except Exception:  # noqa: BLE001 — 探测本身绝不能导致服务不可用
        app.state.health_components = None
    app.state.settings = settings
    yield


def create_app() -> FastAPI:
    settings = get_settings()
    app = FastAPI(title=settings.app_name, lifespan=lifespan)
    app.include_router(health_router)
    app.include_router(qa_router)
    app.include_router(kb_router)
    app.include_router(alerts_router)
    app.include_router(metrics_router)
    app.include_router(admin_router)
    app.include_router(domain_router)
    app.include_router(dashboard_router)
    app.include_router(ops_resources_router)
    app.include_router(query_router)
    app.include_router(tickets_router)
    app.include_router(kbfix_router)
    app.include_router(stats_router)
    app.include_router(integrations_router)
    app.include_router(ingest_router)
    app.include_router(ingest_workspace_router)
    app.include_router(evidence_router)
    app.include_router(agent_router)
    app.include_router(business_workspace_router)
    app.include_router(version_workspace_router)
    # 轻量前端：/ 返回聊天页，静态资源挂 /static
    static_dir = Path(__file__).resolve().parent / "static"
    if static_dir.is_dir():
        app.mount("/static", StaticFiles(directory=str(static_dir)), name="static")

        @app.get("/", include_in_schema=False)
        def index():
            # no-cache：前端迭代频繁，确保浏览器每次拿到最新版（避免旧 JS 引用新接口报错）
            return FileResponse(static_dir / "index.html",
                                headers={"Cache-Control": "no-cache, no-store, must-revalidate"})

    # 测试进程会反复 create_app；后台 Torch 线程可能在解释器退出时触发原生层 abort。
    # test 环境保持按需加载，dev/demo/prod 才执行启动预热。
    if not settings.is_test:
        _warmup_models()  # 后台预加载模型：消除容器重启后首查 20s+ 冷加载
    return app


def _warmup_models() -> None:
    """启动后台线程预热全链路模型（embedding/rerank/intent）。

    模型加载发生在首次使用时（rerank 冷启动 ~20s、BERT ~10s），
    warmup 让它们在服务就绪后立即完成加载，首查即达热路径性能。
    预热失败不阻塞服务——首查自然冷加载。
    """
    import threading

    def _run() -> None:
        try:
            from qa_core.api.route_qa import get_usecase

            uc = get_usecase()
            from qa_core.intent import model_classifier

            q = "预热查询"
            uc.searcher._embed_query_cached(q)                 # embedding 模型
            from qa_core.rerank.reranker import get_reranker

            class _D:  # 最小 dummy doc（rerank 只用到 text）
                text = "预热文本"

            rr = get_reranker(uc.settings.reranker_model_path, uc.settings.rerank_device)
            rr.rerank(q, [_D()], top_n=1)                      # rerank 模型
            # First imports of Transformers lazy modules are not safe to race
            # across the intent and embedding threads. Finish embedding/rerank
            # initialization before submitting the BERT warmup.
            # “预热”规则查询不证明 BERT ready，仍需真正执行其 warmup。
            intent_future = model_classifier.warmup(
                uc.intent_model_path, uc.settings.embedding_device)
            if intent_future is not None:
                intent_future.result(timeout=120)
            intent_status = model_classifier.readiness(
                uc.intent_model_path, uc.settings.embedding_device)
            print(f"[warmup] embedding/rerank 预热完成；intent={intent_status['status']}")
        except Exception as exc:  # noqa: BLE001
            print(f"[warmup] 预热失败（首查将冷加载）: {type(exc).__name__}: {exc}")

    threading.Thread(target=_run, daemon=True, name="model-warmup").start()


app = create_app()

if __name__ == "__main__":
    import uvicorn

    settings = get_settings()
    uvicorn.run("app:app", host=settings.api_host, port=settings.api_port)
