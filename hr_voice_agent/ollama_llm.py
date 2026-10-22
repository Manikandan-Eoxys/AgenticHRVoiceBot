"""
ollama_llm.py

Thin LiveKit-compatible LLM wrapper around Ollama's native /api/chat endpoint.

WHY THIS EXISTS
--------------
Ollama's OpenAI-compat endpoint (/v1/chat/completions) ignores the `think`
parameter at the computation level — it strips <think> tags from output but
the model still runs the full reasoning chain internally.

  Tested results for qwen2.5:3b  on this machine:
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
        model="qwen2.5:3b ",
        base_url="http://localhost:11434",  # NO /v1 suffix
        think=False,
    )
"""

# ============================================================================
# FILE MAP (read this first)
# ============================================================================
# This file has two features:
#
#   1. OllamaLLM + OllamaLLMStream
#        Talk to a LOCAL Ollama server and hand its answer to LiveKit
#        in the format LiveKit understands.
#
#   2. FallbackLLM + FallbackLLMStream
#        Wrap TWO LLMs: a cloud one (primary) and OllamaLLM (backup).
#        If the cloud has a temporary problem, use Ollama instead.
#
# Pattern used by LiveKit (same for both features):
#   LLM class        = the "factory". LiveKit calls  llm.chat(...)
#   LLMStream class  = one running request. Its _run() does the real work
#                      and pushes ChatChunk objects into self._event_ch.
#                      LiveKit reads those chunks (text -> TTS, tool calls -> run tools).
# ============================================================================

# Makes type hints (like "OllamaLLM" before the class exists) work smoothly.
from __future__ import annotations

import asyncio    # async/await support, timeouts
import json       # convert between Python dicts and JSON text
import logging    # write log lines to the terminal
import time       # measure how long requests take
import uuid       # generate unique request / tool-call ids

import aiohttp    # async HTTP client, used to call Ollama's web API

# LiveKit building blocks used to create a custom LLM.
from livekit.agents.llm import (
    LLM,                # base class for any LLM
    ChatChunk,          # one small piece of the model's streamed reply
    ChatContext,        # the whole conversation history
    ChatMessage,        # one message inside the history
    ChoiceDelta,        # the "new content" part inside a ChatChunk
    CompletionUsage,    # token counts (prompt / completion / total)
    LLMStream,          # base class for one streaming request
    FunctionToolCall,   # the model asking to run a tool (function)
    ToolContext,        # helper that turns Python tools into JSON schemas
)
from livekit.agents.llm.llm import APIConnectOptions   # retry / timeout settings
from livekit.agents._exceptions import APIConnectionError, APITimeoutError, APIStatusError

# Logger named after this file (ollama_llm), so log lines show where they came from.
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
MAX_HISTORY_MESSAGES = 6   # keep only the last 6 messages (+ the system prompt)


