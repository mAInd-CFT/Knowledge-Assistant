from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.db.postgres import Base
from app.db import models  # noqa: F401
from app.core.session_store import add_message, create_session, get_history, get_request_result, get_session, list_sessions


def test_session_history_and_owner_isolation():
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    factory = sessionmaker(bind=engine, expire_on_commit=False)
    with factory() as db:
        first = create_session(db, "client-a")
        add_message(db, first.id, "user", "Привет")
        add_message(db, first.id, "assistant", "Здравствуйте")
        assert get_session(db, first.id, "client-a") is not None
        assert get_session(db, first.id, "client-b") is None
        assert [m["role"] for m in get_history(db, first.id)] == ["user", "assistant"]
        assert len(list_sessions(db, "client-a")) == 1
        assert list_sessions(db, "client-b") == []


def test_request_idempotency_result_is_saved():
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    factory = sessionmaker(bind=engine, expire_on_commit=False)
    with factory() as db:
        session = create_session(db, "client-a")
        request_id = "a2f8f2f0-6f26-4b8e-9f3c-4f39a5c4f4b1"
        add_message(db, session.id, "user", "Вопрос", request_id=request_id)
        add_message(db, session.id, "assistant", "Ответ", [{"title": "Источник"}], request_id, True)
        result = get_request_result(db, session.id, request_id)
        assert result is not None
        assert result.content == "Ответ"
        assert result.needs_clarification is True
