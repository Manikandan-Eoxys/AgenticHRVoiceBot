"""
controllers/rag_router.py

FastAPI router — RAG Configuration Management API
===================================================
Prefix: /rag
Tags:   ["RAG"]

Mounted in token_server.py the same way monitor_router is:

    from controllers.rag_router import router as rag_router
    app.include_router(rag_router)

Authentication
--------------
This project has no HTTP authentication layer (the existing /monitor and /getToken
endpoints are equally open). This router follows the same pattern. If you add an
auth middleware or API-key dependency to token_server.py in the future, all routers
will inherit it automatically.

Endpoints
---------
PUT  /rag/config/{agent_id}      — Save/update knowledgeBase config for an agent.
GET  /rag/config/{agent_id}      — Retrieve saved config; authValue is always masked.
POST /rag/test-connection         — Fire a live search and return diagnostics.

Secret handling
---------------
externalRag.authValue is encrypted with Fernet (env RAG_SECRET_KEY, a URL-safe
base-64-encoded 32-byte key). If RAG_SECRET_KEY is absent and the body contains a
non-empty authValue the endpoint returns HTTP 422 so no plaintext secret leaks into
the database. The masked placeholder "********" is never re-saved; the stored
ciphertext is kept as-is.

Never log secrets, raw auth values, or full snippet text.
"""

import json
import logging
import os
import time
from typing import Any, Optional

from fastapi import APIRouter, HTTPException
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field

from services.rag_retriever_service import (
    HttpRetriever,
    RagConfig,
    validate_rag_url,
)

logger = logging.getLogger("rag-router")

router = APIRouter(prefix="/rag", tags=["RAG"])

# ---------------------------------------------------------------------------
# Sentinel value for "keep the existing secret"
# ---------------------------------------------------------------------------
_MASKED = "********"


# ---------------------------------------------------------------------------
# Fernet helpers
# ---------------------------------------------------------------------------

def _get_fernet():
    """Return a Fernet instance or raise HTTPException if the key is missing/invalid."""
    try:
        from cryptography.fernet import Fernet  # noqa: F401
    except ImportError:
        raise HTTPException(
            status_code=500,
            detail="cryptography package is not installed. Run: pip install cryptography",
        )

    raw_key = os.environ.get("RAG_SECRET_KEY") or ""
    try:
        from config import RAG_SECRET_KEY as _cfg_key
        raw_key = raw_key or (_cfg_key or "")
    except Exception:
        pass

    if not raw_key:
        return None  # caller decides whether to raise

    try:
        from cryptography.fernet import Fernet
        return Fernet(raw_key.encode() if isinstance(raw_key, str) else raw_key)
    except Exception:
        raise HTTPException(
            status_code=500,
            detail=(
                "RAG_SECRET_KEY is set but is not a valid Fernet key. "
                "Generate one with: python -c \"from cryptography.fernet import Fernet; "
                "print(Fernet.generate_key().decode())\""
            ),
        )


def _encrypt(value: str) -> str:
    """Encrypt authValue. Raises HTTPException if key is absent."""
    f = _get_fernet()
    if f is None:
        raise HTTPException(
            status_code=422,
            detail=(
                "RAG_SECRET_KEY env var is not set. Cannot store secrets securely. "
                "Either set RAG_SECRET_KEY or omit authValue from the config."
            ),
        )
    return f.encrypt(value.encode()).decode()


def _decrypt(ciphertext: str) -> str:
    """Decrypt authValue. Returns '' if key is absent or decryption fails."""
    f = _get_fernet()
    if f is None:
        logger.warning("RAG_SECRET_KEY not set; cannot decrypt stored authValue")
        return ""
    try:
        return f.decrypt(ciphertext.encode()).decode()
    except Exception:
        logger.warning("Failed to decrypt stored RAG authValue (wrong key or corrupted value)")
        return ""


# ---------------------------------------------------------------------------
# DB helpers — same style as agentConfigController.save_agent_config_to_db
# ---------------------------------------------------------------------------

