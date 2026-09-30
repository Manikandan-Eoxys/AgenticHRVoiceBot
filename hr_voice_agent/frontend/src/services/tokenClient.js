/**
 * hr_voice_agent/frontend/src/services/tokenClient.js
 *
 * Token fetcher — calls token_server.py on :8000 to get a signed LiveKit JWT.
 * The Vite proxy forwards /getToken → http://localhost:8000/getToken.
 */

/**
 * Fetch a LiveKit JWT room token from the local token server.
 *
 * @param {string} participantName  - Display name for the WebRTC participant
 * @returns {Promise<{token: string, url: string}>}
 */
export async function fetchToken(participantName) {
  const res = await fetch(`/getToken?name=${encodeURIComponent(participantName)}`);
  if (!res.ok) {
    throw new Error(`Token fetch failed: ${res.status} ${res.statusText}`);
  }
  const data = await res.json();
  return {
    token: data.accessToken || data.token,
    url: data.url || import.meta.env.VITE_LIVEKIT_URL || "",
  };
}
