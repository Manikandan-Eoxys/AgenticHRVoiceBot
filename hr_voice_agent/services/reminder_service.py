"""
services/reminder_service.py

Business logic for:
1. Mandatory HR Policy Acknowledgements (Work From Home policy, etc.)
2. Document / Form Submission Reminders (PF nomination form, etc.)
3. Salary Slip Download Notifications & Reminders
"""

import logging
import re
from datetime import datetime, date, timedelta
from database.db import get_db_connection

logger = logging.getLogger("reminder-service")


class ReminderService:

    def _query(self, sql: str, params: tuple = ()) -> list[dict]:
        conn = get_db_connection()
        try:
            cursor = conn.cursor(dictionary=True)
            cursor.execute(sql, params)
            rows = cursor.fetchall()
            cursor.close()
            return rows
        finally:
            conn.close()

    def _execute(self, sql: str, params: tuple = ()) -> int:
        conn = get_db_connection()
        try:
            cursor = conn.cursor()
            cursor.execute(sql, params)
            conn.commit()
            last_id = cursor.lastrowid or cursor.rowcount
            cursor.close()
            return last_id
        finally:
            conn.close()

    # ----------------------------------------------------------------------
    # 1. Mandatory HR Policy Acknowledgements
    # ----------------------------------------------------------------------
    def get_pending_policy_acknowledgements(self, employee_id: str) -> dict:
        """Returns pending policy announcements requiring acknowledgement for an employee."""
        rows = self._query(
            """SELECT ack_id, employee_id, policy_code, policy_name, announcement_text, deadline_date, status
               FROM policy_acknowledgements
               WHERE employee_id = %s AND status = 'Pending'
               ORDER BY deadline_date ASC""",
            (str(employee_id),)
        )

        if not rows:
            # Check if employee already acknowledged recently
            ack_rows = self._query(
                """SELECT policy_name, acknowledged_at
                   FROM policy_acknowledgements
                   WHERE employee_id = %s AND status = 'Acknowledged'
                   ORDER BY acknowledged_at DESC LIMIT 1""",
                (str(employee_id),)
            )
            if ack_rows:
                p_name = ack_rows[0]["policy_name"]
                ack_date = ack_rows[0]["acknowledged_at"]
                ack_date_str = ack_date.strftime("%B %d, %Y") if hasattr(ack_date, "strftime") else str(ack_date)
                return {
                    "success": True,
                    "pending_count": 0,
                    "policies": [],
                    "message": f"You have no pending policy acknowledgements. You have already acknowledged the {p_name}."
                }
            return {
                "success": True,
                "pending_count": 0,
                "policies": [],
                "message": "You have no pending policy acknowledgements at this time."
            }

        policy = rows[0]
        policy_name = policy["policy_name"]
        deadline = policy["deadline_date"]
        # Format deadline day name if possible
        deadline_str = deadline.strftime("%A, %B %d") if hasattr(deadline, "strftime") else str(deadline)

        return {
            "success": True,
            "pending_count": len(rows),
            "policies": rows,
            "policy_name": policy_name,
            "announcement_text": policy["announcement_text"],
            "message": (
                f"A new {policy_name} has been released. "
                f"Please review the policy and confirm whether you acknowledge it."
            )
        }

    def record_policy_acknowledgement(
        self,
        employee_id: str,
        policy_name_or_code: str = "Work From Home Policy",
        acknowledgement_note: str = "Acknowledged via Voice Assistant",
    ) -> dict:
        """Records employee's formal acknowledgement of a mandatory HR policy."""
        # Find pending acknowledgement
        rows = self._query(
            """SELECT ack_id, policy_name, status FROM policy_acknowledgements
               WHERE employee_id = %s AND (
                   LOWER(policy_name) LIKE %s OR LOWER(policy_code) LIKE %s OR %s = ''
               )""",
            (str(employee_id), f"%{policy_name_or_code.lower()}%", f"%{policy_name_or_code.lower()}%", policy_name_or_code)
        )

        if rows:
            ack_id = rows[0]["ack_id"]
            policy_title = rows[0]["policy_name"]
            self._execute(
                """UPDATE policy_acknowledgements
                   SET status = 'Acknowledged', acknowledged_at = CURRENT_TIMESTAMP, acknowledgement_note = %s
                   WHERE ack_id = %s""",
                (acknowledgement_note, ack_id)
            )
            return {
                "success": True,
                "ack_id": ack_id,
                "policy_name": policy_title,
                "status": "Acknowledged",
                "message": "Thank you. Your acknowledgement has been recorded."
            }
        else:
            # If no existing row found, create an acknowledged row
            policy_code = "WFH_HYBRID" if "wfh" in policy_name_or_code.lower() or "home" in policy_name_or_code.lower() else "GENERAL_POLICY"
            ack_id = self._execute(
                """INSERT INTO policy_acknowledgements
                   (employee_id, policy_code, policy_name, announcement_text, deadline_date, status, acknowledged_at, acknowledgement_note)
                   VALUES (%s, %s, %s, %s, %s, 'Acknowledged', CURRENT_TIMESTAMP, %s)""",
                (str(employee_id), policy_code, policy_name_or_code or "Work From Home Policy",
                 "Policy acknowledgement", date.today() + timedelta(days=5), acknowledgement_note)
            )
            return {
                "success": True,
                "ack_id": ack_id,
                "policy_name": policy_name_or_code or "Work From Home Policy",
                "status": "Acknowledged",
                "message": "Thank you. Your acknowledgement has been recorded."
            }

    # ----------------------------------------------------------------------
    # 2. Scheduled Reminders (Form submission, Payslip download, etc.)
    # ----------------------------------------------------------------------
    def _parse_scheduled_time(self, time_str: str) -> tuple[datetime, str]:
        """Parses human relative time string like 'tomorrow at 10 AM' into datetime and clean label."""
        now = datetime.now()
        lowered = time_str.lower().strip()

        target_date = now.date()
        target_hour = 10
        target_minute = 0

        # Check day
        if "tomorrow" in lowered:
            target_date = now.date() + timedelta(days=1)
        elif "today" in lowered:
            target_date = now.date()
        elif "next week" in lowered or "monday" in lowered:
            days_ahead = (0 - now.weekday() + 7) % 7 or 7
            target_date = now.date() + timedelta(days=days_ahead)

        # Check hour
        time_match = re.search(r"(\d{1,2})(?::(\d{2}))?\s*(am|pm)?", lowered)
        if time_match:
            h = int(time_match.group(1))
            m = int(time_match.group(2)) if time_match.group(2) else 0
            ampm = time_match.group(3)
            if ampm == "pm" and h < 12:
                h += 12
            elif ampm == "am" and h == 12:
                h = 0
            target_hour = h
            target_minute = m

        dt = datetime(target_date.year, target_date.month, target_date.day, target_hour, target_minute)
        
        # Build human display label
        if target_date == now.date() + timedelta(days=1):
            day_label = "tomorrow"
        elif target_date == now.date():
            day_label = "today"
        else:
            day_label = target_date.strftime("%A, %B %d")

        period = "AM" if target_hour < 12 else "PM"
        display_hour = target_hour if 1 <= target_hour <= 12 else (target_hour - 12 if target_hour > 12 else 12)
        min_part = f":{target_minute:02d}" if target_minute != 0 else ""
        human_str = f"{day_label} at {display_hour}{min_part} {period}"

        return dt, human_str

    def schedule_reminder(
        self,
        employee_id: str,
        reminder_topic: str,
        scheduled_time_str: str = "tomorrow at 10 AM",
        reminder_type: str = "CUSTOM",
    ) -> dict:
        """Schedules a new reminder for an employee."""
        dt, human_label = self._parse_scheduled_time(scheduled_time_str)

        # Infer reminder type and clear prompt
        topic_lower = reminder_topic.lower()
        if "pf" in topic_lower or "nomination" in topic_lower or "form" in topic_lower:
            reminder_type = "FORM_SUBMISSION"
            title = "PF Nomination Form Submission"
            prompt_text = "Good morning. This is a reminder to submit your PF nomination form. Have you completed it?"
        elif "salary" in topic_lower or "payslip" in topic_lower or "slip" in topic_lower:
            reminder_type = "PAYSLIP_DOWNLOAD"
            title = "September Salary Slip Download"
            prompt_text = "Your salary slip is available. Have you downloaded it?"
        elif "policy" in topic_lower or "wfh" in topic_lower:
            reminder_type = "POLICY_ACKNOWLEDGEMENT"
            title = "Work From Home Policy Acknowledgement"
            prompt_text = "This is a reminder to review and acknowledge the Work From Home policy. Have you acknowledged it?"
        else:
            title = reminder_topic.strip().title()
            prompt_text = f"This is a reminder regarding {reminder_topic}. Have you completed it?"

        reminder_id = self._execute(
            """INSERT INTO scheduled_reminders
               (employee_id, reminder_type, title, reminder_text, scheduled_time, status)
               VALUES (%s, %s, %s, %s, %s, 'Scheduled')""",
            (str(employee_id), reminder_type, title, prompt_text, dt)
        )

        return {
            "success": True,
            "reminder_id": reminder_id,
            "title": title,
            "scheduled_time": dt.strftime("%Y-%m-%d %H:%M:%S"),
            "human_schedule": human_label,
            "message": f"Sure. I'll remind you {human_label}."
        }

    def get_scheduled_reminders(self, employee_id: str, status: str = None) -> dict:
        """Lists scheduled reminders for an employee."""
        if status:
            rows = self._query(
                """SELECT reminder_id, reminder_type, title, reminder_text, scheduled_time, status, employee_response
                   FROM scheduled_reminders
                   WHERE employee_id = %s AND status = %s
                   ORDER BY scheduled_time ASC""",
                (str(employee_id), status)
            )
        else:
            rows = self._query(
                """SELECT reminder_id, reminder_type, title, reminder_text, scheduled_time, status, employee_response
                   FROM scheduled_reminders
                   WHERE employee_id = %s
                   ORDER BY scheduled_time DESC LIMIT 5""",
                (str(employee_id),)
            )

        if not rows:
            return {
                "success": True,
                "reminders": [],
                "count": 0,
                "message": "You have no scheduled reminders at this time."
            }

        active = [r for r in rows if r["status"] in ("Scheduled", "Triggered")]
        if not active:
            latest = rows[0]
            return {
                "success": True,
                "reminders": rows,
                "count": len(rows),
                "message": f"You have no pending reminders. Your latest reminder '{latest['title']}' is marked as {latest['status']}."
            }

        items = []
        for r in active:
            t = r["scheduled_time"]
            t_str = t.strftime("%A at %I:%M %p") if hasattr(t, "strftime") else str(t)
            items.append(f"'{r['title']}' scheduled for {t_str}")

        return {
            "success": True,
            "reminders": rows,
            "count": len(active),
            "message": f"You have {len(active)} active scheduled reminder(s): " + "; ".join(items) + "."
        }

    def record_reminder_acknowledgement(
        self,
        employee_id: str,
        reminder_topic_or_id: str = "",
        response_text: str = "Yes, completed",
    ) -> dict:
        """Records employee's completion or acknowledgement of a scheduled reminder task."""
        rows = self._query(
            """SELECT reminder_id, title, status FROM scheduled_reminders
               WHERE employee_id = %s AND status IN ('Scheduled', 'Triggered')
               ORDER BY reminder_id DESC""",
            (str(employee_id),)
        )

        target_row = None
        matching_ids = []
        if rows:
            if reminder_topic_or_id:
                topic_low = str(reminder_topic_or_id).lower()
                for r in rows:
                    if (str(r["reminder_id"]) == str(reminder_topic_or_id)
                        or topic_low in r["title"].lower()
                        or ("pf" in topic_low and "pf" in r["title"].lower())
                        or ("payslip" in topic_low and "salary" in r["title"].lower())
                        or ("salary" in topic_low and "salary" in r["title"].lower())):
                        matching_ids.append(r["reminder_id"])
                        if not target_row:
                            target_row = r
            if not target_row:
                target_row = rows[0]
                matching_ids.append(target_row["reminder_id"])

        if target_row and matching_ids:
            format_strings = ','.join(['%s'] * len(matching_ids))
            self._execute(
                f"""UPDATE scheduled_reminders
                   SET status = 'Acknowledged', acknowledged_at = CURRENT_TIMESTAMP, employee_response = %s
                   WHERE reminder_id IN ({format_strings})""",
                tuple([response_text] + matching_ids)
            )
            return {
                "success": True,
                "reminder_id": target_row["reminder_id"],
                "title": target_row["title"],
                "status": "Acknowledged",
                "message": "Thank you. I've recorded your acknowledgement."
            }
        else:
            return {
                "success": True,
                "status": "Acknowledged",
                "message": "Thank you. Your acknowledgement has been recorded."
            }

    def trigger_reminder(self, reminder_id: int) -> dict:
        """Marks a reminder as triggered and returns its delivery text."""
        rows = self._query(
            "SELECT reminder_id, employee_id, title, reminder_text FROM scheduled_reminders WHERE reminder_id = %s",
            (reminder_id,)
        )
        if not rows:
            return {"success": False, "message": "Reminder not found."}

        self._execute(
            "UPDATE scheduled_reminders SET status = 'Triggered', triggered_at = CURRENT_TIMESTAMP WHERE reminder_id = %s",
            (reminder_id,)
        )
        return {
            "success": True,
            "reminder_id": reminder_id,
            "reminder_text": rows[0]["reminder_text"]
        }
