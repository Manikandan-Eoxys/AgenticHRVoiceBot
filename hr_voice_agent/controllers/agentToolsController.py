"""
controllers/agentToolsController.py

Domain Business Logic & Tool Controller for HR Voice Agent.

Responsibilities:
  1. Manages all Goal Services (Domain Business Logic) across the application:
     - Identity verification & authentication
     - Leave balance, applications, and status
     - Attendance & regularizations
     - Work From Home (WFH) quotas and applications
     - Payroll & salary information
     - HR policies, conduct, grievances, and ticketing
     - Calendar & holiday lookups
     - Reminders & policy acknowledgements
     - Employee benefits & insurance
     - Coordinator call transfer
  2. Database tool controller (declarative SQL table lookups, mutations).
  3. Third-party declarative data sources (REST API integrations, IFS Cloud, SQL Server).
  4. Unified tool registry & deduplication for LiveKit LLM ToolContext.
"""

from __future__ import annotations

import logging
from typing import Any, Callable, Dict, List, Optional, Type
from livekit.agents import function_tool

from datasources import get_data_source_adapter
from datasources.base import BaseDataSourceAdapter
from datasources.sql_table_source import SqlTableSource
from datasources.rest_api_source import RestApiSource
from goals import (
    GoalService,
    ALL_GOAL_CLASSES,
    INBOUND_GOALS,
    OUTBOUND_LEAVE_VERIFICATION_GOALS,
    OUTBOUND_REMINDER_GOALS,
    IdentityVerificationService,
    LeaveBalanceService,
    LeaveApplicationService,
    LeaveStatusService,
    AttendanceService,
    WorkFromHomeService,
    PayrollService,
    HRPolicyService,
    CalendarService,
    RemindersService,
    EmployeeBenefitsService,
    LeaveVerificationOutboundService,
    CoordinatorTransferService,
)

logger = logging.getLogger("controllers.agent_tools")


def _extract_tool_name(tool_callable: Any) -> str:
    """
    Helper function to cleanly extract a readable function name from a tool.
    Handles LiveKit @function_tool objects as well as standard Python functions.
    """
    # 1. LiveKit @function_tool stores metadata in .info.name
    info = getattr(tool_callable, "info", None)
    if info and hasattr(info, "name") and info.name:
        return info.name

    # 2. Standard Python function has __name__
    if hasattr(tool_callable, "__name__"):
        return tool_callable.__name__

    # 3. Fallback to string representation
    return str(tool_callable)


