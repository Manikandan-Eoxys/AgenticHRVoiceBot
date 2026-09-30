"""
goals/g09_calendar.py  —  Goal 9: Company Calendar & Holidays

Actions:
  ├── check_company_holiday()           is a specific date a holiday?
  └── list_upcoming_company_holidays()  what holidays are coming up?

Runtime state consumed: none (public info — no identity gate required)
Durable state read:     company_holidays (DB)
Durable state written:  none
"""

from goals.base import GoalService, GoalMeta
from hr_tools import check_company_holiday, list_upcoming_company_holidays


class CalendarService(GoalService):
    """
    Goal 9 — Company Calendar & Holidays

    Answers questions about company holidays: whether a given date is a
    holiday, and what public/optional holidays are coming up.

    Note: This goal does NOT require identity verification — holiday
    information is company-wide and public. The LLM may call these tools
    even before confirm_employee_identity.
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

    def tools(self) -> list:
        return [
            check_company_holiday,
            list_upcoming_company_holidays,
        ]
