"""
agentToolsController.py

Goal-based tool registration hub for the HR Voice Agent.

Architecture
─────────────
Instead of a flat list of functions, tools are grouped by Goal → Service class.
Each Goal is a GoalService subclass; each Action is a @function_tool on that service.

    AgentToolsController
    ├── g01  IdentityVerificationService    → [get_employee_by_id, confirm_employee_identity, ...]
    ├── g02  LeaveBalanceService            → [list_leave_types, get_leave_balance, ...]
    ├── g03  LeaveApplicationService        → [check_leave_availability, confirm_leave_request, ...]
    ├── g04  LeaveStatusService             → [get_leave_request_status, send_manager_leave_reminder]
    ├── g05  AttendanceService              → [check_attendance_status, get_late_marks, ...]
    ├── g06  WorkFromHomeService            → [check_wfh_eligibility, apply_wfh_request, ...]
    ├── g07  PayrollService                 → [get_salary_credit_date, get_basic_salary_info, ...]
    ├── g08  HRPolicyService                → [get_hr_policy, raise_hr_ticket, ...]
    ├── g09  CalendarService                → [check_company_holiday, list_upcoming_company_holidays]
    ├── g10  RemindersService               → [schedule_employee_reminder, ...]
    ├── g11  EmployeeBenefitsService        → [get_employee_schemes, list_insurance_plans, ...]
    └── g12  LeaveVerificationOutboundService (outbound-only)

Benefits
─────────
• Enable/disable an entire goal with one line  →  controller.disable_goal("g06_wfh")
• Per-goal logging / rate-limiting seam
• Call type routing: inbound vs. outbound call types get different goal sets
• Developer trace: "which goal fired?" → go to that service class
• Builder symmetry: goal accordion in UI → GoalService class in code

Call type → tool set mapping
─────────────────────────────
  inbound                → INBOUND_GOALS (all except g12)
  leave_verification     → OUTBOUND_LEAVE_VERIFICATION_GOALS (g01, g12, g04)
  scheduled_reminder     → OUTBOUND_REMINDER_GOALS (g01, g10, g08)
  policy_acknowledgement → OUTBOUND_REMINDER_GOALS (g01, g10, g08)
"""

import logging
from typing import Any, Callable, Optional

from goals import (
    GoalService,
    ALL_GOAL_CLASSES,
    INBOUND_GOALS,
    OUTBOUND_LEAVE_VERIFICATION_GOALS,
    OUTBOUND_REMINDER_GOALS,
)

logger = logging.getLogger("controllers.agent_tools")


