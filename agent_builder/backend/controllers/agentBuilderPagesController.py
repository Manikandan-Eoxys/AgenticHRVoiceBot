"""
agentBuilderPagesController.py

SPA page routing and static file serving for the Agent Builder frontend.

Responsibilities:
  - Serves the compiled Vite/React SPA (agent_builder/frontend/dist/)
  - Handles client-side routing (returns index.html for all unknown paths so
    React Router can handle navigation)
  - Provides API endpoints for builder page data:
      GET /api/pages/agents       — list configured agents
      GET /api/pages/agent/{id}   — single agent config (Goals, Actions, Flow, Creds)
      POST /api/pages/agent       — create a new agent config
      PUT  /api/pages/agent/{id}  — update agent config
      DELETE /api/pages/agent/{id}— delete agent config
  - All write operations require builder admin auth

Agent configs are stored as JSON files in agent_builder/data/agents/
(or a DB table if you wire up the database later).
"""

import json
import logging
import os
import time
import uuid
from pathlib import Path
from typing import Any, Optional

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import FileResponse, HTMLResponse
from fastapi.staticfiles import StaticFiles

from controllers.agentBuilderLoginController import login_controller

logger = logging.getLogger("agent_builder.pages")

# ---------------------------------------------------------------------------
# Paths
# ---------------------------------------------------------------------------

_BUILDER_ROOT = Path(__file__).resolve().parents[2]  # agent_builder/
_FRONTEND_DIST = _BUILDER_ROOT / "frontend" / "dist"
_DATA_DIR = _BUILDER_ROOT / "data" / "agents"
_DATA_DIR.mkdir(parents=True, exist_ok=True)


# ---------------------------------------------------------------------------
# Agent Config Storage (JSON file-based — swap for DB when ready)
# ---------------------------------------------------------------------------

def _agent_file(agent_id: str) -> Path:
    safe_id = agent_id.replace("/", "").replace("..", "")
    return _DATA_DIR / f"{safe_id}.json"


def _list_agents() -> list[dict]:
    agents = []
    for f in sorted(_DATA_DIR.glob("*.json")):
        try:
            data = json.loads(f.read_text())
            agents.append(data)
        except Exception:
            pass
    return agents


def _get_agent(agent_id: str) -> Optional[dict]:
    fp = _agent_file(agent_id)
    if not fp.exists():
        return None
    try:
        return json.loads(fp.read_text())
    except Exception:
        return None


def _save_agent(agent_id: str, data: dict) -> None:
    _agent_file(agent_id).write_text(json.dumps(data, indent=2))


def _delete_agent(agent_id: str) -> bool:
    fp = _agent_file(agent_id)
    if fp.exists():
        fp.unlink()
        return True
    return False


# ---------------------------------------------------------------------------
# Pydantic-free request models (use plain dicts for portability)
# ---------------------------------------------------------------------------

def _default_agent_schema(name: str, agent_id: str) -> dict:
    """Returns a blank agent config with all required sections."""
    return {
        "id": agent_id,
        "name": name,
        "created_at": time.time(),
        "updated_at": time.time(),
        "goals": [],       # List of agent goals / intents
        "actions": [],     # List of tool actions the agent can perform
        "flow": {},        # Conversation flow definition
        "credentials": {}, # Integration credentials (IFS, SQL, SMTP, etc.)
        "voice": {         # TTS / STT / LLM configuration
            "tts_provider": "cartesia",
            "tts_model": "cartesia/sonic-3",
            "tts_voice": "",
            "stt_language": "en",
            "llm_model": "google/gemma-4-31b-it",
        },
        "telephony": {     # SIP / LiveKit configuration
            "outbound_trunk_id": "",
            "coordinator_phone": "",
            "sip_caller_id": "",
        },
        "status": "draft",  # draft | deployed | archived
    }


# ---------------------------------------------------------------------------
# Router Factory
# ---------------------------------------------------------------------------

