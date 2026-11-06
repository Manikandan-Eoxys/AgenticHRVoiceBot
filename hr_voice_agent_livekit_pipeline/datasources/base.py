"""
datasources/base.py

Abstract base class for all data source adapters.
Driven by ActionConfig.dataSource in agent_config.json.
"""

from abc import ABC, abstractmethod
from typing import Any, Dict
from models.config_models import DataSourceConfig


class BaseDataSourceAdapter(ABC):
    """Abstract data source adapter."""

    def __init__(self, config: DataSourceConfig) -> None:
        self.config = config

    @abstractmethod
    async def execute(self, params: Dict[str, Any]) -> Any:
        """Executes the data source operation with given invocation parameters."""
        ...
