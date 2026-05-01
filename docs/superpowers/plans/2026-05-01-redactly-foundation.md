# Foundation Implementation Plan

> **For agentic workers:** REQUIRED: Use superpowers:subagent-driven-development (if subagents available) or superpowers:executing-plans to implement this plan. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Stand up the V2 application skeleton — FastAPI service, Postgres + Alembic, Celery + Redis, blob storage abstraction, basic auth (local password + API keys), structured logging, Docker Compose stack, bootstrap CLI — so subsequent plans (parsing, detection, redaction, APIs, frontend) can build on solid ground.

**Architecture:** Replace the current Flask + ad-hoc start.sh setup with a FastAPI + Celery + Postgres stack orchestrated by a single Docker Compose file. The new server lives at `server/` (the legacy tree is moved to `server.legacy/` for reference). Sync SQLAlchemy 2.0 + psycopg v3 throughout (no asyncpg/Celery split). Argon2 password hashing + HS256 JWT for auth (RS256 is a V2 follow-up if asymmetric verification is needed for federated services). The full DB schema from spec §5.2 lands in the first migration so V2 multi-tenant work needs no historical data migration.

**Tech Stack:** Python 3.12 · FastAPI · Uvicorn · SQLAlchemy 2.0 · psycopg 3 · Alembic · Celery · Redis 7 · Postgres 16 · Argon2 · `python-jose[cryptography]` · pydantic-settings · structlog · slowapi · Typer (CLI) · uv (deps) · Docker Compose · Nginx.

**Reference spec:** `docs/superpowers/specs/2026-05-01-redactly-overhaul-design.md`

**Out of scope (later plans):**
- OIDC SSO → Plan 5 (Job APIs)
- S3/MinIO blob backend impls → leave interface clean; ship local FS only in V1.0
- Ollama / detection / parsing / redaction → Plans 2-4
- HITL frontend → Plan 6

**Prerequisites for the executor:**
- Docker + Docker Compose v2 installed.
- `uv` installed (`pip install uv` if missing) — modern Python dep manager; replaces pip-tools/poetry.
- Read the spec referenced above before starting.

---

## File Structure

| Path | Responsibility |
|---|---|
| `server.legacy/` | Old Flask code, retained read-only for cribbing logic in later plans |
| `server/pyproject.toml` | Dep declarations + project metadata (replaces `requirements.txt`) |
| `server/uv.lock` | Resolved deps (committed) |
| `server/Dockerfile` | Python 3.12-slim image with uv-installed deps; non-root user |
| `server/alembic.ini` | Alembic config |
| `server/cli.py` | Typer CLI: `admin create`, `migrate` |
| `server/app/main.py` | FastAPI app, lifespan, middleware, router wiring |
| `server/app/config.py` | pydantic-settings; one source of truth for runtime config |
| `server/app/logging.py` | structlog configuration; request-id middleware |
| `server/app/db/session.py` | SQLAlchemy engine + session factory + dependency |
| `server/app/db/models.py` | All ORM models from spec §5.2 |
| `server/app/db/migrations/` | Alembic migration env + versions |
| `server/app/core/auth.py` | Argon2 password hashing, JWT, role decorator, current_user dependency |
| `server/app/core/audit.py` | `write_audit_event(...)` helper |
| `server/app/core/storage.py` | `BlobStore` interface + `LocalBlobStore` impl |
| `server/app/core/security.py` | `RequireRole`, `RequireScope`, dependency wrappers |
| `server/app/core/rate_limit.py` | Module-level `slowapi` `Limiter` instance for handler-side decorators |
| `server/app/api/v1/__init__.py` | v1 router aggregator |
| `server/app/api/v1/auth.py` | `/auth/login`, `/auth/me` |
| `server/app/api/v1/admin.py` | `/admin/api-keys` create/list/revoke |
| `server/app/api/v1/health.py` | `/health`, `/metrics` |
| `server/app/workers/celery_app.py` | Celery config + Redis broker URL |
| `server/app/workers/tasks.py` | Placeholder noop task; real tasks land in later plans |
| `docker-compose.yml` | Stack: api · worker · beat · postgres · redis · ollama · nginx (S3/MinIO and frontend deferred to follow-up plans) |
| `compose.gpu.yml` | GPU overlay placeholder (filled in Plan 3) |
| `nginx/nginx.conf` | TLS termination + reverse proxy to api/frontend |
| `nginx/dev.crt`, `dev.key` | Self-signed cert for local TLS |
| `.env.example` | Every documented env var with sane defaults |
| `ops/backup.sh`, `ops/restore.sh` | pg_dump + blob snapshot helpers |
| `README.md` | Top-level docs replaced with V2 boot instructions |

---

## Task 1: Move legacy server out of the way

**Files:**
- Rename: `server/` → `server.legacy/`

- [ ] **Step 1: Rename and confirm git tracks the rename**

```bash
git mv server server.legacy
git status
```

Expected: `renamed: server/... -> server.legacy/...` for every file in the old tree.

- [ ] **Step 2: Add a one-line README inside `server.legacy/`**

Create `server.legacy/README.md`:

```markdown
# Legacy V1 server (read-only reference)

This is the V1 Flask + Presidio implementation, retained as a reference for cribbing
detection and redaction logic during the V2 build. Do not modify. The V2 server is at
`/server`. Once V2 reaches functional parity, this directory will be deleted.
```

- [ ] **Step 3: Commit**

```bash
git add server.legacy/README.md
git commit -m "chore: move legacy server to server.legacy/ for reference"
```

---

## Task 2: Initialize the new server tree

**Files:**
- Create: empty `server/` with package skeleton.

- [ ] **Step 1: Create the directory structure**

```bash
mkdir -p server/app/api/v1 \
         server/app/core \
         server/app/db/migrations \
         server/app/workers \
         server/ops \
         nginx
touch server/app/__init__.py \
      server/app/api/__init__.py \
      server/app/api/v1/__init__.py \
      server/app/core/__init__.py \
      server/app/db/__init__.py \
      server/app/workers/__init__.py
```

- [ ] **Step 2: Verify the layout**

```bash
find server -type f -o -type d | sort
```

Expected: every path listed in the **File Structure** table for `server/...`, all empty except the `__init__.py` files.

- [ ] **Step 3: Commit**

```bash
git add server/
git commit -m "feat(foundation): scaffold V2 server package layout"
```

---

## Task 3: Add `pyproject.toml` and lock dependencies with uv

**Files:**
- Create: `server/pyproject.toml`

- [ ] **Step 1: Write `server/pyproject.toml`**

```toml
[project]
name = "redactly-server"
version = "0.2.0"
description = "RedactLy V2 server — privacy-first PII redaction"
requires-python = ">=3.12,<3.13"
dependencies = [
    "fastapi>=0.115,<0.116",
    "uvicorn[standard]>=0.32,<0.33",
    "pydantic>=2.9,<3",
    "pydantic-settings>=2.6,<3",
    "sqlalchemy>=2.0,<2.1",
    "psycopg[binary,pool]>=3.2,<3.3",
    "alembic>=1.14,<1.15",
    "celery[redis]>=5.4,<5.5",
    "redis>=5.2,<5.3",
    "argon2-cffi>=23.1,<24",
    "python-jose[cryptography]>=3.3,<4",
    "python-multipart>=0.0.20,<0.1",
    "structlog>=24.4,<25",
    "slowapi>=0.1.9,<0.2",
    "typer>=0.13,<0.14",
    "prometheus-client>=0.21,<0.22",
    "boto3>=1.35,<2",
]

[build-system]
requires = ["hatchling"]
build-backend = "hatchling.build"

[tool.hatch.build.targets.wheel]
packages = ["app"]
```

- [ ] **Step 2: Resolve and lock the dependencies**

```bash
cd server && uv lock && cd ..
```

Expected: `server/uv.lock` is created without errors. Look at the file briefly — it should reference the pinned versions above.

- [ ] **Step 3: Smoke-test the env**

```bash
cd server && uv sync && uv run python -c "import fastapi, sqlalchemy, celery, structlog; print('ok')" && cd ..
```

Expected: stdout prints `ok`. If any import fails, the offending package needs version pinning attention.

- [ ] **Step 4: Commit**

```bash
git add server/pyproject.toml server/uv.lock
git commit -m "feat(foundation): add pyproject.toml + uv lock"
```

---

## Task 4: Configuration via pydantic-settings

**Files:**
- Create: `server/app/config.py`
- Create: `.env.example` (root)

- [ ] **Step 1: Write `server/app/config.py`**

```python
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
```

- [ ] **Step 2: Write `.env.example` at the repo root**

```bash
APP_ENV=development

DATABASE_URL=postgresql+psycopg://redactly:redactly@postgres:5432/redactly
REDIS_URL=redis://redis:6379/0

# 32+ random chars in production; rotate on incident
JWT_SECRET=dev-secret-change-me
JWT_ALGORITHM=HS256
JWT_ACCESS_TTL_MINUTES=15
JWT_REFRESH_TTL_DAYS=7

STORAGE_BACKEND=local
STORAGE_LOCAL_ROOT=/var/lib/redactly/blobs
# When STORAGE_BACKEND=s3 or minio, fill these:
# STORAGE_S3_BUCKET=
# STORAGE_S3_ENDPOINT=
# STORAGE_S3_REGION=us-east-1
# STORAGE_S3_ACCESS_KEY=
# STORAGE_S3_SECRET_KEY=

UPLOAD_MAX_FILE_BYTES=104857600
UPLOAD_MAX_JOB_BYTES=524288000

DEFAULT_TENANT_RETENTION_HOURS=24

# Ollama (used in Plan 3)
OLLAMA_HOST=http://ollama:11434
OLLAMA_MODEL=phi3.5:mini-instruct-q4_K_M
LLM_VERIFIER_ENABLED=true
```

