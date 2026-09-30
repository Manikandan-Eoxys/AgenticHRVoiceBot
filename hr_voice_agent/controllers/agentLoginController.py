"""
agentLoginController.py

Caller-facing identity verification for the HR Voice Agent.

Responsibilities:
  - Collecting 4-digit employee ID via DTMF keypad, keyboard DataChannel, or spoken voice
  - Coordinating the DtmfCollector wait/timeout lifecycle
  - Interfacing with the security auth layer (confirm_employee_identity)
  - Providing a clean API so agent.py / Assistant class can delegate all
    identity logic here without mixing it into conversation management

This is NOT the builder team's admin login — that lives in
agent_builder/backend/controllers/agentBuilderLoginController.py
"""

import asyncio
import logging
import re
import time
from typing import Optional

logger = logging.getLogger("controllers.agent_login")


# ---------------------------------------------------------------------------
# Word → Digit Map  (extended for accented / Tamil-English pronunciations)
# ---------------------------------------------------------------------------
WORD_DIGIT_MAP: dict[str, str] = {
    "zero": "0", "oh": "0", "o": "0",
    "one": "1", "won": "1",
    "two": "2", "to": "2", "too": "2",
    "three": "3",
    "four": "4", "for": "4",
    "five": "5",
    "six": "6",
    "seven": "7",
    "eight": "8", "ate": "8",
    "nine": "9",
}


class IdentitySession:
    """
    Tracks whether the current call session has been verified.

    Attach one of these to each AgentSession at call-start so that
    any downstream tool can gate-check via `is_verified`.
    """

    def __init__(self) -> None:
        self._verified: bool = False
        self._employee_id: Optional[str] = None
        self._employee_name: Optional[str] = None
        self._locked_at: float = 0.0

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    @property
    def is_verified(self) -> bool:
        return self._verified

    @property
    def employee_id(self) -> Optional[str]:
        return self._employee_id

    @property
    def employee_name(self) -> Optional[str]:
        return self._employee_name

    def lock(self, employee_id: str, employee_name: str) -> None:
        """Called by confirm_employee_identity once the caller confirms their name."""
        self._verified = True
        self._employee_id = str(employee_id)
        self._employee_name = employee_name
        self._locked_at = time.monotonic()
        logger.info(
            f"🔐 [IdentitySession] Session locked: emp_id={employee_id}, name={employee_name}"
        )

    def unlock(self) -> None:
        """Resets identity (e.g. caller denied the readback — retry allowed)."""
        self._verified = False
        self._employee_id = None
        self._employee_name = None
        logger.info("🔓 [IdentitySession] Session identity cleared for retry.")

    def __repr__(self) -> str:
        return (
            f"<IdentitySession verified={self._verified} "
            f"emp_id={self._employee_id} name={self._employee_name!r}>"
        )


# ---------------------------------------------------------------------------
# DtmfCollector — central hub for all three input paths
# ---------------------------------------------------------------------------

