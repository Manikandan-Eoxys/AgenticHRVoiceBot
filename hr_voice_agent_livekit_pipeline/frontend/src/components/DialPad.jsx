/**
 * hr_voice_agent/frontend/src/components/DialPad.jsx
 *
 * DTMF keypad with live digit display, keyboard support, and glow effects.
 * Shows last 8 digits typed in a monospace display above the grid.
 */

import { useEffect } from "react";

const KEYS = [
  ["1", "2", "3"],
  ["4", "5", "6"],
  ["7", "8", "9"],
  ["*", "0", "#"],
];

export default function DialPad({ onDigit, disabled, dtmfBuffer }) {
  // Keyboard support
  useEffect(() => {
    if (disabled) return;
    function handleKey(e) {
      if (/^[0-9*#]$/.test(e.key)) onDigit(e.key);
    }
    window.addEventListener("keydown", handleKey);
    return () => window.removeEventListener("keydown", handleKey);
  }, [disabled, onDigit]);

  return (
    <div className="dialpad" id="dtmf-dialpad" aria-label="DTMF Keypad">
      <div className="dialpad__header">
        <span className="dialpad__title">⌨️ Keypad — DTMF Input</span>
      </div>

      {/* Digit display */}
      <div className="dialpad__input-display" aria-label="DTMF digit buffer">
        {dtmfBuffer ? (
          dtmfBuffer.split("").map((d, i) => (
            <span key={i} style={{ marginRight: "0.2rem" }}>{d}</span>
          ))
        ) : (
          <span className="dialpad__input-placeholder">Enter employee ID…</span>
        )}
      </div>

      {/* Grid */}
      <div className="dialpad__grid">
        {KEYS.flat().map((key) => {
          const isSpecial = key === "*" || key === "#";
          return (
            <button
              key={key}
              id={`dialpad-key-${key === "*" ? "star" : key === "#" ? "hash" : key}`}
              className={`dialpad__key${isSpecial ? " dialpad__key--special" : ""}`}
              disabled={disabled}
              onClick={() => onDigit(key)}
              aria-label={`Dial ${key}`}
            >
              {key}
            </button>
          );
        })}
      </div>

      {disabled && (
        <p className="dialpad__hint">
          📞 Start a call to use the keypad
        </p>
      )}
    </div>
  );
}
