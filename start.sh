#!/usr/bin/env bash
# Startar konsultsystemet (macOS/Linux). Stoppa med Ctrl+C.
cd "$(dirname "$0")"
if ! command -v uv >/dev/null 2>&1; then
  echo "Installerar uv (Python-verktyg) ..."
  curl -LsSf https://astral.sh/uv/install.sh | sh
  export PATH="$HOME/.local/bin:$PATH"
fi
uv run python manage.py starta "$@"
