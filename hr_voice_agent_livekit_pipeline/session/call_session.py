"""
session/call_session.py

Per-call runtime state container for the HR Voice Agent.

Rule (from architecture design):
  - Runtime variables that only exist DURING a live call → CallSession (this file)
  - Config data written by the builder → DB table / AgentConfigController
  - State that must SURVIVE across calls (e.g. pending leave requests) → DB table

One CallSession is created fresh at the start of every call and discarded
when the call ends.  It replaces the module-level dicts (`_session_verified`,
`_session_name`) that previously lived in hr_tools.py.

Usage:
    session = CallSession(room_name=ctx.room.name)
    session.lock_identity("1007", "Guna")
    session.verify_access("1007")   # raises if not locked to this ID
"""

import logging
import time
from dataclasses import dataclass, field
from typing import Optional

logger = logging.getLogger("session.call_session")


class IdentityNotVerifiedError(Exception):
    """Raised when a tool is called before the caller has verified their identity."""


class IdentityMismatchError(Exception):
    """Raised when a tool is called for an employee ID that is not the verified one."""


@dataclass
class CallSession:
    """
    Holds all transient state for one live phone call.

    Attach this to the AgentSession so every tool can read it via
    context.session.call_session.

    Fields
    ------
    room_name           : LiveKit room identifier for this call
    verified_employee_id: Employee ID confirmed and locked in identity gate
    verified_employee_name: Caller's full name (from DB at lock time)
    call_type           : 'inbound' | 'leave_verification' | 'scheduled_reminder'
    started_at          : Epoch seconds when call started
    coordinator_connected: True once transfer_to_coordinator succeeds
    recording_egress_id : Egress ID if a LiveKit cloud recording is running
    dtmf_buffer         : Digits collected via keypad so far (cleared after use)
    last_intent         : Most recent goal that was active (for logging/audit)
    context_vars        : Free-form dict for goal services to stash turn-level state
    """

    room_name: str = ""
    call_type: str = "inbound"
    started_at: float = field(default_factory=time.time)

    # Identity gate
    verified_employee_id: Optional[str] = None
    verified_employee_name: Optional[str] = None
    _identity_locked_at: float = 0.0

    # Transfer / recording state
    coordinator_connected: bool = False
    recording_egress_id: Optional[str] = None

    # DTMF / keypad
    dtmf_buffer: list = field(default_factory=list)

    # Goal tracking
    last_intent: Optional[str] = None

    # Free-form per-turn scratchpad (goal services may stash context here)
    context_vars: dict = field(default_factory=dict)

    # ------------------------------------------------------------------ #
    # Identity API
    # ------------------------------------------------------------------ #

    @property
    def is_verified(self) -> bool:
        return self.verified_employee_id is not None

    def lock_identity(self, employee_id: str, employee_name: str = "") -> None:
        """
        Locks this call session to a verified employee.
        Must be called by IdentityVerificationService after caller confirms.
        """
        self.verified_employee_id = str(employee_id).strip()
        self.verified_employee_name = employee_name
        self._identity_locked_at = time.time()
        logger.info(
            f"🔐 [CallSession] Identity locked: emp_id={employee_id} "
            f"name='{employee_name}' room='{self.room_name}'"
        )

    def unlock_identity(self) -> None:
        """Clears identity lock (caller denied readback — allow retry)."""
        prev = self.verified_employee_id
        self.verified_employee_id = None
        self.verified_employee_name = None
        self._identity_locked_at = 0.0
        logger.info(f"🔓 [CallSession] Identity unlocked (was {prev}).")

    def require_verified(self, employee_id: str) -> None:
        """
        Security gate called by every data-access tool.

        Raises:
            IdentityNotVerifiedError  — no identity locked yet on this call
            IdentityMismatchError     — request is for a different employee
        """
        if self.verified_employee_id is None:
            raise IdentityNotVerifiedError(
                "No employee has been verified on this call. "
                "Ask the caller for their 4-digit employee ID and call "
                "get_employee_by_id → confirm_employee_identity first."
            )
        requested = str(employee_id).strip().lstrip("0") or employee_id
        verified = self.verified_employee_id.lstrip("0") or self.verified_employee_id
        if requested != verified:
            raise IdentityMismatchError(
                f"This call is verified for employee {self.verified_employee_id} only. "
                f"Cannot access data for employee {employee_id}."
            )

    # ------------------------------------------------------------------ #
    # Goal tracking
    # ------------------------------------------------------------------ #

    def set_intent(self, goal_id: str) -> None:
        self.last_intent = goal_id
        logger.debug(f"🎯 [CallSession] Active goal → {goal_id}")

    # ------------------------------------------------------------------ #
    # Context vars helpers
    # ------------------------------------------------------------------ #

    def set(self, key: str, value) -> None:
        self.context_vars[key] = value

    def get(self, key: str, default=None):
        return self.context_vars.get(key, default)

    # ------------------------------------------------------------------ #
    # Lifecycle
    # ------------------------------------------------------------------ #

    @property
    def duration_seconds(self) -> float:
        return time.time() - self.started_at

    def summary(self) -> dict:
        return {
            "room": self.room_name,
            "call_type": self.call_type,
            "verified_emp_id": self.verified_employee_id,
            "verified_emp_name": self.verified_employee_name,
            "coordinator_connected": self.coordinator_connected,
            "last_intent": self.last_intent,
            "duration_s": round(self.duration_seconds, 1),
        }

    def __repr__(self) -> str:
        return (
            f"<CallSession room={self.room_name!r} "
            f"emp={self.verified_employee_id} "
            f"verified={self.is_verified}>"
        )
