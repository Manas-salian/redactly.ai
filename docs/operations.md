# Operations Runbook

This document covers deploying, configuring, maintaining, and troubleshooting
a RedactLy instance. Audience: whoever manages the deployment.

## First-time setup

### Prerequisites

- Docker Engine 24+ with the Compose v2 plugin (`docker compose version`)
- `openssl` available on the host (for the dev cert step)
- Port 443 and 80 free on the host (see Windows note below)

### Boot sequence

```bash
# 1. Clone the repo and enter the directory
git clone https://github.com/your-org/redactly.ai
cd redactly.ai

# 2. Copy the example environment file
cp .env.example .env

# 3. Edit .env — at minimum, change JWT_SECRET for anything non-throwaway
#    In production this must not be "dev-secret-change-me".
#    The app will refuse to boot in APP_ENV=production with the default secret.

# 4. Generate the self-signed TLS certificate
openssl req -x509 -nodes -newkey rsa:2048 \
    -keyout nginx/dev.key -out nginx/dev.crt \
    -days 365 -subj "/CN=localhost" \
    -addext "subjectAltName=DNS:localhost,IP:127.0.0.1"

# 5. Build and start all services
docker compose up -d --build

# 6. Watch startup health checks
docker compose ps

# 7. Bootstrap the first admin user
docker compose exec api python -m cli admin create \
    --email you@example.com --password 'change-me-now'
```

Verify:

```bash
curl -k https://localhost/api/v1/health
# {"status":"healthy","components":{"database":true,"redis":true}}
```

### Windows port-80 conflict

On Windows, port 80 is often held by IIS or another service. The compose file
publishes `8080:80` as a fallback:

```bash
# HTTP (redirects to HTTPS — only useful for the redirect, not for API calls)
curl http://localhost:8080/api/v1/health

# HTTPS (normal path)
curl -k https://localhost/api/v1/health
```

To free port 80, stop IIS: `net stop W3SVC` (elevated PowerShell), or edit
`docker-compose.yml` to change `"443:443"` and `"8080:80"` to other ports.

## CPU vs GPU mode

By default the stack runs in CPU mode. The Ollama service starts but the LLM
verifier task (Plan 3) is not yet implemented.

**CPU mode** (default):

```bash
docker compose up -d
```

Default Ollama model: `phi3.5:mini-instruct-q4_K_M` (~2.4 GB quantized).

**GPU mode** (requires NVIDIA drivers + nvidia-container-toolkit):

```bash
docker compose -f docker-compose.yml -f compose.gpu.yml up -d
```

The GPU overlay (`compose.gpu.yml`) adds `nvidia` device reservations to the
`ollama` and `worker` services. When the LLM verifier lands in Plan 3, GPU mode
will switch to `qwen2.5-7b-instruct-q4_K_M` (~4.5 GB).

To switch back to CPU mode, bring the stack down and restart without the overlay:

```bash
docker compose down
docker compose up -d
```

## Configuration

All settings are read from the `.env` file by `server/app/config.py` using
`pydantic-settings`. Environment variables override `.env` values.

### Application

| Variable | Default | Purpose |
|---|---|---|
| `APP_ENV` | `development` | `development` or `production`. In production, the app refuses to boot if `JWT_SECRET` is the default. CORS is also locked down in production. |
| `API_V1_PREFIX` | `/api/v1` | URL prefix for all v1 routes. |

### Database

| Variable | Default | Purpose |
|---|---|---|
| `DATABASE_URL` | `postgresql+psycopg://redactly:redactly@postgres:5432/redactly` | Full SQLAlchemy-style connection string. Override for external Postgres. |

### Redis / Celery

| Variable | Default | Purpose |
|---|---|---|
| `REDIS_URL` | `redis://redis:6379/0` | Redis connection for both the Celery broker and result backend. |
| `CELERY_BROKER_URL` | (falls back to `REDIS_URL`) | Override broker independently if needed. |
| `CELERY_RESULT_BACKEND` | (falls back to `REDIS_URL`) | Override result backend independently. |

### Auth

