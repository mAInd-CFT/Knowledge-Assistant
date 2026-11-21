from functools import lru_cache
from urllib.parse import quote_plus

from pydantic import SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    app_name: str = "CFT Knowledge Assistant API"
    app_version: str = "1.6.1-cft-rag-hardening"
    app_env: str = "production"
    api_prefix: str = "/api"
    cors_origins: str = "http://localhost:8080,http://127.0.0.1:8080"

    deepseek_api_key: SecretStr = SecretStr("")
    client_token_secret: SecretStr = SecretStr("")
    llm_base_url: str = "https://api.deepseek.com"
    llm_model: str = "deepseek-flash"
    llm_timeout_seconds: float = 45.0
    llm_max_retries: int = 2
    llm_max_concurrent: int = 4
    llm_circuit_breaker_failures: int = 3
    llm_circuit_breaker_cooldown_seconds: int = 20

    embedding_model: str = "intfloat/multilingual-e5-large"
    embedding_dim: int = 1024
    embedding_batch_size: int = 32
    embedding_threads: int = 2

    qdrant_host: str = "qdrant"
    qdrant_port: int = 6333
    qdrant_collection: str = "knowledge_chunks_e5_large_v2"

    postgres_user: str = "app"
    postgres_password: SecretStr = SecretStr("local-only")
    postgres_db: str = "agent"
    postgres_host: str = "postgres"
    postgres_port: int = 5432

    session_ttl_hours: int = 24
    retrieval_top_k: int = 8
    retrieval_min_score: float = 0.005
    rag_enabled: bool = True

    model_config = SettingsConfigDict(
        env_file=("../.env", ".env"),
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore",
    )

    @property
    def cors_origins_list(self) -> list[str]:
        return [item.strip() for item in self.cors_origins.split(",") if item.strip()]

    @property
    def database_url(self) -> str:
        password = quote_plus(self.postgres_password.get_secret_value())
        return (
            f"postgresql+psycopg2://{self.postgres_user}:{password}"
            f"@{self.postgres_host}:{self.postgres_port}/{self.postgres_db}"
        )


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    return Settings()


settings = get_settings()
