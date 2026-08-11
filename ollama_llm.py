"""
ollama_llm.py

Thin LiveKit-compatible LLM wrapper around Ollama's native /api/chat endpoint.

WHY THIS EXISTS
--------------
Ollama's OpenAI-compat endpoint (/v1/chat/completions) ignores the `think`
parameter at the computation level — it strips <think> tags from output but
the model still runs the full reasoning chain internally.

  Tested results for qwen3:8b on this machine:
    /v1/chat/completions + think:false (top-level) → 36s  (tags stripped, slow)
    /v1/chat/completions + /no_think in system msg  → 63s  (no effect)
    /api/chat            + "think": false           → ~instant

The native /api/chat endpoint with "think": false truly suppresses the reasoning
computation, dropping response time from ~70s to ~3-5s for qwen3:8b.

LiveKit's livekit-plugins-openai LLM only speaks to the OpenAI-compat endpoint,
so we can't use it to get real thinking suppression. This wrapper calls the
native endpoint directly, streaming chunks back in the format LiveKit expects.

USAGE (in main.py)
------------------
    from ollama_llm import OllamaLLM
    llm = OllamaLLM(
        model="qwen3:8b",
        base_url="http://localhost:11434",  # NO /v1 suffix
        think=False,
    )
"""

from __future__ import annotations

import asyncio
import json
import logging
import uuid

import aiohttp

from livekit.agents.llm import (
    LLM,
    ChatChunk,
    ChatContext,
    ChatMessage,
    ChoiceDelta,
    CompletionUsage,
    LLMStream,
    FunctionToolCall,
    ToolContext,
)
from livekit.agents.llm.llm import APIConnectOptions
from livekit.agents._exceptions import APIConnectionError, APITimeoutError

logger = logging.getLogger(__name__)


class OllamaLLMStream(LLMStream):
    """Async stream of ChatChunks from Ollama's native /api/chat endpoint with tool support."""

    def __init__(
        self,
        llm: "OllamaLLM",
        *,
        chat_ctx: ChatContext,
        tools,
        conn_options: APIConnectOptions,
        model: str,
        base_url: str,
        think: bool,
    ) -> None:
        super().__init__(llm, chat_ctx=chat_ctx, tools=tools or [], conn_options=conn_options)
        self._model = model
        self._base_url = base_url.rstrip("/")
        self._think = think

    def _build_messages(self) -> list[dict]:
        """Convert LiveKit ChatContext to OpenAI-compatible message list for Ollama."""
        try:
            formatted_messages, _ = self._chat_ctx.to_provider_format(format="openai")
            if formatted_messages:
                return formatted_messages
        except Exception as e:
            logger.warning("[OllamaLLM] to_provider_format failed, falling back: %s", e)

        messages = []
        for item in self._chat_ctx.items:
            if not isinstance(item, ChatMessage):
                continue
            role = str(item.role) if getattr(item, "role", None) else "user"
            if role not in ("system", "user", "assistant", "tool"):
                role = "user"
            content = item.text_content or ""
            if content:
                messages.append({"role": role, "content": content})
        return messages

    async def _run(self) -> None:
        messages = self._build_messages()
        payload = {
            "model": self._model,
            "messages": messages,
            "stream": True,
            "think": self._think,
        }

        # Include tool definitions if available
        if self._tools:
            try:
                tool_schemas = ToolContext(self._tools).parse_function_tools("openai")
                if tool_schemas:
                    payload["tools"] = tool_schemas
            except Exception as e:
                logger.warning("[OllamaLLM] Failed to parse tool schemas: %s", e)

        url = f"{self._base_url}/api/chat"
        # Ensure timeout total is at least 60s for local Ollama prompt eval
        req_timeout = max(float(self._conn_options.timeout), getattr(self._llm, "_timeout", 60.0), 60.0)
        timeout = aiohttp.ClientTimeout(
            total=req_timeout,
            connect=30.0,
        )
        request_id = str(uuid.uuid4())

        try:
            async with aiohttp.ClientSession(timeout=timeout) as session:
                async with session.post(url, json=payload) as resp:
                    if resp.status != 200:
                        body = await resp.text()
                        raise APIConnectionError(
                            f"Ollama /api/chat returned HTTP {resp.status}: {body}"
                        )

                    async for raw_line in resp.content:
                        line = raw_line.decode("utf-8").strip()
                        if not line:
                            continue
                        try:
                            data = json.loads(line)
                        except json.JSONDecodeError:
                            continue

                        msg = data.get("message", {})
                        content = msg.get("content", "")
                        raw_tool_calls = msg.get("tool_calls", [])
                        done = data.get("done", False)

                        lk_tool_calls = []
                        if raw_tool_calls:
                            for tc in raw_tool_calls:
                                fn = tc.get("function", {})
                                name = fn.get("name", "")
                                args = fn.get("arguments", {})
                                args_str = json.dumps(args) if isinstance(args, dict) else str(args)
                                call_id = tc.get("id") or f"call_{uuid.uuid4().hex[:8]}"
                                lk_tool_calls.append(
                                    FunctionToolCall(
                                        name=name,
                                        arguments=args_str,
                                        call_id=call_id,
                                    )
                                )

                        if content or lk_tool_calls:
                            self._event_ch.send_nowait(
                                ChatChunk(
                                    id=request_id,
                                    delta=ChoiceDelta(
                                        role="assistant",
                                        content=content if content else None,
                                        tool_calls=lk_tool_calls,
                                    ),
                                )
                            )

                        if done:
                            eval_count = data.get("eval_count", 0)
                            prompt_count = data.get("prompt_eval_count", 0)
                            self._event_ch.send_nowait(
                                ChatChunk(
                                    id=request_id,
                                    delta=None,
                                    usage=CompletionUsage(
                                        completion_tokens=eval_count,
                                        prompt_tokens=prompt_count,
                                        total_tokens=eval_count + prompt_count,
                                    ),
                                )
                            )
                            break

        except asyncio.TimeoutError as e:
            raise APITimeoutError(retryable=True) from e
        except aiohttp.ClientConnectionError as e:
            raise APIConnectionError(str(e)) from e


