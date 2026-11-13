# Connectors: one way to connect a service to the AI tabs

> 中文摘要：連接器核心是一套「不認得任何服務」的共用底層。一個服務 = 一份 JSON 描述（名稱、接法、最小權限、
> 每家 CLI 怎麼接、步驟、常見問題）加一支小 adapter（只放需要程式的檢查和測試）。核心負責步驟狀態機、
> 重啟後接著走、MCP 加移查（只動 `shellbot-` 開頭的自己項目）、秘密進系統鑰匙圈、遮罩、同一份 status
> 給設定頁／側欄／sfctl／手機／Telegram。新增一個服務的目標是 2～4 小時。

A *service* is a mailbox, a file store, a chat, a calendar: something an AI tab should be able to use. The
core in `connectors/` walks a person through connecting one, keeps where they are, adds it to every AI
program ShellBot can set up, notices when it stops working, and shows the same status everywhere. It knows
no service by name: `tests_connectors_hygiene.py` fails if a service's name appears in a core file.

```
connectors/registry.py   read and check a service's description; the registry of services; parse_connect_tag
connectors/problems.py   plain-language errors, each with a next step; the words of the core's own actions
connectors/secrets.py    the key store (Keychain / Credential Manager), the one mask(), refusing defaults under test
connectors/clis.py       MCP add / remove / check in claude, codex, agy, grok; manual entries for the rest; the support table
connectors/adapter.py    Adapter (what a service's code implements) and Ctx (what the core hands it)
connectors/engine.py     the steps: start / step / test / disconnect / refresh; connectors.json in the state folder
connectors/health.py     the one status; its short form for the phone; one sentence for Telegram and sfctl
connectors/services/     one <id>.json (+ <id>.py when it needs code) per service
api_connect.py           js_api, sfctl connect, the phone's `connections`, a bot's connect tag, the hourly check
connect_tabs.py          a temporary AI tab for an adapter's connection test
```

## The rules the core keeps

1. **No secret in `config.json`, `connectors.json`, a log, a status, an answer or the screen.** A pasted key goes
   straight to the system's key store (`secrets.SecretStore`). `connectors.json` holds, per key, only yes or no;
   every save is checked (`secrets.assert_clean`) and a field shaped like a secret is refused, the file left as it was.
   Every text that is shown or logged goes through `secrets.mask`.
2. **The AI program's own settings file is where a key it needs ends up.** For a route with `env_from_secret`,
   ShellBot hands the value to each AI program (`claude mcp add -e KEY=VALUE`, and the others alike) so it can start
   the server; that program keeps it in its own settings file (`~/.claude.json`, `~/.codex/config.toml`, …). That is
   the AI program's own store, which the design allows, and it is the one place a key lives outside the key store.
   The step says so on screen. If that is not acceptable for a service, give it a route that needs no key.
3. **Least permissions.** Each route lists the scopes it asks for, a subset of the service's scopes that must contain
   every `required` one. A route for read-only use does not ask for more than reading.
4. **ShellBot only touches its own entries.** A server name must be `shellbot-<name>` (`clis.own`, and the description
   validator): anything else is refused before a program starts. Adding is "remove ours, then add ours" every time.
5. **A test says "it works" or which step did not, and never touches the content.** An adapter's `test()` returns a
   state and a step id; it does not return, keep or log what it saw (a mail's subject, a file's name).
6. **Nothing is run by a test.** The default runner and the default key store refuse while a test runs
   (`SHELLFRAME_TESTS`); a test passes stand-ins (`_connectors_fake.py`).
7. **What cannot be done is said.** `clis.table()` lists, per AI program, `connector` / `auto` / `manual` /
   `unsupported`, and the page shows it as it is.

## The status (health.py)

```
{ "service", "name": T, "icon", "kind": <route kind> | "",
  "state": not_started | waiting_user | verifying | connected | failed | needs_reconnect,
  "route": <route id> | "", "step": {"id", "index", "total"} | null,
  "clis": {"claude": connected | missing | manual | unsupported | failed | unknown, …},
  "scopes": [<scope id>], "connected_at": epoch, "checked_at": epoch,
  "problem": {"code", "text": T, "next": T, "step"} | null, "attention": bool }
```

`T` is `{"en": …, "zh": …}`. `attention` is true for `failed` and `needs_reconnect`: the "needs to be connected
again" card. The same dict feeds the Settings page, the sidebar card, `sfctl`, the phone's `/link/bots`
(`connections`, the short form `{id, name, icon, state, attention, step: "i/n"}`) and Telegram (`health.words`).

An adapter's health answer becomes a state in one place (`health.apply_health`):
`ok` → connected; `expired` / `revoked` → needs_reconnect; `unreachable` → stays connected, with a problem and no
attention (a dropped network is not a reason to redo steps); `unknown` → nothing changes. The engine adds two
losses the core can see by itself: an entry that left an AI program's settings (`cli_entry_missing`) and a key that
left the key store (`secret_missing`); both need reconnecting.

