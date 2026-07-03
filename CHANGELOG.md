# Changelog

## v0.22.7 (2026-07-03)

### Fixes
- **Loops 排程面板改以 launchd 實際狀態為準，並排除常駐 daemon**：
  - **開關狀態不準的根因**：原本只查 launchctl 的 disabled override DB＋plist 的 `Disabled` key，**沒查該 job 有沒有真的 bootstrap 進 launchd**——plist 在磁碟上但沒載入（不會執行）照樣顯示綠燈。現在改用 `launchctl list` 的實際載入清單當 ground truth：有載入才亮綠，沒載入就是灰。
  - **排除非排程的常駐項**：`RunAtLoad`/`KeepAlive` 但沒有任何計時器（`StartInterval`/`StartCalendarInterval`）的 daemon（如 Telegram Channel、Telegram 終端 Bot——ShellFrame 本來就自己橋接/追蹤 TG）不是排程，不再列進面板佔位；規則是通用判斷、不是黑名單，未來新增 daemon 也自動排除。
  - **上次執行失敗看得到**：從 `launchctl list` 一併取回 last exit status，非 0 的排程在頻率旁標紅「上次失敗」（tooltip 附 exit code）。
  - **開關回報真實結果**：toggle 後重查 launchd 實際狀態再回報，bootstrap 失敗（plist 壞掉、路徑不存在）會顯示為仍然關閉，不再假成功。

## v0.22.6 (2026-06-30)

### Features
- **手動點燈號切換顏色**：分頁／側邊欄的狀態燈（busy-dot）現在可以**點一下循環切色**：綠（完成）→ 藍（工作中）→ 橘（卡住）→ 紅（需決策）→ 熄滅 →（再回綠）。用途：對話其實早就結束、燈號卻卡在藍燈，讓你誤以為還在跑——不想為了滅燈特地再丟一句話時，直接點燈號標成完成／熄滅即可。手動設定會一直保留，**直到該分頁下次有新輸出（下一輪對話）才自動清除、交回系統自動偵測接管**。燈點上加了透明擴大點擊區（燈本身只有 7px）與提示文字；點燈不會誤觸切換分頁／改名／拖曳。

## v0.22.5 (2026-06-30)

### Fixes
- **水位老是「查不到」的真因：OAuth 用量 API 被限流（HTTP 429），而且我們把 429 當成「沒登入」還一直重試，雪上加霜**。`api.anthropic.com/api/oauth/usage` 限流很兇，而膠囊、`/usage` 彈窗、事件驅動刷新三條路都各自打這支 API，加上「查不到就重試」會形成重試風暴，把自己卡在 429。修法（後端 `usage_probe`）：
  - **共用快取 + 退避**：45 秒內重用上次好讀數、完全不打 API（吸收多來源的連續查詢）；任一次嘗試後至少間隔 60 秒才會再打 API，重試風暴最多每分鐘一次 call，從根本上不再自我限流。
  - **查不到先拉上次的、只警示本次失敗**：限流／網路失敗時，回傳「上次的好讀數」並標記 `stale`，而不是回 no_data。膠囊照常顯示數字與**重置時間**（最常要看的就是這個），只是淡化並加 `⚠` 前綴、tooltip 註明「本次更新失敗，顯示上次資料」；`/usage` 彈窗同樣加這行警示。
  - **跨重啟保留**：好讀數寫到 `~/.config/shellframe/usage_cache.json`（保存 24 小時內），App 重開也能立刻顯示上次的水位與到期時間，不必等 API。
- 真正完全沒資料（從未成功抓過、又沒登入）才會顯示「查不到」。

## v0.22.4 (2026-06-30)

### Fixes
- **點開水位彈窗查到了，膠囊卻還停在「查不到」**：彈窗走 `tab_usage`、膠囊走 `tab_usage_brief`，是兩支不同 API；點開只更新了彈窗，膠囊沒跟著動。現在點開彈窗（＝你主動「發起更新」）成功取得用量後，會一併重新探測膠囊，把「查不到」換成實際數字。
- **「查不到」在同供應商分頁間切換會卡住不重試**：v0.22.3 的「同供應商不重查」對**好讀數**是對的，但若膠囊當下是「查不到／⚠」（多半是暫時性失敗），同供應商切換就一直不重試、卡著。改成快取記一個 `ok` 旗標：只有**真實讀數**才在同供應商切換時沿用；「查不到／⚠」會在每次切分頁時重試，直到補上數字。換供應商也只沿用「好的」快取，壞的一律重探。

