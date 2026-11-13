# ShellBot (formerly ShellFrame) installer for Windows.
#
#   irm https://raw.githubusercontent.com/h2ocloud/shellframe/main/install.ps1 | iex
#
# One line from a new PC to ShellBot open. It installs what is missing (Git, Python 3.12,
# the WebView2 runtime), then ShellBot, its Python packages, the shellframe / sfctl
# commands and a desktop shortcut. Safe to run again; running it again is also how an
# install is updated. Uses winget when it works; without it (Windows Sandbox, LTSC,
# Server) it downloads the same official installers directly.
#
# What may need you: Windows may ask "Do you want to allow this app to make changes to
# your device?" (User Account Control) while Git or WebView2 installs: click Yes. Each
# such step is announced first. AI CLIs (Claude Code, Codex) are optional; the end of the
# install offers them.
#
# Environment, all optional (the same as install.sh):
#   SHELLFRAME_REF, SHELLFRAME_REPO, SHELLFRAME_LANG (en|zh), SHELLFRAME_DRY_RUN=1,
#   SHELLFRAME_NONINTERACTIVE=1, SHELLFRAME_INSTALL_CLIS (claude,codex|none),
#   SHELLFRAME_NO_LAUNCH=1
#
# Everything runs inside & { ... }: its settings ($ErrorActionPreference, the current
# folder) do not stay behind in your PowerShell window, and it never calls `exit`, which
# under `irm | iex` would close that window.