def _ensure_table(db) -> None:
    """CREATE TABLE IF NOT EXISTS agent_rag_config (idempotent)."""
    db.cursor.execute(
        """
        CREATE TABLE IF NOT EXISTS agent_rag_config (
            config_id   INT AUTO_INCREMENT PRIMARY KEY,
            agent_id    VARCHAR(64) NOT NULL UNIQUE,
            config_json LONGTEXT NOT NULL,
            created_at  TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            updated_at  TIMESTAMP DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
            KEY idx_agent_id (agent_id)
        )
        """
    )


def _db_save(agent_id: str, config_dict: dict) -> None:
    """Upsert the RAG config for agent_id."""
    from database.db import get_db
    with get_db() as db:
        _ensure_table(db)
        db.cursor.execute(
            """
            INSERT INTO agent_rag_config (agent_id, config_json)
            VALUES (%s, %s)
            ON DUPLICATE KEY UPDATE config_json = VALUES(config_json), updated_at = NOW()
            """,
            (agent_id, json.dumps(config_dict)),
        )
        db.conn.commit()


def _db_load(agent_id: str) -> Optional[dict]:
    """Return stored config dict or None."""
    from database.db import get_db
    with get_db() as db:
        _ensure_table(db)
        db.cursor.execute(
            "SELECT config_json FROM agent_rag_config WHERE agent_id = %s",
            (agent_id,),
        )
        row = db.cursor.fetchone()
        if not row:
            return None
        raw = row["config_json"] if isinstance(row, dict) else row[0]
        if not raw:
            return None
        parsed = json.loads(raw) if isinstance(raw, str) else raw
        return parsed if isinstance(parsed, dict) else None


# ---------------------------------------------------------------------------
# Masking helper
# ---------------------------------------------------------------------------

def _mask_config(config_dict: dict) -> dict:
    """Return a deep copy with authValue replaced by '********'."""
    import copy
    masked = copy.deepcopy(config_dict)
    ext = masked.get("externalRag")
    if isinstance(ext, dict) and ext.get("authValue"):
        ext["authValue"] = _MASKED
    return masked


# ---------------------------------------------------------------------------
# Pydantic request/response models
# ---------------------------------------------------------------------------

class ExternalRagBody(BaseModel):
    url: str = Field("", description="HTTPS retrieval endpoint URL")
    method: str = Field("POST", description="HTTP method: POST or GET")
    authType: str = Field("none", description="Auth type: none, bearer, header")
    authValue: str = Field("", description="Secret token. Send '********' to keep existing value.")
    authName: str = Field("", description="Header name for auth_type='header'")
    queryKey: str = Field("query", description="JSON/query-param key for the search term")
    responsePath: str = Field("results[].content", description="Dot-path to extract snippets from response")


class KnowledgeBaseBody(BaseModel):
    ragEnabled: bool = Field(False)
    groundingMode: str = Field("balanced")
    sourceMode: str = Field("external")
    topK: int = Field(3, ge=1, le=20)
    externalRag: Optional[ExternalRagBody] = None


class TestConnectionBody(BaseModel):
    agent_id: Optional[str] = Field(None, description="Look up stored config for this agent")
    externalRag: Optional[ExternalRagBody] = Field(None, description="Inline config (overrides stored)")
    sample_query: str = Field(..., min_length=1, description="Query to send to the retrieval endpoint")


# ---------------------------------------------------------------------------
# PUT /rag/config/{agent_id}
# ---------------------------------------------------------------------------

