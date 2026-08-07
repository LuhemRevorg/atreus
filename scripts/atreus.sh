#!/bin/bash
# Wrapper launchd uses to start Atreus. launchd gives processes a bare
# environment, so PATH has to be set explicitly for ollama and ffplay.
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"

export PATH="/opt/homebrew/bin:/usr/bin:/bin:/usr/sbin:/sbin"
export PYTHONUNBUFFERED=1

cd "$ROOT"
exec "$ROOT/.venv/bin/python" "$ROOT/src/main.py"
