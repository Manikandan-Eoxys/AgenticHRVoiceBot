"""
hr_voice_agent/telephony/dtmf.py

DTMF Digit Buffering and Telephony Keypad Collection
===================================================
Buffers keypad digits received via ARI ChannelDtmfReceived events or in-band detection.
Features:
  - Inter-digit timeout (emits buffered digits if user pauses)
  - Target length detection (e.g. 4 digits for employee ID)
  - Hash '#' delimiter completion
  - Async awaitable collection for tool actions (e.g. collect_employee_id_via_keypad)
"""

from __future__ import annotations

import asyncio
import logging
import time
from typing import Callable, Optional

logger = logging.getLogger("telephony.dtmf")


class DtmfBuffer:
    """
    Asynchronous DTMF digit accumulator with inter-digit timeout and length triggers.
    """

    def __init__(
        self,
        expected_length: int = 4,
        inter_digit_timeout: float = 2.5,
        delimiter: str = "#",
        on_id_complete: Optional[Callable[[str], None]] = None,
    ) -> None:
        self.expected_length = expected_length
        self.inter_digit_timeout = inter_digit_timeout
        self.delimiter = delimiter
        self.on_id_complete = on_id_complete

        self._buffer: list[str] = []
        self._last_digit_time = 0.0
        self._timer_task: Optional[asyncio.Task] = None
        self._completion_event = asyncio.Event()
        self._collected_id: Optional[str] = None
        self._lock = asyncio.Lock()

    @property
    def current_digits(self) -> str:
        return "".join(self._buffer)

    def on_digit(self, digit: str) -> None:
        """
        Receives a single DTMF digit from ARI or in-band detector.
        """
        clean_digit = str(digit).strip()
        if not clean_digit:
            return

        logger.info(f"🔢 [DTMF] Digit received: '{clean_digit}'")
        self._buffer.append(clean_digit)
        self._last_digit_time = time.monotonic()

        # Check delimiter '#'
        if clean_digit == self.delimiter:
            digits = "".join(d for d in self._buffer if d != self.delimiter)
            self._finalize_id(digits)
            return

        # Check expected length (e.g. 4 digits)
        digits_only = "".join(d for d in self._buffer if d.isdigit())
        if len(digits_only) >= self.expected_length:
            self._finalize_id(digits_only[:self.expected_length])
            return

        # Start or reset inter-digit timeout timer if running in an event loop
        try:
            loop = asyncio.get_running_loop()
            if self._timer_task and not self._timer_task.done():
                self._timer_task.cancel()
            self._timer_task = loop.create_task(self._inter_digit_timer())
        except RuntimeError:
            pass

    async def _inter_digit_timer(self) -> None:
        """Fires if no new digit arrives within inter_digit_timeout."""
        try:
            await asyncio.sleep(self.inter_digit_timeout)
            digits = "".join(d for d in self._buffer if d.isdigit())
            if digits:
                logger.info(f"⏱️ [DTMF] Inter-digit timeout reached with digits: {digits}")
                self._finalize_id(digits)
        except asyncio.CancelledError:
            pass

    def _finalize_id(self, emp_id: str) -> None:
        """Emits completed digits and triggers completion event."""
        if self._timer_task and not self._timer_task.done():
            self._timer_task.cancel()

        self._collected_id = emp_id
        self._buffer.clear()
        self._completion_event.set()

        if self.on_id_complete and emp_id:
            try:
                res = self.on_id_complete(emp_id)
                if asyncio.iscoroutine(res):
                    try:
                        loop = asyncio.get_running_loop()
                        loop.create_task(res)
                    except RuntimeError:
                        pass
            except Exception as e:
                logger.error(f"Error in on_id_complete DTMF callback: {e}")

    async def wait_for_id(self, timeout: float = 25.0) -> Optional[str]:
        """
        Awaits collection of employee ID digits from keypad.
        Used by IdentityVerificationService.collect_employee_id_via_keypad.
        """
        self._completion_event.clear()
        self._collected_id = None
        self._buffer.clear()

        try:
            await asyncio.wait_for(self._completion_event.wait(), timeout=timeout)
            return self._collected_id
        except asyncio.TimeoutError:
            logger.warning(f"⏱️ DTMF collection timed out after {timeout}s.")
            return None

    def clear(self) -> None:
        """Clears buffer and cancels pending timers."""
        if self._timer_task and not self._timer_task.done():
            self._timer_task.cancel()
        self._buffer.clear()
        self._completion_event.clear()
        self._collected_id = None
