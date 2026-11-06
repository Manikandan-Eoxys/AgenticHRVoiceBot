"""
tests/test_rag_router.py

API tests for controllers/rag_router.py using FastAPI TestClient.

Design principles:
- A fake in-memory DB replaces _db_save / _db_load entirely.
- The Fernet key is injected via monkeypatch — no real secrets.
- HttpRetriever.search is stubbed so no network calls are made.
- validate_rag_url is stubbed to always pass (SSRF unit tests live elsewhere).
- Never assert on secrets, auth tokens, or snippet text beyond length checks.
"""

import json
import os
import sys
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

# Ensure hr_voice_agent/ is on the path (the project's own conftest would do
# this, but we add it explicitly so the file works from any cwd).
_HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _HERE not in sys.path:
    sys.path.insert(0, _HERE)

# ---------------------------------------------------------------------------
# Minimal Fernet key for tests (generated offline; safe to commit to tests)
# ---------------------------------------------------------------------------
_TEST_FERNET_KEY = "D3nGn7oIbKEk1qT7A7JXR9mW2Dsy0hCwdFiVi8mXxaQ="


# ---------------------------------------------------------------------------
# Patch env BEFORE importing the router so config.py picks up the key
# ---------------------------------------------------------------------------
os.environ.setdefault("RAG_SECRET_KEY", _TEST_FERNET_KEY)
# Silence the config.py PROMPT_FILE read that happens on import
os.environ.setdefault("LIVEKIT_API_KEY", "test")
os.environ.setdefault("LIVEKIT_API_SECRET", "test")
os.environ.setdefault("LIVEKIT_URL", "wss://test")

# Import the router module once; patch targets are its globals
import controllers.rag_router as rag_mod  # noqa: E402


# ---------------------------------------------------------------------------
# In-memory fake DB store shared across all tests
# ---------------------------------------------------------------------------
_store: dict[str, str] = {}


def _fake_db_save(agent_id: str, cfg: dict) -> None:
    _store[agent_id] = json.dumps(cfg)


def _fake_db_load(agent_id: str):
    raw = _store.get(agent_id)
    return json.loads(raw) if raw else None


# ---------------------------------------------------------------------------
# Per-test fixtures
# ---------------------------------------------------------------------------

@pytest.fixture(autouse=True)
def _clear_store():
    _store.clear()
    yield
    _store.clear()


@pytest.fixture(autouse=True)
def _patch_env(monkeypatch):
    monkeypatch.setenv("RAG_SECRET_KEY", _TEST_FERNET_KEY)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _app():
    app = FastAPI()
    app.include_router(rag_mod.router)
    return app


def _client():
    return TestClient(_app())


def _valid_kb(auth_value: str = "", auth_type: str = "none") -> dict:
    return {
        "ragEnabled": True,
        "groundingMode": "balanced",
        "sourceMode": "external",
        "topK": 3,
        "externalRag": {
            "url": "https://example.com/rag",
            "method": "POST",
            "authType": auth_type,
            "authValue": auth_value,
            "authName": "",
            "queryKey": "query",
            "responsePath": "results[].content",
        },
    }


# ---------------------------------------------------------------------------
# PUT /rag/config/{agent_id}
# ---------------------------------------------------------------------------

