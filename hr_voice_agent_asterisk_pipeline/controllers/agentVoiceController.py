"""
controllers/agentVoiceController.py

Voice Controller for Asterisk Telephony pipeline.
Provides:
  - AsteriskVoiceController
  - VoiceController (alias for AsteriskVoiceController)
  - AgentVoiceController (alias for AsteriskVoiceController)
  - ControllerState
  - prewarm_worker_resources
  - _prime_ollama_model
"""

from __future__ import annotations

from controllers.asteriskVoiceController import (
    AsteriskVoiceController,
    ControllerState,
    prewarm_worker_resources,
    _prime_ollama_model,
)

# Aliases for backwards compatibility
VoiceController = AsteriskVoiceController
AgentVoiceController = AsteriskVoiceController

__all__ = [
    "AsteriskVoiceController",
    "VoiceController",
    "AgentVoiceController",
    "ControllerState",
    "prewarm_worker_resources",
    "_prime_ollama_model",
]
