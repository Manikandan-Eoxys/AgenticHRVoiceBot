#!/usr/bin/env bash
# Start the HR voice pipeline (Asterisk ARI + AudioSocket) AND the browser web-call landing page.
#   ./run_webcall.sh            -> installs deps if needed, makes a dev cert if none, runs agent.py
#   ./run_webcall.sh selftest   -> end-to-end WebRTC<->AudioSocket check (no Asterisk/Deepgram needed)
set -euo pipefail
cd "$(dirname "$0")"

[ -f .env ] || { cp .env.example .env; echo "📝 Created .env from .env.example - fill in your keys, then re-run."; exit 1; }

PY="${PYTHON:-python3}"
if ! $PY -c "import aiortc, av" 2>/dev/null; then
  echo "📦 Installing dependencies..."
  $PY -m pip install -r requirements.txt
fi

if [ "${1:-}" = "selftest" ]; then exec $PY -m webcall.selftest; fi

set -a; . ./.env; set +a
export WEBCALL_ENABLED=1
if [ -n "${WEBCALL_CERT_FILE:-}" ] && [ ! -f "$WEBCALL_CERT_FILE" ]; then
  echo "🔐 No cert at $WEBCALL_CERT_FILE - generating a self-signed dev cert"
  ./scripts/gen_cert.sh localhost 127.0.0.1 "$(hostname -I 2>/dev/null | awk '{print $1}')" || true
fi

scheme=http; [ -n "${WEBCALL_CERT_FILE:-}" ] && scheme=https
echo "🌐 Landing page : $scheme://localhost:${WEBCALL_PORT:-8443}/"
echo "📞 Phone calls  : Asterisk ARI (${ARI_APP_NAME:-hr_voicebot}) -> AudioSocket :${AST_BRIDGE_PORT:-9090}"
exec $PY agent.py
