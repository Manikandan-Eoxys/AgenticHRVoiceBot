"""
agent_builder/backend/routers/auth_router.py

Authentication Routes
======================
FastAPI router for admin authentication endpoints.
  POST /api/auth/login   - Authenticate admin and receive JWT
  GET  /api/auth/verify  - Verify an existing JWT token
"""

from fastapi import APIRouter, HTTPException, Depends
from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials
from pydantic import BaseModel

from agent_builder.backend.controllers.agentBuilderLoginController import (
    login,
    verify_token,
)

router = APIRouter(prefix="/api/auth", tags=["Authentication"])
_bearer = HTTPBearer()


class LoginRequest(BaseModel):
    username: str
    password: str


@router.post("/login")
async def login_endpoint(body: LoginRequest):
    """Authenticate admin credentials and return a signed JWT token."""
    result = await login(body.username, body.password)
    if not result.get("success"):
        raise HTTPException(status_code=401, detail=result.get("message", "Unauthorized"))
    return result


@router.get("/verify")
async def verify_endpoint(credentials: HTTPAuthorizationCredentials = Depends(_bearer)):
    """Verify validity of a JWT bearer token."""
    result = await verify_token(credentials.credentials)
    if not result.get("valid"):
        raise HTTPException(status_code=401, detail="Token invalid or expired")
    return result
