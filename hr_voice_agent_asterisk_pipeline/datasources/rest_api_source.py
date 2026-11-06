"""
datasources/rest_api_source.py

REST API adapter driven by ActionConfig.dataSource.
Executes declarative HTTP requests with configurable authentication,
protected by Circuit Breaker, strict latency timeouts, and output sanitization.
"""

import logging
from typing import Any, Dict
from urllib.parse import urlparse
from datasources.base import BaseDataSourceAdapter
from models.config_models import DataSourceConfig
from security.tool_guardrails import default_tool_guardrail, ToolSanitizer

logger = logging.getLogger("datasources.rest")


class RestApiSource(BaseDataSourceAdapter):
    """
    Executes declarative HTTP API requests.
    Example:
      apiUrl: "https://api.internal/v1/employees/{employee_id}"
      method: "GET"
      authType: "bearer"
      secretRef: "${HR_API_TOKEN}"
    """

    def __init__(self, config: DataSourceConfig) -> None:
        super().__init__(config)
        self.api_url = config.api_url or ""
        self.method = (config.method or "GET").upper()
        self.auth_type = config.auth_type
        self.auth_header = config.auth_header
        self.secret_ref = config.secret_ref
        
        # Determine host for circuit breaker tracking
        parsed = urlparse(self.api_url) if self.api_url else None
        self.circuit_name = f"rest_api_{parsed.netloc or 'unknown'}"
        self.circuit_breaker = default_tool_guardrail.get_circuit_breaker(self.circuit_name)

    async def execute(self, params: Dict[str, Any]) -> Any:
        if not self.api_url:
            return {"status": "error", "error": "No apiUrl specified for REST API data source"}

        # 1. Outage Resilience: Fast-fail if upstream service circuit breaker is open
        if not self.circuit_breaker.can_execute():
            logger.warning(f"🛑 [REST Outage] Tripped circuit breaker for '{self.circuit_name}'. Fast-failing.")
            return {
                "status": "error",
                "error_code": "CIRCUIT_OPEN",
                "error": "Upstream REST service is currently unreachable (circuit open).",
            }

        # Format URL with parameters if URL template contains {param}
        url = self.api_url
        for k, v in params.items():
            if f"{{{k}}}" in url:
                url = url.replace(f"{{{k}}}", str(v))

        headers: Dict[str, str] = {"Content-Type": "application/json"}
        secret = self.config.resolved_secret

        if self.auth_type == "bearer" and secret:
            headers["Authorization"] = f"Bearer {secret}"
        elif self.auth_type == "api_key" and secret:
            header_name = self.auth_header or "X-API-Key"
            headers[header_name] = secret
        elif self.auth_type == "basic" and secret:
            headers["Authorization"] = f"Basic {secret}"

        try:
            import aiohttp
            # Voice-grade timeout: 3.5s SLA to prevent telephony dead-air
            client_timeout = aiohttp.ClientTimeout(total=3.5)
            async with aiohttp.ClientSession(timeout=client_timeout) as session:
                if self.method == "GET":
                    async with session.get(url, headers=headers, params=params) as resp:
                        data = await resp.json() if resp.content_type == "application/json" else await resp.text()
                        self.circuit_breaker.record_success()
                        # Sanitize data against prompt injections / token leaks
                        sanitized = ToolSanitizer.sanitize_output(self.circuit_name, data)
                        return {"status": resp.status, "data": sanitized}
                elif self.method in ("POST", "PUT"):
                    async with session.request(self.method, url, headers=headers, json=params) as resp:
                        data = await resp.json() if resp.content_type == "application/json" else await resp.text()
                        self.circuit_breaker.record_success()
                        sanitized = ToolSanitizer.sanitize_output(self.circuit_name, data)
                        return {"status": resp.status, "data": sanitized}
                else:
                    return {"status": "error", "error": f"Unsupported HTTP method: {self.method}"}
        except Exception as e:
            self.circuit_breaker.record_failure(e)
            logger.error(f"REST API call failed ({url}): {e}")
            return {
                "status": "error",
                "error_code": "SERVICE_UNAVAILABLE",
                "error": f"External REST API failure: {type(e).__name__}",
            }
