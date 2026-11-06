"""
datasources/none_source.py

No-op data source adapter for purely conversational or in-memory actions.
"""

from typing import Any, Dict
from datasources.base import BaseDataSourceAdapter


class NoneSource(BaseDataSourceAdapter):
    """Fallback / no-op data source adapter."""

    async def execute(self, params: Dict[str, Any]) -> Any:
        return {"status": "ok", "params": params}
