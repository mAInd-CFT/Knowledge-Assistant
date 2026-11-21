from __future__ import annotations

from functools import lru_cache

from fastembed import SparseTextEmbedding, TextEmbedding

from app.config import settings


@lru_cache(maxsize=1)
def dense_model() -> TextEmbedding:
    """Load multilingual E5 locally via ONNX Runtime (CPU only)."""
    return TextEmbedding(
        model_name=settings.embedding_model,
        threads=settings.embedding_threads,
        cuda=False,
    )


def dense_embed(texts: list[str], task: str = "retrieval.passage") -> list[list[float]]:
    """Create local dense vectors; E5 uses separate query/passage adapters."""
    if not texts:
        return []

    model = dense_model()
    batch_size = max(1, settings.embedding_batch_size)
    if task == "retrieval.query":
        vectors = list(model.query_embed(texts, batch_size=batch_size))
    else:
        vectors = list(model.passage_embed(texts, batch_size=batch_size))

    if len(vectors) != len(texts):
        raise RuntimeError(
            f"Local embedding model returned {len(vectors)} vectors for {len(texts)} inputs"
        )
    result = [vector.tolist() for vector in vectors]
    if any(len(vector) != settings.embedding_dim for vector in result):
        raise RuntimeError(
            f"Local embedding model returned an unexpected vector size; expected {settings.embedding_dim}"
        )
    return result


@lru_cache(maxsize=1)
def sparse_model() -> SparseTextEmbedding:
    """Lightweight local BM25 encoder; complements semantic dense retrieval."""
    return SparseTextEmbedding(model_name="Qdrant/bm25", language="russian")


def sparse_query_embed(text: str) -> dict:
    """Encode a search query as BM25 query terms (unit query weights)."""
    item = next(iter(sparse_model().query_embed(text)))
    return {"indices": item.indices.tolist(), "values": item.values.tolist()}


def sparse_passage_embed(text: str) -> dict:
    """Encode a document chunk with BM25 term-frequency weights."""
    item = next(iter(sparse_model().embed(text)))
    return {"indices": item.indices.tolist(), "values": item.values.tolist()}
