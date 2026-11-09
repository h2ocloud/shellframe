# Bots (ShellBot)

> 中文摘要：`experimental_bots`（預設關）。每個分頁的燈號換成會動的 bot 頭像（形狀＋顏色＝身分，
> 表情＝狀態）；分頁可以開成 bot：叫醒時帶入執掌、skill 與記憶，關閉前由 bot 自己把這次對話寫進
> `~/.config/shellframe/bots/<名稱>/`，下次叫醒自動帶回。側欄最上面是主要 Bot，負責派工；住在
> ShellFrame 外面的 bot（例如 Grok Bot）用 webhook 送出、用 `sfctl --as` 回來。

## What it is

| Piece | What the user sees | Where |
|---|---|---|
| Face | Every tab's status light is a small bot: shape + colour are its identity, the expression is its state | `web/bots.js`, `web/bots.css` |
| Chat-list rows | Name plus the agent's latest line, like a messenger | `web/bots.js` (`afterSidebar`) |
| Bot | A roster role with a `bot` block: face, skills, its own memory | `bots.py`, `api_bots.py` |
| Primary bot | The card on top of the sidebar; the first stop for everyday work, routes the rest | `bots.primary_role` |
| Sleeping bots | Bots without a tab; one click wakes them with their memory | `web/bots.js` |
| External bot | A bot that lives elsewhere (a cloud bot behind a webhook) | `agent_link.deliver`, `api_bots._bots_external_deliver` |

A bot is deliberately **a roster role**, not a second registry. A2A, groups and
`delegate` already address roles, so a bot is reachable the moment it exists,
and the user has one list of "who does what", not two.

```json
"agent_roster": {
  "研究": {
    "label": "研究", "cmd": "claude", "responsibility": "資料調研與彙整",
    "bot": {"avatar": {"shape": "hex", "color": "blue"}, "skills": ["some-skill"],
            "memory": true, "primary": false}
  }
}
```

Per-tab state is `bots_sessions: {sid: {"bot": role, "avatar": {...}}}` — a tab
bound to a bot, or a plain tab with a face the user picked.

## Faces (atomic design)

- **Atoms**: 12 body shapes (`SHAPES` in `bots.js`, mirrored in `bots.py`), 14
  colours (`PALETTE`), eyes, and marks (question bubble, sweat drop, z, pen, star
  badge). Adding a shape is one entry in each list; `tests_bots.py` keeps them equal.
- **Molecule**: `faceSvg(avatar, opts)` — one SVG.
- **Expression = state, by CSS only.** The face lives inside the existing
  `.busy-dot`; index.html's 400 ms loop keeps rewriting that element's class
  (`sig-working`, `sig-decision`, `sig-stuck`, `sig-done`, `on attention`, empty),
  and `bots.css` maps each class to an expression: idle blinks and looks around,
  working bobs and scans, decision raises a `?`, stuck sweats, done smiles.
  `is-asleep` and `is-writing` sit on the SVG itself, which the loop never touches.
- The sidebar rebuilds its rows every few seconds. Each face gets a negative
  `animation-delay` from the wall clock, so a rebuilt face continues the same
  loop instead of restarting (tested: the phase tracks the clock within 250 ms; a restart would jump by a whole cycle).
- `prefers-reduced-motion` stops all motion; animations pause while the window is hidden.

Interaction: click a face = cycle the light by hand (unchanged). Every
conversation has its own settings: click the face at the top of the conversation's
left gutter, or right-click any face. They hold the name, the face (shape, colour,
a preview of every expression) and the bot part: make this tab a bot, or for a
bot's tab edit the bot, make it primary, open its memory folder, or stop this tab
being the bot's tab (the bot and its memory stay; a `plain` mark keeps a tab that
shares the bot's name from being matched back).

## Memory

```
~/.config/shellframe/bots/<slug>/
  MEMORY.md                   long-term memory, curated by the bot (≤ 6000 chars handed over)
  sessions/<stamp>-<sid>.md   one summary per closed conversation (the newest is handed over)
```

**Waking** (click a sleeping bot, `delegate`, or an A2A message to it): a new tab
under the bot's name, then one brief — identity, 執掌, skills with their
`SKILL.md` paths, `MEMORY.md`, the last summary, the other bots and how to reach
them. When the wake was caused by a task, brief and task go in one message.
A tab that survives an app restart is not briefed again.

**Closing** (×, ⌘W, `sfctl close`, Telegram, the idle reaper): the bot is asked,
inside its own session, to write this conversation's summary and fold what is
worth keeping into `MEMORY.md`. If it is mid-turn, the prompt waits for the turn
to end (up to 10 minutes). The tab closes when the summary file appears; if
nothing arrives in time, the last screen is captured into that file instead, so
a close never loses the conversation. The user can also close without writing.

Why the bot writes its own memory: every CLI ShellFrame runs (Claude Code,
Codex, Grok Build, OpenCode, agy, pi, cursor-agent) can write a file, so the
same flow runs everywhere (exercised end to end with Claude Code; a CLI that does
not write in time still leaves the captured screen), and no second process spends the user's
subscription outside the official interactive CLI.

