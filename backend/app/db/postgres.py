from sqlalchemy import create_engine, text
from sqlalchemy.orm import DeclarativeBase, sessionmaker

from app.config import settings


class Base(DeclarativeBase):
    pass


engine = create_engine(
    settings.database_url,
    pool_pre_ping=True,
    pool_size=5,
    max_overflow=10,
    pool_timeout=30,
    pool_recycle=1800,
    connect_args={"connect_timeout": 5},
)

SessionLocal = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)


def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


def init_db() -> None:
    from app.db import models  # noqa: F401
    Base.metadata.create_all(bind=engine)
    if engine.dialect.name == "postgresql":
        # Additive, idempotent upgrades for existing Docker volumes.
        upgrades = {
            "chat_messages": {
                "request_id": "VARCHAR(36) NULL",
                "needs_clarification": "BOOLEAN NOT NULL DEFAULT FALSE",
            },
            "documents": {
                "source_file": "VARCHAR(500) NOT NULL DEFAULT ''",
                "scope": "VARCHAR(32) NOT NULL DEFAULT 'unclassified'",
                "retrieved_at": "TIMESTAMP WITH TIME ZONE NOT NULL DEFAULT NOW()",
                "published_at": "TIMESTAMP WITH TIME ZONE NULL",
            },
            "document_versions": {
                "retrieved_at": "TIMESTAMP WITH TIME ZONE NOT NULL DEFAULT NOW()",
                "published_at": "TIMESTAMP WITH TIME ZONE NULL",
            },
        }
        with engine.begin() as connection:
            for table, columns in upgrades.items():
                for column, definition in columns.items():
                    connection.execute(text(
                        f'ALTER TABLE "{table}" ADD COLUMN IF NOT EXISTS "{column}" {definition}'
                    ))
            connection.execute(text(
                "CREATE INDEX IF NOT EXISTS ix_chat_messages_request_id ON chat_messages (request_id)"
            ))


def database_ready() -> bool:
    with engine.connect() as connection:
        connection.execute(text("SELECT 1"))
    return True
