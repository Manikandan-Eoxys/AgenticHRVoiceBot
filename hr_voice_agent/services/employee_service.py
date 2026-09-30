"""
employee_service.py

Business logic for Employee Management.

Flow:

Agent
    ↓
EmployeeService
    ↓
EmployeeTools
    ↓
SQLite
"""

from tools.employee_tools import EmployeeTools


class EmployeeService:

    def __init__(self):

        self.employee_tool = EmployeeTools()

    # -------------------------------------------------------
    # Get Employee Details
    # -------------------------------------------------------

    def get_employee(self, employee_id: int):

        result = self.employee_tool.get_employee(employee_id)

        if not result["success"]:

            return {
                "success": False,
                "message": "Employee not found."
            }

        return result

    # -------------------------------------------------------
    # List All Employees
    # -------------------------------------------------------

    def list_employees(self):

        return self.employee_tool.list_employees()

    # -------------------------------------------------------
    # Get Employee Email
    # -------------------------------------------------------

    def get_email(self, employee_id: int):

        employee = self.employee_tool.get_employee(employee_id)

        if not employee["success"]:

            return {

                "success": False,

                "message": "Employee not found."

            }

        return {

            "success": True,

            "email": employee["employee"]["email"]

        }

    # -------------------------------------------------------
    # Get Employee Name
    # -------------------------------------------------------

    def get_name(self, employee_id: int):

        employee = self.employee_tool.get_employee(employee_id)

        if not employee["success"]:

            return {

                "success": False,

                "message": "Employee not found."

            }

        return {

            "success": True,

            "name": employee["employee"]["name"]

        }

    # -------------------------------------------------------
    # Get Department
    # -------------------------------------------------------

    def get_department(self, employee_id: int):

        employee = self.employee_tool.get_employee(employee_id)

        if not employee["success"]:

            return {

                "success": False,

                "message": "Employee not found."

            }

        return {

            "success": True,

            "department": employee["employee"]["department"]

        }

    # -------------------------------------------------------
    # Get Designation
    # -------------------------------------------------------

    def get_designation(self, employee_id: int):

        employee = self.employee_tool.get_employee(employee_id)

        if not employee["success"]:

            return {

                "success": False,

                "message": "Employee not found."

            }

        return {

            "success": True,

            "designation": employee["employee"]["designation"]

        }

    # -------------------------------------------------------
    # Get Manager
    # -------------------------------------------------------

    def get_manager(self, employee_id: int):

        employee = self.employee_tool.get_employee(employee_id)

        if not employee["success"]:

            return {

                "success": False,

                "message": "Employee not found."

            }

        manager = employee["employee"].get("manager")

        if not manager:

            return {

                "success": False,

                "message": "Manager not assigned."

            }

        return {

            "success": True,

            "manager": manager

        }

    # -------------------------------------------------------
    # Employee Exists
    # -------------------------------------------------------

    def employee_exists(self, employee_id: int):

        employee = self.employee_tool.get_employee(employee_id)

        return {

            "success": employee["success"],

            "exists": employee["success"]

        }

    # -------------------------------------------------------
    # Employee Summary
    # -------------------------------------------------------

    def get_employee_summary(self, employee_id: int):

        employee = self.employee_tool.get_employee(employee_id)

        if not employee["success"]:

            return {

                "success": False,

                "message": "Employee not found."

            }

        emp = employee["employee"]

        return {

            "success": True,

            "summary": {

                "employee_id": emp["employee_id"],

                "name": emp["name"],

                "department": emp["department"],

                "designation": emp["designation"],

                "email": emp["email"],

                "manager": emp["manager"]

            }

        }

    # -------------------------------------------------------
    # Search Employee by Name
    # -------------------------------------------------------

    def search_employee(self, name: str):

        employees = self.employee_tool.list_employees()

        if not employees["success"]:

            return employees

        matches = []

        for emp in employees["employees"]:

            if name.lower() in emp["name"].lower():

                matches.append(emp)

        if not matches:

            return {

                "success": False,

                "message": "No employee found."

            }

        return {

            "success": True,

            "employees": matches

        }