& {
$ErrorActionPreference = "Stop"
# Invoke-WebRequest in Windows PowerShell 5.1 is many times slower while it draws progress.
$ProgressPreference = "SilentlyContinue"

$InstallDir = "$env:USERPROFILE\.local\apps\shellframe"
$BinDir = "$env:USERPROFILE\.local\bin"
$RepoUrl = if ($env:SHELLFRAME_REPO) { $env:SHELLFRAME_REPO } else { "https://github.com/h2ocloud/shellframe.git" }
$Ref = if ($env:SHELLFRAME_REF) { $env:SHELLFRAME_REF } else { "main" }
$DryRun = ($env:SHELLFRAME_DRY_RUN -eq "1")
$PythonVersion = "3.12.10"     # the python.org installer used when winget cannot install Python
$Started = Get-Date

# State the helpers share. A hashtable, so functions can change it without scope modifiers
# (under iex, $script: would be the caller's global scope).
$S = @{ Step = ""; Hint = ""; LastCmd = ""; Installed = @(); Next = @(); Fresh = $false; Pushed = $false;
        Winget = $false; Python = "" }

$lang = $env:SHELLFRAME_LANG
if (-not $lang) { try { $lang = (Get-UICulture).Name } catch { $lang = "" } }
# Not $Zh: PowerShell names are case-insensitive, so the parameter $zh of T would hide it.
$UseZh = ($lang -like "zh*")

function T([string]$en, [string]$zh) { if ($UseZh) { $zh } else { $en } }
function Say([string]$en, [string]$zh, [string]$color = "Gray") { Write-Host (T $en $zh) -ForegroundColor $color }
function Note([string]$en, [string]$zh) { Write-Host ("    " + (T $en $zh)) -ForegroundColor DarkGray }
function Warn([string]$en, [string]$zh) { Write-Host ("    ! " + (T $en $zh)) -ForegroundColor DarkYellow }
function Hint([string]$en, [string]$zh) { $S.Hint = T $en $zh }
function Step([string]$en, [string]$zh) {
    $S.Step = T $en $zh
    $S.Hint = ""
    Write-Host ""
    Write-Host "==> $($S.Step)" -ForegroundColor Cyan
}
function Installing([string]$name, [string]$whyEn, [string]$whyZh) {
    Write-Host ("    " + (T "Installing $name ($whyEn)" "正在裝 ${name}（${whyZh}）")) -ForegroundColor Yellow
    $S.Installed += $name
}
function Fail([string]$en, [string]$zh) { throw (T $en $zh) }

function Have-Command($name) { return [bool](Get-Command $name -ErrorAction SilentlyContinue) }

# A person can answer only in an interactive console. `powershell -NonInteractive`, a
# scheduled task or a remote session takes the defaults instead of waiting.
$CanAsk = ($env:SHELLFRAME_NONINTERACTIVE -ne "1") -and [Environment]::UserInteractive -and
          ($Host.Name -eq "ConsoleHost") -and -not ([Environment]::GetCommandLineArgs() -match '^-noni')

function Ask([string]$en, [string]$zh) {
    if (-not $CanAsk) { return $false }
    $a = Read-Host ("    " + (T $en $zh))
    return ($a -match '^(y|yes|是|好|要)$')
}

function Refresh-Path {
    # Machine + user PATH from the registry, so a tool installed a moment ago resolves in
    # this window, while keeping what this session added itself.
    $machine = [Environment]::GetEnvironmentVariable("Path", "Machine")
    $user = [Environment]::GetEnvironmentVariable("Path", "User")
    $seen = @{}
    $parts = @()
    foreach ($p in (@($env:Path, $machine, $user) -join ";").Split(";")) {
        if ($p -and -not $seen.ContainsKey($p.ToLower())) { $seen[$p.ToLower()] = $true; $parts += $p }
    }
    $env:Path = $parts -join ";"
}

function Add-SessionPath([string[]]$dirs) {
    foreach ($d in $dirs) { if ($d -and (Test-Path $d)) { $env:Path = "$d;$env:Path" } }
}

# Run a program and return its exit code. Native programs do not throw in PowerShell 5.1,
# and with $ErrorActionPreference = Stop their stderr lines can turn into errors, so they
# run with Continue and the caller checks the code. Output goes to the screen.
function Invoke-Tool([string]$Exe, [string[]]$ArgList) {
    $S.LastCmd = "$Exe $($ArgList -join ' ')"
    if ($DryRun) { Write-Host "    [dry-run] $($S.LastCmd)"; return 0 }
    $old = $ErrorActionPreference
    $ErrorActionPreference = "Continue"
    try {
        & $Exe @ArgList | Out-Host
        $code = $LASTEXITCODE
    } finally { $ErrorActionPreference = $old }
    if ($null -eq $code) { $code = 0 }
    return $code
}

function Download([string]$url, [string]$file) {
    $S.LastCmd = "download $url"
    if ($DryRun) { Write-Host "    [dry-run] download $url"; return }
    Invoke-WebRequest -Uri $url -OutFile $file -UseBasicParsing
}

# Run a downloaded installer and wait for it.
function Start-Installer([string]$file, [string[]]$ArgList) {
    $S.LastCmd = "$file $($ArgList -join ' ')"
    if ($DryRun) { Write-Host "    [dry-run] $($S.LastCmd)"; return 0 }
    $p = Start-Process -FilePath $file -ArgumentList $ArgList -Wait -PassThru
    return $p.ExitCode
}

function Say-Uac {
    Say "    Windows may now ask `"Do you want to allow this app to make changes to your device?`" Click Yes." `
        "    Windows 可能會跳出「是否允許此 App 變更您的裝置？」（使用者帳戶控制）視窗，請按「是」。" "Yellow"
    Say "    (If it does not come to the front, look for a flashing shield icon in the taskbar.)" `
        "    （如果視窗沒有跳到最前面，請看工作列上閃爍的盾牌圖示。）"
}

# winget, if it is there and works. A new Windows account may have App Installer but not
# registered yet; registering it is quiet and needs no admin.
function Find-Winget {
    if (Have-Command winget) { return $true }
    if ($DryRun) { Write-Host "    [dry-run] Add-AppxPackage -RegisterByFamilyName -MainPackage Microsoft.DesktopAppInstaller_8wekyb3d8bbwe"; return $false }
    try {
        Add-AppxPackage -RegisterByFamilyName -MainPackage Microsoft.DesktopAppInstaller_8wekyb3d8bbwe -ErrorAction Stop
        Refresh-Path
    } catch { }
    return (Have-Command winget)
}

function Winget-Install([string]$id, [string[]]$extra = @()) {
    if (-not $S.Winget) { return $false }
    $code = Invoke-Tool "winget" (@("install", "--id", $id, "-e", "--source", "winget", "--silent",
                                    "--accept-package-agreements", "--accept-source-agreements") + $extra)
    Refresh-Path
    return ($code -eq 0)
}

function Arch { if ($env:PROCESSOR_ARCHITECTURE -eq "ARM64") { "arm64" } else { "amd64" } }

# ── Git ─────────────────────────────────────────────────────

function Git-Ok {
    if (-not (Have-Command git)) { return $false }
    $old = $ErrorActionPreference; $ErrorActionPreference = "Continue"
    try { & git --version 2>$null | Out-Null; return ($LASTEXITCODE -eq 0) } catch { return $false }
    finally { $ErrorActionPreference = $old }
}

function Ensure-Git {
    Step "Git" "Git"
    if (Git-Ok) { Note "Already installed." "已經有了。"; return }
    Installing "Git" "to download ShellBot and update it" "下載和更新 ShellBot 用"
    Hint "Install Git for Windows from https://git-scm.com/download/win, open a new PowerShell window, and paste the install line again." `
         "請從 https://git-scm.com/download/win 安裝 Git for Windows，開一個新的 PowerShell 視窗，再貼一次安裝指令。"
    Say-Uac
    $ok = Winget-Install "Git.Git"
    if (-not $ok -and -not $DryRun -and -not (Git-Ok)) {
        Note "Downloading Git for Windows from its GitHub releases..." "從 GitHub 下載 Git for Windows 安裝程式…"
        $arch = if ((Arch) -eq "arm64") { "arm64" } else { "64-bit" }
        $rel = Invoke-RestMethod -Uri "https://api.github.com/repos/git-for-windows/git/releases/latest" -UseBasicParsing
        $asset = $rel.assets | Where-Object { $_.name -match "^Git-[\d.]+-$arch\.exe$" } | Select-Object -First 1
        if (-not $asset) { Fail "No Git for Windows installer found in its latest release." "Git for Windows 最新版裡找不到安裝程式。" }
        $file = Join-Path $env:TEMP $asset.name
        Download $asset.browser_download_url $file
        $code = Start-Installer $file @("/VERYSILENT", "/NORESTART", "/NOCANCEL", "/SP-", "/SUPPRESSMSGBOXES")
        if ($code -ne 0) { Fail "The Git installer stopped (exit $code)." "Git 安裝程式中途停止（exit ${code}）。" }
    } elseif ($DryRun -and -not $S.Winget) {
        Write-Host "    [dry-run] download Git for Windows (latest, from github.com/git-for-windows) and run it /VERYSILENT"
    }
    if ($DryRun) { return }
    Refresh-Path
    Add-SessionPath @("$env:ProgramFiles\Git\cmd", "$env:LOCALAPPDATA\Programs\Git\cmd")
    if (-not (Git-Ok)) { Fail "Git was installed but is not found yet." "Git 裝了，但這個視窗還找不到它。" }
}

# ── Python ──────────────────────────────────────────────────

# A Python 3.10+ that really runs. The Microsoft Store puts a python.exe stub in
# WindowsApps that only opens the Store; it does not count.
function Python-Ok([string]$exe) {
    if (-not $exe) { return $false }
    if ($exe -like "*\WindowsApps\*") { return $false }
    if (-not (Test-Path $exe)) { return $false }
    $old = $ErrorActionPreference; $ErrorActionPreference = "Continue"
    try {
        & $exe -c "import sys; sys.exit(0 if sys.version_info >= (3, 10) else 1)" 2>$null | Out-Null
        return ($LASTEXITCODE -eq 0)
    } catch { return $false }
    finally { $ErrorActionPreference = $old }
}

function Find-Python {
    $candidates = @()
    $cmd = Get-Command python -ErrorAction SilentlyContinue
    if ($cmd) { $candidates += $cmd.Source }
    foreach ($v in @("312", "313", "311", "310", "314")) {
        $candidates += "$env:LOCALAPPDATA\Programs\Python\Python$v\python.exe"
        $candidates += "$env:ProgramFiles\Python$v\python.exe"
    }
    foreach ($c in $candidates) { if (Python-Ok $c) { return $c } }
    return ""
}

function Ensure-Python {
    Step "Python 3" "Python 3"
    $S.Python = Find-Python
    if ($S.Python) { Note "Using $($S.Python)" "使用 $($S.Python)"; return }
    Installing "Python 3.12" "ShellBot is written in Python" "ShellBot 是用 Python 寫的"
    Hint "Install Python 3.12 from https://www.python.org/downloads/windows/ (tick `"Add python.exe to PATH`"), open a new PowerShell window, and paste the install line again." `
         "請從 https://www.python.org/downloads/windows/ 安裝 Python 3.12（勾選「Add python.exe to PATH」），開一個新的 PowerShell 視窗，再貼一次安裝指令。"
    Note "Installed for this Windows account only, so it needs no administrator." "只裝給目前這個 Windows 帳號，不需要管理員權限。"
    [void](Winget-Install "Python.Python.3.12" @("--scope", "user"))
    if (-not $DryRun) { $S.Python = Find-Python }
    if (-not $S.Python -and -not $DryRun) {
        Note "Downloading Python $PythonVersion from python.org..." "從 python.org 下載 Python ${PythonVersion}…"
        $file = Join-Path $env:TEMP "python-$PythonVersion-$(Arch).exe"
        Download "https://www.python.org/ftp/python/$PythonVersion/python-$PythonVersion-$(Arch).exe" $file
        $code = Start-Installer $file @("/quiet", "InstallAllUsers=0", "PrependPath=1", "Include_test=0", "Include_launcher=0")
        if ($code -ne 0) { Fail "The Python installer stopped (exit $code)." "Python 安裝程式中途停止（exit ${code}）。" }
        Refresh-Path
        $S.Python = Find-Python
    } elseif ($DryRun -and -not $S.Winget) {
        Write-Host "    [dry-run] download python-$PythonVersion-$(Arch).exe from python.org and run it /quiet InstallAllUsers=0 PrependPath=1"
    }
    if ($DryRun) { $S.Python = "python"; return }
    if (-not $S.Python) { Fail "Python was installed but is not found yet." "Python 裝了，但這個視窗還找不到它。" }
    Note "Using $($S.Python)" "使用 $($S.Python)"
}

# ── WebView2 ────────────────────────────────────────────────

function WebView2-Ok {
    $keys = @(
        "HKLM:\SOFTWARE\WOW6432Node\Microsoft\EdgeUpdate\Clients\{F3017226-FE2A-4295-8BDF-00C3A9A7E4C5}",
        "HKLM:\SOFTWARE\Microsoft\EdgeUpdate\Clients\{F3017226-FE2A-4295-8BDF-00C3A9A7E4C5}",
        "HKCU:\SOFTWARE\Microsoft\EdgeUpdate\Clients\{F3017226-FE2A-4295-8BDF-00C3A9A7E4C5}"
    )
    foreach ($k in $keys) {
        $pv = (Get-ItemProperty -Path $k -Name pv -ErrorAction SilentlyContinue).pv
        if ($pv -and $pv -ne "0.0.0.0") { return $true }
    }
    return $false
}

function Ensure-WebView2 {
    Step "WebView2 runtime (draws ShellBot's window)" "WebView2 執行環境（ShellBot 的視窗靠它顯示）"
    if (WebView2-Ok) { Note "Already installed." "已經有了。"; return }
    Installing "WebView2 Runtime" "the window is a web page drawn by Microsoft Edge's engine" "視窗是用 Microsoft Edge 引擎畫的網頁"
    Hint "Install the Evergreen WebView2 Runtime from https://developer.microsoft.com/microsoft-edge/webview2/ and paste the install line again." `
         "請從 https://developer.microsoft.com/microsoft-edge/webview2/ 安裝 Evergreen 版 WebView2 Runtime，再貼一次安裝指令。"
    Say-Uac
    [void](Winget-Install "Microsoft.EdgeWebView2Runtime")
    if ($DryRun) {
        if (-not $S.Winget) { Write-Host "    [dry-run] download MicrosoftEdgeWebview2Setup.exe from go.microsoft.com and run it /silent /install" }
        return
    }
    if (-not (WebView2-Ok)) {
        Note "Downloading the WebView2 installer from Microsoft..." "從 Microsoft 下載 WebView2 安裝程式…"
        $file = Join-Path $env:TEMP "MicrosoftEdgeWebview2Setup.exe"
        Download "https://go.microsoft.com/fwlink/p/?LinkId=2124703" $file
        $code = Start-Installer $file @("/silent", "/install")
        if ($code -ne 0 -and -not (WebView2-Ok)) { Fail "The WebView2 installer stopped (exit $code)." "WebView2 安裝程式中途停止（exit ${code}）。" }
    }
    if (-not (WebView2-Ok)) {
        Warn "WebView2 is not detected yet. If ShellBot's window stays blank, install it from the link above." `
             "還偵測不到 WebView2。如果 ShellBot 視窗一片空白，請從上面的連結安裝。"
    }
}

