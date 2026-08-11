"""
calendar_tools.py

Calendar Tool Functions

Used for:
1. Schedule Meeting
2. List Meetings
3. Cancel Meeting
4. Update Meeting
"""

import sqlite3
from config import Config


class CalendarTools:

    def __init__(self):
        self.db = Config.DATABASE_PATH

    def _connect(self):
        return sqlite3.connect(self.db)

    # ----------------------------------------------------
    # Schedule Meeting
    # ----------------------------------------------------
    def schedule_meeting(
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
        INSERT INTO calendar_events(

            employee_id,

            title,

            event_date,

            event_time,

            duration,

            location,

            attendees,

            status

        )

        VALUES(

            ?,?,?,?,?,?,?,?

        )
        """,

        (

            employee_id,

            title,

            event_date,

            event_time,

            duration,

            location,

            attendees,

            "Scheduled"

        ))

        event_id = cursor.lastrowid

        conn.commit()

        conn.close()

        return {

            "success": True,

            "event_id": event_id,

            "message": "Meeting Scheduled."

        }

    # ----------------------------------------------------
    # List Meetings
    # ----------------------------------------------------
    def list_meetings(self, employee_id):

        conn = self._connect()

        cursor = conn.cursor()

        cursor.execute("""

        SELECT

            event_id,

            title,

            event_date,

            event_time,

            duration,

            location,

            status

        FROM calendar_events

        WHERE employee_id=?

        ORDER BY event_date,event_time

        """,

        (employee_id,))

        rows = cursor.fetchall()

        conn.close()

        meetings = []

        for row in rows:

            meetings.append({

                "event_id": row[0],

                "title": row[1],

                "date": row[2],

                "time": row[3],

                "duration": row[4],

                "location": row[5],

                "status": row[6]

            })

        return {

            "success": True,

            "meetings": meetings

        }

    # ----------------------------------------------------
    # Cancel Meeting
    # ----------------------------------------------------
    def cancel_meeting(self, event_id):

        conn = self._connect()

        cursor = conn.cursor()

        cursor.execute("""

        UPDATE calendar_events

        SET status='Cancelled'

        WHERE event_id=?

        """,

        (event_id,))

        conn.commit()

        conn.close()

        return {

            "success": True,

            "message": "Meeting Cancelled."

        }

    # ----------------------------------------------------
    # Update Meeting
    # ----------------------------------------------------
    def update_meeting(

        self,

        event_id,

        event_date,

        event_time

    ):

        conn = self._connect()

        cursor = conn.cursor()

        cursor.execute("""

        UPDATE calendar_events

        SET

            event_date=?,

            event_time=?

        WHERE event_id=?

        """,

        (

            event_date,

            event_time,

            event_id

        ))

        conn.commit()

        conn.close()

        return {

            "success": True,

            "message": "Meeting Updated."

        }

    # ----------------------------------------------------
    # Get Meeting
    # ----------------------------------------------------
    def get_meeting(self, event_id):

        conn = self._connect()

        cursor = conn.cursor()

        cursor.execute("""

        SELECT

            event_id,

            title,

            event_date,

            event_time,

            duration,

            location,

            attendees,

            status

        FROM calendar_events

        WHERE event_id=?

        """,

        (event_id,))

        row = cursor.fetchone()

        conn.close()

        if row is None:

            return {

                "success": False,

                "message": "Meeting not found."

            }

        return {

            "success": True,

            "meeting": {

                "event_id": row[0],

                "title": row[1],

                "date": row[2],

                "time": row[3],

                "duration": row[4],

                "location": row[5],

                "attendees": row[6],

                "status": row[7]

            }

        }