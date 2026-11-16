# Connectors: one way to connect a service to the AI tabs

> 中文摘要：連接器核心是一套「不認得任何服務」的共用底層。一個服務 = 一份 JSON 描述（名稱、連結方式、最小權限、
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

## The catalog: where a service sits on Settings → Connect

> 中文摘要：設定 → 連結是一個「應用程式目錄」：搜尋框、分類泡泡、為你推薦、這台 Mac、已連結 N 個（只算真的通的）、
> N 個要處理（點了只看那些）。這些全部由描述裡的選填欄位算出來（`category`、`recommended`、`platforms`、`local_app`、
> `unavailable`），核心與頁面都不認得任何服務名。舊的描述沒有這些欄位也照樣能讀。還不能連結的服務一樣列出來，寫清楚為什麼
> （規則 7）。Claude 帳號連接器那條是「一鍵」：按「連結」直接打開該服務在 claude.ai 的頁面，回到 ShellBot 就自動測試。

Five optional fields at the top of a description; an older description without them still reads (it has no bubble, is
not recommended, is connectable everywhere).

```jsonc
{ …,
  "category": {"id": "productivity", "name": T},  // one bubble per id; the same id carries the same name in every description
  "recommended": true,                             // listed under "Recommended for you"
  "platforms": ["mac"],                            // mac | windows | linux; left out = everywhere
  "local_app": true,                               // an app on this computer: the "This Mac" / "This PC" section and bubble
  "unavailable": {"text": T, "next": T},           // listed before it can be connected: then "routes": [] and no adapter
  "routes": [] }
```

And three for the wizard, all optional: a route's `"beta": true` (a badge on its card and on the catalog's row, when it
is the way the service starts with), a step's `"done_label": T` (the words of its main button, for a step that is a
question: "Yes, a claude.ai account"), and a step's `"in_browser": true` (below).

- `registry.validate` checks each field when it is there: a kebab-case category id with a name in both languages, true or
  false, known platforms (each once, at least one), `unavailable` only with `"routes": []` (no routes is refused
  without it), `in_browser` only on a step with a `url`. `registry.category_conflicts` finds one id named two ways;
  `tests_connectors_hygiene.py` runs it on the shipped descriptions. A link may carry a listing's id (a UUID as a whole
  path segment, as a directory page's address does); one in a query string is still refused as a possible key.