class DtmfCollector:
    """
    Collects a 4-digit employee ID from three parallel input sources:
      1. SIP RFC-2833 / in-band Goertzel DTMF audio tones
      2. LiveKit DataChannel keyboard input (web console / chat)
      3. Spoken voice transcription (extract_digits_from_speech)

    Usage:
        collector = DtmfCollector(player=dtmf_player, session=agent_session)

        # Feed digits from SIP DTMF event:
        await collector.on_dtmf(digit)

        # Feed spoken transcription text:
        collector.on_spoken_text(transcript)

        # Wait for a complete 4-digit ID (returns "" on timeout):
        emp_id = await collector.wait_for_id(timeout=25.0)
    """

    def __init__(self, player=None, session=None) -> None:
        self.player = player
        self.session = session
        self._buffer: list[str] = []
        self._event: asyncio.Event = asyncio.Event()
        self._collecting: bool = False
        self.last_captured_id: Optional[str] = None
        self._last_id_time: float = 0.0

    # ------------------------------------------------------------------
    # State
    # ------------------------------------------------------------------

    def is_collecting(self) -> bool:
        return self._collecting

    # ------------------------------------------------------------------
    # Input: DTMF digit (SIP or in-band audio)
    # ------------------------------------------------------------------

    async def on_dtmf(self, digit: str) -> None:
        d = str(digit).strip().upper()
        if not d:
            return

        # Play authentic telephone dual-tone feedback immediately
        if self.player:
            self.player.play_tone(d)

        if d == "*":
            self._buffer.clear()
            logger.info("🔢 [Keypad] Star (*) pressed — buffer cleared.")
            return

        if d == "#":
            curr = "".join(self._buffer)
            logger.info(f"🔢 [Keypad] Pound (#) pressed. Buffer: '{curr}'")
            if len(self._buffer) >= 4:
                cand = "".join(self._buffer[:4])
                self._capture(cand)
            return

        if d in "0123456789":
            self._buffer.append(d)
            curr = "".join(self._buffer)
            logger.info(f"🔢 [Keypad] Received '{d}'. Buffer: {curr}")
            if len(self._buffer) >= 4:
                cand = "".join(self._buffer[:4])
                self._capture(cand)

                # Caller typed full 4-digit ID *before* the keypad tool was invoked
                # (e.g. during initial greeting) — trigger verification proactively
                if (
                    not self._collecting
                    and self.session
                    and not getattr(self.session, "verified_employee_id", None)
                ):
                    self._buffer.clear()
                    logger.info(
                        f"🔢 [Keypad] Full ID '{cand}' entered during greeting — "
                        "triggering auto-verification."
                    )
                    asyncio.create_task(
                        self.session.generate_reply(
                            user_input=(
                                f"The caller entered employee ID {cand} on their phone keypad."
                            )
                        )
                    )

    # ------------------------------------------------------------------
    # Input: Spoken voice transcription text
    # ------------------------------------------------------------------

    def on_spoken_text(self, text: str) -> None:
        if not text:
            return

        # Fast path: bare 4-digit number in the transcript
        m = re.search(r"\b(10\d{2}|1\d{3})\b", text)
        if m:
            extracted = m.group(1)
            logger.info(f"🗣️ [Voice] Captured 4-digit ID from speech: '{extracted}'")
            self._buffer = list(extracted)
            self._capture(extracted)
            return

        # Slow path: words like "one zero zero seven"
        digits = self._extract_spoken_digits(text)
        if len(digits) >= 4:
            cand = "".join(digits[:4])
            if cand.startswith("1"):
                logger.info(f"🗣️ [Voice] Captured spelled-out digits: '{cand}'")
                self._buffer = list(cand)
                self._capture(cand)

    # ------------------------------------------------------------------
    # Wait for a complete 4-digit ID
    # ------------------------------------------------------------------

    async def wait_for_id(self, timeout: float = 25.0) -> str:
        self._collecting = True
        self._event.clear()

        # Already have 4 digits buffered
        if len(self._buffer) >= 4:
            res = "".join(self._buffer[:4])
            self._buffer.clear()
            self._collecting = False
            return res

        # Recent capture within the last 15 s
        if self.last_captured_id and (time.monotonic() - self._last_id_time) < 15.0:
            res = self.last_captured_id
            self.last_captured_id = None
            self._collecting = False
            return res

        try:
            await asyncio.wait_for(self._event.wait(), timeout=timeout)
            if len(self._buffer) >= 4:
                res = "".join(self._buffer[:4])
                self._buffer.clear()
                return res
            if self.last_captured_id:
                res = self.last_captured_id
                self.last_captured_id = None
                return res
        except asyncio.TimeoutError:
            pass
        finally:
            self._collecting = False

        if len(self._buffer) >= 4:
            res = "".join(self._buffer[:4])
            self._buffer.clear()
            return res

        return ""

    # ------------------------------------------------------------------
    # Private helpers
    # ------------------------------------------------------------------

    def _capture(self, candidate: str) -> None:
        self.last_captured_id = candidate
        self._last_id_time = time.monotonic()
        self._event.set()

    @staticmethod
    def _extract_spoken_digits(text: str) -> list[str]:
        words = text.lower().replace("-", " ").split()
        digits: list[str] = []
        for w in words:
            if w in WORD_DIGIT_MAP:
                digits.append(WORD_DIGIT_MAP[w])
            elif w.isdigit():
                digits.extend(list(w))
        return digits
