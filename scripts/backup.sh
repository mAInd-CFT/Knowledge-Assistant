#!/usr/bin/env bash
set -Eeuo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT_DIR"
STAMP="$(date -u +%Y%m%dT%H%M%SZ)"
DEST="${1:-$ROOT_DIR/backups/$STAMP}"
mkdir -p "$DEST"

# PostgreSQL logical backup.
docker compose exec -T postgres sh -lc 'pg_dump -U "$POSTGRES_USER" -d "$POSTGRES_DB" -Fc' > "$DEST/postgres.dump"

# Qdrant persists its collection in a named Docker volume. Stop it briefly so
# the filesystem archive is consistent, then restart it even if tar fails.
QDRANT_CID="$(docker compose ps -q qdrant)"
if [[ -z "$QDRANT_CID" ]]; then
  echo "Qdrant container is not running; start the stack first." >&2
  exit 1
fi
VOLUME="$(docker inspect --format '{{range .Mounts}}{{if eq .Destination "/qdrant/storage"}}{{.Name}}{{end}}{{end}}' "$QDRANT_CID")"
if [[ -z "$VOLUME" ]]; then
  echo "Could not resolve Qdrant storage volume." >&2
  exit 1
fi
restart_qdrant() { docker compose start qdrant >/dev/null 2>&1 || true; }
trap restart_qdrant EXIT
docker compose stop qdrant >/dev/null
docker run --rm -v "$VOLUME:/data:ro" -v "$DEST:/backup" alpine:3.20 \
  sh -c 'cd /data && tar -czf /backup/qdrant-storage.tar.gz .'
restart_qdrant
trap - EXIT

cp .env.example "$DEST/env.example.reference"
cat > "$DEST/README.txt" <<EOF
Backup created at UTC: $STAMP
Contains PostgreSQL logical dump and Qdrant storage volume archive.
Keep the real .env and its secrets separately in an approved secret store.
Restore only into a stopped, compatible deployment after testing the procedure.
EOF

echo "Backup created: $DEST"
