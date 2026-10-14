#!/usr/bin/env bash
# Obtain a Let's Encrypt short-lived IP certificate via webroot.
# Prerequisites: Elastic IP attached, ports 80/443 open, PUBLIC_IPV4 set,
# bootstrap Nginx overlay running (ACME only — no app on HTTP).
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/../.." && pwd)"
cd "$ROOT"

: "${PUBLIC_IPV4:?Set PUBLIC_IPV4 to the EC2 Elastic IP (IPv4) before issuing a certificate}"
: "${CERTBOT_EMAIL:?Set CERTBOT_EMAIL to a contact address for Let's Encrypt}"

mkdir -p server/logs

echo "Starting ACME-only Nginx bootstrap (no app / no API on HTTP)..."
docker compose -f docker-compose.prod.yml -f docker-compose.bootstrap.yml up -d --no-deps web

echo "Requesting short-lived IP certificate for ${PUBLIC_IPV4}..."
docker compose -f docker-compose.prod.yml --profile certs run --rm --entrypoint certbot certbot \
  certonly \
  --webroot -w /var/www/certbot \
  --agree-tos --non-interactive \
  -m "${CERTBOT_EMAIL}" \
  --cert-name historyit \
  --profile shortlived \
  -d "${PUBLIC_IPV4}"

echo "Certificate issued under /etc/letsencrypt/live/historyit/"
echo "Next: docker compose -f docker-compose.prod.yml --env-file .env up -d --build"
echo "Then enable renewal cron: see deploy/scripts/renew-certs.sh"
