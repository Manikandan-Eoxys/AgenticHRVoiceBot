import "./StatusBar.css";
export default function StatusBar({
    connected,
    status,
    room,
    participants,
    duration,
}) {
    return (
        <div className="status">
            <div className="status-title">Connection</div>
            <div className="status-row">
                <span>Status</span>
                <b className={connected ? "status-live" : ""}>{status}</b>
            </div>
            <div className="status-row">
                <span>Room</span>
                <b>{room}</b>
            </div>
            <div className="status-row">
                <span>Participants</span>
                <b>{participants}</b>
            </div>
            <div className="status-row">
                <span>Duration</span>
                <b>{duration}</b>
            </div>
        </div>
    );
}
