from qa_core.indexing.embedder.embedder import BGEEmbedder


class FakeModel:
    def __init__(self):
        self.batch_size = None

    def encode(self, texts, *, batch_size, normalize_embeddings, show_progress_bar):
        self.batch_size = batch_size

        class Result:
            @staticmethod
            def tolist():
                return [[0.0] for _ in texts]

        return Result()


def _embedder():
    instance = object.__new__(BGEEmbedder)
    instance.model = FakeModel()
    instance.device = "cpu"
    instance.dim = 1
    return instance


def test_passage_batch_size_adapts_to_text_length():
    embedder = _embedder()
    embedder.embed_passages(["x" * 100])
    assert embedder.model.batch_size == 64
    embedder.embed_passages(["x" * 1000])
    assert embedder.model.batch_size == 32
    embedder.embed_passages(["x" * 2000])
    assert embedder.model.batch_size == 8
