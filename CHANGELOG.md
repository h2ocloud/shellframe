# Changelog

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
