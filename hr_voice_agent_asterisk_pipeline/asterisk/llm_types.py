"""
asterisk/llm_types.py

Native LLM data structures and function tool decorator for the Asterisk pipeline.
Completely standalone — zero dependency on LiveKit.
"""

from __future__ import annotations

import asyncio
import inspect
import json
import logging
from dataclasses import dataclass, field
from typing import Any, Callable, Dict, List, Optional, Union

logger = logging.getLogger("asterisk.llm")


# ---------------------------------------------------------------------------
# Exceptions
# ---------------------------------------------------------------------------
class APIConnectionError(Exception):
    pass

class APITimeoutError(Exception):
    pass

class APIStatusError(Exception):
    pass


# ---------------------------------------------------------------------------
# Options
# ---------------------------------------------------------------------------
@dataclass
class APIConnectOptions:
    max_retry: int = 3
    timeout: float = 30.0
    retry_interval: float = 1.0  # seconds to wait between retries


# ---------------------------------------------------------------------------
# Function Tool Decorator
# ---------------------------------------------------------------------------
class ToolInfo:
    def __init__(self, name: str, description: str):
        self.name = name
        self.description = description


def function_tool(
    func: Optional[Callable] = None,
    *,
    name: Optional[str] = None,
    description: Optional[str] = None,
):
    """
    Asterisk-native @function_tool decorator.
    Attaches tool name and description metadata to callable methods.
    """
    def decorator(fn: Callable) -> Callable:
        fn_name = name or fn.__name__
        fn_desc = description or (inspect.getdoc(fn) or "")
        info = ToolInfo(fn_name, fn_desc)
        setattr(fn, "info", info)
        setattr(fn, "__tool_name__", fn_name)
        setattr(fn, "__doc__", fn_desc)
        setattr(fn, "__is_function_tool__", True)
        return fn

    if func is not None:
        return decorator(func)
    return decorator


# ---------------------------------------------------------------------------
# Messages & Chat Context
# ---------------------------------------------------------------------------
@dataclass
class ChatMessage:
    role: str
    content: str = ""
    tool_calls: Optional[List[Any]] = None
    tool_call_id: Optional[str] = None

    @property
    def text_content(self) -> str:
        return self.content or ""


class ChatContext:
    """Manages chat message history for Asterisk turns."""

    def __init__(self, messages: Optional[List[ChatMessage]] = None):
        self.messages: List[ChatMessage] = list(messages or [])

    @property
    def items(self) -> List[ChatMessage]:
        return self.messages

    def add_message(
        self,
        role: str,
        content: str = "",
        tool_calls: Optional[List[Any]] = None,
        tool_call_id: Optional[str] = None,
    ) -> ChatMessage:
        msg = ChatMessage(role=role, content=content, tool_calls=tool_calls, tool_call_id=tool_call_id)
        self.messages.append(msg)
        return msg

    def copy(self) -> ChatContext:
        return ChatContext([ChatMessage(role=m.role, content=m.content, tool_calls=m.tool_calls, tool_call_id=m.tool_call_id) for m in self.messages])

    def to_provider_format(self, format: str = "openai") -> tuple[list[dict], list]:
        """Converts internal messages to OpenAI-compatible dict format."""
        formatted = []
        for m in self.messages:
            msg_dict: Dict[str, Any] = {"role": m.role, "content": m.content}
            if m.tool_calls:
                msg_dict["tool_calls"] = m.tool_calls
            if m.tool_call_id:
                msg_dict["tool_call_id"] = m.tool_call_id
            formatted.append(msg_dict)
        return formatted, []


# ---------------------------------------------------------------------------
# Chunks & Streaming Structures
# ---------------------------------------------------------------------------
@dataclass
class FunctionToolCall:
    id: str
    function: Any


@dataclass
class ChoiceDelta:
    content: Optional[str] = None
    tool_calls: Optional[List[Any]] = None


@dataclass
class CompletionUsage:
    prompt_tokens: int = 0
    completion_tokens: int = 0
    total_tokens: int = 0


@dataclass
class ChatChunk:
    delta: ChoiceDelta = field(default_factory=ChoiceDelta)
    usage: Optional[CompletionUsage] = None


class ToolContext:
    """Extracts JSON schemas from Python tool callables for LLM prompts."""
    def __init__(self, tools: List[Callable]):
        self.tools = tools or []

    def parse_function_tools(self, format: str = "openai") -> List[Dict[str, Any]]:
        schemas: List[Dict[str, Any]] = []
        for t in self.tools:
            info = getattr(t, "info", None)
            name = getattr(info, "name", getattr(t, "__name__", "unknown"))
            desc = getattr(info, "description", inspect.getdoc(t) or "")
            try:
                sig = inspect.signature(t)
                properties: Dict[str, Any] = {}
                required: List[str] = []
                for p_name, p in sig.parameters.items():
                    if p_name in ("self", "context", "job_ctx"):
                        continue
                    p_type = "string"
                    if p.annotation == int:
                        p_type = "integer"
                    elif p.annotation == float:
                        p_type = "number"
                    elif p.annotation == bool:
                        p_type = "boolean"
                    elif p.annotation in (list, List[str]):
                        p_type = "array"
                    properties[p_name] = {"type": p_type}
                    if p.default == inspect.Parameter.empty:
                        required.append(p_name)
                schema = {
                    "type": "function",
                    "function": {
                        "name": name,
                        "description": desc,
                        "parameters": {
                            "type": "object",
                            "properties": properties,
                            "required": required,
                        },
                    },
                }
                schemas.append(schema)
            except Exception:
                pass
        return schemas


