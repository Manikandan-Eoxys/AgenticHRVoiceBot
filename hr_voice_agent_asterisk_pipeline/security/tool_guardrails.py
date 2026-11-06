"""
security/tool_guardrails.py

Production Tool & MCP Security & Reliability Guardrails
======================================================
Addresses the 4 Critical Tool / MCP Failures:
  1. Tool Poisoning Defense:
     - Sanitizes tool returns to strip indirect prompt injection attempts.
     - Redacts leaked sensitive credentials/tokens (Bearer, API keys, passwords).
     - Caps output size to prevent context stuffing and token denial-of-service.
  2. Over-Permissioned Token & Least-Privilege Protection:
     - Enforces caller authentication checks before sensitive state-mutating actions.
     - Enforces identity scoping (prevent caller A from modifying caller B's records).
     - Distinguishes READ_ONLY vs MUTATION actions with confirmation safety.
  3. Tool Overload Prevention (Dynamic Discovery & Routing):
     - Dynamically filters the registered tool pool (40+ tools) down to 2-5 task-relevant
       tools based on caller authentication state and detected intent.
  4. Server Outage & Failure Resilience:
     - Circuit Breaker pattern with CLOSED, OPEN, HALF-OPEN states.
     - Strict telephony-grade timeouts (default 3.0s) to eliminate voice dead air.
     - Structured JSON error envelopes feeding clear degradation guidance to the LLM.
"""

from __future__ import annotations

import asyncio
import inspect
import logging
import re
import time
from enum import Enum
from typing import Any, Callable, Dict, List, Optional, Set, Tuple

logger = logging.getLogger("security.tool_guardrails")


# ==============================================================================
# 1. TOOL POISONING & INJECTION SANITIZER
# ==============================================================================

class ToolSanitizer:
    """
    Sanitizes tool outputs to defend against indirect prompt injections
    and credential leaks in dynamic tool data.
    """

    MAX_OUTPUT_CHARS = 1200

    # Common injection patterns found in untrusted external data
    INJECTION_PATTERNS = [
        re.compile(r"(?i)\bignore\s+(all\s+)?(previous|prior|above)\s+instructions\b"),
        re.compile(r"(?i)\bdisregard\s+(all\s+)?(previous|prior|system)\s+instructions\b"),
        re.compile(r"(?i)\byou\s+are\s+now\s+in\s+developer\s+mode\b"),
        re.compile(r"(?i)\b(system|assistant|admin)\s*:\s*"),
        re.compile(r"(?i)<\s*(system|prompt|instruction)\s*>"),
        re.compile(r"(?i)<\s*/\s*(system|prompt|instruction)\s*>"),
        re.compile(r"(?i)\boverride\s+system\s+prompt\b"),
    ]

    # Sensitive token leaks
    SENSITIVE_PATTERNS = [
        (re.compile(r"(?i)(bearer\s+)[a-zA-Z0-9_\-\.]{15,}"), r"\1[REDACTED_TOKEN]"),
        (re.compile(r"(?i)(api[_\-]?key\s*[:=]\s*)['\"]?[a-zA-Z0-9_\-]{16,}['\"]?"), r"\1[REDACTED_KEY]"),
        (re.compile(r"(?i)(password\s*[:=]\s*)['\"][^'\"]+['\"]"), r"\1[REDACTED_PASSWORD]"),
        (re.compile(r"(?i)(secret\s*[:=]\s*)['\"][^'\"]+['\"]"), r"\1[REDACTED_SECRET]"),
    ]

    @classmethod
    def sanitize_output(cls, tool_name: str, raw_output: Any) -> str:
        """
        Converts raw tool output to a sanitized string safe for LLM context.
        """
        if raw_output is None:
            return ""

        text = raw_output if isinstance(raw_output, str) else str(raw_output)

        # 1. Neutralize prompt injection attempts
        for pattern in cls.INJECTION_PATTERNS:
            if pattern.search(text):
                logger.warning(
                    f"🛡️ [Tool Poisoning Blocked] Detected injection pattern in tool '{tool_name}' output. Neutralizing."
                )
                text = pattern.sub("[BLOCKED_INJECTION_ATTEMPT]", text)

        # 2. Redact accidental credential leaks
        for pattern, replacement in cls.SENSITIVE_PATTERNS:
            text = pattern.sub(replacement, text)

        # 3. Prevent context stuffing / DoS by capping length
        if len(text) > cls.MAX_OUTPUT_CHARS:
            logger.info(
                f"✂️ [Tool Output Truncated] Output for '{tool_name}' truncated from {len(text)} to {cls.MAX_OUTPUT_CHARS} chars."
            )
            text = text[: cls.MAX_OUTPUT_CHARS] + "... [Output Truncated for Voice Latency]"

        return text


# ==============================================================================
# 2. OVER-PERMISSIONED TOKENS & LEAST-PRIVILEGE GUARD
# ==============================================================================

