# ShellBot

A home for your AI bots. Every CLI agent you already pay for (Claude Code, Codex, Grok Build, OpenCode, …) runs in its own tab; turn a tab into a **bot** and it gets a face, a job, its own memory that survives the tab, and colleagues it can hand work to — with a primary bot on top that takes stock and dispatches.

> ShellBot is the new name of **ShellFrame**. Only the name you see changed: the command (`sfctl`), the config folder (`~/.config/shellframe`), the repo and the install URL keep the old name, so existing setups and paired machines keep working.

## Why

Terminal-based AI assistants can't receive screenshots from your clipboard. ShellBot (then ShellFrame) started as a native GUI window that intercepts `Cmd+V`, saves the image, and injects the file path — screenshot-to-AI in one paste. Over time it grew into a full multi-agent control panel, and now into a team of bots. See [docs/bots.md](docs/bots.md).

## Features

| Feature | Description |
|---|---|
| **Chat view** | A tab can show a conversation instead of a terminal: replies line up one by one, tool calls collapse into a single line, and what needs your decision becomes buttons. Drop images and files into the input box. What you write here or on a phone goes into the tab at once, even while it is working; so do messages from other bots and computers. Each tab has a Chat / Terminal switch (`Cmd+Shift+M`); Settings → Interface style sets the default. |
| **Signing in again** | When a Claude Code tab's sign-in expires, the chat view shows a card that takes you through signing in again, with buttons only: it types `/login`, offers the subscription or the API account, opens the sign-in page on the computer you are at, takes the code if the page gives one, and then offers to send your last message again. |
| **Bots** | Every AI tab starts as a temporary bot; press **Keep** and it becomes a bot with a name, a face, a job, skills and a memory folder it writes before it closes. Switch its CLI or model later and the name, face and memory stay. A bot knows who it is from its first word: where the program takes added standing instructions (Claude Code, pi and launchers named after them), its name, what it looks after and where its memory is go in there on every start (a relaunch, Telegram's /relaunch, a restore after the computer restarted), so neither compaction nor /new loses them; other programs get them as the first message. A pi tab started again goes on with the conversation it was in, on the model that conversation was switched to. [docs/bots.md](docs/bots.md) |
| **Faces** | Shape and colour are who a bot is (12 shapes, 14 colours); the expression is what it is doing: working, waiting on you (`?`), stuck, done, writing memory, resting. |
| **Primary bot** | The card at the top of the sidebar: it takes stock of who is busy, stuck or waiting on you, and hands the work to the right bot. Dispatching needs bot-to-bot messaging (A2A), an experimental switch that is off by default. While a bot is primary it appears only on that card, not again in the list. |
| **"Needs your decision"** | A bot's question and its numbered options reach your phone or Telegram; one tap answers that bot. |
| **Tasks** | A bot keeps what it is doing as tasks (`sfctl task add`, `update`, `note`, `done`, `drop`): owner, next step, due date, what it waits for, and a line of history for every change. `sfctl task mine` shows a bot its open tasks and what other bots wait on it; `--decision` puts a question only you can answer on your own list (`sfctl task list --decisions`); `sfctl task stuck` lists what nothing has happened to for too long. A hand-off can belong to a task (`--for-task N`) and its answer comes back onto it. Everything is one database file on your computer, backed up daily (seven kept), with keys and tokens masked before they are written. |
| **Office** | Experimental, off by default: Settings → General → Office. A button at the sidebar's foot opens an office floor over the window: bots are the staff at their own desks (the primary bot at the lobby desk), AI tabs that are not bots are interns at a long table, and the subagents a Claude Code tab launches are part-timers standing beside whoever sent them until they finish, so a stuck one is easy to see. A Zoning select at the top arranges the staff by AI (the default: a walled room per CLI), in custom groups, or in one room. A lit screen, a red "!", a yellow sign and a coffee cup mean busy, waiting on you, stuck and idle, the same as the tab lights; click a person to follow them (the panel has a Conversation page, short like the chat view, with a box to give them an instruction and buttons to answer a question they ask; a bot with no open tab and a part-timer get no box; a paired computer's bot or intern that has a tab gets the same page, and what you write is sent to that computer), drag a bot to swap seats. With the switch off nothing runs. Its own text is Chinese only, a paired computer's part-timers are not shown, and hand-offs are not drawn as flying paper yet (the key already lists them). |
| **Remote tabs** | A tab opened from the sidebar's other-ShellBot section follows this computer's mode: in chat style it opens as a conversation read from that computer, otherwise you get its terminal. Its name carries "Remote · machine name" and the message box names the machine; your messages, answers, "send now" and signing in again go to that computer. Both computers need v0.46.1 or later; an older one shows its terminal with a line saying to update. |
| **iPhone / iPad app** | Pairs with your computer through Frame Link (Tailscale when you are off your network): bot list, chat view, one-tap answers. Not on the App Store; developer install only. [docs/mobile-link.md](docs/mobile-link.md) |
| **Supported CLIs** | Claude Code, Codex, Grok Build, OpenCode, Pi, Cursor and Antigravity are built in; any other terminal command opens as a tab too. Usage display for most of them. |
| **Connect** | Settings → Connect is a catalog of apps: a search box, categories, what needs your attention at the top and a row of recommended ones (Gmail, Google Calendar, Google Drive, Notion). Google Drive, Google Calendar, Notion and Slack link through the Claude account's connectors (Claude tabs): press Connect, ShellBot opens that service's own page on claude.ai, and when you come back it tests the connection by itself. Gmail is different: the Gmail server on this computer is recommended (any AI; the permission stays on this computer and reads mail only by default; it is added to, and tested in, the CLI the primary bot runs on, Claude Code or Codex), and the claude.ai connector comes second, for Claude tabs only. The Google Cloud steps say which audience to pick: Internal (a company Google Workspace address) does not expire; External stays in testing and asks you to sign in again every 7 days. “N connected” counts only what works; what needs fixing is counted apart. On a Mac, Reminders and Calendar have a beta route (a local server that asks before every change) that has not been tried on a real Mac yet, so macOS may not ask for access; Messages and Notes are listed as not available yet. A new service is one description file. ShellBot itself neither reads nor stores any mail. [docs/connectors.md](docs/connectors.md) |
| **Guide** | A first launch gets a short tour of the features (Next, Back, Skip). The sidebar's Guide button lists every feature with what it does and where it is; "show me" opens the Settings tab it lives on and points at it, and what is new since you last looked is counted on the button. During the tour the rest of the screen is dimmed and the place it points at gets a bright ring. A computer that already ran ShellBot gets no popup. [docs/guide.md](docs/guide.md) |
| **Find a tab** | The sidebar's search takes a name, a job or the latest line. Chips under it filter by Bot or by tab (temporary ones and terminals), and by idle, quiet for over 30 minutes, or newest reply first; the choice is remembered. |
| **Image/file paste** | `Cmd+V` screenshots, Finder files, or drag & drop. Preview bar + auto-attach on Enter. |
| **Multi-tab** | Multiple CLI sessions side by side. Named tabs, drag reorder. |
| **tmux persistence** | Sessions run inside tmux. Close ShellBot → reopen → all tabs and scrollback survive. (Windows restores the tab list; Claude Code and Codex reattach to their conversation, scrollback is not kept.) |
| **Telegram bridge** | One TG bot routes across all sessions. `/list`, `/1 /2` to switch, voice messages (STT), file/photo forwarding. |
| **Status lights** | Each AI tab shows whether it is working, waiting on you, stuck or done, read from the CLI's own conversation log and the screen. |
| **Session rename** | Double-click tab or sidebar to name sessions. Syncs to TG `/list`. |
| **Ctrl+Click file paths** | Click local file paths in terminal output to open in default app. |
| **Right-click copy/paste** | Select → right-click = copy. Nothing selected → right-click opens a small menu, and Paste pastes only when you pick it (a right-click never pastes by itself). |
| **Presets** | Saved commands with emoji icons. Drag to reorder. |
| **Settings tabs** | General (font, UI scale S/M/L, language), Bots, Telegram Bridge (connection, STT providers), LINE, Connect, and Advanced (auto /compact, the model badge, the local HTTP API, idle-tab clean-up and the Telegram and LINE wrappers; experiments nobody used are gone). |
| **Two-tier reload** | UI changes → hot reload. Python changes → full restart with session preservation notice. |
| **Cross-platform** | macOS (WKWebView), Windows (Edge WebView2), Linux (GTK WebView). |
| **Auto-update** | Startup check + one-click update. TG `/update` and `/update_now` for remote management. |
| **i18n** | English + 繁體中文. Auto-detects OS language. |

