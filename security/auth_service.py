"""
auth_service.py
"""

from security.otp_manager import OTPManager

from tools.employee_tools import EmployeeTools


class AuthService:

    def __init__(self):

        self.employee_tools = EmployeeTools()

        self.otp = OTPManager()

    def begin_verification(self, employee_id):

        employee = self.employee_tools.get_employee(employee_id)

        if employee is None:

            return {

                "success": False,

                "message": "Employee not found."

            }

        otp = self.otp.generate(employee_id)

        return {

            "success": True,

            "employee": employee,

            "otp": otp
        }

    def verify_otp(self, employee_id, otp):

        ok = self.otp.verify(employee_id, otp)

        return {

            "success": ok
        }