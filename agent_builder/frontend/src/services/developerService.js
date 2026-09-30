/**
 * agent_builder/frontend/src/services/developerService.js
 *
 * Frontend service retrieving and saving developer configuration,
 * environment status, and system health metrics from the agent_builder backend.
 */

const BASE = "/api/developer";

/**
 * Fetch developer runtime info: Git, system, AI stack, API keys, LiveKit status, profile, logs.
 * @returns {Promise<Object>}
 */
export async function getDeveloperInfo() {
  const res = await fetch(`${BASE}/info`);
  if (!res.ok) throw new Error(`getDeveloperInfo failed: ${res.status}`);
  return res.json();
}

/**
 * Fetch live system metrics: CPU, memory, thread count, active workers.
 * @returns {Promise<{cpu_percent: number, memory_mb: number, threads: number, active_workers: number}>}
 */
export async function getSystemMetrics() {
  const res = await fetch(`${BASE}/metrics`);
  if (!res.ok) throw new Error(`getSystemMetrics failed: ${res.status}`);
  return res.json();
}

/**
 * Fetch the buffered log stream (last N lines) from the monitoring handler.
 * @param {string} [level="ALL"]  - Filter by log level
 * @returns {Promise<Array>}
 */
export async function getLogs(level = "ALL") {
  const params = level !== "ALL" ? `?level=${level}` : "";
  const res = await fetch(`${BASE}/logs${params}`);
  if (!res.ok) throw new Error(`getLogs failed: ${res.status}`);
  return res.json();
}

/**
 * Fetch the persistent developer profile (name, email, role, phone, notes).
 * @returns {Promise<{status: string, profile: Object}>}
 */
export async function getDeveloperProfile() {
  const res = await fetch(`${BASE}/profile`);
  if (!res.ok) throw new Error(`getDeveloperProfile failed: ${res.status}`);
  return res.json();
}

/**
 * Save/update the persistent developer profile.
 * @param {Object} profile - {name, email, role, department, phone, slack_channel, notes, environment}
 * @returns {Promise<{status: string, message: string, profile: Object}>}
 */
export async function saveDeveloperProfile(profile) {
  const res = await fetch(`${BASE}/profile`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(profile),
  });
  if (!res.ok) throw new Error(`saveDeveloperProfile failed: ${res.status}`);
  return res.json();
}
