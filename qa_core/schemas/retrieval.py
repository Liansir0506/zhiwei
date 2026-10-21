"""检索层数据模型。分数链路：dense / sparse / fusion / rerank 四种分数分开记录，不混比。"""
from __future__ import annotations

from dataclasses import dataclass, field


@dataclass
class RetrievedDoc:
    id: str
    text: str
    fusion_score: float          # hybrid 加权融合分（0-1）
    dense_score: float = 0.0     # COSINE 原始分（0-1）
    sparse_score: float = 0.0    # BM25 min-max 归一后的分（0-1）
    rerank_score: float | None = None  # sigmoid(logit)，0-1
    # 元数据
    doc_type: str = ""
    source: str = ""
    alarm_code: str = ""
    kb_version: str = ""
    network_tech: str = ""
    equipment_type: str = ""
    vendor: str = ""
    tenant_id: str = "default"
    dept_id: str = ""
    security_level: str = ""
    device_type: str = ""
    source_system: str = ""
    source_id: str = ""
    source_version: str = ""
    effective_from: str = ""
    effective_to: str = ""
    status: str = ""
    ticket_id: str = ""
    cmdb_id: str = ""
    change_id: str = ""
    asset_id: str = ""
    origin_type: str = ""
    synthetic: bool = False
    generator_version: str = ""
    generation_seed: str = ""
    review_status: str = ""
    license_status: str = ""
    evidence_refs: list[str] = field(default_factory=list)
    role_scope: list[str] = field(default_factory=list)
    clause: str = ""
    parent_id: str = ""
    question: str = ""   # faq 专用
    answer: str = ""     # faq 专用
    extra: dict = field(default_factory=dict)


    def to_dict(self) -> dict:
        from dataclasses import asdict

        return asdict(self)

    @classmethod
    def from_dict(cls, d: dict) -> "RetrievedDoc":
        d = {k: v for k, v in d.items() if k in cls.__dataclass_fields__}
        return cls(**d)


@dataclass
class RetrievalResult:
    query_variants: list[str]
    docs: list[RetrievedDoc]
    candidates_dense: int = 0
    candidates_sparse: int = 0
    after_dedup: int = 0
    filter_relaxed: bool = False   # 软过滤放宽重查过一次
    cache_hit: bool = False        # L2 检索候选缓存命中

    @property
    def top_score(self) -> float:
        return self.docs[0].fusion_score if self.docs else 0.0