class AgentToolsController:
    """
    Goal-based tool registration controller.

    Instantiate once at worker start (or per-call for call-type-specific tool sets).

    Usage:
        # Inbound call — all goals
        ctrl = AgentToolsController.for_call_type("inbound")
        tools = ctrl.get_all_tools()    # pass to Agent(tools=...)

        # Outbound leave verification — restricted goal set
        ctrl = AgentToolsController.for_call_type("leave_verification")
        tools = ctrl.get_all_tools()

        # Disable a specific goal at runtime
        ctrl.disable_goal("g06_wfh")

        # Get only tools for specific goals
        tools = ctrl.get_tools_for_goals("g01_identity", "g02_leave_balance")

        # Audit log at startup
        ctrl.log_manifest()
    """

    def __init__(self, goal_classes: list[type[GoalService]] | None = None) -> None:
        """
        Args:
            goal_classes: Which GoalService subclasses to activate.
                          Defaults to ALL_GOAL_CLASSES (all 12 goals).
        """
        classes = goal_classes or ALL_GOAL_CLASSES
        # Instantiate one service object per goal
        self._services: dict[str, GoalService] = {
            svc.meta.id: svc
            for cls in classes
            for svc in [cls()]
        }
        self._declarative_tools: list[Callable] = []
        self._loaded = False

    # ------------------------------------------------------------------
    # Config Application & Dynamic Declarative Tools
    # ------------------------------------------------------------------

    def apply_config(self, agent_config: Any) -> None:
        """
        Applies goals and actions from AgentConfig model.
        Enables/disables goals and compiles declarative data source actions.
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

        # 2. Register declarative actions with dataSources
        self._declarative_tools = []
        try:
            from datasources import get_data_source_adapter
            from livekit.agents import function_tool

            existing_names = set(self.get_tool_names())

            for act in getattr(agent_config, "actions", []):
                if not act.enabled:
                    continue
                # If tool is already coded in a GoalService, skip creating duplicate
                if act.function_name in existing_names:
                    continue
                # If dataSource is configured (e.g. sql_table, rest_api)
                if act.data_source and act.data_source.source_type != "none":
                    adapter = get_data_source_adapter(act.data_source)
                    fn_name = act.function_name
                    fn_doc = act.instruction or act.trigger or f"Execute action {fn_name}"

                    # Create dynamic async tool
                    async def _declarative_fn(context: Any, **kwargs: Any) -> Any:
                        return await adapter.execute(kwargs)

                    _declarative_fn.__name__ = fn_name
                    _declarative_fn.__doc__ = fn_doc
                    tool_callable = function_tool()(_declarative_fn)
                    self._declarative_tools.append(tool_callable)
                    logger.info(f"✨ [AgentTools] Created declarative tool '{fn_name}' backed by {act.data_source.source_type}")
        except Exception as e:
            logger.debug(f"[AgentTools] Note on declarative tools compilation: {e}")

    # ------------------------------------------------------------------
    # Factory — call-type-aware construction
    # ------------------------------------------------------------------

    @classmethod
    def for_call_type(cls, call_type: str) -> "AgentToolsController":
        """
        Creates a controller with the right goal set for the given call type,
        applying current agent_config.json enabled goals.
        """
        mapping = {
            "inbound": INBOUND_GOALS,
            "leave_verification": OUTBOUND_LEAVE_VERIFICATION_GOALS,
            "scheduled_reminder": OUTBOUND_REMINDER_GOALS,
            "policy_acknowledgement": OUTBOUND_REMINDER_GOALS,
        }
        goal_classes = mapping.get(call_type, INBOUND_GOALS)
        ctrl = cls(goal_classes=goal_classes)

        # Apply agent_config if available
        try:
            from controllers.agentConfigController import AgentConfigController
            cfg = AgentConfigController.instance().get_agent_config()
            ctrl.apply_config(cfg)
        except Exception as err:
            logger.debug(f"[AgentTools] Defaulting to hardcoded goal defaults: {err}")

        logger.info(
            f"🎯 [AgentTools] Created controller for call_type='{call_type}' "
            f"with {len(ctrl.active_goal_ids)} active goals."
        )
        return ctrl

    # ------------------------------------------------------------------
    # Core tool accessors
    # ------------------------------------------------------------------

    def get_all_tools(self) -> list[Callable]:
        """
        Returns a flat list of all @function_tool callables from every
        enabled goal and declarative tool. Pass this directly to Agent(tools=...).
        """
        tools: list[Callable] = []
        for svc in self._services.values():
            if svc.is_enabled():
                tools.extend(svc.tools())
        # Append declarative tools
        tools.extend(self._declarative_tools)

        if not self._loaded:
            self._loaded = True
            logger.info(
                f"🔧 [AgentTools] {len(tools)} tools loaded across "
                f"{sum(1 for s in self._services.values() if s.is_enabled())} enabled goals."
            )
        return tools

    def get_tools_for_goals(self, *goal_ids: str) -> list[Callable]:
        """
        Returns tools only from the specified goal IDs.

        Example:
            ctrl.get_tools_for_goals("g01_identity", "g02_leave_balance")
        """
        tools: list[Callable] = []
        for gid in goal_ids:
            svc = self._services.get(gid)
            if svc is None:
                logger.warning(f"⚠️ [AgentTools] Unknown goal id '{gid}'. Available: {self.goal_ids}")
                continue
            if svc.is_enabled():
                tools.extend(svc.tools())
        return tools

    # ------------------------------------------------------------------
    # Goal enable / disable
    # ------------------------------------------------------------------

    def disable_goal(self, goal_id: str) -> None:
        """Disables all tools for the specified goal at runtime."""
        svc = self._services.get(goal_id)
        if svc:
            svc.disable()
            logger.info(f"🔕 [AgentTools] Goal '{goal_id}' disabled.")
        else:
            logger.warning(f"⚠️ [AgentTools] Cannot disable unknown goal '{goal_id}'.")

    def enable_goal(self, goal_id: str) -> None:
        """Re-enables a previously disabled goal."""
        svc = self._services.get(goal_id)
        if svc:
            svc.enable()
            logger.info(f"🔔 [AgentTools] Goal '{goal_id}' enabled.")
        else:
            logger.warning(f"⚠️ [AgentTools] Cannot enable unknown goal '{goal_id}'.")

    # ------------------------------------------------------------------
    # Introspection
    # ------------------------------------------------------------------

    @property
    def goal_ids(self) -> list[str]:
        return list(self._services.keys())

    @property
    def active_goal_ids(self) -> list[str]:
        return [gid for gid, svc in self._services.items() if svc.is_enabled()]

    def get_goal(self, goal_id: str) -> Optional[GoalService]:
        return self._services.get(goal_id)

    def get_tool_names(self) -> list[str]:
        """Returns sorted list of all enabled tool function names."""
        names = []
        for svc in self._services.values():
            if svc.is_enabled():
                names.extend(svc.tool_names())
        return sorted(names)

    def tool_count(self) -> int:
        return sum(len(svc.tools()) for svc in self._services.values() if svc.is_enabled())

    def goal_summary(self) -> list[dict]:
        """Returns a list of dicts for builder UI / monitoring."""
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

    def log_manifest(self) -> None:
        """Logs the full tool manifest grouped by goal — call at worker startup."""
        logger.info("📋 [AgentTools] Goal → Tool manifest:")
        logger.info(f"  {'GOAL ID':<35} {'STATUS':<10} TOOLS")
        logger.info("  " + "-" * 75)
        for gid, svc in self._services.items():
            status = "enabled " if svc.is_enabled() else "DISABLED"
            names = ", ".join(svc.tool_names()) if svc.is_enabled() else "-"
            logger.info(f"  {gid:<35} {status:<10} {names}")
        logger.info(f"  Total: {self.tool_count()} tools across {len(self.active_goal_ids)} active goals.")
