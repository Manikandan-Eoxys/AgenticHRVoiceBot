"""
goals/g08_hr_policy.py  —  Goal 8: HR Policy, Tickets & Grievances

Actions:
  ├── get_leave_policy()                  detailed policy for one leave type
  ├── get_hr_policy()                     general HR policy lookup
  ├── get_code_of_conduct_policy()        conduct / dress code / VPN rules
  ├── check_pending_policy_acknowledgements() any policy needing sign-off?
  ├── acknowledge_hr_policy()             record employee acknowledgement
  ├── raise_hr_ticket()                   escalate to HR human team
  ├── check_my_hr_tickets()               check status of raised tickets
  └── report_confidential_grievance()     sensitive complaints (harassment etc.)

Durable state written: hr_tickets, policy_acknowledgements (DB)
"""

from livekit.agents import function_tool, RunContext

from goals.base import GoalService, GoalMeta
from tools.policy_tools import (
    execute_get_leave_policy,
    execute_get_hr_policy,
)
from tools.grievance_tools import (
    execute_get_code_of_conduct_policy,
    execute_report_confidential_grievance,
)
from tools.reminder_tools import (
    execute_check_pending_policy_acknowledgements,
    execute_acknowledge_hr_policy,
)
from tools.ticket_tools import (
    execute_raise_hr_ticket,
    execute_check_my_hr_tickets,
)


class HRPolicyService(GoalService):
    """
    Goal 8 — HR Policy, Tickets & Grievances

    Actions:
      ├── get_leave_policy()                  detailed policy for one leave type
      ├── get_hr_policy()                     general HR policy lookup
      ├── get_code_of_conduct_policy()        conduct / dress code / VPN rules
      ├── check_pending_policy_acknowledgements() any policy needing sign-off?
      ├── acknowledge_hr_policy()             record employee acknowledgement
      ├── raise_hr_ticket()                   escalate to HR human team
      ├── check_my_hr_tickets()               check status of raised tickets
      └── report_confidential_grievance()     sensitive complaints (harassment etc.)
    """

    meta = GoalMeta(
        id="g08_hr_policy",
        name="HR Policy, Tickets & Grievances",
        description=(
            "Handles HR policy questions (leave, conduct, documents), mandatory "
            "policy acknowledgements, HR ticket creation and status, and confidential "
            "grievance reporting."
        ),
        trigger_phrases=[
            "policy",
            "hr policy",
            "leave policy",
            "dress code",
            "code of conduct",
            "raise ticket",
            "hr ticket",
            "complaint",
            "grievance",
            "harassment",
            "misconduct",
            "acknowledge",
            "acknowledge policy",
            "pending acknowledgement",
        ],
    )

    @function_tool
    async def get_leave_policy(self, leave_type: str) -> str:
        """Look up company rules for a specific leave type."""
        self.log_action("get_leave_policy", leave_type=leave_type)
        return await execute_get_leave_policy(leave_type=leave_type)

    @function_tool
    async def get_hr_policy(self, topic: str = "") -> str:
        """Look up general HR policy information (probation, notice period, working hours, benefits, appraisal)."""
        self.log_action("get_hr_policy", topic=topic)
        return await execute_get_hr_policy(policy_name=topic)

    @function_tool
    async def get_code_of_conduct_policy(self, topic: str = "") -> str:
        """Look up company guidelines on workplace conduct, dress code, anti-harassment (POSH), remote work etiquette, or security."""
        self.log_action("get_code_of_conduct_policy", topic=topic)
        return await execute_get_code_of_conduct_policy(topic=topic)

    @function_tool
    async def check_pending_policy_acknowledgements(
        self,
        context: RunContext,
        employee_id: str = "",
    ) -> str:
        """Check if there are any mandatory company policies or documents the caller must acknowledge or sign."""
        self.log_action("check_pending_policy_acknowledgements", employee_id=employee_id)
        return await execute_check_pending_policy_acknowledgements(
            employee_id=employee_id,
            context=context,
        )

    @function_tool
    async def acknowledge_hr_policy(
        self,
        policy_topic: str,
        context: RunContext,
        employee_id: str = "",
    ) -> str:
        """Record the employee's formal acknowledgement/agreement to a specific policy or updated terms."""
        self.log_action("acknowledge_hr_policy", policy_topic=policy_topic)
        return await execute_acknowledge_hr_policy(
            policy_name=policy_topic,
            employee_id=employee_id,
            context=context,
        )

    @function_tool
    async def raise_hr_ticket(
        self,
        subject: str,
        description: str,
        context: RunContext,
        category: str = "general",
        priority: str = "medium",
        employee_id: str = "",
    ) -> str:
        """Raise an official HR support ticket to escalate an issue to human HR coordinators."""
        self.log_action("raise_hr_ticket", subject=subject, category=category)
        return await execute_raise_hr_ticket(
            category=category,
            ticket_type=subject,
            description=description,
            priority=priority,
            employee_id=employee_id,
            context=context,
        )

    @function_tool
    async def check_my_hr_tickets(
        self,
        context: RunContext,
        ticket_id: int = 0,
        employee_id: str = "",
    ) -> str:
        """Check the status of tickets previously raised by the caller."""
        self.log_action("check_my_hr_tickets", ticket_id=ticket_id)
        return await execute_check_my_hr_tickets(
            employee_id=employee_id,
            context=context,
        )

    @function_tool
    async def report_confidential_grievance(
        self,
        grievance_category: str,
        description: str,
        context: RunContext,
        employee_id: str = "",
        department_id: int = 0,
    ) -> str:
        """Privately lodge a confidential grievance, harassment report, or ethical violation."""
        self.log_action("report_confidential_grievance", grievance_category=grievance_category)
        return await execute_report_confidential_grievance(
            complaint_details=description,
            issue_type=grievance_category,
            employee_id=employee_id,
            context=context,
        )

