from functools import lru_cache
from pathlib import Path
from typing import Literal

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore",
    )

    # Application
    app_name: str = "redactly"
    app_env: Literal["development", "production"] = "development"
    api_v1_prefix: str = "/api/v1"

    # Database
    database_url: str = Field(
        default="postgresql+psycopg://redactly:redactly@postgres:5432/redactly"
    )

    # Redis / Celery
    redis_url: str = Field(default="redis://redis:6379/0")
    celery_broker_url: str | None = None  # falls back to redis_url
    celery_result_backend: str | None = None  # falls back to redis_url

    # Auth
    jwt_secret: str = Field(default="dev-secret-change-me")
    jwt_algorithm: str = "HS256"
    jwt_access_ttl_minutes: int = 15
    jwt_refresh_ttl_days: int = 7

    # Storage
    storage_backend: Literal["local", "s3", "minio"] = "local"
    storage_local_root: Path = Path("/var/lib/redactly/blobs")
    storage_s3_bucket: str | None = None
    storage_s3_endpoint: str | None = None  # for MinIO
    storage_s3_region: str = "us-east-1"
    storage_s3_access_key: str | None = None
    storage_s3_secret_key: str | None = None

    # Limits
    upload_max_file_bytes: int = 100 * 1024 * 1024
    upload_max_job_bytes: int = 500 * 1024 * 1024

    # Tenant defaults (used when bootstrap creates the default tenant)
    default_tenant_retention_hours: int = 24

    # Ollama (used by Plan 3; declared here for completeness)
    ollama_host: str = "http://ollama:11434"
    ollama_model: str = "phi3.5:mini-instruct-q4_K_M"
    llm_verifier_enabled: bool = True

    @property
    def broker_url(self) -> str:
        return self.celery_broker_url or self.redis_url

    @property
    def result_backend(self) -> str:
        return self.celery_result_backend or self.redis_url


@lru_cache
def get_settings() -> Settings:
    return Settings()
