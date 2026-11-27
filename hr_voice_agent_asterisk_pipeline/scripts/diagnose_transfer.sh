#!/usr/bin/env bash
# scripts/diagnose_transfer.sh - capture a coordinator transfer and say who hung up.
#   sudo bash scripts/diagnose_transfer.sh
# Then call your Plivo number, say your employee ID, ask for the coordinator, answer on the coordinator phone,
# wait for the drop, press Ctrl-C.
set -e
HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
OUT="/tmp/sip_trace_$(date +%H%M%S).log"
asterisk -rx "pjsip set logger on" >/dev/null
echo "📡 Capturing SIP to $OUT  - place the test call now, Ctrl-C when the call has dropped."
trap 'echo; asterisk -rx "pjsip set logger off" >/dev/null; python3 "$HERE/sip_trace_summary.py" "$OUT"; exit 0' INT
asterisk -rvvvvv | tee "$OUT" >/dev/null
