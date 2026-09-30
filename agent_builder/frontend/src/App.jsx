import { useState } from "react";
import "./styles/app.css";
import Sidebar from "./components/Sidebar";
import VoiceAssistant from "./components/VoiceAssistant";
import DbManager from "./components/DbManager";
import DeveloperInfo from "./components/DeveloperInfo";

export default function App() {
    const [activeTab, setActiveTab] = useState("conversation");

    return (
        <div className="app">
            <Sidebar activeTab={activeTab} onTabChange={setActiveTab} />
            <main style={{ flex: 1, overflow: "hidden", display: "flex", flexDirection: "column" }}>
                {activeTab === "conversation" && <VoiceAssistant />}
                {activeTab === "database" && <DbManager />}
                {activeTab === "developer" && <DeveloperInfo />}
            </main>
        </div>
    );
}
