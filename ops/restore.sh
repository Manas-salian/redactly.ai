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
