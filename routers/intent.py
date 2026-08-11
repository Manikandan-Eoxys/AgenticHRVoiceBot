"""
intent.py

Defines all supported HR intents.

Every user request should be mapped to one of these
before selecting the corresponding specialized agent.
"""

from enum import Enum


class Intent(str, Enum):

    EMPLOYEE = "employee"

    LEAVE = "leave"

    POLICY = "policy"

    CALENDAR = "calendar"

    GRIEVANCE = "grievance"

    UNKNOWN = "unknown"