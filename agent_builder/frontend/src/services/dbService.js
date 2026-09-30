/**
 * agent_builder/frontend/src/services/dbService.js
 *
 * Frontend API client communicating with the backend DB management endpoints.
 * All calls route through the Vite proxy to /api/db/* (agent_builder backend :8571).
 */

const BASE = "/api/db";

/**
 * Fetch list of all tables with row counts.
 * @returns {Promise<{tables: Array}>}
 */
export async function fetchTables() {
  const res = await fetch(`${BASE}/tables`);
  if (!res.ok) throw new Error(`fetchTables failed: ${res.status}`);
  return res.json();
}

/**
 * Fetch schema (column definitions) for a specific table.
 * @param {string} tableName
 * @returns {Promise<{schema: Array}>}
 */
export async function fetchTableSchema(tableName) {
  const res = await fetch(`${BASE}/schema/${encodeURIComponent(tableName)}`);
  if (!res.ok) throw new Error(`fetchTableSchema failed: ${res.status}`);
  return res.json();
}

/**
 * Execute a read-only SQL query against the HR database.
 * @param {string} sql  - Must be a SELECT statement
 * @returns {Promise<{rows: Array, columns: Array, row_count: number, exec_ms: number}>}
 */
export async function executeQuery(sql) {
  const res = await fetch(`${BASE}/query`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ sql }),
  });
  if (!res.ok) {
    const body = await res.json().catch(() => ({}));
    throw new Error(body.detail || `executeQuery failed: ${res.status}`);
  }
  return res.json();
}

/**
 * Fetch MySQL connection pool statistics.
 * @returns {Promise<Object>}
 */
export async function fetchPoolStats() {
  const res = await fetch(`${BASE}/pool`);
  if (!res.ok) throw new Error(`fetchPoolStats failed: ${res.status}`);
  return res.json();
}
