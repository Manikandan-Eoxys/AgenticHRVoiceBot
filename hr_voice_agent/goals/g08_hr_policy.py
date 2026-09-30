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

from goals.base import GoalService, GoalMeta
from hr_tools import (
    get_leave_policy,
    get_hr_policy,
    get_code_of_conduct_policy,
    check_pending_policy_acknowledgements,
    acknowledge_hr_policy,
    raise_hr_ticket,
    check_my_hr_tickets,
    report_confidential_grievance,
)


class HRPolicyService(GoalService):
    """
    Goal 8 — HR Policy, Tickets & Grievances

    Covers policy lookups, mandatory acknowledgements, HR ticket
    creation/status, and confidential grievance reporting.

    Design note: This goal intentionally bundles policy reading AND
    ticket escalation because both arise from the same conversational
    context: the employee either wants to understand policy, or has
    already tried and needs human help.
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

    def tools(self) -> list:
        return [
            get_leave_policy,
            get_hr_policy,
            get_code_of_conduct_policy,
            check_pending_policy_acknowledgements,
            acknowledge_hr_policy,
            raise_hr_ticket,
            check_my_hr_tickets,
            report_confidential_grievance,
        ]