| Variable | Default | Purpose |
|---|---|---|
| `JWT_SECRET` | `dev-secret-change-me` | **Change this.** Minimum 32 random characters. The app fails to start in production with the default value. |
| `JWT_ALGORITHM` | `HS256` | Signing algorithm. |
| `JWT_ACCESS_TTL_MINUTES` | `15` | Access token lifetime in minutes. |
| `JWT_REFRESH_TTL_DAYS` | `7` | Refresh token lifetime in days. |

### Storage

| Variable | Default | Purpose |
|---|---|---|
| `STORAGE_BACKEND` | `local` | `local` (filesystem). `s3` and `minio` are interface-only in V1; implementations land in a follow-up plan. |
| `STORAGE_LOCAL_ROOT` | `/var/lib/redactly/blobs` | Filesystem root for the local blob store. Mounted as a Docker volume in compose. |
| `STORAGE_S3_BUCKET` | — | Required when `STORAGE_BACKEND=s3`. |
| `STORAGE_S3_ENDPOINT` | — | Override endpoint URL for MinIO or other S3-compatible services. |
| `STORAGE_S3_REGION` | `us-east-1` | AWS region. |
| `STORAGE_S3_ACCESS_KEY` | — | S3 access key. |
| `STORAGE_S3_SECRET_KEY` | — | S3 secret key. |

### Limits

| Variable | Default | Purpose |
|---|---|---|
| `UPLOAD_MAX_FILE_BYTES` | `104857600` (100 MB) | Maximum size of a single uploaded file. |
| `UPLOAD_MAX_JOB_BYTES` | `524288000` (500 MB) | Maximum total bytes across all files in one job. |

### Tenant

| Variable | Default | Purpose |
|---|---|---|
| `DEFAULT_TENANT_RETENTION_HOURS` | `24` | Hours before a job's blobs are purged. Applied when the bootstrap creates the default tenant. |

### Ollama

| Variable | Default | Purpose |
|---|---|---|
| `OLLAMA_HOST` | `http://ollama:11434` | Base URL for the Ollama service. |
| `OLLAMA_MODEL` | `phi3.5:mini-instruct-q4_K_M` | Default model for the LLM verifier (Plan 3). |
| `LLM_VERIFIER_ENABLED` | `true` | Set to `false` to skip the LLM verifier layer entirely. |

## Health and metrics

### Health endpoint

`GET /api/v1/health` performs synchronous liveness checks on each dependency:

```json
{
  "status": "healthy",
  "components": {
    "database": true,
    "redis": true
  }
}
```

`status` is `"healthy"` when all components are `true`; `"degraded"` otherwise.
The HTTP response code is always `200` — use the `status` field for automation.

Suggested alert: fire if `status != "healthy"` for more than 60 seconds.

### Prometheus metrics

`GET /api/v1/metrics` returns metrics in Prometheus text format. Scrape interval
recommendation: 15 s.

| Metric | Alert threshold |
|---|---|
| `redactly_jobs_total` | — (informational) |
| `redactly_job_duration_seconds` | P95 > 300s warrants investigation |
| `redactly_detections_total` | — (informational) |
| `redactly_llm_verifier_calls_total` | — (informational) |
| `redactly_redaction_verify_failures_total` | **Any non-zero value is Sev-1.** A non-zero reading means the verification pass found PII surviving in a redacted document. Halt new jobs and investigate immediately. |

Note: the redaction pipeline (Plan 4) has not shipped yet. `redactly_redaction_verify_failures_total` will read zero until Plan 4 is deployed. Configure the alert now so it fires the moment anything goes wrong after that plan lands.

The `/metrics` endpoint is unauthenticated. This is a known V1 limitation; see
[docs/security.md](security.md).

### Viewing logs

All services emit structured JSON to stdout:

```bash
docker compose logs -f api       # FastAPI application
docker compose logs -f worker    # Celery worker
docker compose logs -f beat      # Celery beat scheduler
docker compose logs -f nginx     # Nginx access + error
docker compose logs -f postgres  # Postgres server
```

Key log fields: `ts`, `level`, `event`, `request_id`, `tenant_id`, `job_id`.

## Backups

### Taking a backup

`ops/backup.sh` dumps Postgres and snapshots the blob volume into a single
timestamped tarball.

```bash
# Run from the repo root; output goes to ./backups/ by default
./ops/backup.sh

# Specify a different output directory
./ops/backup.sh /mnt/nas/redactly-backups
```

