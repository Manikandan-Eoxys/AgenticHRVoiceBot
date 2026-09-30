"""
datasources package for hr_voice_agent.
Adapters execute actions defined by DataSourceConfig.
"""

from datasources.base import BaseDataSourceAdapter
from datasources.sql_table_source import SqlTableSource
from datasources.rest_api_source import RestApiSource
from datasources.none_source import NoneSource
from models.config_models import DataSourceConfig


def get_data_source_adapter(config: DataSourceConfig) -> BaseDataSourceAdapter:
    """Factory returning the appropriate adapter for a DataSourceConfig."""
    source_type = (config.source_type or "none").lower()
    if source_type == "sql_table":
        return SqlTableSource(config)
    elif source_type == "rest_api":
        return RestApiSource(config)
    else:
        return NoneSource(config)


__all__ = [
    "BaseDataSourceAdapter",
    "SqlTableSource",
    "RestApiSource",
    "NoneSource",
    "get_data_source_adapter",
]
