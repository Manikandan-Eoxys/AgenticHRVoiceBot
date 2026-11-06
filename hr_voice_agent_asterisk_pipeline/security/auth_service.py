"""
security/auth_service.py

Simplified authentication: employee ID only — no OTP required.
Delegates to EmployeeService in the services layer.
"""

from services.employee_service import EmployeeService


class AuthService:

    def __init__(self):
        self.employee_service = EmployeeService()
        self.employee_tools = self.employee_service  # Backward compatibility alias

    def begin_verification(self, employee_id):
        """Look up the employee by ID and return their info. No OTP needed."""
        employee = self.employee_service.get_employee(employee_id)

        if not employee or not employee.get("success"):
            return {
                "success": False,
                "message": "Employee not found. Please check your Employee ID."
            }

        return {
            "success": True,
            "employee": employee,
        }