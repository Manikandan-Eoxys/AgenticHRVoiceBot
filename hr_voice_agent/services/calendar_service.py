"""
calendar_service.py

Business Logic for Calendar Management

Flow:

Agent
    ↓
CalendarService
    ↓
CalendarTools
    ↓
SQLite
"""

from datetime import datetime

from tools.calendar_tools import CalendarTools
from tools.employee_tools import EmployeeTools


class CalendarService:

    def __init__(self):

        self.calendar_tool = CalendarTools()
        self.employee_tool = EmployeeTools()

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

        # Validate Employee
        employee = self.employee_tool.get_employee(employee_id)

        if not employee["success"]:

            return {
                "success": False,
                "message": "Employee not found."
            }

        # Validate Title
        if not title.strip():

            return {
                "success": False,
                "message": "Meeting title cannot be empty."
            }

        # Validate Date
        try:

            datetime.strptime(event_date, "%Y-%m-%d")

        except ValueError:

            return {
                "success": False,
                "message": "Date format should be YYYY-MM-DD."
            }

        # Validate Time
        try:

            datetime.strptime(event_time, "%H:%M")

        except ValueError:

            return {
                "success": False,
                "message": "Time format should be HH:MM."
            }

        # Check Meeting Conflict
        conflict = self.check_conflict(
            employee_id,
            event_date,
            event_time
        )

        if conflict["conflict"]:

            return {

                "success": False,

                "message":
                    "Another meeting already exists at this time."

            }

        return self.calendar_tool.schedule_meeting(

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

        return self.calendar_tool.cancel_meeting(event_id)

    # -------------------------------------------------------
    # List Meetings
    # -------------------------------------------------------

    def list_meetings(self, employee_id: int):

        employee = self.employee_tool.get_employee(employee_id)

        if not employee["success"]:

            return {

                "success": False,

                "message": "Employee not found."

            }

        return self.calendar_tool.list_meetings(employee_id)

    # -------------------------------------------------------
    # Upcoming Meetings
    # -------------------------------------------------------

    def upcoming_meetings(self, employee_id: int):

        meetings = self.calendar_tool.list_meetings(employee_id)

        if not meetings["success"]:

            return meetings

        today = datetime.today().date()

        upcoming = []

        for meeting in meetings["meetings"]:

            meeting_date = datetime.strptime(
                meeting["event_date"],
                "%Y-%m-%d"
            ).date()

            if meeting_date >= today:

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

    ):

        meetings = self.calendar_tool.list_meetings(employee_id)

        if not meetings["success"]:

            return {

                "conflict": False

            }

        for meeting in meetings["meetings"]:

            if (

                meeting["event_date"] == event_date

                and

                meeting["event_time"] == event_time

                and

                meeting["status"] == "Scheduled"

            ):

                return {

                    "conflict": True,

                    "meeting": meeting

                }

        return {

            "conflict": False

        }

    # -------------------------------------------------------
    # Reschedule Meeting
    # -------------------------------------------------------

    def reschedule_meeting(

        self,

        event_id: int,

        new_date: str,

        new_time: str

    ):

        return self.calendar_tool.reschedule_meeting(

            event_id,

            new_date,

            new_time

        )

    # -------------------------------------------------------
    # Meeting Details
    # -------------------------------------------------------

    def get_meeting(self, event_id: int):

        return self.calendar_tool.get_meeting(event_id)

    # -------------------------------------------------------
    # Today's Meetings
    # -------------------------------------------------------

    def todays_meetings(self, employee_id: int):

        meetings = self.calendar_tool.list_meetings(employee_id)

        if not meetings["success"]:

            return meetings

        today = datetime.today().strftime("%Y-%m-%d")

        today_list = []

        for meeting in meetings["meetings"]:

            if meeting["event_date"] == today:

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

    ):

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

        meetings = self.calendar_tool.list_meetings(employee_id)

        booked = []

        if meetings["success"]:

            for meeting in meetings["meetings"]:

                if meeting["event_date"] == event_date:

                    booked.append(meeting["event_time"])

        available = [

            slot

            for slot in all_slots

            if slot not in booked

        ]

        return {

            "success": True,

            "available_slots": available

        }