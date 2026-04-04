# ShellFrame

Lightweight multi-tab GUI terminal wrapper with clipboard image paste support. Works with any CLI tool — Claude Code, Codex, and more.

## Why

Terminal-based AI coding assistants (Claude Code, Codex, etc.) don't support pasting images from the clipboard. ShellFrame wraps them in a native GUI window that intercepts `Cmd+V` / `Ctrl+V`, saves the image, and auto-injects the file path into your session — giving you screenshot-to-AI in one paste.

## Features

- **Image paste** — `Cmd+V` a screenshot, it shows inline preview and auto-attaches on Enter
- **Multi-tab** — Run multiple CLI sessions side by side
- **Presets** — Save frequently used commands (e.g., `claude`, `codex`, `claude --channels ...`)
- **Cross-platform** — Mac (WKWebView), Windows (Edge WebView2), Linux (GTK WebView)
- **Lightweight** — Native OS WebView, not Electron. ~200 lines Python + ~400 lines HTML/JS
- **Auto-update** — Checks for new versions on startup, one-click update

## Install

### One-line install (Mac / Linux)

```bash
curl -fsSL https://raw.githubusercontent.com/h2ocloud/shellframe/main/install.sh | bash
```

### Manual install

```bash
git clone https://github.com/h2ocloud/shellframe.git ~/.local/apps/shellframe
cd ~/.local/apps/shellframe
python3 -m venv .venv
.venv/bin/pip install pywebview
```

### Requirements

- Python 3.9+
- `pywebview` (auto-installed)
- macOS 12+ / Windows 10+ / Linux with GTK3

## Usage

### Launch

```bash
# GUI with session picker
shellframe

# Or on Mac — Spotlight search "ShellFrame"
```

### Workflow: Paste screenshot to AI

1. Take a screenshot (`Cmd+Shift+4` on Mac, `Win+Shift+S` on Windows)
2. In ShellFrame, press `Cmd+V` — image preview appears above the terminal
3. Type your question and press `Enter` — the image file path is auto-appended
4. The AI tool reads the image and responds

### Keyboard shortcuts

| Shortcut | Action |
|---|---|
| `Cmd+T` / `Ctrl+T` | New tab |
| `Cmd+W` / `Ctrl+W` | Close tab |
| `Ctrl+Tab` | Next tab |
| `Ctrl+Shift+Tab` | Previous tab |
| `Cmd+V` | Paste image from clipboard |
| `Cmd+,` / `Ctrl+,` | Settings |
| `Escape` | Close modal |

### Settings

Click the gear icon (⚙) in the tab bar or press `Cmd+,` to manage presets. Each preset has:

- **Icon** — emoji or character
- **Name** — display label
- **Command** — the CLI command to run (supports arguments)

Presets are saved to `~/.config/shellframe/config.json`.

### Example presets

```json
{
  "presets": [
    {"name": "Claude Code", "cmd": "claude", "icon": "✨"},
    {"name": "Claude + Telegram", "cmd": "claude --channels plugin:telegram@claude-plugins-official", "icon": "📱"},
    {"name": "Codex", "cmd": "codex", "icon": "⚡"},
    {"name": "Bash", "cmd": "bash", "icon": "▶"}
  ]
}
```

## Architecture

```
shellframe/
├── main.py              # Python backend: multi-session PTY + pywebview bridge
├── web/index.html       # Frontend: xterm.js terminal + tabs + image paste + modals
├── ShellFrame.app/      # macOS .app bundle (Spotlight/Launchpad)
├── install.sh           # One-line installer
├── requirements.txt     # Python dependencies
├── run.sh / run.bat     # Platform launchers
└── icon.png             # App icon
```

### Tech stack

| Component | Technology |
|---|---|
| GUI window | pywebview (native OS WebView) |
| Terminal rendering | xterm.js |
| PTY management | Python `pty` (Unix) / `subprocess` (Windows) |
| Image handling | Web Clipboard API → base64 → PNG file |
| Config | JSON (`~/.config/shellframe/config.json`) |

### How image paste works

1. Web Clipboard API intercepts `paste` event, detects `image/*` MIME type
2. Image is read as base64 data URL via `FileReader`
3. Python backend decodes and saves to `~/.claude/tmp/clipboard_YYYYMMDD_HHMMSS.png`
4. Path is shown in preview bar; on Enter, path is appended to terminal input
5. Old images (>1 hour) are auto-purged

## Update

ShellFrame checks for updates on startup. You can also update manually:

```bash
cd ~/.local/apps/shellframe && git pull
```

## Author

**Howard Wu** ([@h2ocloud](https://github.com/h2ocloud))

## License

[MIT](LICENSE)
