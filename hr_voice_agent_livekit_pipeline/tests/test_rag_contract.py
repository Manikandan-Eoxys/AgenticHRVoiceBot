"""
tests/test_rag_contract.py

Contract tests for HttpRetriever against the mock FastAPI RAG server.

Covers:
- Success response parsing (default results[].content)
- Alternate response shape (data.matches[].text)
- Single answer response shape (answer)
- Empty results handling
- Timeout handling (timeout=1 against /slow?delay=3)
- HTTP 500 error handling
- Invalid JSON payload handling
- Authentication: success with valid token, failure with missing/invalid token
- HTTP methods: GET and POST query dispatch
- Circuit breaker: 3 consecutive failures open it, returns [] without hitting server,
  recovers after cooldown (cooldown made patchable via time.monotonic)
- Idempotent cleanup: aclose() called twice safely
"""

import asyncio
import functools
import json
import os
import socket
import sys
import threading
import time
from unittest.mock import patch

import aiohttp
import pytest
import uvicorn

# Ensure hr_voice_agent is on sys.path
_HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _HERE not in sys.path:
    sys.path.insert(0, _HERE)

from services.rag_retriever_service import HttpRetriever, RagConfig
from tests.mock_rag_server import OK_DATA, app as mock_app


def async_test(fn):
    """Decorator to run async test functions synchronously without requiring pytest-asyncio."""
    @functools.wraps(fn)
    def wrapper(*args, **kwargs):
        return asyncio.run(fn(*args, **kwargs))
    return wrapper


def _find_free_port() -> int:
    """Bind to an ephemeral port on 127.0.0.1 to find an unused port."""
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


@pytest.fixture(scope="module")
def mock_server():
    """
    Run mock_rag_server via uvicorn in a background thread for the duration of the test module.
    """
    port = _find_free_port()
    config = uvicorn.Config(mock_app, host="127.0.0.1", port=port, log_level="error")
    server = uvicorn.Server(config)
    thread = threading.Thread(target=server.run, daemon=True)
    thread.start()

    # Wait for server to start accepting connections
    while not server.started:
        time.sleep(0.01)

    server_url = f"http://127.0.0.1:{port}"
    yield server_url

    server.should_exit = True
    thread.join(timeout=3)


@pytest.fixture(autouse=True)
def allow_local_rag(monkeypatch):
    """
    Permit HTTP and loopback hostnames for test endpoints against mock_rag_server.
    """
    monkeypatch.setenv("RAG_ALLOW_INSECURE_DEV", "true")
    monkeypatch.setenv("RAG_ALLOWED_HOSTS", "127.0.0.1,localhost")


@async_test
async def test_retriever_success(mock_server):
    """Test standard retrieval returning top_k snippets from results[].content."""
    config = RagConfig(
        enabled=True,
        url=f"{mock_server}/ok",
        method="POST",
        response_path="results[].content",
        top_k=3,
    )
    retriever = HttpRetriever(config)
    try:
        results = await retriever.search("leave policy")
        assert len(results) == 3
        assert results[0] == OK_DATA["results"][0]["content"]
        assert results[1] == OK_DATA["results"][1]["content"]
        assert results[2] == OK_DATA["results"][2]["content"]
    finally:
        await retriever.aclose()


@async_test
async def test_retriever_alternate_shape(mock_server):
    """Test retrieval parsing alternate shape: data.matches[].text."""
    config = RagConfig(
        enabled=True,
        url=f"{mock_server}/alt",
        method="POST",
        response_path="data.matches[].text",
        top_k=5,
    )
    retriever = HttpRetriever(config)
    try:
        results = await retriever.search("remote work policy")
        assert len(results) == 2
        assert "Remote work is permitted" in results[0]
        assert "Core working hours" in results[1]
    finally:
        await retriever.aclose()


@async_test
async def test_retriever_single_answer(mock_server):
    """Test retrieval parsing single string answer field: answer."""
    config = RagConfig(
        enabled=True,
        url=f"{mock_server}/single",
        method="POST",
        response_path="answer",
        top_k=1,
    )
    retriever = HttpRetriever(config)
    try:
        results = await retriever.search("probation period")
        assert len(results) == 1
        assert results[0] == "Standard probation period is three months."
    finally:
        await retriever.aclose()


@async_test
async def test_retriever_empty(mock_server):
    """Test retrieval returning empty list when results is empty."""
    config = RagConfig(
        enabled=True,
        url=f"{mock_server}/empty",
        method="POST",
        response_path="results[].content",
    )
    retriever = HttpRetriever(config)
    try:
        results = await retriever.search("unknown query")
        assert results == []
    finally:
        await retriever.aclose()


@async_test
async def test_retriever_timeout(mock_server):
    """Test retrieval timeout with timeout=1 against /slow?delay=3."""
    config = RagConfig(
        enabled=True,
        url=f"{mock_server}/slow?delay=3",
        method="GET",
    )
    retriever = HttpRetriever(config, timeout=1.0)
    try:
        with pytest.raises((asyncio.TimeoutError, aiohttp.ServerTimeoutError, TimeoutError)):
            await retriever.search("slow query")
    finally:
        await retriever.aclose()


