import "./ToolCalls.css";
export default function ToolCalls({ tools = [] }) {
    return (
        <div className="tool-panel">
            <h3>Tool Execution</h3>
            {tools.length === 0 ? (
                <p className="empty">No tool executed yet.</p>
            ) : (
                tools.map((tool, index) => (
                    <div className="tool-item" key={index}>
                        <div className="tool-name">
                            {tool.name}
                        </div>
                        <div
                            className={`tool-status ${tool.status}`}
                        >
                            {tool.status}
                        </div>
                    </div>
                ))
            )}
        </div>
    );
}