## v0.22.3 (2026-06-30)

### Changes
- **切換同供應商的分頁不再重查水位，直接沿用畫面上的讀數**：膠囊一次只跟著一個供應商（claude 或 codex）。原本每切一次分頁都重打一次 probe，但 claude→claude 切來切去數字根本一樣，白查。改成：切到的新分頁若對應的供應商跟膠囊現在顯示的相同 → 什麼都不做；只有 claude↔codex 真的換了供應商才動作，且優先沿用該供應商 2 分鐘內的快取讀數，沒有新鮮快取才真的去 probe。等於只有換供應商、且讀數過期時才會 call，來回切不會一直重打。供應商判定對齊後端（codex 分頁→codex，其餘含 claude／非 AI 分頁→claude）。Howard 2026-06-30 提。

## v0.22.2 (2026-06-30)

### Changes
- **水位膠囊改成事件驅動更新，不再固定每 5 分鐘輪詢**：水位只有在「跑了一個回合」之後才會變動，固定計時器多半是重撈同一個數字。改成跟著當前分頁的對話活動走：(1) 你**下了新 prompt／回合開始**（分頁由閒置轉忙）→ 立刻刷新（15 秒內不重複，避免一來一回狂打）；(2) 回合**結束**（由忙轉閒）→ 等 4 秒沉澱再刷新，短回合連發只會合併成一次、且抓得到回合後的最新數字；(3) **都沒動靜** → 每 15 分鐘輪詢一次當 fallback（每次刷新都重置這個倒數，只在真的安靜一段時間後才會跑）。切換分頁仍即時刷新。等於有事才查、沒事 15 分鐘看一次。Howard 2026-06-30 提。

## v0.22.1 (2026-06-30)

### Fixes
- **水位膠囊查不到時不再整顆消失**：v0.22.0 的右上角 AI 用量水位膠囊，只要 fetch 不到水位（沒登入供應商、抓不到資料、後端例外）就直接 `display:none` 把整顆膠囊藏掉——看起來像功能壞了，膠囊在頂列的位置也跟著消失。改為保留膠囊、改顯示灰字佔位狀態：(1) 抓到供應商但沒水位（多半沒登入）→ `用量 查不到`，tooltip 提示「請確認已登入 <claude/codex>」；(2) 後端 fetch 例外 → `用量 ⚠`，tooltip 帶錯誤訊息；(3) IPC/JS 例外 → 有上一次讀數就保留並淡化（沿用舊行為），沒有才顯示 `用量 ⚠`。三種狀態都維持原位置、仍可點擊開完整水位彈窗重試。Howard 2026-06-30 回報。

## v0.22.0 (2026-06-29)

### Features
- **右上角常駐 AI 用量水位**：頂列新增一顆水位膠囊，直接顯示關鍵數字 `5h <已用%> · wk <已用%>`（5 小時視窗＋每週視窗的**已用**百分比，與 `/usage` 彈窗口徑一致），不必再為了看水位一直點開彈窗。每 5 分鐘自動 fetch 一次、切換分頁時即時更新；數字依已用量上色（綠 <60%／橘 60–85%／紅 ≥85%），滑鼠移上去的 tooltip 顯示帳號、各視窗已用％與重置時間。**點膠囊**＝開原本的完整水位彈窗看細節。指示器跟著當前分頁的供應商走（claude／codex），非 AI 分頁則回退顯示 Claude（帳號層級、與分頁無關）。fetch 失敗會保留上一次讀數並淡化，不清空。後端新增 `usage_probe.probe_data()`（結構化版的 `probe()`）與 RPC `tab_usage_brief(sid)`。同時移除原本的 📊 工具列鈕（功能與膠囊重複——點膠囊即可開同一個彈窗）。

### Changes
- **移除「任務看板 / 交換區（實驗性）」設定開關**：自 v0.21.0 Loops 排程面板上線後，右側面板已整個改由 Loops 接管，舊任務看板的渲染（`renderBoard`）早已無處呼叫——這個開關打開也不會有任何 UI 出現。移除該開關與其死碼（`renderBoard`、相關 CSS / i18n / 事件綁定）。後端的 `[[SF:TASK:...]]` marker 處理、`board.py`、`board_*` RPC 保留不動（目前無 UI 入口，僅供日後沿用）。

