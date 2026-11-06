"""
datasources/sql_table_source.py

SQL Table adapter driven by ActionConfig.dataSource.
Performs declarative query lookups against MySQL/SQL tables with
SQL injection prevention, Circuit Breaker resilience, and output sanitization.
"""

import logging
import re
from typing import Any, Dict, Optional
from datasources.base import BaseDataSourceAdapter
from models.config_models import DataSourceConfig
from security.tool_guardrails import default_tool_guardrail, ToolSanitizer

logger = logging.getLogger("datasources.sql")

SAFE_IDENTIFIER_RE = re.compile(r"^[a-zA-Z0-9_]+$")


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
        self.table_name = (config.table_name or "").strip()
        self.columns = config.columns or []
        self.filter_column = (config.filter_column or "").strip()

        # Circuit breaker tracking for this database table
        self.circuit_name = f"sql_table_{self.table_name or 'unknown'}"
        self.circuit_breaker = default_tool_guardrail.get_circuit_breaker(self.circuit_name)

    async def execute(self, params: Dict[str, Any]) -> Any:
        if not self.table_name:
            return {"status": "error", "error": "No table_name specified for SQL data source"}

        # Security: Validate table name identifier against injection
        if not SAFE_IDENTIFIER_RE.match(self.table_name):
            logger.error(f"⛔ [SQL Security] Invalid table name: '{self.table_name}'")
            return {"status": "error", "error": "Invalid table identifier."}

        # Validate column names
        safe_cols = []
        for col in self.columns:
            clean_col = str(col).strip()
            if SAFE_IDENTIFIER_RE.match(clean_col):
                safe_cols.append(clean_col)
        cols_clause = ", ".join(safe_cols) if safe_cols else "*"

        # Check filter column identifier
        if self.filter_column and not SAFE_IDENTIFIER_RE.match(self.filter_column):
            logger.error(f"⛔ [SQL Security] Invalid filter column: '{self.filter_column}'")
            return {"status": "error", "error": "Invalid filter column identifier."}

        # 1. Outage Resilience: Fast-fail if database is unreachable
        if not self.circuit_breaker.can_execute():
            logger.warning(f"🛑 [SQL Outage] Tripped circuit breaker for '{self.circuit_name}'. Fast-failing.")
            return {
                "status": "error",
                "error_code": "CIRCUIT_OPEN",
                "error": "Database service is currently experiencing connection errors.",
            }

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
                    query = f"SELECT {cols_clause} FROM `{self.table_name}` WHERE `{self.filter_column}` = %s LIMIT 1"
                    db.cursor.execute(query, (str(filter_val),))
                    row = db.cursor.fetchone()
                    self.circuit_breaker.record_success()
                    if not row:
                        return {"status": "not_found", "message": f"No record found in {self.table_name} where {self.filter_column}={filter_val}"}
                    sanitized_row = ToolSanitizer.sanitize_output(self.circuit_name, row)
                    return {"status": "success", "data": sanitized_row}
                else:
                    query = f"SELECT {cols_clause} FROM `{self.table_name}` LIMIT 20"
                    db.cursor.execute(query)
                    rows = db.cursor.fetchall()
                    self.circuit_breaker.record_success()
                    sanitized_rows = ToolSanitizer.sanitize_output(self.circuit_name, rows)
                    return {"status": "success", "data": sanitized_rows}
        except Exception as e:
            self.circuit_breaker.record_failure(e)
            logger.error(f"SQL execution error on {self.table_name}: {e}")
            return {"status": "error", "error": str(e)}
