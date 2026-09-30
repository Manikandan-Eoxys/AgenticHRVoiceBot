/**
 * hr_voice_agent/frontend/src/components/DialPad.jsx
 *
 * Interactive DTMF keypad for sending 4-digit employee ID input.
 * Emits digits via WebRTC Data Channel to the agent's DtmfCollector.
 * Supports both click and keyboard input (0-9, *, #).
 */

import { useEffect } from "react";

const KEYS = [
  ["1", "2", "3"],
  ["4", "5", "6"],
  ["7", "8", "9"],
  ["*", "0", "#"],
];

export default function DialPad({ onDigit, disabled }) {
  // Support keyboard input when not disabled
  useEffect(() => {
    if (disabled) return;
    function handleKey(e) {
      if (/^[0-9*#]$/.test(e.key)) {
        onDigit(e.key);
      }
    }
    window.addEventListener("keydown", handleKey);
    return () => window.removeEventListener("keydown", handleKey);
  }, [disabled, onDigit]);

  return (
    <div className="dialpad" id="dtmf-dialpad" aria-label="DTMF Keypad">
      <h3 className="dialpad__title">Keypad</h3>
      <div className="dialpad__grid">
        {KEYS.flat().map((key) => (
          <button
            key={key}
            id={`dialpad-key-${key === "*" ? "star" : key === "#" ? "hash" : key}`}
            className="dialpad__key"
            disabled={disabled}
            onClick={() => onDigit(key)}
            aria-label={`Dial ${key}`}
          >
            {key}
          </button>
        ))}
      </div>
      {disabled && (
        <p className="dialpad__hint">Start a call to use the keypad</p>
      )}
    </div>
  );
}
