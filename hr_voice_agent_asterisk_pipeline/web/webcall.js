/* web/webcall.js - Asterisk WebRTC (SIP over WebSocket via JsSIP) + AudioBridge Fallback */
(() => {
  const API = (window.WEBCALL_API_BASE || "").replace(/\/$/, "");
  const MESSAGES = {
    busy: "All agent lines are currently busy (SIP 486 Busy Here). Dialplan limit reached.",
    rate_limited: "Too many attempts. Please wait a minute and try again.",
    too_many_calls: "You already have an active call in progress (concurrency limit reached).",
    bad_offer: "Could not start the call. Please refresh the page and try again.",
    negotiation_failed: "Could not set up the WebRTC audio connection. Please try again.",
    time_limit: "The call reached its configured time limit.",
    pipeline_unreachable: "The Asterisk voice pipeline is offline right now. Please try again shortly.",
    connect_timeout: "Could not connect the audio stream. Check your network and try again.",
  };
  const $ = (id) => document.getElementById(id);
  let pc, stream, callId, dc, ctx, timerId, raf, started, ending, muted;
  let ua = null;
  let sipSession = null;
  let useAsteriskSip = typeof window.JsSIP !== "undefined";
  let fallbackTriggered = false;
  let bridgeStarting = false;

  function cleanupSIP() {
    if (sipSession) {
      try { sipSession.terminate(); } catch (_) {}
      sipSession = null;
    }
    if (ua) {
      try {
        ua.removeAllListeners();
        ua.stop();
      } catch (_) {}
      ua = null;
    }
  }

  function triggerFallback(reason) {
    if (fallbackTriggered || started || ending) return;
    fallbackTriggered = true;
    console.warn(`🔄 Triggering fallback to WebCall bridge (reason: ${reason})`);
    cleanupSIP();
    startDirectBridge();
  }

  function setState(state, title, sub) {
    $("wcStatus").textContent = {
      idle: "Ready",
      connecting: "Connecting",
      live: "Live",
      ended: "Call ended",
      error: "Unable to connect"
    }[state];
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

  function hookMeters(remoteStream) {
    try {
      ctx = ctx || new (window.AudioContext || window.webkitAudioContext)();
      const bars = [...document.querySelectorAll("#wcMeters span")];
      const mk = (s) => {
        const a = ctx.createAnalyser();
        a.fftSize = 128;
        ctx.createMediaStreamSource(s).connect(a);
        return a;
      };
      const agent = meter(mk(remoteStream), bars);
      const tick = () => { agent(); raf = requestAnimationFrame(tick); };
      tick();
    } catch (_) {}
  }

  function live() {
    started = true;
    setState("live", "Talking with HR Voicebot", "Asterisk WebRTC active. Speak naturally or use the keypad.");
    const t0 = Date.now();
    timerId = setInterval(() => {
      const s = Math.floor((Date.now() - t0) / 1000);
      $("wcTimer").textContent = String(Math.floor(s / 60)).padStart(2, "0") + ":" + String(s % 60).padStart(2, "0");
    }, 500);
  }

  function cleanup() {
    clearInterval(timerId);
    cancelAnimationFrame(raf);
    cleanupSIP();
    fallbackTriggered = false;
    bridgeStarting = false;
    try { dc && dc.close(); } catch (_) {}
    try { pc && pc.close(); } catch (_) {}
    if (stream) {
      stream.getTracks().forEach((t) => t.stop());
      stream = null;
    }
    if (ctx) {
      ctx.close().catch(() => {});
      ctx = null;
    }
    const audio = $("wcAudio");
    if (audio) audio.srcObject = null;
    pc = dc = null;
    $("wcMeters").querySelectorAll("span").forEach((b) => {
      b.style.height = "8px";
      b.style.opacity = ".45";
    });
  }

  function finish(reason) {
    ending = true;
    cleanup();
    const msg = MESSAGES[reason];
    if (reason === "busy" || reason === "too_many_calls") {
      setState("ended", "Line Busy", msg);
    } else if (reason === "time_limit" || (msg && reason !== "browser_hangup")) {
      setState("ended", "Call ended", msg);
    } else {
      setState("ended", "Thanks for calling", "Call again any time.");
    }
  }

  function fail(text) {
    ending = true;
    cleanup();
    setState("error", "Call not started", text);
  }

  function hangup() {
    if (sipSession) {
      try { sipSession.terminate(); } catch (_) {}
      sipSession = null;
    }
    if (callId) {
      fetch(API + "/api/webcall/hangup", {
        method: "POST",
        keepalive: true,
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ callId }),
      }).catch(() => {});
      callId = null;
    }
    ending = true;
    cleanup();
  }

  /* -------------------------------------------------------------------------
   * Method 1: Asterisk WebRTC (JsSIP over WS/WSS directly to Asterisk PJSIP)
   * ------------------------------------------------------------------------- */
  async function startAsteriskWebRTC() {
    const isSecure = window.location.protocol === "https:";
    const host = window.location.hostname || "127.0.0.1";
    // In HTTPS mode, Asterisk WSS is on port 8089; in HTTP mode, WS is on port 8088
    const wsUrl = window.ASTERISK_WS_URL || (isSecure ? `wss://${host}:8089/ws` : `ws://${host}:8088/ws`);
    const sipDomain = host;
    const sipUser = "webrtc-client";
    const sipPass = "webrtc_secret_pass";
    const targetExtension = "200";

    setState("connecting", "Calling Asterisk via WebRTC", `Connecting to Asterisk PJSIP (${wsUrl})...`);

    let callSessionTimer = null;
    let fallbackTimer = setTimeout(() => {
      if (!started && !ending) {
        triggerFallback("WebSocket connect timeout");
      }
    }, 1000);

    try {
      const socket = new window.JsSIP.WebSocketInterface(wsUrl);
      const configuration = {
        sockets: [socket],
        uri: `sip:${sipUser}@${sipDomain}`,
        password: sipPass,
        display_name: "WebRTC Browser Caller",
        session_timers: false,
        register: false,
      };

      ua = new window.JsSIP.UA(configuration);

      ua.on("connected", () => {
        clearTimeout(fallbackTimer);
        console.log("✅ [JsSIP] Connected to Asterisk WebSocket, calling extension...");
        setState("connecting", "Calling Extension 200", "Negotiating WebRTC audio session with Asterisk...");

        callSessionTimer = setTimeout(() => {
          if (!started && !ending) {
            triggerFallback("Asterisk SIP answer timeout");
          }
        }, 2500);

        try {
          sipSession = ua.call(targetExtension, options);
        } catch (callErr) {
          console.warn("⚠️ [JsSIP] ua.call error:", callErr);
          if (callSessionTimer) clearTimeout(callSessionTimer);
          triggerFallback("ua.call exception");
        }
      });

      ua.on("disconnected", () => {
        clearTimeout(fallbackTimer);
        if (callSessionTimer) clearTimeout(callSessionTimer);
        console.warn("⚠️ [JsSIP] Disconnected from Asterisk WebSocket.");
        if (!started && !ending) {
          triggerFallback("WebSocket disconnected");
        }
      });

      const eventHandlers = {
        progress: () => {
          clearTimeout(fallbackTimer);
          setState("connecting", "Ringing HR Voicebot", "Asterisk dialplan evaluating call concurrency limits...");
        },
        failed: (e) => {
          clearTimeout(fallbackTimer);
          if (callSessionTimer) clearTimeout(callSessionTimer);
          console.warn("❌ [JsSIP] Call failed:", e.cause, e.message);
          const statusCode = e.message ? e.message.status_code : null;
          if (statusCode === 486 || e.cause === window.JsSIP.C.causes.BUSY) {
            finish("busy");
          } else if (statusCode === 503) {
            finish("too_many_calls");
          } else {
            triggerFallback("Call setup failed: " + e.cause);
          }
        },
        ended: (e) => {
          clearTimeout(fallbackTimer);
          if (callSessionTimer) clearTimeout(callSessionTimer);
          console.log("📴 [JsSIP] Call ended:", e.cause);
          if (!ending) finish(e.cause === window.JsSIP.C.causes.BUSY ? "busy" : "normal");
        },
        confirmed: () => {
          clearTimeout(fallbackTimer);
          if (callSessionTimer) clearTimeout(callSessionTimer);
          console.log("🎉 [JsSIP] Asterisk Call answered (Stasis active).");
          live();
          const peer = sipSession.connection;
          const remoteAudio = $("wcAudio");
          if (peer) {
            const remoteStreams = peer.getRemoteStreams();
            if (remoteStreams && remoteStreams.length > 0) {
              remoteAudio.srcObject = remoteStreams[0];
              remoteAudio.play().catch(() => {});
              hookMeters(remoteStreams[0]);
            } else {
              peer.ontrack = (ev) => {
                const streamObj = ev.streams[0] || new MediaStream([ev.track]);
                remoteAudio.srcObject = streamObj;
                remoteAudio.play().catch(() => {});
                hookMeters(streamObj);
              };
            }
          }
        },
      };

      const iceServers = window.WEBCALL_ICE_SERVERS || [{ urls: "stun:stun.l.google.com:19302" }];

      const options = {
        eventHandlers: eventHandlers,
        mediaConstraints: { audio: true, video: false },
        pcConfig: {
          iceServers: iceServers,
        },
      };

      ua.start();
    } catch (err) {
      clearTimeout(fallbackTimer);
      console.warn("⚠️ Asterisk WebRTC init error, falling back to WebCall bridge:", err);
      triggerFallback("Asterisk init exception: " + err);
    }
  }

  /* -------------------------------------------------------------------------
   * Method 2: Direct WebCall Bridge (Fallback: browser WebRTC -> AudioSocket)
   * ------------------------------------------------------------------------- */
  async function startDirectBridge() {
    if (bridgeStarting || started || ending) return;
    bridgeStarting = true;
    cleanupSIP();
    setState("connecting", "Calling HR Voicebot", "Connecting via WebRTC Audio Bridge...");

    if (!stream || stream.getTracks().every((t) => t.readyState === "ended")) {
      try {
        stream = await navigator.mediaDevices.getUserMedia({
          audio: { echoCancellation: true, noiseSuppression: true, autoGainControl: true },
        });
      } catch (e) {
        return fail("Microphone access was lost or blocked.");
      }
    }
    function waitIce(conn) {
      if (conn.iceGatheringState === "complete") return Promise.resolve();
      return new Promise((res) => {
        const done = () =>
          conn.iceGatheringState === "complete" &&
          (conn.removeEventListener("icegatheringstatechange", done), res());
        conn.addEventListener("icegatheringstatechange", done);
        setTimeout(res, 400);
      });
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
        method: "POST",
        headers: { "Content-Type": "application/json" },
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

  async function start() {
    ending = false;
    muted = false;
    started = false;
    fallbackTriggered = false;
    bridgeStarting = false;
    $("wcMute").classList.remove("on");
    $("wcMute").textContent = "Mute";
    if (!window.isSecureContext || !navigator.mediaDevices) {
      return fail("Your browser only allows the microphone on https:// pages or localhost. Open this page over HTTPS.");
    }
    setState("connecting", "Calling HR Voicebot", "Allow microphone access when your browser asks.");
    try {
      stream = await navigator.mediaDevices.getUserMedia({
        audio: { echoCancellation: true, noiseSuppression: true, autoGainControl: true },
      });
    } catch (e) {
      return fail("Microphone access was blocked. Allow it in your browser's address bar and try again.");
    }

    if (useAsteriskSip && window.JsSIP) {
      startAsteriskWebRTC();
    } else {
      startDirectBridge();
    }
  }

  function open() {
    $("wcOverlay").classList.add("open");
    document.body.style.overflow = "hidden";
    start();
  }

  function close() {
    hangup();
    $("wcOverlay").classList.remove("open");
    document.body.style.overflow = "";
    $("wcTimer").textContent = "";
  }

  document.addEventListener("DOMContentLoaded", () => {
    $("hrVoicebotBtn").addEventListener("click", (e) => {
      e.preventDefault();
      open();
    });
    $("wcClose").onclick = close;
    $("wcEnd").onclick = () => {
      hangup();
      setState("ended", "Thanks for calling", "Call again any time.");
    };
    $("wcRetry").onclick = () => {
      hangup();
      callId = null;
      open();
    };
    $("wcMute").onclick = () => {
      muted = !muted;
      if (sipSession) {
        if (muted) sipSession.mute({ audio: true });
        else sipSession.unmute({ audio: true });
      } else if (stream) {
        stream.getAudioTracks().forEach((t) => (t.enabled = !muted));
      }
      $("wcMute").classList.toggle("on", muted);
      $("wcMute").textContent = muted ? "Unmute" : "Mute";
    };
    $("wcKeys").onclick = () => $("wcPad").classList.toggle("open");
    $("wcPad").addEventListener("click", (e) => {
      const d = e.target.dataset.d;
      if (d) {
        if (sipSession) {
          // Native Asterisk DTMF over SIP INFO / RFC 4733
          try { sipSession.sendDTMF(d); } catch (_) {}
        } else if (dc && dc.readyState === "open") {
          dc.send(JSON.stringify({ digit: d }));
        }
      }
    });
    document.addEventListener("keydown", (e) => {
      if (e.key === "Escape" && $("wcOverlay").classList.contains("open")) close();
    });
    window.addEventListener("pagehide", hangup);
  });
})();
