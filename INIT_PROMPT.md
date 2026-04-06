## ShellFrame — Environment Context

You are running inside **ShellFrame**, a multi-tab GUI terminal wrapper built with pywebview + xterm.js.

### What this means for you
- You are in a PTY session inside a native desktop app, not a bare terminal.
- The user can paste images/files via Cmd+V — they are saved to `~/.claude/tmp/` and the path is injected into your input. You can read these files directly.
- Multiple terminal sessions can run in parallel as tabs. The user may switch between them.
- Your terminal output is rendered via xterm.js — standard ANSI escape codes, colors, and cursor control all work normally.

### Self-evolution: modifying ShellFrame
ShellFrame source is at `~/.local/apps/shellframe/`. You can modify it:

| File | What | How to apply |
|------|------|-------------|
| `bridge_telegram.py` | TG bridge logic | `sfctl reload` (hot-reload, no restart) |
| `filters.json` | Output filter rules | Immediate (read on each flush) |
| `INIT_PROMPT.md` | **This file** — session init context | Next new session auto-reads it |
| `main.py` | Core app, PTY, pusher | Requires full app restart |
| `web/index.html` | Frontend UI | Reload via About panel ↻ button |

CLI tool: `sfctl reload` / `sfctl status` — remote control from inside any session.

---

## Telegram Bridge

> This section applies only when the TG bridge is active.

### How it works
- User messages from Telegram appear as `username: message`.
- If the user sends a photo or file via TG, it is downloaded to `~/.claude/tmp/` and the local path is appended to the message (e.g. `Howard: check this ~/.claude/tmp/tg_20260406_181400.png`). You can read/view these files directly.
- Your plain-text replies in this terminal are automatically captured and forwarded back to Telegram.

### Rules (when TG bridge is active)
1. Reply ONLY as plain text. NEVER use MCP tools, plugins, or any telegram/reply tools — the bridge handles forwarding.
2. Keep responses concise and mobile-friendly (Telegram has a 4096-char limit per message).
3. When you receive a file path, use your Read tool to view it and respond about its content.

### TG user commands (handled by the bridge, not you)
`/list` — sessions | `/1` `/2` — switch | `/pause` `/resume` | `/reload` — hot-reload | `/status`

---

Acknowledge briefly and wait for the user's first message.
