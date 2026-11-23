#!/usr/bin/env bash
# Start the HR voice pipeline (Asterisk ARI + AudioSocket) AND the browser web-call landing page.
# Reads configuration ONLY from the outer project .env (../.env).
#   ./run_webcall.sh            -> runs agent.py with the web call front door enabled
#   ./run_webcall.sh selftest   -> end-to-end WebRTC<->AudioSocket check (no Asterisk/Deepgram needed)
set -euo pipefail
cd "$(dirname "$0")"

ENV_FILE="../.env"

if [ -z "${PYTHON:-}" ]; then
  if [ -n "${VIRTUAL_ENV:-}" ] && [ -x "$VIRTUAL_ENV/bin/python" ]; then
    PY="$VIRTUAL_ENV/bin/python"
  elif [ -x "../.venv/bin/python" ]; then
    PY="../.venv/bin/python"
  elif [ -x ".venv/bin/python" ]; then
    PY=".venv/bin/python"
  else
    PY="python3"
  fi
else
  PY="$PYTHON"
fi

if ! $PY -c "import aiortc, av" 2>/dev/null; then
  echo "📦 Installing dependencies..."
  $PY -m pip install -r requirements.txt
fi

if [ "${1:-}" = "selftest" ]; then exec $PY -m webcall.selftest; fi

if [ ! -f "$ENV_FILE" ]; then
  echo "❌ $(cd .. && pwd)/.env not found. Create it in the project root."
  exit 1
fi
if [ -f ./.env ]; then
  echo "⚠️  Found $(pwd)/.env - agent.py loads this one INSTEAD of the outer .env."
  echo "    Delete it to use only ../.env:  rm $(pwd)/.env"
  exit 1
fi

# Read only the values this script needs (the .env is NOT sourced, so spaces in values are fine)
read_env() { $PY -c "import sys; from dotenv import dotenv_values as d; print(d(sys.argv[1]).get(sys.argv[2]) or '')" "$ENV_FILE" "$1"; }
CERT="$(read_env WEBCALL_CERT_FILE)"
PORT="$(read_env WEBCALL_PORT)"
export WEBCALL_ENABLED=1

if [ -n "$CERT" ] && [ ! -f "$CERT" ]; then
  echo "🔐 No cert at $CERT - generating a self-signed dev cert"
  ./scripts/gen_cert.sh localhost 127.0.0.1 "$(hostname -I 2>/dev/null | awk '{print $1}')" || true
fi

scheme=http; [ -n "$CERT" ] && scheme=https
echo "🌐 Landing page : $scheme://localhost:${PORT:-8443}/"
exec $PY agent.py