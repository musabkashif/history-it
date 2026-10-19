#!/usr/bin/env bash
# Operator entry point. Activation decisions live in deploy_release.py.
set -euo pipefail

if ! command -v python3 >/dev/null 2>&1; then
  echo "historyit-deploy: python3 is required on the host" >&2
  exit 1
fi

here="$(cd "$(dirname "$0")" && pwd)"
exec python3 "$here/deploy_release.py" "$@"
