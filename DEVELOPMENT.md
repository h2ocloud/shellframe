# ShellFrame 開發 / 發布注意事項

> 累積的 gotcha 與慣例，避免重踩。改動前先讀。
> 模組地圖、資料流、執行緒模型與「新程式碼放哪」的規則見 [`docs/architecture.md`](docs/architecture.md)。

## Reload vs Restart（哪種改動用哪個）

| 改到的檔 | 生效方式 |
|---|---|
| `bridge_telegram.py` / `filters.json` | `sfctl reload`（hot-reload） |
| `main.py` / `api_*.py` / `web/index.html` / `sfctl.py` / 新模組（如 `board.py`） | **`sfctl restart`（必須）** |

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
- **測試碰不到這台的設定檔。** `sf_config.config_dir()` 在測試裡（`run_tests.sh` 設
  `SHELLFRAME_TESTS=1`，或直接跑 `tests_*.py`）每個行程給一個暫存資料夾，子行程沿用；
  `run_tests.sh` 跑完整批刪掉。要讀寫 config.json 的程式一律走 `sf_config.config_file()`，
  不要自己拼 `~/.config/shellframe/config.json`（`tests_config_isolation.py` 會擋）。

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
- 暫解：在「系統設定 > 隱私權與安全性 > 檔案與資料夾 / 完全取硬碟取用權」手動把 ShellBot（舊安裝是 ShellFrame）加進去永久授權。

## macOS app 的名字：裝成 ShellBot.app（`app_bundle.py`）

- Finder、Launchpad、Dock、⌘-Tab 顯示的是 bundle 的**檔名**，所以安裝成 `ShellBot.app`；bundle 內容一個位元都不變（bundle id、cdhash 都不變），系統權限（輔助使用、麥克風、Downloads）跟著走。
- **repo 的樣板 `ShellFrame.app/` 不改名、內容也不要動**：每台 Mac 更新時都複製它再簽章，內容一變，裝上去的 cdhash 就變，macOS 會重新要權限。`tests_app_bundle.py` 守著樣板的指紋；真的要改，就是有意讓每台重給權限。
- **測試用的第二個實例不會改名**：`SHELLFRAME_TESTS`、`SHELLFRAME_CONFIG_DIR` 有設，或 HOME 不是這個帳號的家目錄，`app_bundle.at_start()` 就跳過（`/Applications` 對它也是同一個，改名或重啟 Dock 會動到正在用的那隻）。
- 找 App 一律問 `app_bundle.find()`（新名優先，舊名也認）。舊安裝的 `ShellFrame.app` 由新程式**啟動時**改名（`migrate`：`os.rename`、LaunchServices 換登記、Dock 釘選改指新路徑並重啟 Dock）；`sfctl restart` 在重開前也會先改名。舊版的 App 內重啟會開回舊名，所以那一次執行中的圖示要到下一次重啟才變。
- **回滾到改名前的版本**：舊程式只找 `ShellFrame.app`，找不到會退到樣板（能跑，但 Dock 身分不對）。先 `mv /Applications/ShellBot.app /Applications/ShellFrame.app` 再重啟；之後再升級會自動改回來。
- `open -a ShellFrame` 在改名後就找不到 App 了（舊路徑留 symlink 也一樣），請用 `open -a ShellBot`。
- Windows 只有桌面捷徑改成 `ShellBot.lnk`（新安裝由 install.ps1 建；舊安裝啟動時改名）。

## 程序無聲結束時，證據在哪裡（`sf_trail.py`）

都在 debug log 那個資料夾（macOS `/tmp`，Windows `%TEMP%`）：

- `shellframe_debug.log` 的 `[lifecycle]`：
  - **啟動那一行**：`exe=`、`console=`、`restarted_from=`。Windows 寫著 `console=yes` 的，代表關掉那個主控台、或在裡面按 Ctrl+C／Ctrl+Break，App 會馬上結束。
  - **主控台事件**、視窗的 closing／closed／shown、page loaded、WebView2 process failed；
  - **未處理的例外**：哪個執行緒、在哪一行；
  - **`exiting: …`**：每個 `os._exit` 前的一行。`os._exit` 會跳過 atexit，所以一定要先寫。
- `shellframe_fault.log`：faulthandler（原生當機時所有執行緒的 Python stack）。前兩次有內容的保留成 `.1`、`.2`；超過 8 MB 就關掉，因為 Windows 上它也會記程式自己接住的 .NET 例外。
- `shellframe_jsapi.log`：頁面呼叫 Python 的每一次進出，只記方法名和 tab id，不記內容。最後一行只有 `>`、沒有對應 `<` 的，就是消失時還在跑的那個呼叫。
- `~/.shellframe-crash.log`：`main()` 丟出例外時寫的。

**Windows 的 App 內重啟**是用 `shellframe.bat` 開新程序，跑的是 `python.exe`，會掛在一個新的主控台視窗上。那個視窗被關掉，App 就沒了，而且什麼都不會留下，只有啟動那一行的 `console=yes` 和主控台事件可以看出來。從桌面捷徑或 `shellframe.vbs` 啟動的是 `pythonw.exe`，沒有主控台。

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
