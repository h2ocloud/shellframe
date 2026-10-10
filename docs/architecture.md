# ShellFrame architecture

> 中文摘要：ShellFrame 是「pywebview 前端 + Python 後端 + 每個分頁一個 PTY/tmux」的桌面終端，
> 專門跑 AI CLI（Claude Code、Codex、opencode、pi、Grok Build）。後端唯一對前端的介面是 `Api`
> 物件；它由 `main.py` 的核心加上 `api_*.py` 的領域 mixin 組成。新增功能時，先在
> 下面的模組地圖找到所屬領域，放進對應的 mixin 或模組；`tests_architecture.py`
> 會擋住 `main.py` 再次膨脹、mixin 反向 import main、同名方法互蓋。

This page is the map. Read it before adding code, and keep it current when a
boundary moves. Gotchas and release mechanics live in
[`DEVELOPMENT.md`](../DEVELOPMENT.md).

## 1. Shape of the system

```
 web/index.html (xterm.js, one IIFE)         external ingress
        │  pywebview js_api (one thread per call)   ├─ Telegram / LINE bridges
        ▼                                           ├─ sfctl CLI (command files)
 ┌───────────────────── Api ──────────────────┐     ├─ local HTTP API (api_server)
 │ main.py core: sessions, input, readiness,  │◄────┤─ Frame Link peers (frame_link)
 │ delegation, restore, config persistence    │     └─ agent hooks (sf_agent_hook)
 │ + api_*.py domain mixins (accounts, link,  │
 │   bridges, remote, status, update, …)      │
 └──────────────┬─────────────────────────────┘
                ▼
 domain modules: agent_status, usage_probe, account_manager, frame_link,
                 bridge_telegram, bridge_line, plugin_sdk, board, agent_*
                ▼
 OS adapters:    tmux (macOS/Linux) · pty · winpty/ConPTY (Windows) · pasteboard
```

`main.py` is the composition root: it owns module-level state (config file
paths, `ACCOUNT_MANAGER`, platform flags, the `Session` class) and builds one
`Api` instance that pywebview exposes to the page.

## 2. Module map

| Layer | Module | Responsibility |
|---|---|---|
| Composition root | `main.py` | `Session` (one PTY/tmux pane), the `Api` core: session lifecycle, input and paste, startup-dialog/readiness gating, delegation and master-turn preamble, restore after restart, config load/save |
| js_api domains | `api_accounts.py` | Claude/Codex account profiles, switching (carries the transcript, resumes the conversation), login capture, provider install, usage |
| | `api_bridges.py` | Start/stop the Telegram and LINE bridges, register tabs, keep bridge slots in sync |
| | `api_desktop.py` | Open files/URLs, clipboard text/images/files, drag-and-drop pasteboard |
| | `api_extensions.py` | Plugin loading and events, plugin marketplace, AI skill install |
| | `api_glasses.py` | Glasses relay: which tabs are exposed, status and transcript reporting |
| | `api_history.py` | Scroll-back history overlay (transcript → pyte → tmux capture) |
| | `api_link.py` | Frame Link panel: pairing, remote tabs, remote input/stream/files |
| | `api_remote.py` | sfctl command watcher and the `_execute_sfctl` dispatcher; local HTTP API server |
| | `api_schedules.py` | The Loops panel (user LaunchAgents) |
| | `api_status.py` | Agent status: hook events, status hook install, the status monitor thread |
| | `api_update.py` | Version, changelog, self-update, app restart |
| | `api_voice.py` | Speech-to-text settings and the in-app microphone |
| | `api_host.py` | Late-bound `main` handle the mixins use for main.py globals (see §5) |
| Domain | `agent_status.py` | Screen-based agent state detection per tab |
| | `agent_grok.py` | Grok Build's session files: which session a tab owns, turn status, transcript, model and usage |
| | `usage_probe.py` | Per-tab usage/quota probes |
| | `account_manager.py` | Profile directories and credential seeding per provider |
| | `frame_link.py`, `link_relay.py`, `relay_server.py` | Cross-machine pairing, signed peer requests, NAT relay |
| | `bridge_telegram.py`, `bridge_line.py`, `bridge_base.py` | Chat bridges: polling, routing, injection, reply extraction |
| | `plugin_sdk.py`, `board.py`, `agent_group.py`, `agent_link.py`, `agent_model.py`, `ai_skill.py` | Plugins, task board, role groups, agent-to-agent messages, model pins, AI skill |
| Ingress | `sfctl.py`, `api_server.py`, `sf_agent_hook.py` | CLI, HTTP API, Claude Code hook |
| Shared | `sf_log.py` | `_dlog` / `_swallow` logging primitives |

