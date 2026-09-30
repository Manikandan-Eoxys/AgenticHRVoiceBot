import "./Sidebar.css";

/**
 * Sidebar navigation rail for Agent Builder.
 * Supports switching between Conversation, Database Manager, and Developer Info views.
 */
export default function Sidebar({ activeTab, onTabChange }) {
    const items = [
        { key: "conversation", title: "Conversation & Voice Bot", icon: "💬" },
        { key: "database", title: "Database Management", icon: "🗄️" },
        { key: "developer", title: "Developer Info & Diagnostics", icon: "🛠️" },
    ];

    return (
        <nav className="sidebar">
            <div className="sidebar-logo" title="AgenticVoiceBot">AV</div>
            {items.map((item) => (
                <button
                    key={item.key}
                    className={`sidebar-btn ${activeTab === item.key ? "sidebar-btn--active" : ""}`}
                    title={item.title}
                    onClick={() => onTabChange(item.key)}
                >
                    <span>{item.icon}</span>
                </button>
            ))}
            <div className="sidebar-spacer" />
            <button
                className={`sidebar-btn ${activeTab === "developer" ? "sidebar-btn--active" : ""}`}
                title="Developer Settings"
                onClick={() => onTabChange("developer")}
            >
                <span>⚙️</span>
            </button>
        </nav>
    );
}
