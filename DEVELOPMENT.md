# ShellFrame 開發 / 發布注意事項

> 累積的 gotcha 與慣例，避免重踩。改動前先讀。

## Reload vs Restart（哪種改動用哪個）

| 改到的檔 | 生效方式 |
|---|---|
| `bridge_telegram.py` / `filters.json` | `sfctl reload`（hot-reload） |
| `main.py` / `web/index.html` / `sfctl.py` / 新模組（如 `board.py`） | **`sfctl restart`（必須）** |

- `sfctl reload` → `hot_reload_bridge()`：`importlib.reload(bridge_telegram)` + **重建 `TelegramBridge` instance**，保留 PTY sessions、polling offset、per-slot 狀態（`sent_texts` / `sent_responses` / pending_menu）。
- ⚠️ 改了 `main.py` / `web` / 新 import 的模組卻只 `reload`，**不會生效**——一定要 `restart`。`restart` 時 tmux sessions 會留著。
- 排查「改了沒生效」時，先確認用對 reload/restart。

## 跑測試

- `./run_tests.sh` — 一次跑完 `tests_*.py` 與 `tests_*.js`。
- ⚠️ 前端純邏輯的測試是 **`.js`**（例：`tests_ime_dedup.js`，用 node 跑，函式直接
  從 `web/index.html` 抽出來，不另外複製一份會走樣的副本）。只用 `tests_*.py`
  的 glob 掃會漏掉它們——所以一律走 `run_tests.sh`。
- `tests_ime_seq.py` 需要 playwright（載真實 xterm.js 驗 IME 事件序列）。`.venv`
  裡沒裝，它會自己轉手給系統 `python3`；兩邊都沒有才 SKIP。

## 版本與發布

- 任何 **user-visible** 變更 → bump `version.json` + 寫 `CHANGELOG.md`。
  **格式與語言規範見 [`docs/changelog-guide.md`](docs/changelog-guide.md)**（英文先、中文後；不寫人名、不貼原始對話；症狀／根因／修法／驗證四件事）。
  由 `tests_changelog_format.py` 強制檢查，`./run_tests.sh` 會跑——格式不符是紅燈。version 用 semver；新功能進 minor（如 0.13.x → 0.14.0）。
- **多台同步**：維護者多台機器都會在線推版。改動前先 `git fetch` / 確認 `origin/main`，**別自己 bump 撞到別台的版號**；改完就 `commit` + `push origin main`（SF 慣例直推 main、靠推版同步多台）。
- WIP 不要混進無關 commit：working tree 可能同時有別的開發中改動，commit 時只 `git add` 自己要發的檔，別 `git add .` 把半成品一起推。

## macOS codesign / TCC 權限（反覆跳權限彈窗的根因）

- app 目前是 **ad-hoc 簽名**（`codesign -dv` 顯示 `Signature=adhoc`、`TeamIdentifier=not set`）。
- 後果：macOS TCC **記不住「檔案與資料夾」授權**，每次（尤其 `restart` 後）把它當新 app、反覆跳「想取用 XXX 資料夾」彈窗。頻繁 restart 會加劇。
- 根治：用 **Apple Developer ID 正式 codesign**（需開發者憑證）→ TCC 才能穩定記住。
- 暫解：在「系統設定 > 隱私權與安全性 > 檔案與資料夾 / 完全取硬碟取用權」手動把 ShellFrame 加進去永久授權。

## TG Bridge — reply 遺漏（v0.13.9 修）

- reply 靠 PTY 螢幕 scrape + marker 配對（`[[TG_REPLY_xxx]]` … `[[/TG_REPLY_xxx]]`）。
- **tail guard**（`_extract_marked_mobile_reply`）：end marker 閉合後，**只在 tail 還含另一組 start marker 時才放棄抓取**；後續 tool 輸出 / 操作 / 雜訊不再擋住 reply。
  - 舊版（< 0.13.9）：marker 後只要有任何非雜訊內容就 `return ""`、要等 30s force-extract，turn 太快結束就**遺漏回覆**。常見觸發：reply 後在同一 turn 緊接 Bash/Read 等 tool（含 background 啟動回顯）。
- **行為慣例**：reply 盡量放在 turn 最後；少在 reply 之後同 turn 接大量 tool 回顯。

## Prompt 注入順序

- `INIT_PROMPT` / `master_turn_preamble` 必須排在第一則 user 內容**之前**。
- web 路徑（`main.py.write_input`）：xterm.js 把訊息文字與送出的 Enter 拆成不同 `write_input` 呼叫；注入要在**第一個帶內容的 chunk**（`_is_user_content()`）觸發、前置 prompt，而非等尾端 bare `\r`（v0.13.8 修）。
- TG / delegate 路徑（`bridge_telegram` consume/concat、`_send_text_to_session` tmux paste-buffer）本就 prompt 在前。
- TG→master 貼文字污染（變 preamble / malformed）：靠 write 序列化 + bracketed paste + busy 守門解（v0.13.5–0.13.7）。

## 手機 App（`ios/`）與 relay

- 手機／iPad／Watch App 在 `ios/`：`xcodegen generate` 產專案（`brew install xcodegen`），
  SwiftTerm 走 SPM（第一次要 `xcodebuild -downloadComponent MetalToolchain`），
  xcodebuild 加 `-skipPackagePluginValidation -skipMacroValidation`。設計與協定見
  [`docs/mobile-link.md`](docs/mobile-link.md)。
- **模擬器 build 不能加 `CODE_SIGNING_ALLOWED=NO`**：沒簽章就沒 keychain entitlement，
  Keychain 回 -34018，配對金鑰存不進去（現在有檔案 fallback，但別依賴它）。
- 配對／串流／resize 的 UI 驗收：`ShellFrameMobileUITests`（XCUITest，會真的打字進
  配對的電腦）。跑法：
  `TEST_RUNNER_SF_QA_SID=<sid> TEST_RUNNER_SF_QA_OUT=<dir> xcodebuild test -scheme ShellFrameMobile -destination 'platform=iOS Simulator,name=iPhone 16 Pro' …`
  模擬器沒相機，配對用 QA 掛鉤 `SIMCTL_CHILD_SF_QA_PAIR_URL=<pair_url> xcrun simctl launch …`
  （`simctl openurl` 會跳系統的「要在 ShellFrame 打開嗎？」點不掉）。
- `frame_link.py` / `link_relay.py` / `relay_server.py` 改動：`sfctl restart`（不是 reload）。
  回歸：`tests_frame_link.py`、`tests_relay.py`（同 process 起 relay + 兩個 instance）。
- relay 是「電腦出站長輪詢」，跟 TG bridge 一樣不用開 port；relay 看得到明文，自架＋TLS。

## 持久化 / state

- json 持久化仿 `_persist`（main.py ~1157）+ soft-session 機制；state 放 `~/.local/state/shellframe/`。
- agent signal marker `[[SF:WORKING|GREEN|RED|YELLOW]]` 由 `_SIGNAL_RE` 偵測（line-anchored，wrapper 內文提到不會誤觸）；新增 harness marker（如看板 `[[BOARD:...]]`）照這個 pattern 寫。