# ── ShellBot itself ─────────────────────────────────────────

function Git-Run([string[]]$ArgList, [string]$en, [string]$zh) {
    $code = Invoke-Tool "git" $ArgList
    if ($code -ne 0) { Fail "$en (git exit $code)" "${zh}（git exit ${code}）" }
}

function Fetch-Source {
    Step "ShellBot ($Ref)" "ShellBot 本體（${Ref}）"
    Hint "Check the network, then paste the install line again. To start over, move $InstallDir aside first." `
         "請確認網路後再貼一次安裝指令。若要重新開始，先把 $InstallDir 移到別處。"
    if (Test-Path "$InstallDir\.git") {
        Say "    Updating the existing install..." "    更新已經裝好的版本…"
        Push-Location $InstallDir; $S.Pushed = $true
        $old = $ErrorActionPreference; $ErrorActionPreference = "Continue"
        try {
            # Local edits are stashed so the update never blocks on them.
            $dirty = (& git status --porcelain 2>$null)
            if ($dirty) { & git stash push -u -m "install.ps1-auto-$(Get-Date -Format yyyyMMddHHmmss)" 2>$null | Out-Null }
        } finally { $ErrorActionPreference = $old }
        if (-not $env:SHELLFRAME_REF) {
            # As before: fast-forward the branch it is on; if history diverged, follow origin/main.
            if ((Invoke-Tool "git" @("pull", "--ff-only")) -ne 0) {
                Note "Fast-forward was not possible; syncing to origin/main" "無法快轉，改為同步到 origin/main"
                Git-Run @("fetch", "origin", "main") "Could not fetch origin/main" "無法取得 origin/main"
                Git-Run @("reset", "--hard", "origin/main") "Could not reset to origin/main" "無法同步到 origin/main"
            }
        } else {
            Git-Run @("fetch", "origin", $Ref) "Could not fetch $Ref" "無法取得 $Ref"
            Git-Run @("checkout", "-B", $Ref, "origin/$Ref") "Could not switch to $Ref" "無法切換到 $Ref"
        }
        Pop-Location; $S.Pushed = $false
        return
    }
    $S.Fresh = $true
    if ((Test-Path $InstallDir) -and (Get-ChildItem $InstallDir -Force -ErrorAction SilentlyContinue)) {
        # Files but no .git (zip download / copied tree): back up and clone clean, since
        # cloning into a non-empty folder fails.
        $backup = "$InstallDir.non-git-backup.$(Get-Date -Format yyyyMMddHHmmss)"
        Note "Backing up the existing folder to $backup" "把原本的資料夾備份到 $backup"
        if (-not $DryRun) { Move-Item -Path $InstallDir -Destination $backup -Force }
        $S.Fresh = $false
    }
    $parent = Split-Path $InstallDir -Parent
    if (-not $DryRun -and -not (Test-Path $parent)) { New-Item -ItemType Directory -Path $parent -Force | Out-Null }
    Say "    Downloading ShellBot..." "    下載 ShellBot…"
    Git-Run @("clone", "--branch", $Ref, $RepoUrl, $InstallDir) "Could not download ShellBot" "無法下載 ShellBot"
}

function Setup-Venv {
    Step "ShellBot's Python packages" "ShellBot 的 Python 套件"
    Hint "Check the network, then paste the install line again. If it keeps failing, delete $InstallDir\.venv and run it again." `
         "請確認網路後再貼一次安裝指令。若一直失敗，刪掉 $InstallDir\.venv 再跑一次。"
    $venvPy = "$InstallDir\.venv\Scripts\python.exe"
    if ((Test-Path "$InstallDir\.venv") -and -not $DryRun -and -not (Python-Ok $venvPy)) {
        # Its Python was removed or upgraded away: the venv cannot run at all.
        Note "The existing .venv no longer runs; rebuilding it" "原本的 .venv 已經無法執行，重建"
        Remove-Item -Recurse -Force "$InstallDir\.venv"
    }
    if (-not (Test-Path "$InstallDir\.venv")) {
        Say "    Creating the virtual environment with $($S.Python)..." "    用 $($S.Python) 建立虛擬環境…"
        $code = Invoke-Tool $S.Python @("-m", "venv", "$InstallDir\.venv")
        if ($code -ne 0) { Fail "Could not create the virtual environment (exit $code)." "無法建立虛擬環境（exit ${code}）。" }
    }
    Say "    Installing Python packages (pywebview, pyte, pywinpty)..." "    安裝 Python 套件（pywebview、pyte、pywinpty）…"
    [void](Invoke-Tool $venvPy @("-m", "pip", "install", "-q", "--upgrade", "pip"))
    $code = Invoke-Tool $venvPy @("-m", "pip", "install", "-q", "-r", "$InstallDir\requirements.txt")
    if ($code -ne 0) { Fail "pip could not install ShellBot's packages (exit $code)." "pip 無法安裝 ShellBot 的套件（exit ${code}）。" }

    Step "Checking that ShellBot can open its window" "確認 ShellBot 開得了視窗"
    Hint "Run: $venvPy -c `"import webview`" to see the error." "請執行 $venvPy -c `"import webview`" 看錯誤訊息。"
    $code = Invoke-Tool $venvPy @("-c", "import webview, pyte, winpty, clr")
    if ($code -ne 0) { Fail "ShellBot's packages do not import (exit $code)." "ShellBot 的套件無法載入（exit ${code}）。" }
    Note "OK" "沒問題"
}

