from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import FileResponse
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.db.models import Document, DocumentVersion
from app.db.postgres import get_db

router = APIRouter(prefix="/knowledge", tags=["knowledge"])
RAW_DIR = Path("/app/data/raw").resolve()


@router.get("/stats")
def knowledge_stats(db: Session = Depends(get_db)):
    cft_sources = select(Document.source_id).where(Document.scope == "cft")
    documents = db.scalar(select(func.count()).select_from(Document).where(Document.scope == "cft")) or 0
    active_versions = db.scalar(
        select(func.count()).select_from(DocumentVersion).where(
            DocumentVersion.status == "active",
            DocumentVersion.source_id.in_(cft_sources),
        )
    ) or 0
    chunks = db.scalar(
        select(func.coalesce(func.sum(DocumentVersion.chunk_count), 0))
        .where(
            DocumentVersion.status == "active",
            DocumentVersion.source_id.in_(cft_sources),
        )
    ) or 0
    latest = db.scalar(select(func.max(Document.updated_at)).where(Document.scope == "cft"))
    return {
        "scope": "АО «Центр Финансовых Технологий» (ЦФТ)",
        "documents": documents,
        "active_versions": active_versions,
        "chunks": int(chunks),
        "updated_at": latest.isoformat() if latest else None,
    }


@router.get("/sources")
def knowledge_sources(db: Session = Depends(get_db)):
    rows = db.scalars(select(Document).where(Document.scope == "cft").order_by(Document.updated_at.desc()).limit(200))
    return [
        {
            "source_id": row.source_id,
            "title": row.title,
            "source": row.source_uri if row.source_uri.startswith(("http://", "https://")) else None,
            "file_name": row.source_file or None,
            "version": row.active_version,
            "updated_at": row.updated_at.isoformat() if row.updated_at else None,
            "retrieved_at": row.retrieved_at.isoformat() if row.retrieved_at else None,
            "published_at": row.published_at.isoformat() if row.published_at else None,
        }
        for row in rows
    ]



@router.get("/files/{source_id}")
def download_knowledge_file(source_id: str, db: Session = Depends(get_db)):
    if len(source_id) != 64 or any(char not in "0123456789abcdef" for char in source_id.lower()):
        raise HTTPException(status_code=404, detail="Source not found")
    row = db.get(Document, source_id)
    if row is None or row.scope != "cft" or not row.source_file:
        raise HTTPException(status_code=404, detail="Source not found")
    candidate = (RAW_DIR / row.source_file).resolve()
    if RAW_DIR not in candidate.parents or not candidate.is_file():
        raise HTTPException(status_code=404, detail="Source file is unavailable")
    return FileResponse(
        candidate,
        filename=candidate.name,
        media_type="application/octet-stream",
    )
