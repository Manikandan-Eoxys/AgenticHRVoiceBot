"""
datasources/rest_api_source.py

REST API adapter driven by ActionConfig.dataSource.
Executes declarative HTTP requests with configurable authentication.
"""

import logging
from typing import Any, Dict
from datasources.base import BaseDataSourceAdapter
from models.config_models import DataSourceConfig

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

    async def execute(self, params: Dict[str, Any]) -> Any:
        if not self.api_url:
            return {"error": "No apiUrl specified for REST API data source"}

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
            async with aiohttp.ClientSession() as session:
                if self.method == "GET":
                    async with session.get(url, headers=headers, params=params, timeout=10) as resp:
                        data = await resp.json() if resp.content_type == "application/json" else await resp.text()
                        return {"status": resp.status, "data": data}
                elif self.method in ("POST", "PUT"):
                    async with session.request(self.method, url, headers=headers, json=params, timeout=10) as resp:
                        data = await resp.json() if resp.content_type == "application/json" else await resp.text()
                        return {"status": resp.status, "data": data}
                else:
                    return {"error": f"Unsupported HTTP method: {self.method}"}
        except Exception as e:
            logger.error(f"REST API call failed ({url}): {e}")
            return {"status": "error", "error": str(e)}
