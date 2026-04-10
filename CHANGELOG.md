# Changelog

## v0.5.4 (2026-04-10)

### New Features
- **Active tab persistence** — The tab you had focused when closing shellframe is now restored on next launch. Backed by `~/.config/shellframe/config.json` (durable across WKWebView storage clears) with localStorage as a secondary cache. Saved on every tab switch (debounced) and on `beforeunload`. The init flow does a `requestAnimationFrame` double-apply so the highlight + visible pane stay in sync even if an async render races.
- **Edge-driven scroll history** — Refined the tmux scroll history flow so it actually drives the scrollbar: on scroll-up, parks the tmux cursor at `top-line` so the next motion scrolls the screen straight into scrollback; on scroll-down, jumps the cursor to `bottom-line` so motion scrolls the screen back toward live (instead of walking the cursor across visible rows). Auto-cancels copy-mode at `scroll_position == 0`.

### Fixes
- **Active tab restore was painting wrong tab** — `get_active_tab` previously returned a raw Python string which pywebview occasionally surfaced as something other than a clean string. Now returns `{"sid": "..."}` JSON like every other API method, defensively parsed in JS.
- **Scroll-down line-walking** — Replaced literal `Up`/`Down` keys with semantic `-X cursor-up` / `-X cursor-down` (works under both vi and emacs `mode-keys`).

### 新功能
- **Active tab 記住** — 關閉 shellframe 時的當前 tab，下次開啟會自動回到。寫進 `~/.config/shellframe/config.json`（不怕 WKWebView 清 localStorage），localStorage 當二級 cache。每次切 tab debounce 寫一次、`beforeunload` 也補一次。init 流程加 `requestAnimationFrame` 二次校對，避免非同步 render race 把高亮畫錯 tab。
- **邊緣驅動的歷史滾動** — 重做 tmux scroll history：往上滾時把 tmux cursor 釘到 `top-line`，下一次 motion 直接把畫面往上推進歷史；往下滾時釘到 `bottom-line`，motion 往下推回 live，不再讓 cursor 在可見區走步。`scroll_position == 0` 自動 `cancel` 退出 copy-mode。

### 修正
- **Active tab 還原時高亮錯 tab** — `get_active_tab` 之前回 Python 純字串，pywebview 偶爾傳回的不是乾淨字串。改回 `{"sid": "..."}` JSON 格式跟其他 API 一致，JS 端 defensive parse。
- **滾動 cursor 走步** — 把 literal `Up`/`Down` key 換成 semantic `-X cursor-up` / `-X cursor-down`，vi 跟 emacs `mode-keys` 都通。

## v0.5.3 (2026-04-10)

### New Features
- **Scroll history via tmux copy-mode** — Claude/Codex TUIs redraw in-place via cursor positioning, so xterm.js scrollback is always empty. Now when you scroll up at the top of the terminal, shellframe automatically enters tmux copy-mode and jumps to the first page of real scrollback history. Navigate with PageUp/Down and arrow keys, press `q` to exit back to normal.
- **Stall detection** — If a TG message gets no response for 15s (common when macOS pops a permission dialog that blocks the CLI in the background), the bridge sends a TG warning and a macOS Notification Center alert with sound so you know to check your Mac.
- **Multi-image paste** — Pasting multiple images from clipboard now correctly attaches ALL of them (previously only the first was kept). The attach bar shows a count summary (`📷 4 images`) and each chip is tagged `#1` through `#N`.
- **TG slash commands per-chat scope** — Commands are now registered with `botCommandScopeChat` (highest priority), so they always show in the TG menu even when the Claude Code telegram plugin continuously overwrites the `all_private_chats` scope with its own `/start /help /status`.

