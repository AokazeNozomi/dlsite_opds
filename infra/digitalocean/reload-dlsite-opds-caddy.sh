#!/usr/bin/env bash
# Regenerate Caddyfile from app .env files and restart the shared Caddy container.
set -euo pipefail

CADDY_DIR=/opt/dlsite-opds-caddy
CADDYFILE="$CADDY_DIR/Caddyfile"

mkdir -p "$CADDY_DIR"
if [ "$(id -u)" -eq 0 ]; then
  chown -R deploy:deploy "$CADDY_DIR"
fi

{
  for app_dir in /opt/dlsite-opds-nightly /opt/dlsite-opds; do
    if [ ! -s "$app_dir/.env" ]; then
      continue
    fi

    # shellcheck disable=SC1090
    set -a
    source "$app_dir/.env"
    set +a

    if [ -z "${OPDS_DOMAIN:-}" ] || [ -z "${CONTAINER_NAME:-}" ]; then
      echo "Skipping $app_dir: OPDS_DOMAIN and CONTAINER_NAME are required in .env" >&2
      continue
    fi

    external_port="${OPDS_EXTERNAL_PORT:-2580}"
    printf '%s:%s {\n    reverse_proxy %s:2580\n}\n\n' \
      "$OPDS_DOMAIN" "$external_port" "$CONTAINER_NAME"
  done
} > "$CADDYFILE"

if [ ! -s "$CADDYFILE" ]; then
  echo "No app environments configured; refusing to start Caddy." >&2
  exit 1
fi

cd "$CADDY_DIR"
docker compose -f docker-compose.caddy.yml up -d
