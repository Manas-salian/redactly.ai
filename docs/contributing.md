# Contributing

This document covers setting up a local development environment, common
code-change recipes, and the conventions the project follows.

## Development setup

### Prerequisites

- Python 3.12 (exactly; `pyproject.toml` pins `>=3.12,<3.13`)
- `uv` — install once: `pip install uv`
- Docker + Compose v2 (for the database and Redis)
- `git`

### Clone and bootstrap

```bash
git clone https://github.com/your-org/redactly.ai
cd redactly.ai

# Copy the environment file; the defaults work for local development
cp .env.example .env

# Generate the dev TLS cert (required for Nginx to start)
openssl req -x509 -nodes -newkey rsa:2048 \
    -keyout nginx/dev.key -out nginx/dev.crt \
    -days 365 -subj "/CN=localhost" \
    -addext "subjectAltName=DNS:localhost,IP:127.0.0.1"
```

### Option A: Run just the backing services; run the API locally

Start Postgres and Redis in containers, run FastAPI on the host:

```bash
# Start only the infrastructure services
docker compose up -d postgres redis

# Install Python dependencies (into a local .venv)
cd server
uv sync

# Run the API with auto-reload
uv run uvicorn app.main:app --reload --host 0.0.0.0 --port 8000
```

The API is now at `http://localhost:8000/api/v1/`. There is no TLS in this mode;
use plain `http://`.

Bootstrap the admin user (from a second terminal inside `server/`):

```bash
cd server
uv run python -m cli admin create \
    --email you@example.com --password 'change-me-now'
```

### Option B: Run the full stack

If you need Nginx TLS, Celery workers, and Ollama:

```bash
docker compose up -d --build
```

Then attach a debugger to the running API container or use `docker compose logs
-f api` to watch logs.

## Project layout

```
server/
  app/
    api/v1/          Route handlers. One file per resource group.
      auth.py          /auth/login, /auth/me
      admin.py         /admin/api-keys
      health.py        /health, /metrics
      __init__.py      Router aggregator — import here to register a new router
    core/            Cross-cutting concerns
      auth.py          Password hashing + JWT helpers
      security.py      FastAPI dependencies: current_user, require_role
      audit.py         write_audit_event() helper
      rate_limit.py    Module-level slowapi Limiter
      storage.py       BlobStore interface + LocalBlobStore
    db/
      models.py        All SQLAlchemy ORM models
      session.py       Engine + SessionLocal + get_db dependency
      migrations/      Alembic env + migration versions
    workers/
      celery_app.py    Celery application instance
      tasks.py         Task definitions
    config.py          pydantic-settings configuration (all env vars)
    logging.py         structlog config + RequestIdMiddleware
    main.py            FastAPI app factory + lifespan + middleware wiring
  cli.py               Typer CLI: admin create, migrate
  pyproject.toml       Dependencies and project metadata
  alembic.ini          Alembic configuration
```

## Adding an endpoint

1. **Define Pydantic models** for request and response in the relevant
   `api/v1/*.py` file (or in a shared `schemas.py` if they are large):

   ```python
   class CreateFooRequest(BaseModel):
       name: str

   class FooResponse(BaseModel):
       id: str
       name: str
   ```

2. **Add the route** to an existing router or a new `api/v1/foo.py`:

   ```python
   router = APIRouter(prefix="/foos", tags=["foos"])

   @router.post("/", response_model=FooResponse, status_code=201)
   def create_foo(
       body: CreateFooRequest,
       db: Annotated[Session, Depends(get_db)],
       user: Annotated[User, Depends(current_user)],
   ) -> FooResponse:
       ...
   ```

3. **Register the router** in `server/app/api/v1/__init__.py`:

   ```python
   from app.api.v1 import admin, auth, foo, health

   api_v1 = APIRouter()
   api_v1.include_router(foo.router)
   ```

4. Restart the server and check `/api/v1/docs` — the new endpoint should appear.

