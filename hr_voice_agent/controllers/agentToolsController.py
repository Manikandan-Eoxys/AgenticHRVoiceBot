"""
controllers/agentToolsController.py

Third-Party & Database Tool Controller for HR Voice Agent.

Manages:
  1. Third-party declarative data sources (REST API integrations, external services).
  2. Database tool controller (SQL table queries, database mutations).
  3. Dynamic compilation and registration of external third-party function tools.
"""

from __future__ import annotations

import logging
from typing import Any, Callable, Dict, List, Optional
from livekit.agents import function_tool
from datasources import get_data_source_adapter
from datasources.base import BaseDataSourceAdapter
from datasources.sql_table_source import SqlTableSource
from datasources.rest_api_source import RestApiSource

logger = logging.getLogger("controllers.agent_tools")


class AgentToolsController:
    """
    Third-Party & Database Tool Controller.

    Manages third-party REST APIs, external integrations (IFS Cloud, SQL Server),
    and declarative SQL database table tools.
    """

    def __init__(self) -> None:
        self._third_party_tools: list[Callable] = []
        self._db_tools: list[Callable] = []
        self._declarative_tools: list[Callable] = []
        self._loaded: bool = False

    def register_third_party_tool(self, tool_callable: Callable) -> None:
        """Register an external or third-party function tool callable directly."""
        self._third_party_tools.append(tool_callable)
        tname = getattr(tool_callable, "__name__", str(tool_callable))
        logger.info(f"✨ [AgentToolsController] Registered third-party tool: '{tname}'")

    def register_db_tool(self, tool_callable: Callable) -> None:
        """Register a database function tool callable."""
        self._db_tools.append(tool_callable)
        tname = getattr(tool_callable, "__name__", str(tool_callable))
        logger.info(f"✨ [AgentToolsController] Registered DB tool: '{tname}'")

    def register_declarative_action(self, action_config: Any) -> Optional[Callable]:
        """
        Compile and register a third-party declarative action using a data source adapter
        (REST API, SQL Table, or external client).
        """
        if not getattr(action_config, "data_source", None) or action_config.data_source.source_type == "none":
            return None

        adapter = get_data_source_adapter(action_config.data_source)
        fn_name = action_config.function_name
        fn_doc = action_config.instruction or action_config.trigger or f"Execute action {fn_name}"

        async def _declarative_fn(context: Any, **kwargs: Any) -> Any:
            return await adapter.execute(kwargs)

        _declarative_fn.__name__ = fn_name
        _declarative_fn.__doc__ = fn_doc
        tool_callable = function_tool()(_declarative_fn)

        if isinstance(adapter, SqlTableSource):
            self._db_tools.append(tool_callable)
            logger.info(f"✨ [AgentToolsController] Created DB table tool: '{fn_name}'")
        else:
            self._third_party_tools.append(tool_callable)
            logger.info(f"✨ [AgentToolsController] Created third-party API tool: '{fn_name}'")

        self._declarative_tools.append(tool_callable)
        return tool_callable

    def get_third_party_tools(self) -> list[Callable]:
        """Returns all third-party external tools (REST API, external integrations)."""
        return list(self._third_party_tools)

    def get_db_tools(self) -> list[Callable]:
        """Returns all database tools (SQL table lookups, mutations)."""
        return list(self._db_tools)

    def get_all_tools(self) -> list[Callable]:
        """Returns all third-party and database tools."""
        all_tools: list[Callable] = []
        all_tools.extend(self._db_tools)
        seen = set(id(t) for t in self._db_tools)
        for t in self._third_party_tools:
            if id(t) not in seen:
                all_tools.append(t)
                seen.add(id(t))
        for t in self._declarative_tools:
            if id(t) not in seen:
                all_tools.append(t)
                seen.add(id(t))

        if not self._loaded:
            self._loaded = True
            logger.info(f"🔧 [AgentToolsController] {len(all_tools)} third-party & DB tools loaded.")
        return all_tools

    def apply_config(self, agent_config: Any) -> None:
        """Compiles declarative third-party and DB actions from AgentConfig."""
        if not agent_config:
            return

        self._declarative_tools = []
        try:
            for act in getattr(agent_config, "actions", []):
                if not getattr(act, "enabled", True):
                    continue
                self.register_declarative_action(act)
        except Exception as e:
            logger.debug(f"[AgentToolsController] Declarative tools compilation note: {e}")


__all__ = ["AgentToolsController"]