### Fixes
- **Scrollbar visible but couldn't scroll** — The custom `scrollToLine` in `_pushOutput` was fighting xterm.js's native scroll-preserve behavior, snapping the viewport back on every PTY push. Removed entirely — xterm.js handles it natively.
- **UTF-8 garbled characters** (`─���─`) — `Session.read()` used a stateless `bytes.decode()` which replaced partial multi-byte characters at 16KB chunk boundaries with U+FFFD. Switched to `codecs.getincrementaldecoder('utf-8')` which carries incomplete sequences across calls.
- **TG bridge button wrapping** — Added `white-space: nowrap` to the TG status button so `TG ● 6` doesn't break across two lines when the tab bar is narrow.
- **setChatMenuButton** — Bridge now explicitly sets the menu button type to `commands` on every startup, preventing stale iOS TG client caches from showing an empty menu.

### 新功能
- **tmux copy-mode 滾動歷史** — Claude/Codex 的 TUI 用 cursor positioning 原地重繪，xterm.js 的 scrollback 永遠是 0 行。現在在終端頂端往上滾，shellframe 會自動進入 tmux copy-mode 並跳到第一頁歷史。用 PageUp/Down 和方向鍵翻閱，按 `q` 回到正常模式。
- **TG 無回應偵測** — 送出 TG 訊息 15 秒後若沒有 PTY 回應（常見原因：macOS 權限彈窗在背景擋住 CLI），bridge 會發 TG 警告並在 Mac 右上角跳 Notification Center 通知 + 聲音提醒。
- **多圖貼上** — 從剪貼簿一次貼多張圖，現在會正確附加全部（以前只留第一張）。附件列顯示 `📷 4 images` 總數，每個 chip 標 `#1` ~ `#N`。
- **TG slash 指令 per-chat scope** — 指令改用 `botCommandScopeChat` 註冊（最高優先），即使 Claude Code telegram plugin 不斷覆寫 `all_private_chats` scope 的 `/start /help /status`，你的 TG menu 永遠看得到 shellframe 完整指令。

### 修正
- **Scrollbar 看得到但滑不動** — `_pushOutput` 裡自訂的 `scrollToLine` 跟 xterm.js 內建的 scroll-preserve 互相打架，每次 PTY push 都把 viewport 拽回去。移除自訂邏輯，完全信任 xterm.js 原生行為。
- **UTF-8 亂碼** (`─���─`) — `Session.read()` 用無狀態 `bytes.decode()`，16KB chunk 剛好切在多位元字元中間就產生 U+FFFD。改用 `codecs.getincrementaldecoder('utf-8')` 跨 call 保留不完整 sequence。
- **TG 按鈕跑版** — TG 狀態按鈕加 `white-space: nowrap`，「TG ● 6」不再在窄 tab bar 時斷行。
- **setChatMenuButton** — Bridge 每次啟動都 explicit 設 menu button type 為 `commands`，避免 iOS TG client cache 卡住。

## v0.5.2 (2026-04-09)

### New Features
- **TG menu prompts** — When an AI session is waiting on a numbered choice (e.g., Claude permission dialog `❯ 1. Yes / 2. No`), the bridge now forwards the options to TG. Reply with just `1`, `2`, etc. and the digit is sent raw (no `Howard:` prefix) so the CLI picks the option.

### 新功能
- **TG 選單回應** — AI session 卡在編號選項（例如 Claude 權限對話框 `❯ 1. Yes / 2. No`）時，bridge 會把選項送到 TG。直接回 `1`、`2` 等數字即可，bridge 會跳過 `Howard:` 前綴讓 CLI 正確選擇。

## v0.5.1 (2026-04-09)

### New Features
- **AI busy indicator** — Tabs and sidebar entries now show a pulsing orange dot when an AI session is actively responding. Detection is purely client-side: lights up only when PTY output streams continuously (≥3 chunks spread over ≥400ms in a 1.5s window), so single-frame bursts from page reload, tmux reattach, or window resize don't false-trigger.
- **`/list` shows session previews** — Telegram `/list` now embeds a 3-line preview of each session's last AI response, so you can pick by content instead of by sid.
- **One-command install** — `install.sh` now runs end-to-end: clones, sets up venv, auto-installs `tmux` via Homebrew if missing, drops the `.app` bundle into `/Applications` for Launchpad/Spotlight visibility, and resolves the launcher PATH through symlinks.

