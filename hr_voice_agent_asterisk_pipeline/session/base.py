"""
hr_voice_agent/telephony/base.py

Base Telephony Session Abstraction
==================================
Defines the transport-agnostic interface for telephony sessions.
This decouples the business logic, conversation loop, FSMs, STT, and TTS
from Asterisk ARI, LiveKit, or any other underlying transport.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from typing import AsyncIterator, Callable, List, Optional


class BaseTelephonySession(ABC):
    """
    Abstract base class for active phone call sessions.
    Exposes unified audio I/O, speech interruption, DTMF handling,
    call transfer, and hangup primitives.
    """

    def __init__(self, call_id: str, caller_number: str) -> None:
        self.call_id = call_id
        self.caller_number = caller_number
        self._dtmf_callbacks: List[Callable[[str], None]] = []
        self._is_active = True

    @property
    def is_active(self) -> bool:
        """Returns True if the telephony session is active and connected."""
        return self._is_active

    @abstractmethod
    def audio_frames(self) -> AsyncIterator[bytes]:
        """
        Asynchronous generator yielding incoming mono 16-bit linear PCM audio frames.
        Frames are typically 20ms duration (e.g., 320 bytes @ 8kHz).
        """
        raise NotImplementedError

    @abstractmethod
    async def send_audio(self, pcm: bytes) -> None:
        """
        Sends outbound mono 16-bit linear PCM audio frames to the caller.
        Audio is queued and paced at real-time rate.
        """
        raise NotImplementedError

    @abstractmethod
    def interrupt_speech(self) -> None:
        """
        Instantly halts outbound speech playback and purges queued audio frames.
        Called when barge-in is detected.
        """
        raise NotImplementedError

    @abstractmethod
    async def wait_for_playout(self, timeout: Optional[float] = None) -> bool:
        """
        Waits until all currently enqueued outbound audio frames have been transmitted.
        """
        raise NotImplementedError

    @abstractmethod
    async def send_dtmf(self, digits: str) -> bool:
        """
        Sends DTMF tones down the channel.
        """
        raise NotImplementedError

    @abstractmethod
    async def transfer_call(self, target: str) -> bool:
        """
        Transfers the caller to a designated target (e.g., coordinator phone number / SIP endpoint).
        Returns True if the transfer was initiated successfully.
        """
        raise NotImplementedError

    @abstractmethod
    async def hangup(self) -> None:
        """
        Terminates the call and cleans up all associated resources idempotently.
        """
        raise NotImplementedError

    def on_dtmf(self, callback: Callable[[str], None]) -> None:
        """
        Registers a callback function to be invoked when a DTMF digit is received.
        """
        if callback not in self._dtmf_callbacks:
            self._dtmf_callbacks.append(callback)

    def remove_dtmf_callback(self, callback: Callable[[str], None]) -> None:
        """Removes a previously registered DTMF callback."""
        if callback in self._dtmf_callbacks:
            self._dtmf_callbacks.remove(callback)

    def dispatch_dtmf(self, digit: str) -> None:
        """
        Invokes all registered DTMF callbacks with the received digit.
        """
        for cb in list(self._dtmf_callbacks):
            try:
                cb(digit)
            except Exception:
                pass
