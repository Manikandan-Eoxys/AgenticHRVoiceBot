/**
 * hr_voice_agent/frontend/src/components/LiveWaveform.jsx
 *
 * WebAudio API visualizer rendering real-time amplitude bars for
 * caller (microphone) and agent (speaker) audio streams.
 */

import { useEffect, useRef } from "react";

export default function LiveWaveform({ room, connected }) {
  const callerCanvasRef = useRef(null);
  const agentCanvasRef  = useRef(null);
  const animRef         = useRef(null);
  const analyserRef     = useRef(null);
  const audioCtxRef     = useRef(null);

  useEffect(() => {
    if (!connected || !room) {
      cancelAnimationFrame(animRef.current);
      clearCanvas(callerCanvasRef.current);
      clearCanvas(agentCanvasRef.current);
      return;
    }

    let micStream = null;

    async function startViz() {
      try {
        audioCtxRef.current = new AudioContext();
        const analyser = audioCtxRef.current.createAnalyser();
        analyser.fftSize = 256;
        analyserRef.current = analyser;

        micStream = await navigator.mediaDevices.getUserMedia({ audio: true });
        const source = audioCtxRef.current.createMediaStreamSource(micStream);
        source.connect(analyser);

        drawWaveform(callerCanvasRef.current, analyser, "#4fc3f7");
      } catch (_) {}
    }

    startViz();

    return () => {
      cancelAnimationFrame(animRef.current);
      micStream?.getTracks().forEach((t) => t.stop());
      audioCtxRef.current?.close();
    };
  }, [connected, room]);

  function clearCanvas(canvas) {
    if (!canvas) return;
    const ctx = canvas.getContext("2d");
    ctx.clearRect(0, 0, canvas.width, canvas.height);
  }

  function drawWaveform(canvas, analyser, color) {
    if (!canvas || !analyser) return;
    const ctx = canvas.getContext("2d");
    const data = new Uint8Array(analyser.frequencyBinCount);

    function render() {
      animRef.current = requestAnimationFrame(render);
      analyser.getByteFrequencyData(data);

      ctx.clearRect(0, 0, canvas.width, canvas.height);
      const barW = canvas.width / data.length;

      data.forEach((val, i) => {
        const barH = (val / 255) * canvas.height;
        ctx.fillStyle = color;
        ctx.fillRect(i * barW, canvas.height - barH, barW - 1, barH);
      });
    }

    render();
  }

  return (
    <div className="waveform" id="live-waveform">
      <div className="waveform__track">
        <span className="waveform__label">🎤 You</span>
        <canvas ref={callerCanvasRef} className="waveform__canvas" width={280} height={60} />
      </div>
      <div className="waveform__track">
        <span className="waveform__label">🤖 Agent</span>
        <canvas ref={agentCanvasRef} className="waveform__canvas" width={280} height={60} />
      </div>
    </div>
  );
}
