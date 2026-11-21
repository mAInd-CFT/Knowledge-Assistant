#!/usr/bin/env bash
set -Eeuo pipefail
if [[ $# -ne 1 ]]; then echo "Usage: $0 /path/to/backup-directory" >&2; exit 2; fi
BACKUP="$(cd "$1" && pwd)"
cd "$(dirname "${BASH_SOURCE[0]}")/.."
[[ -f "$BACKUP/postgres.dump" && -f "$BACKUP/qdrant-storage.tar.gz" ]] || { echo "Backup files missing" >&2; exit 1; }

echo "WARNING: this replaces current database and Qdrant data."
read -r -p "Type RESTORE to continue: " CONFIRM
[[ "$CONFIRM" == "RESTORE" ]] || exit 1

docker compose stop backend frontend ingestion ingestion-ocr crawler qdrant 2>/dev/null || true
docker compose exec -T postgres sh -lc 'dropdb -U "$POSTGRES_USER" --if-exists "$POSTGRES_DB" && createdb -U "$POSTGRES_USER" "$POSTGRES_DB"'
docker compose exec -T postgres sh -lc 'pg_restore -U "$POSTGRES_USER" -d "$POSTGRES_DB" --clean --if-exists' < "$BACKUP/postgres.dump"
QDRANT_CID="$(docker compose ps -aq qdrant | head -n1)"
VOLUME="$(docker inspect --format '{{range .Mounts}}{{if eq .Destination "/qdrant/storage"}}{{.Name}}{{end}}{{end}}' "$QDRANT_CID")"
[[ -n "$VOLUME" ]] || { echo "Could not resolve Qdrant volume; recreate qdrant service first." >&2; exit 1; }
docker run --rm -v "$VOLUME:/data" -v "$BACKUP:/backup:ro" alpine:3.20 sh -c 'rm -rf /data/* /data/.[!.]* 2>/dev/null || true; cd /data && tar -xzf /backup/qdrant-storage.tar.gz'
docker compose up -d postgres qdrant backend frontend
echo "Restore complete. Verify /health/ready and knowledge statistics."