- `registry.available(desc, platform)` is `''`, `not_on_platform` (its `platforms` leave this computer out; asked
  first, so a Mac app never shows as this PC's) or `not_available` (it has `unavailable`). `engine.start` (and so `connect_signal`, `sfctl connect start`) answers that code
  and starts nothing; `problems.explain("not_available", desc)` says the description's own `unavailable` words. The
  status gets `available` (false: the reason is its `problem`, and it never needs attention); `health.words` says it
  "cannot be connected here yet". The phone's `connections` leaves such a service out.
- `connect_list` also answers `catalog` (one `registry.catalog_entry` per service: `id, summary, category, recommended,
  local_app, platforms, available, why, beta`) and `platform`. The page draws the catalog from it and the statuses,
  without asking for each description: a search box (name in either language, the one line, the category's name), the
  bubbles (All, this computer's apps, the categories fullest first, past four under "More ▾"), and the sections: "Needs
  you" (failed, needs reconnecting), "Recommended for you" (up to six, "See all" lists every service), "This Mac", then
  the rest. "N connected ›" counts only what works and lists it; "N to fix ›" (shown when there is one) lists what
  failed or needs reconnecting. A row's button says where it stands (Connect, Continue, ✓ Connected, Reconnect) and
  opens the same wizard; a service that cannot be connected here shows "Not yet" (or "Not on this computer"), which
  opens its reason in the row and never the wizard.
- The words of a check's or an action's result may name a value it came back with: `{plan}`, `{version}`, `{path}`. The
  engine keeps only the values those words name (`fields` beside the code; never anything else the adapter said) and
  fills them in; a name with no value is taken out with the brackets around it, so `{plan}` is never on screen.

### One click: a service through the Claude account's connector

The card asks as little as it can:

- **No choice of a way** when a service has one, or when its recommended way is the Claude account's connector
  (`claude_connector`): "Connect" starts that way at once. The other ways stay one "Change way" away.
- **No sign-in card when ShellBot can tell**: the first step's check (`claude_account`, the plan ShellBot keeps) runs on
  entering and, when it passes, the step is done by itself. Only when it cannot tell does the card ask (`done_label`
  "Yes, a claude.ai account"), with the check's own words for why.
- **`in_browser`**: a step done in the browser opens its `url` as soon as it is the step in front of the person (once per
  card: going back to it offers "Open it again" instead), and says to come back. When the window gets the focus again
  (at least 1.5 s later), the step counts as done and, when the next step is the test, the test starts by itself. Its
  button ("Done, test it") does the same for a window that never lost the focus.
- The connector services link straight to their own page in Claude's directory (`https://claude.ai/directory/<id>`, the
  address Anthropic's connector pages at claude.com/connectors link to), with "I'm stuck → the connectors page" if it
  does not open.

So connecting one is: Connect, then Connect and Allow on claude.ai, then back to ShellBot; the result card says it is
connected. `tests_connect_oneclick_ui.py` runs exactly that against the real js_api and engine.

`connectors/services/_claude_connector.py` is that adapter once (`ConnectorAdapter(service_id, tool, mark, name)`: the
connector's part of the tool names `mcp__claude_ai_<tool>__…`, the word of the test's last line, the service's name for
the question). A service then has a two-line `<id>.py` and its own `<id>.json` (steps `a1`, `a2`, `a3`). The test-tab
machinery is `_tab_test.py` (`TabTestAdapter`), shared with `_apple_events.py`; each adapter keeps its own test tabs, so
one service's clean-up never closes another's. Files that start with `_` are never taken for a service.

### One server for two services

Reminders and Calendar on a Mac are served by one local server, so both descriptions name the same entry,
`shellbot-apple-events`. Adding is "remove ours, add ours" as always, so the second add changes nothing; disconnecting
one keeps the entry while another service that is not "not started" names the same server (`removed.kept_for` says
which), and the last one removes it.

### The services that ship

| id | Name | Way | Other AI programs | Checked |
|---|---|---|---|---|
| `gmail` | Gmail | the connector (one click), or a local MCP server | the local server for every program (see `gmail.json`) | as before; the one-click flow with stand-ins |
| `google_drive` | Google Drive | the connector, one click | `none` (noted: Codex's own Google Drive plugin; Google's own MCP server is a developer preview) | descriptions, adapter and engine with stand-ins; the tool names as a Claude Code session lists them |
| `google_calendar` | Google Calendar | the connector, one click | `none` (noted: Google's own MCP server is a developer preview) | the same |
| `notion` | Notion | the connector, one click | `none` (noted: Notion's own hosted MCP server; Notion's docs give steps for Codex, Antigravity and Cursor; Grok Build and OpenCode take remote MCP servers by their own docs; Pi has MCP built in since 0.99) | the same |
| `slack` | Slack | the connector, one click (not recommended: in Communication) | `none` (Slack's own MCP server takes only the clients Slack lists) | the same |
| `mac_reminders`, `mac_calendar` | Reminders, Calendar | a local server, **beta**: `npx -y mcp-server-apple-events@1.6.2 --require-write-confirmation`, entry `shellbot-apple-events` | every program by MCP (Claude by ShellBot; codex, agy, grok on request; the rest by hand); a program without the confirmation form can only read | descriptions, adapter, engine and the shared entry with stand-ins; not on a real Mac |
| `mac_messages`, `mac_notes` | Messages, Notes | none yet (`unavailable`) | — | listed with the reason; nothing to run |

Reminders and Calendar are a beta because macOS decides who may read them, per app that starts the server (inside
ShellBot, ShellBot), and nobody has yet clicked through that question on a real Mac. The package's own README says the
app that starts it must be able to ask (it names the privacy usage descriptions an app declares) or macOS may refuse
without asking; ShellBot's bundle declares only the microphone's today. Messages and Notes wait for a local server
ShellBot can stand behind (2026-11: the ones that exist are each kept by one person, and most cannot be limited to
reading); taking `unavailable` out and adding a `local_mcp` route is a JSON change.

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

- **Settings → Connect** (the page, SA11): `connect_list`, `connect_describe`, `connect_status`, `connect_start`,
  `connect_step`, `connect_test`, `connect_disconnect` (js_api, all answer JSON with `success`; a failure has `code` and
  `message` {text, next}). `connect_step` ops: `check`, `act` (`action`, `options`, a key's `value`), `done`, `back`, `route`,
  `stuck` (`problem`, `from_sid`: the line goes to the tab asked from, else the primary bot, else a new Claude tab).
- **`sfctl connect list|status|start|test|disconnect <service>`** (`--json`, `--route`, `--reconnect`, `--refresh`).
  `start` begins or goes on and says which step is next and that it is continued in Settings → Connect.
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
- One click: that the window gets the focus when the person comes back from the browser was seen only in a headless
  WebKit (an event the test sends), not in the app's own window on macOS or Windows; the button is there for when it does
  not come. The directory addresses (`claude.ai/directory/<id>`) are the ones Anthropic's connector pages linked to on
  2026-11-16; one that stops working leaves "I'm stuck → the connectors page".
- Reminders and Calendar: no real Mac has run the server under ShellBot; whether macOS asks, and whether Claude Code shows
  the server's confirmation form (MCP elicitation) so that changes can be made at all, are not known yet.
- Google Drive, Google Calendar, Notion and Slack: the tool names (`mcp__claude_ai_Google_Drive__…`, `…Google_Calendar__…`,
  `…Notion__…`, `…Slack__…`, and `authenticate` / `complete_authentication` before a connector is authorized) are as a
  Claude Code session signed in with a claude.ai account lists them (2026-11). No connection test was run against a real
  account; the steps on claude.ai follow Gmail's, and what each other program offers is from the vendors' own pages
  (2026-11), set up by none of ShellBot's code.