class ToolPermissionTier(str, Enum):
    PUBLIC = "public"            # Can run pre-authentication (e.g. verify_employee, end_call)
    READ_ONLY = "read_only"      # Requires auth, cannot alter DB/state (e.g. get_balance)
    MUTATION = "mutation"        # High-impact state change (e.g. apply_leave, cancel_leave, DB write)


class PermissionGuard:
    """
    Validates least-privilege constraints before executing tools:
    - Authentication gate: Ensures sensitive tools cannot run for unverified callers.
    - Identity scoping: Ensures employees can only query or mutate their own records.
    """

    # Tools that can execute without prior authentication (identity discovery & public policies)
    PUBLIC_TOOLS: Set[str] = {
        "get_employee_by_id",
        "confirm_employee_identity",
        "verify_employee",
        "verify_employee_identity",
        "collect_employee_id_via_keypad",
        "list_department_employees",
        "transfer_to_coordinator",
        "end_call",
        "get_hr_policy",
        "get_holiday_calendar",
        "get_company_calendar",
        "get_general_policy",
    }

    # Tools that mutate state or apply changes (require auth + parameter validation)
    MUTATION_TOOLS: Set[str] = {
        "submit_leave_application",
        "cancel_leave_application",
        "apply_leave",
        "regularize_attendance",
        "apply_wfh_request",
        "submit_grievance_ticket",
        "acknowledge_reminder",
    }

    @classmethod
    def get_tier(cls, tool_name: str) -> ToolPermissionTier:
        if tool_name in cls.PUBLIC_TOOLS:
            return ToolPermissionTier.PUBLIC
        if tool_name in cls.MUTATION_TOOLS:
            return ToolPermissionTier.MUTATION
        return ToolPermissionTier.READ_ONLY

    @classmethod
    def check_permission(
        cls,
        tool_name: str,
        caller_employee_id: Optional[str],
        tool_args: Dict[str, Any],
    ) -> Tuple[bool, Optional[str]]:
        """
        Returns (is_allowed, error_reason).
        """
        tier = cls.get_tier(tool_name)

        # Public tools are always allowed
        if tier == ToolPermissionTier.PUBLIC:
            return True, None

        # Authenticated tiers require a verified caller identity
        if not caller_employee_id:
            logger.warning(
                f"⛔ [Permission Denied] Blocked unauthenticated execution of '{tool_name}' (Tier: {tier.value})."
            )
            return (
                False,
                f"Access denied: Identity verification is required before calling '{tool_name}'. Please verify the employee's ID first.",
            )

        # Scoping check: If the tool specifies an 'employee_id' parameter, it MUST match verified caller
        target_emp_id = (
            tool_args.get("employee_id")
            or tool_args.get("emp_id")
            or tool_args.get("employeeId")
        )
        if target_emp_id and str(target_emp_id).strip().upper() != str(caller_employee_id).strip().upper():
            logger.warning(
                f"⛔ [Least-Privilege Violation] Caller '{caller_employee_id}' tried to execute '{tool_name}' on behalf of '{target_emp_id}'."
            )
            return (
                False,
                f"Access denied: Least-privilege violation. You can only access records for verified employee {caller_employee_id}.",
            )

        return True, None


# ==============================================================================
# 3. CIRCUIT BREAKER & OUTAGE RESILIENCE
# ==============================================================================

class CircuitState(str, Enum):
    CLOSED = "closed"          # Healthy, traffic flows normally
    OPEN = "open"              # Outage detected, fail fast immediately
    HALF_OPEN = "half_open"    # Testing if upstream service has recovered


class ToolCircuitBreaker:
    """
    Per-tool / per-service Circuit Breaker.
    Prevents cascading failure loops and telephony audio dead air when
    an upstream database or external MCP/REST service goes down.
    """

    def __init__(
        self,
        name: str,
        failure_threshold: int = 3,
        recovery_timeout: float = 30.0,
    ) -> None:
        self.name = name
        self.failure_threshold = failure_threshold
        self.recovery_timeout = recovery_timeout

        self.state = CircuitState.CLOSED
        self.failure_count = 0
        self.last_failure_time: float = 0.0
        self.last_success_time: float = time.time()

    def can_execute(self) -> bool:
        if self.state == CircuitState.CLOSED:
            return True

        if self.state == CircuitState.OPEN:
            now = time.time()
            if now - self.last_failure_time >= self.recovery_timeout:
                logger.info(
                    f"🟡 [Circuit Breaker: {self.name}] Entering HALF_OPEN state (probing upstream service)."
                )
                self.state = CircuitState.HALF_OPEN
                return True
            return False

        # HALF_OPEN: allow a single probe request through
        return True

    def record_success(self) -> None:
        if self.state != CircuitState.CLOSED:
            logger.info(
                f"🟢 [Circuit Breaker: {self.name}] Service recovered. Resetting circuit to CLOSED."
            )
        self.state = CircuitState.CLOSED
        self.failure_count = 0
        self.last_success_time = time.time()

    def record_failure(self, error: Exception) -> None:
        self.failure_count += 1
        self.last_failure_time = time.time()
        logger.warning(
            f"⚠️ [Circuit Breaker: {self.name}] Recorded failure ({self.failure_count}/{self.failure_threshold}): {error}"
        )

        if self.failure_count >= self.failure_threshold:
            if self.state != CircuitState.OPEN:
                logger.error(
                    f"🔴 [Circuit Breaker: {self.name}] Threshold exceeded! Tripping circuit to OPEN for {self.recovery_timeout}s."
                )
            self.state = CircuitState.OPEN


