# API Reference

This document covers the API surface that exists in **Foundation V1**.
For the full target API surface (jobs, detections, redactions, WebSocket, OIDC,
admin/users, admin/audit), see spec §5.3.

## Versioning

All endpoints are prefixed with `/api/v1`. The v1 surface is stable for the
duration of V1. Breaking changes (if any) will bump to `/api/v2`.

The prefix is driven by `API_V1_PREFIX` in `server/app/config.py`.

## Interactive docs

FastAPI auto-generates Swagger UI and ReDoc from the OpenAPI schema:

- **Swagger UI:** `https://localhost/api/v1/docs`
- **ReDoc:** `https://localhost/api/v1/redoc`

Neither is authentication-protected by default. In production, consider blocking
these paths at Nginx if you do not want the schema publicly readable.

## Authentication

Two credential types are accepted on every protected endpoint. Both flow through
the same `current_user` FastAPI dependency (`server/app/core/security.py`).

### JWT bearer

Obtain tokens from `POST /api/v1/auth/login`. Pass the access token in every
subsequent request:

```
Authorization: Bearer <access_token>
```

- Access token TTL: 15 minutes (configurable via `JWT_ACCESS_TTL_MINUTES`)
- Refresh token TTL: 7 days (configurable via `JWT_REFRESH_TTL_DAYS`)
- Algorithm: HS256 with `JWT_SECRET`

A refresh token endpoint is not yet implemented (Plan 5). To continue a session
after the access token expires, log in again.

### API key

Pass a scoped key in the `X-API-Key` header:

```
X-API-Key: rk_<8-char-prefix><32-char-secret>
```

API keys are created by admins and never re-exposed after creation. The 8-char
prefix is stored unhashed to allow O(1) row lookup before running Argon2
verification (see `server/app/core/security.py`).

Valid scopes: `jobs:create`, `jobs:read`, `admin:audit`, `admin:users`.

## Endpoints

### `POST /api/v1/auth/login`

Authenticate with email and password. Returns a JWT access token and refresh
token.

**Rate limit:** 10 requests per minute per IP address (slowapi).

**Auth required:** No.

**Request body:**

```json
{
  "email": "you@example.com",
  "password": "your-password"
}
```

**Response `200`:**

```json
{
  "access_token": "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9...",
  "refresh_token": "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9...",
  "token_type": "Bearer"
}
```

**Example:**

```bash
curl -k -X POST https://localhost/api/v1/auth/login \
  -H 'Content-Type: application/json' \
  -d '{"email":"you@example.com","password":"change-me-now"}'
```

**Error responses:**

| Status | Condition |
|---|---|
| `401` | Invalid credentials (wrong password or inactive user) |
| `429` | Rate limit exceeded |
| `422` | Malformed request body |

---

### `GET /api/v1/auth/me`

Return the identity of the currently authenticated user.

**Auth required:** JWT bearer or `X-API-Key`.

**Response `200`:**

```json
{
  "id": "550e8400-e29b-41d4-a716-446655440000",
  "email": "you@example.com",
  "role": "admin",
  "tenant_id": "7c9e6679-7425-40de-944b-e07fc1f90ae7"
}
```

**Example:**

```bash
TOKEN="eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9..."

curl -k https://localhost/api/v1/auth/me \
  -H "Authorization: Bearer $TOKEN"
```

---

### `POST /api/v1/admin/api-keys`

Create a new API key. The plaintext key is returned exactly once; it is not
recoverable afterward.

**Auth required:** JWT or API key with `admin` role.

**Request body:**

```json
{
  "name": "ci-pipeline",
  "scopes": ["jobs:create", "jobs:read"]
}
```

Valid scopes: `jobs:create`, `jobs:read`, `admin:audit`, `admin:users`.

**Response `201`:**

```json
{
  "id": "d290f1ee-6c54-4b01-90e6-d701748f0851",
  "name": "ci-pipeline",
  "scopes": ["jobs:create", "jobs:read"],
  "plaintext_key": "rk_Ab3xYz9qLoremIpsumDolorSitAmetConsecteturAdipiscingElitBlah"
}
```

Store `plaintext_key` immediately — it is not stored in any retrievable form.

**Example:**

```bash
curl -k -X POST https://localhost/api/v1/admin/api-keys \
  -H "Authorization: Bearer $TOKEN" \
  -H 'Content-Type: application/json' \
  -d '{"name":"ci-pipeline","scopes":["jobs:create","jobs:read"]}'
```

---

### `GET /api/v1/admin/api-keys`

List all API keys for the current tenant. Does not return plaintext keys.

**Auth required:** Admin role.

**Response `200`:**

```json
[
  {
    "id": "d290f1ee-6c54-4b01-90e6-d701748f0851",
    "name": "ci-pipeline",
    "scopes": ["jobs:create", "jobs:read"],
    "created_at": "2026-05-01T10:00:00Z",
    "revoked_at": null
  }
]
```