function Write-Launchers {
    Step "Commands and the desktop shortcut" "指令與桌面捷徑"
    if ($DryRun) {
        Write-Host "    [dry-run] write $BinDir\shellframe.bat, shellframe.vbs, sfctl.bat; add $BinDir to the user PATH; create the ShellBot desktop shortcut"
        return
    }
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
    if (-not ($userPath -split ";" | Where-Object { $_ -eq $BinDir })) {
        $newPath = if ($userPath) { "$BinDir;$userPath" } else { $BinDir }
        [Environment]::SetEnvironmentVariable("Path", $newPath, "User")
        $env:Path = "$BinDir;$env:Path"
        Note "Added $BinDir to the user PATH" "已把 $BinDir 加進使用者 PATH"
    }

    # Desktop shortcut (standalone GUI, no terminal window)
    try {
        $desktopPath = [Environment]::GetFolderPath("Desktop")
        $shortcutPath = "$desktopPath\ShellBot.lnk"
        # An older install's shortcut had the app's old name (the app renames it at start too).
        $oldShortcut = "$desktopPath\ShellFrame.lnk"
        if (Test-Path $oldShortcut) { Remove-Item $oldShortcut -Force -ErrorAction SilentlyContinue }
        # Always recreate to pick up icon and VBS launcher changes
        $shell = New-Object -ComObject WScript.Shell
        $shortcut = $shell.CreateShortcut($shortcutPath)
        $shortcut.TargetPath = "$BinDir\shellframe.vbs"
        $shortcut.WorkingDirectory = $InstallDir
        $shortcut.Description = "ShellBot"
        $shortcut.IconLocation = "$InstallDir\icon.ico"
        $shortcut.Save()
        Note "Desktop shortcut: ShellBot" "桌面捷徑：ShellBot"
    } catch {
        Warn "Skipped the desktop shortcut: $_" "略過桌面捷徑：$_"
    }
}

