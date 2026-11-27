#!/usr/bin/env python3
"""Ask the running HR voicebot (agent.py) to phone someone.

  python scripts/call_out.py +918925355704                         # plain HR assistant call
  python scripts/call_out.py +918925355704 --type leave_verification --employee 1007
  python scripts/call_out.py --employee 1007 --type scheduled_reminder --reminder "Please submit your timesheet."

Reads OUTBOUND_API_KEY / OUTBOUND_API_HOST / OUTBOUND_API_PORT from the project .env (../.env).
"""
import argparse
import json
import os
import sys
import urllib.error
import urllib.request
from pathlib import Path

try:
    from dotenv import load_dotenv
    root = Path(__file__).resolve().parent.parent
    load_dotenv(root / ".env") if (root / ".env").exists() else load_dotenv(root.parent / ".env")
except ImportError:
    pass


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("to", nargs="?", default="", help="number to call (+91..., 91..., 0..., or 10 digits)")
    ap.add_argument("--type", default="assistant",
                    choices=["assistant", "leave_verification", "scheduled_reminder", "policy_acknowledgement"])
    ap.add_argument("--employee", default="", help="employee ID (required for the employee call types)")
    ap.add_argument("--name", default="", help="employee name (skips the database lookup when --to is also given)")
    ap.add_argument("--reminder", default="", help="reason for the call (scheduled_reminder / policy_acknowledgement)")
    a = ap.parse_args()

    key = os.getenv("OUTBOUND_API_KEY", "")
    host = os.getenv("OUTBOUND_API_HOST", "127.0.0.1")
    port = os.getenv("OUTBOUND_API_PORT", "8090")
    if not key:
        print("OUTBOUND_API_KEY is not set in .env", file=sys.stderr)
        return 2
    body = json.dumps({"to": a.to, "callType": a.type, "employeeId": a.employee,
                       "employeeName": a.name, "reminderPrompt": a.reminder}).encode()
    req = urllib.request.Request(f"http://{host}:{port}/api/outbound/call", data=body,
                                 headers={"Content-Type": "application/json", "X-API-Key": key}, method="POST")
    try:
        with urllib.request.urlopen(req, timeout=15) as r:
            print(json.dumps(json.load(r), indent=2))
            return 0
    except urllib.error.HTTPError as e:
        print(f"HTTP {e.code}: {e.read().decode()}", file=sys.stderr)
    except urllib.error.URLError as e:
        print(f"Cannot reach the agent at {host}:{port} - is agent.py running with OUTBOUND_ENABLED=1? ({e.reason})", file=sys.stderr)
    return 1


if __name__ == "__main__":
    sys.exit(main())
