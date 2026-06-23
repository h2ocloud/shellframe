# ShellFrame installer for Windows
# Usage: irm https://raw.githubusercontent.com/h2ocloud/shellframe/main/install.ps1 | iex

$ErrorActionPreference = "Stop"

$InstallDir = "$env:USERPROFILE\.local\apps\shellframe"
$BinDir = "$env:USERPROFILE\.local\bin"
$RepoUrl = "https://github.com/h2ocloud/shellframe.git"

Write-Host "Installing ShellFrame..." -ForegroundColor Cyan

# ── Helpers ─────────────────────────────────────────────────
function Refresh-Path {
    # Re-read PATH from registry so freshly-installed tools resolve in THIS session
    $machine = [Environment]::GetEnvironmentVariable("Path", "Machine")
    $user = [Environment]::GetEnvironmentVariable("Path", "User")
    $env:Path = (@($machine, $user) | Where-Object { $_ } ) -join ";"
}

function Have-Command($name) {
    return [bool](Get-Command $name -ErrorAction SilentlyContinue)
}

function Python-IsReal {
    # The Microsoft Store ships a "python.exe" stub under WindowsApps that just
    # opens the Store. Treat that as "not installed".
    $cmd = Get-Command python -ErrorAction SilentlyContinue
    if (-not $cmd) { return $false }
    if ($cmd.Source -and $cmd.Source -like "*\WindowsApps\*") { return $false }
    try {
        $v = & python -c "import sys; print(sys.version_info[0])" 2>$null
        return ($v -eq "3")
    } catch { return $false }
}

function Winget-Install($id, $label) {
    if (-not (Have-Command winget)) {
        Write-Host "  winget not found — please install $label manually, then re-run." -ForegroundColor Red
        Write-Host "  (winget ships with App Installer from the Microsoft Store on Win10 1709+/Win11.)" -ForegroundColor DarkGray
        throw "winget unavailable; cannot auto-install $label"
    }
    Write-Host "  Installing $label via winget..." -ForegroundColor Yellow
    winget install --id $id -e --source winget `
        --accept-package-agreements --accept-source-agreements
    Refresh-Path
}

# ── 1. System dependencies (git, Python, WebView2) ──────────
Write-Host "Checking dependencies..."

if (-not (Have-Command git)) {
    Winget-Install "Git.Git" "Git"
    # winget puts git here; surface it in this session even before PATH refresh lands
    foreach ($p in @("$env:ProgramFiles\Git\cmd", "${env:ProgramFiles(x86)}\Git\cmd")) {
        if (Test-Path $p) { $env:Path = "$p;$env:Path" }
    }
    if (-not (Have-Command git)) { throw "git still not found after install. Open a new terminal and re-run." }
}

if (-not (Python-IsReal)) {
    Winget-Install "Python.Python.3.12" "Python 3.12"
    foreach ($p in @("$env:LOCALAPPDATA\Programs\Python\Python312",
                     "$env:LOCALAPPDATA\Programs\Python\Python312\Scripts")) {
        if (Test-Path $p) { $env:Path = "$p;$env:Path" }
    }
    if (-not (Python-IsReal)) {
        throw "python still not usable after install. Open a NEW terminal and re-run this installer."
    }
}

# WebView2 Runtime — pywebview needs it for the GUI window. Win10/11 usually ship
# it, but LTSC/Server/VMs may not. Probe registry; auto-install if missing.
$wvKeys = @(
    "HKLM:\SOFTWARE\WOW6432Node\Microsoft\EdgeUpdate\Clients\{F3017226-FE2A-4295-8BDF-00C3A9A7E4C5}",
    "HKLM:\SOFTWARE\Microsoft\EdgeUpdate\Clients\{F3017226-FE2A-4295-8BDF-00C3A9A7E4C5}",
    "HKCU:\SOFTWARE\Microsoft\EdgeUpdate\Clients\{F3017226-FE2A-4295-8BDF-00C3A9A7E4C5}"
)
$wvFound = $false
foreach ($k in $wvKeys) {
    $pv = (Get-ItemProperty -Path $k -Name pv -ErrorAction SilentlyContinue).pv
    if ($pv -and $pv -ne "0.0.0.0") { $wvFound = $true; break }
}
if (-not $wvFound) {
    Write-Host "  WebView2 Runtime not detected — installing..." -ForegroundColor Yellow
    try { Winget-Install "Microsoft.EdgeWebView2Runtime" "WebView2 Runtime" }
    catch {
        Write-Host "  Could not auto-install WebView2. If the window is blank, install it from:" -ForegroundColor DarkYellow
        Write-Host "    https://developer.microsoft.com/microsoft-edge/webview2/" -ForegroundColor DarkYellow
    }
}

# ── 2. Clone or update ──────────────────────────────────────
if (Test-Path "$InstallDir\.git") {
    Write-Host "Updating existing installation..."
    Push-Location $InstallDir
    # Auto-stash local changes so update never blocks (mirrors install.sh)
    $dirty = (git status --porcelain 2>$null)
    if ($dirty) {
        git stash push -u -m "install.ps1-auto-$(Get-Date -Format yyyyMMddHHmmss)" 2>$null | Out-Null
    }
    # ff-only pull; if history diverged, force-sync to origin/main.
    # try/catch because PowerShell 7.4+ turns a native non-zero exit into a
    # terminating error ($PSNativeCommandUseErrorActionPreference), which would
    # otherwise skip the force-sync fallback below.
    $pulled = $false
    try {
        git pull --ff-only 2>$null
        if ($LASTEXITCODE -eq 0) { $pulled = $true }
    } catch { }
    if (-not $pulled) {
        Write-Host "  ff-only pull failed — force-syncing to origin/main" -ForegroundColor DarkYellow
        git fetch origin main 2>$null | Out-Null
        git reset --hard origin/main 2>$null | Out-Null
    }
    Pop-Location
} elseif ((Test-Path $InstallDir) -and (Get-ChildItem $InstallDir -Force -ErrorAction SilentlyContinue)) {
    # Dir exists with files but no .git (zip download / copied tree). Back up and
    # clone clean instead of cloning into a non-empty dir (which fails).
    $backup = "$InstallDir.non-git-backup.$(Get-Date -Format yyyyMMddHHmmss)"
    Write-Host "Backing up non-git install to $backup"
    Move-Item -Path $InstallDir -Destination $backup -Force
    Write-Host "Cloning repository..."
    git clone $RepoUrl $InstallDir
} else {
    Write-Host "Cloning repository..."
    $parent = Split-Path $InstallDir -Parent
    if (-not (Test-Path $parent)) { New-Item -ItemType Directory -Path $parent -Force | Out-Null }
    git clone $RepoUrl $InstallDir
}

Push-Location $InstallDir

# ── 3. Python venv + pip dependencies ───────────────────────
if (-not (Test-Path ".venv")) {
    Write-Host "Creating virtual environment..."
    python -m venv .venv
}
Write-Host "Installing dependencies..."
.venv\Scripts\python.exe -m pip install -q --upgrade pip 2>&1 | Out-Null
.venv\Scripts\pip install -q -r requirements.txt

# ── 4. CLI launchers ────────────────────────────────────────
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
Write-Host "  If 'shellframe' is not found, open a NEW terminal (PATH was updated)." -ForegroundColor DarkGray
Write-Host ""