class TestPutRagConfig:

    def test_save_no_secret(self):
        """Save a config without a secret — should succeed."""
        with (
            patch.object(rag_mod, "_db_save", side_effect=_fake_db_save),
            patch.object(rag_mod, "_db_load", side_effect=_fake_db_load),
            patch.object(rag_mod, "validate_rag_url", return_value=None),
        ):
            resp = _client().put("/rag/config/agent-1", json=_valid_kb())
        assert resp.status_code == 200
        data = resp.json()
        assert data["ok"] is True
        assert data["agent_id"] == "agent-1"

    def test_save_with_bearer_secret_is_encrypted(self):
        """authValue must be stored encrypted (not plaintext) in the DB."""
        db_written: dict = {}

        def capture_save(aid, cfg):
            db_written.update(cfg)

        with (
            patch.object(rag_mod, "_db_save", side_effect=capture_save),
            patch.object(rag_mod, "_db_load", return_value=None),
            patch.object(rag_mod, "validate_rag_url", return_value=None),
        ):
            body = _valid_kb(auth_value="my-secret-token", auth_type="bearer")
            resp = _client().put("/rag/config/agent-2", json=body)
        assert resp.status_code == 200

        stored_val = (db_written.get("externalRag") or {}).get("authValue", "")
        assert stored_val != "my-secret-token", "plaintext secret must not be stored"
        assert stored_val != "", "ciphertext must not be empty"

        # Verify it decrypts back correctly
        from cryptography.fernet import Fernet
        f = Fernet(_TEST_FERNET_KEY.encode())
        assert f.decrypt(stored_val.encode()).decode() == "my-secret-token"

    def test_masked_sentinel_keeps_existing_secret(self):
        """Sending authValue='********' must preserve the existing ciphertext."""
        from cryptography.fernet import Fernet
        f = Fernet(_TEST_FERNET_KEY.encode())
        original_ciphertext = f.encrypt(b"original-secret").decode()

        # Pre-populate store
        _fake_db_save(
            "agent-3",
            _valid_kb(auth_value=original_ciphertext, auth_type="bearer"),
        )

        db_written: dict = {}

        def capture_save(aid, cfg):
            db_written.update(cfg)

        with (
            patch.object(rag_mod, "_db_save", side_effect=capture_save),
            patch.object(rag_mod, "_db_load", side_effect=_fake_db_load),
            patch.object(rag_mod, "validate_rag_url", return_value=None),
        ):
            body = _valid_kb(auth_value="********", auth_type="bearer")
            resp = _client().put("/rag/config/agent-3", json=body)
        assert resp.status_code == 200

        stored_val = (db_written.get("externalRag") or {}).get("authValue", "")
        assert stored_val == original_ciphertext, "existing ciphertext must be preserved"

    def test_missing_secret_key_with_auth_value_returns_422(self, monkeypatch):
        """Without RAG_SECRET_KEY, saving a secret must return 422."""
        monkeypatch.delenv("RAG_SECRET_KEY", raising=False)

        mock_save = MagicMock()
        with (
            patch.object(rag_mod, "_db_save", mock_save),
            patch.object(rag_mod, "_db_load", return_value=None),
            patch.object(rag_mod, "validate_rag_url", return_value=None),
            # _get_fernet reads env var directly; return None to simulate missing key
            patch.object(rag_mod, "_get_fernet", return_value=None),
        ):
            body = _valid_kb(auth_value="should-fail", auth_type="bearer")
            resp = _client().put("/rag/config/agent-fail", json=body)
        assert resp.status_code == 422
        mock_save.assert_not_called()

    def test_topk_out_of_range(self):
        """topK outside [1, 20] must fail Pydantic validation (422)."""
        with (
            patch.object(rag_mod, "_db_save"),
            patch.object(rag_mod, "_db_load", return_value=None),
            patch.object(rag_mod, "validate_rag_url", return_value=None),
        ):
            body = _valid_kb()
            body["topK"] = 0  # invalid: ge=1
            resp = _client().put("/rag/config/agent-bad-topk", json=body)
        assert resp.status_code == 422


# ---------------------------------------------------------------------------
# GET /rag/config/{agent_id}
# ---------------------------------------------------------------------------

class TestGetRagConfig:

    def test_not_found_returns_404(self):
        with patch.object(rag_mod, "_db_load", return_value=None):
            resp = _client().get("/rag/config/nonexistent")
        assert resp.status_code == 404

    def test_auth_value_is_masked(self):
        """GET must return '********' for authValue, never the real secret."""
        stored = _valid_kb(auth_value="some-ciphertext-xyz", auth_type="bearer")
        with patch.object(rag_mod, "_db_load", return_value=stored):
            resp = _client().get("/rag/config/agent-5")
        assert resp.status_code == 200
        data = resp.json()
        ext = data["knowledgeBase"].get("externalRag") or {}
        assert ext.get("authValue") == "********"
        assert "some-ciphertext-xyz" not in resp.text

    def test_returns_saved_fields(self):
        """Non-secret fields must come back unchanged."""
        stored = _valid_kb()
        stored["topK"] = 7
        stored["groundingMode"] = "strict"
        with patch.object(rag_mod, "_db_load", return_value=stored):
            resp = _client().get("/rag/config/agent-6")
        assert resp.status_code == 200
        kb = resp.json()["knowledgeBase"]
        assert kb["topK"] == 7
        assert kb["groundingMode"] == "strict"


# ---------------------------------------------------------------------------
# POST /rag/test-connection
# ---------------------------------------------------------------------------

