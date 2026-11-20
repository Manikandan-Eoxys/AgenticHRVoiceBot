#!/usr/bin/env bash
# Self-signed TLS cert for local/LAN testing (browser will show a one-time warning).
# Usage: scripts/gen_cert.sh [hostname-or-ip ...]    default: localhost 127.0.0.1
set -euo pipefail
cd "$(dirname "$0")/.."
mkdir -p certs
names=("${@:-localhost}")
[ $# -eq 0 ] && names=(localhost 127.0.0.1)
san=""; i=0
for n in "${names[@]}"; do
  if [[ "$n" =~ ^[0-9.]+$ ]]; then san+="IP:$n,"; else san+="DNS:$n,"; fi
done
openssl req -x509 -newkey rsa:2048 -nodes -days 365 \
  -keyout certs/webcall.key -out certs/webcall.crt \
  -subj "/CN=${names[0]}" -addext "subjectAltName=${san%,}"
chmod 600 certs/webcall.key
echo "✅ certs/webcall.crt + certs/webcall.key created for: ${names[*]}"
