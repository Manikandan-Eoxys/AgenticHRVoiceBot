"""
tools/grievance_tools.py

LiveKit Function Tools for Code of Conduct & Grievance:
1. Code of conduct policy inquiries (dress code, company laptops/assets, conflict of interest)
2. Confidential grievance escalation (harassment, misconduct, manager complaints)
"""

from livekit.agents import function_tool, RunContext
from services.grievance_service import GrievanceService
from tools.tool_helpers import require_verified, get_verified_id

_grievance_service = GrievanceService()


@function_tool
async def get_code_of_conduct_policy(topic: str) -> str:
    """Answer questions about workplace code of conduct:
    - Dress code rules (business casual Mon-Thu, smart casual Fri)
    - Company assets and laptop rules (authorized use, mandatory VPN)
    - Conflict of interest and gift acceptance rules
    Topic can be 'dress code', 'laptop', 'conflict of interest', or 'general'."""
    res = _grievance_service.get_code_of_conduct_info(topic)
    return res["message"]


@function_tool
async def report_confidential_grievance(
    complaint_details: str,
    issue_type: str,
    employee_id: str,
    context: RunContext,
) -> str:
    """USE WITH UTMOST SENSITIVITY: Call this when an employee wishes to report harassment,
    misconduct, or a formal complaint against their manager or colleagues.
    Registers a confidential grievance and raises a confidential HR ticket for direct senior HR handling."""
    err = require_verified(context, employee_id)
    if err:
        return err

    emp_id = get_verified_id(context, employee_id)
    res = _grievance_service.log_confidential_grievance(emp_id, complaint_details, issue_type)
    return res["message"]