@router.put("/config/{agent_id}")
async def put_rag_config(agent_id: str, body: KnowledgeBaseBody) -> JSONResponse:
    """
    Save/update the knowledgeBase configuration for an agent.

    - Validates the config (RagConfig rules + SSRF URL check).
    - Encrypts externalRag.authValue with Fernet (requires env RAG_SECRET_KEY).
    - Sending authValue='********' keeps the previously stored secret unchanged.
    - Returns HTTP 422 if authValue is non-empty and RAG_SECRET_KEY is not set.
    """
    agent_id = agent_id.strip()
    if not agent_id:
        raise HTTPException(status_code=400, detail="agent_id must not be empty")

    config_dict: dict[str, Any] = body.model_dump()
    ext = config_dict.get("externalRag") or {}

    # --- SSRF check on URL (if external mode and URL present) ---
    source_mode = (config_dict.get("sourceMode") or "external").lower()
    raw_url = (ext.get("url") or "").strip()
    if source_mode == "external" and body.ragEnabled and raw_url:
        try:
            validate_rag_url(raw_url)
        except ValueError as exc:
            raise HTTPException(status_code=422, detail=f"URL validation failed: {exc}")

    # Validate via RagConfig (tolerant parser; ensures no crash on bad data)
    RagConfig.from_knowledge_base(config_dict)

    # --- Handle authValue: encrypt or keep existing ---
    auth_val = (ext.get("authValue") or "").strip()
    auth_type = (ext.get("authType") or "none").lower()

    if auth_val == _MASKED:
        # Client says "keep whatever is stored" — load existing encrypted value
        try:
            existing = _db_load(agent_id)
        except Exception:
            existing = None
        stored_encrypted = ""
        if existing:
            stored_encrypted = (existing.get("externalRag") or {}).get("authValue", "")
        if config_dict.get("externalRag") is None:
            config_dict["externalRag"] = {}
        config_dict["externalRag"]["authValue"] = stored_encrypted

    elif auth_val and auth_type in ("bearer", "header"):
        # Encrypt the new secret
        encrypted = _encrypt(auth_val)
        config_dict["externalRag"]["authValue"] = encrypted

    else:
        # No secret / auth_type=none — clear authValue
        if config_dict.get("externalRag") is not None:
            config_dict["externalRag"]["authValue"] = ""

    # --- Persist ---
    try:
        _db_save(agent_id, config_dict)
    except Exception as exc:
        logger.error(
            "Failed to save RAG config for agent '%s': %s", agent_id, type(exc).__name__
        )
        raise HTTPException(status_code=500, detail="Failed to save configuration to database")

    logger.info("RAG config saved for agent '%s'", agent_id)
    return JSONResponse(
        status_code=200,
        content={"ok": True, "agent_id": agent_id, "message": "RAG configuration saved"},
    )


# ---------------------------------------------------------------------------
# GET /rag/config/{agent_id}
# ---------------------------------------------------------------------------

@router.get("/config/{agent_id}")
async def get_rag_config(agent_id: str) -> JSONResponse:
    """
    Retrieve the saved knowledgeBase configuration for an agent.
    authValue is always replaced with '********' in the response.
    Returns 404 if no config has been saved yet.
    """
    agent_id = agent_id.strip()
    if not agent_id:
        raise HTTPException(status_code=400, detail="agent_id must not be empty")

    try:
        config = _db_load(agent_id)
    except Exception as exc:
        logger.error(
            "Failed to load RAG config for agent '%s': %s", agent_id, type(exc).__name__
        )
        raise HTTPException(status_code=500, detail="Failed to read configuration from database")

    if config is None:
        raise HTTPException(
            status_code=404, detail=f"No RAG configuration found for agent '{agent_id}'"
        )

    return JSONResponse(
        content={"ok": True, "agent_id": agent_id, "knowledgeBase": _mask_config(config)}
    )


# ---------------------------------------------------------------------------
# POST /rag/test-connection
# ---------------------------------------------------------------------------

