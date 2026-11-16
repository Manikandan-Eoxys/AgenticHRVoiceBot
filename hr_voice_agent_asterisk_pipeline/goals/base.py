import logging
from abc import ABC
from dataclasses import dataclass, field
from typing import Any, Callable

from asterisk.llm_types import function_tool

logger = logging.getLogger("goals.base")


class RunContext:
    """Asterisk native RunContext passed to goal tool actions."""
    def __init__(self, session: Any = None):
        self.session = session

    async def wait_for_playout(self) -> None:
        if self.session and hasattr(self.session, "tts") and hasattr(self.session.tts, "wait_for_playout"):
            await self.session.tts.wait_for_playout()


@dataclass
class GoalMeta:
    """Metadata for a goal — mirrors the builder UI's goal card."""
    id: str                          # e.g. "g01_identity"
    name: str                        # e.g. "Identity Verification"
    description: str                 # one-line description
    trigger_phrases: list[str] = field(default_factory=list)
    enabled: bool = True


class GoalService(ABC):
    """
    Base class for all Asterisk HR Voice Agent goal services.
    Zero dependency on LiveKit.
    """

    meta: GoalMeta  # Must be defined as a class attribute on each subclass

    def __init__(self, **kwargs: Any) -> None:
        self._action_call_counts: dict[str, int] = {}
        for key, value in kwargs.items():
            setattr(self, key, value)

    def tools(self) -> list[Callable]:
        """
        Collects all action functions in this service that have the @function_tool decorator.
        """
        discovered_tools: list[Callable] = []

        for attr_name in dir(self):
            if attr_name.startswith("_"):
                continue

            try:
                method_obj = getattr(self, attr_name)
                is_function_tool = (
                    hasattr(method_obj, "__is_function_tool__")
                    or hasattr(method_obj, "info")
                )

                if is_function_tool:
                    discovered_tools.append(method_obj)
            except Exception:
                pass

        return discovered_tools

    def log_action(self, action_name: str, **kwargs: Any) -> None:
        """Structured per-goal logging when an action is executed."""
        self._action_call_counts[action_name] = self._action_call_counts.get(action_name, 0) + 1
        goal_id = self.meta.id if hasattr(self, "meta") and self.meta else self.__class__.__name__
        params_str = ", ".join(f"{k}={repr(v)}" for k, v in kwargs.items() if k not in ("context", "job_ctx"))
        logger.info(
            f"🎯 [{goal_id}] {self.__class__.__name__}.{action_name}() invoked "
            f"(call #{self._action_call_counts[action_name]})"
            + (f" | params: {params_str}" if params_str else "")
        )

    def is_action_enabled(self, action_name: str) -> bool:
        return self.meta.enabled if hasattr(self, "meta") else True

    def action_count(self, action_name: str) -> int:
        """Returns how many times a given action was executed."""
        return self._action_call_counts.get(action_name, 0)

    def is_enabled(self) -> bool:
        return getattr(self, "meta", None) is not None and self.meta.enabled

    def disable(self) -> None:
        if hasattr(self, "meta") and self.meta:
            self.meta.enabled = False
            logger.info(f"🔕 Goal '{self.meta.id}' disabled.")

    def enable(self) -> None:
        if hasattr(self, "meta") and self.meta:
            self.meta.enabled = True
            logger.info(f"🔔 Goal '{self.meta.id}' enabled.")

    def tool_names(self) -> list[str]:
        """Returns the list of tool function names registered on this service."""
        names = []
        for tool in self.tools():
            info = getattr(tool, "info", None)
            if info and hasattr(info, "name"):
                names.append(info.name)
            elif hasattr(tool, "__name__"):
                names.append(tool.__name__)
            else:
                names.append(str(tool))
        return names

    def __repr__(self) -> str:
        meta = getattr(self, "meta", None)
        return (
            f"<{self.__class__.__name__} "
            f"id={meta.id if meta else '?'} "
            f"enabled={meta.enabled if meta else '?'} "
            f"tools={len(self.tools())}>"
        )


__all__ = [
    "GoalService",
    "GoalMeta",
    "function_tool",
    "RunContext",
]
