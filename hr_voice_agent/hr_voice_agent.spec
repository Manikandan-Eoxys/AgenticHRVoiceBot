# -*- mode: python ; coding: utf-8 -*-
# hr_voice_agent.spec — PyInstaller specification for HR Voice Agent
# Packaged as a one-folder distribution. Runtime config and secrets remain EXTERNAL.

import sys
from pathlib import Path
from PyInstaller.utils.hooks import collect_submodules, collect_data_files

block_cipher = None

# Base directories
hr_root = Path(SPECPATH).resolve()

# Hidden imports — ensure dynamically imported controllers, goals, and plugins are bundled
hidden_imports = [
    "dotenv",
    "jsonschema",
    "mysql.connector",
    "pymysql",
    "aiohttp",
    "numpy",
    "livekit",
    "livekit.agents",
    "livekit.plugins.deepgram",
    "livekit.plugins.cartesia",
    "livekit.plugins.silero",
]
hidden_imports += collect_submodules("controllers")
hidden_imports += collect_submodules("goals")
hidden_imports += collect_submodules("services")
hidden_imports += collect_submodules("datasources")
hidden_imports += collect_submodules("models")
hidden_imports += collect_submodules("security")
hidden_imports += collect_submodules("modules")

# Data files (schemas, prompt templates)
datas = [
    (str(hr_root / "schemas" / "config_schema.json"), "schemas"),
    (str(hr_root / "prompts"), "prompts"),
]

# Explicitly exclude secrets and environment files from being baked into the binary
binaries = []

a = Analysis(
    ["main.py"],
    pathex=[str(hr_root)],
    binaries=binaries,
    datas=datas,
    hiddenimports=hidden_imports,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[
        "tkinter",
        "matplotlib",
        "scipy",
        "torch",
        "torchvision",
    ],
    win_no_prefer_redirects=False,
    win_private_assemblies=False,
    cipher=block_cipher,
    noarchive=False,
)

pyz = PYZ(a.pure, a.zipped_data, cipher=block_cipher)

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name="hr_voice_agent",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    console=True,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
)

coll = COLLECT(
    exe,
    a.binaries,
    a.zipfiles,
    a.datas,
    strip=False,
    upx=True,
    upx_exclude=[],
    name="hr_voice_agent",
)
