# ShellBot

A home for your AI bots. Every CLI agent you already pay for (Claude Code, Codex, Grok Build, OpenCode, …) runs in its own tab; turn a tab into a **bot** and it gets a face, a job, its own memory that survives the tab, and colleagues it can hand work to — with a primary bot on top that takes stock and dispatches.

> ShellBot is the new name of **ShellFrame**. Only the name you see changed: the command (`sfctl`), the config folder (`~/.config/shellframe`), the repo and the install URL keep the old name, so existing setups and paired machines keep working.

## Why

Terminal-based AI assistants can't receive screenshots from your clipboard. ShellBot (then ShellFrame) started as a native GUI window that intercepts `Cmd+V`, saves the image, and injects the file path — screenshot-to-AI in one paste. Over time it grew into a full multi-agent control panel, and now into a team of bots. See [docs/bots.md](docs/bots.md).

## Features

| Feature | Description |
|---|---|
| **Bots** (experimental) | Every tab's light is a bot face (shape + colour = identity, expression = state). A tab can become a bot with a job, skills and memory written before it closes; a primary bot on top takes stock and dispatches; drag bots on and off the primary card. [docs/bots.md](docs/bots.md) |
| **Image/file paste** | `Cmd+V` screenshots, Finder files, or drag & drop. Preview bar + auto-attach on Enter. |
| **Multi-tab** | Multiple CLI sessions side by side. Named tabs, drag reorder. |
| **tmux persistence** | Sessions run inside tmux. Close ShellFrame → reopen → all tabs and scrollback survive. |
| **Telegram bridge** | One TG bot routes across all sessions. `/list`, `/1 /2` to switch, voice messages (STT), file/photo forwarding. |
| **AI busy indicator** | Pulsing orange dot when a session is actively working. Pink dot when waiting on a permission dialog. |
| **Session rename** | Double-click tab or sidebar to name sessions. Syncs to TG `/list`. |
| **Ctrl+Click file paths** | Click local file paths in terminal output to open in default app. |
| **Right-click copy/paste** | Select → right-click = copy. Nothing selected → right-click opens a small menu, and Paste pastes only when you pick it (a right-click never pastes by itself). |
| **Presets** | Saved commands with emoji icons. Drag to reorder. |
| **Settings tabs** | General (font, UI scale S/M/L, language) and Telegram Bridge (connection, STT providers). |
| **Two-tier reload** | UI changes → hot reload. Python changes → full restart with session preservation notice. |
| **Cross-platform** | macOS (WKWebView), Windows (Edge WebView2), Linux (GTK WebView). |
| **Auto-update** | Startup check + one-click update. TG `/update` and `/update_now` for remote management. |
| **i18n** | English + 繁體中文. Auto-detects OS language. |

## Install

**One URL, any device** — open **https://h2ocloud.github.io/shellframe/**. It detects your OS and shows the right command with a copy button. Re-running the same command later updates ShellFrame in place.

Prefer to copy directly:

### macOS / Linux

```bash
curl -fsSL https://raw.githubusercontent.com/h2ocloud/shellframe/main/install.sh | bash
```

