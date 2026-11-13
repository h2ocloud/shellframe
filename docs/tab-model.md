# Switching a running tab's model from the screen

> 中文摘要：每個 AI 分頁上方有一顆模型標籤（對話畫面的標題列、原生 CLI 畫面的分頁列），
> 點下去選模型（和思考強度），ShellBot 代替你在那個分頁裡打這家 CLI 自己的切換操作，
> 打完讀畫面確認有沒有換成。忙的時候排到它做完再換；輸入框有草稿先問；讀不到輸入框先問。
> 這家 CLI 不能在對話中換的，照實說「要重開才能換」，走原本「儲存並現在就換」。
> 下面第一節是 2026-11-13 在真的 CLI 上量到的表，第二節是照這張表做的程式。

## 1. What each CLI does (measured)

Measured on 2026-11-13 in a tmux server of its own (`tmux -L sa12`), with no prompt sent. Claude Code
was started with `--model haiku` and only `/model`, `/effort` and menu keys were typed. A CLI that
cannot be reached on a machine (not signed in, or it never got past its own start-up) is marked
**not measured**; its row is what the CLI's own `--help` or bundled documentation says.

| CLI | In-session switch | Interactive? Can ShellBot drive it? | Effort / thinking | How to tell it worked | Writes a global default? | Measured |
|---|---|---|---|---|---|---|
| Claude Code 2.1.323 | `/model` opens a menu. **`/model <name>` and Enter or a number key in the menu both save the pick as the default for every new session.** `s` in the menu uses it for this tab only. | Yes. Rows are `N.  Name x.y`, the cursor row starts with `❯`, `More models` expands more rows. Up/Down move; `s` applies; Esc leaves (`Kept model as …`). Reliable: the footer says `s to use this session only`. | Same menu: Left/Right changes the effort shown under the list (`Low … Max`, does not wrap, resets to the row's default when the row changes). `/effort <level>` saves a per-model default; `/effort` alone is a slider with `s` for this session only. | `⎿  Set model to Opus 5.5 for this session only with max effort`; the effort glyph at the right of the footer (`◈ max · /effort`). The transcript has no new assistant message until the next prompt, so the status monitor still shows the old model meanwhile. | `/model <name>`, menu Enter or number: **yes** (`settings.json` `model`). Menu `s`: no. | Yes (haiku) |
| Codex 0.142.1 | `/model` opens menu 1 (models, numbered), then menu 2 (reasoning level, numbered). **`/model <name>` does not take an argument: it is sent to the agent as a prompt.** | Yes. Numbered rows, `›` marks the cursor, a number key picks. The model list is the CLI's own (`gpt-5.5 (default)`, `gpt-5.4`, `gpt-5.4-mini`, `gpt-5.3-codex`, `gpt-5.2` here); the model it runs on is not always in it. | Menu 2: `Low`, `Medium`, `High`, `Extra high` (the list depends on the model). | `• Model changed to gpt-5.4 high`; the line under the input box (`gpt-5.4 high · <folder>`). | Yes (`config.toml` `model`). | Yes (menus; not signed in, so nothing was sent) |
| OpenCode 1.18.30 | `/models` (typing `/model` completes to it) opens a searchable dialog. | Yes. Type part of a name to filter, Enter takes the first match. | No command. `ctrl+t` cycles a model's own variants, and the names differ per model. | The line under the input box: `Build · <model> <provider>`. | Yes (`state/opencode/model.json`, the recent model; not the config's `model`). | Yes |
| Grok Build 1.0.77 | `/model <name> [window] [effort]` (alias `/m`); `/model` alone is a picker. | Not needed: the arguments carry everything. | `/effort low\|medium\|high\|xhigh`, or as the last word of `/model`. | The session's `summary.json` (`current_model_id`, `reasoning_effort`), which `agent_grok.model_info` already reads. | Unknown. | **Not measured** (not signed in on the machine). From the CLI's bundled docs. |
| Cursor Agent 2026.11.05 | The CLI's documentation lists `/model <name>`; `--model` also takes bracket settings (`claude-opus-4-8[effort=high]`). | Unknown. | Bracket settings only. | Unknown. | Unknown. | **Not measured** (stopped at "Trusting workspace…"). |
| Pi 0.74.2 | `/model` opens a selector. `/model <name>` pops a completion list: the first Enter accepts the completion, a second one runs it. | Yes. Type to filter, Enter picks; the current model has a check mark. | `Shift+Tab` cycles `off → minimal → low → medium → high` and wraps; also `/settings` → Thinking level. | `Model: <name>`, `Thinking level: <level>`; the footer's right end (`<model> • <level>`; `thinking off` when off). | Yes (`settings.json` default model and thinking level). | Yes |

What this means for the design:

- A single typed line is enough only for Grok (and perhaps Cursor). Claude, Codex, OpenCode and Pi are **menu drivers**:
  type the opening command through `tab_delivery`, then read the screen and press keys.
