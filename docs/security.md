# Security

This document describes the trust model, authentication design, known limitations,
and privacy controls for RedactLy V1. Intended audience: security reviewers,
compliance teams, and anyone running a pentest.

For the full security design rationale, see spec §6.6.

## Trust boundaries

```
OUTSIDE (untrusted)
  Clients (browsers, API consumers, CI pipelines)

NGINX (TLS boundary)
  Terminates TLS; proxies /api/ to the api container.
  Never receives plaintext traffic in production.

INSIDE (trusted network — same Docker bridge)
  api container      FastAPI application
  worker container   Celery job worker
  beat container     Celery scheduler
  postgres           Database
  redis              Broker + result backend
  ollama             Local LLM runtime
  blob volume        Local filesystem for uploaded and redacted documents

OUTSIDE (never touched in any data path)
  Cloud APIs, external ML services, SaaS products
```

Documents and detected PII never leave the Docker network. The LLM verifier
(Plan 3) calls Ollama inside the same network — not an external API.

## Authentication

### Password-based login

Passwords are hashed with **Argon2** using the `argon2-cffi` library defaults.
The library ships memory-hard parameters tuned for the hardware it runs on.
Source: `server/app/core/auth.py`.

### JWT

- Algorithm: **HS256** with `JWT_SECRET`.
- Access token TTL: 15 minutes (configurable).
- Refresh token TTL: 7 days (configurable).
- Claims: `sub` (user UUID), `tenant` (tenant UUID), `role`, `type` (`access`
  or `refresh`), `iat`, `exp`.
- The `type` claim is verified on every request — a refresh token cannot be
  used where an access token is required.
- In production (`APP_ENV=production`), the app refuses to start if `JWT_SECRET`
  is the default `"dev-secret-change-me"`. This guard is in `server/app/main.py`.

### API keys

- Format: `rk_<8-char-prefix><32+-char-secret>` (44+ characters).
- The **8-char lookup prefix** is stored unhashed, indexed. At authentication
  time, the single matching `ApiKey` row is fetched in O(1) by prefix, then
  Argon2-verified against the full plaintext key.
- The full key is **hashed with Argon2** (`hashed_key` column). The plaintext is
  shown exactly once at creation and never stored.
- Revocation sets `revoked_at`; the lookup query filters `revoked_at IS NULL`.
- Scopes (`jobs:create`, `jobs:read`, `admin:audit`, `admin:users`) are checked
  by the route's `require_role` dependency (scoped enforcement comes in Plan 5
  when job/detection routes land).

## Authorization

Three roles, enforced at the FastAPI dependency layer:

| Role | Capabilities |
|---|---|
| `admin` | All endpoints, including API-key management and admin routes |
| `reviewer` | Job review and detection approval (Plan 5+) |
| `viewer` | Read-only access to jobs and detections (Plan 5+) |

The `require_role(*allowed)` decorator in `server/app/core/security.py` wraps
`current_user` and raises `HTTP 403` if the authenticated user's role is not in
the allowed set.

In V1, `tenant_id` is fixed — all users belong to the same default tenant. The
`current_user` dependency does not accept a tenant override in the request. This
means tenant isolation is a V2 concern; in V1 there is nothing to isolate from.

## Audit trail

`audit_events` rows are written on:
- Successful login (`auth.login`)
- API key creation (`api_key.create`)
- API key revocation (`api_key.revoke`)
- (Plans 2–5 will add job state transitions, detection approvals, etc.)

The application code never issues `UPDATE` or `DELETE` against `audit_events`.
This is enforced by code convention in V1.

**Known gap (V2 hardening):** Postgres-level `REVOKE UPDATE, DELETE ON audit_events
FROM redactly_app` is planned but not yet applied. Until that grant is in place,
a compromised application process could technically modify audit rows. This is
tracked as a V2 hardening item per spec §5.6.

## Privacy controls

### Blob expiry

Per-tenant `retention_hours` (default 24 h) controls when the Celery beat task
purges source and redacted blobs from storage and nulls their URIs on the `jobs`
row.

### Detection text purge

`detections.text`, `detections.approved_text`, and `detections.bbox` are nulled
at expiry alongside the blobs. These columns contain the actual PII substrings
the system detected; persisting them after the source document expires would
defeat the privacy guarantee. The decision is recorded in spec §5.7 and the
column comment in `server/app/db/models.py`.

`entity_type`, `score`, `confidence_bucket`, `source_layer`, and `evidence_jsonb`
are preserved for analytics. Any free-text PII that might appear in
`evidence_jsonb.llm.reason` is redacted to `[purged]` at expiry time (Plan 5).

### Audit payloads carry no PII

The `write_audit_event(...)` helper documents that callers are responsible for
redacting or omitting sensitive values from the `payload` argument. The audit
writer never injects field values automatically. See `server/app/core/audit.py`.

## Rate limiting

`POST /api/v1/auth/login` is limited to **10 requests per minute per IP** via
`slowapi`. This mitigates credential-stuffing attacks without locking out users
behind a shared NAT.

The `Limiter` is keyed by `get_remote_address`. In production behind a reverse
proxy, ensure Nginx forwards the real client IP via `proxy_set_header X-Real-IP
$remote_addr` (already set in `nginx/nginx.conf`).

## CORS

- **Development** (`APP_ENV=development`): `allow_origins=["*"]` — any origin
  is accepted.
- **Production** (`APP_ENV=production`): origin is hardcoded to
  `"https://redactly.ai"` as a fallback.

**Known gap:** production CORS origins are currently hardcoded in
`server/app/main.py`. Making them config-driven (`ALLOWED_ORIGINS` env var) is
a V2 follow-up.

## Known V1 limitations

The following gaps were identified during the foundation review and deliberately
deferred to V2 hardening:

| Gap | Risk | V2 plan |
|---|---|---|
| `/api/v1/metrics` is unauthenticated | Exposes job counts, detection rates, and tenant identifiers to anyone who can reach port 443 | Add `current_user` dependency or an IP allowlist at Nginx |
| `get_db` dependency does not call `db.rollback()` on exception | A failed transaction may leave the connection in an error state until the pool recycles it | Wrap the `finally` block to rollback before close |
| Three independent `PasswordHasher` instances (`core/auth.py`, `core/security.py`, `admin.py`) | Slightly inconsistent Argon2 parameters if defaults change between `argon2-cffi` upgrades | Consolidate to a single module-level instance |
| No CSRF protection on state-changing endpoints | Session-hijacking risk if the frontend uses cookie-based auth (not current, but planned) | Add CSRF token middleware when the frontend login lands in Plan 6 |
| Postgres-level UPDATE/DELETE grants on `audit_events` not yet applied | Compromised app process could modify audit records | Apply `REVOKE` in a Plan 5 migration |

These are not blocking for a development or internal deployment, but should be
addressed before exposing an instance to untrusted users.

## Security contacts

Report vulnerabilities by opening a private security advisory on the GitHub
repository or by emailing the maintainer directly.