# ============================================================================
# PART 1: LOCAL OLLAMA LLM
# ============================================================================
class OllamaLLMStream(LLMStream):
    """Async stream of ChatChunks from Ollama's native /api/chat endpoint with tool support.

    One object of this class = ONE request to Ollama (one LLM turn).
    LiveKit creates it, then runs _run(), then reads the chunks we push out.
    """

    def __init__(
        self,
        llm: "OllamaLLM",              # the OllamaLLM that created this stream
        *,
        chat_ctx: ChatContext,         # conversation history for this turn
        tools,                         # tools (functions) the model may call
        conn_options: APIConnectOptions,   # timeout / retry settings
        model: str,                    # Ollama model name
        base_url: str,                 # Ollama server address
        think: bool,                   # reasoning on/off
    ) -> None:
        # Let LiveKit's base class store chat_ctx, tools, etc. ("or []" avoids None)
        super().__init__(llm, chat_ctx=chat_ctx, tools=tools or [], conn_options=conn_options)
        self._model = model
        self._base_url = base_url.rstrip("/")   # remove trailing "/" so URLs join cleanly
        self._think = think

    # ------------------------------------------------------------------
    # Step A: turn LiveKit's conversation into Ollama's message format
    # ------------------------------------------------------------------
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
        # Preferred path: let LiveKit convert the history to OpenAI-style dicts,
        # then fix each message so Ollama accepts it.
        try:
            formatted_messages, _ = self._chat_ctx.to_provider_format(format="openai")
            if formatted_messages:
                messages = [self._to_ollama_message(m) for m in formatted_messages]
                return self._trim_history(messages)
        except Exception as e:
            logger.warning("[OllamaLLM] to_provider_format failed, falling back: %s", e)

        # Backup path (only reached if the above failed): build a simple list by hand,
        # keeping just plain text messages and ignoring tool calls.
        messages = []
        for item in self._chat_ctx.items:
            if not isinstance(item, ChatMessage):
                continue   # skip anything that is not a normal message
            role = str(item.role) if getattr(item, "role", None) else "user"
            if role not in ("system", "user", "assistant", "tool"):
                role = "user"   # unknown role -> treat as user
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
            return messages   # nothing to trim

        # Case 1: first message is the system prompt -> always keep it,
        # and trim only the messages after it.
        if messages[0].get("role") == "system":
            system_msg, rest = messages[0], messages[1:]
            # rest[-6:] means "the last 6 items of the list"
            trimmed = rest[-MAX_HISTORY_MESSAGES:] if len(rest) > MAX_HISTORY_MESSAGES else rest
            if len(rest) > MAX_HISTORY_MESSAGES:
                logger.debug(
                    "[OllamaLLM] Trimmed history: %d -> %d messages (+ system)",
                    len(rest), len(trimmed),
                )
            return [system_msg] + trimmed

        # Case 2: no system message found -> just keep the last 6 messages.
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
        #    e.g. [{"type":"text","text":"Hi"}, {"type":"text","text":" there"}] -> "Hi there"
        content = msg.get("content")
        if isinstance(content, list):
            msg["content"] = "".join(
                part.get("text", "") for part in content if isinstance(part, dict)
            )
        elif content is None:
            msg["content"] = ""   # Ollama wants a string, never None

        # 2. tool_calls[].function.arguments: JSON string -> dict
        #    OpenAI sends  "{\"employee_id\": 1001}"   (text)
        #    Ollama needs  {"employee_id": 1001}        (real dict)
        tool_calls = msg.get("tool_calls")
        if tool_calls:
            fixed_calls = []
            for tc in tool_calls:
                tc = dict(tc)                          # copy again, don't edit the original
                fn = dict(tc.get("function", {}))
                args = fn.get("arguments")
                if isinstance(args, str):
                    try:
                        # empty string -> {} ; otherwise parse the JSON text into a dict
                        fn["arguments"] = json.loads(args) if args.strip() else {}
                    except json.JSONDecodeError:
                        fn["arguments"] = {}           # broken JSON -> use empty dict
                elif args is None:
                    fn["arguments"] = {}
                tc["function"] = fn
                fixed_calls.append(tc)
            msg["tool_calls"] = fixed_calls

        return msg

    # ------------------------------------------------------------------
    # Step B: the real work — call Ollama and stream the answer back
    # ------------------------------------------------------------------
    async def _run(self) -> None:
        # 1) Build the message list (system prompt + last few messages).
        messages = self._build_messages()

        # 2) Build the JSON body that will be sent to Ollama.
        payload = {
            "model": self._model,
            "messages": messages,
            "stream": True,          # get the answer piece by piece (lower latency)
            "think": self._think,    # False = skip the slow reasoning chain
            "options": {
                "num_thread": 8,     # CPU threads Ollama may use
            },
        }

        # 3) Add tool definitions (so the model can call functions like get_employee_by_id).
        if self._tools:
            try:
                # Convert all Python tools to JSON schemas in one go.
                tool_schemas = ToolContext(self._tools).parse_function_tools("openai")
                if tool_schemas:
                    payload["tools"] = tool_schemas
            except Exception as e:
                # If one bad tool breaks the bulk conversion, try each tool separately
                # and keep only the ones that convert successfully.
                logger.warning("[OllamaLLM] Bulk tool parsing failed, falling back to per-tool: %s", e)
                valid_schemas = []
                for t in self._tools:
                    try:
                        s = ToolContext([t]).parse_function_tools("openai")
                        if s:
                            valid_schemas.extend(s)
                    except Exception:
                        pass   # skip the broken tool
                if valid_schemas:
                    payload["tools"] = valid_schemas

        # 4) Work out the URL and timeouts.
        url = f"{self._base_url}/api/chat"
        # Use the biggest of: LiveKit's timeout, our own timeout, and 60s.
        # (A local model can be slow on its first request.)
        req_timeout = max(float(self._conn_options.timeout), getattr(self._llm, "_timeout", 60.0), 60.0)
        timeout = aiohttp.ClientTimeout(
            total=req_timeout,   # max time for the whole request
            connect=30.0,        # max time just to connect to Ollama
        )
        request_id = str(uuid.uuid4())   # unique id, used in logs and chunks

        # Only used for the log line below (how many tool schemas we sent).
        tools_list: list[object] = payload.get("tools") if isinstance(payload.get("tools"), list) else []  # type: ignore[assignment]
        start = time.monotonic()   # stopwatch start
        logger.info(
            "[OllamaLLM] request %s starting — %d messages, %d tool schemas, timeout=%.0fs",
            request_id, len(messages), len(tools_list), req_timeout,
        )

        try:
            # 5) Open an HTTP connection and POST the payload.
            async with aiohttp.ClientSession(timeout=timeout) as session:
                async with session.post(url, json=payload) as resp:
                    logger.info(
                        "[OllamaLLM] request %s got HTTP %s after %.1fs",
                        request_id, resp.status, time.monotonic() - start,
                    )
                    # Anything other than 200 OK is an error: log the payload and raise.
                    if resp.status != 200:
                        body = await resp.text()
                        logger.error("[OllamaLLM] 400 payload was: %s", json.dumps(payload)[:2000])
                        raise APIConnectionError(
                            f"Ollama /api/chat returned HTTP {resp.status}: {body}"
                        )

                    first_chunk = True   # used only to log "first byte" timing once

                    # 6) Ollama streams one JSON object per line (NDJSON). Read line by line.
                    async for raw_line in resp.content:
                        line = raw_line.decode("utf-8").strip()
                        if not line:
                            continue   # skip blank lines
                        try:
                            data = json.loads(line)
                        except json.JSONDecodeError:
                            continue   # skip a line that is not valid JSON

                        if first_chunk:
                            logger.info(
                                "[OllamaLLM] request %s first byte after %.1fs",
                                request_id, time.monotonic() - start,
                            )
                            first_chunk = False

                        # Pull out the parts we care about from this line.
                        msg = data.get("message", {})
                        content = msg.get("content", "")             # new text, if any
                        raw_tool_calls = msg.get("tool_calls", [])   # tool requests, if any
                        done = data.get("done", False)               # True on the last line

                        # 7) Convert Ollama tool calls -> LiveKit FunctionToolCall objects.
                        lk_tool_calls = []
                        if raw_tool_calls:
                            for tc in raw_tool_calls:
                                fn = tc.get("function", {})
                                name = fn.get("name", "")
                                args = fn.get("arguments", {})
                                # LiveKit wants arguments as a JSON STRING (reverse of _to_ollama_message).
                                args_str = json.dumps(args) if isinstance(args, dict) else str(args)
                                # Ollama may not give an id, so create one.
                                call_id = tc.get("id") or f"call_{uuid.uuid4().hex[:8]}"
                                lk_tool_calls.append(
                                    FunctionToolCall(
                                        name=name,
                                        arguments=args_str,
                                        call_id=call_id,
                                    )
                                )

                        # 8) If this line had text or tool calls, push it to LiveKit.
                        #    LiveKit sends the text to TTS and runs the tools.
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

                        # 9) The last line carries token counts. Send a final "usage" chunk and stop.
                        if done:
                            eval_count = data.get("eval_count", 0)            # tokens generated
                            prompt_count = data.get("prompt_eval_count", 0)   # tokens in the prompt
                            logger.info(
                                "[OllamaLLM] request %s done after %.1fs — "
                                "prompt_tokens=%d completion_tokens=%d",
                                request_id, time.monotonic() - start, prompt_count, eval_count,
                            )
                            self._event_ch.send_nowait(
                                ChatChunk(
                                    id=request_id,
                                    delta=None,   # no new text, usage info only
                                    usage=CompletionUsage(
                                        completion_tokens=eval_count,
                                        prompt_tokens=prompt_count,
                                        total_tokens=eval_count + prompt_count,
                                    ),
                                )
                            )
                            break

        # Translate low-level errors into LiveKit's own exception types,
        # so LiveKit (and FallbackLLM) know how to treat them.
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
        Ollama model name, e.g. "qwen2.5:3b ".
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

    # The "*" means every argument must be passed by name:
    # OllamaLLM(model="...", base_url="...")
    def __init__(
        self,
        *,
        model: str = "qwen3:8b",
        base_url: str = "http://localhost:11434",
        think: bool = False,
        timeout: float = 60.0,
    ) -> None:
        super().__init__()   # let LiveKit's LLM base class set itself up
        # Strip /v1 suffix if user accidentally includes it (e.g. from .env)
        self._base_url = base_url.rstrip("/")
        if self._base_url.endswith("/v1"):
            self._base_url = self._base_url[:-3]   # remove the last 3 characters "/v1"
        self._model = (model or "qwen3:8b").strip()   # default if empty, remove stray spaces
        self._think = think
        self._timeout = timeout
        # Note: this only stores settings. No connection to Ollama is made here.
        logger.info(
            "[OllamaLLM] Initialized: model=%s base_url=%s think=%s timeout=%ss",
            self._model, self._base_url, think, timeout,
        )

    # LiveKit reads these two properties for logging / metrics.
    @property
    def model(self) -> str:
        return self._model

    @property
    def provider(self) -> str:
        return "ollama"

    def chat(
        self,
        *,
        chat_ctx: ChatContext,   # conversation history
        tools=None,              # tools the model may call
        conn_options: APIConnectOptions = APIConnectOptions(
            max_retry=2,          # retry up to 2 times on failure
            retry_interval=1.0,   # wait 1 second between retries
            timeout=60.0,   # override default 10s — qwen2.5:3b  needs up to 60s cold
        ),
        **kwargs,                # extra options LiveKit may pass; ignored here
    ) -> OllamaLLMStream:
        """Called by LiveKit once per LLM turn. Returns a stream object;
        LiveKit then runs its _run() method to get the answer."""
        return OllamaLLMStream(
            self,
            chat_ctx=chat_ctx,
            tools=tools,
            conn_options=conn_options,
            model=self._model,
            base_url=self._base_url,
            think=self._think,
        )