### Fixes
- **TG bridge: switch always shows context** — `/N` switch messages used to come back empty when pyte couldn't find a `•`/`⏺` AI marker on the screen. The bridge now prefers `tmux capture-pane` (the same renderer you'd see attaching directly), with the pyte parser kept as fallback. Far fewer "Switched to claude" messages with no preview.
- **Scrollbar always visible** — WKWebView's auto-hiding overlay scrollbar made it nearly impossible to grab the xterm scrollbar on long conversations. Now styled as a 10px draggable bar that's always visible.
- **Scroll position survives tab switch** — Switching to another tab and back used to drop you to the bottom of the previous one. Scroll lock state is now preserved across `switchTab`.
- **Scroll position robust to overflow** — `_pushOutput`'s preserve-scroll path now anchors on absolute line first and falls back to offset-from-bottom if scrollback drops the original line.
- **`.app` launcher PATH** — Resolve symlinks before computing the bundle's PATH so launching from `/Applications` finds Homebrew binaries.

### 新功能
- **AI 忙碌燈號** — 分頁與側邊欄上的 session 名稱旁，AI 在回應時會顯示一個 pulse 中的橘色圓點。偵測完全在前端完成：只有在 PTY 持續吐 output 時（1.5 秒內 ≥3 次且 spread ≥400ms）才會亮，所以 reload UI、tmux reattach、視窗縮放等瞬間爆發不會誤觸。
- **`/list` 顯示對話 preview** — Telegram `/list` 每個 session 會帶最後 AI 回應的 3 行 preview，用對話內容找 session 而不是看 sid。
- **一行指令安裝** — `install.sh` 現在跑完整流程：clone、建 venv、缺 `tmux` 自動用 Homebrew 裝起來、把 `.app` 複製到 `/Applications` 讓 Launchpad / Spotlight 找得到，並 resolve symlinks 設好 launcher PATH。

### 修正
- **TG 切換永遠帶上下文** — `/N` 切 session 之前若 pyte 找不到 `•`/`⏺` AI marker 就送出空 preview。Bridge 改成優先用 `tmux capture-pane`（跟你直接 attach 看到的同一份內容），pyte 留作 fallback，幾乎不會再出現空 preview。
- **Scrollbar 永遠看得到** — WKWebView 的自動隱藏 overlay scrollbar 在長對話下幾乎抓不到。現在 xterm viewport 強制顯示 10px 可拖的 scrollbar。
- **切 tab 不再掉到底** — 在 A tab 滾上去看歷史，切到 B tab 再切回 A，scroll 位置會留在原本的位置而不是被拉回最底部。
- **Scroll 位置抗 scrollback overflow** — `_pushOutput` 保留位置時優先用絕對行號，超出 scrollback 時自動 fallback 到「距離底部 N 行」的相對錨點。
- **`.app` launcher PATH** — 從 `/Applications` 啟動時先 resolve symlinks 才推算 PATH，確保抓得到 Homebrew binaries。

## v0.5.0 (2026-04-09)

### New Features
- **Settings tabs** — Settings modal split into "General" and "Telegram Bridge" tabs.
- **Session rename** — Double-click tab or sidebar to rename. Persists via localStorage + config.json. Syncs to TG `/list`.
- **Smart paste for plain terminals** — Bash: image/file paste writes path directly. AI sessions keep attach UI.
- **Esc line kill** — Esc in plain terminal sends Ctrl+U to clear input line.
- **Preset save button** — Explicit ✓ button appears when preset name/cmd is modified.

### Fixes
- **Scroll lock** — Freely scroll back during AI output without snapping to bottom. Only resets on Enter.
- **Right-click copy/paste** — Capture selection on mousedown before xterm clears it.
- **IME bounce** — Constrain helper textarea to prevent Chinese composition bounce at edge.
- **Paste broken** — Fixed TEXTAREA check blocking xterm paste handler.
- **TG session switch from UI** — Sidebar switch now works even before any TG message is sent.
- **TG prefix echo** — Strip "Howard:" prefix when AI mimics the input format in responses.
- **About buttons** — Check + Reload moved to top of About modal.
- **Hot-reload error logging** — Traceback printed on `/reload` failure.

