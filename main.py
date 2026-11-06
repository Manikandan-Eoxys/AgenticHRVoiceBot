"""
main.py  (project root)

Dispatcher — routes to the correct pipeline based on CLI argument.

Usage:
    python main.py asterisk     →  hr_voice_agent_asterisk_pipeline
    python main.py livekit      →  hr_voice_agent_livekit_pipeline
    python main.py dev          →  hr_voice_agent_livekit_pipeline  (dev alias)
    python main.py              →  reads VOICE_PIPELINE env var, defaults to asterisk
"""
import sys
import os

ROOT_DIR = os.path.dirname(os.path.abspath(__file__))

# ── Auto-reexec with venv Python if called with system Python ──────────────
venv_python = os.path.join(ROOT_DIR, ".venv", "bin", "python")
if (
    os.path.exists(venv_python)
    and sys.executable != venv_python
    and sys.prefix == getattr(sys, "base_prefix", sys.prefix)
):
    os.execv(venv_python, [venv_python] + sys.argv)

# ── Resolve pipeline from CLI arg, then env var, then default ─────────────
_LIVEKIT_ALIASES  = {"livekit", "lk", "dev"}
_ASTERISK_ALIASES = {"asterisk", "ast"}

_arg = sys.argv[1].strip().lower() if len(sys.argv) > 1 else ""
_env = os.environ.get("VOICE_PIPELINE", "").strip().lower()

if _arg in _LIVEKIT_ALIASES or (not _arg and _env in _LIVEKIT_ALIASES):
    target_dir = "hr_voice_agent_livekit_pipeline"
    _mode = "LIVEKIT"
elif _arg in _ASTERISK_ALIASES or (not _arg and _env in _ASTERISK_ALIASES):
    target_dir = "hr_voice_agent_asterisk_pipeline"
    _mode = "ASTERISK"
else:
    # Unknown arg or no arg at all → default to Asterisk
    target_dir = "hr_voice_agent_asterisk_pipeline"
    _mode = "ASTERISK"
    if _arg:
        print(f"[main] Unknown pipeline '{_arg}' — defaulting to asterisk.", file=sys.stderr)
        print(f"[main] Valid options: asterisk | ast | livekit | lk | dev", file=sys.stderr)

# ── Sanity check: does the target directory exist? ─────────────────────────
target_path = os.path.join(ROOT_DIR, target_dir)
if not os.path.isdir(target_path):
    # Fallback to whichever pipeline folder exists
    for fallback in ("hr_voice_agent_asterisk_pipeline", "hr_voice_agent_livekit_pipeline"):
        fb_path = os.path.join(ROOT_DIR, fallback)
        if os.path.isdir(fb_path):
            print(f"[main] '{target_dir}' not found — falling back to '{fallback}'", file=sys.stderr)
            target_dir  = fallback
            target_path = fb_path
            break
    else:
        print("[main] ERROR: No pipeline directory found. Exiting.", file=sys.stderr)
        sys.exit(1)

# ── Strip our own argv[1] so the sub-main doesn't see it ──────────────────
if _arg:
    sys.argv.pop(1)

# ── Launch the pipeline ────────────────────────────────────────────────────
print(f"[main] Starting {_mode} pipeline  ({target_dir}/main.py)")
sys.path.insert(0, target_path)
os.chdir(target_path)

import runpy
runpy.run_module("main", run_name="__main__", alter_sys=True)