@router.post("/test-connection")
async def test_connection(body: TestConnectionBody) -> JSONResponse:
    """
    Fire one live retrieval search and return diagnostics.

    Resolution order for the config:
    1. If agent_id is given, load stored config (decrypts authValue server-side).
    2. If inline externalRag is provided, it overrides stored values field-by-field.
       If inline authValue is '********' or empty, the stored secret is used.
    3. Applies validate_rag_url (SSRF protection) before any network call.

    Response: {ok, status, latency_ms, snippet_count, sample, error}
    ok is True only if: HTTP 2xx + valid JSON + at least one snippet extracted.
    """
    # --- Build base kb dict ---
    kb: dict[str, Any] = {
        "ragEnabled": True,
        "groundingMode": "balanced",
        "sourceMode": "external",
        "topK": 3,
        "externalRag": {
            "url": "",
            "method": "POST",
            "authType": "none",
            "authValue": "",
            "authName": "",
            "queryKey": "query",
            "responsePath": "results[].content",
        },
    }

    stored_auth_encrypted = ""

    # Step 1: Load from DB if agent_id provided
    if body.agent_id:
        try:
            stored = _db_load(body.agent_id.strip())
        except Exception:
            stored = None

        if stored:
            kb.update(stored)
            # Stash the encrypted secret before any override
            stored_auth_encrypted = (stored.get("externalRag") or {}).get("authValue", "")

    # Step 2: Apply inline overrides (field-by-field)
    if body.externalRag:
        inline = body.externalRag.model_dump(exclude_unset=True)
        if not isinstance(kb.get("externalRag"), dict):
            kb["externalRag"] = {}

        for field in ("url", "method", "authType", "authName", "queryKey", "responsePath"):
            val = inline.get(field)
            if val is not None:
                kb["externalRag"][field] = val

        # authValue resolution
        inline_auth = (inline.get("authValue") or "").strip()
        if inline_auth and inline_auth != _MASKED:
            # Caller gave a real secret inline — use it directly (test-only path)
            kb["externalRag"]["authValue"] = inline_auth
            stored_auth_encrypted = ""  # don't fall back to stored
        else:
            # Keep stored secret (will decrypt below)
            kb["externalRag"]["authValue"] = stored_auth_encrypted

    # Step 3: Decrypt the stored secret if needed
    ext = kb.get("externalRag") or {}
    auth_val_field = (ext.get("authValue") or "").strip()
    auth_type = (ext.get("authType") or "none").lower()

    if auth_type in ("bearer", "header") and auth_val_field and auth_val_field != _MASKED:
        decrypted = _decrypt(auth_val_field)
        if decrypted:
            kb["externalRag"]["authValue"] = decrypted

    # Step 4: SSRF + config validation
    url = (ext.get("url") or "").strip()
    if not url:
        raise HTTPException(status_code=422, detail="No URL configured for this agent")

    try:
        validate_rag_url(url)
    except ValueError as exc:
        return JSONResponse(
            content={
                "ok": False,
                "status": None,
                "latency_ms": None,
                "snippet_count": 0,
                "sample": None,
                "error": f"URL validation failed: {exc}",
            }
        )

    # Step 5: Build retriever, run one search, close
    retriever = None
    try:
        config = RagConfig.from_knowledge_base(kb)
        from config import RAG_TIMEOUT
        retriever = HttpRetriever(config=config, timeout=RAG_TIMEOUT)
        start = time.monotonic()
        snippets = await retriever.search(body.sample_query, k=config.top_k)
        latency_ms = round((time.monotonic() - start) * 1000, 1)
        ok = len(snippets) > 0
        sample = snippets[0][:120] if snippets else None
        return JSONResponse(
            content={
                "ok": ok,
                "status": 200,
                "latency_ms": latency_ms,
                "snippet_count": len(snippets),
                "sample": sample,
                "error": None,
            }
        )
    except Exception as exc:
        err_type = type(exc).__name__
        status_code = None
        err_msg = err_type
        if hasattr(exc, "status"):
            status_code = exc.status
            err_msg = f"{err_type} (HTTP {status_code})"
        logger.warning("RAG test-connection failed: %s", err_type)
        return JSONResponse(
            content={
                "ok": False,
                "status": status_code,
                "latency_ms": None,
                "snippet_count": 0,
                "sample": None,
                "error": err_msg,
            }
        )
    finally:
        if retriever is not None:
            try:
                await retriever.aclose()
            except Exception:
                pass