class AgentBuilderPagesController:
    """
    Provides routers for SPA serving and agent config CRUD.

    Usage in builder main.py:
        pages = AgentBuilderPagesController()
        app.include_router(pages.create_api_router())
        pages.mount_spa(app)
    """

    def create_api_router(self) -> APIRouter:
        router = APIRouter(prefix="/api/pages", tags=["pages"])

        # ── Agent CRUD (all require auth) ─────────────────────────────────

        @router.get("/agents", dependencies=[Depends(login_controller.require_admin)])
        async def list_agents():
            return {"agents": _list_agents()}

        @router.get("/agents/{agent_id}", dependencies=[Depends(login_controller.require_admin)])
        async def get_agent(agent_id: str):
            data = _get_agent(agent_id)
            if not data:
                raise HTTPException(status_code=404, detail=f"Agent '{agent_id}' not found.")
            return data

        @router.post("/agents", dependencies=[Depends(login_controller.require_admin)])
        async def create_agent(request: Request):
            body: dict[str, Any] = await request.json()
            name = body.get("name", "Unnamed Agent")
            agent_id = body.get("id") or str(uuid.uuid4())[:8]
            if _get_agent(agent_id):
                raise HTTPException(status_code=409, detail=f"Agent '{agent_id}' already exists.")
            schema = _default_agent_schema(name, agent_id)
            schema.update({k: v for k, v in body.items() if k in schema})
            schema["id"] = agent_id
            _save_agent(agent_id, schema)
            logger.info(f"✅ [Pages] Created agent '{agent_id}' name='{name}'.")
            return schema

        @router.put("/agents/{agent_id}", dependencies=[Depends(login_controller.require_admin)])
        async def update_agent(agent_id: str, request: Request):
            existing = _get_agent(agent_id)
            if not existing:
                raise HTTPException(status_code=404, detail=f"Agent '{agent_id}' not found.")
            body: dict[str, Any] = await request.json()
            existing.update(body)
            existing["id"] = agent_id
            existing["updated_at"] = time.time()
            _save_agent(agent_id, existing)
            logger.info(f"✅ [Pages] Updated agent '{agent_id}'.")
            return existing

        @router.delete("/agents/{agent_id}", dependencies=[Depends(login_controller.require_admin)])
        async def delete_agent(agent_id: str):
            if not _delete_agent(agent_id):
                raise HTTPException(status_code=404, detail=f"Agent '{agent_id}' not found.")
            logger.info(f"🗑️ [Pages] Deleted agent '{agent_id}'.")
            return {"detail": f"Agent '{agent_id}' deleted."}

        # ── Schema Contract & Export Endpoints ────────────────────────────

        @router.get("/schema")
        async def get_config_schema():
            schema_fp = _BUILDER_ROOT / "export" / "config_schema.json"
            if not schema_fp.exists():
                raise HTTPException(status_code=404, detail="Config schema contract file not found.")
            return json.loads(schema_fp.read_text(encoding="utf-8"))

        @router.post("/validate", dependencies=[Depends(login_controller.require_admin)])
        async def validate_agent_config(request: Request):
            payload = await request.json()
            schema_fp = _BUILDER_ROOT / "export" / "config_schema.json"
            if not schema_fp.exists():
                return {"valid": True, "message": "Schema file not found; skipping check."}
            try:
                import jsonschema
                schema = json.loads(schema_fp.read_text(encoding="utf-8"))
                jsonschema.validate(instance=payload, schema=schema)
                return {"valid": True, "message": "Configuration matches contract schema."}
            except Exception as e:
                return {"valid": False, "error": str(e)}

        @router.get("/agents/{agent_id}/export", dependencies=[Depends(login_controller.require_admin)])
        async def export_agent(agent_id: str):
            data = _get_agent(agent_id)
            if not data:
                raise HTTPException(status_code=404, detail=f"Agent '{agent_id}' not found.")

            # Normalise to standard contract keys
            export_doc = {
                "schemaVersion": data.get("schemaVersion", "1.0.0"),
                "agentId": data.get("agentId") or data.get("id", agent_id),
                "agentName": data.get("agentName") or data.get("name", "Voice Agent"),
                "description": data.get("description", ""),
                "status": data.get("status", "active"),
                "voice": data.get("voice", {}),
                "telephony": data.get("telephony", {}),
                "memory": data.get("memory", {"conversationWindow": 10, "enableScratchpad": True}),
                "knowledgeBase": data.get("knowledgeBase", {"enabled": False}),
                "goals": data.get("goals", []),
                "actions": data.get("actions", []),
            }

            # Validate against schema
            schema_fp = _BUILDER_ROOT / "export" / "config_schema.json"
            if schema_fp.exists():
                try:
                    import jsonschema
                    schema = json.loads(schema_fp.read_text(encoding="utf-8"))
                    jsonschema.validate(instance=export_doc, schema=schema)
                except Exception as err:
                    raise HTTPException(
                        status_code=422,
                        detail=f"Export validation failed: {err}"
                    )

            return export_doc

        @router.post("/agents/{agent_id}/publish", dependencies=[Depends(login_controller.require_admin)])
        async def publish_agent(agent_id: str):
            export_doc = await export_agent(agent_id)

            # 1. Write to hr_voice_agent/config/agent_config.json
            runtime_agent_config = _BUILDER_ROOT.parent / "hr_voice_agent" / "config" / "agent_config.json"
            if not runtime_agent_config.parent.exists():
                runtime_agent_config = _BUILDER_ROOT.parent / "hr_voice_agent" / "agent_config.json"
            try:
                runtime_agent_config.write_text(json.dumps(export_doc, indent=2), encoding="utf-8")
                logger.info(f"🚀 [Pages] Published config for '{agent_id}' to {runtime_agent_config}")
            except Exception as e:
                logger.error(f"Failed to write agent_config.json: {e}")
                raise HTTPException(status_code=500, detail=f"Failed to write runtime config: {e}")

            # 2. Mirror into MySQL agent_config table if database is available
            db_persisted = False
            try:
                import sys
                hr_path = str(_BUILDER_ROOT.parent / "hr_voice_agent")
                if hr_path not in sys.path:
                    sys.path.insert(0, hr_path)
                from controllers.agentConfigController import AgentConfigController
                from models.config_models import AgentConfig
                cfg_model = AgentConfig.from_dict(export_doc)
                db_persisted = AgentConfigController.instance().save_agent_config_to_db(cfg_model)
            except Exception as dbe:
                logger.warning(f"Could not mirror config to DB: {dbe}")

            return {
                "status": "published",
                "agentId": agent_id,
                "schemaVersion": export_doc.get("schemaVersion"),
                "goalsCount": len(export_doc.get("goals", [])),
                "actionsCount": len(export_doc.get("actions", [])),
                "dbMirrored": db_persisted,
                "fileUpdated": str(runtime_agent_config),
            }

        @router.get("/health")
        async def health():
            return {"status": "ok", "service": "agent-builder"}

        return router

    def mount_spa(self, app) -> None:
        """
        Mounts the compiled React SPA and adds a catch-all route for
        client-side React Router navigation.

        Call after all API routers are registered.
        """
        if not _FRONTEND_DIST.exists():
            logger.warning(
                f"⚠️ [Pages] Frontend dist not found at {_FRONTEND_DIST}. "
                "Run 'npm run build' in agent_builder/frontend/ to build the SPA."
            )
            return

        # Serve /assets/* etc. from the built bundle
        app.mount(
            "/assets",
            StaticFiles(directory=str(_FRONTEND_DIST / "assets")),
            name="spa_assets",
        )

        # Catch-all: serve index.html for every non-API path
        @app.get("/{full_path:path}", include_in_schema=False)
        async def serve_spa(full_path: str):
            index = _FRONTEND_DIST / "index.html"
            if index.exists():
                return FileResponse(str(index))
            return HTMLResponse("<h1>Builder SPA not built yet.</h1>", status_code=503)

        logger.info(f"🌐 [Pages] SPA mounted from {_FRONTEND_DIST}.")


# Module-level singleton
pages_controller = AgentBuilderPagesController()
