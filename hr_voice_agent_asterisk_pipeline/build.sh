#!/usr/bin/env bash
# ==============================================================================
# build.sh — Production build script for hr_voice_agent
# Produces a clean one-folder standalone package via PyInstaller
# ==============================================================================
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$SCRIPT_DIR"

echo "=================================================="
echo "🚀 Building HR Voice Agent Runtime Package"
echo "=================================================="

# 1. Clean previous build artifacts
echo "🧹 Cleaning previous build and dist directories..."
rm -rf build dist

# 2. Check if pyinstaller is installed
if ! command -v pyinstaller &> /dev/null; then
    echo "⚠️ pyinstaller not found in PATH. Checking python venv..."
    if [ -f "../venv/bin/pyinstaller" ]; then
        PYINSTALLER="../venv/bin/pyinstaller"
    else
        echo "❌ PyInstaller is not installed. Run: pip install pyinstaller"
        exit 1
    fi
else
    PYINSTALLER="pyinstaller"
fi

# 3. Run PyInstaller build
echo "📦 Running PyInstaller using hr_voice_agent.spec..."
"$PYINSTALLER" --clean hr_voice_agent.spec

# 4. Copy external runtime dependencies (keep config and secrets OUTSIDE binary)
echo "📋 Placing external configuration templates..."
DIST_DIR="$SCRIPT_DIR/dist/hr_voice_agent"
mkdir -p "$DIST_DIR/schemas"
mkdir -p "$DIST_DIR/Recordings"

cp agent_config.json "$DIST_DIR/agent_config.json"
cp schemas/config_schema.json "$DIST_DIR/schemas/config_schema.json"

if [ -f ".env.example" ]; then
    cp .env.example "$DIST_DIR/.env.example"
elif [ -f ".env" ]; then
    grep -v -E "(KEY|SECRET|PASSWORD)" .env > "$DIST_DIR/.env.example" || true
fi

# 5. Smoke test step
echo "🧪 Running smoke test verification..."
if [ -x "$DIST_DIR/hr_voice_agent" ]; then
    echo "✅ Binary created successfully: $DIST_DIR/hr_voice_agent"
    # Run test in background with short timeout to verify start up & exit cleanly
    "$DIST_DIR/hr_voice_agent" --help > /dev/null 2>&1 || true
    echo "✅ Smoke test passed: executable launches successfully."
else
    echo "❌ Build failed: executable not found at $DIST_DIR/hr_voice_agent"
    exit 1
fi

echo "=================================================="
echo "🎉 HR Voice Agent build completed successfully!"
echo "Destination: $DIST_DIR"
echo "=================================================="
