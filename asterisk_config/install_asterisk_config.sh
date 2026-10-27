#!/bin/bash
# ============================================================================
# asterisk_config/install_asterisk_config.sh
# Copies Asterisk configuration files into /etc/asterisk and reloads Asterisk
# ============================================================================

set -e

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
AST_DIR="/etc/asterisk"
BACKUP_DIR="/etc/asterisk/backup_$(date +%Y%m%d_%H%M%S)"

echo "📦 Installing Asterisk configuration for Agentic HR VoiceBot..."

if [ "$EUID" -ne 0 ]; then
  echo "⚠️  Please run as root or with sudo:"
  echo "   sudo bash $0"
  exit 1
fi

echo "📁 Creating backup directory at $BACKUP_DIR..."
mkdir -p "$BACKUP_DIR"

for f in pjsip.conf extensions.conf ari.conf http.conf; do
  if [ -f "$AST_DIR/$f" ]; then
    cp "$AST_DIR/$f" "$BACKUP_DIR/$f"
    echo "  Backed up $AST_DIR/$f -> $BACKUP_DIR/$f"
  fi
  cp "$SCRIPT_DIR/$f" "$AST_DIR/$f"
  chown asterisk:asterisk "$AST_DIR/$f" 2>/dev/null || true
  chmod 640 "$AST_DIR/$f" 2>/dev/null || true
  echo "  ✅ Installed $AST_DIR/$f"
done

echo "🔄 Reloading Asterisk..."
asterisk -rx "core reload" || true
asterisk -rx "pjsip reload" || true
asterisk -rx "dialplan reload" || true
asterisk -rx "module reload res_ari.so" || true

echo ""
echo "🎉 Asterisk configuration installed and reloaded successfully!"
echo "---------------------------------------------------------------"
echo "Softphone registration settings (e.g. MicroSIP on Windows):"
echo "  Account Name:   100"
echo "  SIP Server:     <server-ip>:5060"
echo "  Username:       100"
echo "  Domain:         <server-ip>:5060"
echo "  Password:       1234"
echo "---------------------------------------------------------------"