# ── AI CLIs (optional) ──────────────────────────────────────

# The command comes from the provider registry through cli_install, the same as the
# app's AI accounts panel: each CLI's own PowerShell installer, never a bash pipeline.
function Registry-Command([string]$name) {
    $venvPy = "$InstallDir\.venv\Scripts\python.exe"
    if ($DryRun -or -not (Test-Path $venvPy)) { return "" }
    $code = "import sys; sys.path.insert(0, sys.argv[1]); import cli_install, usage_probe as u; " +
            "print(cli_install.for_platform(sys.argv[2], u.PROVIDER_SPECS[sys.argv[2]].get('install'), True).get('command') or '')"
    $old = $ErrorActionPreference; $ErrorActionPreference = "Continue"
    try { $out = & $venvPy -c $code $InstallDir $name 2>$null } finally { $ErrorActionPreference = $old }
    if ($LASTEXITCODE -ne 0 -or -not $out) { return "" }
    return ([string]$out).Trim()
}

function Cli-Present([string]$name) {
    switch ($name) {
        "claude" { return ((Have-Command claude) -or (Test-Path "$env:USERPROFILE\.local\bin\claude.exe")) }
        "codex" { return (Have-Command codex) }
    }
    return $false
}

function Install-Cli([string]$name) {
    if ($name -eq "claude") { $label = "Claude Code"; $fallback = "irm https://claude.ai/install.ps1 | iex" }
    elseif ($name -eq "codex") { $label = "Codex"; $fallback = "npm install -g @openai/codex" }
    else { Warn "Unknown AI CLI: $name" "不認得的 AI CLI：$name"; return }
    if (Cli-Present $name) { Note "$label is already installed." "$label 已經裝好了。"; return }
    $cmd = Registry-Command $name
    if (-not $cmd) { $cmd = $fallback }
    Installing $label "optional: an AI CLI to run in a ShellBot tab" "選用：在 ShellBot 分頁裡跑的 AI CLI"
    try {
        if ($name -eq "codex") {
            if (-not (Have-Command npm)) {
                Installing "Node.js LTS" "Codex is installed with npm" "Codex 要用 npm 安裝"
                Say-Uac
                [void](Winget-Install "OpenJS.NodeJS.LTS")
                Add-SessionPath @("$env:ProgramFiles\nodejs", "$env:APPDATA\npm")
                if (-not $DryRun -and -not (Have-Command npm)) {
                    Warn "Codex was not installed: Node.js is missing. Install Node.js LTS from https://nodejs.org, then run: $cmd" `
                         "Codex 沒有裝：缺 Node.js。請從 https://nodejs.org 安裝 LTS 版，再執行：$cmd"
                    return
                }
            }
            # npm.cmd, not npm: npm.ps1 is blocked by the default execution policy.
            $code = Invoke-Tool "npm.cmd" @("install", "-g", "@openai/codex")
        } else {
            # In a child PowerShell: an installer that calls `exit` must not end this one.
            $code = Invoke-Tool "powershell.exe" @("-NoProfile", "-ExecutionPolicy", "Bypass", "-Command", $cmd)
        }
        if ($code -ne 0) { throw "exit $code" }
    } catch {
        Warn "$label did not install ($_). Run it yourself later: $cmd" "$label 沒有裝成功（${_}）。之後可以自己執行：$cmd"
        return
    }
    Refresh-Path
    if ($name -eq "claude") {
        $S.Next += (T "  Claude Code: in ShellBot press + and open a Claude Code tab; the first time it opens your browser to log in." `
                      "  Claude Code：在 ShellBot 按「+」開一個 Claude Code 分頁，第一次會開瀏覽器請你登入。")
    } else {
        $S.Next += (T "  Codex: open a Codex tab and choose `"Sign in with ChatGPT`" the first time." `
                      "  Codex：開一個 Codex 分頁，第一次選「Sign in with ChatGPT」登入。")
    }
}

function Offer-AiClis {
    Step "AI CLIs (optional)" "AI CLI（選用）"
    Say "    ShellBot runs the AI CLIs you use (Claude Code, Codex, ...) in its tabs. None is needed to install ShellBot." `
        "    ShellBot 是在分頁裡跑你用的 AI CLI（Claude Code、Codex…），安裝 ShellBot 本身不需要它們。"
    $want = @()
    if ($env:SHELLFRAME_INSTALL_CLIS) {
        $want = @($env:SHELLFRAME_INSTALL_CLIS.Split(",") | ForEach-Object { $_.Trim() } | Where-Object { $_ -and $_ -ne "none" })
    } elseif ($CanAsk) {
        if (Cli-Present "claude") { Note "Claude Code is already installed." "Claude Code 已經裝好了。" }
        elseif (Ask "Also install Claude Code now? [y/N]" "要順便安裝 Claude Code 嗎？[y/N]") { $want += "claude" }
        if (Cli-Present "codex") { Note "Codex is already installed." "Codex 已經裝好了。" }
        elseif (Ask "Also install Codex (OpenAI) now? [y/N]" "要順便安裝 Codex（OpenAI）嗎？[y/N]") { $want += "codex" }
    } else {
        Note "No console to ask in, so none is installed. Add them later from ShellBot's AI accounts panel." `
             "沒有可以回答問題的視窗，先不裝。之後可以在 ShellBot 的「AI 帳號」面板安裝。"
    }
    foreach ($n in $want) { Install-Cli $n }
}

# What is installed and what is not, from the same registry the app uses.
function Report-AiClis {
    $venvPy = "$InstallDir\.venv\Scripts\python.exe"
    if ($DryRun -or -not (Test-Path $venvPy)) { return }
    Write-Host ""
    $cliReport = @'
import sys
sys.path.insert(0, sys.argv[1])
import cli_install, usage_probe as u
missing = []
for name, spec in u.PROVIDER_SPECS.items():
    ok = u.provider_installed(name)
    print(("  [x] " if ok else "  [ ] ") + spec["label"] + ("" if ok else " - not installed"))
    cmd = cli_install.for_platform(name, spec.get("install"), True).get("command")
    if not ok and cmd:
        missing.append("      " + cmd)
if missing:
    print("")
    print("  To install the missing ones in PowerShell (one is enough to start):")
    print("\n".join(missing))
    print("      (or use the AI accounts panel in ShellBot)")
'@
    try {
        $reportFile = Join-Path $env:TEMP "shellbot-cli-report.py"
        Set-Content -Path $reportFile -Value $cliReport -Encoding UTF8
        [void](Invoke-Tool $venvPy @($reportFile, $InstallDir))
        Remove-Item $reportFile -ErrorAction SilentlyContinue
    } catch {
        Note "(could not list them; see the AI accounts panel)" "（無法列出；請看 AI 帳號面板）"
    }
}

# ── Main ────────────────────────────────────────────────────

function Install-ShellBot {
    Say "Installing ShellBot..." "安裝 ShellBot…" "Cyan"
    if ($DryRun) { Say "(dry run: checks only; nothing will be installed or changed)" "（試跑：只檢查，不會安裝或修改任何東西）" }
    # Older Windows PowerShell defaults to TLS 1.0/1.1, which GitHub and python.org refuse.
    try { [Net.ServicePointManager]::SecurityProtocol = [Net.ServicePointManager]::SecurityProtocol -bor 3072 } catch { }

    Step "Package manager (winget)" "套件管理工具（winget）"
    $S.Winget = Find-Winget
    if ($S.Winget) { Note "winget is available." "有 winget。" }
    else { Note "No winget here (Windows Sandbox, LTSC or Server): the official installers are downloaded directly." `
                "這台沒有 winget（Windows Sandbox、LTSC 或 Server）：改為直接下載官方安裝程式。" }

    Ensure-Git
    Ensure-Python
    Ensure-WebView2

    if ($DryRun) {
        Step "ShellBot itself" "ShellBot 本體"
        if (Test-Path "$InstallDir\.git") { Write-Host "    [dry-run] update $InstallDir to $Ref" }
        else { Write-Host "    [dry-run] git clone --branch $Ref $RepoUrl $InstallDir" }
        Write-Host "    [dry-run] create $InstallDir\.venv and pip install -r requirements.txt"
        Write-Launchers
    } else {
        Fetch-Source
        Setup-Venv
        Write-Launchers
    }

    Offer-AiClis
    Report-AiClis

    $version = "?"
    try { $version = (Get-Content "$InstallDir\version.json" -Raw | ConvertFrom-Json).version } catch { }
    $took = (Get-Date) - $Started
    Write-Host ""
    if ($DryRun) {
        Say "Dry run finished: nothing was installed or changed." "試跑結束：什麼都沒有安裝或修改。" "Green"
        if ($S.Installed.Count) { Say "Would install: $($S.Installed -join ', ')" "會安裝：$($S.Installed -join ', ')" }
        return
    }
    Say "ShellBot v$version installed! (took $([int][Math]::Floor($took.TotalMinutes))m $($took.Seconds)s)" `
        "ShellBot v$version 裝好了！（花了 $([int][Math]::Floor($took.TotalMinutes)) 分 $($took.Seconds) 秒）" "Green"
    if ($S.Installed.Count) { Say "  Installed this time: $($S.Installed -join ', ')" "  這次安裝了：$($S.Installed -join ', ')" }
    Say "  Open it: the ShellBot shortcut on the desktop, or type shellframe in a new PowerShell window." `
        "  打開方式：桌面上的 ShellBot 捷徑，或在新的 PowerShell 視窗輸入 shellframe。"
    if ($S.Next.Count) { Say "  Next:" "  下一步："; foreach ($n in $S.Next) { Write-Host $n } }
    Write-Host ""
    Say "  Run ``sfctl permissions`` once to pre-add Windows Defender Firewall rules for the bundled Python; it avoids the one-time 'Allow network access' popup." `
        "  建議跑一次 ``sfctl permissions``，先加好 Windows 防火牆規則，第一次啟動就不會跳「允許網路存取」視窗。" "Yellow"
    Write-Host ""

    if ($S.Fresh -and $env:SHELLFRAME_NO_LAUNCH -ne "1") {
        Say "Opening ShellBot..." "正在打開 ShellBot…"
        try { Start-Process -FilePath "wscript.exe" -ArgumentList "`"$BinDir\shellframe.vbs`"" } catch { }
    }
}

try {
    Install-ShellBot
} catch {
    Write-Host ""
    Write-Host ("X " + (T "ShellBot was not installed: it stopped at `"$($S.Step)`"." "ShellBot 沒有裝完：停在「$($S.Step)」。")) -ForegroundColor Red
    Write-Host ("  " + $_.Exception.Message) -ForegroundColor Red
    if ($S.LastCmd) { Write-Host ("  " + (T "Last command: " "最後執行的指令：") + $S.LastCmd) -ForegroundColor DarkGray }
    if ($S.Hint) { Write-Host ("  " + $S.Hint) -ForegroundColor Yellow }
    Write-Host ("  " + (T "Once that is fixed, paste the same install line again: what is already done is skipped." `
                         "補好之後再貼一次同一行安裝指令即可：已經裝好的會跳過。")) -ForegroundColor Yellow
} finally {
    if ($S.Pushed) { Pop-Location }
}
}
