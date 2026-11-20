/* web/webcall.js - browser side of the web call: mic -> WebRTC -> /api/webcall/offer -> agent voice */
(() => {
  const API = (window.WEBCALL_API_BASE || "").replace(/\/$/, "");
  const MESSAGES = {
    busy: "All demo lines are busy right now. Please try again in a minute.",
    rate_limited: "Too many attempts. Please wait a minute and try again.",
    too_many_calls: "You already have a call in progress on this network.",
    bad_offer: "Could not start the call. Please refresh the page and try again.",
    negotiation_failed: "Could not set up the audio connection. Please try again.",
    time_limit: "The demo call reached its time limit.",
    pipeline_unreachable: "The voice agent is offline right now. Please try again shortly.",
    connect_timeout: "Could not connect the audio. Check your network and try again.",
  };
  const $ = (id) => document.getElementById(id);
  let pc, stream, callId, dc, ctx, timerId, raf, started, ending, muted;

  function setState(state, title, sub) {
    $("wcCard").dataset.state = state;
    $("wcStatus").textContent = { idle: "Ready", connecting: "Connecting", live: "Live", ended: "Call ended", error: "Unable to connect" }[state];
    $("wcTitle").textContent = title;
    $("wcSub").textContent = sub || "";
    const live = state === "live" || state === "connecting";
    $("wcMute").hidden = $("wcKeys").hidden = $("wcEnd").hidden = !live;
    $("wcRetry").hidden = live;
    if (!live) $("wcPad").classList.remove("open");
  }

  function meter(analyser, bars) {
    const data = new Uint8Array(analyser.frequencyBinCount);
    return () => {
      analyser.getByteFrequencyData(data);
      const level = data.slice(2, 40).reduce((a, b) => a + b, 0) / 38 / 255;
      bars.forEach((b, i) => {
        const wobble = 0.55 + 0.45 * Math.sin(Date.now() / 140 + i);
        b.style.height = 8 + level * 70 * wobble + "px";
        b.style.opacity = 0.45 + Math.min(level * 2, 0.55);
      });
    };
  }

  function waitIce(conn) {
    if (conn.iceGatheringState === "complete") return Promise.resolve();
    return new Promise((res) => {
      const done = () => conn.iceGatheringState === "complete" && (conn.removeEventListener("icegatheringstatechange", done), res());
      conn.addEventListener("icegatheringstatechange", done);
      setTimeout(res, 2500);
    });
  }

  async function start() {
    ending = false; muted = false; started = false;
    $("wcMute").classList.remove("on"); $("wcMute").textContent = "Mute";
    if (!window.isSecureContext || !navigator.mediaDevices) return fail("Your browser only allows the microphone on https:// pages or localhost. Open this page over HTTPS.");
    setState("connecting", "Calling HR Voicebot", "Allow microphone access when your browser asks.");
    try {
      stream = await navigator.mediaDevices.getUserMedia({ audio: { echoCancellation: true, noiseSuppression: true, autoGainControl: true } });
    } catch (e) {
      return fail("Microphone access was blocked. Allow it in your browser's address bar and try again.");
    }
    try {
      const cfg = await (await fetch(API + "/api/webcall/config")).json();
      pc = new RTCPeerConnection({ iceServers: cfg.iceServers || [] });
      stream.getTracks().forEach((t) => pc.addTrack(t, stream));
      dc = pc.createDataChannel("control");
      dc.onmessage = (m) => {
        const msg = JSON.parse(m.data);
        if (msg.event === "call_ended" && !ending) finish(msg.reason);
      };
      pc.ontrack = (e) => {
        const audio = $("wcAudio");
        audio.srcObject = e.streams[0] || new MediaStream([e.track]);
        audio.play().catch(() => {});
        hookMeters(e.streams[0] || new MediaStream([e.track]));
      };
      pc.onconnectionstatechange = () => {
        if (pc.connectionState === "connected" && !started) live();
        if (["failed", "closed"].includes(pc.connectionState) && !ending) finish("connect_timeout");
      };
      await pc.setLocalDescription(await pc.createOffer());
      await waitIce(pc);
      const r = await fetch(API + "/api/webcall/offer", {
        method: "POST", headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ type: "offer", sdp: pc.localDescription.sdp }),
      });
      const body = await r.json().catch(() => ({}));
      if (!r.ok) return fail(MESSAGES[body.error] || "Something went wrong. Please try again.");
      callId = body.callId;
      await pc.setRemoteDescription({ type: body.type, sdp: body.sdp });
    } catch (e) {
      fail("Could not reach the voice agent. Check your connection and try again.");
    }
  }

  function hookMeters(remote) {
    try {
      ctx = ctx || new (window.AudioContext || window.webkitAudioContext)();
      const bars = [...document.querySelectorAll("#wcMeters span")];
      const mk = (s) => { const a = ctx.createAnalyser(); a.fftSize = 128; ctx.createMediaStreamSource(s).connect(a); return a; };
      const agent = meter(mk(remote), bars);
      const tick = () => { agent(); raf = requestAnimationFrame(tick); };
      tick();
    } catch (_) {}
  }

  function live() {
    started = true;
    setState("live", "You're talking to the HR Voicebot", "Just speak naturally. The agent will greet you.");
    const t0 = Date.now();
    timerId = setInterval(() => {
      const s = Math.floor((Date.now() - t0) / 1000);
      $("wcTimer").textContent = String(Math.floor(s / 60)).padStart(2, "0") + ":" + String(s % 60).padStart(2, "0");
    }, 500);
  }

  function cleanup() {
    clearInterval(timerId); cancelAnimationFrame(raf);
    try { dc && dc.close(); } catch (_) {}
    try { pc && pc.close(); } catch (_) {}
    stream && stream.getTracks().forEach((t) => t.stop());
    if (ctx) { ctx.close().catch(() => {}); ctx = null; }
    $("wcAudio").srcObject = null; pc = stream = dc = null;
    $("wcMeters").querySelectorAll("span").forEach((b) => { b.style.height = "8px"; b.style.opacity = ".45"; });
  }

  function finish(reason) {
    ending = true; cleanup();
    const msg = MESSAGES[reason];
    if (reason === "time_limit" || (msg && reason !== "browser_hangup")) setState("ended", "Call ended", msg);
    else setState("ended", "Thanks for calling", "Call again any time.");
  }

  function fail(text) { ending = true; cleanup(); setState("error", "Call not started", text); }

  function hangup() {
    if (callId) fetch(API + "/api/webcall/hangup", { method: "POST", keepalive: true, headers: { "Content-Type": "application/json" }, body: JSON.stringify({ callId }) }).catch(() => {});
    callId = null; ending = true; cleanup();
  }

  function open() { $("wcOverlay").classList.add("open"); document.body.style.overflow = "hidden"; start(); }
  function close() { hangup(); $("wcOverlay").classList.remove("open"); document.body.style.overflow = ""; $("wcTimer").textContent = ""; }

  document.addEventListener("DOMContentLoaded", () => {
    $("hrVoicebotBtn").addEventListener("click", (e) => { e.preventDefault(); open(); });
    $("wcClose").onclick = close;
    $("wcEnd").onclick = () => { hangup(); setState("ended", "Thanks for calling", "Call again any time."); };
    $("wcRetry").onclick = () => { hangup(); callId = null; open(); };
    $("wcMute").onclick = () => {
      muted = !muted; stream && stream.getAudioTracks().forEach((t) => (t.enabled = !muted));
      $("wcMute").classList.toggle("on", muted); $("wcMute").textContent = muted ? "Unmute" : "Mute";
    };
    $("wcKeys").onclick = () => $("wcPad").classList.toggle("open");
    $("wcPad").addEventListener("click", (e) => {
      const d = e.target.dataset.d;
      if (d && dc && dc.readyState === "open") dc.send(JSON.stringify({ digit: d }));
    });
    document.addEventListener("keydown", (e) => { if (e.key === "Escape" && $("wcOverlay").classList.contains("open")) close(); });
    window.addEventListener("pagehide", hangup);
  });
})();
