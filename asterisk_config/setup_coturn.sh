#!/bin/bash
# ============================================================================
# asterisk_config/setup_coturn.sh
# Installs and configures coturn STUN/TURN server on Ubuntu / Debian
# ============================================================================

set -e

if [ "$EUID" -ne 0 ]; then
  echo "⚠️ Please run as root or with sudo:"
  echo "   sudo bash $0"
  exit 1
fi

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

echo "📦 Installing coturn package..."
apt-get update -qq && apt-get install -y coturn

echo "⚙️ Configuring coturn..."
# Enable automatic startup
if [ -f /etc/default/coturn ]; then
  sed -i 's/#TURNSERVER_ENABLED=1/TURNSERVER_ENABLED=1/' /etc/default/coturn
  sed -i 's/TURNSERVER_ENABLED=0/TURNSERVER_ENABLED=1/' /etc/default/coturn
fi

# Copy configuration
cp "$SCRIPT_DIR/turnserver.conf" /etc/turnserver.conf
chmod 644 /etc/turnserver.conf

echo "🔄 Restarting coturn service..."
systemctl restart coturn || systemctl restart turnserver
systemctl enable coturn 2>/dev/null || systemctl enable turnserver 2>/dev/null || true

echo "✅ coturn STUN/TURN server is active and running on port 3478 / 5349!"

