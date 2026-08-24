"""
calendar_tools.py

Calendar Tool Functions using MySQL.

Used for:
1. Schedule Meeting
2. List Meetings
3. Cancel Meeting
4. Update Meeting
"""

from database.db import get_db_connection


class CalendarTools:

    def _connect(self):
        return get_db_connection()

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

    # ----------------------------------------------------
    # List Meetings
    # ----------------------------------------------------
    def list_meetings(self, employee_id):
        conn = self._connect()
        cursor = conn.cursor(dictionary=True)

        cursor.execute("""
            SELECT event_id, title, event_date, event_time, duration, location, status
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
                "date": str(r["event_date"]),
                "time": r["event_time"],
                "duration": r["duration"],
                "location": r["location"],
                "status": r["status"]
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

    # ----------------------------------------------------
    # Get Meeting
    # ----------------------------------------------------
    def get_meeting(self, event_id):
        conn = self._connect()
        cursor = conn.cursor(dictionary=True)

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
                "date": str(row["event_date"]),
                "time": row["event_time"],
                "duration": row["duration"],
                "location": row["location"],
                "attendees": row["attendees"],
                "status": row["status"]
            }
        }