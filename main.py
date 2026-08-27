"""
main.py

Entry point for HR Voice Agent
LiveKit Agents v1.6.10
"""

import logging
from livekit import agents
from agent import server
from services.monitoring_handler import install_monitoring_handler

# Attach monitoring handler so all logs stream to frontend UI
try:
    install_monitoring_handler()
except Exception as e:
    logging.warning(f"Could not initialize monitoring handler: {e}")

if __name__ == "__main__":
    agents.cli.run_app(server)