class OllamaLLM(LLM):
    """
    LiveKit LLM plugin backed by Ollama's native /api/chat endpoint.

    This is a drop-in replacement for livekit-plugins-openai's LLM when
    using Ollama with qwen3 (or any model with a thinking mode), because
    the OpenAI-compat endpoint cannot truly suppress the reasoning chain.

    Parameters
    ----------
    model : str
        Ollama model name, e.g. "qwen3:8b".
    base_url : str
        Ollama server base URL. Do NOT include /v1 — this uses the native
        API. If /v1 is present it will be stripped automatically.
        Default: "http://localhost:11434"
    think : bool
        Whether to enable extended thinking. Default False (fast, ~3-5s).
        Set True only if you need deep reasoning and can tolerate 60-90s waits.
    timeout : float
        Per-request timeout in seconds passed to APIConnectOptions.
        Default 60.0 — generous enough for a slow first token on 8B models.
    """

    def __init__(
        self,
        *,
        model: str = "qwen3:8b",
        base_url: str = "http://localhost:11434",
        think: bool = False,
        timeout: float = 60.0,
    ) -> None:
        super().__init__()
        # Strip /v1 suffix if user accidentally includes it (e.g. from .env)
        self._base_url = base_url.rstrip("/")
        if self._base_url.endswith("/v1"):
            self._base_url = self._base_url[:-3]
        self._model = model
        self._think = think
        self._timeout = timeout
        logger.info(
            "[OllamaLLM] Initialized: model=%s base_url=%s think=%s timeout=%ss",
            model, self._base_url, think, timeout,
        )

    @property
    def model(self) -> str:
        return self._model

    @property
    def provider(self) -> str:
        return "ollama"

    def chat(
        self,
        *,
        chat_ctx: ChatContext,
        tools=None,
        conn_options: APIConnectOptions = APIConnectOptions(
            max_retry=2,
            retry_interval=1.0,
            timeout=60.0,   # override default 10s — qwen3:8b needs up to 60s cold
        ),
        **kwargs,
    ) -> OllamaLLMStream:
        return OllamaLLMStream(
            self,
            chat_ctx=chat_ctx,
            tools=tools,
            conn_options=conn_options,
            model=self._model,
            base_url=self._base_url,
            think=self._think,
        )