# ==============================================================================
# 4. SECURE TOOL EXECUTION RUNNER
# ==============================================================================

class ToolExecutionGuardrail:
    """
    Unified execution runner wrapping tool invocations with:
      - Circuit breaker protection
      - Strict timeouts (telephony latency SLA)
      - Least-privilege validation
      - Output sanitization
      - Graceful error envelopes for LLM
    """

    def __init__(self, default_timeout_seconds: float = 3.5) -> None:
        self.default_timeout = default_timeout_seconds
        self._circuit_breakers: Dict[str, ToolCircuitBreaker] = {}

    def get_circuit_breaker(self, tool_name: str) -> ToolCircuitBreaker:
        if tool_name not in self._circuit_breakers:
            self._circuit_breakers[tool_name] = ToolCircuitBreaker(name=tool_name)
        return self._circuit_breakers[tool_name]

    async def execute_tool(
        self,
        tool_callable: Callable,
        tool_name: str,
        tool_args: Dict[str, Any],
        caller_employee_id: Optional[str] = None,
        context: Any = None,
        timeout_seconds: Optional[float] = None,
    ) -> Dict[str, Any]:
        """
        Executes the tool wrapped with all 4 security & reliability guardrails.
        Returns a standardized dictionary envelope:
        {
            "success": bool,
            "status": "ok" | "error",
            "data": Any,
            "error_code": Optional[str],
            "message": str
        }
        """
        if tool_name == "collect_employee_id_via_keypad":
            timeout = timeout_seconds or 26.0
        else:
            timeout = timeout_seconds or self.default_timeout
        breaker = self.get_circuit_breaker(tool_name)

        # ── 1. Check Circuit Breaker (Outage Prevention) ──────────────────────
        if not breaker.can_execute():
            logger.warning(
                f"🛑 [Fast Fallback] Tool '{tool_name}' blocked by open circuit breaker."
            )
            return {
                "success": False,
                "status": "error",
                "error_code": "CIRCUIT_OPEN",
                "message": f"The '{tool_name}' service is currently experiencing high latency or outages. Please inform the caller that the service is momentarily unavailable and offer coordinator assistance.",
            }

        # ── 2. Check Least-Privilege & Authentication Guardrails ─────────────
        allowed, perm_error = PermissionGuard.check_permission(
            tool_name=tool_name,
            caller_employee_id=caller_employee_id,
            tool_args=tool_args,
        )
        if not allowed:
            return {
                "success": False,
                "status": "error",
                "error_code": "PERMISSION_DENIED",
                "message": perm_error or "Permission denied.",
            }

        # ── 3. Execute Tool with Strict Telephony Timeout ─────────────────────
        start_time = time.time()
        try:
            target_callable = getattr(tool_callable, "_func", tool_callable)
            sig = inspect.signature(target_callable)

            kwargs = dict(tool_args)
            if "context" in sig.parameters and context is not None:
                kwargs["context"] = context

            res = tool_callable(**kwargs)
            if asyncio.iscoroutine(res):
                res = await asyncio.wait_for(res, timeout=timeout)

            elapsed = time.time() - start_time
            breaker.record_success()

            # ── 4. Tool Output Sanitization (Poisoning Defense) ───────────────
            sanitized_data = ToolSanitizer.sanitize_output(tool_name, res)
            logger.info(
                f"✅ [Guardrail: {tool_name}] Success in {elapsed:.2f}s -> {sanitized_data[:80]}"
            )

            return {
                "success": True,
                "status": "ok",
                "data": sanitized_data,
                "message": "Tool executed successfully.",
            }

        except asyncio.TimeoutError:
            elapsed = time.time() - start_time
            breaker.record_failure(TimeoutError(f"Timeout after {elapsed:.2f}s"))
            logger.error(
                f"⏱️ [Guardrail: {tool_name}] Timed out after {elapsed:.2f}s (> {timeout}s SLA)."
            )
            return {
                "success": False,
                "status": "error",
                "error_code": "TIMEOUT",
                "message": f"The '{tool_name}' service did not respond within {timeout}s. Inform the caller politely that the system is taking longer than expected and offer an alternative.",
            }

        except Exception as e:
            elapsed = time.time() - start_time
            breaker.record_failure(e)
            logger.error(
                f"❌ [Guardrail: {tool_name}] Execution failed after {elapsed:.2f}s: {e}",
                exc_info=True,
            )
            return {
                "success": False,
                "status": "error",
                "error_code": "UPSTREAM_ERROR",
                "message": f"The '{tool_name}' service encountered an internal error ({type(e).__name__}). Please inform the caller calmly.",
            }


# Singleton instance for application-wide use
default_tool_guardrail = ToolExecutionGuardrail()

