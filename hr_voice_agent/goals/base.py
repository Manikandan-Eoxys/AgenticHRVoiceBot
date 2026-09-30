"""
goals/base.py

Abstract base class for all Goal Service classes.

Every goal in the HR Voice Agent is represented as a GoalService subclass.
Each subclass:
  1. Declares its goal metadata (id, name, description, trigger_phrases)
  2. Exposes its actions as @function_tool decorated methods (or imports
     them from the existing tools/ layer and re-exports them)
  3. Provides a tools() method that returns the flat list of @function_tool
     callables to pass to Agent(tools=...)

Design principles:
  - Goal is the unit of enable/disable (toggle self.enabled = False)
  - Goal is the unit of logging (every tool call is tagged with goal_id)
  - Goal is the unit of rate-limiting / auth (future extension point)
  - Goal → Service mapping mirrors the builder UI exactly
"""

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Callable


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

    Subclass this to create a new goal:

        class LeaveBalanceService(GoalService):
            meta = GoalMeta(
                id="g02_leave_balance",
                name="Leave Balance Inquiry",
                description="Lets employees check their leave balances.",
                trigger_phrases=["leave balance", "how many leaves"],
            )

            def tools(self) -> list[Callable]:
                return [get_leave_balance, get_all_leave_balances]
    """

    meta: GoalMeta  # must be defined as a class attribute on each subclass

    @abstractmethod
    def tools(self) -> list[Callable]:
        """Return the list of @function_tool callables for this goal."""
        ...

    def is_enabled(self) -> bool:
        return getattr(self, "meta", None) is not None and self.meta.enabled

    def disable(self) -> None:
        if hasattr(self, "meta"):
            self.meta.enabled = False

    def enable(self) -> None:
        if hasattr(self, "meta"):
            self.meta.enabled = True

    def tool_names(self) -> list[str]:
        return [getattr(t, "__name__", repr(t)) for t in self.tools()]

    def __repr__(self) -> str:
        meta = getattr(self, "meta", None)
        return (
            f"<{self.__class__.__name__} "
            f"id={meta.id if meta else '?'} "
            f"enabled={meta.enabled if meta else '?'} "
            f"tools={len(self.tools())}>"
        )