`engine.refresh(service)` keeps its own ttl (`health.ttl_s`, default 300 s). Nothing in `connectors/` starts a
thread: the Settings page asks with `connect_status(refresh)` while it is open, and the status monitor calls
`Api._connect_tick(now)` on every pass, which starts a one-shot thread once an hour (the first one two minutes after
the app starts). `sfctl connect status <service> --refresh` runs it on demand. A service that comes out of that check needing
attention is told to Telegram once, in one line with where to go (`bridge_home.tell_all`; nothing in quiet hours or with the bridge
off, tried again the next hour), and again only if it needs attention again after having been fine.

Reconnecting (`start(service, reconnect=True)`, or `start` on a `needs_reconnect` service) keeps what is still true:
a done step stays done unless it is marked `redo_on_reconnect`, its check no longer passes, or it is the step the
loss points at (the add step after `cli_entry_missing`, the key step after `secret_missing`). The test always runs again.

## A description (`connectors/services/<id>.json`, schema 1)

Every text a person reads is `{"en": "…", "zh": "…"}` (both required). `registry.validate(desc)` lists what is wrong.

```jsonc
{ "schema": 1, "id": "<id>", "name": T, "icon": "<icon key>", "summary": T,
  "adapter": "connectors.services.<id>",       // optional; the default. No such module = no adapter (built-in actions only)
  "scopes": [ {"id": "read", "label": T, "required": true} ],
  "routes": [ route, … ],                      // the first is recommended unless one says "recommended": true
  "faq":    [ {"id", "q": T, "a": T} ],
  "problems": { "<code>": {"text": T, "next": T} },   // adds to, or replaces, problems.py
  "health": {"ttl_s": 300} }

route = { "id", "kind": "claude_connector" | "local_mcp" | "api_key" | "oauth",
          "name": T, "blurb": T, "recommended": false, "minutes": 5, "needs": [T],
          "scopes": ["read"],                  // the least this route asks for
          "clis": { "<cli>": {"via": "connector" | "mcp" | "none", "note": T} },   // cli: claude codex agy grok opencode cursor pi
          "mcp":  { "server": "shellbot-<name>", "command": "npx", "args": ["-y", "pkg"], "win_command": ["cmd", "/c", "npx", "-y", "pkg"],
                    "env": {"K": "plain value"}, "env_from_secret": {"ENV_NAME": "<secret id>"} },
          "secrets": [ {"id", "label": T, "help": T} ],       // what the person pastes; kept in the key store
          "steps": [ step, …, {"id": "test", "kind": "test", "title": T, "intro": T} ],   // ends with the test
          "test": {"via": T, "verdicts": { "<code>": {"text": T, "next": T} }} }          // words for what a test comes to

step = { "id", "title": T, "how": [T], "url": "https://…", "see": T, "note": T,
         "check":   {"id", "on": "enter" | "done", "gate": bool, "pass": ["ok"], "states": {"<code>": {"tone", "text": T, "hint": T}}},
         "actions": [ {"id", "label": T, "builtin": "cli_add" | "cli_remove" | "cli_check" | "secret_set" | null,
                       "after": "check" | null, "results": {"<code>": {"tone", "text": T}}} ],
         "options": [ {"id", "label": T, "default": bool} ],
         "stuck":   [ {"id", "q": T, "a": T, "links": [{"label": T, "url" | "step"}]} ],
         "redo_on_reconnect": false }
```

How a step moves:

- A check with `"on": "enter"` runs when the step becomes the open one; if it passes, the step is done by itself
  and the next one opens. A check with `"on": "done"` runs when the person presses "I did it" (or `op=check`).
  With `"gate": true` a check that does not pass keeps the step; without it the step is passed anyway
  (the check was only information).
- An action runs, and its result (`state` code, shown with the tone and text of `results[state]`) is kept apart
  from the check's. `"after": "check"` runs the step's check right after the action; if it passes the step is done.
  The built-ins `cli_add` and `secret_set` finish their step when they work.
- `cli_add` adds the route's `mcp` to every AI program that has `"via": "mcp"` and that ShellBot can set up. The ones it
  cannot (`manual`) are listed on the step (`view.step.manual`) with what to enter: the command, the plain env, and
  only the *names* of values that come from the key store. `cli_remove` and `cli_check` are the other two.
- `stuck` entries are what "I'm stuck" offers; the chosen one's question goes into the line sent to a bot
  (`engine.stuck_message`: service, route, step, problem, and a promise that nothing secret is in it).

## The adapter (`connectors/services/<id>.py`)

