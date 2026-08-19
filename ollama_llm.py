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
import time
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


# ============================================================================
# HISTORY TRIMMING
# ============================================================================
# Every past exchange in the conversation gets re-sent and re-evaluated by
# Ollama on EVERY subsequent request — this is the single biggest lever for
# a small local model's per-turn latency, since prompt-eval time scales with
# input length and compounds turn over turn (turn 5 re-evaluates turns 1-4
# every single time).
#
# MAX_HISTORY_MESSAGES caps how many of the most recent chat-context
# messages (user/assistant/tool turns, NOT counting the system message,
# which is always kept) get sent. Tune this — 6 messages is roughly the
# last 3 user/assistant exchanges.
#
# Trade-off: the model will "forget" anything from more than ~3 exchanges
# back unless it's re-stated. For this HR flow that's usually fine, since
# SYSTEM_PROMPT already tells the model to track key facts, and identity
# (self.auth.employee_id in agent.py) is the real source of truth on the
# Python side — not the LLM's memory of the conversation.
MAX_HISTORY_MESSAGES = 6


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
        """Convert LiveKit ChatContext to a message list for Ollama's *native*
        /api/chat endpoint, trimmed to the system message + the most recent
        MAX_HISTORY_MESSAGES turns.

        `to_provider_format(format="openai")` gives us OpenAI wire-format
        messages, which is NOT the same shape Ollama's native endpoint
        expects:

        - OpenAI: assistant tool_calls[].function.arguments is a JSON-encoded
            STRING, e.g. "{}" or "{\"employee_id\": 1001}".
        - Ollama native: tool_calls[].function.arguments must be an actual
            JSON OBJECT, e.g. {} or {"employee_id": 1001}.

        - OpenAI-style content can also be a list of content-part dicts
            (e.g. [{"type": "text", "text": "..."}]) instead of a plain string.
            Ollama's native endpoint only accepts a string.

        Forwarding the OpenAI shape as-is causes Ollama's chat-template
        renderer to choke the next time an assistant/tool_call turn with a
        stringified `arguments` field shows up in history — surfacing as
        HTTP 400: "Value looks like object, but can't find closing '}' symbol".
        We normalize both fields here before sending.
        """
        try:
            formatted_messages, _ = self._chat_ctx.to_provider_format(format="openai")
            if formatted_messages:
                messages = [self._to_ollama_message(m) for m in formatted_messages]
                return self._trim_history(messages)
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
        return self._trim_history(messages)

    @staticmethod
    def _trim_history(messages: list[dict]) -> list[dict]:
        """Keep the system message (always first, if present) plus only the
        most recent MAX_HISTORY_MESSAGES messages. This is what keeps
        prompt-eval time roughly constant across a long call instead of
        growing turn over turn.
        """
        if not messages:
            return messages

        if messages[0].get("role") == "system":
            system_msg, rest = messages[0], messages[1:]
            trimmed = rest[-MAX_HISTORY_MESSAGES:] if len(rest) > MAX_HISTORY_MESSAGES else rest
            if len(rest) > MAX_HISTORY_MESSAGES:
                logger.debug(
                    "[OllamaLLM] Trimmed history: %d -> %d messages (+ system)",
                    len(rest), len(trimmed),
                )
            return [system_msg] + trimmed

        trimmed = messages[-MAX_HISTORY_MESSAGES:] if len(messages) > MAX_HISTORY_MESSAGES else messages
        if len(messages) > MAX_HISTORY_MESSAGES:
            logger.debug(
                "[OllamaLLM] Trimmed history: %d -> %d messages (no system msg found)",
                len(messages), len(trimmed),
            )
        return trimmed

    @staticmethod
    def _to_ollama_message(msg: dict) -> dict:
        """Fix up one OpenAI-shaped message for Ollama's native /api/chat."""
        msg = dict(msg)  # shallow copy, don't mutate LiveKit's internals

        # 1. content: list-of-parts -> plain string
        content = msg.get("content")
        if isinstance(content, list):
            msg["content"] = "".join(
                part.get("text", "") for part in content if isinstance(part, dict)
            )
        elif content is None:
            msg["content"] = ""

        # 2. tool_calls[].function.arguments: JSON string -> dict
        tool_calls = msg.get("tool_calls")
        if tool_calls:
            fixed_calls = []
            for tc in tool_calls:
                tc = dict(tc)
                fn = dict(tc.get("function", {}))
                args = fn.get("arguments")
                if isinstance(args, str):
                    try:
                        fn["arguments"] = json.loads(args) if args.strip() else {}
                    except json.JSONDecodeError:
                        fn["arguments"] = {}
                elif args is None:
                    fn["arguments"] = {}
                tc["function"] = fn
                fixed_calls.append(tc)
            msg["tool_calls"] = fixed_calls

        return msg

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

        start = time.monotonic()
        logger.info(
            "[OllamaLLM] request %s starting — %d messages, %d tool schemas, timeout=%.0fs",
            request_id, len(messages), len(payload.get("tools", [])), req_timeout,
        )

        try:
            async with aiohttp.ClientSession(timeout=timeout) as session:
                async with session.post(url, json=payload) as resp:
                    logger.info(
                        "[OllamaLLM] request %s got HTTP %s after %.1fs",
                        request_id, resp.status, time.monotonic() - start,
                    )
                    if resp.status != 200:
                        body = await resp.text()
                        logger.error("[OllamaLLM] 400 payload was: %s", json.dumps(payload)[:2000])
                        raise APIConnectionError(
                            f"Ollama /api/chat returned HTTP {resp.status}: {body}"
                        )

                    first_chunk = True
                    async for raw_line in resp.content:
                        line = raw_line.decode("utf-8").strip()
                        if not line:
                            continue
                        try:
                            data = json.loads(line)
                        except json.JSONDecodeError:
                            continue

                        if first_chunk:
                            logger.info(
                                "[OllamaLLM] request %s first byte after %.1fs",
                                request_id, time.monotonic() - start,
                            )
                            first_chunk = False

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
                            logger.info(
                                "[OllamaLLM] request %s done after %.1fs — "
                                "prompt_tokens=%d completion_tokens=%d",
                                request_id, time.monotonic() - start, prompt_count, eval_count,
                            )
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
            logger.warning(
                "[OllamaLLM] request %s TIMED OUT after %.1fs",
                request_id, time.monotonic() - start,
            )
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