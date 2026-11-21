from uuid import UUID

from app.core.client_auth import client_owner

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.orm import Session

from app.core.session_store import create_session, get_messages, get_session, list_sessions
from app.db.postgres import get_db
from app.schemas.chat import MessageResponse, SessionResponse

router = APIRouter(prefix="/sessions", tags=["sessions"])




@router.post("", response_model=SessionResponse, status_code=status.HTTP_201_CREATED)
def create_chat_session(owner_id: str = Depends(client_owner), db: Session = Depends(get_db)):
    item = create_session(db, owner_id)
    return SessionResponse(
        session_id=UUID(item.id), created_at=item.created_at, title=item.title
    )


@router.get("", response_model=list[SessionResponse])
def get_chat_sessions(owner_id: str = Depends(client_owner), db: Session = Depends(get_db)):
    return [
        SessionResponse(
            session_id=UUID(item.id), created_at=item.created_at, title=item.title
        )
        for item in list_sessions(db, owner_id)
    ]


@router.get("/{session_id}", response_model=SessionResponse)
def get_chat_session(
    session_id: UUID,
    owner_id: str = Depends(client_owner),
    db: Session = Depends(get_db),
):
    item = get_session(db, session_id, owner_id)
    if item is None:
        raise HTTPException(status_code=404, detail="Session not found")
    return SessionResponse(
        session_id=session_id, created_at=item.created_at, title=item.title
    )


@router.get("/{session_id}/history", response_model=list[MessageResponse])
def session_history(
    session_id: UUID,
    owner_id: str = Depends(client_owner),
    db: Session = Depends(get_db),
):
    item = get_session(db, session_id, owner_id)
    if item is None:
        raise HTTPException(status_code=404, detail="Session not found")
    return [
        MessageResponse(
            message_id=UUID(row.id), session_id=session_id, role=row.role,
            content=row.content, sources=row.sources or [], created_at=row.created_at,
        )
        for row in get_messages(db, session_id, limit=200)
    ]