## v0.21.1 (2026-06-29)

### Changes
- **Loops 面板「編輯」改成把排程資訊帶進當下對話**：原本每點一次「編輯」會新開一個 `編輯:<排程>` 分頁（reuse 沒生效會狂開重複分頁、又沒切過去，看起來像沒反應）。改為把該排程的 plist 路徑、執行腳本、完整指令、頻率收成一段 prompt，直接貼進**目前 active 分頁**的輸入框（不開新分頁、不自動送出），使用者補上要改什麼再送，就在當下對話裡請 AI 調整。後端 `schedule_edit`（建分頁）改為 `schedule_prompt(label, sid)`（注入指定 session）。

## v0.21.0 (2026-06-29)

### Features
- **Loops 排程面板（實驗性，預設關閉）**：右側面板從「Agents 即時動態」改版成排程管理，收納「有排程的對話」。(1) 你自己的 LaunchAgent（scrum 早晚排卡、plaud、femas 打卡、tech-digest、tmux-groom、telegram 接線…）逐條顯示**編號、頻率、執行指令、開關**——開關直接 `launchctl enable/disable`（只允許 `com.howard.`／`com.neux.`／`com.claude.`／`com.h2ocloud.` 前綴、檔案存在的 agent）；每條一個「編輯」鈕，點了會開（或聚焦）一個 AI 分頁、把該排程的 plist + 腳本路徑當開場 prompt 帶進去，方便直接請 AI 調整，分頁用的 CLI 跟著你最近在用的（claude／codex）走、不寫死。(2) in-session `/loop`：讀 transcript 的 `ScheduleWakeup` 標記，顯示下次喚醒倒數、在等什麼、已跑幾輪；loop 停掉自動消失。後端走 `agent_status.detect_schedules` + `Api.schedules_list/schedule_set_enabled/schedule_edit`。Settings → General 的「Loops 排程對話面板」控制，預設關閉。
- **語音輸入 AI 整理（Typeless 風格）**：透過 Telegram 發語音時，過去是 whisper 逐字稿直接送進 session——口語贅字（嗯／那個／就是／這樣子）、重複、同音辨識錯字、沒標點全部原樣帶入。現在 STT 之後、送進 session 之前，多一層本機 LLM 整理：把逐字稿改寫成「使用者真正要講的通順文字」，修錯字、去贅字、補標點分段，**完整保留原意與具體資訊，不摘要、不回答、不加料**。TG 回顯改為顯示整理後版本，內容若有更動會附上 `🎙 原稿：` 縮略對照。
  - 引擎預設打本機 LM Studio／Ollama 的 OpenAI 相容端點 `http://127.0.0.1:1234/v1/chat/completions`，自動挑一個非 embedding 的 chat model（實測 `gpt-oss-20b`，warm ~3s）——零成本、留在本機。端點不可達或逾時就**原樣 fallback 逐字稿**，整理掛掉不會吞訊息。
  - `~/.config/shellframe/config.json` 的 `settings` 可調：`voice_refine`（預設 true，設 false 關閉回到純逐字稿）、`voice_refine_url`、`voice_refine_model`（留空＝自動挑）、`voice_refine_style`（`clean` 保守整理＝預設／`summary` 重組成一句重點＋條列）。

## v0.20.3 (2026-06-28)

### Fixes
- **TG 收到 Codex 回覆被重複多次、混入「›Explain this codebase」**：當回覆比終端 viewport 長時，Codex/Claude 的 TUI 在串流中會捲動並重繪——把同一塊內容以重疊視窗一再吐進線性化的 PTY 流，於是 `[[TG_REPLY]]` 起訖標記之間夾了好幾幀重複行，原本只去重「相鄰重複行」的清理擋不掉非相鄰重複，整段被 `split_for_telegram` 切成多則超長重複訊息送出。修正三處：(1) `_marker_spans` 改為「每個 end 配對最近的 start」（tightest pairing），避免重繪插入的新 start 讓首個 start→遠端 end 貪婪吃進中間整段殘影；(2) `clean_mobile_marker_response` 改為全域行去重（保留首次出現）並清掉殘留的 `[[TG_REPLY_xxx]]` token，把捲動重繪壓回唯一行；(3) `filters.json` echo_keywords 補上 Codex 空輸入框預設提示 `explain this codebase`，連同既有 `summarize recent commits`／`switch models or reasoning` 一併在 strip 階段濾掉，標記內也不再殘留 composer footer。標記存在時 Telegram 仍只送「標記內最後一個完整 block」，絕不 fallback 整個終端畫面。Howard 2026-06-28 回報。

