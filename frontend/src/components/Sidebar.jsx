import { useState } from "react";
import "./Sidebar.css";

/**
 * Purely presentational icon rail — matches the HR Buddy dashboard mockup.
 * No wiring to app logic; "Conversation" is the only real view this app has,
 * the other icons are placeholders for future sections.
 */
export default function Sidebar() {
    const [active, setActive] = useState("conversation");

    const items = [
        { key: "conversation", title: "Conversation", icon: "💬" },
        { key: "insights", title: "Insights", icon: "📊" },
        { key: "directory", title: "Employee directory", icon: "👥" },
    ];

    return (
        <nav className="sidebar">
            <div className="sidebar-logo">HB</div>
            {items.map((item) => (
                <button
                    key={item.key}
                    className={`sidebar-btn ${active === item.key ? "sidebar-btn--active" : ""}`}
                    title={item.title}
                    onClick={() => setActive(item.key)}
                >
                    <span>{item.icon}</span>
                </button>
            ))}
            <div className="sidebar-spacer" />
            <button className="sidebar-btn" title="Settings">
                <span>⚙️</span>
            </button>
        </nav>
    );
}
