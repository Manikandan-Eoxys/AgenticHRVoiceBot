"""
main.py

Entry point for HR Voice Agent
LiveKit Agents v1.6.10
"""

from livekit import agents
from agent import server

if __name__ == "__main__":
    agents.cli.run_app(server)
