#!/usr/bin/env bash
set -euo pipefail

# Runs the QA Agent FastAPI app locally.
#
# Usage:
#   ./run.sh            # starts on http://127.0.0.1:8000
#   PORT=9000 ./run.sh  # custom port

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
APP_DIR="$ROOT_DIR/qa_agent"

PYTHON_BIN="${PYTHON_BIN:-python3}"
VENV_DIR="${VENV_DIR:-$APP_DIR/.venv}"
HOST="${HOST:-127.0.0.1}"
PORT="${PORT:-8000}"

cd "$APP_DIR"

if [[ ! -d "$VENV_DIR" ]]; then
  "$PYTHON_BIN" -m venv "$VENV_DIR"
fi

"$VENV_DIR/bin/python" -m pip install --upgrade pip
"$VENV_DIR/bin/pip" install -r requirements.txt

# Playwright is optional at runtime unless you use browser flows; install browsers if needed:
# "$VENV_DIR/bin/python" -m playwright install

exec "$VENV_DIR/bin/python" -m uvicorn qa_agent.main:app --host "$HOST" --port "$PORT" --reload

