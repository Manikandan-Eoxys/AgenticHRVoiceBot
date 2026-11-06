/**
 * hr_voice_agent/frontend/src/components/LiveWaveform.jsx
 *
 * WebAudio API amplitude-bar visualizer for caller mic and agent speaker.
 * Shows an idle shimmer animation when not connected.
 */

import { useEffect, useRef } from "react";

export default function LiveWaveform({ room, connected }) {
  const callerCanvasRef = useRef(null);
  const agentCanvasRef  = useRef(null);
  const animRef         = useRef(null);
  const audioCtxRef     = useRef(null);

  useEffect(() => {
    if (!connected || !room) {
      cancelAnimationFrame(animRef.current);
      clearCanvas(callerCanvasRef.current, "#6c8fff22");
      clearCanvas(agentCanvasRef.current,  "#10b98122");
      return;
    }

    let micStream = null;

    async function startViz() {
      try {
        audioCtxRef.current = new AudioContext();
        const analyser = audioCtxRef.current.createAnalyser();
        analyser.fftSize = 256;

        micStream = await navigator.mediaDevices.getUserMedia({ audio: true });
        const source = audioCtxRef.current.createMediaStreamSource(micStream);
        source.connect(analyser);

        drawWaveform(callerCanvasRef.current, analyser, "#6c8fff");
        drawIdleWaveform(agentCanvasRef.current, "#10b981");
      } catch (_) {
        drawIdleWaveform(callerCanvasRef.current, "#6c8fff");
        drawIdleWaveform(agentCanvasRef.current,  "#10b981");
      }
    }

    startViz();

    return () => {
      cancelAnimationFrame(animRef.current);
      micStream?.getTracks().forEach((t) => t.stop());
      audioCtxRef.current?.close();
    };
  }, [connected, room]);

  function clearCanvas(canvas, fill = "transparent") {
    if (!canvas) return;
    const ctx = canvas.getContext("2d");
    ctx.clearRect(0, 0, canvas.width, canvas.height);
  }

  function drawWaveform(canvas, analyser, color) {
    if (!canvas || !analyser) return;
    const ctx  = canvas.getContext("2d");
    const data = new Uint8Array(analyser.frequencyBinCount);

    function render() {
      animRef.current = requestAnimationFrame(render);
      analyser.getByteFrequencyData(data);
      ctx.clearRect(0, 0, canvas.width, canvas.height);

      const barW = canvas.width / data.length;
      data.forEach((val, i) => {
        const barH = (val / 255) * canvas.height;
        const alpha = 0.4 + (val / 255) * 0.6;
        ctx.fillStyle = color + Math.round(alpha * 255).toString(16).padStart(2, "0");
        ctx.fillRect(i * barW, canvas.height - barH, Math.max(barW - 1, 1), barH);
      });
    }
    render();
  }

  // Gentle idle sine wave for agent output (no real stream)
  function drawIdleWaveform(canvas, color) {
    if (!canvas) return;
    const ctx = canvas.getContext("2d");
    let t = 0;

    function render() {
      animRef.current = requestAnimationFrame(render);
      ctx.clearRect(0, 0, canvas.width, canvas.height);
      ctx.beginPath();
      ctx.strokeStyle = color + "88";
      ctx.lineWidth = 1.5;

      for (let x = 0; x < canvas.width; x++) {
        const y = canvas.height / 2 + Math.sin((x / canvas.width) * Math.PI * 6 + t) * 10;
        x === 0 ? ctx.moveTo(x, y) : ctx.lineTo(x, y);
      }
      ctx.stroke();
      t += 0.06;
    }
    render();
  }

  return (
    <div className="waveform" id="live-waveform" aria-label="Audio Waveform">
      <div className="waveform__header">
        <span className="waveform__title">🔉 Audio Levels</span>
      </div>

      <div className="waveform__track">
        <span className="waveform__label">🎤 You</span>
        <canvas ref={callerCanvasRef} className="waveform__canvas" width={300} height={56} />
      </div>
      <div className="waveform__track">
        <span className="waveform__label">🤖 Agent</span>
        <canvas ref={agentCanvasRef}  className="waveform__canvas" width={300} height={56} />
      </div>
    </div>
  );
}
