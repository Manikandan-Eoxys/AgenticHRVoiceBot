"""
policy_tools.py

Policy Tool using MySQL.

Reads HR policies from `leave_policy` table with fallback to JSON.
"""

import json
import os
from database.db import get_db_connection


class PolicyTools:

    def __init__(self):
        self.policy_file = os.path.join("data", "leave_policy.json")

    def _connect(self):
        return get_db_connection()

    # ----------------------------------------
    # Get Policy by Name or Key
    # ----------------------------------------
    def get_policy(self, policy_name):
        conn = self._connect()
        cursor = conn.cursor(dictionary=True)

        pattern = f"%{policy_name.lower()}%"
        cursor.execute("""
            SELECT leave_code, leave_name, annual_range, short_note, description, applies_to
            FROM leave_policy
            WHERE LOWER(leave_name) LIKE %s
               OR LOWER(leave_code) LIKE %s
               OR LOWER(description) LIKE %s
        """, (pattern, pattern, pattern))

        row = cursor.fetchone()
        cursor.close()
        conn.close()

        if row:
            return {
                "success": True,
                "policy": {
                    "policy_id": row["leave_code"],
                    "policy_name": row["leave_name"],
                    "title": row["leave_name"],
                    "category": row["applies_to"],
                    "description": row["description"],
                    "annual_range": row["annual_range"],
                    "short_note": row["short_note"]
                }
            }

        # Fallback to JSON file if DB has no match
        policies_json = self._load_json()
        key = policy_name.lower().replace(" ", "_")
        if key in policies_json:
            val = policies_json[key]
            return {
                "success": True,
                "policy": {
                    "policy_name": val.get("title", policy_name),
                    "title": val.get("title", policy_name),
                    "category": "General",
                    "description": val.get("description", "")
                }
            }

        return {
            "success": False,
            "message": "Policy not found."
        }

    # ----------------------------------------
    # List All Policies
    # ----------------------------------------
    def list_policies(self):
        conn = self._connect()
        cursor = conn.cursor(dictionary=True)

        cursor.execute("""
            SELECT leave_code, leave_name, applies_to, description
            FROM leave_policy
            ORDER BY leave_code
        """)
        rows = cursor.fetchall()
        cursor.close()
        conn.close()

        result = []
        if rows:
            for row in rows:
                result.append({
                    "policy_id": row["leave_code"],
                    "key": row["leave_code"].lower(),
                    "title": row["leave_name"],
                    "category": row["applies_to"],
                    "description": row["description"]
                })
        else:
            policies_json = self._load_json()
            for key, value in policies_json.items():
                result.append({
                    "key": key,
                    "title": value["title"],
                    "category": "General"
                })

        return {
            "success": True,
            "policies": result
        }

    # ----------------------------------------
    # Keyword Search Policies
    # ----------------------------------------
    def search_policy(self, keyword):
        conn = self._connect()
        cursor = conn.cursor(dictionary=True)

        pattern = f"%{keyword.lower()}%"
        cursor.execute("""
            SELECT leave_code, leave_name, applies_to, description
            FROM leave_policy
            WHERE LOWER(leave_name) LIKE %s
               OR LOWER(leave_code) LIKE %s
               OR LOWER(description) LIKE %s
            ORDER BY leave_code
        """, (pattern, pattern, pattern))

        rows = cursor.fetchall()
        cursor.close()
        conn.close()

        matches = []
        if rows:
            for row in rows:
                matches.append({
                    "policy_id": row["leave_code"],
                    "key": row["leave_code"].lower(),
                    "title": row["leave_name"],
                    "category": row["applies_to"],
                    "description": row["description"]
                })

        if not matches:
            policies_json = self._load_json()
            kw = keyword.lower()
            for key, value in policies_json.items():
                text = (value["title"] + " " + value["description"]).lower()
                if kw in text:
                    matches.append({
                        "key": key,
                        "title": value["title"],
                        "category": "General",
                        "description": value["description"]
                    })

        if not matches:
            return {
                "success": False,
                "message": "No matching policy."
            }

        return {
            "success": True,
            "results": matches,
            "policies": matches
        }

    # ----------------------------------------
    # Load JSON Fallback
    # ----------------------------------------
    def _load_json(self):
        if not os.path.exists(self.policy_file):
            return {}
        with open(self.policy_file, "r", encoding="utf-8") as f:
            return json.load(f)

    # ----------------------------------------
    # Reload
    # ----------------------------------------
    def reload(self):
        return {
            "success": True,
            "message": "Policies database reloaded."
        }