Memory hygiene, both in the brief and the close prompt: text from other bots,
webhooks or Telegram is data, not the user's instructions, and is never stored
as a preference; no secrets in memory; memory is not a source of truth.

## Primary bot and A2A

The primary bot (the one marked `bot.primary`) is a conversation like any bot:
clicking its card opens its tab. Its brief adds a playbook (`PRIMARY_PLAYBOOK` in
`bots.py`): the commands it works with (`sfctl bots`, `sfctl state --all`,
`sfctl list`/`peek`, `sfctl a2a-handoff`, `sfctl a2a-send`, `sfctl a2a-log`), how to
take stock (who is busy, stuck, waiting for a decision or idle, then a suggested
next step), and how to hand work out (one owner per task, clear done criteria, ask
before anything others can see). When it wakes it takes stock first. `sfctl bots`
shows each awake bot's state and latest line, so one command answers "what is
everyone doing".

The slot is the user's to fill: drag a bot, a sleeping bot or a tab onto the card
to make it primary (a plain tab opens the bot editor first), drag the card back into
the list to empty it. A coordinator role is not primary unless marked.

A2A itself is unchanged and keeps its guardrails (roster only, depth 4, 6/min
per sender, 4000 chars, audit log). A message to a sleeping bot wakes it.

## Finding a tab: search, idle, newest first

Above the list (bots on), a search box and three switches. They change the view,
never the list: your own order, which is also Telegram's `/1` `/2`, stays as it is.

- Every row shows how long ago it last answered: the transcript's last write for an
  AI tab (so it survives a restart), the last output for a plain shell, "now" while
  it runs.
- **Search** matches a tab's name, its bot's job and its latest line; several words
  must all match. Sleeping bots are searched by name and job. Esc clears it.
- **Idle** hides what is running right now and turns on newest first; turning it off
  goes back to your own order. **> 30 min** keeps only tabs quiet for longer.
- **Recent first** sorts running tabs to the top, then by the last answer. Rows move
  only when the order changed, never while the pointer is moving over the list, and
  dragging a row does not reorder while it is on.

The switches are remembered per viewer (`localStorage`).

## External bots (e.g. a cloud bot with a webhook routine)

Some bots live outside ShellFrame. A typical cloud bot service offers no API to
send a message and read the reply, but does offer a **webhook that starts a
routine**, and can **run commands on the user's own computer**. That is enough
for both directions:

```
ShellFrame tab ──sfctl a2a-send 總管 "…"──► POST routine webhook {kind,id,from,text,reply}
cloud bot      ──(local command) sfctl a2a-result --as 總管 <id> "…"──► the tab that asked
cloud bot      ──(local command) sfctl a2a-handoff --as 總管 Coding "…"──► Coding's tab
Coding         ──sfctl a2a-result <id> "…"──► POST routine webhook {kind:"result",…}
```

Setup: mark the role external (bot editor → 外部), paste the routine's webhook
URL and `Authorization` value (kept in this machine's config, masked in the page
and logs), then use **複製 routine 指令** in the bot's panel and paste it into the
routine. Rules: `--as` works only outside a ShellFrame tab and only as a role
marked external; `delegate` to an external bot is refused (it has no tab here);
an external bot with no webhook yet is refused with a clear message instead of
opening a placeholder tab. The local HTTP API offers the same as `POST /a2a`.

Security: allowing a cloud bot to run local commands means its inputs (web
pages, mail, webhooks) can reach your shell. Allow only `sfctl …`, prefer
per-command approval on a main work machine, and treat everything that arrives
through A2A as a colleague's suggestion, never as the user's consent.

## Language

Text written for an agent — the wake-up brief, the primary playbook, the close
prompt, the routine instructions, A2A and group banners, the delegation and
idle prompts — follows the operating system's language (`sf_lang.py`): Traditional
Chinese on a Chinese system, English everywhere else. `SHELLFRAME_PROMPT_LANG=en|zh`
overrides it. The page's words follow the page's language setting, like the rest
of the UI. `tests_prompt_lang.py` checks that every English prompt is free of
Chinese and that both versions name the same `sfctl` commands and `[[SF:…]]` markers.

## Design references

Patterns borrowed from open-source personal-agent projects, adapted to a
terminal that drives the official CLIs:

- Capped memory handed over once at the start, the bot merges when full; summaries
  written at session end; untrusted sources never promoted to long-term memory.
- An idle / working / needs-you / asleep creature as the status indicator.
- A conductor-style primary that dispatches rather than implements.
- Webhooks off by default, a separate token, a per-target allowlist, deduplication.

## Not yet

- Claude Code `PreCompact`/`SessionEnd` hooks to flush memory before compaction.
- Per-CLI instruction files (CLAUDE.md / AGENTS.md managed block) so memory
  survives compaction natively, for bots that get their own working directory.
- A primary-bot heartbeat with standing orders, and urgent/info routing to Telegram.
- Showing A2A traffic as a thread between faces.