### Fixes
- **Idle-reaper 交接訊息卡在輸入框沒送出**：本機模式（TG bridge 未 active）下，`_write_lifecycle_handoff` 用 naive `target.write(compact + "\r")` 直接寫進總控 PTY，在 Claude/Codex TUI（總控 mid-turn 或輸入行有殘留）下那個 `\r` 常被忽略 → 交接文字累在輸入框、沒提交成一輪。改用既有可靠提交路徑 `_send_text_to_session(target, compact, submit=True)`（tmux bracketed-paste 一次成型 + 貼上完成才送分離的 Enter）。Howard 2026-06-27 實際踩到（idle_reaper 關閉 s75 後交接卡住）。

## v0.20.1 (2026-06-25)

### Fixes
- `/usage` (`/水位`) and the 📊 button now resolve **Codex** water-level too. Same root cause as the Claude fix: the codex path imported `~/.openclaw/.../codex_usage`, which only exists on the openclaw host, so off-host the codex probe always returned「查不到資料」. Now reads codex's latest `codex.rate_limits` snapshot directly from its local SQLite log (`~/.codex/logs_*.sqlite`) — no app-server spawn, no billable call, no openclaw dependency; the openclaw module remains a fallback. Adds a「快照 MM-DD HH:MM」line so a stale snapshot (codex hasn't run recently) is obvious rather than showing a reset time in the past.

## v0.20.0 (2026-06-25)

### Features
- Toolbar 📊 button — one click shows the active tab's AI usage water-level in the same overlay as `/usage` (`/水位`), no typing. Works for any claude/codex tab; unlike the typed command it does not clear the tab's input composer. Lives in the tab bar next to the Telegram toggle.

## v0.19.5 (2026-06-25)

### Fixes
- `/usage` (`/水位`) now fetches Claude water-level by calling the OAuth usage API directly with the local Keychain token, instead of shelling out to `~/.openclaw/.../fetch_oauth_usage.sh`. That script only exists on the openclaw host, so on every other machine the Claude probe always returned「查不到資料」 even though the account/plan resolved. Parses `five_hour`/`seven_day` `utilization` + `resets_at`; the openclaw script is kept only as a legacy fallback. Codex path unchanged.

## v0.19.4 (2026-06-25)

### Changes
- Slimmed the injected session init prompt (`INIT_PROMPT.md`) by ~49% (11.2KB → 5.8KB) — removed redundant prose and the built-in-agent-vs-sfctl comparison table while keeping every behavioral rule (grounding, sfctl commands, master/worker contract, `[[SF:RED]]`/`[[SF:YELLOW]]` hints, Telegram rules). A shorter, denser context keeps the model sharper; the `## Telegram Bridge` split marker and the closing acknowledge line are preserved so bridge-off stripping still works.

## v0.19.3 (2026-06-23)

### Fixes
- Telegram replies now strip residual terminal control fragments such as `[0 q` before sending marked mobile replies.
- Telegram bridge now prunes slots that main.py reports as dead or bridge-disabled, so ghost sessions disappear from `/list` and routing.
- ShellFrame only registers alive sessions when starting or hot-reloading the Telegram bridge.
- Web terminal IME input no longer drops committed text solely because WKWebView still reports composition in progress.
- `sfctl list` now uses ASCII status markers so Windows cp950 consoles do not crash on emoji output.

## v0.19.2 (2026-06-23)

### Fixes
- Telegram-to-Codex sends now log the submit keystroke and retry with an LF fallback if Codex still shows the pasted-content chip after CR, preventing TG messages from getting pasted into the input box without being submitted.

## v0.19.1 (2026-06-23)

### Fixes
- Windows clipboard image paste now falls back to the native system clipboard through STA PowerShell/WinForms when WebView2 cannot expose an image blob.

---

更早的版本（v0.19.x 以前，共 190+ 版）在 [CHANGELOG-archive.md](CHANGELOG-archive.md)。
