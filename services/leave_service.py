"""
leave_service.py

Business logic for Leave Management.

Flow:

Agent
    ↓
LeaveService
    ↓
LeaveTools
    ↓
SQLite
"""

from datetime import datetime

from tools.leave_tools import LeaveTools
from tools.employee_tools import EmployeeTools
from tools.policy_tools import PolicyTools


class LeaveService:

    def __init__(self):

        self.leave_tool = LeaveTools()
        self.employee_tool = EmployeeTools()
        self.policy_tool = PolicyTools()

    # -------------------------------------------------------
    # Check Leave Balance
    # -------------------------------------------------------

    def check_leave_balance(self, employee_id: int):

        employee = self.employee_tool.get_employee(employee_id)

        if not employee["success"]:
            return {
                "success": False,
                "message": "Employee not found."
            }

        return self.leave_tool.check_leave_balance(employee_id)

    # -------------------------------------------------------
    # Apply Leave
    # -------------------------------------------------------

    def apply_leave(
        self,
        employee_id: int,
        from_date: str,
        to_date: str,
        leave_type: str
    ):

        # Validate Employee
        employee = self.employee_tool.get_employee(employee_id)

        if not employee["success"]:
            return {
                "success": False,
                "message": "Employee not found."
            }

        # Validate Date Format
        try:

            start = datetime.strptime(from_date, "%Y-%m-%d")
            end = datetime.strptime(to_date, "%Y-%m-%d")

        except ValueError:

            return {

                "success": False,

                "message": "Date format should be YYYY-MM-DD."

            }

        # Validate Date Range
        if start > end:

            return {

                "success": False,

                "message": "From date cannot be after To date."

            }

        total_days = (end - start).days + 1

        # --------------------------------------------------
        # Step 1: Check Leave Policy
        # --------------------------------------------------
        policy_res = self.policy_tool.get_policy(f"{leave_type} Policy")
        if not policy_res["success"]:
            policy_res = self.policy_tool.get_policy("Leave Policy")

        policy_info = policy_res.get("policy", {}).get("description", "")

        # --------------------------------------------------
        # Step 2: Check Leave Balance
        # --------------------------------------------------
        balance = self.leave_tool.check_leave_balance(employee_id)

        if not balance["success"]:
            return balance

        available = balance.get(leave_type.lower(), 0)

        if available < total_days:
            return {
                "success": False,
                "policy_summary": policy_info,
                "message": f"Insufficient {leave_type} leave balance. Available: {available} days, Requested: {total_days} days."
            }

        # --------------------------------------------------
        # Step 3: Submit Leave Request & Deduct Balance
        # --------------------------------------------------
        result = self.leave_tool.apply_leave(
            employee_id,
            from_date,
            to_date,
            leave_type
        )

        if result.get("success"):
            result["policy_summary"] = policy_info
            result["remaining_balance"] = available - total_days

        return result

    # -------------------------------------------------------
    # Cancel Leave
    # -------------------------------------------------------

    def cancel_leave(self, request_id: int):

        return self.leave_tool.cancel_leave(request_id)

    # -------------------------------------------------------
    # Approve Leave
    # -------------------------------------------------------

    def approve_leave(self, request_id: int):

        return self.leave_tool.approve_leave(request_id)

    # -------------------------------------------------------
    # Reject Leave
    # -------------------------------------------------------

    def reject_leave(self, request_id: int):

        return self.leave_tool.reject_leave(request_id)

    # -------------------------------------------------------
    # Leave History
    # -------------------------------------------------------

    def leave_history(self, employee_id: int):

        employee = self.employee_tool.get_employee(employee_id)

        if not employee["success"]:

            return {

                "success": False,

                "message": "Employee not found."

            }

        return self.leave_tool.list_leave_requests(employee_id)

    # -------------------------------------------------------
    # Check Existing Leave
    # -------------------------------------------------------

    def has_leave_on_date(

        self,

        employee_id: int,

        date: str

    ):

        requests = self.leave_tool.list_leave_requests(employee_id)

        if not requests["success"]:

            return {

                "success": False,

                "message": "Unable to fetch leave history."

            }

        for leave in requests["leave_requests"]:

            if leave["status"] != "Approved":
                continue

            if leave["from_date"] <= date <= leave["to_date"]:

                return {

                    "success": True,

                    "on_leave": True,

                    "leave": leave

                }

        return {

            "success": True,

            "on_leave": False

        }