#!/bin/bash
# run_production.sh — Starts the HR Voicebot as a 24/7 background process
DIR="$( cd "$( dirname "${BASH_SOURCE[0]}" )" >/dev/null 2>&1 && pwd )"
cd "$DIR"

# Stop existing instance if running
pkill -f "python main.py" 2>/dev/null || true

# Run in production mode with nohup
nohup "$DIR/.venv/bin/python" main.py start > voicebot.log 2>&1 &
PID=$!
echo "✅ HR Voicebot started in 24/7 background mode (PID: $PID)"
echo "📄 Logs streaming to: $DIR/voicebot.log"
echo "   Monitor logs with: tail -f voicebot.log"
