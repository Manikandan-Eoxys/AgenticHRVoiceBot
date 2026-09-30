"""
integrations/ifs_cloud_client.py

IFS Cloud REST/OData API Client
Handles:
  - OAuth2 Client Credentials authentication
  - Posting absence records to IFS Cloud
  - Fetching workforce/schedule data from IFS Cloud
  - Updating engineer schedules in IFS Cloud
  - Token refresh and retry logic

STUB MODE: If IFS_CLOUD_BASE_URL is not configured, all methods
operate in stub mode — returning simulated success responses and
logging the intended operation. This allows the rest of the system
to work end-to-end before IFS Cloud credentials are available.
"""

import logging
import time
from datetime import datetime
from config import Config

logger = logging.getLogger(__name__)

# ─── Config ───────────────────────────────────────────────────────────────────
IFS_BASE_URL    = getattr(Config, "IFS_CLOUD_BASE_URL",    None)
IFS_CLIENT_ID   = getattr(Config, "IFS_CLOUD_CLIENT_ID",   None)
IFS_CLIENT_SECRET = getattr(Config, "IFS_CLOUD_CLIENT_SECRET", None)
IFS_TENANT      = getattr(Config, "IFS_CLOUD_TENANT",      None)
STUB_MODE       = not bool(IFS_BASE_URL and IFS_CLIENT_ID and IFS_CLIENT_SECRET)

# ─── Token Cache ──────────────────────────────────────────────────────────────
_token_cache = {"access_token": None, "expires_at": 0}


def _get_access_token() -> str:
    """Fetch or refresh the OAuth2 bearer token."""
    if STUB_MODE:
        return "stub-token"

    now = time.time()
    if _token_cache["access_token"] and _token_cache["expires_at"] > now + 30:
        return _token_cache["access_token"]

    import requests
    token_url = f"{IFS_BASE_URL}/oauth2/token"
    payload   = {
        "grant_type":    "client_credentials",
        "client_id":     IFS_CLIENT_ID,
        "client_secret": IFS_CLIENT_SECRET,
    }
    if IFS_TENANT:
        payload["scope"] = f"ifs_cloud.{IFS_TENANT}"

    try:
        resp = requests.post(token_url, data=payload, timeout=15)
        resp.raise_for_status()
        data = resp.json()
        _token_cache["access_token"] = data["access_token"]
        _token_cache["expires_at"]   = now + int(data.get("expires_in", 3600))
        logger.info("[IFSClient] Token refreshed, expires in %ds", data.get("expires_in", 3600))
        return _token_cache["access_token"]
    except Exception as exc:
        logger.error("[IFSClient] Token fetch failed: %s", exc)
        raise


def _headers() -> dict:
    return {
        "Authorization": f"Bearer {_get_access_token()}",
        "Content-Type":  "application/json",
        "Accept":        "application/json",
    }


