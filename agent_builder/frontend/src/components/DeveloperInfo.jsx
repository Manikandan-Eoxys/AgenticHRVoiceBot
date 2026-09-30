/**
 * agent_builder/frontend/src/components/DeveloperInfo.jsx
 *
 * Developer dashboard and configuration storage view for the admin portal.
 * Features:
 * - Developer Information Storage: Edit & persist developer name, email, role, phone, notes
 * - System runtime specs (CPU, memory, platform, uptime)
 * - Active API key status (LiveKit, Deepgram, Cartesia, Ollama)
 * - Git commit hash / branch / deployment environment
 * - Active STT/LLM/TTS provider versions and status
 * - Real-time debug log viewer with log level filtering
 */

import { useState, useEffect, useRef } from "react";
import {
  getDeveloperInfo,
  getSystemMetrics,
  saveDeveloperProfile,
} from "../services/developerService.js";

const LOG_LEVELS = ["ALL", "DEBUG", "INFO", "WARNING", "ERROR"];

export default function DeveloperInfo() {
  const [devInfo, setDevInfo] = useState(null);
  const [metrics, setMetrics] = useState(null);
  const [logLevel, setLogLevel] = useState("ALL");
  const [logs, setLogs] = useState([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState(null);
  const logsEndRef = useRef(null);

  // Editable Developer Profile Form State
  const [profileForm, setProfileForm] = useState({
    name: "",
    email: "",
    role: "",
    department: "",
    phone: "",
    slack_channel: "",
    environment: "development",
    notes: "",
  });
  const [isSavingProfile, setIsSavingProfile] = useState(false);
  const [profileSaveSuccess, setProfileSaveSuccess] = useState(false);

  useEffect(() => {
    loadInfo();
    const interval = setInterval(loadMetrics, 10000);
    return () => clearInterval(interval);
  }, []);

  useEffect(() => {
    logsEndRef.current?.scrollIntoView({ behavior: "smooth" });
  }, [logs]);

  async function loadInfo() {
    setLoading(true);
    try {
      const [info, metricsData] = await Promise.all([getDeveloperInfo(), getSystemMetrics()]);
      setDevInfo(info);
      setMetrics(metricsData);
      setLogs(info.logs || []);
      if (info.developer_profile) {
        setProfileForm((prev) => ({
          ...prev,
          ...info.developer_profile,
        }));
      }
    } catch (e) {
      setError("Failed to load developer info: " + e.message);
    } finally {
      setLoading(false);
    }
  }

  async function loadMetrics() {
    try {
      const metricsData = await getSystemMetrics();
      setMetrics(metricsData);
    } catch (_) {}
  }

  async function handleProfileSubmit(e) {
    e.preventDefault();
    setIsSavingProfile(true);
    setProfileSaveSuccess(false);
    try {
      const res = await saveDeveloperProfile(profileForm);
      if (res.status === "ok") {
        setProfileSaveSuccess(true);
        setTimeout(() => setProfileSaveSuccess(false), 4000);
      }
    } catch (err) {
      alert("Error saving developer profile: " + err.message);
    } finally {
      setIsSavingProfile(false);
    }
  }

  const filteredLogs = logs.filter(
    (log) => logLevel === "ALL" || log.level === logLevel
  );

  function levelColor(level) {
    return { DEBUG: "#888", INFO: "#4fc3f7", WARNING: "#ffb74d", ERROR: "#ef5350" }[level] || "#ccc";
  }

  if (loading) return <div className="dev-info__loading">Loading developer info…</div>;
  if (error) return <div className="dev-info__error">{error}</div>;

  return (
    <div className="dev-info" style={{ padding: "24px", color: "#e8eaf6", overflowY: "auto", height: "100%" }}>
      {/* Header */}
      <div style={{ marginBottom: "24px", borderBottom: "1px solid #2d3148", paddingBottom: "16px" }}>
        <h2 style={{ margin: 0, fontSize: "22px", color: "#4fc3f7", display: "flex", alignItems: "center", gap: "10px" }}>
          <span>🛠️</span> Developer Information & Diagnostics
        </h2>
        <p style={{ margin: "4px 0 0", color: "#8c9ba5", fontSize: "14px" }}>
          Configure developer profile metadata, monitor active AI stack components, and inspect runtime health.
        </p>
      </div>

      {/* Developer Profile Storage Section */}
      <div
        style={{
          background: "#1a1d27",
          border: "1px solid #2d3148",
          borderRadius: "8px",
          padding: "20px",
          marginBottom: "24px",
        }}
      >
        <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center", marginBottom: "16px" }}>
          <h3 style={{ margin: 0, fontSize: "16px", color: "#fff", display: "flex", alignItems: "center", gap: "8px" }}>
            <span>👤</span> Developer Contact & Configuration
          </h3>
          {profileSaveSuccess && (
            <span style={{ color: "#66bb6a", fontSize: "13px", fontWeight: "600", display: "flex", alignItems: "center", gap: "4px" }}>
              ✓ Saved successfully!
            </span>
          )}
        </div>

        <form onSubmit={handleProfileSubmit}>
          <div style={{ display: "grid", gridTemplateColumns: "repeat(auto-fit, minmax(240px, 1fr))", gap: "16px", marginBottom: "16px" }}>
            <div>
              <label style={{ display: "block", fontSize: "12px", color: "#8c9ba5", marginBottom: "6px" }}>Developer Name</label>
              <input
                type="text"
                value={profileForm.name}
                onChange={(e) => setProfileForm({ ...profileForm, name: e.target.value })}
                style={{ width: "100%", padding: "8px 12px", background: "#0f1117", border: "1px solid #2d3148", borderRadius: "6px", color: "#fff", fontSize: "13px" }}
                placeholder="e.g. Lead Voice AI Engineer"
              />
            </div>

            <div>
              <label style={{ display: "block", fontSize: "12px", color: "#8c9ba5", marginBottom: "6px" }}>Email</label>
              <input
                type="email"
                value={profileForm.email}
                onChange={(e) => setProfileForm({ ...profileForm, email: e.target.value })}
                style={{ width: "100%", padding: "8px 12px", background: "#0f1117", border: "1px solid #2d3148", borderRadius: "6px", color: "#fff", fontSize: "13px" }}
                placeholder="developer@example.com"
              />
            </div>

            <div>
              <label style={{ display: "block", fontSize: "12px", color: "#8c9ba5", marginBottom: "6px" }}>Role / Designation</label>
              <input
                type="text"
                value={profileForm.role}
                onChange={(e) => setProfileForm({ ...profileForm, role: e.target.value })}
                style={{ width: "100%", padding: "8px 12px", background: "#0f1117", border: "1px solid #2d3148", borderRadius: "6px", color: "#fff", fontSize: "13px" }}
                placeholder="e.g. Senior AI Engineer"
              />
            </div>

            <div>
              <label style={{ display: "block", fontSize: "12px", color: "#8c9ba5", marginBottom: "6px" }}>Phone / Direct Contact</label>
              <input
                type="text"
                value={profileForm.phone}
                onChange={(e) => setProfileForm({ ...profileForm, phone: e.target.value })}
                style={{ width: "100%", padding: "8px 12px", background: "#0f1117", border: "1px solid #2d3148", borderRadius: "6px", color: "#fff", fontSize: "13px" }}
                placeholder="+91 ..."
              />
            </div>

            <div>
              <label style={{ display: "block", fontSize: "12px", color: "#8c9ba5", marginBottom: "6px" }}>Slack / Discord Channel</label>
              <input
                type="text"
                value={profileForm.slack_channel}
                onChange={(e) => setProfileForm({ ...profileForm, slack_channel: e.target.value })}
                style={{ width: "100%", padding: "8px 12px", background: "#0f1117", border: "1px solid #2d3148", borderRadius: "6px", color: "#fff", fontSize: "13px" }}
                placeholder="#voicebot-dev"
              />
            </div>

            <div>
              <label style={{ display: "block", fontSize: "12px", color: "#8c9ba5", marginBottom: "6px" }}>Environment</label>
              <select
                value={profileForm.environment}
                onChange={(e) => setProfileForm({ ...profileForm, environment: e.target.value })}
                style={{ width: "100%", padding: "8px 12px", background: "#0f1117", border: "1px solid #2d3148", borderRadius: "6px", color: "#fff", fontSize: "13px" }}
              >
                <option value="development">Development</option>
                <option value="staging">Staging</option>
                <option value="production">Production</option>
              </select>
            </div>
          </div>

          <div style={{ marginBottom: "16px" }}>
            <label style={{ display: "block", fontSize: "12px", color: "#8c9ba5", marginBottom: "6px" }}>Developer Notes / Architecture References</label>
            <textarea
              rows={2}
              value={profileForm.notes}
              onChange={(e) => setProfileForm({ ...profileForm, notes: e.target.value })}
              style={{ width: "100%", padding: "8px 12px", background: "#0f1117", border: "1px solid #2d3148", borderRadius: "6px", color: "#fff", fontSize: "13px", resize: "vertical" }}
              placeholder="Notes on agent state, current release tasks, deployment keys..."
            />
          </div>

          <button
            type="submit"
            disabled={isSavingProfile}
            style={{
              background: "#4fc3f7",
              color: "#0f1117",
              fontWeight: "600",
              border: "none",
              borderRadius: "6px",
              padding: "9px 20px",
              cursor: isSavingProfile ? "not-allowed" : "pointer",
              fontSize: "13px",
              transition: "opacity 0.2s",
            }}
          >
            {isSavingProfile ? "Saving Profile…" : "💾 Save Developer Info"}
          </button>
        </form>
      </div>

      {/* System & Environment Grid */}
      <div style={{ display: "grid", gridTemplateColumns: "repeat(auto-fit, minmax(260px, 1fr))", gap: "16px", marginBottom: "24px" }}>
        <div style={{ background: "#1a1d27", border: "1px solid #2d3148", borderRadius: "8px", padding: "16px" }}>
          <h4 style={{ margin: "0 0 12px", fontSize: "14px", color: "#fff" }}>🖥 System</h4>
          <InfoRow label="Platform" value={devInfo?.system?.platform} />
          <InfoRow label="Python" value={devInfo?.system?.python_version} />
          <InfoRow label="Environment" value={devInfo?.system?.environment} />
          <InfoRow label="Uptime" value={devInfo?.system?.uptime} />
        </div>

        <div style={{ background: "#1a1d27", border: "1px solid #2d3148", borderRadius: "8px", padding: "16px" }}>
          <h4 style={{ margin: "0 0 12px", fontSize: "14px", color: "#fff" }}>⚡ Runtime Metrics</h4>
          <InfoRow label="CPU Usage" value={metrics ? `${metrics.cpu_percent}%` : "—"} />
          <InfoRow label="Memory" value={metrics ? `${metrics.memory_mb} MB` : "—"} />
          <InfoRow label="Threads" value={metrics ? metrics.threads : "—"} />
          <InfoRow label="Workers" value={metrics ? metrics.active_workers : "—"} />
        </div>

        <div style={{ background: "#1a1d27", border: "1px solid #2d3148", borderRadius: "8px", padding: "16px" }}>
          <h4 style={{ margin: "0 0 12px", fontSize: "14px", color: "#fff" }}>🔀 Git Info</h4>
          <InfoRow label="Branch" value={devInfo?.git?.branch} />
          <InfoRow label="Commit" value={<code>{devInfo?.git?.commit?.slice(0, 8)}</code>} />
          <InfoRow label="Message" value={devInfo?.git?.message} />
          <InfoRow label="Author" value={devInfo?.git?.author} />
        </div>

        <div style={{ background: "#1a1d27", border: "1px solid #2d3148", borderRadius: "8px", padding: "16px" }}>
          <h4 style={{ margin: "0 0 12px", fontSize: "14px", color: "#fff" }}>🤖 AI Stack</h4>
          <InfoRow label="STT" value={devInfo?.ai_stack?.stt} />
          <InfoRow label="LLM Primary" value={devInfo?.ai_stack?.llm_primary} />
          <InfoRow label="LLM Fallback" value={devInfo?.ai_stack?.llm_fallback} />
          <InfoRow label="TTS" value={devInfo?.ai_stack?.tts} />
        </div>
      </div>

      {/* API Key Status */}
      <div style={{ background: "#1a1d27", border: "1px solid #2d3148", borderRadius: "8px", padding: "16px", marginBottom: "24px" }}>
        <h4 style={{ margin: "0 0 12px", fontSize: "14px", color: "#fff" }}>🔑 API Key Status</h4>
        <div style={{ display: "flex", flexWrap: "wrap", gap: "8px" }}>
          {devInfo?.api_keys &&
            Object.entries(devInfo.api_keys).map(([name, status]) => (
              <span
                key={name}
                style={{
                  padding: "4px 10px",
                  borderRadius: "4px",
                  fontSize: "12px",
                  fontWeight: "500",
                  background: status === "set" ? "rgba(102, 187, 106, 0.15)" : "rgba(239, 83, 80, 0.15)",
                  color: status === "set" ? "#66bb6a" : "#ef5350",
                  border: `1px solid ${status === "set" ? "rgba(102, 187, 106, 0.3)" : "rgba(239, 83, 80, 0.3)"}`,
                }}
              >
                {name}: {status === "set" ? "✓ Set" : "✗ Missing"}
              </span>
            ))}
        </div>
      </div>

      {/* LiveKit Connectivity */}
      <div style={{ background: "#1a1d27", border: "1px solid #2d3148", borderRadius: "8px", padding: "16px", marginBottom: "24px" }}>
        <h4 style={{ margin: "0 0 12px", fontSize: "14px", color: "#fff" }}>☁ LiveKit Cloud</h4>
        <InfoRow label="Status" value={devInfo?.livekit?.status} />
        <InfoRow label="URL" value={devInfo?.livekit?.url} />
        <InfoRow label="Latency" value={devInfo?.livekit?.latency_ms != null ? `${devInfo.livekit.latency_ms} ms` : "—"} />
      </div>

      {/* Log Viewer */}
      <div style={{ background: "#1a1d27", border: "1px solid #2d3148", borderRadius: "8px", padding: "16px" }}>
        <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center", marginBottom: "12px" }}>
          <h4 style={{ margin: 0, fontSize: "14px", color: "#fff" }}>📋 Debug Logs</h4>
          <div style={{ display: "flex", gap: "6px" }}>
            {LOG_LEVELS.map((lvl) => (
              <button
                key={lvl}
                style={{
                  background: logLevel === lvl ? "#4fc3f7" : "#0f1117",
                  color: logLevel === lvl ? "#0f1117" : "#8c9ba5",
                  border: "1px solid #2d3148",
                  borderRadius: "4px",
                  padding: "4px 8px",
                  fontSize: "11px",
                  cursor: "pointer",
                }}
                onClick={() => setLogLevel(lvl)}
              >
                {lvl}
              </button>
            ))}
            <button
              style={{
                background: "#0f1117",
                color: "#4fc3f7",
                border: "1px solid #2d3148",
                borderRadius: "4px",
                padding: "4px 10px",
                cursor: "pointer",
              }}
              onClick={loadInfo}
            >
              ⟳ Refresh
            </button>
          </div>
        </div>
        <div
          style={{
            background: "#0f1117",
            border: "1px solid #2d3148",
            borderRadius: "6px",
            padding: "12px",
            maxHeight: "260px",
            overflowY: "auto",
            fontFamily: "monospace",
            fontSize: "12px",
          }}
        >
          {filteredLogs.length === 0 ? (
            <div style={{ color: "#666" }}>No logs at this level.</div>
          ) : (
            filteredLogs.map((log, idx) => (
              <div key={idx} style={{ display: "flex", gap: "10px", marginBottom: "4px" }}>
                <span style={{ color: "#666" }}>{log.time}</span>
                <span style={{ color: levelColor(log.level), minWidth: "55px" }}>[{log.level}]</span>
                <span style={{ color: "#e8eaf6" }}>{log.message}</span>
              </div>
            ))
          )}
          <div ref={logsEndRef} />
        </div>
      </div>
    </div>
  );
}

function InfoRow({ label, value }) {
  return (
    <div style={{ display: "flex", justifyContent: "space-between", padding: "6px 0", borderBottom: "1px solid #24283b", fontSize: "13px" }}>
      <span style={{ color: "#8c9ba5" }}>{label}</span>
      <span style={{ color: "#fff", fontWeight: "500" }}>{value ?? <em style={{ color: "#666" }}>N/A</em>}</span>
    </div>
  );
}