- [ ] **Step 3: Smoke-test loading**

```bash
cd server && uv run python -c "from app.config import get_settings; s = get_settings(); print(s.database_url, s.storage_backend)" && cd ..
```

Expected: prints `postgresql+psycopg://...` and `local`.

- [ ] **Step 4: Commit**

```bash
git add server/app/config.py .env.example
git commit -m "feat(foundation): add typed runtime config via pydantic-settings"
```

---

## Task 5: Database session and engine

**Files:**
- Create: `server/app/db/session.py`

- [ ] **Step 1: Write `server/app/db/session.py`**

```python
from collections.abc import Generator

from sqlalchemy import create_engine
from sqlalchemy.orm import DeclarativeBase, Session, sessionmaker

from app.config import get_settings


class Base(DeclarativeBase):
    pass


_settings = get_settings()
engine = create_engine(
    _settings.database_url,
    pool_pre_ping=True,
    pool_size=10,
    max_overflow=20,
    future=True,
)
SessionLocal = sessionmaker(bind=engine, autoflush=False, autocommit=False, future=True)


def get_db() -> Generator[Session, None, None]:
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()
```

- [ ] **Step 2: Smoke-test the import**

```bash
cd server && uv run python -c "from app.db.session import Base, engine, get_db; print('ok')" && cd ..
```

Expected: prints `ok`. (Not yet connecting to DB — that requires Postgres running, which lands in Task 19.)

- [ ] **Step 3: Commit**

```bash
git add server/app/db/session.py
git commit -m "feat(foundation): add SQLAlchemy session + engine"
```

---

## Task 6: Database models — full schema from spec §5.2

**Files:**
- Create: `server/app/db/models.py`

- [ ] **Step 1: Write `server/app/db/models.py`**

```python
from datetime import datetime, timezone
from enum import StrEnum
from uuid import UUID, uuid4

from sqlalchemy import (
    JSON,
    BigInteger,
    Boolean,
    CheckConstraint,
    DateTime,
    Enum as SAEnum,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
    func,
)
from sqlalchemy.dialects.postgresql import ARRAY, INET, JSONB, UUID as PG_UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.session import Base


def _now_utc() -> datetime:
    return datetime.now(timezone.utc)


class JobStatus(StrEnum):
    PENDING = "pending"
    PARSING = "parsing"
    DETECTING = "detecting"
    AWAITING_REVIEW = "awaiting_review"
    REDACTING = "redacting"
    VERIFYING = "verifying"
    COMPLETE = "complete"
    EXPIRED = "expired"
    FAILED = "failed"
    CANCELLED = "cancelled"


class UserRole(StrEnum):
    ADMIN = "admin"
    REVIEWER = "reviewer"
    VIEWER = "viewer"


class ApprovalState(StrEnum):
    PENDING = "pending"
    APPROVED = "approved"
    REJECTED = "rejected"
    EDITED = "edited"


class ConfidenceBucket(StrEnum):
    HIGH = "high"
    MED = "med"
    LOW = "low"


class SourceLayer(StrEnum):
    RULES = "rules"
    NER = "ner"
    LLM = "llm"
    KEYWORD = "keyword"


class ActorType(StrEnum):
    USER = "user"
    SYSTEM = "system"
    API_KEY = "api_key"


class Tenant(Base):
    __tablename__ = "tenants"

    id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True), primary_key=True, default=uuid4)
    name: Mapped[str] = mapped_column(String(255), nullable=False, unique=True)
    settings: Mapped[dict] = mapped_column(JSONB, nullable=False, default=dict)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )

    users: Mapped[list["User"]] = relationship(back_populates="tenant")


class User(Base):
    __tablename__ = "users"
    __table_args__ = (
        UniqueConstraint("tenant_id", "email", name="uq_users_tenant_email"),
        UniqueConstraint("oidc_sub", name="uq_users_oidc_sub"),
    )

    id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True), primary_key=True, default=uuid4)
    tenant_id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True), ForeignKey("tenants.id", ondelete="RESTRICT"), nullable=False
    )
    email: Mapped[str] = mapped_column(String(320), nullable=False)
    hashed_password: Mapped[str | None] = mapped_column(String(255), nullable=True)
    oidc_sub: Mapped[str | None] = mapped_column(String(255), nullable=True)
    role: Mapped[UserRole] = mapped_column(
        SAEnum(UserRole, name="user_role"), nullable=False, default=UserRole.VIEWER
    )
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    last_login_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    tenant: Mapped[Tenant] = relationship(back_populates="users")


class ApiKey(Base):
    __tablename__ = "api_keys"

    id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True), primary_key=True, default=uuid4)
    tenant_id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True), ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False
    )
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    # `lookup_prefix` is the first ~8 characters of the plaintext key after the
    # `rk_` prefix. It is stored unhashed so that authentication can fetch the
    # one matching row (O(1)) and then run a single Argon2 verify, instead of
    # Argon2-verifying every non-revoked row in the table on every request.
    lookup_prefix: Mapped[str] = mapped_column(
        String(16), nullable=False, unique=True, index=True
    )
    hashed_key: Mapped[str] = mapped_column(String(255), nullable=False, unique=True)
    scopes: Mapped[list[str]] = mapped_column(ARRAY(String), nullable=False, default=list)
    created_by: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL"), nullable=False
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class Job(Base):
    __tablename__ = "jobs"
    __table_args__ = (
        Index("ix_jobs_tenant_status_created", "tenant_id", "status", "created_at"),
    )

    id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True), primary_key=True, default=uuid4)
    tenant_id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True), ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False
    )
    created_by: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL"), nullable=False
    )
    status: Mapped[JobStatus] = mapped_column(
        SAEnum(JobStatus, name="job_status"), nullable=False, default=JobStatus.PENDING
    )
    source_filename: Mapped[str] = mapped_column(String(512), nullable=False)
    source_blob_uri: Mapped[str] = mapped_column(Text, nullable=False)
    source_sha256: Mapped[str] = mapped_column(String(64), nullable=False)
    source_format: Mapped[str] = mapped_column(String(16), nullable=False)
    page_count: Mapped[int | None] = mapped_column(Integer, nullable=True)

    redacted_blob_uri: Mapped[str | None] = mapped_column(Text, nullable=True)
    redacted_sha256: Mapped[str | None] = mapped_column(String(64), nullable=True)

    config: Mapped[dict] = mapped_column(JSONB, nullable=False, default=dict)
    error: Mapped[dict | None] = mapped_column(JSONB, nullable=True)

    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        onupdate=func.now(),
        nullable=False,
    )


class Detection(Base):
    __tablename__ = "detections"
    __table_args__ = (
        Index("ix_detections_job_state", "job_id", "approval_state"),
        CheckConstraint("score >= 0.0 AND score <= 1.0", name="ck_detections_score_range"),
    )

    id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True), primary_key=True, default=uuid4)
    job_id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True), ForeignKey("jobs.id", ondelete="CASCADE"), nullable=False
    )
    entity_type: Mapped[str] = mapped_column(String(64), nullable=False)
    # `text` and `bbox` are nullable because the privacy-purge job described
    # in spec §5.7 nulls these fields when the source blob expires — the
    # detection text *is* the PII the system was redacting and must not
    # outlive its source.
    text: Mapped[str | None] = mapped_column(Text, nullable=True)
    page: Mapped[int] = mapped_column(Integer, nullable=False)
    bbox: Mapped[dict | None] = mapped_column(JSONB, nullable=True)
    source_layer: Mapped[SourceLayer] = mapped_column(
        SAEnum(SourceLayer, name="source_layer"), nullable=False
    )
    score: Mapped[float] = mapped_column(nullable=False)
    confidence_bucket: Mapped[ConfidenceBucket] = mapped_column(
        SAEnum(ConfidenceBucket, name="confidence_bucket"), nullable=False
    )
    evidence: Mapped[dict] = mapped_column(JSONB, nullable=False, default=dict)

    approval_state: Mapped[ApprovalState] = mapped_column(
        SAEnum(ApprovalState, name="approval_state"),
        nullable=False,
        default=ApprovalState.PENDING,
    )
    approved_text: Mapped[str | None] = mapped_column(Text, nullable=True)
    reviewed_by: Mapped[UUID | None] = mapped_column(
        PG_UUID(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
    reviewed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class AuditEvent(Base):
    __tablename__ = "audit_events"
    __table_args__ = (
        Index("ix_audit_tenant_created", "tenant_id", "created_at"),
    )

    id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True), primary_key=True, default=uuid4)
    tenant_id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True), ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False
    )
    actor_id: Mapped[UUID | None] = mapped_column(
        PG_UUID(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
    actor_type: Mapped[ActorType] = mapped_column(
        SAEnum(ActorType, name="actor_type"), nullable=False
    )
    action: Mapped[str] = mapped_column(String(128), nullable=False)
    target_type: Mapped[str] = mapped_column(String(64), nullable=False)
    target_id: Mapped[str | None] = mapped_column(String(128), nullable=True)
    ip_address: Mapped[str | None] = mapped_column(INET, nullable=True)
    user_agent: Mapped[str | None] = mapped_column(Text, nullable=True)
    payload: Mapped[dict] = mapped_column(JSONB, nullable=False, default=dict)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
```

