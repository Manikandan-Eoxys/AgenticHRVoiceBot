"""
auth_session.py

Holds the current employee authentication state for one call session.
OTP has been removed — authentication is by Employee ID only.
"""

from dataclasses import dataclass
from typing import Optional


@dataclass
class AuthSession:

    authenticated: bool = False

    employee_id: Optional[int] = None

    employee_name: Optional[str] = None

    verification_pending: bool = False

    def login(self, employee_id, employee_name):
        self.employee_id = employee_id
        self.employee_name = employee_name
        self.authenticated = True
        self.verification_pending = False

    def logout(self):
        self.employee_id = None
        self.employee_name = None
        self.authenticated = False
        self.verification_pending = False