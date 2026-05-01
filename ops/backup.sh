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
