"""
main.py (project root)

Entrypoint shim — delegates to hr_voice_agent/main.py
Run from the project root:

    cd hr_voice_agent && python main.py dev

Or use the run script:
    ./hr_voice_agent/run_production.sh
"""
import sys
import os

# Add hr_voice_agent to Python path and run from there
hr_agent_dir = os.path.join(os.path.dirname(__file__), "hr_voice_agent")
sys.path.insert(0, hr_agent_dir)
os.chdir(hr_agent_dir)

# Now import and run the real entrypoint
import runpy
runpy.run_module("main", run_name="__main__", alter_sys=True)
