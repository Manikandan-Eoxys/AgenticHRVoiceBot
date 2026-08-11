"""
policy_tools.py

Policy Tool

Reads HR policies from SQLite database `policies` table with fallback to JSON.

Used by LiveKit Agent / GPT for answering policy-related questions.
"""

import json
import os
import sqlite3
from config import Config


class PolicyTools:

    def __init__(self):
        self.db = Config.DATABASE_PATH
        self.policy_file = os.path.join("data", "leave_policy.json")

    def _connect(self):
        return sqlite3.connect(self.db)

    # ----------------------------------------
    # Get Policy by Name or Key
    # ----------------------------------------
    def get_policy(self, policy_name):
        conn = self._connect()
        cursor = conn.cursor()

        # Try searching by exact name or case-insensitive match
        cursor.execute("""
            SELECT policy_id, policy_name, category, description
            FROM policies
            WHERE LOWER(policy_name) = LOWER(?)
               OR LOWER(policy_name) LIKE LOWER(?)
        """, (policy_name, f"%{policy_name}%"))

        row = cursor.fetchone()
        conn.close()

        if row:
            return {
                "success": True,
                "policy": {
                    "policy_id": row[0],
                    "policy_name": row[1],
                    "title": row[1],
                    "category": row[2],
                    "description": row[3]
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
        cursor = conn.cursor()

        cursor.execute("""
            SELECT policy_id, policy_name, category
            FROM policies
            ORDER BY policy_id
        """)
        rows = cursor.fetchall()
        conn.close()

        result = []
        if rows:
            for row in rows:
                result.append({
                    "policy_id": row[0],
                    "key": row[1].lower().replace(" ", "_"),
                    "title": row[1],
                    "category": row[2]
                })
        else:
            # Fallback to JSON
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
        cursor = conn.cursor()

        pattern = f"%{keyword.lower()}%"
        cursor.execute("""
            SELECT policy_id, policy_name, category, description
            FROM policies
            WHERE LOWER(policy_name) LIKE ?
               OR LOWER(category) LIKE ?
               OR LOWER(description) LIKE ?
            ORDER BY policy_id
        """, (pattern, pattern, pattern))

        rows = cursor.fetchall()
        conn.close()

        matches = []
        if rows:
            for row in rows:
                matches.append({
                    "policy_id": row[0],
                    "key": row[1].lower().replace(" ", "_"),
                    "title": row[1],
                    "category": row[2],
                    "description": row[3]
                })

        # Also search JSON fallback
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