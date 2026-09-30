"""
agent_builder/backend/main.py

FastAPI application entry point for the Agent Builder web dashboard.

This is a SEPARATE server from the HR Voice Agent worker (hr_voice_agent/main.py).
It does NOT handle phone calls. It provides:
  - Admin authentication  (POST /api/auth/login)
  - Agent config CRUD     (GET/POST/PUT/DELETE /api/pages/agents)
  - Live monitoring proxy (GET /dashboard/*)
  - React SPA hosting     (GET /*)

Run:
    cd agent_builder/backend
    uvicorn main:app --host 0.0.0.0 --port 8571 --reload

Environment variables (all optional with safe defaults):
  BUILDER_ADMIN_USERNAME   — admin username (default: admin)
  BUILDER_ADMIN_PASSWORD   — admin password (CHANGE IN PRODUCTION)
  BUILDER_JWT_SECRET       — JWT signing secret (CHANGE IN PRODUCTION)
  AGENT_DASHBOARD_URL      — URL of the running agent's dashboard API
  AGENT_DASHBOARD_TOKEN    — shared token to authenticate to agent dashboard
"""

import logging
import os
from pathlib import Path

from dotenv import load_dotenv
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

# Load .env from project root (two levels up from this file)
_env_path = Path(__file__).resolve().parents[2] / ".env"
if _env_path.exists():
    load_dotenv(_env_path)

# ---------------------------------------------------------------------------
# Logging
# ---------------------------------------------------------------------------

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
logger = logging.getLogger("agent_builder")

# ---------------------------------------------------------------------------
# Import controllers and routers (after env is loaded)
# ---------------------------------------------------------------------------

from controllers.agentBuilderLoginController import login_controller
from controllers.agentBuilderDashboardController import dashboard_controller
from controllers.agentBuilderPagesController import pages_controller

try:
    from routers.db_router import router as db_router
    from routers.developer_router import router as developer_router
except ImportError:
    from agent_builder.backend.routers.db_router import router as db_router
    from agent_builder.backend.routers.developer_router import router as developer_router

# ---------------------------------------------------------------------------
# App
# ---------------------------------------------------------------------------

app = FastAPI(
    title="HR Voice Agent Builder",
    description=(
        "Web dashboard for configuring, monitoring, and managing "
        "HR Voice Agent deployments."
    ),
    version="1.0.0",
    docs_url="/api/docs",
    redoc_url="/api/redoc",
)

# CORS — restrict to your actual domain in production
app.add_middleware(
    CORSMiddleware,
    allow_origins=os.environ.get("BUILDER_CORS_ORIGINS", "*").split(","),
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# ---------------------------------------------------------------------------
# Routers
# ---------------------------------------------------------------------------

# Auth: POST /api/auth/login, POST /api/auth/logout, GET /api/auth/me
app.include_router(login_controller.create_auth_router(), prefix="/api/auth")

# Dashboard: GET /dashboard/overview, /dashboard/status, /dashboard/calls, /dashboard/logs
app.include_router(dashboard_controller.create_router())

# Pages CRUD: GET/POST/PUT/DELETE /api/pages/agents
app.include_router(pages_controller.create_api_router())

# DB Management: GET /api/db/tables, /api/db/schema/{table}, POST /api/db/query, GET /api/db/pool
app.include_router(db_router)

# Developer Diagnostics & Profile: GET/POST /api/developer/*
app.include_router(developer_router)

# SPA catch-all (MUST be last)
pages_controller.mount_spa(app)

# ---------------------------------------------------------------------------
# Startup event
# ---------------------------------------------------------------------------

@app.on_event("startup")
async def on_startup():
    logger.info("=" * 60)
    logger.info("🏗️  Agent Builder started.")
    logger.info(f"   Docs      : http://0.0.0.0:{os.environ.get('PORT', '8571')}/api/docs")
    logger.info(f"   Agent API : {os.environ.get('AGENT_DASHBOARD_URL', 'http://localhost:8570/dashboard')}")
    logger.info("=" * 60)


@app.on_event("shutdown")
async def on_shutdown():
    logger.info("🛑 Agent Builder shutting down.")
