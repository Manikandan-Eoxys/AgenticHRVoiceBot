"""
grievance_tools.py

Grievance Management Tool

Functions:
1. Create Grievance
2. Get Grievance
3. List Employee Grievances
4. Update Status
5. Add Comment
6. Close Grievance

Used by OpenAI Function Calling / LiveKit Agent
"""

import sqlite3
from datetime import datetime

from config import Config


class GrievanceTools:

    def __init__(self):
        self.db = Config.DATABASE_PATH

    def _connect(self):
        return sqlite3.connect(self.db)

    # ----------------------------------------------------
    # Create New Grievance
    # ----------------------------------------------------
    def create_grievance(
        self,
        employee_id,
        title,
        description
    ):

        conn = self._connect()
        cursor = conn.cursor()

        created_at = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

        cursor.execute(
            """
            INSERT INTO grievances
            (
                employee_id,
                title,
                description,
                status,
                created_at
            )
            VALUES
            (
                ?, ?, ?, ?, ?
            )
            """,
            (
                employee_id,
                title,
                description,
                "Open",
                created_at
            )
        )

        grievance_id = cursor.lastrowid

        conn.commit()
        conn.close()

        return {
            "success": True,
            "grievance_id": grievance_id,
            "status": "Open",
            "message": "Grievance created successfully."
        }

    # ----------------------------------------------------
    # Get Single Grievance
    # ----------------------------------------------------
    def get_grievance(self, grievance_id):

        conn = self._connect()
        cursor = conn.cursor()

        cursor.execute(
            """
            SELECT
                grievance_id,
                employee_id,
                title,
                description,
                status,
                created_at
            FROM grievances
            WHERE grievance_id=?
            """,
            (grievance_id,)
        )

        row = cursor.fetchone()

        conn.close()

        if row is None:

            return {
                "success": False,
                "message": "Grievance not found."
            }

        return {

            "success": True,

            "grievance": {

                "grievance_id": row[0],

                "employee_id": row[1],

                "title": row[2],

                "description": row[3],

                "status": row[4],

                "created_at": row[5]

            }

        }

    # ----------------------------------------------------
    # List Employee Grievances
    # ----------------------------------------------------
    def list_grievances(self, employee_id):

        conn = self._connect()

        cursor = conn.cursor()

        cursor.execute(
            """
            SELECT

                grievance_id,

                title,

                status,

                created_at

            FROM grievances

            WHERE employee_id=?

            ORDER BY grievance_id DESC
            """,
            (employee_id,)
        )

        rows = cursor.fetchall()

        conn.close()

        grievances = []

        for row in rows:

            grievances.append({

                "grievance_id": row[0],

                "title": row[1],

                "status": row[2],

                "created_at": row[3]

            })

        return {

            "success": True,

            "grievances": grievances

        }

    # ----------------------------------------------------
    # Update Status
    # ----------------------------------------------------
    def update_status(
        self,
        grievance_id,
        status
    ):

        conn = self._connect()

        cursor = conn.cursor()

        cursor.execute(
            """
            UPDATE grievances
            SET status=?
            WHERE grievance_id=?
            """,
            (
                status,
                grievance_id
            )
        )

        conn.commit()

        conn.close()

        return {

            "success": True,

            "message": "Status Updated."

        }

    # ----------------------------------------------------
    # Close Grievance
    # ----------------------------------------------------
    def close_grievance(self, grievance_id):

        return self.update_status(
            grievance_id,
            "Closed"
        )

    # ----------------------------------------------------
    # Add Comment
    # ----------------------------------------------------
    def add_comment(
        self,
        grievance_id,
        comment
    ):

        conn = self._connect()

        cursor = conn.cursor()

        cursor.execute(
            """
            SELECT description
            FROM grievances
            WHERE grievance_id=?
            """,
            (grievance_id,)
        )

        row = cursor.fetchone()

        if row is None:

            conn.close()

            return {

                "success": False,

                "message": "Grievance not found."

            }

        updated_description = (
            row[0]
            + "\n\nComment:\n"
            + comment
        )

        cursor.execute(
            """
            UPDATE grievances
            SET description=?
            WHERE grievance_id=?
            """,
            (
                updated_description,
                grievance_id
            )
        )

        conn.commit()

        conn.close()

        return {

            "success": True,

            "message": "Comment added."

        }