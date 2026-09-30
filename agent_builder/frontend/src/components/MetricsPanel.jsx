import "./MetricsPanel.css";
export default function MetricsPanel({
    metrics = {}
}) {
    const defaults = {
        stt: "--",
        llm: "--",
        tool: "--",
        database: "--",
        tts: "--",
        overall: "--"
    };
    metrics = { ...defaults, ...metrics };
    return (
        <div className="metrics-panel">
            <h3>Performance</h3>
            <Metric
                title="Speech → Text"
                value={metrics.stt}
            />
            <Metric
                title="LLM"
                value={metrics.llm}
            />
            <Metric
                title="Tool"
                value={metrics.tool}
            />
            <Metric
                title="Database"
                value={metrics.database}
            />
            <Metric
                title="Text → Speech"
                value={metrics.tts}
            />
            <Metric
                title="Overall"
                value={metrics.overall}
            />
        </div>
    );
}
function Metric({ title, value }) {
    return (
        <div className="metric-row">
            <span>{title}</span>
            <strong>{value}</strong>
        </div>
    );
}
