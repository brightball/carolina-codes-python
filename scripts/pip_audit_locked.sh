#!/bin/sh
# Audit the locked runtime dependency set exported from uv.lock.
set -eu
root="$(CDPATH= cd -- "$(dirname "$0")/.." && pwd)"
cd "$root"
tmp="${root}/.requirements-audit.txt"
uv export --frozen --no-dev --no-hashes -o "$tmp"
if command -v pip-audit >/dev/null 2>&1; then
  pip-audit -r "$tmp" --progress-spinner off
else
  uv run pip-audit -r "$tmp" --progress-spinner off
fi
