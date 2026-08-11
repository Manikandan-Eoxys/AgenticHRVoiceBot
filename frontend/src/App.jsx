import "./styles/app.css";
import Sidebar from "./components/Sidebar";
import VoiceAssistant from "./components/VoiceAssistant";
export default function App() {
    return (
        <div className="app">
            <Sidebar />
            <VoiceAssistant />
        </div>
    );
}
