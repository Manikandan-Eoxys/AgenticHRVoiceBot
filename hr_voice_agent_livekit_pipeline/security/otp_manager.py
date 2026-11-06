"""
otp_manager.py
"""

import random
import time


class OTPManager:

    OTP_EXPIRY = 300

    def __init__(self):

        self.active_otps = {}

    def generate(self, employee_id):

        otp = random.randint(100000, 999999)

        self.active_otps[employee_id] = {

            "otp": str(otp),

            "timestamp": time.time(),

        }

        return str(otp)

    def verify(self, employee_id, otp):

        if employee_id not in self.active_otps:

            return False

        data = self.active_otps[employee_id]

        if time.time() - data["timestamp"] > self.OTP_EXPIRY:

            del self.active_otps[employee_id]

            return False

        if data["otp"] == otp:

            del self.active_otps[employee_id]

            return True

        return False