### 新功能
- **設定分頁** — 設定分為「一般」和「Telegram Bridge」兩頁。
- **Session 命名** — 雙擊分頁或側邊欄命名，localStorage + config.json 雙重持久化，同步 TG `/list`。
- **純終端智慧貼上** — Bash：貼圖/檔案直接寫路徑。AI session 維持附件 UI。
- **Esc 清行** — 純終端按 Esc 清掉整行。
- **Preset 儲存按鈕** — 修改指令後顯示 ✓ 按鈕，明確儲存。

### 修正
- **捲動鎖定** — AI 輸出時可自由回滾，不再被拉回底部。按 Enter 才重置。
- **右鍵複製/貼上** — mousedown 暫存選取文字。
- **IME 彈跳** — 限制 textarea 寬度防止中文組字溢出。
- **貼圖失效** — 修正 TEXTAREA 判斷誤擋 xterm paste。
- **TG session 切換** — 從側邊欄切換在重啟後也能正確運作。
- **TG 前綴回聲** — AI 模仿 "Howard:" 格式時自動去除。
- **About 按鈕上移** — Check 和 Reload 移到頂部。

## v0.4.3 (2026-04-08)

### New Features
- **Session rename** — Double-click tab or sidebar item to rename. Custom names sync to TG bridge `/list` and persist across reload/restart.
- **Smart paste for plain terminals** — Bash sessions: paste image/file writes path directly, no chip UI. AI sessions keep existing attach behavior.
- **Esc line kill** — Press Esc in plain terminal to clear current input line (sends Ctrl+U).
- **Settings tabs** — Settings modal split into "General" and "Telegram Bridge" tabs.
- **About buttons moved** — Check + Reload buttons moved to top of About modal for quick access.

### Fixes
- **Right-click copy/paste** — Capture selection on mousedown before xterm clears it. Paste uses write_input directly.
- **IME bounce** — Constrain xterm helper textarea width to prevent Chinese composition text bouncing at edge.
- **Rename UX** — In-page modal (no Python icon), optimistic update with green flash, dual-persist (localStorage + config.json).

### 新功能
- **Session 命名** — 雙擊分頁或側邊欄即可命名。名稱同步到 TG `/list`，reload/重啟後保留。
- **純終端智慧貼上** — Bash：貼圖/檔案直接寫入路徑，不跳附件 UI。AI session 維持原行為。
- **Esc 清行** — 純終端按 Esc 送 Ctrl+U 清掉整行輸入。
- **設定分頁** — 設定 modal 分為「一般」和「Telegram Bridge」兩個分頁。
- **About 按鈕上移** — Check 和 Reload 按鈕移到 About modal 頂部。

### 修正
- **右鍵複製/貼上** — 在 mousedown 時暫存選取文字，避免 xterm 清掉。貼上改用 write_input。
- **IME 彈跳** — 限制 xterm helper textarea 寬度，防止中文組字溢出邊緣。
- **命名 UX** — 改用頁內 modal、optimistic update + 綠色閃爍確認、雙重持久化。

## v0.4.0 (2026-04-08)

### New Features
- **Tmux-backed sessions** — PTY sessions now run inside tmux. Close ShellFrame and reopen — all tabs and their terminal state survive the restart. Requires `tmux` on PATH.
- **Auto-restore TG bridge** — Telegram bridge automatically reconnects on startup if previously configured. No more manual reconnect after restart.
- **Right-click copy/paste** — Windows CMD-style right-click: select text → right-click to copy; no selection → right-click to paste. Code blocks also get a copy button.
- **Sidebar rewrite** — Mouse-based drag reorder with event delegation. Two-section TG layout with active session indicator. Debug panel for troubleshooting TG switch + drag.
- **Tab drag reorder** — Tab numbering synced with TG bridge slot order. Drag tabs to reorder, reflected in TG `/list`.

