"""
policy_service.py

Business Logic for HR Policies

Flow:

Agent
    ↓
PolicyService
    ↓
PolicyTools
    ↓
SQLite / JSON
"""

from tools.policy_tools import PolicyTools


class PolicyService:

    def __init__(self):

        self.policy_tool = PolicyTools()

    # -------------------------------------------------------
    # Get Policy by Name
    # -------------------------------------------------------

    def get_policy(self, policy_name: str):

        if not policy_name.strip():

            return {
                "success": False,
                "message": "Policy name cannot be empty."
            }

        return self.policy_tool.get_policy(policy_name)

    # -------------------------------------------------------
    # Search Policies
    # -------------------------------------------------------

    def search_policy(self, keyword: str):

        if not keyword.strip():

            return {
                "success": False,
                "message": "Search keyword cannot be empty."
            }

        return self.policy_tool.search_policy(keyword)

    # -------------------------------------------------------
    # Get Leave Policy
    # -------------------------------------------------------

    def get_leave_policy(self):

        return self.policy_tool.get_policy("Leave Policy")

    # -------------------------------------------------------
    # Get Work From Home Policy
    # -------------------------------------------------------

    def get_wfh_policy(self):

        return self.policy_tool.get_policy(
            "Work From Home Policy"
        )

    # -------------------------------------------------------
    # Get Attendance Policy
    # -------------------------------------------------------

    def get_attendance_policy(self):

        return self.policy_tool.get_policy(
            "Attendance Policy"
        )

    # -------------------------------------------------------
    # Get Probation Policy
    # -------------------------------------------------------

    def get_probation_policy(self):

        return self.policy_tool.get_policy(
            "Probation Policy"
        )

    # -------------------------------------------------------
    # Get Travel Policy
    # -------------------------------------------------------

    def get_travel_policy(self):

        return self.policy_tool.get_policy(
            "Travel Policy"
        )

    # -------------------------------------------------------
    # Check Leave Eligibility
    # -------------------------------------------------------

    def check_leave_eligibility(
        self,
        leave_type: str,
        years_of_service: int
    ):

        leave_type = leave_type.lower()

        rules = {

            "casual": 0,

            "sick": 0,

            "earned": 1,

            "maternity": 1,

            "paternity": 1

        }

        if leave_type not in rules:

            return {

                "success": False,

                "message": "Unknown leave type."

            }

        eligible = years_of_service >= rules[leave_type]

        return {

            "success": True,

            "eligible": eligible,

            "leave_type": leave_type.title(),

            "minimum_service_years": rules[leave_type]

        }

    # -------------------------------------------------------
    # Explain Policy
    # -------------------------------------------------------

    def explain_policy(
        self,
        policy_name: str
    ):

        result = self.get_policy(policy_name)

        if not result["success"]:

            return result

        return {

            "success": True,

            "policy_name": result["policy"]["policy_name"],

            "summary": result["policy"]["description"]

        }

    # -------------------------------------------------------
    # List All Policies
    # -------------------------------------------------------

    def list_policies(self):

        return self.policy_tool.list_policies()

    # -------------------------------------------------------
    # Check Policy Availability
    # -------------------------------------------------------

    def policy_exists(
        self,
        policy_name: str
    ):

        result = self.get_policy(policy_name)

        return {

            "success": result["success"],

            "exists": result["success"]

        }

    # -------------------------------------------------------
    # Recommend Related Policies
    # -------------------------------------------------------

    def recommend_policies(
        self,
        keyword: str
    ):

        result = self.search_policy(keyword)

        if not result["success"]:

            return result

        return {

            "success": True,

            "recommended_policies": result["policies"]

        }