<details>
<summary>繁體中文</summary>

| 功能 | 說明 |
|---|---|
| **對話畫面** | 分頁可以改看對話而不是終端機：AI 的回覆一則一則排好，工具呼叫收成一行，要你決定的事變成按鈕。圖片和檔案可以直接拖進輸入框。你在這裡或手機上寫的訊息，分頁正在跑也會直接送進去，其他 bot 和電腦送來的訊息也一樣。每個分頁有「對話／終端機」切換鈕（`Cmd+Shift+M`），設定 → 介面風格決定預設。 |
| **重新登入** | Claude Code 分頁的登入過期時，對話畫面會出現一張卡片帶你重新登入，只要按按鈕：它替你送出 `/login`，讓你選訂閱帳號或 API 帳號，在你眼前這台電腦打開登入頁，登入頁給了代碼就收下，最後問你要不要把剛才那則訊息再送一次。 |
| **Bots** | 每個 AI 分頁一開就是臨時 bot；按「記住」就變成有名字、有臉、有執掌和 skill、有自己記憶資料夾的 bot，關閉前會自己寫記憶。之後換 CLI 或模型，名字、臉和記憶都留著。bot 從第一句話起就知道自己是誰：程式有提供額外常駐指示的地方（Claude Code、pi，以及用它們名字命名的啟動器），名字、負責什麼、記憶放在哪裡，每次啟動（重啟 CLI、Telegram 的 /relaunch、電腦重開後接回）都放在那裡，長對話被壓縮、按 /new 都不會弄丟；其他程式則當第一則訊息交給它。pi 分頁重新啟動時，會接著原本的對話，用對話裡切換過的模型。[docs/bots.md](docs/bots.md) |
| **臉** | 形狀和顏色是 bot 的身分（12 種形狀、14 種顏色），表情是它在做什麼：做事、等你決定（`?`）、卡住、做完、寫記憶、休息。 |
| **主要 bot** | 側欄最上面那張卡片：先盤點誰在忙、誰卡住、誰在等你，再把工作派給對的 bot。派工要用到「bot 之間傳話（A2A）」，這是預設關閉的實驗開關。當主要 bot 的時候，它只出現在那張卡片，清單裡不再重複。 |
| **「要你決定」** | bot 的提問連同編號選項送到手機或 Telegram，一按就回答那個 bot。 |
| **任務** | bot 把正在做的事記成任務（`sfctl task add`、`update`、`note`、`done`、`drop`）：誰負責、下一步、到期日、在等什麼，每次變動留一行紀錄。`sfctl task mine` 讓 bot 看到自己手上的任務和別的 bot 在等它的事；加上 `--decision`，只有你能回答的問題會放到你自己的清單（`sfctl task list --decisions`）；`sfctl task stuck` 列出太久沒動靜的任務。交辦可以掛在任務底下（`--for-task N`），回覆會寫回同一件任務。全部存在你這台電腦的一個資料庫檔案裡，每天備份（留七份），金鑰和 token 寫進去之前先遮掉。 |
| **辦公室** | 實驗性、預設關閉：設定 → 一般 →「辦公室」。側欄底部的按鈕打開一層辦公室：bot 是正職，坐在各自的桌子前（主要 bot 坐大廳的辦公桌），不是 bot 的 AI 分頁是實習生，坐長桌；Claude Code 分頁派出去的 subagent 是工讀生，站在派他的人旁邊，做完就走，卡住看得出來。上方的「分區」選單可以依 AI（預設：每種 CLI 一間有牆的房間）、自訂分組或不分區來排正職。螢幕亮、紅色「!」、黃色警示、咖啡杯，跟分頁燈號一樣，分別是在忙、等你決定、卡住、閒置；點一個人可以跟拍（面板有「對話」頁：精簡的對話，下面有傳話框可以下指令，他問的問題也能按選項回答；沒開分頁的 bot 和工讀生沒有傳話框；配對電腦上有分頁的 bot 和實習生一樣有，訊息送到那台電腦），拖曳 bot 可以換位子。開關關著時什麼都不跑。辦公室裡的字只有中文，配對電腦的工讀生看不到，交辦還不會畫成飛紙（圖例先列著）。 |
| **遠端分頁** | 從側欄「其他 ShellBot」那一區打開別台電腦的分頁，會照這台電腦的模式顯示：對話模式就當對話打開（內容從那台電腦讀來），否則顯示終端機。名稱帶著「遠端 · 機器名稱」，輸入框寫明是傳給哪一台；你的訊息、回答、「立刻送出」和重新登入都送到那台電腦。兩台都要更新到 v0.46.1 以上；舊版那一台會顯示終端機並提醒你更新。 |
| **iPhone／iPad App** | 透過 Frame Link 和你的電腦配對（不在同一個網路時用 Tailscale）：bot 清單、對話畫面、一按就回答。還沒上 App Store，目前只能由開發者安裝。[docs/mobile-link.md](docs/mobile-link.md) |
| **支援的 CLI** | 內建 Claude Code、Codex、Grok Build、OpenCode、Pi、Cursor、Antigravity；其他終端機指令也能開成分頁。大部分有額度顯示。 |
| **連結** | 設定 →「連結」是應用程式目錄：有搜尋框、分類，最上面是需要你處理的，接著是推薦（Gmail、Google 日曆、Google Drive、Notion）。Google Drive、Google 日曆、Notion、Slack 都透過 Claude 帳號的連接器（Claude 分頁可用）：按「連結」，ShellBot 直接打開 claude.ai 上那個服務的頁面，回到 ShellBot 時自動測試。Gmail 不一樣：推薦用這台電腦上的 Gmail 伺服器（每種 AI 都能用，授權存在這台電腦，預設只讀信；加進主 bot 用的 CLI，Claude Code 或 Codex，並在同一種 CLI 的分頁測試），claude.ai 的連接器排第二，只給 Claude 分頁用。Google Cloud 的步驟會說明選哪一種對象：公司 Google Workspace 信箱選 Internal，不會過期；選 External 會停在測試中，Google 每 7 天要重新登入一次。「已連結 N 個」只算真的通的，要處理的另外算。Mac 的提醒事項和行事曆有測試版路線（本機伺服器，每次修改前都會先問），還沒在真的 Mac 上試過，macOS 可能不會跳出授權；訊息和備忘錄標成「還不能連結」。新增一個服務只要一份描述檔。ShellBot 本身不讀、不存任何信件。[docs/connectors.md](docs/connectors.md) |
| **導覽** | 第一次開 ShellBot 會走一段功能導覽（下一步、上一步、略過）。側欄的「導覽」按鈕列出每個功能做什麼、在哪裡，「指給我看」會打開它所在的設定分頁並指出位置，上次看過之後的新功能會在按鈕上顯示數量。導覽時其餘畫面會壓暗，指到的地方有一圈亮框。已經在用 ShellBot 的電腦不會跳出導覽。[docs/guide.md](docs/guide.md) |
| **找分頁** | 側欄的搜尋用名稱、執掌或最新一句來找。下面的泡泡可以只看 Bot 或只看分頁（臨時的和終端機），也可以只看閒置、超過 30 分鐘沒動靜、最近回應排在上面；選的狀態會記住。 |
| **圖片／檔案貼上** | `Cmd+V` 貼截圖、Finder 檔案，或拖放。預覽列顯示，按 Enter 時自動附上。 |
| **多分頁** | 多個 CLI 工作階段並排。分頁可命名、拖曳排序。 |
| **tmux 持久化** | 工作階段跑在 tmux 裡。關掉 ShellBot 再打開，所有分頁和捲動紀錄都還在。（Windows 保留分頁清單，Claude Code、Codex 會接回對話，捲動紀錄不會留。） |
| **Telegram 橋接** | 一個 Telegram bot 串起所有工作階段：`/list`、`/1 /2` 切換、語音訊息（STT）、檔案和照片轉傳。 |
| **狀態燈** | 每個 AI 分頁顯示它是在做事、等你、卡住還是做完，從 CLI 自己的對話紀錄和畫面判斷。 |
| **工作階段改名** | 雙擊分頁或側欄命名，同步到 TG `/list`。 |
| **Ctrl+點擊檔案路徑** | 點終端機輸出裡的本機檔案路徑，用預設程式打開。 |
| **右鍵複製／貼上** | 選取後右鍵 = 複製。沒有選取時右鍵會開一個小選單，要選「貼上」才會貼（右鍵本身不會貼）。 |
| **預設指令** | 帶 emoji 圖示的常用指令，可拖曳排序。 |
| **設定分頁** | 一般（字型、介面大小 S/M/L、語言）、Bots、Telegram 橋接（連線、STT 供應商）、LINE、連結，和進階（自動 /compact、模型標籤、本機 HTTP API、閒置分頁清理、Telegram 和 LINE 的包裝設定；沒人用的實驗已拿掉）。 |
| **兩段式重載** | 改介面 → 熱重載；改 Python → 完整重啟並提示分頁已保留。 |
| **跨平台** | macOS（WKWebView）、Windows（Edge WebView2）、Linux（GTK WebView）。 |
| **自動更新** | 啟動時檢查，一鍵更新。Telegram 用 `/update`、`/update_now` 遠端管理。 |
| **多語系** | 英文＋繁體中文，依作業系統語言自動切換。 |

