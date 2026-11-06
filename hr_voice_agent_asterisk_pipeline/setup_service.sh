#!/usr/bin/env bash
# ==============================================================================
# setup_service.sh — 24/7 Systemd Background Service Management for HR Voicebot
# ==============================================================================
# This script sets up, enables, and manages the Agentic HR Voice Bot as a
# 24/7 systemd service on Linux / Ubuntu.
#
# Usage:
#   sudo ./setup_service.sh [install|start|stop|restart|status|logs|uninstall]
# ==============================================================================

set -e

# Resolve repository directory and user
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$SCRIPT_DIR"

ACTUAL_USER="${SUDO_USER:-$(id -un)}"
SERVICE_NAME="hr-voicebot.service"
SYSTEMD_DEST="/etc/systemd/system/${SERVICE_NAME}"
VENV_PYTHON="${SCRIPT_DIR}/.venv/bin/python"

# Color helpers
GREEN='\033[0;32m'
BLUE='\033[0;34m'
YELLOW='\033[1;33m'
RED='\033[0;31m'
NC='\033[0m' # No Color

require_root() {
    if [ "$EUID" -ne 0 ]; then
        echo -e "${YELLOW}Notice:${NC} This operation requires superuser privileges."
        echo -e "Re-running with sudo..."
        exec sudo "$0" "$@"
    fi
}

install_service() {
    require_root "$@"

    echo -e "${BLUE}====================================================${NC}"
    echo -e "${GREEN}Configuring 24/7 Systemd Service for HR Voicebot${NC}"
    echo -e "${BLUE}====================================================${NC}"
    echo -e "User:              ${GREEN}${ACTUAL_USER}${NC}"
    echo -e "Working Directory: ${GREEN}${SCRIPT_DIR}${NC}"
    echo -e "Python Executable: ${GREEN}${VENV_PYTHON}${NC}"
    echo -e "Service Path:      ${GREEN}${SYSTEMD_DEST}${NC}"
    echo ""

    # Check for python virtualenv
    if [ ! -f "$VENV_PYTHON" ]; then
        echo -e "${YELLOW}Warning: Virtual environment python not found at:${NC}"
        echo -e "  $VENV_PYTHON"
        echo -e "Please create it before starting the service:"
        echo -e "  python3 -m venv .venv"
        echo -e "  source .venv/bin/activate"
        echo -e "  pip install -r requirements.txt"
        echo ""
    fi

    # Check for .env file
    if [ ! -f "${SCRIPT_DIR}/.env" ]; then
        if [ -f "${SCRIPT_DIR}/.env.example" ]; then
            echo -e "${YELLOW}Notice: .env file not found. Copying .env.example -> .env${NC}"
            cp "${SCRIPT_DIR}/.env.example" "${SCRIPT_DIR}/.env"
            chown "${ACTUAL_USER}:${ACTUAL_USER}" "${SCRIPT_DIR}/.env" 2>/dev/null || true
            echo -e "${YELLOW}Please edit .env to configure your credentials.${NC}"
        else
            echo -e "${RED}Warning: Neither .env nor .env.example was found.${NC}"
        fi
        echo ""
    fi

    # Generate systemd unit file
    echo -e "Creating ${SYSTEMD_DEST}..."
    cat <<EOF > "$SYSTEMD_DEST"
[Unit]
Description=Agentic HR Voice Bot Service (24/7)
After=network.target mysql.service

[Service]
Type=simple
User=${ACTUAL_USER}
WorkingDirectory=${SCRIPT_DIR}
ExecStart=${VENV_PYTHON} main.py start
Restart=always
RestartSec=5s
Environment=PYTHONUNBUFFERED=1
StandardOutput=journal
StandardError=journal

[Install]
WantedBy=multi-user.target
EOF

    chmod 644 "$SYSTEMD_DEST"

    # Reload systemd daemon
    echo -e "Reloading systemd daemon (systemctl daemon-reload)..."
    systemctl daemon-reload

    # Enable service on boot
    echo -e "Enabling ${SERVICE_NAME} to auto-start on boot..."
    systemctl enable "${SERVICE_NAME}"

    echo ""
    echo -e "${GREEN}✅ Systemd service installed and enabled successfully!${NC}"
    echo ""
    echo -e "To manage the service, use:"
    echo -e "  sudo ./setup_service.sh start    # or: sudo systemctl start ${SERVICE_NAME}"
    echo -e "  sudo ./setup_service.sh stop     # or: sudo systemctl stop ${SERVICE_NAME}"
    echo -e "  sudo ./setup_service.sh restart  # or: sudo systemctl restart ${SERVICE_NAME}"
    echo -e "  ./setup_service.sh status        # or: systemctl status ${SERVICE_NAME}"
    echo -e "  ./setup_service.sh logs          # or: journalctl -u ${SERVICE_NAME} -f"
    echo -e "${BLUE}====================================================${NC}"
}

start_service() {
    require_root "$@"
    echo -e "Starting ${SERVICE_NAME}..."
    systemctl start "${SERVICE_NAME}"
    echo -e "${GREEN}Service started.${NC}"
    systemctl status "${SERVICE_NAME}" --no-pager
}

stop_service() {
    require_root "$@"
    echo -e "Stopping ${SERVICE_NAME}..."
    systemctl stop "${SERVICE_NAME}"
    echo -e "${YELLOW}Service stopped.${NC}"
}

restart_service() {
    require_root "$@"
    echo -e "Restarting ${SERVICE_NAME}..."
    systemctl restart "${SERVICE_NAME}"
    echo -e "${GREEN}Service restarted.${NC}"
    systemctl status "${SERVICE_NAME}" --no-pager
}

status_service() {
    systemctl status "${SERVICE_NAME}" --no-pager
}

logs_service() {
    echo -e "Streaming logs from ${SERVICE_NAME} (Ctrl+C to exit)..."
    journalctl -u "${SERVICE_NAME}" -f -o cat
}

uninstall_service() {
    require_root "$@"
    echo -e "Stopping and disabling ${SERVICE_NAME}..."
    systemctl stop "${SERVICE_NAME}" 2>/dev/null || true
    systemctl disable "${SERVICE_NAME}" 2>/dev/null || true
    if [ -f "$SYSTEMD_DEST" ]; then
        rm -f "$SYSTEMD_DEST"
        echo -e "Removed ${SYSTEMD_DEST}"
    fi
    systemctl daemon-reload
    echo -e "${GREEN}✅ Service completely uninstalled.${NC}"
}

print_help() {
    echo "Usage: ./setup_service.sh [COMMAND]"
    echo ""
    echo "Commands:"
    echo "  install    Install, daemon-reload, and enable systemd service (default)"
    echo "  start      Start the 24/7 background voice bot service"
    echo "  stop       Stop the voice bot service"
    echo "  restart    Restart the voice bot service"
    echo "  status     Check current service status"
    echo "  logs       Live stream service output logs (journalctl -f)"
    echo "  uninstall  Stop, disable, and delete service file"
    echo "  help       Show this help message"
}

# Command dispatch
ACTION="${1:-install}"

case "$ACTION" in
    install)
        install_service "$@"
        ;;
    start)
        start_service "$@"
        ;;
    stop)
        stop_service "$@"
        ;;
    restart)
        restart_service "$@"
        ;;
    status)
        status_service
        ;;
    logs)
        logs_service
        ;;
    uninstall)
        uninstall_service "$@"
        ;;
    help|--help|-h)
        print_help
        ;;
    *)
        echo -e "${RED}Unknown command: ${ACTION}${NC}"
        print_help
        exit 1
        ;;
esac
