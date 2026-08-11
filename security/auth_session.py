"""
auth_session.py
"""

from dataclasses import dataclass
from typing import Optional


@dataclass
class AuthSession:

    authenticated: bool = False

    employee_id: Optional[int] = None

    employee_name: Optional[str] = None

    otp_verified: bool = False

    verification_pending: bool = False

    otp_sent: bool = False

    def login(self, employee_id, employee_name):

        self.employee_id = employee_id

        self.employee_name = employee_name

        self.authenticated = True

        self.otp_verified = True

        self.verification_pending = False

    def logout(self):

        self.employee_id = None

        self.employee_name = None

        self.authenticated = False

        self.otp_verified = False

        self.verification_pending = False

        self.otp_sent = False