# ============================================================================
# PART 2: HYBRID / FALLBACK LLM WRAPPER
# ============================================================================
# Idea: try the cloud LLM first. If it has a TEMPORARY problem, use local Ollama.
#
#   caller speaks
#        |
#        v
#   FallbackLLM.chat()  ->  FallbackLLMStream._run()
#        |
#        |-- cloud healthy?  --yes--> stream cloud answer  (done)
#        |
#        '-- cloud failed?
#              |-- already sent text to the caller?  -> re-raise error (can't switch mid-sentence)
#              |-- temporary error (timeout/429/5xx)? -> mark cloud "degraded" for 60s, use Ollama
#              '-- permanent error (bad request/auth)?-> use Ollama for THIS request only
# ============================================================================

# Transient errors that justify falling back to local Ollama.
# Bad-request / auth / validation errors should NOT trigger fallback —
# they indicate a bug, not a temporary outage.
# (A tuple of exception classes, used later with isinstance().)
_TRANSIENT_EXCEPTIONS = (APIConnectionError, APITimeoutError, ConnectionError, OSError)


class FallbackLLMStream(LLMStream):
    """
    Stream wrapper that attempts the primary LLM (e.g. LiveKit Cloud / OpenAI)
    and automatically falls back to local OllamaLLM on *transient* errors only.

    Mid-stream safety: if the primary has already emitted at least one content
    chunk, we do NOT silently fall back (which would cause the user to hear a
    partial sentence followed by a full duplicate).  Instead the error is
    re-raised so the session can surface it normally.
    """

    def __init__(
        self,
        fallback_obj: "FallbackLLM",   # the FallbackLLM that owns the shared state
        *,
        chat_ctx: ChatContext,
        tools=None,
        conn_options: APIConnectOptions = APIConnectOptions(),
        **kwargs,
    ) -> None:
        super().__init__(fallback_obj, chat_ctx=chat_ctx, tools=tools or [], conn_options=conn_options)
        # Keep our own copies so _run() can pass them on to the primary / fallback LLM.
        self._fallback_obj = fallback_obj
        self._chat_ctx = chat_ctx
        self._tools = tools or []
        self._conn_options = conn_options
        self._kwargs = kwargs

    @staticmethod
    def _is_transient(exc: Exception) -> bool:
        """Return True if the exception looks like a transient outage
        (timeout, connection failure, 429 rate-limit) rather than a
        permanent error (bad request, auth, malformed schema)."""
        # Check 1: is it one of the known "temporary" exception classes?
        if isinstance(exc, _TRANSIENT_EXCEPTIONS):
            return True
        # Check 2: HTTP-level 429 (too many requests) or 5xx (server error)
        # from LiveKit's APIStatusError
        if isinstance(exc, APIStatusError):
            return exc.status_code == 429 or exc.status_code >= 500  # type: ignore[attr-defined]
        # Check 3: some providers wrap a 429 inside a plain message, so search the text.
        msg = str(exc).lower()
        if "429" in msg or "rate limit" in msg or "quota" in msg or "too many requests" in msg:
            return True
        # Anything else (bad request, auth error, etc.) is treated as permanent.
        return False

    async def _run(self) -> None:
        # ---- Step 1: if the cloud was marked "degraded", has the cooldown passed? ----
        if self._fallback_obj._primary_degraded:
            elapsed = time.monotonic() - self._fallback_obj._last_degraded_time
            if elapsed > self._fallback_obj._cooldown_seconds:
                logger.info(
                    "[FallbackLLM] Cooldown (%.0fs) passed. Retrying primary LLM...",
                    elapsed,
                )
                self._fallback_obj._primary_degraded = False   # give the cloud another chance

        # ---- Step 2: try the cloud LLM (only if it is not currently degraded) ----
        if not self._fallback_obj._primary_degraded:
            chunks_emitted = 0   # how many chunks we already sent to LiveKit
            try:
                primary_stream = self._fallback_obj.primary_llm.chat(
                    chat_ctx=self._chat_ctx,
                    tools=self._tools,
                    conn_options=self._conn_options,
                    **self._kwargs,
                )
                # Forward every chunk from the cloud straight to LiveKit.
                async for chunk in primary_stream:
                    self._event_ch.send_nowait(chunk)
                    chunks_emitted += 1
                return  # ✅ Cloud succeeded — we are done, Ollama is never used
            except Exception as exc:
                # --- Case A: failed AFTER some output was already sent ---
                if chunks_emitted > 0:
                    # Mid-stream failure: tokens already sent to TTS.
                    # Falling back now would duplicate output, so re-raise.
                    logger.error(
                        "[FallbackLLM] Primary LLM failed MID-STREAM after %d chunks. "
                        "Cannot safely fall back — re-raising. Error: %s",
                        chunks_emitted, exc,
                    )
                    raise   # pass the error up to LiveKit

                # --- Case B: permanent error (bug / bad config) ---
                if not self._is_transient(exc):
                    # Non-transient error (bad request, auth, etc.)
                    # Don't mark the primary as degraded.
                    logger.error(
                        "[FallbackLLM] Primary LLM hit a NON-TRANSIENT error (%s: %s). "
                        "Falling back for this request only (not marking degraded).",
                        type(exc).__name__, exc,
                    )
                    # Fall through to Ollama below, but don't set _primary_degraded
                # --- Case C: temporary error (timeout / 429 / 5xx / connection) ---
                else:
                    # Transient error — mark degraded and start cooldown
                    logger.warning(
                        "[FallbackLLM] Primary LLM stream failed with transient error (%s: %s). "
                        "Automatically falling back to local OllamaLLM for %.0fs...",
                        type(exc).__name__, exc, self._fallback_obj._cooldown_seconds,
                    )
                    self._fallback_obj._primary_degraded = True                 # skip cloud from now on...
                    self._fallback_obj._last_degraded_time = time.monotonic()   # ...starting at this moment

        # ---- Step 3: use local Ollama ----
        # Reached when: cloud is degraded, OR cloud failed (case B or C) before sending anything.
        logger.info("[FallbackLLM] Using local Ollama fallback.")
        fallback_stream = self._fallback_obj.fallback_llm.chat(
            chat_ctx=self._chat_ctx,
            tools=self._tools,
            conn_options=self._conn_options,
            **self._kwargs,
        )
        # Forward Ollama's chunks to LiveKit exactly like the cloud's chunks.
        async for chunk in fallback_stream:
            self._event_ch.send_nowait(chunk)


