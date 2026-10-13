"""
goals/g09_calendar.py  —  Goal 9: Company Calendar & Holidays

Actions:
  ├── check_company_holiday()           is a specific date a holiday?
  └── list_upcoming_company_holidays()  what holidays are coming up?

Layer flow:
  Agent / LLM -> Goal 9 (g09_calendar) -> PolicyService (services layer) -> DB
"""

from livekit.agents import function_tool

from goals.base import GoalService, GoalMeta
from services.policy_service import PolicyService


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

    def __init__(self, **kwargs) -> None:
        super().__init__(**kwargs)
        self.policy_service = PolicyService()

    @function_tool
    async def check_company_holiday(self, target_date: str) -> str:
        """Check whether a specific date is an official company holiday."""
        self.log_action("check_company_holiday", target_date=target_date)
        res = self.policy_service.check_is_holiday(target_date)
        return res["message"]

    @function_tool
    async def list_upcoming_company_holidays(self, limit: int = 5) -> str:
        """List upcoming official company holidays (dates, holiday names, and mandatory/optional status)."""
        self.log_action("list_upcoming_company_holidays", limit=limit)
        holidays = self.policy_service.list_upcoming_holidays(limit=limit)
        if not holidays:
            return "No upcoming holidays found on the company calendar."
        lines = [f"{h['holiday_date']} ({h['holiday_day']}): {h['holiday_name']}" for h in holidays]
        return "Upcoming company holidays: " + "; ".join(lines)


# ── Standalone tool execution helpers & aliases ───────────────────────────────
_default_calendar_goal = CalendarService()

async def check_company_holiday(target_date: str) -> str:
    return await _default_calendar_goal.check_company_holiday(target_date)

async def list_upcoming_company_holidays(limit: int = 5) -> str:
    return await _default_calendar_goal.list_upcoming_company_holidays(limit)

execute_check_company_holiday = check_company_holiday
execute_list_upcoming_company_holidays = list_upcoming_company_holidays