Some Linux desktops (Ubuntu's among them) come without `curl`; there, use `wget -qO- https://raw.githubusercontent.com/h2ocloud/shellframe/main/install.sh | bash`.

Paste it into a terminal on a brand-new computer and it gets to ShellBot open: whatever is missing it installs, whatever is there it leaves alone. Each step says what it installs and why.

| Missing piece | macOS | Linux (apt / dnf / pacman) |
|---|---|---|
| Package manager | Homebrew | the system's own |
| git, a C compiler | Apple's Command Line Tools (Homebrew brings them) | `git` |
| Python 3.10+ with venv | Python 3.12 from Homebrew (not Apple's: its privacy permissions would not stick) | `python3`, `python3-venv` |
| tmux | `tmux` | `tmux` |
| The window | built in (WebKit) | GTK 3, WebKit2GTK, PyGObject (`python3-gi`, `gir1.2-webkit2-4.1`, …) |

Then ShellBot itself in `~/.local/apps/shellframe`, its Python packages, the `shellframe` and `sfctl` commands, and ShellBot.app (macOS) or an app-menu entry (Linux). A first install opens ShellBot when it is done.

**What may need you** (each is announced before it happens):

- **macOS without Homebrew:** press **Enter** when Homebrew asks, then type your **login password** (nothing shows while you type). Your account must be an administrator.
- **macOS, if the Command Line Tools still are not there:** a macOS window asks to install them: click **Install**, then **Agree**. The installer waits and continues by itself.
- **Linux:** `sudo` asks for your password once, for the system packages.
- **At the end, optional:** "Also install Claude Code? / Codex?" (`y` or Enter to skip). If you say yes, the next step is logging in the first time you open that tab.

**How long:** a Linux desktop about 2–5 minutes (most of it the GTK/WebKit packages); a Mac that already has Homebrew 1–3 minutes; a brand-new Mac about 10–20 minutes, most of it Apple's Command Line Tools download. Running the line again updates ShellBot and skips what is already there. `curl … | SHELLFRAME_DRY_RUN=1 bash` only prints what it would install.

### Windows

```powershell
irm https://raw.githubusercontent.com/h2ocloud/shellframe/main/install.ps1 | iex
```

Run in **PowerShell**. When missing, it installs Git, Python 3.12 (for your account only) and the WebView2 runtime, with `winget`; where winget is not available (Windows Sandbox, LTSC, Server) it downloads the same official installers directly. Then ShellBot, its Python packages, the `shellframe` / `sfctl` commands and a **ShellBot** desktop shortcut, and it opens ShellBot.

**What may need you:** Windows may ask *"Do you want to allow this app to make changes to your device?"* while Git or WebView2 installs: click **Yes**. At the end, optional: Claude Code / Codex. Usually 3–8 minutes on a new PC.

### Requirements

The installers above take care of these:

- **Python 3.10+** (3.12 is what the installers install and test against)
- **tmux** — for session persistence (macOS / Linux; Windows uses soft persistence)
- **pywebview** + **pyte** — auto-installed via `requirements.txt`; on Linux pywebview draws with the distribution's GTK + WebKit2GTK
- **pywinpty** (Windows only) — auto-installed, provides ConPTY for TUI apps
- AI CLIs (Claude Code, Codex, …) are optional and installed separately; the installer offers Claude Code and Codex, and ShellBot's AI accounts panel offers the rest

## Usage

### Launch

```bash
shellframe            # GUI with session picker
# Or on Mac — Spotlight/Launchpad search "ShellFrame"
```

### Keyboard shortcuts

| Action | macOS | Windows |
|---|---|---|
| New tab | `Cmd+T` | `Ctrl+T` / `Ctrl+Shift+T` |
| Close tab (asks to confirm) | `Cmd+W` | `Ctrl+W` / `Ctrl+Shift+W` |
| Next / prev tab | `Ctrl+Tab` / `Ctrl+Shift+Tab`, `Cmd+]` / `Cmd+[` | `Ctrl+Tab` / `Ctrl+Shift+Tab`, `Ctrl+PgDn` / `Ctrl+PgUp` |
| Paste image/file | `Cmd+V` | `Ctrl+V` |
| Settings | `Cmd+,` | `Ctrl+,` |
| Copy selection | `Cmd+C` | `Ctrl+C` (only with a selection — otherwise it interrupts, as in any terminal) |
| Open file path in default app | `Cmd+Click` / `Ctrl+Click` | `Ctrl+Click` |
| Copy / paste | Right-click: copies a selection; with none, a menu whose Paste you pick | Same |
| Clear input line (plain terminal) | `Esc` | `Esc` |
| Rename session | Double-click tab | Double-click tab |

Bare `Ctrl` chords that terminals rely on are not hijacked: on macOS app shortcuts live exclusively on `Cmd` (`Ctrl+W` stays delete-word, `Ctrl+T` transpose), and `Ctrl+C` is never an app shortcut — on Windows it copies only when text is selected. Closing a tab from the keyboard always shows a confirmation, so a mistyped chord can't kill a running session.

### Idle Tab Cleanup

Open Settings → General → Idle tab cleanup to control ShellFrame's automatic
AI tab cleanup.

- **Idle after** controls how long an AI tab can sit without user input before
  ShellFrame asks it for a short summary.
- **Summary wait** controls how long ShellFrame waits after asking for that
  summary before closing the tab.
- **Write handoff to master** writes a short `[ShellFrame 交接]` note to the
  master tab when an orchestrated or idle-cleaned tab is closed.

The same values live in `~/.config/shellframe/config.json`:

```json
"idle_reaper": {
  "enabled": true,
  "idle_sec": 1800,
  "summary_grace_sec": 120,
  "handoff_to_main": true,
  "handoff_on_start": false
}
```

For example, change `idle_sec` from `1800` to `10800` for 3 hours.

### Master / Worker Delegation

ShellFrame can keep one user-facing `總控-*` tab and delegate real work to
configured worker roles without hard-routing incoming messages.

```bash
sfctl roster
sfctl delegate 時程信件 "送假單今明兩天居家"
sfctl delegate Coding "修 ShellFrame 設定 UI"
sfctl delegate 研究 "整理 Plaud 與 RFP 待辦"
sfctl peek s6 --lines 80
```

Roles live in `~/.config/shellframe/config.json` under `agent_roster`. The
delegate command creates or reuses the role's labeled tab, sends a wrapper
prompt with that role's responsibility, and returns the sid for polling.

Finished worker tabs are kept by default so the master can follow up in the
same context. Do not close a worker just because its task is done; let idle tab
cleanup summarize and remove unused workers later, unless the user explicitly
asks to close it or the tab is broken/noisy.

Workers should return user-ready drafts, reports, lookup results, or operation
conclusions immediately in a ready-to-forward form. The master can pass those
results back to the user while other delegated work continues.

Workers should search from known project paths. Avoid broad scans of `/Users`,
`~/Library`, `~/Library/Mobile Documents`, Mail, Messages, Photos, or other
macOS protected data folders because they can trigger privacy prompts for the
ShellFrame Python process.

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
├── ShellFrame.app/         # macOS .app bundle template (installed as ShellBot.app)
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

### Versioning

Format: `MAJOR.MINOR.PATCH` (e.g. `0.13.0`)

| Bump | When | Example |
|---|---|---|
| **MINOR** (`0.X.0`) | New feature, new Settings UI section, new bridge capability, architectural change | 0.12 → 0.13 |
| **PATCH** (`0.0.X`) | Bug fix, copy/style tweak, existing feature refinement | 0.13.0 → 0.13.1 |

- Always bump `version.json` **and** add a CHANGELOG entry (bilingual EN + ZH-TW).
- Multiple patches within one session can share a single version bump.
- MAJOR stays at `0` until a stable public release.

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
