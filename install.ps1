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

# CLI launchers
if (-not (Test-Path $BinDir)) { New-Item -ItemType Directory -Path $BinDir -Force | Out-Null }

# shellframe — main app (absolute paths)
$launcherMain = @"
@echo off
"$InstallDir\.venv\Scripts\python.exe" "$InstallDir\main.py" %*
"@
Set-Content -Path "$BinDir\shellframe.bat" -Value $launcherMain

# shellframe — GUI launcher (no console window)
$vbsLauncher = @"
Set WshShell = CreateObject("WScript.Shell")
WshShell.CurrentDirectory = "$InstallDir"
WshShell.Run """$InstallDir\.venv\Scripts\pythonw.exe"" ""$InstallDir\main.py""", 0, False
"@
Set-Content -Path "$BinDir\shellframe.vbs" -Value $vbsLauncher

# sfctl — remote control for AI agents (absolute paths)
$launcherSfctl = @"
@echo off
"$InstallDir\.venv\Scripts\python.exe" "$InstallDir\sfctl.py" %*
"@
Set-Content -Path "$BinDir\sfctl.bat" -Value $launcherSfctl

# Ensure BinDir is in user PATH
$userPath = [Environment]::GetEnvironmentVariable("Path", "User")
if (-not $userPath) { $userPath = "" }
if ($userPath -split ";" | Where-Object { $_ -eq $BinDir }) {
    # already in PATH
} else {
    $newPath = if ($userPath) { "$BinDir;$userPath" } else { $BinDir }
    [Environment]::SetEnvironmentVariable("Path", $newPath, "User")
    $env:Path = "$BinDir;$env:Path"
    Write-Host "  Added $BinDir to user PATH" -ForegroundColor Yellow
}

# Desktop shortcut (standalone GUI, no terminal window)
try {
    $desktopPath = [Environment]::GetFolderPath("Desktop")
    $shortcutPath = "$desktopPath\ShellFrame.lnk"
    # Always recreate to pick up icon and VBS launcher changes
    $shell = New-Object -ComObject WScript.Shell
    $shortcut = $shell.CreateShortcut($shortcutPath)
    $shortcut.TargetPath = "$BinDir\shellframe.vbs"
    $shortcut.WorkingDirectory = $InstallDir
    $shortcut.Description = "ShellFrame"
    $shortcut.IconLocation = "$InstallDir\icon.ico"
    $shortcut.Save()
    Write-Host "  Desktop shortcut created (with icon)" -ForegroundColor Yellow
} catch {
    Write-Host "  (Skipped desktop shortcut: $_)" -ForegroundColor DarkGray
}

# Read version
$version = "?"
try {
    $versionJson = Get-Content "$InstallDir\version.json" -Raw | ConvertFrom-Json
    $version = $versionJson.version
} catch {}

Pop-Location

Write-Host ""
Write-Host "ShellFrame v$version installed!" -ForegroundColor Green
Write-Host "  CLI:       shellframe"
Write-Host "  Control:   sfctl"
Write-Host "  Path:      $InstallDir"
Write-Host ""
Write-Host "  Run ``sfctl permissions`` once to pre-add Windows Defender" -ForegroundColor Yellow
Write-Host "  Firewall rules for the bundled Python — avoids the one-time" -ForegroundColor Yellow
Write-Host "  'Allow network access' popup on first launch." -ForegroundColor Yellow
Write-Host ""
