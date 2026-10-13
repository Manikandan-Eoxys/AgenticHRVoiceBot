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

Layer flow:
  Agent / LLM -> Goal 8 (g08_hr_policy) -> PolicyService / GrievanceService / TicketService / ReminderService -> DB
"""

from livekit.agents import function_tool, RunContext

from goals.base import GoalService, GoalMeta
from goals.helpers import require_verified, get_verified_id
from services.policy_service import PolicyService
from services.grievance_service import GrievanceService
from services.reminder_service import ReminderService
from services.ticket_service import TicketService


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

    def __init__(self, **kwargs) -> None:
        super().__init__(**kwargs)
        self.policy_service = PolicyService()
        self.grievance_service = GrievanceService()
        self.reminder_service = ReminderService()
        self.ticket_service = TicketService()

    @function_tool
    async def get_leave_policy(self, leave_type: str) -> str:
        """Look up company rules for a specific leave type."""
        self.log_action("get_leave_policy", leave_type=leave_type)
        res = self.policy_service.get_leave_policy(leave_type)
        return res["message"]

    @function_tool
    async def get_hr_policy(self, topic: str = "", policy_name: str = "") -> str:
        """Look up general HR policy information (probation, notice period, working hours, benefits, appraisal)."""
        target = topic or policy_name
        self.log_action("get_hr_policy", topic=target)
        res = self.policy_service.get_policy(target)
        if res.get("success"):
            return f"{res['policy_name']} ({res['category']}): {res['details']}"
        return res["message"]

    @function_tool
    async def get_code_of_conduct_policy(self, topic: str = "") -> str:
        """Look up company guidelines on workplace conduct, dress code, anti-harassment (POSH), remote work etiquette, or security."""
        self.log_action("get_code_of_conduct_policy", topic=topic)
        res = self.grievance_service.get_code_of_conduct_info(topic)
        return res["message"]

    @function_tool
    async def check_pending_policy_acknowledgements(
        self,
        context: RunContext,
        employee_id: str = "",
    ) -> str:
        """Check whether there are any mandatory company policies that the caller must acknowledge."""
        self.log_action("check_pending_policy_acknowledgements", employee_id=employee_id)
        err = require_verified(context, employee_id)
        if err:
            return err
        emp_id = get_verified_id(context, employee_id)
        res = self.reminder_service.get_pending_policy_acknowledgements(emp_id)
        return res["message"]

    @function_tool
    async def acknowledge_hr_policy(
        self,
        policy_name: str,
        context: RunContext,
        employee_id: str = "",
    ) -> str:
        """Record the caller's acknowledgement of an HR policy after they give verbal agreement."""
        self.log_action("acknowledge_hr_policy", policy_name=policy_name, employee_id=employee_id)
        err = require_verified(context, employee_id)
        if err:
            return err
        emp_id = get_verified_id(context, employee_id)
        res = self.reminder_service.record_policy_acknowledgement(
            employee_id=emp_id,
            policy_name_or_code=policy_name or "Work From Home Policy",
            acknowledgement_note="Acknowledged via HR Voice Assistant",
        )
        return res["message"]

    @function_tool
    async def raise_hr_ticket(
        self,
        category: str,
        ticket_type: str,
        description: str,
        priority: str,
        context: RunContext,
        employee_id: str = "",
    ) -> str:
        """Raise an HR ticket for an issue that requires manual HR team action."""
        self.log_action("raise_hr_ticket", category=category, ticket_type=ticket_type, priority=priority)
        err = require_verified(context, employee_id)
        if err:
            return err
        emp_id = get_verified_id(context, employee_id)
        res = self.ticket_service.create_ticket(emp_id, category, ticket_type, description, priority or "Normal")
        return res["message"]

    @function_tool
    async def check_my_hr_tickets(
        self,
        context: RunContext,
        employee_id: str = "",
    ) -> str:
        """Check the status of tickets previously raised by the caller."""
        self.log_action("check_my_hr_tickets", employee_id=employee_id)
        err = require_verified(context, employee_id)
        if err:
            return err
        emp_id = get_verified_id(context, employee_id)
        res = self.ticket_service.get_employee_tickets(emp_id)
        return res["message"]

    @function_tool
    async def report_confidential_grievance(
        self,
        complaint_details: str,
        issue_type: str,
        context: RunContext,
        employee_id: str = "",
    ) -> str:
        """Confidential reporting for sensitive complaints (harassment, ethical concerns, misconduct)."""
        self.log_action("report_confidential_grievance", issue_type=issue_type)
        err = require_verified(context, employee_id)
        if err:
            return err
        emp_id = get_verified_id(context, employee_id)
        res = self.grievance_service.log_confidential_grievance(emp_id, complaint_details, issue_type)
        return res["message"]


# ── Standalone tool execution helpers & aliases ───────────────────────────────
_default_hr_policy_goal = HRPolicyService()

async def get_leave_policy(leave_type: str) -> str:
    return await _default_hr_policy_goal.get_leave_policy(leave_type)

async def get_hr_policy(policy_name: str = "", topic: str = "") -> str:
    return await _default_hr_policy_goal.get_hr_policy(topic=topic or policy_name)

async def get_code_of_conduct_policy(topic: str = "") -> str:
    return await _default_hr_policy_goal.get_code_of_conduct_policy(topic)

async def check_pending_policy_acknowledgements(employee_id: str = "", context: RunContext = None) -> str:
    return await _default_hr_policy_goal.check_pending_policy_acknowledgements(context=context, employee_id=employee_id)

async def acknowledge_hr_policy(policy_name: str = "", employee_id: str = "", context: RunContext = None) -> str:
    return await _default_hr_policy_goal.acknowledge_hr_policy(policy_name=policy_name, context=context, employee_id=employee_id)

async def raise_hr_ticket(
    category: str = "",
    ticket_type: str = "",
    description: str = "",
    priority: str = "Normal",
    employee_id: str = "",
    context: RunContext = None,
) -> str:
    return await _default_hr_policy_goal.raise_hr_ticket(
        category=category,
        ticket_type=ticket_type,
        description=description,
        priority=priority,
        context=context,
        employee_id=employee_id,
    )

async def check_my_hr_tickets(employee_id: str = "", context: RunContext = None) -> str:
    return await _default_hr_policy_goal.check_my_hr_tickets(context=context, employee_id=employee_id)

async def report_confidential_grievance(
    complaint_details: str = "",
    issue_type: str = "",
    employee_id: str = "",
    context: RunContext = None,
) -> str:
    return await _default_hr_policy_goal.report_confidential_grievance(
        complaint_details=complaint_details,
        issue_type=issue_type,
        context=context,
        employee_id=employee_id,
    )

execute_get_leave_policy = get_leave_policy
execute_get_hr_policy = get_hr_policy
execute_get_code_of_conduct_policy = get_code_of_conduct_policy
execute_check_pending_policy_acknowledgements = check_pending_policy_acknowledgements
execute_acknowledge_hr_policy = acknowledge_hr_policy
execute_raise_hr_ticket = raise_hr_ticket
execute_check_my_hr_tickets = check_my_hr_tickets
execute_report_confidential_grievance = report_confidential_grievance