- [ ] **Step 2: Smoke-test the import**

```bash
cd server && uv run python -c "from app.db.models import Tenant, User, ApiKey, Job, Detection, AuditEvent; print('ok')" && cd ..
```

Expected: `ok`.

- [ ] **Step 3: Commit**

```bash
git add server/app/db/models.py
git commit -m "feat(foundation): add full ORM schema (tenants, users, api_keys, jobs, detections, audit)"
```

---

## Task 7: Initialize Alembic and write the first migration

**Files:**
- Create: `server/alembic.ini`
- Create: `server/app/db/migrations/env.py`
- Create: `server/app/db/migrations/script.py.mako`
- Create: `server/app/db/migrations/versions/0001_initial.py`

- [ ] **Step 1: Init Alembic**

```bash
cd server && uv run alembic init -t generic app/db/migrations && cd ..
```

This creates `app/db/migrations/env.py`, `script.py.mako`, `versions/`, plus `alembic.ini`. Move `alembic.ini` to `server/alembic.ini` if it ended up at repo root.

- [ ] **Step 2: Edit `server/alembic.ini`**

Set `script_location = app/db/migrations` and remove the `sqlalchemy.url` line (the env will read it from settings). Keep the rest at defaults.

- [ ] **Step 3: Replace `server/app/db/migrations/env.py` with the version below**

`alembic init` writes its own `env.py` with a different shape (no settings import, hardcoded url). Discard it entirely — paste the file below in its place.

```python
from logging.config import fileConfig

from alembic import context
from sqlalchemy import engine_from_config, pool

from app.config import get_settings
from app.db.session import Base
from app.db import models  # noqa: F401  -- registers models on Base.metadata

config = context.config
if config.config_file_name is not None:
    fileConfig(config.config_file_name)

config.set_main_option("sqlalchemy.url", get_settings().database_url)
target_metadata = Base.metadata


def run_migrations_offline() -> None:
    context.configure(
        url=config.get_main_option("sqlalchemy.url"),
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
    )
    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    connectable = engine_from_config(
        config.get_section(config.config_ini_section, {}),
        prefix="sqlalchemy.",
        poolclass=pool.NullPool,
    )
    with connectable.connect() as connection:
        context.configure(connection=connection, target_metadata=target_metadata)
        with context.begin_transaction():
            context.run_migrations()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
```

- [ ] **Step 4: Generate the initial migration**

```bash
cd server && uv run alembic revision --autogenerate -m "initial schema" && cd ..
```

Expected: a new file in `server/app/db/migrations/versions/` (filename includes a hash + `_initial_schema.py`). Inspect it; it should `op.create_table(...)` for `tenants`, `users`, `api_keys`, `jobs`, `detections`, `audit_events`, plus `op.create_index(...)` calls. Rename the file to `0001_initial.py` if you want stable ordering.

- [ ] **Step 5: Verify migration is idempotent (offline render)**

```bash
cd server && uv run alembic upgrade head --sql > /tmp/migration.sql && head -30 /tmp/migration.sql && cd ..
```

