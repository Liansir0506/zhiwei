"""BGE-M3 embedding 封装。

关键约定（易错点，勿改）：
- 入库侧（passage）不加任何指令前缀；
- 查询侧（query）加 BGE 官方查询指令前缀——前缀不对称是 BGE 系列既定用法；
- 输出 L2 归一化（COSINE 等价 IP）；
- dense 只算 1024 维（BGE-M3 dense 头），sparse 交给 Milvus 服务端 BM25。
"""
from __future__ import annotations

import threading

QUERY_INSTRUCTION = "为这个句子生成表示以用于检索相关文章："

_lock = threading.Lock()
_singleton: "BGEEmbedder | None" = None


class BGEEmbedder:
    def __init__(self, model_path: str, device: str = "cpu"):
        from sentence_transformers import SentenceTransformer

        self.model = SentenceTransformer(model_path, device=device)
        self.device = device
        self.dim = self.model.get_sentence_embedding_dimension()  # 1024

    def embed_passages(self, texts: list[str], batch_size: int | None = None) -> list[list[float]]:
        """按最长文本自适应批次，在 CPU 吞吐和长文本内存峰值之间取平衡。"""
        if batch_size is None:
            longest = max((len(text) for text in texts), default=0)
            batch_size = 64 if longest <= 256 else 32 if longest <= 1600 else 8
        return self.model.encode(
            texts, batch_size=batch_size, normalize_embeddings=True,
            show_progress_bar=False,
        ).tolist()

    def embed_query(self, query: str, batch_size: int = 8) -> list[float]:
        return self.model.encode(
            [QUERY_INSTRUCTION + query], batch_size=batch_size,
            normalize_embeddings=True, show_progress_bar=False,
        )[0].tolist()


def get_embedder(model_path: str, device: str = "cpu") -> BGEEmbedder:
    """进程级单例：模型加载耗时且占内存，重复加载没有意义。"""
    global _singleton
    with _lock:
        if _singleton is None:
            _singleton = BGEEmbedder(model_path, device)
    return _singleton


def reset_embedder() -> None:
    """测试用。"""
    global _singleton
    with _lock:
        _singleton = None
