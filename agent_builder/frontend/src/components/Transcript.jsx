import { useEffect, useRef } from "react";
import "./Transcript.css";
export default function Transcript({ messages = [], onClear }) {
    const bodyRef = useRef(null);

    // New: auto-scroll to the latest message as streaming text grows —
    // without this, live-updating text below the fold would be invisible
    // until the user manually scrolled down.
    useEffect(() => {
        bodyRef.current?.scrollTo({
            top: bodyRef.current.scrollHeight,
            behavior: "smooth",
        });
    }, [messages]);

    return (
        <div className="transcript-container">
            <div className="transcript-toolbar">
                <div className="transcript-header">
                    💬 Conversation
                </div>
                <button className="clear-btn" onClick={onClear}>Clear chat</button>
            </div>
            <div className="transcript-body" ref={bodyRef}>
                {messages.length === 0 ? (
                    <div className="empty-chat">
                        <h3>No conversation yet</h3>
                        <p>
                            Press Connect and start talking to HR Buddy.
                        </p>
                    </div>
                ) : (
                    messages.map((msg) => (
                        <div
                            key={msg.id}
                            className={
                                (msg.role === "user"
                                    ? "message user"
                                    : "message assistant") +
                                (msg.final === false ? " streaming" : "")
                            }
                        >
                            <div className="avatar">
                                {msg.role === "user" ? "👤" : "🤖"}
                            </div>
                            <div className="bubble">
                                <div className="sender">
                                    {msg.role === "user"
                                        ? "Employee"
                                        : "HR Buddy"}
                                </div>
                                <div className="text">
                                    {msg.text}
                                    {msg.final === false && (
                                        <span className="streaming-cursor" />
                                    )}
                                </div>
                            </div>
                        </div>
                    ))
                )}
            </div>
        </div>
    );
}