class AgentToolsController:
    """
    Central Tool & Domain Business Logic Controller.

    Owns all Goal Services, function tools, declarative database tools,
    and third-party integrations, providing a single consolidated tool context
    to the Voice Assistant.
    """

    def __init__(
        self,
        goal_classes: Optional[list[Type[GoalService]]] = None,
        call_type: str = "inbound",
        job_ctx: Any = None,
        collector: Any = None,
        call_metadata: Optional[dict] = None,
    ) -> None:
        self.call_type: str = call_type
        self.job_ctx: Any = job_ctx
        self.collector: Any = collector
        self.call_metadata: dict = call_metadata or {}
        self.goal_classes: list[Type[GoalService]] = goal_classes or ALL_GOAL_CLASSES

        # 1. Domain Goal Services (Business Logic)
        self._services: dict[str, GoalService] = {}
        self._init_goals(
            goal_classes=self.goal_classes,
            job_ctx=self.job_ctx,
            collector=self.collector,
            call_metadata=self.call_metadata,
        )

        # 2. Third-Party & DB Tools
        self._third_party_tools: list[Callable] = []
        self._db_tools: list[Callable] = []
        self._declarative_tools: list[Callable] = []
        self._loaded: bool = False

    # ── Goal & Service Initialization ─────────────────────────────────────
    def _instantiate_goal(
        self,
        goal_cls: Type[GoalService],
        job_ctx: Any = None,
        collector: Any = None,
        call_metadata: Optional[dict] = None,
    ) -> GoalService:
        """Instantiate a GoalService with call runtime context."""
        if goal_cls is IdentityVerificationService:
            return goal_cls(collector=collector)
        if goal_cls is CoordinatorTransferService:
            return goal_cls(job_ctx=job_ctx)
        return goal_cls()

    def _init_goals(
        self,
        goal_classes: list[Type[GoalService]],
        job_ctx: Any = None,
        collector: Any = None,
        call_metadata: Optional[dict] = None,
    ) -> None:
        # Initialize an empty dictionary to hold all active goal services: {goal_id: service_instance}
        self._services = {}

        # Loop through each goal class (e.g. IdentityVerificationService, LeaveBalanceService)
        for goal_cls in goal_classes:
            # Create a live instance of the goal service with this call's context
            svc = self._instantiate_goal(
                goal_cls,
                job_ctx=job_ctx,
                collector=collector,
                call_metadata=call_metadata,
            )
            # Store it using its unique ID as key (e.g. "g01_identity", "g02_leave_balance")
            self._services[svc.meta.id] = svc

    def setup_call(
        self,
        call_type: Optional[str] = None,
        job_ctx: Any = None,
        collector: Any = None,
        call_metadata: Optional[dict] = None,
    ) -> None:
        """
        Configures the Tools Controller for an active call session,
        re-binding GoalServices and call-specific context (e.g. DTMF collector).
        """
        mapping = {
            "inbound": INBOUND_GOALS,
            "leave_verification": OUTBOUND_LEAVE_VERIFICATION_GOALS,
            "scheduled_reminder": OUTBOUND_REMINDER_GOALS,
            "policy_acknowledgement": OUTBOUND_REMINDER_GOALS,
        }
        self.call_type = call_type or "inbound"
        self.job_ctx = job_ctx
        self.collector = collector
        self.call_metadata = call_metadata or {}
        self.goal_classes = mapping.get(self.call_type, INBOUND_GOALS)

        self._init_goals(
            goal_classes=self.goal_classes,
            job_ctx=job_ctx,
            collector=collector,
            call_metadata=self.call_metadata,
        )

        try:
            from controllers.agentConfigController import AgentConfigController
            cfg = AgentConfigController.instance().get_agent_config()
            self.apply_config(cfg)
        except Exception as e:
            logger.debug(f"[AgentToolsController] Config apply skipped: {e}")

    # ── Service Accessors ─────────────────────────────────────────────────
    @property
    def services(self) -> dict[str, GoalService]:
        """All GoalService instances keyed by goal ID."""
        return self._services

    @property
    def goal_ids(self) -> list[str]:
        """All registered goal IDs."""
        return list(self._services.keys())

    @property
    def active_goal_ids(self) -> list[str]:
        """IDs of currently enabled goals."""
        return [gid for gid, svc in self._services.items() if svc.is_enabled()]

    def get_service(self, goal_id: str) -> Optional[GoalService]:
        """Retrieve a GoalService instance by goal ID (e.g. 'g01_identity')."""
        return self._services.get(goal_id)

    def get_goal(self, goal_id: str) -> Optional[GoalService]:
        """Alias for get_service."""
        return self.get_service(goal_id)

    @property
    def identity_service(self) -> Optional[IdentityVerificationService]:
        svc = self._services.get("g01_identity")
        return svc if isinstance(svc, IdentityVerificationService) else None

    @property
    def leave_balance_service(self) -> Optional[LeaveBalanceService]:
        svc = self._services.get("g02_leave_balance")
        return svc if isinstance(svc, LeaveBalanceService) else None

    @property
    def leave_application_service(self) -> Optional[LeaveApplicationService]:
        svc = self._services.get("g03_leave_application")
        return svc if isinstance(svc, LeaveApplicationService) else None

    @property
    def leave_status_service(self) -> Optional[LeaveStatusService]:
        svc = self._services.get("g04_leave_status")
        return svc if isinstance(svc, LeaveStatusService) else None

    @property
    def attendance_service(self) -> Optional[AttendanceService]:
        svc = self._services.get("g05_attendance")
        return svc if isinstance(svc, AttendanceService) else None

    @property
    def wfh_service(self) -> Optional[WorkFromHomeService]:
        svc = self._services.get("g06_wfh")
        return svc if isinstance(svc, WorkFromHomeService) else None

    @property
    def payroll_service(self) -> Optional[PayrollService]:
        svc = self._services.get("g07_payroll")
        return svc if isinstance(svc, PayrollService) else None

    @property
    def hr_policy_service(self) -> Optional[HRPolicyService]:
        svc = self._services.get("g08_hr_policy")
        return svc if isinstance(svc, HRPolicyService) else None

    @property
    def calendar_service(self) -> Optional[CalendarService]:
        svc = self._services.get("g09_calendar")
        return svc if isinstance(svc, CalendarService) else None

    @property
    def reminders_service(self) -> Optional[RemindersService]:
        svc = self._services.get("g10_reminders")
        return svc if isinstance(svc, RemindersService) else None

    @property
    def insurance_service(self) -> Optional[EmployeeBenefitsService]:
        svc = self._services.get("g11_benefits")
        return svc if isinstance(svc, EmployeeBenefitsService) else None

    @property
    def coordinator_service(self) -> Optional[CoordinatorTransferService]:
        svc = self._services.get("g13_coordinator")
        return svc if isinstance(svc, CoordinatorTransferService) else None

    @property
    def outbound_verification_service(self) -> Optional[LeaveVerificationOutboundService]:
        svc = self._services.get("g12_leave_verification_outbound")
        return svc if isinstance(svc, LeaveVerificationOutboundService) else None

    # ── Goal Enablement & Introspection ───────────────────────────────────
    def disable_goal(self, goal_id: str) -> None:
        """Disables all tools for the specified goal at runtime."""
        svc = self._services.get(goal_id)
        if svc:
            svc.disable()
            logger.info(f"🔕 [AgentToolsController] Goal '{goal_id}' disabled.")
        else:
            logger.warning(f"⚠️ [AgentToolsController] Cannot disable unknown goal '{goal_id}'.")

    def enable_goal(self, goal_id: str) -> None:
        """Re-enables a previously disabled goal."""
        svc = self._services.get(goal_id)
        if svc:
            svc.enable()
            logger.info(f"🔔 [AgentToolsController] Goal '{goal_id}' enabled.")
        else:
            logger.warning(f"⚠️ [AgentToolsController] Cannot enable unknown goal '{goal_id}'.")

    def is_goal_enabled(self, goal_id: str) -> bool:
        """Checks if a specific goal is currently active/enabled."""
        svc = self._services.get(goal_id)
        return bool(svc and svc.is_enabled())

    def get_goal_tools(self) -> list[Callable]:
        """Builds and returns all @function_tool callables from every currently enabled GoalService."""
        tools: list[Callable] = []
        for svc in self._services.values():
            if svc.is_enabled():
                tools.extend(svc.tools())
        return tools

    def get_tools_for_goals(self, *goal_ids: str) -> list[Callable]:
        """Builds and returns tools only from the specified goal IDs."""
        tools: list[Callable] = []
        for gid in goal_ids:
            svc = self._services.get(gid)
            if svc and svc.is_enabled():
                tools.extend(svc.tools())
        return tools

    # ── Third-Party & DB Tool Registration ────────────────────────────────
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

    # ── Master Tool Registry (Consolidated & Deduplicated) ─────────────────
    def get_all_tools(self) -> list[Callable]:
        """
        Compiles and returns the full deduplicated tool list for the voice agent session:
        GoalService action tools (Domain Business Logic) + DB tools + Third-Party tools.
        Guarantees unique function names to prevent LiveKit duplicate tool registration errors.
        """
        raw_tools: list[Callable] = self.get_goal_tools()
        raw_tools.extend(self._db_tools)

        seen_registered = set(id(t) for t in self._db_tools)
        for t in self._third_party_tools:
            if id(t) not in seen_registered:
                raw_tools.append(t)
                seen_registered.add(id(t))
        for t in self._declarative_tools:
            if id(t) not in seen_registered:
                raw_tools.append(t)
                seen_registered.add(id(t))

        # Deduplicate tools by name to avoid duplicate registration errors
        seen_names = set()
        unique_tools = []
        for tool_fn in raw_tools:
            name = _extract_tool_name(tool_fn)
            if name not in seen_names:
                seen_names.add(name)
                unique_tools.append(tool_fn)
            else:
                logger.warning(f"[AgentToolsController] Skipping duplicate tool '{name}'")

        if not self._loaded:
            self._loaded = True
            logger.info(f"🔧 [AgentToolsController] {len(unique_tools)} tools active for session.")

        return unique_tools

    def get_tool_names(self) -> list[str]:
        """Returns sorted list of all registered tool function names in the registry."""
        names = [_extract_tool_name(tool_fn) for tool_fn in self.get_all_tools()]
        return sorted(list(set(names)))

    def get_tool_count(self) -> int:
        """Returns total count of registered tools in the registry."""
        return len(self.get_all_tools())

    def get_goal_summary(self) -> list[dict]:
        """Returns a list of goal status dicts from the registry (for builder UI / monitoring)."""
        return [
            {
                "id": svc.meta.id,
                "name": svc.meta.name,
                "enabled": svc.is_enabled(),
                "tool_count": len(svc.tools()),
                "tools": svc.tool_names(),
            }
            for svc in self._services.values()
        ]

    def log_get_manifest(self) -> None:
        """Logs the full tool registry manifest grouped by goal, plus third-party & DB tools."""
        logger.info("📋 [AgentToolsController] Goal → Tool registry manifest:")
        logger.info(f"  {'GOAL ID':<35} {'STATUS':<10} TOOLS")
        logger.info("  " + "-" * 75)
        for gid, svc in self._services.items():
            status = "enabled " if svc.is_enabled() else "DISABLED"
            names = ", ".join(svc.tool_names()) if svc.is_enabled() else "-"
            logger.info(f"  {gid:<35} {status:<10} {names}")
        if self._db_tools or self._third_party_tools:
            ext_tools = self._db_tools + self._third_party_tools
            ext_names = ", ".join(getattr(t, "__name__", str(t)) for t in ext_tools)
            logger.info(f"  {'db_and_third_party':<35} {'enabled ':<10} {ext_names}")
        logger.info(
            f"  Total: {self.get_tool_count()} tools across {len(self.active_goal_ids)} active goals."
        )

    def apply_config(self, agent_config: Any) -> None:
        """
        Applies goals enablement from AgentConfig to GoalServices,
        and compiles declarative third-party and DB actions.
        """
        if not agent_config:
            return

        # 1. Sync goal enabled states
        goal_states = {g.id: g.enabled for g in getattr(agent_config, "goals", [])}
        for gid, svc in self._services.items():
            if gid in goal_states:
                if goal_states[gid]:
                    svc.enable()
                else:
                    svc.disable()

        # 2. Compile declarative actions
        self._declarative_tools = []
        try:
            for act in getattr(agent_config, "actions", []):
                if not getattr(act, "enabled", True):
                    continue
                self.register_declarative_action(act)
        except Exception as e:
            logger.debug(f"[AgentToolsController] Declarative tools compilation note: {e}")


__all__ = ["AgentToolsController"]