### Fixes
- **Attachment UX** — Fixed scroll stability, tab switching artifacts, and TG sync issues with file attachments.
- **Drag conflicts** — Fixed tab drag accidentally triggering file drop handler. Sidebar drag uses internal variable instead of `dataTransfer` for reliable TG session switching.
- **TG active indicator** — Correct highlight for active session in sidebar TG section. Fixed divider drag zone interference.

### Dependencies
- **Python**: `pywebview>=5.0`, `pyte>=0.8` (install via `pip install -r requirements.txt`)
- **System**: `tmux` (required for session persistence — `brew install tmux` on macOS)
- **Windows only**: `pywinpty>=2.0` (auto-installed from requirements.txt)

### 新功能
- **Tmux 持久化 Session** — PTY session 改在 tmux 內執行。關閉 ShellFrame 再重開，所有分頁和終端狀態完整恢復。需要系統安裝 `tmux`。
- **TG 橋接自動重連** — 啟動時自動恢復先前設定的 Telegram 橋接連線，不需手動重連。
- **右鍵複製/貼上** — Windows CMD 風格：選取文字 → 右鍵複製；無選取 → 右鍵貼上。程式碼區塊也新增複製按鈕。
- **側邊欄重寫** — 滑鼠拖拉排序 + 事件委派架構。TG 雙區段佈局含作用中 session 指示器。新增除錯面板。
- **分頁拖拉排序** — 分頁編號與 TG bridge slot 順序同步，拖拉排序後 TG `/list` 即時反映。

### 修正
- **附件 UX** — 修正捲動穩定性、分頁切換殘影、TG 同步問題。
- **拖拉衝突** — 修正分頁拖拉誤觸檔案拖放。側邊欄改用內部變數取代 `dataTransfer`，TG session 切換更可靠。
- **TG 作用中指示器** — 側邊欄 TG 區段正確高亮作用中 session，修正分隔線拖拉區域干擾。

### 依賴
- **Python**: `pywebview>=5.0`、`pyte>=0.8`（執行 `pip install -r requirements.txt`）
- **系統**: `tmux`（session 持久化必要 — macOS 用 `brew install tmux`）
- **僅 Windows**: `pywinpty>=2.0`（由 requirements.txt 自動安裝）

## v0.3.3 (2026-04-08)

### New Features
- **Left sidebar** — Collapsible sidebar (☰) with session list, Settings & About links moved from tab bar. State persists via localStorage.
- **Per-session TG bridge toggle** — Each session shows a TG badge in the sidebar when bridge is active. Click to disable bridge monitoring for sessions that already handle their own TG connection.

### 新功能
- **左側欄** — 可收合的側邊欄（☰），顯示 session 列表，Settings 與 About 移入側邊欄底部。展開狀態透過 localStorage 記憶。
- **單一 Session TG 橋接開關** — Bridge 啟用時，側邊欄每個 session 旁顯示 TG badge，點擊可關閉該 session 的橋接監控，避免與 session 自帶的 TG 連線衝突。

## v0.3.2 (2026-04-07)

### Fixes
- **Typing latency reduction** — Output pusher is now event-driven (`threading.Event`) instead of fixed-interval sleep. Reader threads wake the pusher instantly when PTY data arrives.
- **Bridge feed decoupled** — `feed_output` (pyte parse + lock) moved to a dedicated thread via `SimpleQueue`, no longer blocks the output→frontend hot path.
- **JS keystroke microbatch** — `setTimeout(0)` batches rapid keystrokes into a single bridge IPC call, reducing WKWebView message-passing overhead during fast typing.

### 修正
- **打字延遲優化** — Output pusher 改為 event-driven，PTY 有資料時立即喚醒，不再固定 sleep 5-15ms。
- **Bridge feed 脫鉤** — `feed_output`（pyte 解析 + lock）移至獨立線程，不再阻塞 output 送前端的熱路徑。
- **JS 按鍵微批次** — 快速打字時合併多次按鍵為單一 bridge IPC call，減少 WKWebView 訊息傳遞開銷。

## v0.3.0 (2026-04-06)

