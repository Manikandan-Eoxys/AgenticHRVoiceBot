"""
goals/helpers.py

Common security and validation helpers for LiveKit function tools & goal actions.
Ensures goal actions gate access strictly to the verified employee on the active call.
"""

import re
from livekit.agents import RunContext


def clean_id(employee_id: str | int) -> str:
    """Normalizes an employee ID to digits only."""
    return re.sub(r"[^0-9]", "", str(employee_id or ""))


def require_verified(context: RunContext, employee_id: str | int = "") -> str | None:
    """Security gate: goal action must only ever act on the employee ID that was
    actually verified for THIS call."""
    if not context or not hasattr(context, "session"):
        return None

    verified = getattr(context.session, "verified_employee_id", None)
    if verified is None:
        return (
            "No employee has been verified yet on this call. Verify the caller's "
            "identity with get_employee_by_id and confirm_employee_identity first."
        )

    if employee_id:
        cleaned = clean_id(employee_id)
        if cleaned and cleaned != verified:
            return (
                f"This call is verified for employee {verified} only. I cannot access or submit "
                f"records for employee {employee_id}."
            )

    return None


def get_verified_id(context: RunContext, employee_id: str | int = "") -> str:
    """Returns the cleaned verified employee ID from context or parameter."""
    if context and hasattr(context, "session"):
        verified = getattr(context.session, "verified_employee_id", None)
        if verified:
            return str(verified)
    return clean_id(employee_id)
