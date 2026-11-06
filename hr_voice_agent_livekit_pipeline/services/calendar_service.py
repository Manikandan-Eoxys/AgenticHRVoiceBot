"""
services/calendar_service.py

Business Logic and database queries for Calendar Management.

Architecture:
  Goals Layer (g09_calendar, etc.)
      ↓
  CalendarService (Services Layer)
      ↓
  MySQL Database (database.db)
"""

from datetime import datetime
from typing import Any
from database.db import get_db_connection
from services.employee_service import EmployeeService


class CalendarService:
    """Calendar service managing meetings, schedule validation, and event queries."""

    def __init__(self):
        self.employee_service = EmployeeService()

    def _connect(self):
        return get_db_connection()

    # ----------------------------------------------------
    # Database operations (formerly CalendarTools)
    # ----------------------------------------------------
    def _db_schedule_meeting(
        self,
        employee_id,
        title,
        event_date,
        event_time,
        duration=30,
        location="Meeting Room",
        attendees=""
    ):
        conn = self._connect()
        cursor = conn.cursor()

        cursor.execute("""
            INSERT INTO calendar_events
            (employee_id, title, event_date, event_time, duration, location, attendees, status)
            VALUES (%s, %s, %s, %s, %s, %s, %s, 'Scheduled')
        """, (str(employee_id), title, event_date, event_time, duration, location, attendees))

        event_id = cursor.lastrowid
        conn.commit()
        cursor.close()
        conn.close()

        return {
            "success": True,
            "event_id": event_id,
            "message": "Meeting Scheduled."
        }

    def _db_list_meetings(self, employee_id) -> dict[str, Any]:
        conn = self._connect()
        cursor: Any = conn.cursor(dictionary=True)

        cursor.execute("""
            SELECT event_id, title, event_date, event_time, duration, location, attendees, status
            FROM calendar_events
            WHERE employee_id = %s
            ORDER BY event_date, event_time
        """, (str(employee_id),))

        rows = cursor.fetchall()
        cursor.close()
        conn.close()

        meetings = []
        for r in rows:
            meetings.append({
                "event_id": r["event_id"],
                "title": r["title"],
                "event_date": str(r["event_date"]),
                "date": str(r["event_date"]),
                "event_time": str(r["event_time"]),
                "time": str(r["event_time"]),
                "duration": r["duration"],
                "location": r["location"],
                "attendees": r.get("attendees", ""),
                "status": r["status"]
            })

        return {
            "success": True,
            "meetings": meetings
        }

    def _db_cancel_meeting(self, event_id):
        conn = self._connect()
        cursor = conn.cursor()

        cursor.execute("""
            UPDATE calendar_events
            SET status = 'Cancelled'
            WHERE event_id = %s
        """, (event_id,))

        conn.commit()
        cursor.close()
        conn.close()

        return {
            "success": True,
            "message": "Meeting Cancelled."
        }

    def _db_update_meeting(self, event_id, event_date, event_time):
        conn = self._connect()
        cursor = conn.cursor()

        cursor.execute("""
            UPDATE calendar_events
            SET event_date = %s, event_time = %s
            WHERE event_id = %s
        """, (event_date, event_time, event_id))

        conn.commit()
        cursor.close()
        conn.close()

        return {
            "success": True,
            "message": "Meeting Updated."
        }

    def _db_get_meeting(self, event_id) -> dict[str, Any]:
        conn = self._connect()
        cursor: Any = conn.cursor(dictionary=True)

        cursor.execute("""
            SELECT event_id, title, event_date, event_time, duration, location, attendees, status
            FROM calendar_events
            WHERE event_id = %s
        """, (event_id,))

        row = cursor.fetchone()
        cursor.close()
        conn.close()

        if row is None:
            return {
                "success": False,
                "message": "Meeting not found."
            }

        return {
            "success": True,
            "meeting": {
                "event_id": row["event_id"],
                "title": row["title"],
                "event_date": str(row["event_date"]),
                "date": str(row["event_date"]),
                "event_time": str(row["event_time"]),
                "time": str(row["event_time"]),
                "duration": row["duration"],
                "location": row["location"],
                "attendees": row.get("attendees", ""),
                "status": row["status"]
            }
        }

    # -------------------------------------------------------
    # Schedule Meeting
    # -------------------------------------------------------
    def schedule_meeting(
        self,
        employee_id: int,
        title: str,
        event_date: str,
        event_time: str,
        duration: int = 30,
        location: str = "Meeting Room",
        attendees: str = "",
    ):
        employee = self.employee_service.get_employee(employee_id)
        if not employee["success"]:
            return {
                "success": False,
                "message": "Employee not found."
            }

        if not title.strip():
            return {
                "success": False,
                "message": "Meeting title cannot be empty."
            }

        try:
            datetime.strptime(event_date, "%Y-%m-%d")
        except ValueError:
            return {
                "success": False,
                "message": "Date format should be YYYY-MM-DD."
            }

        try:
            datetime.strptime(event_time, "%H:%M")
        except ValueError:
            return {
                "success": False,
                "message": "Time format should be HH:MM."
            }

        conflict = self.check_conflict(
            employee_id,
            event_date,
            event_time
        )
        if conflict["conflict"]:
            return {
                "success": False,
                "message": "Another meeting already exists at this time."
            }

        return self._db_schedule_meeting(
            employee_id=employee_id,
            title=title,
            event_date=event_date,
            event_time=event_time,
            duration=duration,
            location=location,
            attendees=attendees
        )

    # -------------------------------------------------------
    # Cancel Meeting
    # -------------------------------------------------------
    def cancel_meeting(self, event_id: int):
        return self._db_cancel_meeting(event_id)

    # -------------------------------------------------------
    # List Meetings
    # -------------------------------------------------------
    def list_meetings(self, employee_id: int) -> dict[str, Any]:
        employee = self.employee_service.get_employee(employee_id)
        if not employee["success"]:
            return {
                "success": False,
                "message": "Employee not found."
            }
        return self._db_list_meetings(employee_id)

    # -------------------------------------------------------
    # Upcoming Meetings
    # -------------------------------------------------------
    def upcoming_meetings(self, employee_id: int) -> dict[str, Any]:
        meetings = self.list_meetings(employee_id)
        if not meetings.get("success"):
            return meetings

        today = datetime.today().date()
        upcoming = []
        raw_meetings = meetings.get("meetings", [])
        meeting_list: list[dict[str, Any]] = raw_meetings if isinstance(raw_meetings, list) else []

        for meeting in meeting_list:
            if not isinstance(meeting, dict):
                continue
            try:
                meeting_date = datetime.strptime(
                    str(meeting.get("event_date", "")),
                    "%Y-%m-%d"
                ).date()
                if meeting_date >= today:
                    upcoming.append(meeting)
            except Exception:
                upcoming.append(meeting)

        return {
            "success": True,
            "meetings": upcoming
        }

    # -------------------------------------------------------
    # Check Time Conflict
    # -------------------------------------------------------
    def check_conflict(
        self,
        employee_id: int,
        event_date: str,
        event_time: str
    ) -> dict[str, Any]:
        meetings = self.list_meetings(employee_id)
        if not meetings.get("success"):
            return {"conflict": False}

        raw_meetings = meetings.get("meetings", [])
        meeting_list: list[dict[str, Any]] = raw_meetings if isinstance(raw_meetings, list) else []

        for meeting in meeting_list:
            if not isinstance(meeting, dict):
                continue
            if (
                meeting.get("event_date") == event_date
                and meeting.get("event_time") == event_time
                and meeting.get("status") == "Scheduled"
            ):
                return {
                    "conflict": True,
                    "meeting": meeting
                }

        return {"conflict": False}

    # -------------------------------------------------------
    # Reschedule Meeting
    # -------------------------------------------------------
    def reschedule_meeting(
        self,
        event_id: int,
        new_date: str,
        new_time: str
    ) -> dict[str, Any]:
        return self._db_update_meeting(event_id, new_date, new_time)

    # -------------------------------------------------------
    # Meeting Details
    # -------------------------------------------------------
    def get_meeting(self, event_id: int) -> dict[str, Any]:
        return self._db_get_meeting(event_id)

    # -------------------------------------------------------
    # Today's Meetings
    # -------------------------------------------------------
    def todays_meetings(self, employee_id: int) -> dict[str, Any]:
        meetings = self.list_meetings(employee_id)
        if not meetings.get("success"):
            return meetings

        today = datetime.today().strftime("%Y-%m-%d")
        today_list = []
        raw_meetings = meetings.get("meetings", [])
        meeting_list: list[dict[str, Any]] = raw_meetings if isinstance(raw_meetings, list) else []

        for meeting in meeting_list:
            if not isinstance(meeting, dict):
                continue
            if meeting.get("event_date") == today:
                today_list.append(meeting)

        return {
            "success": True,
            "meetings": today_list
        }

    # -------------------------------------------------------
    # Available Time Slots
    # -------------------------------------------------------
    def available_slots(
        self,
        employee_id: int,
        event_date: str
    ) -> dict[str, Any]:
        all_slots = [
            "09:00",
            "10:00",
            "11:00",
            "12:00",
            "14:00",
            "15:00",
            "16:00",
            "17:00"
        ]

        meetings = self.list_meetings(employee_id)
        booked = []

        if meetings.get("success"):
            raw_meetings = meetings.get("meetings", [])
            meeting_list: list[dict[str, Any]] = raw_meetings if isinstance(raw_meetings, list) else []
            for meeting in meeting_list:
                if not isinstance(meeting, dict):
                    continue
                if meeting.get("event_date") == event_date:
                    booked.append(meeting.get("event_time"))

        available = [slot for slot in all_slots if slot not in booked]

        return {
            "success": True,
            "available_slots": available
        }


# Backward compatibility alias
CalendarTools = CalendarService