# ---------------------------------------------------------------------------
# Base LLM and LLMStream
# ---------------------------------------------------------------------------
class LLM:
    """Base abstract LLM for Asterisk."""
    def chat(
        self,
        *,
        chat_ctx: ChatContext,
        tools: Optional[List[Callable]] = None,
        conn_options: Optional[APIConnectOptions] = None,
    ) -> "LLMStream":
        raise NotImplementedError


class LLMStream:
    """Base async stream for Asterisk LLM generation."""
    def __init__(
        self,
        llm: LLM,
        *,
        chat_ctx: ChatContext,
        tools: Optional[List[Callable]] = None,
        conn_options: Optional[APIConnectOptions] = None,
    ):
        self._llm = llm
        self._chat_ctx = chat_ctx
        self._tools = tools or []
        self._conn_options = conn_options or APIConnectOptions()

    def __aiter__(self):
        return self

    async def __anext__(self) -> ChatChunk:
        raise StopAsyncIteration


class OpenAICompatibleStream(LLMStream):
    """Streams ChatChunk objects from OpenAI-compatible cloud endpoints (OpenRouter, Groq)."""

    def __init__(
        self,
        llm: "OpenAICompatibleLLM",
        *,
        chat_ctx: ChatContext,
        tools: Optional[List[Callable]] = None,
        conn_options: Optional[APIConnectOptions] = None,
    ):
        super().__init__(llm, chat_ctx=chat_ctx, tools=tools, conn_options=conn_options)
        self._queue: asyncio.Queue[Optional[ChatChunk]] = asyncio.Queue()
        self._started = False
        self._task: Optional[asyncio.Task] = None

    async def _run(self) -> None:
        import aiohttp
        try:
            url = f"{self._llm.base_url}/chat/completions"
            headers = {
                "Authorization": f"Bearer {self._llm.api_key}",
                "Content-Type": "application/json",
            }
            messages, _ = self._chat_ctx.to_provider_format("openai")
            payload: Dict[str, Any] = {
                "model": self._llm.model,
                "messages": messages,
                "stream": True,
            }
            if self._tools:
                tools_schema = ToolContext(self._tools).parse_function_tools("openai")
                if tools_schema:
                    payload["tools"] = tools_schema

            timeout = aiohttp.ClientTimeout(total=self._conn_options.timeout)
            async with aiohttp.ClientSession(timeout=timeout) as session:
                async with session.post(url, headers=headers, json=payload) as resp:
                    if resp.status >= 400:
                        err_text = await resp.text()
                        raise APIStatusError(f"HTTP {resp.status}: {err_text}")
                    async for line in resp.content:
                        line_str = line.decode("utf-8").strip()
                        if not line_str.startswith("data:"):
                            continue
                        data_str = line_str[5:].strip()
                        if data_str == "[DONE]":
                            break
                        try:
                            parsed = json.loads(data_str)
                            choices = parsed.get("choices", [])
                            if not choices:
                                continue
                            delta_dict = choices[0].get("delta", {})
                            content = delta_dict.get("content")
                            tool_calls = delta_dict.get("tool_calls")
                            if content or tool_calls:
                                chunk = ChatChunk(delta=ChoiceDelta(content=content, tool_calls=tool_calls))
                                await self._queue.put(chunk)
                        except Exception:
                            continue
        except Exception as exc:
            logger.error(f"[OpenAICompatibleStream] Error: {exc}")
            raise exc
        finally:
            await self._queue.put(None)

    def __aiter__(self):
        if not self._started:
            self._started = True
            self._task = asyncio.create_task(self._run())
        return self

    async def __anext__(self) -> ChatChunk:
        if not self._started:
            self.__aiter__()
        chunk = await self._queue.get()
        if chunk is None:
            raise StopAsyncIteration
        return chunk


class OpenAICompatibleLLM(LLM):
    """OpenAI-compatible cloud LLM (OpenRouter, Groq, OpenAI)."""

    def __init__(self, model: str, api_key: str, base_url: str = "https://api.openai.com/v1"):
        self.model = model
        self.api_key = api_key
        self.base_url = base_url.rstrip("/")

    def chat(
        self,
        *,
        chat_ctx: ChatContext,
        tools: Optional[List[Callable]] = None,
        conn_options: Optional[APIConnectOptions] = None,
    ) -> OpenAICompatibleStream:
        return OpenAICompatibleStream(self, chat_ctx=chat_ctx, tools=tools, conn_options=conn_options)