class TestTestConnection:

    def _body(self, **override):
        body = {
            "externalRag": {
                "url": "https://example.com/rag",
                "method": "POST",
                "authType": "none",
                "authValue": "",
                "authName": "",
                "queryKey": "query",
                "responsePath": "results[].content",
            },
            "sample_query": "What is the maternity leave policy?",
        }
        body.update(override)
        return body

    def test_successful_connection(self):
        """ok=True when search returns snippets."""
        snippets = ["Employees get 12 annual leave days per year."]
        mock_instance = MagicMock()
        mock_instance.search = AsyncMock(return_value=snippets)
        mock_instance.aclose = AsyncMock()

        with (
            patch.object(rag_mod, "_db_load", return_value=None),
            patch.object(rag_mod, "validate_rag_url", return_value=None),
            patch.object(rag_mod, "HttpRetriever", return_value=mock_instance),
        ):
            resp = _client().post("/rag/test-connection", json=self._body())
        assert resp.status_code == 200
        data = resp.json()
        assert data["ok"] is True
        assert data["snippet_count"] == 1
        assert data["sample"] is not None
        assert len(data["sample"]) <= 120
        assert data["error"] is None
        mock_instance.aclose.assert_called_once()

    def test_empty_snippets_returns_ok_false(self):
        """ok=False when retrieval returns no snippets."""
        mock_instance = MagicMock()
        mock_instance.search = AsyncMock(return_value=[])
        mock_instance.aclose = AsyncMock()

        with (
            patch.object(rag_mod, "_db_load", return_value=None),
            patch.object(rag_mod, "validate_rag_url", return_value=None),
            patch.object(rag_mod, "HttpRetriever", return_value=mock_instance),
        ):
            resp = _client().post("/rag/test-connection", json=self._body())
        assert resp.status_code == 200
        data = resp.json()
        assert data["ok"] is False
        assert data["snippet_count"] == 0
        assert data["sample"] is None

    def test_retriever_error_returns_ok_false(self):
        """Network or HTTP errors are caught; ok=False; retriever still closed."""
        mock_instance = MagicMock()
        mock_instance.search = AsyncMock(side_effect=TimeoutError("request timed out"))
        mock_instance.aclose = AsyncMock()

        with (
            patch.object(rag_mod, "_db_load", return_value=None),
            patch.object(rag_mod, "validate_rag_url", return_value=None),
            patch.object(rag_mod, "HttpRetriever", return_value=mock_instance),
        ):
            resp = _client().post("/rag/test-connection", json=self._body())
        assert resp.status_code == 200
        data = resp.json()
        assert data["ok"] is False
        assert data["error"] is not None
        mock_instance.aclose.assert_called_once()

    def test_ssrf_url_blocked_returns_ok_false(self):
        """SSRF-blocked URL caught and returned as ok=False (not a 500)."""
        with (
            patch.object(rag_mod, "_db_load", return_value=None),
            patch.object(rag_mod, "validate_rag_url",
                         side_effect=ValueError("RAG URL host resolved to private address")),
        ):
            body = self._body()
            body["externalRag"]["url"] = "https://192.168.1.1/rag"
            resp = _client().post("/rag/test-connection", json=body)
        assert resp.status_code == 200
        data = resp.json()
        assert data["ok"] is False
        assert "URL validation" in data["error"]

    def test_no_url_returns_422(self):
        """Missing URL raises HTTP 422 before any network call."""
        with (
            patch.object(rag_mod, "_db_load", return_value=None),
            patch.object(rag_mod, "validate_rag_url", return_value=None),
        ):
            # No externalRag, no agent_id → no URL configured
            resp = _client().post("/rag/test-connection", json={"sample_query": "policy?"})
        assert resp.status_code == 422

    def test_stored_secret_used_when_inline_auth_is_masked(self):
        """When inline authValue='********', the stored secret is decrypted and used."""
        from cryptography.fernet import Fernet
        f = Fernet(_TEST_FERNET_KEY.encode())
        ciphertext = f.encrypt(b"stored-secret").decode()

        stored_cfg = {
            "ragEnabled": True,
            "sourceMode": "external",
            "topK": 3,
            "externalRag": {
                "url": "https://example.com/rag",
                "method": "POST",
                "authType": "bearer",
                "authValue": ciphertext,
                "authName": "",
                "queryKey": "query",
                "responsePath": "results[].content",
            },
        }

        mock_instance = MagicMock()
        mock_instance.search = AsyncMock(return_value=["snippet"])
        mock_instance.aclose = AsyncMock()

        with (
            patch.object(rag_mod, "_db_load", return_value=stored_cfg),
            patch.object(rag_mod, "validate_rag_url", return_value=None),
            patch.object(rag_mod, "_decrypt", return_value="stored-secret"),
            patch.object(rag_mod, "HttpRetriever", return_value=mock_instance),
        ):
            body = {
                "agent_id": "agent-7",
                "externalRag": {
                    "url": "https://example.com/rag",
                    "method": "POST",
                    "authType": "bearer",
                    "authValue": "********",
                    "authName": "",
                    "queryKey": "query",
                    "responsePath": "results[].content",
                },
                "sample_query": "annual leave policy",
            }
            resp = _client().post("/rag/test-connection", json=body)
        assert resp.status_code == 200
        assert resp.json()["ok"] is True

    def test_sample_truncated_to_120_chars(self):
        """First snippet in response is capped at 120 characters."""
        long_snippet = "A" * 300
        mock_instance = MagicMock()
        mock_instance.search = AsyncMock(return_value=[long_snippet])
        mock_instance.aclose = AsyncMock()

        with (
            patch.object(rag_mod, "_db_load", return_value=None),
            patch.object(rag_mod, "validate_rag_url", return_value=None),
            patch.object(rag_mod, "HttpRetriever", return_value=mock_instance),
        ):
            resp = _client().post("/rag/test-connection", json=self._body())
        assert resp.status_code == 200
        assert len(resp.json()["sample"]) == 120
