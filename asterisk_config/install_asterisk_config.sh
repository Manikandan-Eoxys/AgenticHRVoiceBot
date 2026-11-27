#!/bin/bash
# ============================================================================
# asterisk_config/install_asterisk_config.sh
# Copies Asterisk configuration files into /etc/asterisk and reloads Asterisk
# ============================================================================

set -e

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
AST_DIR="/etc/asterisk"
BACKUP_DIR="/etc/asterisk/backup_$(date +%Y%m%d_%H%M%S)"
REPO_DIR="$(cd "$SCRIPT_DIR/.." && pwd)"

echo "📦 Installing Asterisk configuration for Agentic HR VoiceBot..."

if [ "$EUID" -ne 0 ]; then
  echo "⚠️  Please run as root or with sudo:"
  echo "   sudo bash $0"
  exit 1
fi

echo "📁 Creating backup directory at $BACKUP_DIR..."
mkdir -p "$BACKUP_DIR"

# Config files to install
CONFIG_FILES=("pjsip.conf" "extensions.conf" "ari.conf" "http.conf" "rtp.conf")

for f in "${CONFIG_FILES[@]}"; do
  if [ -f "$AST_DIR/$f" ]; then
    cp "$AST_DIR/$f" "$BACKUP_DIR/$f"
    echo "  Backed up $AST_DIR/$f -> $BACKUP_DIR/$f"
  fi
  if [ -f "$SCRIPT_DIR/$f" ]; then
    cp "$SCRIPT_DIR/$f" "$AST_DIR/$f"
    chown asterisk:asterisk "$AST_DIR/$f" 2>/dev/null || true
    chmod 640 "$AST_DIR/$f" 2>/dev/null || true
    echo "  ✅ Installed $AST_DIR/$f"
  fi
done

# Setup TLS keys for WebRTC WSS (port 8089)
KEYS_DIR="$AST_DIR/keys"
mkdir -p "$KEYS_DIR"

if [ -f "$REPO_DIR/hr_voice_agent_asterisk_pipeline/certs/webcall.crt" ] && [ -f "$REPO_DIR/hr_voice_agent_asterisk_pipeline/certs/webcall.key" ]; then
  echo "🔑 Installing WebRTC TLS certificates from certs/..."
  cp "$REPO_DIR/hr_voice_agent_asterisk_pipeline/certs/webcall.crt" "$KEYS_DIR/asterisk.crt"
  cp "$REPO_DIR/hr_voice_agent_asterisk_pipeline/certs/webcall.key" "$KEYS_DIR/asterisk.key"
elif [ ! -f "$KEYS_DIR/asterisk.crt" ]; then
  echo "🔑 Generating self-signed TLS certificate for Asterisk WebRTC WSS..."
  openssl req -x509 -newkey rsa:2048 -nodes -days 365 \
    -keyout "$KEYS_DIR/asterisk.key" -out "$KEYS_DIR/asterisk.crt" \
    -subj "/CN=localhost" -addext "subjectAltName=DNS:localhost,IP:127.0.0.1"
fi

chown -R asterisk:asterisk "$KEYS_DIR" 2>/dev/null || true
chmod 600 "$KEYS_DIR"/* 2>/dev/null || true
echo "  ✅ TLS Keys configured in $KEYS_DIR"

echo "🔄 Reloading Asterisk modules..."
asterisk -rx "core reload" || true
asterisk -rx "pjsip reload" || true
asterisk -rx "dialplan reload" || true
asterisk -rx "http reload" || true
asterisk -rx "module reload res_http_websocket.so" || true
asterisk -rx "module reload res_pjsip_transport_websocket.so" || true
asterisk -rx "module reload res_ari.so" || true

echo ""
echo "🎉 Asterisk configuration installed and reloaded successfully!"
echo "---------------------------------------------------------------"
echo "Asterisk WebRTC Configuration:"
echo "  WebSocket (WS):    ws://<server-ip>:8088/ws"
echo "  Secure WS (WSS):   wss://<server-ip>:8089/ws"
echo "  WebRTC Endpoint:   webrtc-client (password: webrtc_secret_pass)"
echo "  WebRTC Extension:  200"
echo "  Call Limits:       10 global concurrent calls, 1 per caller"
echo "  Rejection:         Asterisk Busy(5) -> SIP 486 Busy Here"
echo "---------------------------------------------------------------"
