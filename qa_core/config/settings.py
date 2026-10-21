"""全局配置：唯一来源为环境变量/.env（pydantic-settings）。"""
from __future__ import annotations

import os
from functools import lru_cache
from pathlib import Path
from typing import Literal

from pydantic_settings import BaseSettings, SettingsConfigDict

QUERY_MAX_CHARS = 500
HISTORY_WINDOW_TURNS = 3


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=os.getenv("APP_ENV_FILE", ".env"),
        env_file_encoding="utf-8",
        extra="ignore",
    )

    app_name: str = "telecom-ops-rag"
    app_env: Literal["dev", "test", "demo", "prod"] = "dev"
    api_host: str = "0.0.0.0"
    api_port: int = 8010
    scenario: str = "telecom_ops"

    # 企业系统集成边界。默认地址指向 Docker Compose 中明确标注的模拟服务。
    enterprise_base_url: str = "http://enterprise-mock:8020/mock/v1"
    adapter_timeout_seconds: float = 2.0
    adapter_max_retries: int = 2
    adapter_page_size: int = 100

    mysql_dsn: str = "mysql+pymysql://rag:rag_pass@mysql:3306/rag_kb?charset=utf8mb4"
    redis_url: str = "redis://redis:6379/0"
    milvus_uri: str = "http://milvus:19530"
    doc_collection: str = "doc_collection"
    faq_collection: str = "faq_collection"

    data_dir: str = "data"
    reports_dir: str = "reports"

    models_dir: str = "/app/models"
    embedding_model_path: str = "/app/models/bge-m3"
    reranker_model_path: str = "/app/models/bge-reranker-large"
    intent_model_path: str = "/app/models/bert_intent_classifier_v1"
    embedding_device: str = "cpu"
    rerank_device: str = "cpu"

    llm_api_key: str = ""
    llm_base_url: str = "https://api.deepseek.com/v1"
    llm_model: str = "deepseek-chat"
    generation_enabled: bool = True  # false：仅输出获准证据，所有问答入口均不调用 LLM
    agent_enabled: bool = False  # deterministic read-only diagnosis; isolated opt-in

    dense_ef: int = 64
    top_k_dense: int = 50
    top_k_sparse: int = 50
    top_n_final: int = 5
    rerank_min: float = 0.30
    # 证据覆盖检查（7E）：问题内容词落在证据文本上的最低比例。
    # CrossEncoder 分数是候选集内的相对分，跨查询不可校准，挡不住域外问题；
    # 词汇覆盖是确定性信号。设 0 保证不拦截（关闭该检查）。
    evidence_min_coverage: float = 0.15
    faq_direct: float = 0.80
    faq_gap: float = 0.05

    auth_required: bool = False  # 生产置 true：无有效 X-API-Key 一律 401
    rate_limit_qps: int = 5      # 每认证主体每秒请求数（对应 scenario.limits.user_qps）
    semantic_cache_enabled: bool = True  # 语义缓存：相似问题直接复用已核验答案
    workflow_evidence_enabled: bool = False  # 候选验证后再开启，不改变旧库入口
    fusion_method: str = "weighted"      # 融合策略：weighted（加权）| rrf（倒数排名融合）
    cache_emb_ttl: int = 86400
    cache_ret_ttl: int = 600
    cache_faq_ttl: int = 3600

    @property
    def is_test(self) -> bool:
        return self.app_env == "test"

    @property
    def is_demo(self) -> bool:
        return self.app_env == "demo"

    def model_paths_exist(self) -> dict[str, bool]:
        """模型目录就位检查（宿主机调试时 /app/models 不存在属正常）。"""
        base = Path(self.models_dir)
        return {
            "bge-m3": (base / "bge-m3").is_dir(),
            "reranker": (base / "bge-reranker-large").is_dir(),
            "intent": (base / "bert_intent_classifier_v1").is_dir(),
        }


@lru_cache
def get_settings() -> Settings:
    return Settings()