## 3. Data flows

- **Keystroke → PTY.** xterm `onData` → IME dedup → `pywebview.api.write_input` →
  `Api.write_input` (backend dedup, init-prompt / preamble injection) → `Session.write`.
  pywebview runs each js_api call on its own thread, so input has no ordering
  guarantee across calls.
- **PTY → UI.** Per-session reader thread fills `Session.buffer` → one pusher thread
  (`_start_output_pusher`) decodes, feeds the bridge queue and Frame Link, throttles
  background tabs and calls `evaluate_js('_pushOutput(...)')`. A slow
  `evaluate_js` delays every consumer of that loop.
- **External injection.** sfctl and agent hooks drop command files that the watcher
  (`api_remote`) executes one at a time; the HTTP API and Frame Link call
  `_execute_sfctl` directly; the Telegram bridge types into the tab
  (Ctrl-U, bracketed paste, Enter) after the readiness gate.
- **Agent status.** Hook events (exact) override the screen-scraping monitor
  (`api_status`, every 0.6 s, idle-gated); the UI is pushed only on change plus a
  heartbeat.

## 4. Threads and shared state

Background threads: one reader per session, the output pusher, the status
monitor, the sfctl watcher, the delay scheduler, the idle reaper, per-tab
startup-trust watchers, and the bridges' poll/dispatch/flush threads. Rules:

- Never hold a lock across `evaluate_js`, a bridge call or a subprocess.
- Iterate a snapshot (`list(d)`) of any dict another thread mutates.
- Hand out ids under a lock (`Api._next_sid`).
- Config: every load → modify → save holds the one shared lock
  (`sf_config.CONFIG_LOCK`; `main._CONFIG_LOCK` is the same object), or goes
  through `update_config`. Writes are atomic; reads take no lock.
  `load_config` falls back to the last good copy if the file cannot be parsed.
  `tests_config_rmw.py` rejects an unlocked read-modify-write.
- Telegram turns: arm a turn only through `TelegramBridge._begin_turn` (one locked
  step, bumps `turn_epoch`); flush-loop clean-up must check the epoch it extracted
  under. Unanswered reply markers stay in `slot.reply_markers`.

## 5. Rules for changes

1. **New js_api method** → the matching `api_<domain>.py` mixin. A new domain gets a
   new mixin added to `Api`'s bases in `main.py`.
2. **Mixins never `import main`.** main.py runs as `__main__`; importing it again
   would execute it twice. Reach main.py globals through
   `from api_host import main` → `main.load_config()`. This is a transitional seam:
   prefer moving a helper into its own module and importing it directly.
   `tests_architecture.py` caps the number of `main.` references per mixin.
3. **Size budgets** (`tests_architecture.py`): `main.py`, the `Api` class and every
   module have a ceiling; the oversized legacy files may only shrink.
4. **Test behaviour, not text.** If a test has to look at source, use
   `_testsrc.app_source()` so it keeps working when a method moves between files.
5. **One method, one definition.** A name defined in two mixins is resolved silently
   by the MRO; the fitness test forbids it.

## 6. Known hazards (open)

From the 0.38 architecture review; each needs its own change and tests.
(Fixed in 0.38.1: the two Telegram lost-reply races and unlocked config writes.)

- The Telegram bridge calls the host through a single command/result file pair
  (`_sfctl_call`); concurrent calls can overwrite each other.
- `_execute_sfctl` is a 1,000-line `if/elif` chain; a table of handlers would let
  sfctl help, the HTTP API and allowlists come from one place.
- `web/index.html` is one 8.5k-line IIFE with ad-hoc RPC calls and polling.
