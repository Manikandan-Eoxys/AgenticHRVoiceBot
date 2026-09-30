# ==============================================================================
# build.ps1 — Windows PowerShell build script for hr_voice_agent
# ==============================================================================
$ErrorActionPreference = "Stop"

$ScriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
Set-Location $ScriptDir

Write-Host "==================================================" -ForegroundColor Cyan
Write-Host "Building HR Voice Agent Runtime Package (Windows)" -ForegroundColor Cyan
Write-Host "==================================================" -ForegroundColor Cyan

# 1. Clean previous build artifacts
if (Test-Path "build") { Remove-Item -Recurse -Force "build" }
if (Test-Path "dist") { Remove-Item -Recurse -Force "dist" }

# 2. Check PyInstaller
$PyInstaller = "pyinstaller"
if (-not (Get-Command $PyInstaller -ErrorAction SilentlyContinue)) {
    if (Test-Path "..\venv\Scripts\pyinstaller.exe") {
        $PyInstaller = "..\venv\Scripts\pyinstaller.exe"
    } else {
        Write-Error "PyInstaller is not installed. Run: pip install pyinstaller"
    }
}

# 3. Run PyInstaller
Write-Host "Packaging with PyInstaller..." -ForegroundColor Yellow
& $PyInstaller --clean hr_voice_agent.spec

# 4. Copy configuration
$DistDir = "$ScriptDir\dist\hr_voice_agent"
New-Item -ItemType Directory -Force -Path "$DistDir\schemas" | Out-Null
New-Item -ItemType Directory -Force -Path "$DistDir\Recordings" | Out-Null

Copy-Item "agent_config.json" -Destination "$DistDir\agent_config.json"
Copy-Item "schemas\config_schema.json" -Destination "$DistDir\schemas\config_schema.json"

if (Test-Path ".env.example") {
    Copy-Item ".env.example" -Destination "$DistDir\.env.example"
}

Write-Host "Build complete! Package location: $DistDir" -ForegroundColor Green