Output filename format: `redactly-backup-YYYYMMDDTHHMMSSZ.tar.gz`

The script uses the compose project name (`redactly`) to locate the `redactly_blobs`
volume. If you've overridden the project name with `COMPOSE_PROJECT_NAME`, export
that variable before running the script:

```bash
export COMPOSE_PROJECT_NAME=myproject
./ops/backup.sh
```

### Restoring a backup

```bash
./ops/restore.sh /path/to/redactly-backup-20260501T120000Z.tar.gz
```

This restores both the Postgres dump and the blob volume. The stack should be
running (the postgres container must be healthy) but ideally not serving traffic
during restore.

### What backups cover

- Postgres database (all tables, users, sequences)
- Blob storage volume (`/var/lib/redactly/blobs`)

### What backups do NOT cover

- Redis state (Celery task queues and results — transient; in-flight jobs will
  need to be re-queued manually after restore)
- Ollama model weights (re-downloaded on first use after restore; not PII-bearing)
- TLS certificates (`nginx/dev.crt`, `nginx/dev.key` are gitignored and
  host-specific)

### Backup scheduling

The scripts do not install a cron job themselves. Set one up on the host or use
a Docker cron container. Daily backups with 30-day retention are a reasonable
starting point.

## Common issues

### `api` container is unhealthy at startup

```bash
docker compose logs api
```

Common causes:

1. **Alembic migration failure** — look for `alembic.exceptions.CannotAutoUpgrade`
   or SQL errors in the log. The compose `command` runs `python -m cli migrate`
   before starting Uvicorn. If migrations fail, Uvicorn never starts.

2. **Postgres not yet ready** — the compose healthcheck retries 20 times at 5-second
   intervals (100 s total). If Postgres takes longer, increase `retries` in
   `docker-compose.yml`.

3. **`JWT_SECRET=dev-secret-change-me` in production** — the app intentionally
   refuses to start in `APP_ENV=production` with the default secret. Set a real
   secret in `.env`.

### Port 80 conflict on Windows

The `nginx` container binds `"443:443"` and `"8080:80"`. If port 443 is also
blocked, change the mapping in `docker-compose.yml`:

```yaml
ports:
  - "8443:443"
  - "8080:80"
```

Then use `https://localhost:8443/api/v1/...`.

### `nginx/dev.crt` or `nginx/dev.key` is missing

These files are gitignored (`nginx/dev.crt`, `nginx/dev.key` in `.gitignore`).
Regenerate them:

```bash
openssl req -x509 -nodes -newkey rsa:2048 \
    -keyout nginx/dev.key -out nginx/dev.crt \
    -days 365 -subj "/CN=localhost" \
    -addext "subjectAltName=DNS:localhost,IP:127.0.0.1"
```

Then restart Nginx: `docker compose restart nginx`.

### `JWT_SECRET=dev-secret-change-me` in production

The app fails fast:

```
startup_failed error=production mode with dev secret app_env=production
```

Set a proper secret in `.env`:

```bash
# Generate a 40-character random secret
python -c "import secrets; print(secrets.token_urlsafe(40))"
```

Then copy the output into `JWT_SECRET=` in `.env` and restart:
`docker compose restart api`.

## Upgrading

After pulling new code that includes database schema changes:

```bash
# Pull latest image or rebuild
docker compose build api

# Apply Alembic migrations
docker compose exec api python -m cli migrate

# Restart to pick up any code changes
docker compose up -d api worker beat
```

The `python -m cli migrate` command runs `alembic upgrade head`. Check the
Alembic output for any migration warnings before proceeding in production.

To review what migrations are pending without applying them:

```bash
docker compose exec api python -m alembic current
docker compose exec api python -m alembic heads
```

## Decommissioning

To tear down the stack and remove all persistent data (irreversible):

```bash
docker compose down -v
```

The `-v` flag removes the named volumes (`postgres-data`, `ollama-models`,
`blobs`). Take a backup first if you need the data.

TLS certificates (`nginx/dev.crt`, `nginx/dev.key`) are gitignored and remain on
disk. Delete them manually if needed: `rm nginx/dev.crt nginx/dev.key`.
