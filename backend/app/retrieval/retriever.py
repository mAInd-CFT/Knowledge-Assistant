from functools import lru_cache

from qdrant_client import QdrantClient, models
from qdrant_client.http.exceptions import UnexpectedResponse

from app.config import settings
from app.retrieval.embeddings import dense_embed, sparse_query_embed


@lru_cache(maxsize=1)
def get_qdrant() -> QdrantClient:
    return QdrantClient(host=settings.qdrant_host, port=settings.qdrant_port, timeout=10)


def search(query: str, top_k: int | None = None) -> list[dict]:
    limit = min(max(int(top_k or settings.retrieval_top_k), 1), 30)
    client = get_qdrant()
    try:
        client.get_collection(settings.qdrant_collection)
    except UnexpectedResponse as exc:
        if exc.status_code == 404:
            return []
        raise

    dense = dense_embed([query], task="retrieval.query")[0]
    sparse = sparse_query_embed(query)
    active_filter = models.Filter(
        must=[
            models.FieldCondition(key="is_active", match=models.MatchValue(value=True)),
            models.FieldCondition(key="scope", match=models.MatchValue(value="cft")),
        ]
    )
    result = client.query_points(
        collection_name=settings.qdrant_collection,
        prefetch=[
            models.Prefetch(
                query=dense, using="dense", limit=limit * 3,
                filter=active_filter,
            ),
            models.Prefetch(
                query=models.SparseVector(**sparse),
                using="bm25", limit=limit * 3, filter=active_filter,
            ),
        ],
        query=models.FusionQuery(fusion=models.Fusion.RRF),
        query_filter=active_filter,
        limit=limit,
        with_payload=True,
    )
    output = []
    for point in result.points:
        if float(point.score or 0.0) < settings.retrieval_min_score:
            continue
        payload = point.payload or {}
        output.append({
            "text": payload.get("chunk_text", ""),
            "title": payload.get("title", ""),
            "source_url": payload.get("source_uri", "") if str(payload.get("source_uri", "")).startswith(("http://", "https://")) else "",
            "file_name": payload.get("source_file", ""),
            "section": payload.get("section", ""),
            "page": payload.get("page"),
            "source_id": payload.get("source_id"),
            "version": payload.get("version"),
            "retrieved_at": payload.get("retrieved_at"),
            "published_at": payload.get("published_at"),
            "score": point.score,
        })
    return output
