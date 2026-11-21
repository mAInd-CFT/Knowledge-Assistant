from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from app.api import chat, client, knowledge, session
from app.core.client_auth import validate_client_auth_config
from app.config import settings
from app.db.postgres import database_ready, init_db
from app.retrieval.retriever import get_qdrant
from app.schemas.chat import HealthResponse


@asynccontextmanager
async def lifespan(app: FastAPI):
    # Fail fast for missing/weak browser-token signing configuration.
    validate_client_auth_config()
    init_db()
    yield


app = FastAPI(
    title=settings.app_name,
    version=settings.app_version,
    description="Информационный RAG-ассистент по продуктам, технологиям и материалам АО «ЦФТ».",
    docs_url="/docs" if settings.app_env != "production" else None,
    redoc_url=None,
    lifespan=lifespan,
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_origins_list,
    allow_credentials=False,
    allow_methods=["GET", "POST", "OPTIONS"],
    allow_headers=["Content-Type", "X-Client-Token"],
)

app.include_router(client.router, prefix=settings.api_prefix)
app.include_router(chat.router, prefix=settings.api_prefix)
app.include_router(session.router, prefix=settings.api_prefix)
app.include_router(knowledge.router, prefix=settings.api_prefix)


@app.get("/", tags=["system"])
def root():
    return {"message": f"{settings.app_name} is running", "health": "/health/ready"}


@app.get("/health/live", tags=["system"])
def live():
    return {"status": "ok"}


@app.get("/health/ready", tags=["system"])
def ready():
    try:
        database_ready()
        get_qdrant().get_collections()
        llm_configured = bool(settings.deepseek_api_key.get_secret_value())
        if not llm_configured:
            raise RuntimeError("DEEPSEEK_API_KEY is not configured")
        return {"status": "ready", "dependencies": {"postgres": "ok", "qdrant": "ok", "llm_configured": True}}
    except Exception:
        return JSONResponse(
            status_code=503,
            content={"status": "not_ready", "dependencies": "one or more dependencies unavailable"},
        )


@app.get("/health", response_model=HealthResponse, tags=["system"])
def health():
    return HealthResponse(status="ok", app=settings.app_name, version=settings.app_version)
