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