Expected: SQL statements creating the six tables. (We can't apply yet — Postgres lands in Task 19.)

- [ ] **Step 6: Commit**

```bash
git add server/alembic.ini server/app/db/migrations/
git commit -m "feat(foundation): initialize Alembic + initial schema migration"
```

---

## Task 8: Blob storage abstraction + local FS implementation

**Files:**
- Create: `server/app/core/storage.py`

- [ ] **Step 1: Write `server/app/core/storage.py`**

```python
from __future__ import annotations

import hashlib
import shutil
from abc import ABC, abstractmethod
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import BinaryIO
from urllib.parse import urlencode
from uuid import uuid4

from app.config import Settings, get_settings


class BlobStore(ABC):
    @abstractmethod
    def put(self, key: str, data: BinaryIO) -> str:
        """Upload bytes; return the canonical URI (e.g. file:///... or s3://...)."""

    @abstractmethod
    def get(self, key: str) -> BinaryIO:
        """Open a binary stream for reading."""

    @abstractmethod
    def delete(self, key: str) -> None: ...

    @abstractmethod
    def signed_url(self, key: str, ttl_seconds: int = 300) -> str: ...

    @staticmethod
    def make_key(*parts: str) -> str:
        cleaned = [p.strip("/") for p in parts if p]
        cleaned.append(uuid4().hex)
        return "/".join(cleaned)

    @staticmethod
    def sha256(data: BinaryIO) -> str:
        h = hashlib.sha256()
        for chunk in iter(lambda: data.read(8192), b""):
            h.update(chunk)
        data.seek(0)
        return h.hexdigest()


class LocalBlobStore(BlobStore):
    def __init__(self, root: Path) -> None:
        self.root = root
        self.root.mkdir(parents=True, exist_ok=True)

    def _path(self, key: str) -> Path:
        # Disallow path traversal; keys are always relative.
        p = (self.root / key).resolve()
        if not str(p).startswith(str(self.root.resolve())):
            raise ValueError(f"key {key!r} resolves outside storage root")
        return p

    def put(self, key: str, data: BinaryIO) -> str:
        p = self._path(key)
        p.parent.mkdir(parents=True, exist_ok=True)
        with p.open("wb") as fh:
            shutil.copyfileobj(data, fh)
        return p.as_uri()

    def get(self, key: str) -> BinaryIO:
        return self._path(key).open("rb")

    def delete(self, key: str) -> None:
        try:
            self._path(key).unlink()
        except FileNotFoundError:
            pass

    def signed_url(self, key: str, ttl_seconds: int = 300) -> str:
        # Local backend has no real signing; we return a pseudo-signed URL the
        # API layer recognizes and proxies. Format: blob+local://<key>?exp=...
        exp = int((datetime.now(timezone.utc) + timedelta(seconds=ttl_seconds)).timestamp())
        return f"blob+local://{key}?{urlencode({'exp': exp})}"


def get_blob_store(settings: Settings | None = None) -> BlobStore:
    s = settings or get_settings()
    if s.storage_backend == "local":
        return LocalBlobStore(s.storage_local_root)
    raise NotImplementedError(
        f"storage_backend={s.storage_backend} is not implemented in V1.0; "
        "add S3/MinIO impls in a follow-up plan when needed"
    )
```

- [ ] **Step 2: Smoke-test the local store**

```bash
cd server && uv run python -c "
from io import BytesIO
from pathlib import Path
import tempfile
from app.core.storage import LocalBlobStore

with tempfile.TemporaryDirectory() as tmp:
    store = LocalBlobStore(Path(tmp))
    uri = store.put('jobs/test.txt', BytesIO(b'hello'))
    assert uri.startswith('file://')
    got = store.get('jobs/test.txt').read()
    assert got == b'hello', got
    print('ok', uri)
" && cd ..
```

Expected: prints `ok file://...`.

- [ ] **Step 3: Commit**

```bash
git add server/app/core/storage.py
git commit -m "feat(foundation): add BlobStore abstraction + local FS impl"
```

---

## Task 9: Auth core — Argon2 + JWT + role decorator + rate limiter

**Files:**
- Create: `server/app/core/auth.py`
- Create: `server/app/core/security.py`
- Create: `server/app/core/rate_limit.py`

- [ ] **Step 1: Write `server/app/core/auth.py`**

```python
from datetime import datetime, timedelta, timezone
from typing import Any
from uuid import UUID

from argon2 import PasswordHasher
from argon2.exceptions import VerifyMismatchError
from jose import JWTError, jwt
from pydantic import BaseModel

from app.config import get_settings

_ph = PasswordHasher()


def hash_password(password: str) -> str:
    return _ph.hash(password)


def verify_password(password: str, hashed: str) -> bool:
    try:
        _ph.verify(hashed, password)
    except VerifyMismatchError:
        return False
    return True


class TokenClaims(BaseModel):
    sub: str  # user uuid
    tenant: str  # tenant uuid
    role: str
    type: str  # "access" | "refresh"
    exp: int
    iat: int


def issue_access_token(user_id: UUID, tenant_id: UUID, role: str) -> str:
    s = get_settings()
    now = datetime.now(timezone.utc)
    payload: dict[str, Any] = {
        "sub": str(user_id),
        "tenant": str(tenant_id),
        "role": role,
        "type": "access",
        "iat": int(now.timestamp()),
        "exp": int((now + timedelta(minutes=s.jwt_access_ttl_minutes)).timestamp()),
    }
    return jwt.encode(payload, s.jwt_secret, algorithm=s.jwt_algorithm)


def issue_refresh_token(user_id: UUID, tenant_id: UUID, role: str) -> str:
    s = get_settings()
    now = datetime.now(timezone.utc)
    payload: dict[str, Any] = {
        "sub": str(user_id),
        "tenant": str(tenant_id),
        "role": role,
        "type": "refresh",
        "iat": int(now.timestamp()),
        "exp": int((now + timedelta(days=s.jwt_refresh_ttl_days)).timestamp()),
    }
    return jwt.encode(payload, s.jwt_secret, algorithm=s.jwt_algorithm)


def decode_token(token: str) -> TokenClaims:
    s = get_settings()
    try:
        data = jwt.decode(token, s.jwt_secret, algorithms=[s.jwt_algorithm])
    except JWTError as e:
        raise ValueError(f"invalid token: {e}") from e
    return TokenClaims.model_validate(data)
```

- [ ] **Step 2: Write `server/app/core/security.py`**

```python
from typing import Annotated
from uuid import UUID

from argon2 import PasswordHasher
from argon2.exceptions import VerifyMismatchError
from fastapi import Depends, HTTPException, Request, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from sqlalchemy.orm import Session

from app.core.auth import decode_token
from app.db.models import ApiKey, User, UserRole
from app.db.session import get_db

_bearer = HTTPBearer(auto_error=False)
_ph = PasswordHasher()

# `rk_` + 8-char prefix + `_` + 32-char secret tail = 44 chars total.
# The first 8 characters after `rk_` are the lookup prefix used to fetch
# the single matching ApiKey row before running Argon2 verification.
API_KEY_PREFIX = "rk_"
API_KEY_LOOKUP_LEN = 8
API_KEY_MIN_LEN = len(API_KEY_PREFIX) + API_KEY_LOOKUP_LEN + 16  # safety floor


def _unauthorized(detail: str = "not authenticated") -> HTTPException:
    return HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail=detail)


def _forbidden(detail: str = "forbidden") -> HTTPException:
    return HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail=detail)


def _resolve_api_key(db: Session, plaintext: str) -> User:
    if not plaintext.startswith(API_KEY_PREFIX) or len(plaintext) < API_KEY_MIN_LEN:
        raise _unauthorized("invalid api key format")
    prefix = plaintext[len(API_KEY_PREFIX) : len(API_KEY_PREFIX) + API_KEY_LOOKUP_LEN]
    row = (
        db.query(ApiKey)
        .filter(ApiKey.lookup_prefix == prefix, ApiKey.revoked_at.is_(None))
        .one_or_none()
    )
    if not row:
        raise _unauthorized("invalid api key")
    try:
        _ph.verify(row.hashed_key, plaintext)
    except VerifyMismatchError as e:
        raise _unauthorized("invalid api key") from e
    user = (
        db.query(User)
        .filter(User.tenant_id == row.tenant_id, User.is_active.is_(True))
        .order_by(User.created_at)
        .first()
    )
    if not user:
        raise _unauthorized("api key has no associated user")
    return user


async def current_user(
    request: Request,
    creds: Annotated[HTTPAuthorizationCredentials | None, Depends(_bearer)],
    db: Annotated[Session, Depends(get_db)],
) -> User:
    api_key_header = request.headers.get("x-api-key")
    if api_key_header:
        return _resolve_api_key(db, api_key_header)

    if not creds or not creds.credentials:
        raise _unauthorized()
    try:
        claims = decode_token(creds.credentials)
    except ValueError as e:
        raise _unauthorized(str(e)) from e
    if claims.type != "access":
        raise _unauthorized("wrong token type")
    try:
        user_uuid = UUID(claims.sub)
    except ValueError as e:
        raise _unauthorized("invalid subject") from e
    user = db.get(User, user_uuid)
    if not user or not user.is_active:
        raise _unauthorized("user inactive or missing")
    return user


def require_role(*allowed: UserRole):
    async def _dep(user: Annotated[User, Depends(current_user)]) -> User:
        if user.role not in allowed:
            raise _forbidden(f"role {user.role} not in {[r.value for r in allowed]}")
        return user

    return _dep
```

- [ ] **Step 2b: Write `server/app/core/rate_limit.py`**

```python
from slowapi import Limiter
from slowapi.util import get_remote_address

# Module-level Limiter so handlers can decorate routes (e.g. @limiter.limit("10/minute"))
# without importing from app.main. main.py registers the exception handler.
limiter = Limiter(key_func=get_remote_address)
```

- [ ] **Step 3: Smoke-test password + JWT round-trip**

```bash
cd server && uv run python -c "
from uuid import uuid4
from app.core.auth import hash_password, verify_password, issue_access_token, decode_token

h = hash_password('s3cret!')
assert verify_password('s3cret!', h)
assert not verify_password('wrong', h)

uid, tid = uuid4(), uuid4()
tok = issue_access_token(uid, tid, 'admin')
claims = decode_token(tok)
assert claims.sub == str(uid) and claims.tenant == str(tid) and claims.role == 'admin'
print('ok')
" && cd ..
```

Expected: `ok`.

- [ ] **Step 4: Commit**

```bash
git add server/app/core/auth.py server/app/core/security.py server/app/core/rate_limit.py
git commit -m "feat(foundation): add Argon2 hashing, JWT issue/verify, role/api-key dependencies, rate limiter"
```

---

## Task 10: Audit event writer

**Files:**
- Create: `server/app/core/audit.py`

- [ ] **Step 1: Write `server/app/core/audit.py`**

```python
from typing import Any
from uuid import UUID

from sqlalchemy.orm import Session

from app.db.models import ActorType, AuditEvent


def write_audit_event(
    db: Session,
    *,
    tenant_id: UUID,
    actor_id: UUID | None,
    actor_type: ActorType,
    action: str,
    target_type: str,
    target_id: str | None = None,
    ip_address: str | None = None,
    user_agent: str | None = None,
    payload: dict[str, Any] | None = None,
) -> AuditEvent:
    """Append an audit row.

    Caller must commit the surrounding session. We never write PII text into
    payload — caller is responsible for redacting/hashing sensitive fields
    before passing them in (see spec §5.7).
    """
    row = AuditEvent(
        tenant_id=tenant_id,
        actor_id=actor_id,
        actor_type=actor_type,
        action=action,
        target_type=target_type,
        target_id=target_id,
        ip_address=ip_address,
        user_agent=user_agent,
        payload=payload or {},
    )
    db.add(row)
    db.flush()
    return row
```

- [ ] **Step 2: Smoke-test import**

```bash
cd server && uv run python -c "from app.core.audit import write_audit_event; print('ok')" && cd ..
```

- [ ] **Step 3: Commit**

```bash
git add server/app/core/audit.py
git commit -m "feat(foundation): add audit event writer"
```

---

## Task 11: Structured logging with request-id middleware

**Files:**
- Create: `server/app/logging.py`

- [ ] **Step 1: Write `server/app/logging.py`**

```python
import logging
import sys
from uuid import uuid4

import structlog
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import Response


def configure_logging(app_env: str) -> None:
    timestamper = structlog.processors.TimeStamper(fmt="iso", utc=True)
    shared_processors: list = [
        structlog.contextvars.merge_contextvars,
        structlog.processors.add_log_level,
        timestamper,
    ]

    structlog.configure(
        processors=shared_processors
        + [
            structlog.processors.StackInfoRenderer(),
            structlog.processors.format_exc_info,
            structlog.processors.JSONRenderer(),
        ],
        wrapper_class=structlog.make_filtering_bound_logger(logging.INFO),
        logger_factory=structlog.PrintLoggerFactory(file=sys.stdout),
        cache_logger_on_first_use=True,
    )

    # Pipe stdlib logging (uvicorn, sqlalchemy, etc.) through structlog's renderer.
    handler = logging.StreamHandler(sys.stdout)
    formatter = structlog.stdlib.ProcessorFormatter(
        foreign_pre_chain=shared_processors,
        processors=[structlog.processors.JSONRenderer()],
    )
    handler.setFormatter(formatter)

    root = logging.getLogger()
    root.handlers = [handler]
    root.setLevel(logging.INFO if app_env == "production" else logging.DEBUG)


class RequestIdMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next) -> Response:
        request_id = request.headers.get("x-request-id") or uuid4().hex
        structlog.contextvars.bind_contextvars(request_id=request_id)
        try:
            response: Response = await call_next(request)
        finally:
            structlog.contextvars.clear_contextvars()
        response.headers["x-request-id"] = request_id
        return response
```

- [ ] **Step 2: Smoke-test logging output**

```bash
cd server && uv run python -c "
from app.logging import configure_logging
import structlog
configure_logging('development')
log = structlog.get_logger('test')
log.info('hello', user='nobody')
" && cd ..
```

Expected: a single JSON line on stdout with `event=hello`, `user=nobody`, an ISO timestamp, and `level=info`.

- [ ] **Step 3: Commit**

```bash
git add server/app/logging.py
git commit -m "feat(foundation): add structlog config + request-id middleware"
```

---

## Task 12: Auth endpoints — `/auth/login`, `/auth/me`

**Files:**
- Create: `server/app/api/v1/auth.py`

- [ ] **Step 1: Write `server/app/api/v1/auth.py`**

```python
from datetime import datetime, timezone
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Request, status
from pydantic import BaseModel, EmailStr
from sqlalchemy.orm import Session

from app.core.audit import write_audit_event
from app.core.auth import issue_access_token, issue_refresh_token, verify_password
from app.core.rate_limit import limiter
from app.core.security import current_user
from app.db.models import ActorType, User
from app.db.session import get_db

router = APIRouter(prefix="/auth", tags=["auth"])


class LoginRequest(BaseModel):
    email: EmailStr
    password: str


class TokenResponse(BaseModel):
    access_token: str
    refresh_token: str
    token_type: str = "Bearer"


class MeResponse(BaseModel):
    id: str
    email: str
    role: str
    tenant_id: str


# Rate limit per spec §6.6: 10 login attempts per minute per IP, mitigates
# credential-stuffing without locking out a user behind a NAT.
@router.post("/login", response_model=TokenResponse)
@limiter.limit("10/minute")
def login(
    request: Request,
    body: LoginRequest,
    db: Annotated[Session, Depends(get_db)],
) -> TokenResponse:
    user = db.query(User).filter(User.email == body.email, User.is_active.is_(True)).one_or_none()
    if not user or not user.hashed_password or not verify_password(body.password, user.hashed_password):
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="invalid credentials")

    user.last_login_at = datetime.now(timezone.utc)
    write_audit_event(
        db,
        tenant_id=user.tenant_id,
        actor_id=user.id,
        actor_type=ActorType.USER,
        action="auth.login",
        target_type="user",
        target_id=str(user.id),
        ip_address=request.client.host if request.client else None,
        user_agent=request.headers.get("user-agent"),
    )
    db.commit()

    return TokenResponse(
        access_token=issue_access_token(user.id, user.tenant_id, user.role.value),
        refresh_token=issue_refresh_token(user.id, user.tenant_id, user.role.value),
    )


@router.get("/me", response_model=MeResponse)
def me(user: Annotated[User, Depends(current_user)]) -> MeResponse:
    return MeResponse(
        id=str(user.id), email=user.email, role=user.role.value, tenant_id=str(user.tenant_id)
    )
```

- [ ] **Step 2: Smoke-test import**

```bash
cd server && uv run python -c "from app.api.v1.auth import router; print(len(router.routes))" && cd ..
```

Expected: `2`.

- [ ] **Step 3: Commit**

```bash
git add server/app/api/v1/auth.py
git commit -m "feat(foundation): add /auth/login and /auth/me endpoints"
```

---

## Task 13: Admin API key management endpoints

**Files:**
- Create: `server/app/api/v1/admin.py`

- [ ] **Step 1: Write `server/app/api/v1/admin.py`**

```python
import secrets
from datetime import datetime, timezone
from typing import Annotated
from uuid import UUID

from argon2 import PasswordHasher
from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.core.audit import write_audit_event
from app.core.security import require_role
from app.db.models import ActorType, ApiKey, User, UserRole
from app.db.session import get_db

router = APIRouter(prefix="/admin", tags=["admin"])

_ph = PasswordHasher()
_ALLOWED_SCOPES = {"jobs:create", "jobs:read", "admin:audit", "admin:users"}


class CreateApiKeyRequest(BaseModel):
    name: str
    scopes: list[str]


class CreateApiKeyResponse(BaseModel):
    id: str
    name: str
    scopes: list[str]
    plaintext_key: str  # shown exactly once


class ApiKeyRow(BaseModel):
    id: str
    name: str
    scopes: list[str]
    created_at: datetime
    revoked_at: datetime | None


@router.post(
    "/api-keys",
    response_model=CreateApiKeyResponse,
    status_code=status.HTTP_201_CREATED,
    dependencies=[Depends(require_role(UserRole.ADMIN))],
)
def create_api_key(
    body: CreateApiKeyRequest,
    db: Annotated[Session, Depends(get_db)],
    user: Annotated[User, Depends(require_role(UserRole.ADMIN))],
) -> CreateApiKeyResponse:
    bad = set(body.scopes) - _ALLOWED_SCOPES
    if bad:
        raise HTTPException(status_code=400, detail=f"unknown scopes: {sorted(bad)}")

    # Format: rk_<8-char prefix><urlsafe tail>. The 8-char prefix is stored
    # unhashed to allow O(1) lookup at auth time; see app/core/security.py.
    prefix = secrets.token_urlsafe(6)[:8]
    tail = secrets.token_urlsafe(32)
    plain = f"rk_{prefix}{tail}"
    row = ApiKey(
        tenant_id=user.tenant_id,
        name=body.name,
        lookup_prefix=prefix,
        hashed_key=_ph.hash(plain),
        scopes=body.scopes,
        created_by=user.id,
    )
    db.add(row)
    db.flush()
    write_audit_event(
        db,
        tenant_id=user.tenant_id,
        actor_id=user.id,
        actor_type=ActorType.USER,
        action="api_key.create",
        target_type="api_key",
        target_id=str(row.id),
        payload={"name": row.name, "scopes": row.scopes},
    )
    db.commit()
    return CreateApiKeyResponse(
        id=str(row.id), name=row.name, scopes=row.scopes, plaintext_key=plain
    )


@router.get("/api-keys", response_model=list[ApiKeyRow])
def list_api_keys(
    db: Annotated[Session, Depends(get_db)],
    user: Annotated[User, Depends(require_role(UserRole.ADMIN))],
) -> list[ApiKeyRow]:
    rows = db.query(ApiKey).filter(ApiKey.tenant_id == user.tenant_id).all()
    return [
        ApiKeyRow(
            id=str(r.id),
            name=r.name,
            scopes=r.scopes,
            created_at=r.created_at,
            revoked_at=r.revoked_at,
        )
        for r in rows
    ]


@router.delete("/api-keys/{key_id}", status_code=status.HTTP_204_NO_CONTENT)
def revoke_api_key(
    key_id: UUID,
    db: Annotated[Session, Depends(get_db)],
    user: Annotated[User, Depends(require_role(UserRole.ADMIN))],
) -> None:
    row = db.get(ApiKey, key_id)
    if not row or row.tenant_id != user.tenant_id:
        raise HTTPException(status_code=404, detail="not found")
    if row.revoked_at is None:
        row.revoked_at = datetime.now(timezone.utc)
        write_audit_event(
            db,
            tenant_id=user.tenant_id,
            actor_id=user.id,
            actor_type=ActorType.USER,
            action="api_key.revoke",
            target_type="api_key",
            target_id=str(row.id),
        )
        db.commit()
```

- [ ] **Step 2: Smoke-test import**

```bash
cd server && uv run python -c "from app.api.v1.admin import router; print(len(router.routes))" && cd ..
```

Expected: `3`.

- [ ] **Step 3: Commit**

```bash
git add server/app/api/v1/admin.py
git commit -m "feat(foundation): add admin API-key create/list/revoke endpoints"
```

---

## Task 14: Health and Prometheus metrics endpoints

**Files:**
- Create: `server/app/api/v1/health.py`

- [ ] **Step 1: Write `server/app/api/v1/health.py`**

```python
from typing import Annotated

import redis as redis_lib
from fastapi import APIRouter, Depends, Response
from prometheus_client import CONTENT_TYPE_LATEST, Counter, Histogram, generate_latest
from sqlalchemy import text
from sqlalchemy.orm import Session

from app.config import get_settings
from app.db.session import get_db

router = APIRouter(tags=["health"])

# These are exported for handlers in later plans to increment.
JOBS_TOTAL = Counter("redactly_jobs_total", "Jobs created", ["status", "tenant"])
JOB_DURATION = Histogram("redactly_job_duration_seconds", "Job duration by stage", ["stage"])
DETECTIONS_TOTAL = Counter(
    "redactly_detections_total", "Detections produced", ["layer", "type"]
)
LLM_CALLS_TOTAL = Counter(
    "redactly_llm_verifier_calls_total", "LLM verifier invocations", ["result"]
)
VERIFY_FAILURES_TOTAL = Counter(
    "redactly_redaction_verify_failures_total",
    "Post-redaction verify failures (Sev-1 — alert)",
)


@router.get("/health")
def health(db: Annotated[Session, Depends(get_db)]) -> dict:
    s = get_settings()

    db_ok = False
    try:
        db.execute(text("SELECT 1"))
        db_ok = True
    except Exception:  # noqa: BLE001
        db_ok = False

    redis_ok = False
    try:
        client = redis_lib.from_url(s.redis_url, socket_connect_timeout=1)
        client.ping()
        redis_ok = True
    except Exception:  # noqa: BLE001
        redis_ok = False

    return {
        "status": "healthy" if db_ok and redis_ok else "degraded",
        "components": {"database": db_ok, "redis": redis_ok},
    }


@router.get("/metrics")
def metrics() -> Response:
    return Response(content=generate_latest(), media_type=CONTENT_TYPE_LATEST)
```

- [ ] **Step 2: Smoke-test import**

```bash
cd server && uv run python -c "from app.api.v1.health import router; print(len(router.routes))" && cd ..
```

Expected: `2`.

- [ ] **Step 3: Commit**

```bash
git add server/app/api/v1/health.py
git commit -m "feat(foundation): add /health and /metrics endpoints"
```

---

## Task 15: Wire FastAPI app + middleware + router aggregation

**Files:**
- Create: `server/app/api/v1/__init__.py`
- Create: `server/app/main.py`

- [ ] **Step 1: Write `server/app/api/v1/__init__.py`**

```python
from fastapi import APIRouter

from app.api.v1 import admin, auth, health

api_v1 = APIRouter()
api_v1.include_router(auth.router)
api_v1.include_router(admin.router)
api_v1.include_router(health.router)
```

- [ ] **Step 2: Write `server/app/main.py`**

```python
from contextlib import asynccontextmanager

import structlog
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from slowapi import _rate_limit_exceeded_handler
from slowapi.errors import RateLimitExceeded

from app.api.v1 import api_v1
from app.config import get_settings
from app.core.rate_limit import limiter
from app.logging import RequestIdMiddleware, configure_logging

log = structlog.get_logger(__name__)

_DEFAULT_DEV_SECRET = "dev-secret-change-me"


@asynccontextmanager
async def lifespan(app: FastAPI):
    s = get_settings()
    configure_logging(s.app_env)
    if s.app_env == "production" and s.jwt_secret == _DEFAULT_DEV_SECRET:
        # Refuse to boot rather than silently sign tokens with a public secret.
        raise RuntimeError(
            "JWT_SECRET is set to the dev default while APP_ENV=production. "
            "Generate a fresh secret (>= 32 random chars) and redeploy."
        )
    log.info("startup", app_env=s.app_env)
    yield
    log.info("shutdown")


def create_app() -> FastAPI:
    s = get_settings()
    app = FastAPI(
        title="RedactLy V2 API",
        version="0.2.0",
        lifespan=lifespan,
    )

    # Rate limiter — handlers attach @limiter.limit(...) decorators directly.
    app.state.limiter = limiter
    app.add_exception_handler(RateLimitExceeded, _rate_limit_exceeded_handler)

    app.add_middleware(RequestIdMiddleware)
    app.add_middleware(
        CORSMiddleware,
        allow_origins=["*"] if s.app_env == "development" else [],
        allow_methods=["GET", "POST", "PATCH", "DELETE", "OPTIONS"],
        allow_headers=["*"],
        allow_credentials=True,
    )

    app.include_router(api_v1, prefix=s.api_v1_prefix)
    return app


app = create_app()
```

- [ ] **Step 3: Smoke-test the app boots in-process (no DB needed)**

```bash
cd server && uv run python -c "
from fastapi.testclient import TestClient
from app.main import app
c = TestClient(app, raise_server_exceptions=False)
r = c.get('/api/v1/metrics')
assert r.status_code == 200, r.status_code
assert b'redactly_jobs_total' in r.content
print('ok')
" && cd ..
```

Expected: `ok`. (Note: `/health` will partially fail until Postgres + Redis are up in Task 19; that's fine for now.)

- [ ] **Step 4: Commit**

```bash
git add server/app/api/v1/__init__.py server/app/main.py
git commit -m "feat(foundation): wire FastAPI app with middleware + router aggregation"
```

---

## Task 16: Celery worker scaffolding

**Files:**
- Create: `server/app/workers/celery_app.py`
- Create: `server/app/workers/tasks.py`

- [ ] **Step 1: Write `server/app/workers/celery_app.py`**

```python
from celery import Celery

from app.config import get_settings

_s = get_settings()

celery_app = Celery(
    "redactly",
    broker=_s.broker_url,
    backend=_s.result_backend,
    include=["app.workers.tasks"],
)
celery_app.conf.update(
    task_track_started=True,
    task_time_limit=20 * 60,
    task_soft_time_limit=15 * 60,
    worker_prefetch_multiplier=1,
    broker_connection_retry_on_startup=True,
)
```

- [ ] **Step 2: Write `server/app/workers/tasks.py`**

```python
import structlog

from app.workers.celery_app import celery_app

log = structlog.get_logger(__name__)


@celery_app.task(name="app.workers.tasks.ping")
def ping() -> str:
    """Smoke task — used by ops to confirm the worker is alive.

    Real tasks (parse_job, detect_job, redact_job) land in Plans 2-4.
    """
    log.info("ping")
    return "pong"
```

- [ ] **Step 3: Smoke-test imports**

```bash
cd server && uv run python -c "from app.workers.celery_app import celery_app; from app.workers.tasks import ping; print('ok', ping.name)" && cd ..
```

Expected: `ok app.workers.tasks.ping`.

- [ ] **Step 4: Commit**

```bash
git add server/app/workers/celery_app.py server/app/workers/tasks.py
git commit -m "feat(foundation): add Celery app + ping task placeholder"
```

---

## Task 17: Bootstrap CLI (`redactly` command)

**Files:**
- Create: `server/cli.py`

- [ ] **Step 1: Write `server/cli.py`**

```python
"""RedactLy admin CLI.

Usage examples:
    uv run python -m cli admin create --email me@example.com --password '...'
    uv run python -m cli migrate
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import typer

from app.config import get_settings
from app.core.auth import hash_password
from app.db.models import Tenant, User, UserRole
from app.db.session import SessionLocal

app = typer.Typer(no_args_is_help=True)
admin = typer.Typer(no_args_is_help=True, help="Administrative commands")
app.add_typer(admin, name="admin")


@app.command()
def migrate() -> None:
    """Apply outstanding Alembic migrations."""
    here = Path(__file__).parent
    rc = subprocess.call(
        [sys.executable, "-m", "alembic", "upgrade", "head"], cwd=here
    )
    raise typer.Exit(code=rc)


@admin.command("create")
def admin_create(
    email: str = typer.Option(..., help="Admin email address"),
    password: str = typer.Option(..., prompt=True, hide_input=True, confirmation_prompt=True),
    tenant_name: str = typer.Option("default", help="Tenant name; created if missing"),
) -> None:
    """Create the first admin user (idempotent on email)."""
    s = get_settings()
    with SessionLocal() as db:
        tenant = db.query(Tenant).filter(Tenant.name == tenant_name).one_or_none()
        if not tenant:
            tenant = Tenant(
                name=tenant_name,
                settings={"retention_hours": s.default_tenant_retention_hours},
            )
            db.add(tenant)
            db.flush()

        user = (
            db.query(User)
            .filter(User.tenant_id == tenant.id, User.email == email)
            .one_or_none()
        )
        if user:
            user.hashed_password = hash_password(password)
            user.role = UserRole.ADMIN
            user.is_active = True
        else:
            user = User(
                tenant_id=tenant.id,
                email=email,
                hashed_password=hash_password(password),
                role=UserRole.ADMIN,
                is_active=True,
            )
            db.add(user)
        db.commit()
        typer.echo(f"admin: {user.email} (tenant={tenant.name}, id={user.id})")


if __name__ == "__main__":
    app()
```

- [ ] **Step 2: Smoke-test the CLI help renders**

```bash
cd server && uv run python -m cli --help && cd ..
```

Expected: typer help text listing `admin` and `migrate` subcommands.

- [ ] **Step 3: Commit**

```bash
git add server/cli.py
git commit -m "feat(foundation): add Typer CLI with 'admin create' and 'migrate'"
```

---

## Task 18: Dockerfile

**Files:**
- Create: `server/Dockerfile`
- Create: `server/.dockerignore`

- [ ] **Step 1: Write `server/.dockerignore`**

```
__pycache__
*.pyc
.pytest_cache
.mypy_cache
.ruff_cache
.venv
.env
.env.*
!.env.example
*.sqlite
*.db
```

- [ ] **Step 2: Write `server/Dockerfile`**

```dockerfile
FROM python:3.12-slim-bookworm AS base

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    UV_PROJECT_ENVIRONMENT=/opt/venv \
    PATH="/opt/venv/bin:$PATH"

# System deps. Heavier deps (tesseract, libgl) land in Plans 2-3 when needed.
RUN apt-get update \
    && apt-get install -y --no-install-recommends \
       ca-certificates curl libpq5 \
    && rm -rf /var/lib/apt/lists/*

# Install uv
COPY --from=ghcr.io/astral-sh/uv:0.5.10 /uv /usr/local/bin/uv

# Non-root user
RUN useradd --uid 5000 --create-home --shell /usr/sbin/nologin appuser

WORKDIR /app

# Resolve deps as root, install into /opt/venv that appuser can read.
COPY --chown=appuser:appuser pyproject.toml uv.lock ./
RUN uv sync --frozen --no-install-project

COPY --chown=appuser:appuser . .

USER appuser

EXPOSE 8000
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
```

- [ ] **Step 3: Build the image to confirm the Dockerfile is valid**

```bash
docker build -t redactly-server:foundation -f server/Dockerfile server/
```

Expected: build succeeds. Total time on first build ~2-4 minutes.

- [ ] **Step 4: Commit**

```bash
git add server/Dockerfile server/.dockerignore
git commit -m "feat(foundation): add server Dockerfile (Python 3.12-slim, uv-installed deps)"
```

---

## Task 19: Docker Compose stack

**Files:**
- Create: `docker-compose.yml`
- Create: `compose.gpu.yml` (placeholder)

- [ ] **Step 1: Write `docker-compose.yml`**

```yaml
name: redactly

services:
  postgres:
    image: postgres:16-alpine
    environment:
      POSTGRES_USER: redactly
      POSTGRES_PASSWORD: redactly
      POSTGRES_DB: redactly
    volumes:
      - postgres-data:/var/lib/postgresql/data
    healthcheck:
      test: ["CMD-SHELL", "pg_isready -U redactly -d redactly"]
      interval: 5s
      timeout: 3s
      retries: 20
    restart: unless-stopped

  redis:
    image: redis:7-alpine
    healthcheck:
      test: ["CMD", "redis-cli", "ping"]
      interval: 5s
      timeout: 3s
      retries: 20
    restart: unless-stopped

  ollama:
    # Used by Plan 3 (LLM verifier). Started here so the stack is whole.
    image: ollama/ollama:0.4.7
    volumes:
      - ollama-models:/root/.ollama
    healthcheck:
      test: ["CMD", "ollama", "list"]
      interval: 10s
      timeout: 5s
      retries: 12
    restart: unless-stopped

  api:
    build:
      context: ./server
      dockerfile: Dockerfile
    image: redactly-server:foundation
    env_file: .env
    environment:
      DATABASE_URL: postgresql+psycopg://redactly:redactly@postgres:5432/redactly
      REDIS_URL: redis://redis:6379/0
      STORAGE_LOCAL_ROOT: /var/lib/redactly/blobs
    volumes:
      - blobs:/var/lib/redactly/blobs
    depends_on:
      postgres:
        condition: service_healthy
      redis:
        condition: service_healthy
    command: >
      sh -c "python -m cli migrate && uvicorn app.main:app --host 0.0.0.0 --port 8000"
    healthcheck:
      test: ["CMD", "curl", "-fsS", "http://localhost:8000/api/v1/metrics"]
      interval: 5s
      timeout: 3s
      retries: 20
    restart: unless-stopped

  worker:
    image: redactly-server:foundation
    env_file: .env
    environment:
      DATABASE_URL: postgresql+psycopg://redactly:redactly@postgres:5432/redactly
      REDIS_URL: redis://redis:6379/0
      STORAGE_LOCAL_ROOT: /var/lib/redactly/blobs
    volumes:
      - blobs:/var/lib/redactly/blobs
    depends_on:
      postgres:
        condition: service_healthy
      redis:
        condition: service_healthy
    command: ["celery", "-A", "app.workers.celery_app.celery_app", "worker", "--loglevel=info", "--concurrency=2"]
    restart: unless-stopped

  beat:
    image: redactly-server:foundation
    env_file: .env
    environment:
      DATABASE_URL: postgresql+psycopg://redactly:redactly@postgres:5432/redactly
      REDIS_URL: redis://redis:6379/0
    depends_on:
      redis:
        condition: service_healthy
    command: ["celery", "-A", "app.workers.celery_app.celery_app", "beat", "--loglevel=info"]
    restart: unless-stopped

  nginx:
    image: nginx:1.27-alpine
    ports:
      - "443:443"
      - "80:80"
    volumes:
      - ./nginx/nginx.conf:/etc/nginx/nginx.conf:ro
      - ./nginx/dev.crt:/etc/nginx/certs/server.crt:ro
      - ./nginx/dev.key:/etc/nginx/certs/server.key:ro
    depends_on:
      api:
        condition: service_healthy
    restart: unless-stopped

volumes:
  postgres-data:
  ollama-models:
  blobs:
```

- [ ] **Step 2: Write `compose.gpu.yml` placeholder overlay**

```yaml
# GPU overlay — populated in Plan 3 when the LLM verifier lands.
# Usage:
#   docker compose -f docker-compose.yml -f compose.gpu.yml up -d
services:
  ollama:
    deploy:
      resources:
        reservations:
          devices:
            - driver: nvidia
              count: all
              capabilities: [gpu]
    environment:
      OLLAMA_KEEP_ALIVE: "30m"
  worker:
    deploy:
      resources:
        reservations:
          devices:
            - driver: nvidia
              count: all
              capabilities: [gpu]
```

- [ ] **Step 3: Commit (we boot and verify in Task 25 once nginx config is in place)**

```bash
git add docker-compose.yml compose.gpu.yml
git commit -m "feat(foundation): add docker-compose.yml stack + GPU overlay placeholder"
```

---

## Task 20: Nginx TLS terminator + dev self-signed cert

**Files:**
- Create: `nginx/nginx.conf`
- Create: `nginx/dev.crt`, `nginx/dev.key`

- [ ] **Step 1: Generate a self-signed cert for local dev**

```bash
openssl req -x509 -nodes -newkey rsa:2048 \
    -keyout nginx/dev.key -out nginx/dev.crt \
    -days 365 -subj "/CN=localhost" \
    -addext "subjectAltName=DNS:localhost,IP:127.0.0.1"
```

Expected: `nginx/dev.crt` and `nginx/dev.key` exist (~1-2 KB each).

- [ ] **Step 2: Write `nginx/nginx.conf`**

```nginx
worker_processes auto;
events { worker_connections 1024; }

http {
    sendfile on;
    keepalive_timeout 65;
    client_max_body_size 600M;
    server_tokens off;

    upstream api { server api:8000; }

    server {
        listen 80;
        server_name _;
        return 308 https://$host$request_uri;
    }

    server {
        listen 443 ssl;
        http2 on;
        server_name _;

        ssl_certificate     /etc/nginx/certs/server.crt;
        ssl_certificate_key /etc/nginx/certs/server.key;
        ssl_protocols       TLSv1.2 TLSv1.3;

        # Security headers (frontend lands in Plan 6; CSP tightened then)
        add_header Strict-Transport-Security "max-age=31536000; includeSubDomains" always;
        add_header X-Frame-Options "DENY" always;
        add_header X-Content-Type-Options "nosniff" always;
        add_header Referrer-Policy "no-referrer" always;

        location /api/ {
            proxy_pass http://api;
            proxy_http_version 1.1;
            proxy_set_header Host $host;
            proxy_set_header X-Real-IP $remote_addr;
            proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
            proxy_set_header X-Forwarded-Proto https;
            proxy_set_header Upgrade $http_upgrade;
            proxy_set_header Connection "upgrade";
            proxy_read_timeout 300s;
        }

        location / {
            return 200 "RedactLy V2 — frontend served from Plan 6 onward.";
            add_header Content-Type text/plain;
        }
    }
}
```

- [ ] **Step 3: Update `.gitignore` to skip the dev cert/key**

Append to root `.gitignore`:

```
nginx/dev.crt
nginx/dev.key
```

- [ ] **Step 4: Add `nginx/README.md`**

```markdown
# Nginx

TLS terminator for the V2 stack. Reverse-proxies `/api/*` to the FastAPI service.

## Local dev cert

Run:

    openssl req -x509 -nodes -newkey rsa:2048 \
        -keyout nginx/dev.key -out nginx/dev.crt \
        -days 365 -subj "/CN=localhost" \
        -addext "subjectAltName=DNS:localhost,IP:127.0.0.1"

The cert/key are gitignored. Each developer regenerates them on first run.

## Production

Replace `nginx/dev.crt` and `nginx/dev.key` with real certificates (Let's Encrypt
or your CA-issued pair) and bake them in via Docker secrets or a volume mount.
```

- [ ] **Step 5: Commit**

```bash
git add nginx/nginx.conf nginx/README.md .gitignore
git commit -m "feat(foundation): add Nginx TLS terminator + dev cert generator"
```

---

## Task 21: Backup and restore scripts

**Files:**
- Create: `ops/backup.sh`
- Create: `ops/restore.sh`

- [ ] **Step 1: Write `ops/backup.sh`**

```bash
#!/usr/bin/env bash
set -euo pipefail

# Backs up Postgres + the local blob volume into a single timestamped tarball.
# Run from repo root: ./ops/backup.sh [/path/to/output-dir]
#
# The compose project name is pinned to `redactly` via `name:` in
# docker-compose.yml, so the named volume is `redactly_blobs` regardless of
# the host directory. If you've overridden the project name with
# COMPOSE_PROJECT_NAME or `-p`, set $PROJECT below to match.

PROJECT="${COMPOSE_PROJECT_NAME:-redactly}"
OUT_DIR="${1:-./backups}"
mkdir -p "$OUT_DIR"
TS="$(date -u +%Y%m%dT%H%M%SZ)"
WORK="$(mktemp -d)"
trap 'rm -rf "$WORK"' EXIT

echo "[backup] dumping postgres -> $WORK/postgres.sql"
docker compose -p "$PROJECT" exec -T postgres pg_dump -U redactly redactly > "$WORK/postgres.sql"

echo "[backup] snapshotting blob volume ${PROJECT}_blobs"
docker run --rm \
    -v "${PROJECT}_blobs":/from \
    -v "$WORK":/to \
    alpine:3.21 \
    sh -c "tar -czf /to/blobs.tar.gz -C /from ."

OUT="$OUT_DIR/redactly-backup-$TS.tar.gz"
tar -czf "$OUT" -C "$WORK" .
echo "[backup] wrote $OUT"
```

- [ ] **Step 2: Write `ops/restore.sh`**

```bash
#!/usr/bin/env bash
set -euo pipefail

# Restores from a backup tarball produced by ops/backup.sh.
# Run from repo root: ./ops/restore.sh /path/to/redactly-backup-...tar.gz
#
# Compose project name (and therefore the named volume) is read from
# $COMPOSE_PROJECT_NAME or defaults to `redactly` to match docker-compose.yml.

PROJECT="${COMPOSE_PROJECT_NAME:-redactly}"
BACKUP="${1:?usage: ./ops/restore.sh <backup.tar.gz>}"
WORK="$(mktemp -d)"
trap 'rm -rf "$WORK"' EXIT

tar -xzf "$BACKUP" -C "$WORK"

echo "[restore] applying postgres dump"
docker compose -p "$PROJECT" exec -T postgres psql -U redactly -d redactly < "$WORK/postgres.sql"

echo "[restore] restoring blob volume ${PROJECT}_blobs"
docker run --rm \
    -v "${PROJECT}_blobs":/to \
    -v "$WORK":/from \
    alpine:3.21 \
    sh -c "rm -rf /to/* && tar -xzf /from/blobs.tar.gz -C /to"

echo "[restore] done"
```

- [ ] **Step 3: chmod and commit**

```bash
chmod +x ops/backup.sh ops/restore.sh
git add ops/backup.sh ops/restore.sh
git commit -m "feat(foundation): add backup/restore scripts (pg_dump + blob volume)"
```

---

## Task 22: Replace top-level README with V2 boot guide

**Files:**
- Modify: `README.md`

- [ ] **Step 1: Read the current README**

Read `README.md` end-to-end so the rewrite preserves the project framing (logo, tagline) and only swaps the operational sections.

- [ ] **Step 2: Replace `README.md` with this**

```markdown
![RedactLy.AI](assets/header_logo.png)

# RedactLy.AI

Privacy-first PII redaction for documents and images. Runs fully self-hosted; no
cloud APIs in any data path.

> **V2 is in active development.** This README documents the V2 stack scaffolded
> by `docs/superpowers/plans/2026-05-01-redactly-foundation.md`. The legacy V1
> Flask code is retained at `server.legacy/` for reference and will be removed
> once V2 reaches functional parity.

## Stack

- **Backend:** FastAPI · Celery · Postgres 16 · Redis 7 · Ollama (CPU; GPU optional)
- **Frontend:** React + Vite + shadcn (rewritten in Plan 6)
- **Auth:** local accounts (Argon2 + JWT) · API keys · OIDC (Plan 5)
- **Storage:** local FS (default) · S3/MinIO (configurable in a follow-up plan)

## Boot

```bash
cp .env.example .env                   # adjust JWT_SECRET in production
openssl req -x509 -nodes -newkey rsa:2048 \
    -keyout nginx/dev.key -out nginx/dev.crt \
    -days 365 -subj "/CN=localhost" \
    -addext "subjectAltName=DNS:localhost,IP:127.0.0.1"

docker compose up -d
docker compose exec api python -m cli admin create \
    --email you@example.com --password 'change-me-now'
```

> The CLI is invoked as a Python module (`python -m cli ...`); there is no
> separate `redactly` console script in V1.0 — adding one would require
> changing the Dockerfile to install the project package, which is a V2
> nice-to-have.

Then:

```bash
curl -k https://localhost/api/v1/health
# {"status":"healthy","components":{"database":true,"redis":true}}

curl -k -X POST https://localhost/api/v1/auth/login \
    -H 'Content-Type: application/json' \
    -d '{"email":"you@example.com","password":"change-me-now"}'
# {"access_token":"eyJ...","refresh_token":"eyJ...","token_type":"Bearer"}
```

## GPU mode

```bash
docker compose -f docker-compose.yml -f compose.gpu.yml up -d
```

The GPU overlay swaps the LLM verifier (Plan 3) to `qwen2.5:7b-instruct-q4_K_M`
and reserves NVIDIA devices for the Ollama and worker services. CPU mode runs
`phi3.5:mini-instruct-q4_K_M` by default.

## Operations

- **Backup:** `./ops/backup.sh [output-dir]` — dumps Postgres + blob volume into a timestamped tarball.
- **Restore:** `./ops/restore.sh path/to/backup.tar.gz`.
- **Logs:** structured JSON on stdout for every service.
- **Metrics:** `GET /api/v1/metrics` — Prometheus exposition format.

## API surface (V2 — current scope)

| Endpoint | Notes |
|---|---|
| `POST /api/v1/auth/login` | Returns access + refresh JWT |
| `GET  /api/v1/auth/me` | Returns current user identity |
| `POST /api/v1/admin/api-keys` | Admin-only; returns plaintext key once |
| `GET  /api/v1/admin/api-keys` | Admin-only |
| `DELETE /api/v1/admin/api-keys/{id}` | Admin-only |
| `GET  /api/v1/health` | DB + Redis liveness |
| `GET  /api/v1/metrics` | Prometheus metrics |

Job, detection, and redaction endpoints land in Plan 5; the HITL frontend in Plan 6.

## Architecture, plans, spec

- Spec: `docs/superpowers/specs/2026-05-01-redactly-overhaul-design.md`
- Roadmap: `docs/superpowers/plans/2026-05-01-redactly-overhaul-roadmap.md`
- Foundation plan (this scaffolding): `docs/superpowers/plans/2026-05-01-redactly-foundation.md`

## License

MIT.
```

- [ ] **Step 3: Commit**

```bash
git add README.md
git commit -m "docs: replace README with V2 boot guide"
```

---

## Task 23: Smoke-verify the full stack boots

This is the acceptance gate. Every previous task feeds into it.

- [ ] **Step 1: Generate the dev cert if not already present**

```bash
test -f nginx/dev.crt && test -f nginx/dev.key || openssl req -x509 -nodes -newkey rsa:2048 \
    -keyout nginx/dev.key -out nginx/dev.crt \
    -days 365 -subj "/CN=localhost" \
    -addext "subjectAltName=DNS:localhost,IP:127.0.0.1"
```

- [ ] **Step 2: Copy `.env.example` to `.env`**

```bash
test -f .env || cp .env.example .env
```

- [ ] **Step 3: Bring up the stack**

```bash
docker compose up -d --build
```

Expected: every service starts; `docker compose ps` shows all containers healthy within ~60 seconds (Ollama may stay "starting" until a model is pulled in Plan 3 — that's fine; api/worker/beat/postgres/redis/nginx must all be healthy).

- [ ] **Step 4: Confirm the migration ran**

```bash
docker compose exec postgres psql -U redactly -d redactly -c '\dt'
```

Expected: `tenants`, `users`, `api_keys`, `jobs`, `detections`, `audit_events`, `alembic_version` listed.

- [ ] **Step 5: Bootstrap an admin user**

```bash
docker compose exec api python -m cli admin create \
    --email admin@local --password "$(openssl rand -base64 18)"
```

Expected: prints `admin: admin@local (tenant=default, id=...)`. Save the password — you'll need it in the next step.

- [ ] **Step 6: Log in and hit `/me`**

Bash / Linux / macOS:

```bash
TOKEN=$(curl -sk -X POST https://localhost/api/v1/auth/login \
    -H 'Content-Type: application/json' \
    -d '{"email":"admin@local","password":"<paste-the-password>"}' \
    | python -c "import sys, json; print(json.load(sys.stdin)['access_token'])")

curl -sk https://localhost/api/v1/auth/me -H "Authorization: Bearer $TOKEN"
```

PowerShell (Windows):

```powershell
$resp = Invoke-RestMethod -Method POST -Uri https://localhost/api/v1/auth/login `
    -ContentType 'application/json' `
    -Body (@{ email = 'admin@local'; password = '<paste-the-password>' } | ConvertTo-Json) `
    -SkipCertificateCheck
$token = $resp.access_token
Invoke-RestMethod -Uri https://localhost/api/v1/auth/me `
    -Headers @{ Authorization = "Bearer $token" } -SkipCertificateCheck
```

Expected (either path): JSON `{"id":"...","email":"admin@local","role":"admin","tenant_id":"..."}`.

- [ ] **Step 7: Confirm health and metrics**

```bash
curl -sk https://localhost/api/v1/health
# {"status":"healthy","components":{"database":true,"redis":true}}

curl -sk https://localhost/api/v1/metrics | head
# # HELP redactly_jobs_total Jobs created
# # TYPE redactly_jobs_total counter
```

- [ ] **Step 8: Confirm the worker is alive via Celery ping**

```bash
docker compose exec worker celery -A app.workers.celery_app.celery_app inspect ping
```

Expected: a `pong` reply from `celery@<container-id>`.

- [ ] **Step 9: Audit log was written for the login**

```bash
docker compose exec postgres psql -U redactly -d redactly \
    -c "SELECT action, target_type FROM audit_events ORDER BY created_at DESC LIMIT 1;"
```

Expected: one row, `auth.login | user`.

- [ ] **Step 10: Tear down cleanly**

```bash
docker compose down
```

Expected: all containers stop; volumes preserved.

- [ ] **Step 11: Commit any last-mile fixes**

If any earlier task needed an adjustment to make the boot work, commit those changes now under a single message:

```bash
git add -p
git commit -m "fix(foundation): smoke-verify adjustments"
```

If nothing needed fixing, skip the commit — that itself is a good sign.

---

## Acceptance gate (recap)

Foundation is done when every Task 23 step passes on a clean clone:

- [x] `docker compose up -d` brings every service healthy.
- [x] Alembic migration created all six tables on first start.
- [x] `redactly admin create` provisions the first admin.
- [x] Login returns a valid JWT; `/auth/me` echoes the admin identity.
- [x] `/health` returns DB + Redis reachability.
- [x] `/metrics` returns Prometheus output with the declared counters.
- [x] Worker responds to `celery inspect ping`.
- [x] Audit log records the login event.

Once green, hand off to **Plan 2 (Document parsing)** and **Plan 3 (Detection engine)** in parallel — both depend only on Foundation.
