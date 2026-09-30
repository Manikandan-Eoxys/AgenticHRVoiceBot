"""
services/grievance_service.py

Business logic for Code of Conduct & Grievances:
1. Standard inquiries: Dress code, company laptop/asset policy, conflict of interest
2. Sensitive escalations: Harassment, misconduct, manager grievances (confidential logging and HR team escalation)
"""

import logging
from database.db import get_db_connection

logger = logging.getLogger("grievance-service")


class GrievanceService:

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
            result = cursor.lastrowid if cursor.lastrowid else cursor.rowcount
            cursor.close()
            return result
        except Exception:
            conn.rollback()
            raise
        finally:
            conn.close()

    def get_code_of_conduct_info(self, topic: str = "general") -> dict:
        """Provides guidance on dress code, equipment/laptops, and conflict of interest."""
        t = (topic or "").strip().lower()

        if "dress" in t or "wear" in t or "clothing" in t:
            return {
                "success": True,
                "topic": "Dress Code",
                "message": "Company dress code is business casual Monday through Thursday, and smart casuals (e.g. jeans and polo t-shirts) on Fridays."
            }
        elif "laptop" in t or "computer" in t or "asset" in t or "device" in t:
            return {
                "success": True,
                "topic": "Company Assets & Laptops",
                "message": "Company-provided laptops and equipment are strictly for authorized work purposes. Company VPN is mandatory when connecting from external networks, and installing unauthorized software is prohibited."
            }
        elif "conflict" in t or "gift" in t or "outside" in t:
            return {
                "success": True,
                "topic": "Conflict of Interest",
                "message": "Employees must disclose any external business interests, secondary employment, or board memberships. Accepting gifts or hospitality valued over ₹1,000 from vendors or clients must be disclosed to HR."
            }
        else:
            rows = self._query(
                "SELECT details FROM hr_policies WHERE policy_code = 'CONDUCT_GRIEVANCE'"
            )
            details = rows[0]["details"] if rows else "Code of conduct covers workplace ethics, dress code, asset usage, and anti-harassment."
            return {
                "success": True,
                "topic": "Code of Conduct",
                "message": details
            }

    def log_confidential_grievance(self, employee_id: str, issue_description: str, issue_type: str = "Confidential Grievance") -> dict:
        """Registers a confidential grievance into grievances and creates a high-priority HR ticket."""
        # 1. Insert into grievances table
        grievance_id = self._execute(
            """INSERT INTO grievances (employee_id, title, description, status, priority)
               VALUES (%s, %s, %s, 'Open', 'Confidential')""",
            (str(employee_id), issue_type, issue_description)
        )

        # 2. Insert matching confidential HR ticket
        ticket_num = f"TICK-G{grievance_id:04d}"
        self._execute(
            """INSERT INTO hr_tickets (ticket_number, employee_id, category, ticket_type, description, status, priority)
               VALUES (%s, %s, 'Code of Conduct & Grievance', %s, %s, 'Open', 'Confidential')""",
            (ticket_num, str(employee_id), issue_type, issue_description)
        )

        return {
            "success": True,
            "grievance_id": grievance_id,
            "ticket_number": ticket_num,
            "message": f"This is a sensitive matter. I take this very seriously and have raised confidential HR ticket {ticket_num} with our specialized HR team. A designated senior HR officer will contact you confidentially."
        }