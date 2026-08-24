"""
auth_service.py

Simplified authentication: employee ID only — no OTP required.
"""

from tools.employee_tools import EmployeeTools


class AuthService:

    def __init__(self):
        self.employee_tools = EmployeeTools()

    def begin_verification(self, employee_id):
        """Look up the employee by ID and return their info. No OTP needed."""
        employee = self.employee_tools.get_employee(employee_id)

        if employee is None:
            return {
                "success": False,
                "message": "Employee not found. Please check your Employee ID."
            }

        return {
            "success": True,
            "employee": employee,
        }