### New Features
- **Init prompt injection** — AI CLI tools (Claude, Codex, Aider, Gemini, etc.) automatically receive ShellFrame context on first message. Non-AI commands (bash, vim, python) are skipped. Configurable per preset with `"inject_init": true/false`.
- **Multi-file attachments** — Paste multiple images/files via Cmd+V without overwriting. Image bar shows count and all filenames. Enter sends all paths at once.
- **TG file & photo receiving** — Telegram bridge now accepts photos and documents. Files are downloaded to `~/.claude/tmp/` and the path is forwarded to the active CLI session.
- **`sfctl` remote control** — AI agents can self-modify ShellFrame and hot-reload via `sfctl reload` / `sfctl status` from inside any session. File-based IPC with 15s timeout.
- **`INIT_PROMPT.md`** — Centralized init knowledge file. AI tools can edit it to evolve their own onboarding context. Two-section design: base ShellFrame context (always) + TG bridge section (only when bridge is active).
- **Source-based .app launcher** — `ShellFrame.app` now runs source code directly instead of py2app bundle, so code changes take effect on restart without rebuilding.

### Fixes
- **Clipboard paste broken** — Fixed xterm.js `stopPropagation()` blocking paste events. Switched to capture phase listener with proper ANSI/text passthrough.
- **`/reload` infinite loop** — Preserved TG polling offset across hot-reload so the `/reload` command isn't re-processed.
- **Output push reliability** — Added pending buffer to output pusher so data isn't lost during page reload/reconnect.
- **Auth-safe init injection** — Init prompt waits for AI-ready signals in CLI output (prompt markers, model info) before injecting. Login/auth flows pass through untouched.

### 新功能
- **Init prompt 自動注入** — AI CLI 工具（Claude、Codex、Aider、Gemini 等）在第一則訊息時自動帶入 ShellFrame 上下文。一般指令（bash、vim、python）不會觸發。可透過 preset 的 `"inject_init"` 自訂。
- **多檔案附加** — Cmd+V 可連續貼多張圖片/檔案，不會覆蓋。預覽列顯示數量和檔名，Enter 一次送出所有路徑。
- **TG 圖片/檔案接收** — Telegram bridge 現在可接收照片和文件，下載到 `~/.claude/tmp/` 後路徑轉發給 CLI session。
- **`sfctl` 遠端控制** — AI 可在 session 內透過 `sfctl reload` / `sfctl status` 自我修改並熱載入 ShellFrame。檔案式 IPC，15 秒 timeout。
- **`INIT_PROMPT.md`** — 集中管理 init 知識檔。AI 工具可自行編輯來進化上下文。雙區段設計：基礎 ShellFrame 上下文（永遠注入）+ TG bridge 區段（有連才加）。
- **原始碼直接啟動** — `ShellFrame.app` 改為直接執行原始碼，程式碼修改後重啟即可生效，不需重新打包。

### 修正
- **剪貼簿貼圖失效** — 修正 xterm.js 的 `stopPropagation()` 阻擋 paste 事件。改用 capture phase 監聽，正確區分圖片和純文字。
- **`/reload` 無限迴圈** — 熱載入時保留 TG polling offset，避免重新處理 `/reload` 指令。
- **Output push 可靠性** — 加入 pending buffer，頁面 reload/reconnect 時資料不再遺失。
- **登入安全的 init 注入** — Init prompt 等待 CLI output 出現對話就緒信號（prompt marker、model info）後才注入，登入/授權流程不受影響。

---

## v0.2.8 (2026-04-06)

### Fixes
- **TG Bridge: missing responses** — Switched from `pyte.Screen(200,50)` to `pyte.HistoryScreen` with 10K line scrollback. Long Claude responses that scrolled off the 50-line screen were silently lost.
- **Premature flush** — Increased force-flush timeout from 15s to 60s. Claude can take 2+ minutes; 15s caused mid-response extraction capturing spinners instead of actual replies.
- **`⏺` misclassified as spinner** — Removed from `spinner_chars` in filters.json; it's an AI response marker.
- **Startup update check respects settings** — Disabling auto-update in Settings now also skips the startup update modal.
- **Changelog rendered as HTML** — Update modal now formats release notes with proper headings and bullet styling instead of raw markdown text.