```python
from connectors.adapter import Adapter

class MyAdapter(Adapter):
    id = "my-service"

    def check_tool(ctx, args):                     # fn(ctx, args) -> {"state": "<code>", …}
        return {"state": "ok" if ctx.find("my-tool") else "missing"}

    checks = {"tool_ok": check_tool}               # step.check.id
    actions = {"open_signin": ...}                 # step.actions[].id that are not built-ins

    def test(self, ctx, route, handle=None):       # "it works", or the step that did not
        return {"state": "done", "ok": True}

    def health(self, ctx, route):                  # ok | expired | revoked | unreachable | unknown
        return {"state": "ok"}

ADAPTER = MyAdapter()
```

Answers are plain dicts: a `state` code and scalars; no sentences (they are in the description), no command output, no
secret. An exception inside is caught and shown as `adapter_error`, without its text.

`test()` answers:

```
{"state": "done", "ok": True}
{"state": "done", "ok": False, "code": "<a verdict>", "failed_step": "<step id or ''>"}   // the step to go back to
{"state": "running", "handle": "<opaque>", "phase": "<code>"}                              // asked again with the handle
{"state": "needs_user", "handle": "<opaque>", "code": "<code>"}                            // waiting for the person
```

`ctx.cancel` true means stop and answer `{"state": "done", "ok": False, "code": "test_cancelled"}`; the service goes
back to what it was. `ctx` (`adapter.Ctx`): `service, route, cfg, env (no key or token), win, lang, run(argv, timeout, env) ->
Result(rc, out), find(binary), open_tab(cmd, title), open_path(path), tabs (connect_tabs.TabKit), secrets (get / set /
delete / has), cancel, now()`. A test that needs an AI tab uses `ctx.tabs` (a temporary tab that is not a bot, Telegram
forwarding off; the adapter judges from the *names* of the tools called and the last line, never from what they returned).

## Adding a service: 2 to 4 hours

| | Time | What |
|---|---|---|
| 1 | 15 min | Copy the made-up service in `_connectors_fake.py` into `services/<id>.json`: id, name, icon, scopes (the least), one route. |
| 2 | 45 min | The steps and their words in both languages. One card per step, one thing to do on it; put the usual mistakes in `stuck`. |
| 3 | 30–60 min | The adapter: the checks and actions the steps name. Keep each to one thing: "is the file there" (look at names and sizes, never open a login file), "open the sign-in". |
| 4 | 30 min | `test()` and `health()`. The test asks for the smallest thing that proves the way through is open, and returns codes only. |
| 5 | 30 min | A test file: `registry.validate(desc) == []`, `registry.validate_adapter(desc, adapter) == []`, then walk the route with the fake runner and key store, as `tests_connectors_engine.py` does. |
| 6 | 15 min | `docs/ai-skill.md`'s build table row; nothing else (the core, the page, sfctl and the phone need no change). |

`tests_connectors_hygiene.py` already checks every shipped description and that its adapter has every check and action
it names. A route that needs a key: `"kind": "api_key"`, `secrets`, `env_from_secret`, a `secret_set` step, then `cli_add`.

## How a person and a bot reach it

- **Settings → Connections** (the page, SA11): `connect_list`, `connect_describe`, `connect_status`, `connect_start`,
  `connect_step`, `connect_test`, `connect_disconnect` (js_api, all answer JSON with `success`; a failure has `code` and
  `message` {text, next}). `connect_step` ops: `check`, `act` (`action`, `options`, a key's `value`), `done`, `back`, `route`,
  `stuck` (`problem`, `from_sid`: the line goes to the tab asked from, else the primary bot, else a new Claude tab).
- **`sfctl connect list|status|start|test|disconnect <service>`** (`--json`, `--route`, `--reconnect`, `--refresh`).
  `start` begins or goes on and says which step is next and that it is continued in Settings → Connections.
- **A bot's `[[SF:RED:connect:<service>]]`**: `connect_signal` does the same as `connect_start`. The phone's
  `decision` carries `connect` ({service, name, state}); Telegram's question gets one more sentence
  (`api_connect._connect_line`).
- **The phone**: `/link/bots` has a new top-level `connections` (the short status of every service); old fields untouched.

## Not verified (said, not hidden)

- The AI programs' commands are from each one's own `mcp --help` (2026-11); no real account's settings were changed to
  check them. `claude mcp get` starts the server to answer, so a health check can take seconds; a program that does not
  answer in time is `unknown`, never "gone".
- The macOS Keychain backend was exercised against a fake `security` program only (the value goes by stdin as hex, never
  in an argument); look at it once on a Mac with a throwaway name. The Windows Credential Manager backend (advapi32) was not
  run on Windows; neither was `cmd /c` for a server's command.
- Pi, OpenCode and Cursor Agent are `manual`: ShellBot shows what to enter, it writes none of their files.