class IFSCloudClient:
    """
    IFS Cloud integration client.
    All methods return a standardised dict: {success, data, error, stub_mode}
    """

    # ──────────────────────────────────────────────────────────────────────────
    # 1. Post Absence Record to IFS Cloud
    # ──────────────────────────────────────────────────────────────────────────
    def post_absence(
        self,
        employee_id: int,
        absence_type: str,
        from_date: str,
        to_date: str,
        reason: str = "",
        request_id: int = None,
    ) -> dict:
        """
        Creates an absence record in IFS Cloud HR module.
        Returns ifs_cloud_ref — the IFS-assigned record ID.
        """
        payload = {
            "EmployeeId":  str(employee_id),
            "AbsenceType": absence_type,
            "StartDate":   from_date,
            "EndDate":     to_date,
            "Reason":      reason,
            "SourceRef":   f"HR-BOT-{request_id}" if request_id else "HR-BOT",
            "CreatedAt":   datetime.utcnow().isoformat() + "Z",
        }

        if STUB_MODE:
            ifs_ref = f"IFS-ABS-STUB-{request_id or '0'}"
            logger.info(
                "[IFSClient STUB] post_absence: employee=%s dates=%s→%s ref=%s",
                employee_id, from_date, to_date, ifs_ref,
            )
            return {
                "success":       True,
                "ifs_cloud_ref": ifs_ref,
                "stub_mode":     True,
                "payload_sent":  payload,
            }

        import requests
        url = f"{IFS_BASE_URL}/api/v1/hr/absences"
        try:
            resp = requests.post(url, json=payload, headers=_headers(), timeout=20)
            resp.raise_for_status()
            data    = resp.json()
            ifs_ref = data.get("AbsenceId") or data.get("id") or f"IFS-{request_id}"
            logger.info("[IFSClient] Absence posted: ref=%s", ifs_ref)
            return {
                "success":       True,
                "ifs_cloud_ref": ifs_ref,
                "stub_mode":     False,
                "response":      data,
            }
        except Exception as exc:
            logger.error("[IFSClient] post_absence failed: %s", exc)
            return {
                "success":   False,
                "error":     str(exc),
                "stub_mode": False,
            }

    # ──────────────────────────────────────────────────────────────────────────
    # 2. Fetch Employee Schedule from IFS Cloud
    # ──────────────────────────────────────────────────────────────────────────
    def get_employee_schedule(self, employee_id: int, from_date: str, to_date: str) -> dict:
        """
        Fetches roster / work schedule for an employee from IFS Cloud.
        """
        if STUB_MODE:
            logger.info(
                "[IFSClient STUB] get_employee_schedule: employee=%s %s→%s",
                employee_id, from_date, to_date,
            )
            return {
                "success":   True,
                "stub_mode": True,
                "schedule":  [
                    {"date": from_date, "shift": "09:00-17:00", "status": "Scheduled"},
                ],
            }

        import requests
        url = (
            f"{IFS_BASE_URL}/api/v1/hr/employees/{employee_id}/schedules"
            f"?startDate={from_date}&endDate={to_date}"
        )
        try:
            resp = requests.get(url, headers=_headers(), timeout=20)
            resp.raise_for_status()
            return {"success": True, "stub_mode": False, "schedule": resp.json()}
        except Exception as exc:
            logger.error("[IFSClient] get_employee_schedule failed: %s", exc)
            return {"success": False, "error": str(exc), "stub_mode": False}

    # ──────────────────────────────────────────────────────────────────────────
    # 3. Update Schedule in IFS Cloud (after reallocation)
    # ──────────────────────────────────────────────────────────────────────────
    def patch_schedule(
        self,
        employee_id: int,
        schedule_date: str,
        status: str,
        notes: str = "",
    ) -> dict:
        """
        PATCHes an existing schedule entry in IFS Cloud.
        """
        payload = {
            "EmployeeId":  str(employee_id),
            "Date":        schedule_date,
            "ShiftStatus": status,
            "Notes":       notes,
        }

        if STUB_MODE:
            logger.info(
                "[IFSClient STUB] patch_schedule: employee=%s date=%s status=%s",
                employee_id, schedule_date, status,
            )
            return {"success": True, "stub_mode": True, "payload_sent": payload}

        import requests
        url = f"{IFS_BASE_URL}/api/v1/hr/employees/{employee_id}/schedules/{schedule_date}"
        try:
            resp = requests.patch(url, json=payload, headers=_headers(), timeout=20)
            resp.raise_for_status()
            return {"success": True, "stub_mode": False, "response": resp.json()}
        except Exception as exc:
            logger.error("[IFSClient] patch_schedule failed: %s", exc)
            return {"success": False, "error": str(exc), "stub_mode": False}

    # ──────────────────────────────────────────────────────────────────────────
    # 4. Fetch Workforce Allocation from IFS Cloud
    # ──────────────────────────────────────────────────────────────────────────
    def get_workforce_allocation(self, team_id: int, target_date: str) -> dict:
        """
        Fetches workforce / engineer allocation for a team from IFS Cloud.
        """
        if STUB_MODE:
            logger.info(
                "[IFSClient STUB] get_workforce_allocation: team=%s date=%s",
                team_id, target_date,
            )
            return {
                "success":    True,
                "stub_mode":  True,
                "team_id":    team_id,
                "target_date":target_date,
                "allocation": [],
            }

        import requests
        url = f"{IFS_BASE_URL}/api/v1/operations/teams/{team_id}/allocation?date={target_date}"
        try:
            resp = requests.get(url, headers=_headers(), timeout=20)
            resp.raise_for_status()
            return {"success": True, "stub_mode": False, "allocation": resp.json()}
        except Exception as exc:
            logger.error("[IFSClient] get_workforce_allocation failed: %s", exc)
            return {"success": False, "error": str(exc), "stub_mode": False}


# Module-level singleton
ifs_client = IFSCloudClient()