@async_test
async def test_retriever_http_500_error(mock_server):
    """Test retrieval against /error returns HTTP 500 and raises ClientResponseError."""
    config = RagConfig(
        enabled=True,
        url=f"{mock_server}/error",
        method="POST",
    )
    retriever = HttpRetriever(config)
    try:
        with pytest.raises(aiohttp.ClientResponseError) as exc_info:
            await retriever.search("trigger error")
        assert exc_info.value.status == 500
    finally:
        await retriever.aclose()


@async_test
async def test_retriever_invalid_json(mock_server):
    """Test retrieval against /badjson fails parsing and raises an error."""
    config = RagConfig(
        enabled=True,
        url=f"{mock_server}/badjson",
        method="GET",
    )
    retriever = HttpRetriever(config)
    try:
        with pytest.raises(Exception):
            await retriever.search("bad json")
    finally:
        await retriever.aclose()


@async_test
async def test_retriever_auth_success(mock_server):
    """Test retrieval with valid Bearer token against /auth succeeds."""
    config = RagConfig(
        enabled=True,
        url=f"{mock_server}/auth",
        method="POST",
        auth_type="bearer",
        auth_value="test-token",
    )
    retriever = HttpRetriever(config)
    try:
        results = await retriever.search("authenticated query")
        assert len(results) == 3
    finally:
        await retriever.aclose()


@async_test
async def test_retriever_auth_failure(mock_server):
    """Test retrieval with missing or invalid token against /auth raises 401."""
    config = RagConfig(
        enabled=True,
        url=f"{mock_server}/auth",
        method="POST",
        auth_type="bearer",
        auth_value="wrong-token",
    )
    retriever = HttpRetriever(config)
    try:
        with pytest.raises(aiohttp.ClientResponseError) as exc_info:
            await retriever.search("unauthorized query")
        assert exc_info.value.status == 401
    finally:
        await retriever.aclose()


@async_test
async def test_retriever_get_and_post_methods(mock_server):
    """Test both GET and POST requests against /ok."""
    # Test GET
    get_config = RagConfig(
        enabled=True,
        url=f"{mock_server}/ok",
        method="GET",
        query_key="q",
    )
    get_retriever = HttpRetriever(get_config)
    try:
        get_results = await get_retriever.search("get query")
        assert len(get_results) == 3
    finally:
        await get_retriever.aclose()

    # Test POST
    post_config = RagConfig(
        enabled=True,
        url=f"{mock_server}/ok",
        method="POST",
        query_key="query",
    )
    post_retriever = HttpRetriever(post_config)
    try:
        post_results = await post_retriever.search("post query")
        assert len(post_results) == 3
    finally:
        await post_retriever.aclose()


@async_test
async def test_retriever_circuit_breaker(mock_server):
    """
    Test circuit breaker:
    1. 3 consecutive failures trips the breaker (is_open == True).
    2. While open, search() returns [] immediately without calling the server.
    3. After the cooldown expires, the breaker recovers and calls the server again.
    """
    config = RagConfig(
        enabled=True,
        url=f"{mock_server}/error",
        method="POST",
    )
    retriever = HttpRetriever(config)
    try:
        # Control time.monotonic to test cooldown recovery cleanly without waiting 60s
        current_time = 1000.0

        def fake_monotonic():
            return current_time

        with patch("services.rag_retriever_service.time.monotonic", side_effect=fake_monotonic):
            assert retriever.is_open is False

            # Failure 1
            with pytest.raises(aiohttp.ClientResponseError):
                await retriever.search("fail 1")
            assert retriever.is_open is False

            # Failure 2
            with pytest.raises(aiohttp.ClientResponseError):
                await retriever.search("fail 2")
            assert retriever.is_open is False

            # Failure 3 -> should trip breaker
            with pytest.raises(aiohttp.ClientResponseError):
                await retriever.search("fail 3")
            assert retriever.is_open is True

            # 4th call: circuit breaker is open -> returns [] immediately without calling server
            suppressed = await retriever.search("while open")
            assert suppressed == []

            # Advance time past 60s cooldown (e.g. +61 seconds)
            current_time += 61.0
            assert retriever.is_open is False

            # Point to healthy endpoint /ok; retrieval should recover and succeed
            retriever.config.url = f"{mock_server}/ok"
            recovered = await retriever.search("recovered query")
            assert len(recovered) == 3
            assert retriever.is_open is False
    finally:
        await retriever.aclose()


@async_test
async def test_retriever_aclose_idempotent(mock_server):
    """Test calling aclose() twice in a row cleans up safely without raising exceptions."""
    config = RagConfig(
        enabled=True,
        url=f"{mock_server}/ok",
        method="POST",
    )
    retriever = HttpRetriever(config)
    # First search initializes session
    results = await retriever.search("init session")
    assert len(results) == 3
    assert retriever._session is not None

    # First aclose
    await retriever.aclose()
    assert retriever._session is None

    # Second aclose - must not raise
    await retriever.aclose()
    assert retriever._session is None
