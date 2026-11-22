import hashlib
import re
from datetime import datetime, timezone
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit
from uuid import NAMESPACE_URL, uuid5

from qdrant_client import QdrantClient, models
from sqlalchemy import select

from app.config import settings
from app.db.models import Document, DocumentVersion
from app.db.postgres import SessionLocal
from app.retrieval.chunker import chunk_text
from app.retrieval.embeddings import dense_embed, sparse_passage_embed


def _client() -> QdrantClient:
    return QdrantClient(host=settings.qdrant_host, port=settings.qdrant_port, timeout=30)


def ensure_collection(client: QdrantClient) -> None:
    existing = {item.name for item in client.get_collections().collections}
    if settings.qdrant_collection not in existing:
        client.create_collection(
            collection_name=settings.qdrant_collection,
            vectors_config={
                "dense": models.VectorParams(
                    size=settings.embedding_dim,
                    distance=models.Distance.COSINE,
                )
            },
            sparse_vectors_config={
                "bm25": models.SparseVectorParams(modifier=models.Modifier.IDF)
            },
        )
    for field, schema in (
        ("is_active", models.PayloadSchemaType.BOOL),
        ("source_id", models.PayloadSchemaType.KEYWORD),
        ("version", models.PayloadSchemaType.KEYWORD),
        ("scope", models.PayloadSchemaType.KEYWORD),
    ):
        client.create_payload_index(
            collection_name=settings.qdrant_collection,
            field_name=field,
            field_schema=schema,
        )


def canonicalize_source(source_uri: str) -> str:
    parts = urlsplit(source_uri)
    if parts.scheme not in {"http", "https"}:
        return source_uri
    host = (parts.hostname or "").lower()
    port = parts.port
    netloc = host if not port or (parts.scheme == "https" and port == 443) or (parts.scheme == "http" and port == 80) else f"{host}:{port}"
    query = [
        (key, value) for key, value in parse_qsl(parts.query, keep_blank_values=True)
        if not key.lower().startswith(("utm_", "fbclid", "gclid"))
    ]
    return urlunsplit((parts.scheme.lower(), netloc, parts.path or "/", urlencode(sorted(query)), ""))


def _set_active(client: QdrantClient, source_id: str, version: str, active: bool) -> None:
    client.set_payload(
        collection_name=settings.qdrant_collection,
        payload={"is_active": active},
        points=models.Filter(must=[
            models.FieldCondition(key="source_id", match=models.MatchValue(value=source_id)),
            models.FieldCondition(key="version", match=models.MatchValue(value=version)),
        ]),
        wait=True,
    )


