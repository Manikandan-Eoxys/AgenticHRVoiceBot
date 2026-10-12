"""
goals/g09_calendar.py  —  Goal 9: Company Calendar & Holidays

Actions:
  ├── check_company_holiday()           is a specific date a holiday?
  └── list_upcoming_company_holidays()  what holidays are coming up?

Runtime state consumed: none (public info — no identity gate required)
Durable state read:     company_holidays (DB)
Durable state written:  none
"""

from livekit.agents import function_tool

from goals.base import GoalService, GoalMeta
from tools.policy_tools import (
    execute_check_company_holiday,
    execute_list_upcoming_company_holidays,
)


class CalendarService(GoalService):
    """
    Goal 9 — Company Calendar & Holidays

    Actions:
      ├── check_company_holiday()           is a specific date a holiday?
      └── list_upcoming_company_holidays()  what holidays are coming up?
    """

    meta = GoalMeta(
        id="g09_calendar",
        name="Company Calendar & Holidays",
        description=(
            "Check whether a specific date is a company holiday and list "
            "upcoming holidays on the company calendar."
        ),
        trigger_phrases=[
            "holiday",
            "is tomorrow a holiday",
            "company holiday",
            "public holiday",
            "upcoming holidays",
            "holiday list",
            "diwali holiday",
            "is monday a holiday",
        ],
    )

    @function_tool
    async def check_company_holiday(self, target_date: str) -> str:
        """Check whether a specific date is an official company holiday."""
        self.log_action("check_company_holiday", target_date=target_date)
        return await execute_check_company_holiday(target_date)

    @function_tool
    async def list_upcoming_company_holidays(self, limit: int = 5) -> str:
        """List upcoming official company holidays (dates, holiday names, and mandatory/optional status)."""
        self.log_action("list_upcoming_company_holidays", limit=limit)
        return await execute_list_upcoming_company_holidays()

