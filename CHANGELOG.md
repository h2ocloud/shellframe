# Changelog

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