### New Features
- **Hot-reload bridge** — `/reload` command in Telegram hot-reloads `bridge_telegram.py` without restarting ShellFrame or killing PTY sessions. Also available via `hot_reload_bridge()` JS API.
- **Paste files from Finder** — Copy files in Finder (Cmd+C), then paste (Cmd+V) in ShellFrame to attach their path. Supports single and multiple files. Works alongside the existing image paste and drag-and-drop.

### 修正
- **TG 橋接：回應遺失** — 從 `pyte.Screen(200,50)` 改用 `HistoryScreen`（10K 行 scrollback）。超過 50 行的 Claude 回應不再消失。
- **過早 flush** — 強制 flush timeout 15s → 60s。Claude 跑 2 分鐘以上很常見，15s 會抓到 spinner 而非實際回應。
- **`⏺` 被誤判為 spinner** — 從 filters.json 的 spinner_chars 移除，這是 AI 回應標記。
- **啟動更新檢查尊重設定** — 關閉自動更新後，啟動時也不會跳更新彈窗。
- **Changelog 改為 HTML 渲染** — 更新彈窗的 release notes 用格式化顯示，不再是純文字。

### 新功能
- **熱載入橋接** — TG 輸入 `/reload` 可熱載入 bridge_telegram.py，不需重啟 ShellFrame 或中斷 PTY session。JS API 也可呼叫 `hot_reload_bridge()`。
- **Finder 複製貼上** — 在 Finder 複製檔案（Cmd+C），在 ShellFrame 貼上（Cmd+V）即可附加檔案路徑。支援單檔和多檔。與既有的圖片貼上和拖放並存。

---

## v0.2.7 (2026-04-05)

### Fixes
- **Bridge config persisted** — Bot token, allowed users, prefix, prompt saved to config.json and restored on restart
- **Settings pre-filled** — Opening Settings or TG modal auto-fills saved bridge config

### 修正
- **橋接設定持久化** — Bot token、白名單、前綴、prompt 存入 config.json，重開自動還原
- **設定自動填入** — 開啟 Settings 或 TG modal 自動帶入已存設定

---

## v0.2.6 (2026-04-05)

### Fixes
- **TG Bridge: messages not submitted** — Changed `\n` to `\r` (carriage return) when writing to PTY. Terminal expects `\r` to simulate Enter key, `\n` only moves cursor without submitting.

### 修正
- **TG 橋接：訊息沒有送出** — PTY 寫入改用 `\r`（carriage return）。終端機需要 `\r` 才等於按 Enter，`\n` 只會換行不會送出。

---

## v0.2.5 (2026-04-04)

### New Features
- **Auto-update toggle** — Disable automatic update checks in Settings
- **Background update check** — Every 5 minutes, silently pulls if update available
- **"Reload to update" banner** — Yellow banner in tab bar after background update
- **Release history** — About modal shows last 5 versions of changelog

### 新功能
- **自動更新開關** — 在設定中可關閉自動更新檢查
- **背景更新偵測** — 每 5 分鐘自動檢查，有更新靜默拉取
- **「重載以更新」提示** — 背景更新後 tab bar 顯示黃色提示
- **版本歷史** — About 介面顯示最近 5 個版本的 changelog

---

## v0.2.4 (2026-04-04)

### Fixes
- **Update-first startup** — Update notification now shows BEFORE launcher modal, not after
- **Release notes on update** — After updating and reloading, release notes display automatically
- **Skip → launcher** — Clicking "Skip" on update opens the session launcher

### 修正
- **更新優先啟動** — 更新通知現在在 launcher 之前顯示，不是之後
- **更新後 Release Notes** — 更新重載後自動顯示版本說明
- **跳過 → launcher** — 點「跳過」後自動開啟 session 選單

---

## v0.2.3 (2026-04-04)

### Fixes
- **Changelog display** — Fixed release notes showing "# Changelog" header instead of version content
- **Check Update button** — Added to About modal for manual update check
- **Reload UI button** — Reload frontend without breaking active sessions

