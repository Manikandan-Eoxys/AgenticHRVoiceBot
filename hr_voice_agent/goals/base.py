import logging
from abc import ABC
from dataclasses import dataclass, field
from typing import Any, Callable

try:
    from livekit.agents.llm.tool_context import FunctionTool
except ImportError:
    FunctionTool = None

logger = logging.getLogger("goals.base")


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
    Base class for all HR Voice Agent goal services.

    Architecture:
      - Each Goal is a GoalService subclass.
      - Each Action is a member method on that subclass decorated with @function_tool.
      - tools() automatically discovers all @function_tool member methods on the instance.
      - Actions call self.log_action(...) for structured per-goal auditing and trace logs.
    """

    meta: GoalMeta  # Must be defined as a class attribute on each subclass

    def __init__(self, **kwargs: Any) -> None:
        self._action_call_counts: dict[str, int] = {}
        for key, value in kwargs.items():
            setattr(self, key, value)

    def tools(self) -> list[Callable]:
        """
        Return the list of @function_tool callables for this goal.
        By default, auto-discovers all member methods decorated with @function_tool.
        """
        discovered: list[Callable] = []
        for name in dir(self):
            if name.startswith("_"):
                continue
            try:
                attr = getattr(self, name)
                if FunctionTool is not None and isinstance(attr, FunctionTool):
                    discovered.append(attr)
                elif hasattr(attr, "__livekit_tool_info") or hasattr(attr, "info"):
                    discovered.append(attr)
            except Exception:
                pass
        return discovered

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
        names = []
        for t in self.tools():
            info = getattr(t, "info", None)
            if info and hasattr(info, "name"):
                names.append(info.name)
            else:
                names.append(getattr(t, "__name__", repr(t)))
        return names

    def __repr__(self) -> str:
        meta = getattr(self, "meta", None)
        return (
            f"<{self.__class__.__name__} "
            f"id={meta.id if meta else '?'} "
            f"enabled={meta.enabled if meta else '?'} "
            f"tools={len(self.tools())}>"
        )

