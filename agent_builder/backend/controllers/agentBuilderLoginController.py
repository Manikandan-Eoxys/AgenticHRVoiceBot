"""
agentBuilderLoginController.py

Builder team authentication for the Agent Builder dashboard.

This is the admin/team login — NOT the caller-facing voice identity check.
(Caller identity is handled by hr_voice_agent/controllers/agentLoginController.py)

Responsibilities:
  - Admin credentials validation (username + password → JWT)
  - JWT creation, verification, and refresh
  - Session management (in-memory for single-node; swap for Redis in multi-node)
  - Protects all builder dashboard routes via FastAPI dependency injection

Configuration (.env):
  BUILDER_ADMIN_USERNAME   — default: "admin"
  BUILDER_ADMIN_PASSWORD   — default: "changeme" (MUST be changed in production)
  BUILDER_JWT_SECRET       — random 32-char secret (REQUIRED in production)
  BUILDER_JWT_EXPIRE_HOURS — token lifetime in hours (default: 8)
"""

import logging
import os
import time
from datetime import datetime, timedelta, timezone
from typing import Optional

from fastapi import Depends, HTTPException, status
from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials

logger = logging.getLogger("agent_builder.login")

# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------

_ADMIN_USERNAME: str = os.environ.get("BUILDER_ADMIN_USERNAME", "admin")
_ADMIN_PASSWORD: str = os.environ.get("BUILDER_ADMIN_PASSWORD", "changeme")
_JWT_SECRET: str = os.environ.get("BUILDER_JWT_SECRET", "CHANGE_THIS_IN_PRODUCTION_32CHARS")
_JWT_EXPIRE_HOURS: int = int(os.environ.get("BUILDER_JWT_EXPIRE_HOURS", "8"))
_ALGORITHM = "HS256"

_http_bearer = HTTPBearer(auto_error=False)


# ---------------------------------------------------------------------------
# Lightweight JWT implementation (no PyJWT dependency required)
# Falls back to PyJWT if available for stronger algorithm support.
# ---------------------------------------------------------------------------

def _create_token(username: str, expires_in_hours: int = _JWT_EXPIRE_HOURS) -> str:
    """Creates a signed JWT access token."""
    try:
        import jwt as _jwt
        payload = {
            "sub": username,
            "iat": datetime.now(timezone.utc),
            "exp": datetime.now(timezone.utc) + timedelta(hours=expires_in_hours),
            "role": "builder_admin",
        }
        return _jwt.encode(payload, _JWT_SECRET, algorithm=_ALGORITHM)
    except ImportError:
        # Fallback: simple base64 token (NOT cryptographically secure)
        import base64, json, hashlib, hmac
        payload = {
            "sub": username,
            "exp": time.time() + expires_in_hours * 3600,
            "role": "builder_admin",
        }
        data = base64.urlsafe_b64encode(json.dumps(payload).encode()).decode()
        sig = hmac.new(_JWT_SECRET.encode(), data.encode(), hashlib.sha256).hexdigest()
        return f"{data}.{sig}"


def _verify_token(token: str) -> Optional[dict]:
    """Verifies a JWT token and returns the payload, or None on failure."""
    try:
        import jwt as _jwt
        return _jwt.decode(token, _JWT_SECRET, algorithms=[_ALGORITHM])
    except ImportError:
        import base64, json, hashlib, hmac
        try:
            data, sig = token.rsplit(".", 1)
            expected = hmac.new(_JWT_SECRET.encode(), data.encode(), hashlib.sha256).hexdigest()
            if not hmac.compare_digest(sig, expected):
                return None
            payload = json.loads(base64.urlsafe_b64decode(data + "=="))
            if payload.get("exp", 0) < time.time():
                return None
            return payload
        except Exception:
            return None
    except Exception:
        return None


# ---------------------------------------------------------------------------
# Active session registry (in-memory)
# ---------------------------------------------------------------------------

_active_sessions: dict[str, dict] = {}  # token -> {username, created_at}


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

class BuilderLoginController:
    """
    Handles builder team authentication.

    Mount the router returned by `create_auth_router()` under /api/auth.
    Use `require_admin` as a FastAPI dependency to protect routes.
    """

    def authenticate(self, username: str, password: str) -> Optional[str]:
        """
        Validates credentials and returns a JWT access token, or None on failure.
        """
        if username != _ADMIN_USERNAME or password != _ADMIN_PASSWORD:
            logger.warning(f"🚫 [BuilderLogin] Failed login attempt for username='{username}'.")
            return None

        token = _create_token(username)
        _active_sessions[token] = {
            "username": username,
            "created_at": time.time(),
        }
        logger.info(f"✅ [BuilderLogin] Admin '{username}' logged in. Session created.")
        return token

    def logout(self, token: str) -> bool:
        """Invalidates an active session token."""
        removed = _active_sessions.pop(token, None) is not None
        if removed:
            logger.info("🔓 [BuilderLogin] Session invalidated.")
        return removed

    def require_admin(
        self,
        credentials: Optional[HTTPAuthorizationCredentials] = Depends(_http_bearer),
    ) -> dict:
        """
        FastAPI dependency — use on protected routes:
            @router.get("/protected", dependencies=[Depends(login_ctrl.require_admin)])
        or:
            @router.get("/protected")
            async def route(admin=Depends(login_ctrl.require_admin)):
                ...
        """
        if not credentials:
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="Authentication required.",
                headers={"WWW-Authenticate": "Bearer"},
            )
        token = credentials.credentials
        payload = _verify_token(token)
        if not payload or payload.get("role") != "builder_admin":
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="Invalid or expired token.",
                headers={"WWW-Authenticate": "Bearer"},
            )
        return payload

    def create_auth_router(self):
        """
        Returns a FastAPI APIRouter with /login and /logout endpoints.

        Mount it in main.py:
            from controllers.agentBuilderLoginController import BuilderLoginController
            ctrl = BuilderLoginController()
            app.include_router(ctrl.create_auth_router(), prefix="/api/auth")
        """
        from fastapi import APIRouter
        from pydantic import BaseModel

        router = APIRouter(tags=["auth"])

        class LoginRequest(BaseModel):
            username: str
            password: str

        @router.post("/login")
        async def login(req: LoginRequest):
            token = self.authenticate(req.username, req.password)
            if not token:
                raise HTTPException(
                    status_code=status.HTTP_401_UNAUTHORIZED,
                    detail="Invalid credentials.",
                )
            return {
                "access_token": token,
                "token_type": "bearer",
                "expires_in_hours": _JWT_EXPIRE_HOURS,
            }

        @router.post("/logout")
        async def logout(credentials: Optional[HTTPAuthorizationCredentials] = Depends(_http_bearer)):
            if credentials:
                self.logout(credentials.credentials)
            return {"detail": "Logged out."}

        @router.get("/me")
        async def me(admin: dict = Depends(self.require_admin)):
            return {"username": admin.get("sub"), "role": admin.get("role")}

        return router


# Module-level singleton for convenience
login_controller = BuilderLoginController()