def index_document(
    source_uri: str,
    title: str,
    text: str,
    source_file: str | None = None,
    published_at: str | None = None,
    retrieved_at: str | None = None,
    metadata: dict | None = None,
    dedupe_content: bool = True,
) -> dict:
    source_uri = canonicalize_source(source_uri)
    # Preserve headings, paragraphs and page markers; whitespace normalization
    # must not flatten the document before chunking.
    normalized = "\n".join(line.rstrip() for line in (text or "").replace("\r\n", "\n").replace("\r", "\n").splitlines()).strip()
    if not normalized:
        raise ValueError(f"No extractable text in {source_uri}")

    source_id = hashlib.sha256(source_uri.encode("utf-8")).hexdigest()
    version = hashlib.sha256(normalized.encode("utf-8")).hexdigest()
    with SessionLocal() as db:
        current = db.get(Document, source_id)
        if dedupe_content:
            duplicate = db.scalar(select(Document).where(
                Document.active_version == version,
                Document.source_id != source_id,
                Document.scope == "cft",
            ).limit(1))
            if duplicate is not None:
                return {
                    "source_id": source_id,
                    "version": version,
                    "chunks": 0,
                    "unchanged": True,
                    "duplicate": True,
                    "duplicate_of": duplicate.source_uri,
                }
        if current and current.active_version == version:
            row = db.scalar(select(DocumentVersion).where(
                DocumentVersion.source_id == source_id,
                DocumentVersion.version == version,
            ))
            client = _client()
            try:
                scoped_count = client.count(
                    collection_name=settings.qdrant_collection,
                    count_filter=models.Filter(must=[
                        models.FieldCondition(key="source_id", match=models.MatchValue(value=source_id)),
                        models.FieldCondition(key="version", match=models.MatchValue(value=version)),
                        models.FieldCondition(key="scope", match=models.MatchValue(value="cft")),
                    ]),
                    exact=True,
                ).count
            except Exception:
                scoped_count = 0
            if scoped_count:
                # Content may be unchanged while title, filename or publication metadata changed.
                published_dt = None
                if published_at:
                    try:
                        published_dt = datetime.fromisoformat(published_at.replace("Z", "+00:00"))
                        if published_dt.tzinfo is None:
                            published_dt = published_dt.replace(tzinfo=timezone.utc)
                    except ValueError:
                        published_dt = None
                current.source_uri = source_uri
                current.title = title
                current.source_file = source_file or current.source_file
                current.scope = "cft"
                retrieved_dt = datetime.now(timezone.utc)
                if retrieved_at:
                    try:
                        retrieved_dt = datetime.fromisoformat(retrieved_at.replace("Z", "+00:00"))
                        if retrieved_dt.tzinfo is None:
                            retrieved_dt = retrieved_dt.replace(tzinfo=timezone.utc)
                    except ValueError:
                        pass
                current.retrieved_at = retrieved_dt
                current.published_at = published_dt
                client.set_payload(
                    collection_name=settings.qdrant_collection,
                    payload={"source_uri": source_uri, "source_file": source_file or "", "title": title,
                             "retrieved_at": retrieved_dt.isoformat(),
                             "published_at": published_dt.isoformat() if published_dt else None,
                             "product_version": (metadata or {}).get("product_version") or "",
                             "code": (metadata or {}).get("code") or "",
                             "item_type": (metadata or {}).get("item_type") or "",
                             "catalog": (metadata or {}).get("catalog") or ""},
                    points=models.Filter(must=[
                        models.FieldCondition(key="source_id", match=models.MatchValue(value=source_id)),
                        models.FieldCondition(key="version", match=models.MatchValue(value=version)),
                    ]), wait=True,
                )
                if row is not None:
                    row.retrieved_at = current.retrieved_at
                    row.published_at = published_dt
                db.commit()
                return {
                    "source_id": source_id,
                    "version": version,
                    "chunks": row.chunk_count if row else scoped_count,
                    "unchanged": True,
                    "metadata_updated": True,
                }
        old_version = current.active_version if current else None

    chunks = chunk_text(normalized)
    dense_vectors = dense_embed(chunks)
    client = _client()
    ensure_collection(client)
    retrieved_at_dt = datetime.now(timezone.utc)
    if retrieved_at:
        try:
            retrieved_at_dt = datetime.fromisoformat(retrieved_at.replace("Z", "+00:00"))
            if retrieved_at_dt.tzinfo is None:
                retrieved_at_dt = retrieved_at_dt.replace(tzinfo=timezone.utc)
        except ValueError:
            pass
    published_at_dt = None
    if published_at:
        try:
            published_at_dt = datetime.fromisoformat(published_at.replace("Z", "+00:00"))
            if published_at_dt.tzinfo is None:
                published_at_dt = published_at_dt.replace(tzinfo=timezone.utc)
        except ValueError:
            published_at_dt = None
    retrieved_at = retrieved_at_dt.isoformat()

    points = []
    current_section = ""
    current_page = None
    for index, (chunk, dense) in enumerate(zip(chunks, dense_vectors)):
        headings = re.findall(r"(?m)^#{1,6}\s+(.+)$", chunk)
        for heading in headings:
            page_match = re.match(r"(?i)Страница\s+(\d+)", heading.strip())
            if page_match:
                current_page = page_match.group(1)
            else:
                current_section = heading.strip()
        page_number = current_page
        point_id = str(uuid5(NAMESPACE_URL, f"{source_id}:{version}:{index}"))
        sparse = sparse_passage_embed(chunk)
        points.append(models.PointStruct(
            id=point_id,
            vector={
                "dense": dense,
                "bm25": models.SparseVector(**sparse),
            },
            payload={
                "source_id": source_id,
                "scope": "cft",
                "version": version,
                "chunk_index": index,
                "chunk_text": chunk,
                "source_uri": source_uri,
                "source_file": source_file or "",
                "title": title,
                "section": current_section,
                "page": page_number,
                "retrieved_at": retrieved_at,
                "published_at": published_at_dt.isoformat() if published_at_dt else None,
                "product_version": (metadata or {}).get("product_version") or "",
                "code": (metadata or {}).get("code") or "",
                "item_type": (metadata or {}).get("item_type") or "",
                "catalog": (metadata or {}).get("catalog") or "",
                "is_active": False,
            },
        ))

    # Phase 1: stage the complete revision without changing active search results.
    client.upsert(collection_name=settings.qdrant_collection, points=points, wait=True)

    # Phase 2: switch the Qdrant alias-like active flag and persist the registry.
    # If any step fails, compensate by restoring the previous active revision.
    try:
        _set_active(client, source_id, version, True)
        if old_version and old_version != version:
            _set_active(client, source_id, old_version, False)

        with SessionLocal() as db:
            doc = db.get(Document, source_id)
            if doc is None:
                doc = Document(
                    source_id=source_id,
                    source_uri=source_uri,
                    active_version=version,
                    title=title,
                    source_file=source_file or "",
                    scope="cft",
                    retrieved_at=retrieved_at_dt,
                    published_at=published_at_dt,
                )
                db.add(doc)
            else:
                doc.active_version = version
                doc.source_uri = source_uri
                doc.title = title
                doc.source_file = source_file or doc.source_file
                doc.scope = "cft"
                doc.retrieved_at = retrieved_at_dt
                doc.published_at = published_at_dt

            if old_version and old_version != version:
                previous_row = db.scalar(select(DocumentVersion).where(
                    DocumentVersion.source_id == source_id,
                    DocumentVersion.version == old_version,
                ))
                if previous_row is not None:
                    previous_row.status = "superseded"

            version_row = db.scalar(select(DocumentVersion).where(
                DocumentVersion.source_id == source_id,
                DocumentVersion.version == version,
            ))
            if version_row is None:
                version_row = DocumentVersion(
                    source_id=source_id,
                    version=version,
                    status="active",
                    chunk_count=len(chunks),
                    retrieved_at=retrieved_at_dt,
                    published_at=published_at_dt,
                )
                db.add(version_row)
            else:
                version_row.status = "active"
                version_row.chunk_count = len(chunks)
                version_row.retrieved_at = retrieved_at_dt
                version_row.published_at = published_at_dt
            db.commit()
    except Exception:
        if old_version and old_version != version:
            try:
                _set_active(client, source_id, old_version, True)
            except Exception:
                pass
        try:
            _set_active(client, source_id, version, False)
        except Exception:
            pass
        raise

    return {"source_id": source_id, "version": version, "chunks": len(chunks), "unchanged": False}