### 修正
- **Changelog 顯示** — 修正 release notes 顯示標題而非版本內容
- **檢查更新按鈕** — About 介面新增手動檢查更新
- **重載 UI 按鈕** — 重載前端不影響現有 session

---

## v0.2.2 (2026-04-04)

### Improvements
- **Emoji picker** — Icon field replaced with clickable emoji selector (24 options)
- **UI polish** — Fixed white background on icon buttons, aligned add-preset row
- **Settings TG Bridge** — Configure Telegram Bridge directly in Settings modal

### 改善
- **Emoji 選擇器** — Icon 欄位改為點擊式 emoji 選單（24 個選項）
- **UI 修正** — 修正 icon 按鈕白底問題，對齊新增列
- **設定 TG 橋接** — 在設定介面直接配置 Telegram Bridge

---

## v0.2.1 (2026-04-04)

### Improvements
- **Update notification** — Startup modal shows available update with changelog preview
- **Release notes** — After update, shows what's new in a dedicated modal
- **Multi-session TG bridge** — One bot routes across all tabs with /list, /1, /2 slash commands
- **Version tracking** — Detects version change between sessions

### 改善
- **更新通知** — 啟動時彈出更新視窗，顯示更新內容預覽
- **Release Notes** — 更新後直接顯示新版本的變更說明
- **多 Session TG 橋接** — 一個 bot 管所有 tab，用 /list、/1、/2 切換
- **版號追蹤** — 偵測版本變化，自動顯示更新內容

---

## v0.2.0 (2026-04-04)

### New Features
- **Telegram Bridge** — Bidirectional TG bot ↔ PTY bridging with multi-session routing
- **Slash Commands** — `/list`, `/1`, `/2`... to switch sessions from TG
- **Pause/Resume** — One-click bridge toggle, auto-resume on TG message
- **Drag & Drop** — Drop files into window to attach file paths
- **i18n** — Traditional Chinese (繁體中文) + English
- **Font Size** — Adjustable in Settings (10-24px)
- **Auto Update** — Check for updates on startup, one-click update with hot reload
- **Windows ConPTY** — Full terminal experience on Windows via pywinpty
- **Settings Modal** — Manage presets, font size, language
- **About Modal** — Version, usage guide, shortcuts, license, update check

### 新功能
- **Telegram 橋接** — 雙向 TG bot ↔ PTY 橋接，支援多 session 路由
- **Slash 指令** — `/list`、`/1`、`/2`... 在 TG 切換 session
- **暫停/恢復** — 一鍵切換橋接，收到 TG 訊息自動恢復
- **拖拉檔案** — 拖檔案進視窗自動附加路徑
- **多語系** — 繁體中文 + 英文
- **字型大小** — 在設定中調整（10-24px）
- **自動更新** — 啟動時檢查新版，一鍵更新 + 熱重載（session 不斷）
- **Windows ConPTY** — Windows 完整終端體驗
- **設定介面** — 管理預設指令、字型大小、語言
- **關於介面** — 版本、使用說明、快捷鍵、授權、更新檢查

### Improvements
- Enlarged Settings/About icons in tab bar
- Fixed terminal refit on tab switch
- Fixed window close not killing child processes
- Tab bar scrollable when many tabs open
- Image path shortened in preview bar

### 改善
- 放大 tab bar 的設定/關於圖示
- 修正切換 tab 時終端機跑版
- 修正關閉視窗時子進程未正確終止
- 多 tab 時 tab bar 可捲動
- 預覽列路徑顯示縮短

---

## v0.1.0 (2026-04-04)

### Initial Release
- Multi-tab PTY sessions
- Clipboard image paste (Cmd+V) with inline preview
- Preset system for quick-launch commands
- Cross-platform: Mac (.app) / Windows / Linux
- macOS .app bundle with Spotlight/Launchpad support
- One-line install script (curl | bash)

### 初始版本
- 多分頁 PTY sessions
- 剪貼簿圖片貼上（Cmd+V）+ inline 預覽
- 預設指令系統
- 跨平台：Mac (.app) / Windows / Linux
- macOS .app 支援 Spotlight/Launchpad
- 一行安裝腳本
