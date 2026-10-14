#!/usr/bin/env bash
# Renew Let's Encrypt certificates and reload Nginx after success.
# Uses the lineage renewal config written at issuance (shortlived IP cert).
# Do not pass unsupported --profile; certbot renew reuses the stored profile.
#
# Docker privileges: run as a docker-group user or with sudo, preserving
# working directory (see deploy/PUBLIC_ACCESS.md).
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/../.." && pwd)"
cd "$ROOT"

docker compose -f docker-compose.prod.yml --profile certs run --rm --entrypoint certbot certbot \
  renew \
  --cert-name historyit \
  --deploy-hook "echo renewed"

# Reload Nginx in the web container so new certs are picked up.
if docker compose -f docker-compose.prod.yml exec -T web nginx -s reload; then
  echo "Nginx reloaded."
else
  echo "Warning: certificate renew finished but Nginx reload failed." >&2
  exit 1
fi
