# ShellFrame

Multi-tab GUI terminal wrapper for AI coding assistants. Wraps any CLI tool (Claude Code, Codex, Aider, etc.) with image paste, Telegram bridge, session persistence, and more.

## Why

Terminal-based AI assistants can't receive screenshots from your clipboard. ShellFrame wraps them in a native GUI window that intercepts `Cmd+V`, saves the image, and injects the file path — screenshot-to-AI in one paste. Over time it grew into a full multi-agent control panel.

## Features

| Feature | Description |
|---|---|
| **Image/file paste** | `Cmd+V` screenshots, Finder files, or drag & drop. Preview bar + auto-attach on Enter. |
| **Multi-tab** | Multiple CLI sessions side by side. Named tabs, drag reorder. |
| **tmux persistence** | Sessions run inside tmux. Close ShellFrame → reopen → all tabs and scrollback survive. |
| **Telegram bridge** | One TG bot routes across all sessions. `/list`, `/1 /2` to switch, voice messages (STT), file/photo forwarding. |
| **AI busy indicator** | Pulsing orange dot when a session is actively working. Pink dot when waiting on a permission dialog. |
| **Session rename** | Double-click tab or sidebar to name sessions. Syncs to TG `/list`. |
| **Ctrl+Click file paths** | Click local file paths in terminal output to open in default app. |
| **Right-click copy/paste** | Windows CMD-style: select → right-click = copy; no selection → right-click = paste. |
| **Presets** | Saved commands with emoji icons. Drag to reorder. |
| **Settings tabs** | General (font, UI scale S/M/L, language) and Telegram Bridge (connection, STT providers). |
| **Two-tier reload** | UI changes → hot reload. Python changes → full restart with session preservation notice. |
| **Cross-platform** | macOS (WKWebView), Windows (Edge WebView2), Linux (GTK WebView). |
| **Auto-update** | Startup check + one-click update. TG `/update` and `/update_now` for remote management. |
| **i18n** | English + 繁體中文. Auto-detects OS language. |

## Install

### macOS / Linux

```bash
curl -fsSL https://raw.githubusercontent.com/h2ocloud/shellframe/main/install.sh | bash
```

### Windows

```powershell
irm https://raw.githubusercontent.com/h2ocloud/shellframe/main/install.ps1 | iex
```

### Requirements

- **Python 3.9+**
- **tmux** — for session persistence (macOS: `brew install tmux`; Linux: `apt install tmux`; Windows: not needed, uses soft persistence)
- **pywebview** + **pyte** — auto-installed via `requirements.txt`
- **pywinpty** (Windows only) — auto-installed, provides ConPTY for TUI apps

## Usage

### Launch

```bash
shellframe            # GUI with session picker
# Or on Mac — Spotlight/Launchpad search "ShellFrame"
```

### Keyboard shortcuts

| Shortcut | Action |
|---|---|
| `Cmd+T` | New tab |
| `Cmd+W` | Close tab |
| `Ctrl+Tab` | Next tab |
| `Cmd+]` / `Cmd+[` | Next / prev tab |
| `Cmd+V` | Paste image/file |
| `Cmd+,` | Settings |
| `Ctrl+Click` / `Cmd+Click` | Open file path in default app |
| Right-click (with selection) | Copy |
| Right-click (no selection) | Paste |
| `Esc` (plain terminal) | Clear input line |
| Double-click tab | Rename session |

### Telegram Bridge

1. Open Settings → Telegram Bridge
2. Paste your bot token, set allowed user IDs
3. Click Connect

TG commands:
| Command | Action |
|---|---|
| `/list` | List all sessions with last response preview |
| `/1` `/2` `/3`... | Switch to session N |
| `/new` | Create new session (shows preset picker) |
| `/close` | Close current session |
| `/pause` / `/resume` | Pause/resume bridge |
| `/reload` | Hot-reload bridge code |
| `/restart` | Full app restart (sessions preserved) |
| `/update` | Check for updates |
| `/update_now` | Pull + restart if needed |
| `/status` | Show bridge status |

Voice messages are transcribed via configurable STT providers (Settings → TG Bridge → 🎙 STT).

### STT (Voice Transcription)

Supports a pluggable provider chain:
- **Local**: whisper.cpp via `whisper-cli` (install from Settings)
- **Remote**: any whisper-compatible HTTP server (configure in Settings)
- **Plugin**: custom Python at `~/.config/shellframe/stt_plugin.py`

Backend modes: `auto` (plugin → local → remote) / `local` / `remote` / `plugin` / `off`.

## Architecture

```
shellframe/
├── main.py                 # Python: multi-session PTY + pywebview + tmux + bridge API
├── bridge_telegram.py      # TG bot: multi-session routing, STT, menu prompts
├── bridge_base.py          # Base class for bridges
├── web/index.html          # Frontend: xterm.js + tabs + sidebar + modals + i18n
├── sfctl.py                # CLI remote control (file-based IPC)
├── filters.json            # Dynamic output filter rules for TG bridge
├── INIT_PROMPT.md          # Auto-injected context for AI CLI sessions
├── ShellFrame.app/         # macOS .app bundle
├── install.sh              # macOS/Linux installer
├── install.ps1             # Windows installer
├── requirements.txt        # Python: pywebview, pyte, pywinpty (Windows)
├── version.json            # Version tracking for auto-update
├── CHANGELOG.md            # Release history (bilingual)
├── WINDOWS.md              # Windows-specific docs
└── .github/
    └── REVIEW_WORKFLOW.md  # PR/issue triage playbook
```

### Tech stack

| Component | Technology |
|---|---|
| GUI window | pywebview (native OS WebView — not Electron) |
| Terminal | xterm.js 5.5 + fit/web-links/unicode11 addons |
| PTY | `pty.fork()` + tmux (Unix) / pywinpty ConPTY (Windows) |
| TG bridge | urllib (stdlib, zero external deps) + pyte virtual terminal |
| STT | whisper.cpp (local) / HTTP providers (remote) / plugin file |
| Config | JSON (`~/.config/shellframe/config.json`) |
| IPC | File-based (`sfctl`) for in-session remote control |

### Session persistence

**macOS/Linux**: Every PTY runs inside a tmux session (`sf_s1`, `sf_s2`...). Close ShellFrame → tmux sessions survive. Next launch → automatic reattach with full scrollback.

**Windows**: No tmux. ShellFrame uses "soft persistence" — saves the session list to config, recreates fresh PTYs on next launch. Same tabs and labels, but scrollback is lost. See [WINDOWS.md](WINDOWS.md).

## File locations

| Purpose | macOS/Linux | Windows |
|---|---|---|
| Source + venv | `~/.local/apps/shellframe` | `%USERPROFILE%\.local\apps\shellframe` |
| Config | `~/.config/shellframe/config.json` | `%USERPROFILE%\.config\shellframe\config.json` |
| STT plugin | `~/.config/shellframe/stt_plugin.py` | same |
| Whisper model | `~/.local/share/shellframe/whisper-models/` | same |
| Temp (logs, IPC) | `/tmp/shellframe_*.log` | `%TEMP%\shellframe_*.log` |
| TG offset | `~/.config/shellframe/tg_offset.json` | same |

## Update

ShellFrame checks for updates on startup. Manual:

```bash
cd ~/.local/apps/shellframe && git pull
```

Or from Telegram: `/update_now`

## Contributing

See [.github/REVIEW_WORKFLOW.md](.github/REVIEW_WORKFLOW.md) for the PR/issue review process.

## Author

**Howard Wu** ([@h2ocloud](https://github.com/h2ocloud))

## License

[MIT](LICENSE)
