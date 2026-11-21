from datetime import datetime, timedelta, timezone
from uuid import UUID

from sqlalchemy import delete, select
from sqlalchemy.orm import Session

from app.db.models import ChatMessage, ChatSession
from app.config import settings


def _expiry_cutoff():
    return datetime.now(timezone.utc) - timedelta(hours=max(1, settings.session_ttl_hours))


def cleanup_expired_sessions(db: Session) -> int:
    result = db.execute(delete(ChatSession).where(ChatSession.updated_at < _expiry_cutoff()))
    db.commit()
    return int(result.rowcount or 0)


def create_session(db: Session, owner_id: str) -> ChatSession:
    cleanup_expired_sessions(db)
    session = ChatSession(owner_id=owner_id)
    db.add(session)
    db.commit()
    db.refresh(session)
    return session


def get_session(db: Session, session_id: UUID | str, owner_id: str) -> ChatSession | None:
    stmt = select(ChatSession).where(
        ChatSession.id == str(session_id),
        ChatSession.owner_id == owner_id,
        ChatSession.updated_at >= _expiry_cutoff(),
    )
    return db.scalar(stmt)


def list_sessions(db: Session, owner_id: str, limit: int = 50) -> list[ChatSession]:
    stmt = (
        select(ChatSession)
        .where(ChatSession.owner_id == owner_id, ChatSession.updated_at >= _expiry_cutoff())
        .order_by(ChatSession.updated_at.desc())
        .limit(min(max(limit, 1), 100))
    )
    return list(db.scalars(stmt))


def add_message(db: Session, session_id: UUID | str, role: str, content: str, sources=None, request_id: UUID | str | None = None, needs_clarification: bool = False) -> ChatMessage:
    message = ChatMessage(
        session_id=str(session_id),
        role=role,
        content=content,
        sources=sources or [],
        request_id=str(request_id) if request_id else None,
        needs_clarification=needs_clarification,
    )
    db.add(message)
    session = db.get(ChatSession, str(session_id))
    if session:
        session.updated_at = datetime.now(timezone.utc)
        if role == "user" and session.title == "Новый диалог":
            session.title = content[:150]
    db.commit()
    db.refresh(message)
    return message


def get_history(db: Session, session_id: UUID | str, limit: int = 10) -> list[dict]:
    stmt = (
        select(ChatMessage)
        .where(ChatMessage.session_id == str(session_id))
        .order_by(ChatMessage.created_at.desc())
        .limit(limit)
    )
    rows = list(db.scalars(stmt))
    rows.reverse()
    return [{"role": row.role, "content": row.content} for row in rows]


def get_messages(db: Session, session_id: UUID | str, limit: int = 200) -> list[ChatMessage]:
    stmt = (
        select(ChatMessage)
        .where(ChatMessage.session_id == str(session_id))
        .order_by(ChatMessage.created_at.desc())
        .limit(min(max(limit, 1), 500))
    )
    rows = list(db.scalars(stmt))
    rows.reverse()
    return rows


def get_request_result(db: Session, session_id: UUID | str, request_id: UUID | str) -> ChatMessage | None:
    stmt = (
        select(ChatMessage)
        .where(
            ChatMessage.session_id == str(session_id),
            ChatMessage.request_id == str(request_id),
            ChatMessage.role == "assistant",
        )
        .order_by(ChatMessage.created_at.desc())
        .limit(1)
    )
    return db.scalar(stmt)
