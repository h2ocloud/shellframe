# ShellFrame v0.2.8 開發交接

> 最後更新：2026-04-06，commit `b61c94f`

## 本次改動總覽

### 1. TG Bridge 回應擷取修復（核心 bug）

**問題**：TG 收不到 Claude 的回應。  
**根因**：`pyte.Screen(200, 50)` 只保留最後 50 行，Claude 的長回應（tool calls + 多個 `⏺` block）超過後早期內容直接消失。

**修了什麼**（`bridge_telegram.py`）：
- `SessionSlot.__init__`：`pyte.Screen` → `pyte.HistoryScreen(200, 50, history=10000)`
- `_extract_new_text()`：現在從 `screen.history.top`（scrollback）+ `screen.display` 一起讀
  - 用 `_history_offset` 追蹤已處理的 history 行，避免重複處理
  - history 的每行是 `StaticDefaultDict`，取文字用 `hist_line[col].data for col in range(cols)`
- flush timeout：15s → 60s（Claude 跑 1-2 分鐘很常見）
- `filters.json`：從 `spinner_chars` 移除 `⏺`（它是 AI 回應標記，不是 spinner）

**已驗證**：用模擬測試確認 55+ 行 filler 後的 `⏺` block 在舊 Screen 下消失（False），HistoryScreen 正確保留（True）。

### 2. 熱載入（Hot Reload）

**`bridge_telegram.py`**：
- `TelegramBridge.__init__` 新增 `on_reload` callback 參數
- `/reload` TG 指令 → 呼叫 callback → 在背景線程執行
- 已註冊到 bot commands menu

**`main.py`**：
- `hot_reload_bridge()`：用 `importlib.reload(bridge_telegram)` 重載模組
  - 保存舊 config → stop bridge → reload module → 用新 class 重建 bridge → re-register 所有 session → start
  - PTY session 完全不受影響
- `start_bridge()` 和 `hot_reload_bridge()` 都傳入 `on_reload=self.hot_reload_bridge`

**注意**：`bridge_base.py` 是被 bridge_telegram import 的，如果改了 base 也需要 reload。目前只 reload `bridge_telegram`。

### 3. 啟動更新檢查

**`web/index.html`**：
- `startupUpdateCheck()` 加了 `if (!autoUpdateEnabled) return false;`
  - 在 `loadConfig()` 之後才執行，所以 `autoUpdateEnabled` 已經從 config 讀入
- changelog 改用 `renderChangelog()` 做簡易 markdown → HTML（`###` 標題、`- **粗體**` bullet）
- `showReleaseNotes()` 也改用 HTML 渲染，且會 match 對應版本的 section

### 4. Finder 檔案貼上

**`main.py`**：
- `get_clipboard_files()`：macOS 用 `osascript` 讀取 `«class furl»`（Finder 複製的檔案路徑）
  - Windows 用 PowerShell `Get-Clipboard -Format FileDropList`
  - 回傳 JSON array of paths，有驗證 `os.path.exists()`
- `save_file_from_clipboard()`：存非圖片的 clipboard blob 到 `~/.claude/tmp/`

**`web/index.html` paste handler**：
- 優先級：①圖片 blob → ②非圖片 file blob → ③系統剪貼簿檔案（Finder copy）
- 多檔時 `attachPath` 用空格串接所有路徑，顯示 `[N files]`
- 按 Enter 送出路徑到 PTY（跟既有圖片行為一致）

**限制**：瀏覽器 Clipboard API 拿不到 Finder 複製的檔案路徑，必須走 Python → osascript。已測試可正常偵測。

### 5. 全域資源管控

- `~/.claude/CLAUDE.md`：新建，寫入 8GB RAM 機器的資源管控規範
- `~/.claude/skills/medium-publish/SKILL.md`、`notebooklm/SKILL.md`：加了資源管控提醒
- Memory：`feedback_resource_management.md`

---

## 尚未測試 / 已知需要注意的

| 項目 | 狀態 | 說明 |
|------|------|------|
| HistoryScreen 實際 TG 測試 | 未測 | 模擬測試通過，但需要實際開 ShellFrame + TG bridge 驗證 |
| `/reload` 指令 | 未測 | 邏輯寫好了，需要實際從 TG 發 `/reload` 測試 |
| Finder 貼上 | 部分測試 | `get_clipboard_files()` 的 osascript 已驗證，前端整合未測 |
| 多檔貼上 | 未測 | 空格串接路徑可能在含空格的路徑出問題，考慮改用其他分隔 |
| Windows 相容 | 未測 | `get_clipboard_files()` 的 PowerShell 路徑未驗證 |
| `renderChangelog()` | 未測 | 簡易 regex 轉換，複雜 markdown 可能跑版 |

## 檔案清單

```
bridge_telegram.py  — HistoryScreen + /reload + flush timeout
main.py             — hot_reload_bridge + get_clipboard_files + save_file_from_clipboard
web/index.html      — startup check + renderChangelog + paste handler
filters.json        — 移除 ⏺ from spinner_chars
version.json        — 0.2.7 → 0.2.8
CHANGELOG.md        — v0.2.8 release notes（中英雙語）
```

## 快速開發指引

```bash
# 開發模式（有 DevTools）
cd ~/.local/apps/shellframe
.venv/bin/python3 main.py --debug

# 測試 bridge module 載入
.venv/bin/python3 -c "import bridge_telegram; print('OK')"

# 測試 HistoryScreen extraction
.venv/bin/python3 -c "
from bridge_telegram import *
slot = SessionSlot('t', 'test', lambda x: None, 1)
slot.has_user_msg = True
slot.stream.feed('⏺ Hello world\r\n❯ \r\n')
bridge = TelegramBridge('t', TelegramBridgeConfig(bot_token='x'))
bridge.slots = {'t': slot}
bridge._slot_order = ['t']
print(bridge._extract_new_text(slot))
"
```
