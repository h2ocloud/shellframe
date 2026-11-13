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

*(filled in as the code lands)*
