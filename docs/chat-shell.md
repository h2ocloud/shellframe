# The chat view (settings.ui_style)

> 中文摘要：桌面版每個分頁可以用「對話」或「原生 CLI」來看。對話畫面只是一層畫面：
> 讀的是手機那套資料（`conversation` 的 turns、`_link_decision` 的「要你決定」），
> 送的是手機那條路（`send`、`/link/answer`），分頁、程序、delivery 都不變。換字只換
> 顯示；選項認不全就照原文，讀不全就請人到終端機選，送出去的號碼永遠是 CLI 印的那個。

The desktop shows a tab either as its terminal ("Native CLI") or as a conversation
("Chat"). The chat view is a screen over the tab and nothing more: the terminal keeps
running underneath with its size unchanged, and everything that types into the tab
(sfctl, A2A, Telegram, Frame Link, the phone) is untouched.

| Piece | Where |
|---|---|
| Routes for the page | `api_chat.py` (`ChatApiMixin`): `chat_view`, `chat_send`, `chat_answer` |
| The screen | `web/chat.js` + `web/chat.css` (hooks: `SFChat.onSwitch`, `SFChat.covers`, `SFChat.sidebarLine`) |
| Tests | `tests_chat_shell.py` (backend), `tests_chat_shell.js` (the rules, run in node), `tests_chat_narrow.py` (the layout in a narrow window, in a browser) |

## Data: the phone's, not a second parser

- **Turns**: `chat_view` calls sfctl `conversation` (what `GET /link/conversation` gives a
  phone): `api_remote.conversation_turns` over `agent_status`'s normalisation.
- **Questions**: `_link_decision` (the `decision` of `GET /link/bots`): the last numbered
  menu on the visible screen, or the `[[SF:RED]]` question a turn ended on. The menu
  reader is Telegram's (`bridge_telegram.menu_block`): an option wrapped onto a second
  line is joined back, and numbers that do not run 1, 2, 3… (the top is off the screen)
  are not a menu. Its cases: `tests_decision_reader.py`.
- **Sending**: `chat_send` is sfctl `send`, the phone's message box: it waits for a turn,
  a menu or the user's draft to end (queued meanwhile, up to 30 min).
- **Answering**: `chat_answer` is `/link/answer`'s path (`_bots_link_answer`): only while
  that very question is up (`id`), only a listed option. A menu gets the option's number,
  and Enter only if the same menu is still up a moment later (`tab_delivery.answer_choice`:
  Codex takes the number alone and moves on); a `[[SF:RED]]` question is answered as a
  message, the number and Enter. Telegram's copies of the question say it was answered on
  the computer.

## Switching

- **Settings → Interface style** (`settings.ui_style`: `chat` | `cli`) is what a tab opens on.
- **Per tab**: the switch in the tab bar, the view's own "Terminal" button, or
  ⌘⇧M / Ctrl+Shift+M. The choice stays on that tab (switching tabs does not undo it)
  until the tab closes or the setting changes, which puts every tab back on the setting.
  It is kept with the tab's object, so a reused tab id starts from the setting.
- **New or existing computer**: a first launch has no `config.json` and gets
  `DEFAULT_CONFIG`'s `chat`. A `config.json` written before the setting existed belongs to
  a computer that already ran ShellBot: `api_chat.ensure_ui_style` writes `cli` once.
  Deleting `~/.config/shellframe` makes a computer new again.
- The view draws the CLIs whose conversation sfctl `conversation` can read
  (`CHAT_PROVIDERS`): Claude Code, Codex, OpenCode (its SQLite; a session is found by the
  title OpenCode gives it), Grok and Cursor (their session files) and Pi (`agent_pi.py`).
  Antigravity keeps its steps as protobuf and every other tab is a terminal: they show
  their terminal, and in chat style a small note says why. A tab with no record yet shows
  an empty conversation with the input box.

## New conversation, names, files

- **"+" in chat style** opens "New conversation": each AI by what it is for (the default
  variant of each provider group from `preset_groups`, no commands shown) and, with bots
  on, the bots. The full preset list and a typed command fold under "Advanced". In Native
  CLI style the dialog is unchanged.
- **Command-named tabs** (`claude`, `codex`, `bash`…) show as "Claude 對話" / "終端機" in
  chat style (tab bar, sidebar, header), numbered when there are several. Display only:
  the tab's name for sfctl, Telegram and A2A does not change.
- **Files**: a picture or file pasted into the box is saved by the terminal's own calls
  (`save_image`, `get_clipboard_files`, `save_file_from_clipboard`); a dropped one goes
  through the page's drop path (`attachFile` hands it to the view). They go in ahead of
  the words as their paths, as a phone sends a photo; Claude Code turns an image path
  into `[Image #N]` itself (measured), and its record of it shows as a file chip.

## Display rules (display only; tested in `tests_chat_shell.js`)

- **Questions** (`mapDecision`): an option list is put in plain words only when *every*
  option matches a known pattern (`OPTION_RULES`: Claude Code 2.1.322 and Codex 0.142.1
  wording, taken from their own strings); otherwise every option is shown as written.
  The number sent is always the option's own `n`, never its place on screen.
- **What a question is about** (`askContext`): the dialog's own words (the decision's
  `detail`, e.g. the whole command a permission prompt asks about), never translated;
  masked as `agent_status.redact` masks (a credential or any 32+ character token, a SHA
  say, shows as `<redacted>`; runs of spaces as one).
  Without them, the call it is about is shown as only its start (agent_status keeps a
  command's first line, 40 characters), with the terminal offered.
- **Tool calls**: consecutive calls fold into one plain line ("查了 3 個檔案、跑了 1 個指令");
  one click shows each call's tool and target. Nothing is dropped.
- **Working**: the face and one plain line from the status monitor's verdict
  ("正在改 relay.py", "正在跑測試"); the sidebar's second line uses the same words in chat style.
- **Turns**: a compaction summary (the phone's test: a user turn starting "This session is
  being continued from a previous conversation") is a folded note; Telegram's
  reply-marker paragraph and an A2A header become a small tag; Codex's empty reasoning
  records, its own context messages and its doubled answers are left out; `[[SF:…]]`
  markers are not shown, and a `[[SF:RED]]` tail folds away while it is up as buttons.
- **Markdown**: agent text is escaped first; then a fixed set of tags (paragraphs, lists,
  code, tables, quotes, bold, italics, links). Links are http(s) only and open outside
  the page.
- **Silence**: when the last word is yours, the tab is not working and nothing came back
  for 15 s, the view says so and offers the terminal (some failures are only on screen).

## Hidden in chat style

The terminal's vertical tab label (the view has a header instead) and the sidebar's model
badge. Command names are not shown as the conversation's name: a tab still called
`claude`/`codex` is titled "對話/Chat".

## A narrow view

The view's narrow layout follows its own width, not the window's (`@container chat` in
`web/chat.css`): an 800px window with the sidebar at its widest leaves it ~210px. Below
21em the Terminal button keeps only its icon, Send moves under the input box beside the
mic, and a question drops its second face, so the tab's name and the box stay usable.

## Not yet

- **Remote (Frame Link) tabs** keep their terminal. The turns (`/link/conversation`),
  sending (`/link/send`) and the state (`/link/state`) are there; what is missing is a
  question to answer: a peer gives one only with bots on (`/link/bots`), answering needs
  an `/link/answer` client in `frame_link.py` (at its size limit: something has to move
  out first), and buttons for a peer's menus should wait until the peer has the fixed
  shared menu reader.
- OpenCode's failed model calls are not in its record, so the view says "no answer came
  back" rather than the error; Pi's are (an error line).
