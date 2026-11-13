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
| | `api_link.py` | Frame Link panel: pairing, remote tabs, remote input/stream/files; `link_peer_bots` (the paired machines' bots, kept by `link_bots`) |
| | `api_remote.py` | sfctl command watcher and the `_execute_sfctl` dispatcher; local HTTP API server |
| | `api_schedules.py` | The Loops panel's schedule list (user LaunchAgents): reads the plist folder and `launchctl list`, toggles a job, builds the edit prompt. Which labels are the user's own comes from `schedules.json` in the config folder, never from code; on a platform without launchd it says so |
| | `api_status.py` | Agent status: hook events, status hook install, the status monitor thread |
| | `api_update.py` | Version, changelog, self-update, app restart |
| | `api_voice.py` | Speech-to-text settings and the in-app microphone |
| | `api_push.py` | Push to the paired phones: the `/link/push/*` and `/link/peer/name` routes, and the cues (a [[SF:RED]] question, a dialog or [[SF:YELLOW]], a finished turn, an a2a result, `sfctl notify`) the status monitor turns into pushes and withdrawals |
| | `api_bots.py` | Bots: state for the page, avatars, bot editor, wake, close-with-memory, external bots over A2A, the phone's bot view (`/link/bots`); `web/bots.js` + `web/bots.css` draw the faces |
| | `api_bot_memory.py` | What a bot keeps in its folder besides its memory file: the bot editor's calls for its own skills (`bots_own_skills`, `bots_skill_new/copy/delete/open`) and the thread that has `bot_memory.tick` look at the open bots once a minute |
| | `api_chat.py` | The desktop's chat view (`settings.ui_style`): a tab's turns and question as the phone gets them, messages and answers the phone's way; `web/chat.js` + `web/chat.css` draw it. Rules in [`chat-shell.md`](chat-shell.md) |
| | `api_tab_model.py` | The model chip on a running AI tab: `tab_model_info` (what it runs, the table's list, the levels, its bot), `tab_model_set` (a switch as a job: the CLI's opening line goes in through `tab_delivery`, then the menu is driven with the tab held), `tab_model_cancel`, `tab_model_dismiss`, `tab_model_save_bot`; `web/tabmodel.js` + `tabmodel.css` draw it. Rules and measurements in [`tab-model.md`](tab-model.md) |
| | `api_tmux.py` | Tab ↔ tmux bookkeeping around restore: sid resolution, background reattach of tabs that failed at startup, `sfctl orphans`, repaint after display wake |
| | `api_host.py` | Late-bound `main` handle the mixins use for main.py globals (see §5) |
| | `api_connect.py` | Connections: js_api `connect_list/describe/status/start/step/test/disconnect/signal`, `sfctl connect`, the phone's `connections` (a new field of `/link/bots`), a bot's `[[SF:RED:connect:<service>]]`, the hourly health check the status monitor starts. Rules and how to add a service in [`connectors.md`](connectors.md) |
| Domain | `agent_status.py` | Screen-based agent state detection per tab |
| | `agent_grok.py` | Grok Build's session files: which session a tab owns, turn status, transcript, model and usage |
| | `agent_cursor.py` | Cursor Agent (`cursor-agent` only): session pin, store.db, transcript, billing class |
| | `agent_route.py` | Quota hint for delegate / handoff. Suggests; does not spend or retarget |
| | `a2a_host.py` | sfctl A2A verbs over the existing Frame Link transport |
| | `a2a_ssh.py` | SSH executor for a headless cursor-agent on a host without ShellFrame |
| | `bot_skills.py` | A bot's own skills (`bots/<bot>/skills/<skill>/SKILL.md`): a blank one, a copy of a shared skill's folder (size cap, no links), delete, the folder to open; a skill's folder name is checked and its path kept inside the bot's skills folder. Reading them for the wake-up brief is `bots.own_skill_catalog` / `bots.brief_skills` |
| | `bot_memory.py` | An open bot saves its memory after real work and a quiet spell: when to ask (real conversation since the last save, idle for `bot_memory_idle_min`, and no running turn, question, draft, queue, close or request in flight), the request, and watching it end. Sent through `tab_delivery.deliver(queue=False)` |
| | `bot_names.py` | One name per bot, and the one way to find a bot or a tab by a name: a sid, a bot's name, a tab's title, an alias or old label, the start of exactly one; a bot's tab by its binding first. delegate, A2A, sfctl's tab commands and the bot view use it |
| | `bots.py` | Bots (`experimental_bots`): identity, per-bot memory folder, wake-up brief, close-time summary, external-bot payloads |
| | `agent_prompts.py`, `sf_lang.py` | The delegation and idle-summary prompts; the language every agent-facing text uses (the OS's: English or Traditional Chinese) |
| | `bridge_home.py` | The primary bot on Telegram: a phone's messages default to it (woken if asleep), `/home`, a bot's `[[SF:RED]]` pushed as buttons from any turn (and kept for `/link/bots` with or without Telegram), `sfctl tg-send`, quiet hours, pairing a phone with a one-time code. `bridge_telegram.py` only calls in; also the line under each tab on `/list` (`list_snippet`) |
| | `bridge_queue.py` | A Telegram message for a busy tab waits (turn on screen, hooks working, the user's draft) up to 30 min, with a ⚡ button to cut in; past that it is dropped and the phone is told |
| | `bridge_menu.py` | Telegram's command menu (`/1`, `/2` … one per tab) is sent off the thread that changed the tabs: `request(bridge)` returns at once, one thread per bridge sends it, requests that come while it works are one more send of the newest tabs, not one each. `TelegramBridge.refresh_commands` only asks it. Before this, `sfctl new`/`rename`/`close`/`bots-keep` waited on up to three 35 s Telegram calls on the thread that runs every sfctl command |
| | `launch_cmd.py` | What a tab's launch command runs: the `cd … &&` / `env …` / `VAR=…` prefix kept verbatim, the directory it starts in, and Claude Code's arguments rewritten for a relaunch (`--resume` the current conversation; no old session id, no opening prompt sent twice) |
| | `tab_delivery.py` | Hands text to a tab without cutting into a turn, a decision menu or the user's draft: a per-tab queue (30 min, `--now` skips it) kept on disk across restarts; tells the sender when an item cannot be delivered. Also what is on the screen now (`visible_text`, no scrollback) and how a numbered question is answered (`answer_choice`: the number, then Enter only while the same menu is still up; a `[[SF:RED]]` question takes the number and Enter). `HOLD`: a tab whose menu `tab_model` is driving takes no text; `cancel()` takes a waiting item out |
| | `tab_model.py` | Switching the model of a running tab the way each CLI does it: what each takes (`SWITCH`), how its screen reads (Claude Code's menu, Codex's two menus, OpenCode's and Pi's pickers), and `drive()`, the keys to press, with the answer read back from the screen. Pure: the host gives it `lines()` / `send()` / `probe()`; no tmux, no `import main` |
| | `usage_probe.py` | Per-tab usage/quota probes |
| | `sched_view.py` | What a LaunchAgent looks like on the Loops panel: pure functions over a plist and a clock, no launchctl and no `import main`. Cadence text in English and Traditional Chinese (weekdays merged: Mon–Fri 09:50 / 12:30), a month-and-day plist as a one-off of its own (ran / armed / not armed, the warning that launchd repeats it next year, one-offs on one script grouped), KeepAlive jobs, and the user's `schedules.json` (prefixes, titles, groups). `web/schedules.js` + `web/schedules.css` draw its rows |
| | `connectors/` | The connector core: a service is a JSON description plus one small adapter (`services/`), and the core knows no service by name (`tests_connectors_hygiene.py`). `registry` (read and check descriptions), `engine` (steps, state in `connectors.json`, resumable), `health` (the one status), `clis` (MCP add / remove / check, only `shellbot-*`), `secrets` (key store, mask), `adapter`, `problems` (plain words with a next step). See [`connectors.md`](connectors.md) |
| | `connect_tabs.py` | A temporary AI tab (not a bot) for an adapter's connection test: open, ask, read the transcript, close |
| | `tmux_sessions.py` | sf_* tmux sessions as data: tolerant listing, orphan report (also a read-only CLI), highest sid, client refresh |
| | `wake_watch.py` | macOS display sleep/wake notifications → callbacks on a worker thread; memory-pressure probe |
| | `app_bundle.py` | The app as the system shows it: the macOS bundle installed as `ShellBot.app` (the template keeps `ShellFrame.app`), found under either name (`find`), an older install renamed once with LaunchServices and the Dock told (`migrate`), the update's copy (`refresh`), the installer's pin of it first in the Dock (`pin_dock`: through `defaults` with a read-back, never the Dock's plist file); the Windows desktop shortcut `ShellBot.lnk` |
| | `account_manager.py` | Profile directories and credential seeding per provider |
| | `frame_link.py`, `link_relay.py`, `relay_server.py` | Cross-machine pairing, signed peer requests, NAT relay |
| | `link_bots.py` | Another machine's bots for this desktop: each paired desktop this one may control is asked for its `GET /link/bots` in the background (`PeerBots`, with `rev`, at most every 15 s; offline / 403 / 404 give nothing), and the page gets that machine's tab faces, its primary bot and its bots for `#` (`#<machine>/<bot>`, the A2A target; `addresses` picks a name or the id so it always resolves). `web/bots.js` draws them |
| | `bridge_telegram.py`, `bridge_line.py`, `bridge_base.py` | Chat bridges: polling, routing, injection, reply extraction |
| | `plugin_sdk.py`, `board.py`, `agent_group.py`, `agent_link.py`, `agent_model.py`, `ai_skill.py` | Plugins, task board, role groups, agent-to-agent rules (and how a message reads as one line, `agent_link.preview_of`, the same rule as `web/chat.js` `preview`), model pins, AI skill |
| Ingress | `sfctl.py`, `api_server.py`, `sf_agent_hook.py` | CLI, HTTP API, Claude Code hook |
| Shared | `sf_log.py` | `_dlog` / `_swallow` logging primitives |
| | `sf_push.py` | Push through APNs (leaf, stdlib plus the system's `openssl` and `curl`): `push.json`, registrations, payloads, ES256 provider token, HTTP/2 send, de-duplication and throttling; see [`mobile-link.md`](mobile-link.md) |
| | `js_guard.py` | `window.evaluate_js` from a background thread, with a time limit: pywebview's WebView2 backend can wait for a script's result forever, and that hung the sfctl loop; stuck calls are counted and new ones dropped past a few |
| | `sf_trail.py` | A trail for when the app goes: the start line (exe, console, restart), faulthandler's file, uncaught exceptions, Windows console events, the window's events, the page's js_api calls in and out (`shellframe_jsapi.log`), a line before each quiet `os._exit` |
| | `sf_config.py` | The one config.json lock, and the app's folder (`config_dir()`): `~/.config/shellframe`, `SHELLFRAME_CONFIG_DIR`, or for a test a folder of its own |
| | `sf_notify.py` | Optional outbound webhook (leaf, stdlib only): config, payload, masked send, transition tracker for auto-notify. Used by `sfctl notify` and the status monitor; see [`notify-webhook.md`](notify-webhook.md) |
| Page | `web/ctxmenu.js` + `ctxmenu.css` | The small pop-up menu at the pointer (`SFMenu.open`). The terminal's right-click opens it when nothing is selected; Paste runs when it is picked, never from the click itself |
| | `web/dialog.js` + `dialog.css` | The app's own confirm and alert (`SFDialog.confirm` / `SFDialog.alert`, Promises): a native one is titled with the page's address in WebView2. `tests_no_native_dialogs.py` keeps a native call out of the page |
| | `web/connect.js` + `connect.css` | Settings → Connect, the connection wizard card, the sidebar's "needs reconnecting" card and the chat view's `[[SF:RED:connect:<service>]]` card (`SFConnect`). It draws what `connect_list` / `connect_describe` / `connect_status` and the `view` of `connect_start` / `connect_step` / `connect_test` hand it and sends the clicks back; it holds no fact about a service and names none. `tests_connect_ui.py` runs it against a stand-in Python side |
| | `web/tabmodel.js` + `tabmodel.css` | The model chip (`SFTabModel.chipHtml`, drawn by `chat.js` in its headers, and in the tab bar in the native CLI style) and its pop-up: the table's models, thinking levels, the questions (a draft, a box that cannot be read, keep it for the bot) |
| | `web/layout.js` | How wide the sidebar may show for the window it is in (`--sidebar-cap`); the saved width is never touched. `web/index.html` is size-budgeted, so new page code goes in a file of its own |

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
  heartbeat. The same pass feeds the optional
  webhook auto-notify (`sf_notify`), which only reacts to real state transitions.

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
| | `mac_keyboard.py` | macOS: who holds the window's keyboard (key window, first responder), and handing it back to the web view after a click that left the page without focus |
