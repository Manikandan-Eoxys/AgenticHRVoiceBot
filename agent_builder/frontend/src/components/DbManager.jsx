/**
 * agent_builder/frontend/src/components/DbManager.jsx
 *
 * Full-featured web Database Manager for the admin portal.
 * - Interactive table viewer with row counts
 * - Column schema viewer with types and keys
 * - SQL query console with execution history and timing
 */

import { useState, useEffect, useRef } from "react";
import { fetchTables, fetchTableSchema, executeQuery } from "../services/dbService.js";

export default function DbManager() {
  const [tables, setTables] = useState([]);
  const [selectedTable, setSelectedTable] = useState(null);
  const [schema, setSchema] = useState([]);
  const [sql, setSql] = useState("");
  const [queryResult, setQueryResult] = useState(null);
  const [queryHistory, setQueryHistory] = useState([]);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState(null);
  const textareaRef = useRef(null);

  // Load tables on mount
  useEffect(() => {
    loadTables();
  }, []);

  async function loadTables() {
    try {
      const data = await fetchTables();
      setTables(data.tables || []);
    } catch (e) {
      setError("Failed to load tables: " + e.message);
    }
  }

  async function handleTableSelect(tableName) {
    setSelectedTable(tableName);
    setSchema([]);
    setQueryResult(null);
    try {
      const data = await fetchTableSchema(tableName);
      setSchema(data.schema || []);
      setSql(`SELECT * FROM ${tableName} LIMIT 50;`);
    } catch (e) {
      setError("Failed to load schema: " + e.message);
    }
  }

  async function handleRunQuery() {
    if (!sql.trim()) return;
    setLoading(true);
    setError(null);
    setQueryResult(null);
    try {
      const data = await executeQuery(sql);
      setQueryResult(data);
      setQueryHistory((prev) => [
        { sql, timestamp: new Date().toLocaleTimeString(), exec_ms: data.exec_ms },
        ...prev.slice(0, 19),
      ]);
    } catch (e) {
      setError(e.message || "Query failed");
    } finally {
      setLoading(false);
    }
  }

  function handleHistoryClick(historySql) {
    setSql(historySql);
    textareaRef.current?.focus();
  }

  return (
    <div className="db-manager">
      <div className="db-manager__sidebar">
        <h3 className="db-manager__sidebar-title">Tables</h3>
        <button className="db-manager__refresh-btn" onClick={loadTables}>
          ⟳ Refresh
        </button>
        <ul className="db-manager__table-list">
          {tables.map((t) => (
            <li
              key={t.table_name}
              className={`db-manager__table-item ${selectedTable === t.table_name ? "db-manager__table-item--active" : ""}`}
              onClick={() => handleTableSelect(t.table_name)}
            >
              <span className="db-manager__table-name">{t.table_name}</span>
              <span className="db-manager__table-rows">{t.row_count ?? "?"} rows</span>
            </li>
          ))}
        </ul>
      </div>

      <div className="db-manager__main">
        {/* Schema Viewer */}
        {schema.length > 0 && (
          <div className="db-manager__schema">
            <h4 className="db-manager__section-title">Schema — {selectedTable}</h4>
            <table className="db-manager__schema-table">
              <thead>
                <tr>
                  <th>Column</th>
                  <th>Type</th>
                  <th>Nullable</th>
                  <th>Key</th>
                  <th>Default</th>
                  <th>Extra</th>
                </tr>
              </thead>
              <tbody>
                {schema.map((col) => (
                  <tr key={col.column_name}>
                    <td className="db-manager__col-name">{col.column_name}</td>
                    <td><code>{col.column_type}</code></td>
                    <td>{col.nullable}</td>
                    <td>{col.key_type && <span className="db-manager__key-badge">{col.key_type}</span>}</td>
                    <td>{col.default_value ?? <em>—</em>}</td>
                    <td>{col.extra}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}

        {/* SQL Console */}
        <div className="db-manager__console">
          <h4 className="db-manager__section-title">SQL Query Console</h4>
          <textarea
            ref={textareaRef}
            className="db-manager__sql-input"
            value={sql}
            onChange={(e) => setSql(e.target.value)}
            rows={5}
            placeholder="SELECT * FROM employees LIMIT 10;"
            onKeyDown={(e) => {
              if ((e.ctrlKey || e.metaKey) && e.key === "Enter") {
                e.preventDefault();
                handleRunQuery();
              }
            }}
          />
          <div className="db-manager__console-actions">
            <button
              className="db-manager__run-btn"
              onClick={handleRunQuery}
              disabled={loading || !sql.trim()}
            >
              {loading ? "Running…" : "▶ Run Query  (Ctrl+Enter)"}
            </button>
            {queryResult && (
              <span className="db-manager__timing">
                {queryResult.row_count} rows · {queryResult.exec_ms} ms
              </span>
            )}
          </div>

          {error && <div className="db-manager__error">{error}</div>}

          {/* Results Grid */}
          {queryResult && queryResult.rows?.length > 0 && (
            <div className="db-manager__results-wrap">
              <table className="db-manager__results-table">
                <thead>
                  <tr>
                    {queryResult.columns.map((col) => (
                      <th key={col}>{col}</th>
                    ))}
                  </tr>
                </thead>
                <tbody>
                  {queryResult.rows.map((row, idx) => (
                    <tr key={idx}>
                      {queryResult.columns.map((col) => (
                        <td key={col}>{row[col] === null ? <em className="db-manager__null">NULL</em> : String(row[col])}</td>
                      ))}
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          )}

          {queryResult && queryResult.rows?.length === 0 && (
            <div className="db-manager__empty">No rows returned.</div>
          )}
        </div>

        {/* Query History */}
        {queryHistory.length > 0 && (
          <div className="db-manager__history">
            <h4 className="db-manager__section-title">Query History</h4>
            <ul className="db-manager__history-list">
              {queryHistory.map((h, idx) => (
                <li key={idx} className="db-manager__history-item" onClick={() => handleHistoryClick(h.sql)}>
                  <span className="db-manager__history-sql">{h.sql}</span>
                  <span className="db-manager__history-meta">{h.timestamp} · {h.exec_ms} ms</span>
                </li>
              ))}
            </ul>
          </div>
        )}
      </div>
    </div>
  );
}