class FallbackLLM(LLM):
    """
    Hybrid / Automatic Fallback LLM.
    Tries primary_llm (e.g., LiveKit Cloud Hosted LLM / OpenAI).
    If primary_llm fails due to a *transient* error (429, timeout, connection),
    it seamlessly falls back to fallback_llm (local OllamaLLM).
    Automatically retries primary_llm after a configurable cooldown.

    Non-transient errors (bad request, auth) fall back for the single request
    but do NOT mark the primary as degraded.

    Mid-stream failures (primary already emitted chunks) are NOT caught —
    falling back mid-sentence would cause duplicate output.
    """

    def __init__(
        self,
        primary_llm: LLM,                # tried first (the cloud LLM)
        fallback_llm: LLM,               # used when the primary has a temporary problem (Ollama)
        cooldown_seconds: float = 60.0,  # how long to skip the cloud after a temporary failure
    ) -> None:
        super().__init__()
        self.primary_llm = primary_llm
        self.fallback_llm = fallback_llm
        # Shared state, read and written by FallbackLLMStream._run():
        self._primary_degraded = False     # True = cloud recently failed, skip it for now
        self._last_degraded_time = 0.0     # when the cloud last failed (monotonic clock)
        self._cooldown_seconds = cooldown_seconds

    @property
    def model(self) -> str:
        # Report the model name of whichever LLM is currently in use (for logs / metrics).
        active = self.fallback_llm if self._primary_degraded else self.primary_llm
        return getattr(active, "model", "fallback-llm")

    def chat(
        self,
        *,
        chat_ctx: ChatContext,
        tools=None,
        conn_options: APIConnectOptions = APIConnectOptions(),
        **kwargs,
    ) -> LLMStream:
        """Called by LiveKit once per LLM turn. Returns a FallbackLLMStream,
        whose _run() decides between the cloud LLM and Ollama."""
        return FallbackLLMStream(
            self,
            chat_ctx=chat_ctx,
            tools=tools,
            conn_options=conn_options,
            **kwargs,
        )