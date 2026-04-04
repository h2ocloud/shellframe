# ShellFrame installer for Windows
# Usage: irm https://raw.githubusercontent.com/h2ocloud/shellframe/main/install.ps1 | iex

$ErrorActionPreference = "Stop"

$InstallDir = "$env:USERPROFILE\.local\apps\shellframe"
$BinDir = "$env:USERPROFILE\.local\bin"

Write-Host "Installing ShellFrame..." -ForegroundColor Cyan

# Clone or update
if (Test-Path "$InstallDir\.git") {
    Write-Host "Updating existing installation..."
    Push-Location $InstallDir
    git pull --ff-only
    Pop-Location
} else {
    Write-Host "Cloning repository..."
    $parent = Split-Path $InstallDir -Parent
    if (-not (Test-Path $parent)) { New-Item -ItemType Directory -Path $parent -Force | Out-Null }
    git clone https://github.com/h2ocloud/shellframe.git $InstallDir
}

Push-Location $InstallDir

# Set up Python venv
if (-not (Test-Path ".venv")) {
    Write-Host "Creating virtual environment..."
    python -m venv .venv
}
Write-Host "Installing dependencies..."
.venv\Scripts\pip install -q -r requirements.txt

# CLI launcher
if (-not (Test-Path $BinDir)) { New-Item -ItemType Directory -Path $BinDir -Force | Out-Null }
$launcher = @"
@echo off
"%~dp0..\..\apps\shellframe\.venv\Scripts\python.exe" "%~dp0..\..\apps\shellframe\main.py" %*
"@
Set-Content -Path "$BinDir\shellframe.bat" -Value $launcher

Pop-Location

Write-Host ""
Write-Host "ShellFrame installed!" -ForegroundColor Green
Write-Host "  CLI:  shellframe"
Write-Host "  Path: $InstallDir"
Write-Host ""
Write-Host "Add $BinDir to your PATH if not already." -ForegroundColor Yellow