</details>

## Install

**One URL, any device** — open **https://h2ocloud.github.io/shellframe/#install** (the product page, in English and 繁體中文). It detects your OS and shows the right command with a copy button. Re-running the same command later updates ShellBot in place.

Prefer to copy directly:

### macOS / Linux

```bash
curl -fsSL https://raw.githubusercontent.com/h2ocloud/shellframe/main/install.sh | bash
```

Some Linux desktops (Ubuntu's among them) come without `curl`; there, use `wget -qO- https://raw.githubusercontent.com/h2ocloud/shellframe/main/install.sh | bash`.

Paste it into a terminal on a brand-new computer and it gets to ShellBot open: whatever is missing it installs, whatever is there it leaves alone. Each step says what it installs and why.

| Missing piece | macOS | Linux (apt / dnf / pacman) |
|---|---|---|
| Package manager | Homebrew | the system's own |
| git, a C compiler | Apple's Command Line Tools (Homebrew brings them) | `git` |
| Python 3.10+ with venv | Python 3.12 from Homebrew (not Apple's: its privacy permissions would not stick) | `python3`, `python3-venv` |
| tmux | `tmux` | `tmux` |
| The window | built in (WebKit) | GTK 3, WebKit2GTK, PyGObject (`python3-gi`, `gir1.2-webkit2-4.1`, …) |

Linux was tried from scratch on Ubuntu 22.04 and 24.04, Debian 12 and Fedora 45; Arch (pacman) is supported but not yet tried.

Then ShellBot itself in `~/.local/apps/shellframe`, its Python packages, the `shellframe` and `sfctl` commands, and ShellBot.app (macOS) or an app-menu entry (Linux). A first install opens ShellBot when it is done.

**What may need you** (each is announced before it happens):

- **macOS without Homebrew:** press **Enter** when Homebrew asks, then type your **login password** (nothing shows while you type). Your account must be an administrator.
- **macOS, if the Command Line Tools still are not there:** a macOS window asks to install them: click **Install**, then **Agree**. The installer waits and continues by itself.
- **Linux:** `sudo` asks for your password once, for the system packages.
- **At the end, optional:** "Also install Claude Code? / Codex?" (`y` or Enter to skip). If you say yes, the next step is logging in the first time you open that tab.

**How long:** Linux about 1½–4 minutes from a bare system (most of it the GTK/WebKit packages; a desktop already has many of them). Estimates, not yet timed on a clean machine: a Mac that already has Homebrew 1–3 minutes; a brand-new Mac about 10–20 minutes, most of it Apple's Command Line Tools download. Running the line again updates ShellBot and skips what is already there. `curl … | SHELLFRAME_DRY_RUN=1 bash` only prints what it would install.

### Windows

```powershell
irm https://raw.githubusercontent.com/h2ocloud/shellframe/main/install.ps1 | iex
```

Run in **PowerShell**. When missing, it installs Git, Python 3.12 (for your account only) and the WebView2 runtime, with `winget`; where winget is not available (Windows Sandbox, LTSC, Server) it downloads the same official installers directly. Then ShellBot, its Python packages, the `shellframe` / `sfctl` commands and a **ShellBot** desktop shortcut, and it opens ShellBot.

**What may need you:** Windows may ask *"Do you want to allow this app to make changes to your device?"* while Git or WebView2 installs: click **Yes**. At the end, optional: Claude Code / Codex. About 3–8 minutes on a new PC (an estimate, not yet timed).

### Requirements

The installers above take care of these:

- **Python 3.10+** (3.12 is what the installers install and test against)
- **tmux** — for session persistence (macOS / Linux; Windows uses soft persistence)
- **pywebview** + **pyte** — auto-installed via `requirements.txt`; on Linux pywebview draws with the distribution's GTK + WebKit2GTK
- **pywinpty** (Windows only) — auto-installed, provides ConPTY for TUI apps
- AI CLIs (Claude Code, Codex, …) are optional and installed separately; the installer offers Claude Code and Codex, and ShellBot's AI accounts panel offers the rest

## Usage

### Launch

```bash
shellframe            # GUI with session picker
# Or on Mac — Spotlight/Launchpad search "ShellFrame"
```

### Keyboard shortcuts

| Action | macOS | Windows |
|---|---|---|
| New tab | `Cmd+T` | `Ctrl+T` / `Ctrl+Shift+T` |
| Close tab (asks to confirm) | `Cmd+W` | `Ctrl+W` / `Ctrl+Shift+W` |
| Next / prev tab | `Ctrl+Tab` / `Ctrl+Shift+Tab`, `Cmd+]` / `Cmd+[` | `Ctrl+Tab` / `Ctrl+Shift+Tab`, `Ctrl+PgDn` / `Ctrl+PgUp` |
| Paste image/file | `Cmd+V` | `Ctrl+V` |
| Settings | `Cmd+,` | `Ctrl+,` |
| Copy selection | `Cmd+C` | `Ctrl+C` (only with a selection — otherwise it interrupts, as in any terminal) |
| Open file path in default app | `Cmd+Click` / `Ctrl+Click` | `Ctrl+Click` |
| Copy / paste | Right-click: copies a selection; with none, a menu whose Paste you pick | Same |
| Clear input line (plain terminal) | `Esc` | `Esc` |
| Rename session | Double-click tab | Double-click tab |

Bare `Ctrl` chords that terminals rely on are not hijacked: on macOS app shortcuts live exclusively on `Cmd` (`Ctrl+W` stays delete-word, `Ctrl+T` transpose), and `Ctrl+C` is never an app shortcut — on Windows it copies only when text is selected. Closing a tab from the keyboard always shows a confirmation, so a mistyped chord can't kill a running session.

### Idle Tab Cleanup

Open Settings → Advanced → Idle tab cleanup to control ShellFrame's automatic
AI tab cleanup.

- **Idle after** controls how long an AI tab can sit without user input before
  ShellFrame asks it for a short summary.
- **Summary wait** controls how long ShellFrame waits after asking for that
  summary before closing the tab.

The same values live in `~/.config/shellframe/config.json`:

```json
"idle_reaper": {
  "enabled": true,
  "idle_sec": 1800,
  "summary_grace_sec": 120
}
```

For example, change `idle_sec` from `1800` to `10800` for 3 hours.

### Master / Worker Delegation

ShellFrame can keep one user-facing primary-bot tab and delegate real work to
configured worker roles without hard-routing incoming messages.

```bash
sfctl roster
sfctl delegate 信件 "草擬明天的會議通知"
sfctl delegate 寫程式 "修設定畫面的排版"
sfctl delegate 研究 "整理這週的待辦"
sfctl peek s6 --lines 80
```

Roles live in `~/.config/shellframe/config.json` under `agent_roster`. The
delegate command creates or reuses the role's labeled tab, sends a wrapper
prompt with that role's responsibility, and returns the sid for polling.

Finished worker tabs are kept by default so the master can follow up in the
same context. Do not close a worker just because its task is done; let idle tab
cleanup summarize and remove unused workers later, unless the user explicitly
asks to close it or the tab is broken/noisy.

Workers should return user-ready drafts, reports, lookup results, or operation
conclusions immediately in a ready-to-forward form. The master can pass those
results back to the user while other delegated work continues.

Workers should search from known project paths. Avoid broad scans of `/Users`,
`~/Library`, `~/Library/Mobile Documents`, Mail, Messages, Photos, or other
macOS protected data folders because they can trigger privacy prompts for the
ShellFrame Python process.

### Telegram Bridge

1. Open Settings → Telegram Bridge
2. Paste your bot token, set allowed user IDs
3. Click Connect

TG commands:
| Command | Action |
|---|---|
| `/list` | List all sessions with last response preview |
| `/1` `/2` `/3`... | Switch to session N |
| `/new` | Create new session (shows preset picker) |
| `/close` | Close current session |
| `/pause` / `/resume` | Pause/resume bridge |
| `/reload` | Hot-reload bridge code |
| `/restart` | Full app restart (sessions preserved) |
| `/update` | Check for updates |
| `/update_now` | Pull + restart if needed |
| `/status` | Show bridge status |

Voice messages are transcribed via configurable STT providers (Settings → TG Bridge → 🎙 STT).

### STT (Voice Transcription)

Supports a pluggable provider chain:
- **Local**: whisper.cpp via `whisper-cli` (install from Settings)
- **Remote**: any whisper-compatible HTTP server (configure in Settings)
- **Plugin**: custom Python at `~/.config/shellframe/stt_plugin.py`

Backend modes: `auto` (plugin → local → remote) / `local` / `remote` / `plugin` / `off`.

## Architecture

```
shellframe/
├── main.py                 # Python: multi-session PTY + pywebview + tmux + bridge API
├── bridge_telegram.py      # TG bot: multi-session routing, STT, menu prompts
├── bridge_base.py          # Base class for bridges
├── web/index.html          # Frontend: xterm.js + tabs + sidebar + modals + i18n
├── sfctl.py                # CLI remote control (file-based IPC)
├── filters.json            # Dynamic output filter rules for TG bridge
├── ShellFrame.app/         # macOS .app bundle template (installed as ShellBot.app)
├── install.sh              # macOS/Linux installer
├── install.ps1             # Windows installer
├── requirements.txt        # Python: pywebview, pyte, pywinpty (Windows)
├── version.json            # Version tracking for auto-update
├── CHANGELOG.md            # Release history (bilingual)
├── WINDOWS.md              # Windows-specific docs
└── .github/
    └── REVIEW_WORKFLOW.md  # PR/issue triage playbook
```

### Tech stack

| Component | Technology |
|---|---|
| GUI window | pywebview (native OS WebView — not Electron) |
| Terminal | xterm.js 5.5 + fit/web-links/unicode11 addons |
| PTY | `pty.fork()` + tmux (Unix) / pywinpty ConPTY (Windows) |
| TG bridge | urllib (stdlib, zero external deps) + pyte virtual terminal |
| STT | whisper.cpp (local) / HTTP providers (remote) / plugin file |
| Config | JSON (`~/.config/shellframe/config.json`) |
| IPC | File-based (`sfctl`) for in-session remote control |

### Versioning

Format: `MAJOR.MINOR.PATCH` (e.g. `0.13.0`)

| Bump | When | Example |
|---|---|---|
| **MINOR** (`0.X.0`) | New feature, new Settings UI section, new bridge capability, architectural change | 0.12 → 0.13 |
| **PATCH** (`0.0.X`) | Bug fix, copy/style tweak, existing feature refinement | 0.13.0 → 0.13.1 |

- Always bump `version.json` **and** add a CHANGELOG entry (bilingual EN + ZH-TW).
- Multiple patches within one session can share a single version bump.
- MAJOR stays at `0` until a stable public release.

### Session persistence

**macOS/Linux**: Every PTY runs inside a tmux session (`sf_s1`, `sf_s2`...). Close ShellFrame → tmux sessions survive. Next launch → automatic reattach with full scrollback.

**Windows**: No tmux. ShellFrame uses "soft persistence" — saves the session list to config, recreates fresh PTYs on next launch. Same tabs and labels, but scrollback is lost. See [WINDOWS.md](WINDOWS.md).

## File locations

| Purpose | macOS/Linux | Windows |
|---|---|---|
| Source + venv | `~/.local/apps/shellframe` | `%USERPROFILE%\.local\apps\shellframe` |
| Config | `~/.config/shellframe/config.json` | `%USERPROFILE%\.config\shellframe\config.json` |
| STT plugin | `~/.config/shellframe/stt_plugin.py` | same |
| Whisper model | `~/.local/share/shellframe/whisper-models/` | same |
| Temp (logs, IPC) | `/tmp/shellframe_*.log` | `%TEMP%\shellframe_*.log` |
| TG offset | `~/.config/shellframe/tg_offset.json` | same |

## Update

ShellFrame checks for updates on startup. Manual:

```bash
cd ~/.local/apps/shellframe && git pull
```

Or from Telegram: `/update_now`

## Contributing

See [.github/REVIEW_WORKFLOW.md](.github/REVIEW_WORKFLOW.md) for the PR/issue review process.

## Author

**Howard Wu** ([@h2ocloud](https://github.com/h2ocloud))

## License

[MIT](LICENSE)