`revoked_at` is non-null for revoked keys. Revoked keys remain in the list for
audit purposes.

**Example:**

```bash
curl -k https://localhost/api/v1/admin/api-keys \
  -H "Authorization: Bearer $TOKEN"
```

---

### `DELETE /api/v1/admin/api-keys/{id}`

Revoke an API key. Sets `revoked_at` to the current timestamp. Idempotent: if
the key is already revoked, returns `204` without error.

**Auth required:** Admin role.

**Path parameter:** `id` — UUID of the key.

**Response `204`:** No content.

**Response `404`:** Key not found or belongs to a different tenant.

**Example:**

```bash
KEY_ID="d290f1ee-6c54-4b01-90e6-d701748f0851"

curl -k -X DELETE "https://localhost/api/v1/admin/api-keys/$KEY_ID" \
  -H "Authorization: Bearer $TOKEN"
```

---

### `GET /api/v1/health`

Reports liveness of the Postgres and Redis dependencies.

**Auth required:** No.

**Response `200` (healthy):**

```json
{
  "status": "healthy",
  "components": {
    "database": true,
    "redis": true
  }
}
```

**Response `200` (degraded):**

```json
{
  "status": "degraded",
  "components": {
    "database": true,
    "redis": false
  }
}
```

The HTTP status is always `200`. Use the `status` field to drive alerts.

**Example:**

```bash
curl -k https://localhost/api/v1/health
```

---

### `GET /api/v1/metrics`

Prometheus metrics in text exposition format. Suitable as a scrape target for
a Prometheus server.

**Auth required:** No. (Known V1 limitation — see [docs/security.md](security.md).)

**Response:** `Content-Type: text/plain; version=0.0.4`

**Declared counters and histograms** (defined in `server/app/api/v1/health.py`):

| Metric | Type | Labels | Purpose |
|---|---|---|---|
| `redactly_jobs_total` | Counter | `status`, `tenant` | Jobs created by final status |
| `redactly_job_duration_seconds` | Histogram | `stage` | Time per pipeline stage |
| `redactly_detections_total` | Counter | `layer`, `type` | Detections produced per layer |
| `redactly_llm_verifier_calls_total` | Counter | `result` | LLM verifier invocations |
| `redactly_redaction_verify_failures_total` | Counter | — | Post-redaction verify failures (**Sev-1 — alert on any non-zero**) |

Counters are declared now so downstream dashboards and alert rules can reference
stable metric names before the pipeline tasks land. They will remain at zero
until Plans 3 and 4 ship.

**Example:**

```bash
curl -k https://localhost/api/v1/metrics
```

---

## Endpoints coming in subsequent plans

The following endpoints are defined in spec §5.3 and will be added in Plans 2–5.
None exist in the current codebase.

Note: The document parsing pipeline (`parse_job` task, Document parsing CLI,
blob persistence) shipped in Plan 2. The upload endpoint and job-lifecycle APIs
that trigger parsing remain Plan 5.

**Plan 5 — Job lifecycle:**

- `POST /api/v1/jobs` — multipart upload + config; returns `202` with `job_id`
- `GET /api/v1/jobs` — paginated list with status/created_by filters
- `GET /api/v1/jobs/{id}` — state + detection counts
- `DELETE /api/v1/jobs/{id}` — cancel or expire immediately
- `GET /api/v1/jobs/{id}/preview/{page}` — signed URL to page PNG

**Plan 5 — Detections:**

- `GET /api/v1/jobs/{id}/detections` — paginated, filterable
- `PATCH /api/v1/jobs/{id}/detections` — bulk approve/reject/edit
- `POST /api/v1/jobs/{id}/detections/auto-approve` — auto-approve by threshold

**Plan 5 — Redaction:**

- `POST /api/v1/jobs/{id}/commit` — trigger redaction; requires no pending detections
- `GET /api/v1/jobs/{id}/download` — signed URL to redacted artifact

**Plan 5 — Admin:**

- `GET /api/v1/admin/audit` — paginated audit log with CSV export
- `GET /api/v1/admin/users` — user list
- `POST /api/v1/admin/users` — invite user

**Plan 5 — OIDC:**

- `GET /api/v1/auth/oidc/login`
- `GET /api/v1/auth/oidc/callback`

**Plan 5 — Real-time:**

- `WS /api/v1/ws/jobs/{id}` — WebSocket for job state + per-stage progress

## Error responses

All errors follow FastAPI's default shape:

```json
{
  "detail": "human-readable message"
}
```

Common status codes:

| Code | Meaning |
|---|---|
| `401` | Not authenticated (missing or invalid token / API key) |
| `403` | Authenticated but lacks required role or scope |
| `404` | Resource not found or belongs to a different tenant |
| `422` | Request validation failure (missing field, wrong type, etc.) |
| `429` | Rate limit exceeded |