- Claude is driven through its menu with `s`, never with `/model <name>`, because that would change the default of
  every other tab and every new one. Codex, OpenCode and Pi keep the pick as their own default, which ShellBot cannot
  avoid; the page says so next to the list.
- Codex must never be sent `/model <name>`.
- Confirmation is read from the screen, not assumed. A pick the CLI's menu does not list (Codex's table-listed
  `o3`, OpenCode's typed name that matches nothing) is reported as such, not guessed.

## 2. How ShellBot does it

| Piece | Where | What it does |
|---|---|---|
| The chip | `web/tabmodel.js`, `tabmodel.css` | Chat view header, terminal header (chat style), tab bar (native CLI style). Names the model the tab runs, "unknown" when it cannot be read. Click: the table's models, the thinking levels, a box for a name the list lacks, the questions below. |
| The Python side | `api_tab_model.py` | `tab_model_info(sid)`, `tab_model_set(sid, {model, effort, custom, draft, unreadable_ok})`, `tab_model_cancel`, `tab_model_dismiss`, `tab_model_save_bot`. A switch is a job: `waiting` (queued behind a turn or a draft), `switching`, then `ok`, `failed` or `unconfirmed`; `tab_model_info(sid).job` carries it for 3 minutes. |
| What each CLI takes | `tab_model.py` (`SWITCH`, the screen readers, `drive()`) | Section 1 as code. `drive()` only presses a key the screen has just shown to be there; anything else closes the menu with Esc and says so. |
| The opening line | `tab_delivery.deliver(..., what="model:<id>")` | `/model` (`/models` for OpenCode, `/model <name> [level]` for Grok, `/model <name>` for Cursor). Waits for a tab in a turn, on a question, with Telegram in flight or with text in its box; 10 minutes at most. A switch that is still queued is not carried over a restart (nothing would drive its menu). |
| Holding the tab | `tab_delivery.HOLD` | While the menu is driven (seconds), any other text for that tab waits ("it is switching its model"). |

The rules the page and the Python side keep:

1. **A tab in a turn is never cut into.** The switch queues; the chip says "switching when it is done", with Cancel.
2. **Text in the input box is never written over.** It is asked about: "switch after I send it" (queue behind the draft) or "clear it and switch" (one Ctrl+C, only while the tab is not working), or cancel.
3. **A box ShellBot cannot read is asked about first** (`composer_state` finds the box by its two `────` rules: Codex and OpenCode have none, and on Windows the screen is not read): "Switch anyway" or cancel. Nothing is typed before the person says so.
4. **The answer is read back.** Success shows what the CLI reported (`Opus 5.5 · max`), and a level the model did not take is said ("it is on high, not xhigh"). A failure shows its reason (`not_listed`, `no_menu`, `no_confirm`, `no_match`, `ambiguous`, `no_session_only`, …). Grok and Cursor, which were not measured, say "sent, not confirmed" when their files show nothing.
5. **The chip shows the switch's own answer until the status monitor reads something else.** Claude Code's transcript has no new assistant message until the next prompt, so the monitor keeps the old model after `/model`.
6. **A CLI that cannot switch inside a tab** (Antigravity, a command ShellBot does not know) says "this one has to be restarted to change"; for a remembered bot that has a model list, the existing "save and switch now" (`bots_switch(role, '', key, 'now')`) is offered.
7. **A remembered bot is asked, never assumed**: after a switch, "also keep this as the bot's model?" (`bots_switch(..., 'next_wake')`). Not saved unless yes.
8. **The list is `agent_model`'s table**, nothing else. A model the CLI's own menu lacks is reported as not listed (the table's Codex list is older than Codex's menu: `gpt-5-codex`, `gpt-5`, `o3` are launch-only there; a newer one such as `gpt-5.4` is typed under "Other"). OpenCode and Pi have no list in the table: the page has only the name box (OpenCode searches the names as it shows them, Pi the ids; part of a name is enough, the first match is taken; Pi refuses when several match).

Windows: the same code path, but `visible_text` falls back to the Telegram slot's screen or the recent output, which does not hold a menu reliably. Not verified on a Windows machine (outside this work); expect "the menu did not open" rather than a wrong switch.

`/link`: `tabs[sid].model`, `model_effort` and `switch` (`in_session`, `efforts`, `unverified`, `persists`) next to `choices.models`; see [`mobile-link.md`](mobile-link.md#分頁的模型v0431資料結構先留好手機這版不做).

## 3. Measuring a CLI again

When a CLI updates its menu, run the real thing in a tmux server of its own, never a person's tab:

```
tmux -L probe new -d -s t -x 150 -y 45 -c "$(mktemp -d)" "claude --model haiku"   # a cheap model; type only /model, never a prompt
tmux -L probe send-keys -t t "/model" Enter ; tmux -L probe capture-pane -p -t t
tmux -L probe kill-server
```

Back up the CLI's settings file first and compare after: Claude Code (`settings.json`), Codex (`config.toml`), Pi (`settings.json`) and OpenCode (`state/opencode/model.json`) all write the pick as the default. Then put the new screen into `tests_tab_model.py`.
