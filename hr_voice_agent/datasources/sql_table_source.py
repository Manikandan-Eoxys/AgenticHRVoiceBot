"""
datasources/sql_table_source.py

SQL Table adapter driven by ActionConfig.dataSource.
Performs declarative query lookups against MySQL/SQL tables.
"""

import logging
from typing import Any, Dict, Optional
from datasources.base import BaseDataSourceAdapter
from models.config_models import DataSourceConfig

logger = logging.getLogger("datasources.sql")


class SqlTableSource(BaseDataSourceAdapter):
    """
    Executes declarative queries against a configured SQL table.
    Example:
      tableName: "employees"
      columns: ["full_name", "email", "department_id"]
      filterColumn: "employee_id"
    """

    def __init__(self, config: DataSourceConfig) -> None:
        super().__init__(config)
        self.table_name = config.table_name
        self.columns = config.columns
        self.filter_column = config.filter_column

    async def execute(self, params: Dict[str, Any]) -> Any:
        if not self.table_name:
            return {"error": "No table_name specified for SQL data source"}

        # Sanitize column names
        cols_clause = ", ".join(self.columns) if self.columns else "*"

        # Determine filter value
        filter_val = None
        if self.filter_column:
            filter_val = (
                params.get(self.filter_column)
                or params.get("id")
                or params.get("employee_id")
            )

        try:
            from database.db import get_db
            with get_db() as db:
                if filter_val is not None:
                    query = f"SELECT {cols_clause} FROM {self.table_name} WHERE {self.filter_column} = %s LIMIT 1"
                    db.cursor.execute(query, (str(filter_val),))
                    row = db.cursor.fetchone()
                    if not row:
                        return {"status": "not_found", "message": f"No record found in {self.table_name} where {self.filter_column}={filter_val}"}
                    return {"status": "success", "data": row}
                else:
                    query = f"SELECT {cols_clause} FROM {self.table_name} LIMIT 20"
                    db.cursor.execute(query)
                    rows = db.cursor.fetchall()
                    return {"status": "success", "data": rows}
        except Exception as e:
            logger.error(f"SQL execution error on {self.table_name}: {e}")
            return {"status": "error", "error": str(e)}
