"""
modules/date_utils.py

Robust date parsing and normalization for HR Voice Assistant.
Converts relative date strings ("tomorrow", "day after tomorrow", "next thursday")
and corrects outdated years (e.g. 2023 -> 2026) to the current real-time year/date.
"""

from datetime import datetime, timedelta, date


def normalize_date(date_str: str) -> str:
    """
    Parse and normalize a date string into YYYY-MM-DD format using current real-time.

    Handles:
      - None / Empty -> Today's YYYY-MM-DD
      - "today" -> Today's YYYY-MM-DD
      - "tomorrow" -> Tomorrow's YYYY-MM-DD
      - "day after tomorrow" -> Day after tomorrow's YYYY-MM-DD
      - "next thursday", "this friday", etc. -> Upcoming day of week YYYY-MM-DD
      - Outdated years (e.g., "2023-08-27") -> auto-corrects year to current year (2026)
    """
    if not date_str or not isinstance(date_str, str):
        return datetime.now().strftime("%Y-%m-%d")

    clean_str = date_str.strip().lower()
    now = datetime.now()
    today = now.date()

    if clean_str in ("today", "now"):
        return today.strftime("%Y-%m-%d")

    if clean_str == "tomorrow":
        return (today + timedelta(days=1)).strftime("%Y-%m-%d")

    if clean_str in ("day after tomorrow", "day-after-tomorrow", "day after tommorrow"):
        return (today + timedelta(days=2)).strftime("%Y-%m-%d")

    # Day of week handling (e.g. "next thursday", "this friday")
    days_of_week = ["monday", "tuesday", "wednesday", "thursday", "friday", "saturday", "sunday"]
    for idx, day_name in enumerate(days_of_week):
        if day_name in clean_str:
            current_weekday = today.weekday()  # Monday is 0, Sunday is 6
            days_ahead = idx - current_weekday
            if days_ahead <= 0:  # If target day already passed this week, jump to next week
                days_ahead += 7
            target_date = today + timedelta(days=days_ahead)
            return target_date.strftime("%Y-%m-%d")

    # Standard YYYY-MM-DD or DD-MM-YYYY parsing & year correction
    for fmt in ("%Y-%m-%d", "%d-%m-%Y", "%d/%m/%Y", "%Y/%m/%d"):
        try:
            dt = datetime.strptime(clean_str, fmt)
            res_date = dt.date()
            # If the year is in the past (e.g., 2023 when today is 2026)
            if res_date.year < today.year:
                res_date = res_date.replace(year=today.year)
                # If that date in current year has already passed by > 30 days, move to next year
                if res_date < (today - timedelta(days=30)):
                    res_date = res_date.replace(year=today.year + 1)
            return res_date.strftime("%Y-%m-%d")
        except ValueError:
            continue

    return date_str
