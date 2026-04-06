# ShellFrame v0.3.0 開發交接

> 最後更新：2026-04-06

## 本次改動總覽（v0.2.8 → v0.3.0）

### 1. 剪貼簿貼圖修復

**問題**：Cmd+V 貼圖完全失效。  
**根因**：v0.2.8 加了 `if (el.tagName === 'TEXTAREA') return` 保護，但 xterm.js 用隱藏 `<textarea>` 接收輸入，所以 paste handler 永遠跳出。加上 xterm.js 的 `handlePasteEvent` 呼叫 `stopPropagation()`，事件無法冒泡到 document。  
**修正**：改用 capture phase（`addEventListener('paste', ..., true)`），圖片/檔案時 `stopPropagation()` 阻止 xterm 處理，純文字放行。

### 2. 多檔案附加

**問題**：每次貼圖/檔案都覆蓋前一個。  
**修正**：`attachPath`（string）→ `attachments[]`（array of `{path, dataUrl}`），支援累加、去重、批次送出。

### 3. TG 圖片/檔案接收

**問題**：TG 傳圖或檔案，bridge 直接忽略（`if not text: return`）。  
**修正**：新增 `_download_tg_file()` 透過 `getFile` API 下載到 `~/.claude/tmp/`，路徑附加到訊息一起送入 CLI。

### 4. `/reload` 無限迴圈

**問題**：`/reload` 後新 bridge 的 `_offset=0`，重新拉到 `/reload` 訊息。  
**修正**：`hot_reload_bridge()` 保留並傳遞 `saved_offset`。

### 5. Output pusher pending buffer

**問題**：頁面 reload 時 pusher 用 `s.read()` 清空 buffer，但前端還沒 ready，資料丟失（白畫面）。  
**修正**：pusher 用 `pending` dict 暫存，`evaluate_js` 失敗時保留，下次重試。

### 6. `.app` 改用原始碼啟動

**問題**：`ShellFrame.app` 跑的是 py2app 打包的舊 binary（v0.2.5），所有 main.py 改動無效。  
**修正**：`.app/Contents/MacOS/shellframe` 改為 shell script，source `~/.zprofile` + `~/.zshrc` 後執行 `.venv/bin/python main.py`。

### 7. Init prompt 自動注入

**設計**：
- `INIT_PROMPT.md` 集中管理知識，分兩段：基礎 ShellFrame context + TG bridge context
- `AI_CLI_TOOLS` 白名單（claude, codex, aider, cursor, copilot, goose, gemini）+ preset `inject_init` override
- `_should_inject_init()` 兩層判斷：preset override → 白名單 heuristic
- 注入時機：使用者第一次 Enter 且 CLI output 有 AI-ready 信號（`>` prompt、`Tip:`、`model:` 等）
- 未登入時 Enter 正常通過，`_init_pending` 保持 true，登入完成後下一次 Enter 才注入
- 注入方式：init prompt + `\n\n---\nUser's first message: ` + 使用者輸入，合併送出

### 8. `sfctl` 遠端控制

**新增檔案**：`sfctl.py`（symlink 到 `~/.local/bin/sfctl`）  
**機制**：file-based IPC（`/tmp/shellframe_cmd.json` → `/tmp/shellframe_result.json`）  
**指令**：`sfctl reload`（熱載入 bridge）、`sfctl status`（查狀態）  
**main.py**：`_start_command_watcher()` 背景 thread，0.5s 輪詢，30s 過期保護。

## 檔案清單

| 檔案 | 改動類型 |
|------|---------|
| `web/index.html` | 修改：paste capture phase、多檔案 attachments |
| `main.py` | 修改：output pusher pending buffer、init prompt injection、sfctl watcher、.Session._recent |
| `bridge_telegram.py` | 修改：`_download_tg_file`、`_handle_update` 支援 photo/doc、`load_init_prompt()` |
| `INIT_PROMPT.md` | 新增：集中管理 init 知識 |
| `sfctl.py` | 新增：遠端控制 CLI |
| `version.json` | 更新：0.2.8 → 0.3.0 |
| `CHANGELOG.md` | 更新：v0.3.0 release notes |
| `ShellFrame.app/Contents/MacOS/shellframe` | 改為 shell script launcher |
| `test_init_prompt.py` | 新增：27 case 測試 |