## Adding a database column

1. **Edit `server/app/db/models.py`** — add the column to the relevant ORM class:

   ```python
   class Job(Base):
       ...
       priority: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
   ```

2. **Generate a migration** (from inside `server/`):

   ```bash
   uv run python -m alembic revision --autogenerate -m "add job priority column"
   ```

   Alembic will write a new file in `server/app/db/migrations/versions/`.

3. **Review the generated migration** — Alembic's autogenerate is good but not
   perfect. Check that the `upgrade()` and `downgrade()` functions are correct
   before committing.

4. **Apply the migration** locally:

   ```bash
   uv run python -m cli migrate
   ```

5. **Commit both files** together: `models.py` and the new migration version.

## Code style

- **Python version:** 3.12. Use `match` statements, `str | None` union syntax,
  and `Mapped[...]` mapped columns — no workarounds for older Pythons.
- **Indentation:** 4 spaces.
- **Type hints:** required on all public functions and method signatures.
- **Logging:** use `structlog.get_logger()`. Do not use `print()` or the stdlib
  `logging` module directly. Pass context as keyword arguments:
  ```python
  log = structlog.get_logger(__name__)
  log.info("job_created", job_id=str(job.id), tenant_id=str(job.tenant_id))
  ```
- **Pydantic at I/O boundaries:** request bodies and response models are always
  Pydantic `BaseModel` subclasses. Do not pass ORM objects across the API layer.
- **No PII in audit payloads:** when calling `write_audit_event(...)`, review
  the `payload` dict before passing it. Hash or omit any PII text (see
  `server/app/core/audit.py` docstring).
- **Dependency injection:** use FastAPI `Depends(...)` for database sessions and
  current user. Do not call `SessionLocal()` directly in route handlers.
- **Error handling:** raise `HTTPException` from route handlers. Services should
  raise plain Python exceptions and let the route handler decide the HTTP status.

## Tests

**V1 ships with no tests by deliberate scope.** The project owner explicitly
deferred test infrastructure to V2 kickoff (see
`docs/superpowers/plans/2026-05-01-redactly-overhaul-roadmap.md`, "Cross-cutting
notes"). Do not write tests until that scope opens or your change is genuinely
test-blocked (i.e., you cannot verify correctness any other way).

When the test scope opens, the plan is to use `pytest` with `httpx.AsyncClient`
for endpoint testing, mirroring the pattern in the Insights-eg reference project.

## Commit conventions

Follow the conventional commits style already in the log:

```
feat(area): add X
fix(area): correct Y
docs: update Z
chore: bump dependency W
```

Where `area` is a short noun matching the changed subsystem (e.g., `auth`,
`admin`, `health`, `storage`, `migrations`, `docker`). Keep the subject line
under 72 characters. Add a body if the "why" needs more than one line.

## Plan-driven work

V2 development follows the roadmap in
[`docs/superpowers/plans/2026-05-01-redactly-overhaul-roadmap.md`](../docs/superpowers/plans/2026-05-01-redactly-overhaul-roadmap.md)
and the per-plan task lists in `docs/superpowers/plans/`. Each plan defines an
acceptance gate (a set of demonstrable behaviours the implementation must satisfy
before the plan is considered complete). Work starts from the plan document, not
from open-ended feature ideas.

Before starting a new plan:
1. Read the plan document fully.
2. Read the relevant spec sections it references.
3. Check the acceptance gate — your changes must satisfy every item before the
   plan is called done.

## Review etiquette

- **Small PRs.** One PR per plan task (or a logical subset of one). Large PRs
  are hard to review and hard to revert.
- **Link the plan section.** In the PR description, quote the task number and
  acceptance-gate item your change addresses.
- **Demonstrate the smoke gate.** Run the acceptance-gate commands and paste the
  output (or a screen recording) into the PR.
- **Don't squash history.** The commit log is the change record. Merge with a
  merge commit or rebase cleanly; don't squash meaningful task commits into one.
