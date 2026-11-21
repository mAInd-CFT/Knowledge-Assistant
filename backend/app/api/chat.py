import asyncio
import logging
import time
from uuid import UUID

from app.core.client_auth import client_owner

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session
from starlette.concurrency import run_in_threadpool

from app.agent.guards import normalize_user_query
from app.agent.orchestrator import handle_chat
from app.core.session_store import add_message, create_session, get_history, get_request_result, get_session
from app.db.postgres import get_db
from app.schemas.chat import ChatRequest, ChatResponse

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/chat", tags=["chat"])
_session_locks: dict[str, asyncio.Lock] = {}




@router.post("", response_model=ChatResponse)
async def send_message(
    payload: ChatRequest,
    owner_id: str = Depends(client_owner),
    db: Session = Depends(get_db),
) -> ChatResponse:
    try:
        question = normalize_user_query(payload.message)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc

    session = await run_in_threadpool(get_session, db, payload.session_id, owner_id) if payload.session_id else None
    if payload.session_id and session is None:
        raise HTTPException(status_code=404, detail="Session not found")
    if session is None:
        session = await run_in_threadpool(create_session, db, owner_id)

    session_id = UUID(session.id)
    # Serializes concurrent turns in the same session within this API process.
    # The default Compose deployment runs one backend replica; multi-replica
    # deployments must use a shared DB advisory lock before enabling >1 replica.
    lock = _session_locks.setdefault(str(session_id), asyncio.Lock())
    started_at = time.perf_counter()
    async with lock:
        cached = await run_in_threadpool(get_request_result, db, session_id, payload.request_id)
        if cached is not None:
            return ChatResponse(
                answer=cached.content, session_id=session_id, sources=cached.sources or [],
                rag_enabled=True, needs_clarification=cached.needs_clarification, processing_time_ms=None,
            )
        history = await run_in_threadpool(get_history, db, session_id, 10)
        if not history or history[-1].get("content") != question or history[-1].get("role") != "user":
            await run_in_threadpool(add_message, db, session_id, "user", question, None, payload.request_id)
        else:
            # Retry of an interrupted request: reuse the existing user turn.
            history = history[:-1]
        try:
            result = await handle_chat(question, history)
        except ValueError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc
        except Exception as exc:
            logger.exception("Chat generation failed", extra={"session_id": str(session_id), "request_id": str(payload.request_id)})
            raise HTTPException(
                status_code=503,
                detail="Не удалось обработать запрос. Проверьте доступность модели и сервисов поиска; повторите попытку.",
            ) from exc
        await run_in_threadpool(add_message, db, session_id, "assistant", result["answer"], result["sources"], payload.request_id, result["needs_clarification"])

    elapsed_ms = round((time.perf_counter() - started_at) * 1000)
    logger.info(
        "chat.completed",
        extra={"session_id": str(session_id), "request_id": str(payload.request_id),
               "source_count": len(result["sources"]),
               "source_ids": [item.get("source_id") for item in result["sources"] if item.get("source_id")],
               "processing_time_ms": elapsed_ms, "needs_clarification": result["needs_clarification"]},
    )
    return ChatResponse(
        answer=result["answer"],
        session_id=session_id,
        sources=result["sources"],
        rag_enabled=True,
        needs_clarification=result["needs_clarification"],
        processing_time_ms=elapsed_ms,
    )
