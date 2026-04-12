# ShellFrame on Windows

ShellFrame runs on Windows as a second-class citizen — most features work, but
a couple of macOS-only conveniences have to settle for less. This page is the
"what to expect" guide.

## Install

```powershell
irm https://raw.githubusercontent.com/h2ocloud/shellframe/main/install.ps1 | iex
```

The installer:
1. Clones the repo to `%USERPROFILE%\.local\apps\shellframe`
2. Creates a Python venv and installs `pywebview`, `pyte`, `pywinpty`
3. Drops `shellframe.bat` and `sfctl.bat` into `%USERPROFILE%\.local\bin` and
   adds it to your user PATH
4. Creates a desktop shortcut

After install, run `shellframe` from any terminal or double-click the desktop
shortcut.

## Requirements

- **Python 3.9+** (3.10 or newer recommended) on PATH
- **Git** on PATH (for self-update via `/update_now`)
- **`pywinpty`** is auto-installed via `requirements.txt`. It's the binding to
  Windows ConPTY (the modern Windows pseudo-terminal). Without it ShellFrame
  falls back to a plain `subprocess.Popen` and TUI tools (Claude Code, Codex,
  vim) won't render correctly.

Optional but recommended:
- **WebView2 runtime** — pywebview uses Edge WebView2. Modern Windows 10/11
  ships it; if your VM doesn't have it, install from
  https://developer.microsoft.com/en-us/microsoft-edge/webview2/

## What works on Windows

| Feature | Status | Notes |
|---|---|---|
| Multi-tab PTY sessions | ✅ | Via pywinpty (ConPTY) |
| Web UI (sidebar, settings, modals) | ✅ | Same WKWebView/WebView2 surface |
| Telegram bridge (text, photos, voice) | ✅ | Pure stdlib, identical to macOS |
| STT remote chain | ✅ | Same provider config |
| STT plugin file | ✅ | `%USERPROFILE%\.config\shellframe\stt_plugin.py` |
| Local STT (whisper.cpp) | ✅ | Install button uses `winget install ggerganov.whisper.cpp` first, falls back to chocolatey |
| Right-click copy/paste | ✅ | `clip.exe` (write) + `Get-Clipboard -Raw` (read) |
| Ctrl+Click file paths | ✅ | Opens via `os.startfile()` |
| Settings preset auto-save | ✅ | Same as macOS |
| TG `/restart` | ✅ | Spawns via `cmd /c start shellframe.bat` (detached) |
| TG `/update` `/update_now` | ✅ | Git pull + auto-restart |
| Soft session persistence | ⚠️ | See limitation below |

## Known limitations

### No tmux → soft persistence only

On macOS/Linux, ShellFrame backs every PTY with a tmux session. Closing the
window leaves the tmux session running; on next launch ShellFrame reattaches
and you see the full scrollback as if you never left.

Windows has no tmux equivalent. ShellFrame falls back to **soft persistence**:

- The list of open sessions (sid + command + custom label) is written to
  `config.json` whenever you open or close a session.
- On startup, ShellFrame **recreates fresh PTYs** for each persisted entry —
  same command, same label, same UI position.
- **You lose scrollback.** The previous PTY's history is gone. AI sessions
  start a brand new conversation.

This is enough for "open ShellFrame, find my Claude tab where I left it,
keep working" but it's not equivalent to the macOS reattach experience.

### No tmux copy-mode scrollback enhancements

The macOS build uses tmux `copy-mode` to give you smooth keyboard scrollback
with Vi/Emacs bindings. On Windows, scrollback uses xterm.js's built-in
buffer only — still works, just less powerful.

### `/list` previews use the pyte parser

On macOS, TG `/list` previews snapshot each session via `tmux capture-pane`
which gives a clean, fully-rendered view including off-screen scrollback.
On Windows it falls back to the pyte virtual-terminal parser. The output is
slightly less polished but functional.

## File locations

| Purpose | Path |
|---|---|
| Source / venv | `%USERPROFILE%\.local\apps\shellframe` |
| Config | `%USERPROFILE%\.config\shellframe\config.json` |
| STT plugin (optional) | `%USERPROFILE%\.config\shellframe\stt_plugin.py` |
| Local whisper model | `%USERPROFILE%\.local\share\shellframe\whisper-models\` |
| Bridge log | `%TEMP%\shellframe_bridge.log` |
| Debug log | `%TEMP%\shellframe_debug.log` |
| sfctl IPC | `%TEMP%\shellframe_cmd.json` / `_result.json` |
| TG offset persistence | `%USERPROFILE%\.config\shellframe\tg_offset.json` |

## Troubleshooting

**TUI tools render as gibberish / no colors**
You're on the no-pywinpty fallback. Run
`%USERPROFILE%\.local\apps\shellframe\.venv\Scripts\pip install pywinpty`
and restart.

**`/restart` from Telegram does nothing**
Check that `shellframe.bat` exists in `%USERPROFILE%\.local\bin`. If you
moved the install dir, re-run `install.ps1` to recreate the launcher.

**Sessions don't survive restart at all**
Check `config.json` — there should be a `session_list` key with your sessions.
If it's empty after closing, the soft-persistence write failed (check
write permissions on `%USERPROFILE%\.config\shellframe\`).

**Local STT install fails**
The button tries `winget install ggerganov.whisper.cpp` then `choco install
whisper-cpp`. If neither package manager is on PATH, install whisper.cpp
manually from https://github.com/ggml-org/whisper.cpp/releases and ensure
`whisper-cli.exe` is on PATH.
