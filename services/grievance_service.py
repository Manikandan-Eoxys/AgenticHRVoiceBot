"""
grievance_service.py

Business logic for Grievance Management.

Flow:

Agent
    ↓
GrievanceService
    ↓
GrievanceTools
    ↓
SQLite
"""

from datetime import datetime

from tools.grievance_tools import GrievanceTools
from tools.employee_tools import EmployeeTools


class GrievanceService:

    def __init__(self):

        self.grievance_tool = GrievanceTools()
        self.employee_tool = EmployeeTools()

    # -------------------------------------------------------
    # Raise New Grievance
    # -------------------------------------------------------

    def create_grievance(
        self,
        employee_id: int,
        category: str,
        description: str,
        anonymous: bool = False,
    ):

        # Validate Employee
        employee = self.employee_tool.get_employee(employee_id)

        if not employee["success"]:

            return {
                "success": False,
                "message": "Employee not found."
            }

        if not description.strip():

            return {
                "success": False,
                "message": "Grievance description cannot be empty."
            }

        # Determine Priority
        priority = self._calculate_priority(
            category,
            description
        )

        result = self.grievance_tool.create_grievance(

            employee_id=employee_id,

            category=category,

            description=description,

            priority=priority,

            anonymous=anonymous

        )

        return result

    # -------------------------------------------------------
    # Get Grievance
    # -------------------------------------------------------

    def get_grievance(self, grievance_id: int):

        return self.grievance_tool.get_grievance(
            grievance_id
        )

    # -------------------------------------------------------
    # List Employee Grievances
    # -------------------------------------------------------

    def list_employee_grievances(
        self,
        employee_id: int
    ):

        employee = self.employee_tool.get_employee(employee_id)

        if not employee["success"]:

            return {

                "success": False,

                "message": "Employee not found."

            }

        return self.grievance_tool.list_grievances(
            employee_id
        )

    # -------------------------------------------------------
    # Update Status
    # -------------------------------------------------------

    def update_status(

        self,

        grievance_id: int,

        status: str

    ):

        valid_status = [

            "Open",

            "In Progress",

            "Resolved",

            "Closed"

        ]

        if status not in valid_status:

            return {

                "success": False,

                "message": "Invalid grievance status."

            }

        return self.grievance_tool.update_status(

            grievance_id,

            status

        )

    # -------------------------------------------------------
    # Add Note
    # -------------------------------------------------------

    def add_note(

        self,

        grievance_id: int,

        note: str

    ):

        if not note.strip():

            return {

                "success": False,

                "message": "Note cannot be empty."

            }

        return self.grievance_tool.add_note(

            grievance_id,

            note

        )

    # -------------------------------------------------------
    # Escalate Grievance
    # -------------------------------------------------------

    def escalate_grievance(

        self,

        grievance_id: int

    ):

        grievance = self.grievance_tool.get_grievance(

            grievance_id

        )

        if not grievance["success"]:

            return grievance

        self.grievance_tool.update_priority(

            grievance_id,

            "High"

        )

        self.grievance_tool.update_status(

            grievance_id,

            "In Progress"

        )

        return {

            "success": True,

            "message": "Grievance has been escalated."

        }

    # -------------------------------------------------------
    # Close Grievance
    # -------------------------------------------------------

    def close_grievance(

        self,

        grievance_id: int

    ):

        return self.grievance_tool.update_status(

            grievance_id,

            "Closed"

        )

    # -------------------------------------------------------
    # Search Grievances
    # -------------------------------------------------------

    def search_grievances(

        self,

        keyword: str

    ):

        return self.grievance_tool.search_grievances(

            keyword

        )

    # -------------------------------------------------------
    # Private Helper
    # -------------------------------------------------------

    def _calculate_priority(

        self,

        category: str,

        description: str

    ):

        text = (

            category +

            " " +

            description

        ).lower()

        critical_keywords = [

            "harassment",

            "abuse",

            "violence",

            "discrimination",

            "sexual",

            "threat",

            "unsafe"

        ]

        medium_keywords = [

            "salary",

            "leave",

            "manager",

            "overtime",

            "workload",

            "attendance"

        ]

        for word in critical_keywords:

            if word in text:

                return "High"

        for word in medium_keywords:

            if word in text:

                return "Medium"

        return "Low"