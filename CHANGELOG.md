# Changelog

## v0.16.3 (2026-06-12)

### Performance
- **10 tab 掛機不再發燙** — 量測：10 tab 全閒置時主程序常駐 ~34% CPU。三個火源、三個修法：
  - **狀態偵測 idle gating（最大宗）**：原本每 0.6s 對「每個」tab fork 一次 `tmux capture-pane`＋讀 256KB transcript 尾巴再 JSON parse（10 tab ≈ 每秒 17 個 subprocess + ~4MB/s 磁碟讀，全閒置照燒）。改為：tab 自上次計算後 PTY 沒有任何輸出就直接沿用快取、只更新 elapsed（工作中的 tab 一定會持續輸出 spinner/計時幀，所以無輸出＝狀態不可能變）；保底每 15s 全量重算一次，pending-tool age／debounce 這類靠牆鐘的轉換最多延遲 15s。閒置 tab 的偵測成本歸零，feed 即時性不變（有輸出立刻重算）。
  - **狀態推送去重**：偵測結果除了 elapsed 以外沒變就不 `evaluate_js`（原本每 0.6s 無條件喚醒 WebView），elapsed 跳動改 5s 心跳推一次。
  - **Pusher 閒置喚醒降頻**：output pusher 是事件驅動（reader 一有資料就 set event），閒置時的 wait 只是保險絲——從 0.015s 放寬到 0.5s（66 → 2 次/秒，每次醒來都要掃全部 session 的 lock）；串流中維持 5ms 排乾、背景節流窗維持 0.1s，輸出延遲無感。
  - **右側 feed 跳過無效重繪**：`renderFeed` 每 700ms 無條件重建 innerHTML，改為 markup 沒變就跳過（閒置時的常態）。
- 行為驗證：idle 不重算/單 tab 輸出只重算該 tab/心跳推送/elapsed 連續性 7 案、feed 重繪 3 案、既有 27+7 案全 PASS。
- 需 restart 生效（改到 main.py / web/index.html）。

## v0.16.2 (2026-06-11)

### Fixes
- **Agent 即時動態：Codex 支援補強，idle prompt 不再被當成工作中** — 右側 AGENTS feed 會把 Codex 畫面底部的 `› Improve documentation in @filename` / `gpt-* · cwd` idle input box 誤看成還在作動，或拿不到 Codex 的任務/敘述細節。本版把 Codex screen-only 判斷補齊：只要沒有 `Working (...)` / `esc to interrupt`，Codex input prompt 會判為 done/idle，不進右側 feed；有 `Working (... esc to interrupt)` 時仍優先判 working。
- **Codex rollout parser 補齊新版事件** — `agent_status.py` 現在會解析 Codex `user_message` 文字作為 task、`agent_message` 作為 narration、`custom_tool_call` / `tool_search_call` 作為 tool action，並吃 top-level timestamp，避免只靠檔案 mtime 造成 elapsed/state 不準。右側 feed 對 Codex 會顯示「正在跑哪個 tool / 處理哪個使用者任務」，而不是空白或舊 tool。
- **回歸測試** — 新增 `tests_agent_status.py`，覆蓋 Claude prompt idle、Codex prompt idle、Codex working override、Codex user/task/narration/action detail。已通過 `tests_agent_status.py` 與 `tests_tg_reply.py`。
- 需 restart 生效（改到 `agent_status.py`）。

## v0.16.1 (2026-06-11)

### Features
- **本機 HTTP API（選擇性開啟）** — 讓同機的外部 agent（例如 OpenClaw／龍蝦）透過 HTTP/JSON 驅動 ShellFrame，薄封裝既有的 `sfctl` 命令層（`_execute_sfctl`），能力與總控／TG bridge 相同。
  - **預設關閉**，需在 `~/.config/shellframe/config.json` 的 `api_server` 區塊設 `enabled:true` 再 `sfctl restart`。
  - **安全**：綁 loopback（預設 `127.0.0.1`）、IP 白名單（預設 `["127.0.0.1","::1"]`，支援 CIDR）、每個端點需 Bearer token（`Authorization` 或 `X-API-Token`）。token 空白時首次啟用自動生成並寫回 config；啟用但無 token = fail-closed（全部 401）。
  - **端點**：`GET /sessions`、`POST /sessions`、`DELETE /sessions/{sid}`、`GET /sessions/{sid}/peek`、`POST /sessions/{sid}/send`、`POST /sessions/{sid}/rename`、`GET /status`、`GET /roster`、`POST /delegate`、`GET /events`。
  - **雙向**：tab 觸發 `[[SF:RED]]`／`[[SF:YELLOW:reason]]` 信號時推入事件佇列，client 輪詢 `GET /events?since=<cursor>` 取得、決策後用 `POST /sessions/{sid}/send` 回填。事件為記憶體環狀緩衝（最後 500 筆，重啟清空）。
  - **文件**：Swagger UI `GET /docs`、OpenAPI 3.0 `GET /openapi.json`；串接說明見 `docs/local-http-api.md`。
  - 需 restart 生效。

## v0.16.0 (2026-06-11)

### Features
- **`#` tab tagging：派工時標註要互動的 tab** — 在任何 tab 輸入 `#` 跳出 tab 選單（↑↓ 選擇、Enter/Tab 插入、Esc 取消；可打字過濾，支援中文 label、含空白 label 與 sid），選定後自動補完 `#<tab名稱>`。選單錨在游標位置（xterm IME helper textarea），選擇時自動退格清掉已打的過濾字再插入完整 label。
  - **派工自動接線**：`sfctl delegate` 的 task 內含 `#<tab-label>`（或 `#<sid>`）時，自動解析成現存 tab，並在 worker 的派工 prompt 附上明確互動指示——先 `sfctl peek <sid>` 看狀態、用 `sfctl send <sid>` 對話／交接、回報必須包含互動結果——以及 label→sid 對照。`delegate` 回傳 details 多了 `tagged_tabs`。
  - **總控 preamble**（main.py fallback 與 bridge_telegram 兩份）教總控：使用者訊息帶 #tag 時，派工 task 要原樣保留 tag，由 delegate 自動展開；自己處理就直接用 sfctl 跟該 tab 互動。web 貼上路徑的 master turn 另外即時附上 tag→sid 對照。
  - 解析規則：長 label 優先＋已匹配區段遮罩（`#研究-CLD` 不會誤觸到 `#研究`）、大小寫不敏感；派工對象自己被 tag 時不自我標註、也不會洩漏給前綴 label。

### Fixes
- **快捷鍵平台對應（Cmd ↔ Win Ctrl）＋ Ctrl+C 不再害死 session** — 同仁在 Windows 按 Ctrl+C 結果「分頁被關掉」（實際是 interrupt/誤觸把 CLI 弄死或 Ctrl+W 秒關）。整組重整：
  - **macOS**：app 快捷鍵只認 Cmd（Cmd+T/W/,）；以前 `metaKey || ctrlKey` 連 bare Ctrl+T/W 都被劫走，現在還給終端機（Ctrl+W=刪字、Ctrl+T=transpose）。
  - **Windows**：Cmd 完整對應到 Ctrl——Ctrl+T 開分頁、Ctrl+W 關分頁、Ctrl+, 設定；另收 Ctrl+Shift+T/W（Windows Terminal 慣例）與 Ctrl+PgDn/PgUp 切分頁。
  - **Ctrl+C 永遠不是 app 快捷鍵**：Windows 上有反白→複製反白文字（不送 `\x03`，學 Windows Terminal）；沒反白→照常送中斷給終端機。macOS 複製走原生 Cmd+C，Ctrl+C 不動。
  - **鍵盤關分頁一律先確認**（Enter 確認／Esc 取消的小 modal）——關分頁=殺 session，誤觸終端 chord 不可以直接毀掉跑到一半的 agent；tab 上的 × 按鈕維持單擊即關。
  - README／app 內 About 快捷鍵說明同步改為雙平台對照。
- 需 restart 生效（改到 main.py / web/index.html）。

## v0.15.4 (2026-06-10)

### Fixes
- **Agent 狀態：新開的 tab 不再「當沒看到」** — 新 tab 正在跑卻不出現在右側即時動態。根因：`status_for` 在 transcript 解析不到時直接回 `unknown`（feed 只顯示 working/decision/stuck，unknown 被濾掉），而 Claude 的 `<uuid>.jsonl` 要等寫入第一筆才存在 → 新 tab 空窗期即使畫面在跑也被當沒看到。
  - 修法：transcript 不存在／讀取失敗時，改用**純畫面判斷**（`compute_state([])`）而非直接 unknown。畫面訊號（`esc to interrupt` / 決策選單 / 輸入提示 / spinner）抽成 `_screen_signals()`，在無 transcript 事件時也能判斷 → 新 tab 一有動作就即時偵測、立刻進 feed。
  - 一併讓 `esc to interrupt`、決策、閒置提示等畫面訊號在「有/無 transcript」兩條路徑行為一致。
  - 已加新 tab（working/idle/decision/blank/spinner）＋既有 event-based 共 11 案驗證，含新 tab 無 transcript 檔的 `status_for` 整合測試。
- 需 restart 生效（改到 agent_status.py）。

## v0.15.3 (2026-06-10)

### Fixes
- **Agent 狀態：正在思考的 tab 不再被誤判「等決策」** — ToolHub tab 正在 `Spinning… thinking with xhigh effort (2m · esc to interrupt)`（真的在跑），卻顯示「等決策」。根因：`compute_state` 的 `decision_req`（transcript 最後一筆）判斷排在 spinner 之前且無守門；extended thinking 期間 transcript 還沒寫入批准後的新事件，最後一筆停在舊的 `decision_req` → 蓋過畫面實況。
  - 修法：把 **`esc to interrupt`** 提為最優先判斷——這是 Claude Code「正在跑」的唯一鐵證（工具執行／串流／思考時才顯示；決策提示只會顯示 `esc to cancel`，絕不顯示 interrupt）。畫面有它就一律 working，蓋過 transcript 的 stale `decision_req` 或未 flush 的 thinking。
  - decision 分支維持原判斷（含 `↑` 等寬鬆 spinner 字元的真實決策選單不受影響，已加回歸測試）。
- 需 restart 生效（改到 agent_status.py）。

## v0.15.2 (2026-06-10)

### Fixes
- **Agent 狀態：閒置 tab 不再被誤判「工作中」** — 多個早已收工的 tab（右側即時動態顯示「Running … 102m」）仍標工作中。抓 live 畫面 + transcript 診斷出兩個漏洞：
  - **閒置提示偵測太窄**：`compute_state` 只認「單獨一個 `❯`」當閒置，但使用者打了草稿（`❯ 回收此 tab`）或畫面停在評分提示／自訂選單時就配不到 → 往下掉到 `pending_tool → working`。改為：**畫面底部 input 區行首是 `❯`／`›`（不論後面有無草稿）且無 `esc to interrupt` → 必定閒置**（Claude Code 只在等輸入時才顯示可編輯提示，真的在跑時是 spinner）；另把「How is Claude doing this session?」評分提示也認成 done。只掃最後 8 行避免顯示內容裡的 `❯` 誤觸。
  - **pending tool 無 age 守門**：transcript 最後是 tool_call 而對應 tool_result 沒被解析到時，會永遠「working」。改為僅在「有 spinner 或 age < 180s」時才算 tool running；超過卡住閾值又無 spinner 的 pending tool 視為 done（tool 早已結束、result 只是不在解析窗內），不再永遠工作中、也不誤判 stuck。此 age 守門與 POC（已對真實 log 驗證）原設計一致，shipped 版漏掉，現補回。
  - 已用真實閒置畫面（s33 評分提示+草稿、s35 自訂選單+草稿）＋ working/長指令/fresh/無畫面 等 7 案驗證。
- 需 restart 生效（改到 agent_status.py）。

## v0.15.1 (2026-06-10)

### Fixes
- **上滾歷史：收掉「resize 重繪幀」造成的重複（v0.15.0 的真正根因）** — 診斷 live session 的 `history_audit` 發現：視窗 resize（開關側 panel／字級變動／拖拉佔比）會讓 Claude 以新寬度重繪串流中的內容，**tmux scrollback 把每個寬度的副本都錄下來**。這些副本去掉空白後字元流相同、但斷行點不同，所以 v0.15.0 的逐行 dedup 完全看不到 → 上滾看到同一段落以不同斷行重複 N 次、樣式錯亂（Howard 截圖的綠色 diff 區糊成一片即此類）。
  - 新增 `_collapse_redraw_frames()`：wrap-invariant 錨點偵測——每行正規化（去 ANSI＋去所有空白）串成單一字元流，找「100 字元視窗從某行首出現、又在更後面的行首重現」的重繪邊界，保留首次出現之前 + 最後一次出現之後，丟掉中間的過時部分幀（最後一幀才是當前寬度、最完整的渲染）。在 tmux capture 後、逐行 dedup 前先跑。
  - 安全閥：視窗 100 字元（≈ 一整行程式碼/中文，巧合重現機率極低）＋ 中間需 ≥15 行才收 → 短回音與合法重複指令永不誤砍（已用「3 段相同長行、中間夾獨特內容」反例驗證零誤傷）。O(行數) 單趟、迭代收斂多次 resize；10k 行實測 ~20-40ms。
  - 實測 live 擷取：同一指令的 3 份不同寬度重繪 → 收成 1 份，穩定 header 與最終完整內容均保留。
- 需 restart 生效（改到 main.py）。

## v0.15.0 (2026-06-10)

### Features
- **Agent 狀態自動偵測（免 `[[SF:]]` 自我回報）** — 新增 `agent_status.py` 伺服端偵測器：tab → transcript 對應（注入 `claude --session-id` 做確定性關聯），status monitor 執行緒持續解析 transcript/rollout 推導 agent 真實狀態（working／waiting／stuck／done），推送到 webview 顯示 busy-dot + 動作詳情。`[[SF:]]` 自我回報降級為輔助訊號。含誤判防護：忽略本地 slash-command 記錄、idle-prompt 守門、長工具執行不誤判 stuck。
- **右側面板改為即時 agent activity feed** — 每個 tab 的目前動作／任務詳情即時呈現（掃整段 tail 補齊 action/task detail），已完成項目自動隱藏；左側維持狀態圓點，詳情集中右側。

### Fixes
- **上滾歷史 overlay：樣式保留 + 不截斷（長年 bug 收斂）** — 之前二選一：巢狀 xterm 版有顏色但表格右半被裁掉（cols clipping）+ WKWebView 滾輪狀態遺失；`<pre>` 版捲動可靠、不截斷但顏色全丟。本版兩者兼得：
  - 前端新增 `ansiToHtml` 轉換器，`<pre>` overlay 改收 `ansi=true`，tmux `capture-pane -e` 的 SGR 重建為 inline-styled `<span>`（16/256/truecolor、粗體/暗淡/斜體/底線/反白/刪除線；OSC 與非 SGR escape 剝除；內容先 HTML escape，終端輸出視為不可信）。長行維持原生橫向捲動，不重排不裁切。
  - 後端修掉潛在 bug：pyte history row 是 dict（col→Char），舊碼 `for c in row` 迭代到的是 int key，整行渲染成空白 → alt-screen（Claude/Codex 執行中）的 pyte 來源永遠 fall through 到 tmux 的錯誤 buffer（「上滾看到不對的歷史」殘留原因之一）。改按 column 索引，並從 pyte Char 屬性重建 ANSI，Claude TUI 上滾也有完整樣式；CJK 寬字元影子格跳過（不再出現「橘 色」式插空格）、帶背景色的行尾空格保留。
- **多行貼上誤自動送出修正（bracketed paste）** — 貼多行文字不再被逐行當 Enter 送出。
- 需 restart 生效（改到 main.py / agent_status.py / web）。

## v0.14.3 (2026-06-09)

### Performance — per-tab CPU 優化（撐 10+ tab）
背景：5-7 tab 時 main.py 吃 ~52% CPU，收 2 tab→22.8%，證明是 per-tab 處理成本。本版砍 idle floor + 背景 tab webview push 節流。實測 5 tab idle：**4.4% → 2.8%（floor 降 ~36%）**；背景串流 tab 輸出零丟失（60 行 in→61 行 out）。

- **idle reader select 退避**（`main.py._reader_unix`）：近 2s 無輸出的 PTY，`select` timeout 由 0.05s 拉到 0.3s（有資料即返回、不影響延遲），砍 idle tab 空轉喚醒 ~6×。
- **bridge 週期掃描降頻**（`bridge_telegram._flush_loop`）：stall 偵測 + auto-compact 這兩個「每 slot 都掃」的檢查由每 0.5s 改每 2s（tick%4），輸出 drain 仍 0.5s。砍 idle floor。
- **history 擷取不再每次 materialize 整個 deque**（`_extract_new_text`）：改用 `itertools.islice` 只走訪新增的 history 行，且螢幕內滾動（history 沒增長）時直接跳過。
- **pyte HistoryScreen history 3000 → 800 行**：降 per-tab 記憶體與 history 掃描成本（長回應擷取仍足夠）。
- **背景 tab webview push 節流**（`main.py` pusher + `_active_sid`）：只有「當前顯示 tab」全速 push 到 webview；背景(非顯示)tab 的輸出合併、最多 4Hz push。pending 永不丟棄，`set_active_tab` 切換時立即刷出 → 不掉字。砍多背景 tab 串流時的主執行緒負載。

需 restart 生效（已驗證 5 個 tmux tab 重連、切換/輸出/串流正常、bridge 連線、零丟字）。

### Fixes
- **TG 回覆截斷修正 — 長訊息改分多則送出** — 原本 `>4000` 字直接 `msg[:4000] + "...(truncated)"` 截斷遺失內容；改為 `split_for_telegram()` 依行邊界切成 ≤3900 字多則依序送出（Telegram 單則上限 4096，保留 label 前綴餘裕），單行超長則硬切，絕不丟內容。
- **TG 回覆洩漏內部 TUI 修正 — marker 區間夾帶評分提示/重繪重複** — 症狀：手機收到回覆尾巴帶「How is Claude doing this session? / 1: Bad 2: Fine 3: Good 0: Dismiss」評分提示與重複內容。根因：Claude Code 回覆完成後重繪終端，評分提示與重繪的重複文字被線性化 PTY 串流吃進 `[[TG_REPLY]]` start/end marker 之間。修法：`clean_mobile_marker_response()`（marker 抽取唯一收斂點，正常/force 兩路徑共用）新增 `_TUI_SENTINEL_RE` 高信心 TUI 哨兵偵測，一旦命中（評分提示、選項列、`Cooked/Worked for Xs`、`esc to interrupt`）即就地截斷整段尾巴。正常回應不含這些字串故零誤傷。marker 存在時本就只走 marker 抽取、不 fallback 抓原始終端畫面（既有設計），此修進一步硬化 marker 內容。
- 純 `bridge_telegram.py` 改動，`sfctl reload` 即生效（免 restart）。

## v0.14.1 (2026-06-08)

### Features
- **交換區面板字級對齊 + 完整 i18n** — 任務看板面板改納入既有 `zoom: var(--ui-scale)` 縮放群組（原本漏掉，UI scale 調整時看板不跟著縮放），字級沿用 sidebar 級距（標題 10px／卡片 12px／chip 9px）。所有看板 UI 文字（按鈕 title、面板標題、空狀態、狀態/難度標籤、新增 prompt、設定開關標籤）改走既有 `I18N` + `t()` 機制，en／zh-TW 兩語系皆補齊，不再寫死中文。
- **啟動「本次更新」彈窗** — app 啟動偵測 version 變化即彈出本次 CHANGELOG 最新版段落，看過後把版本記入 `config.settings.release_seen_version`（config 持久化，取代原本跨重啟不穩的 localStorage 機制），同版不再跳；以後每次 bump version + restart 都會跳一次。新增 `main.py` Api `get_latest_release_notes()`，前端複用既有 `#modal-release` + `renderChangelog`，network-independent。
- **兩側面板寬度可拖拉調整（resizable split）** — sidebar↔主區、交換區↔主區之間新增可拖曳 divider，即時改變寬度並 persist 到 `config.settings.sidebar_width`／`board_width`（下次開維持）。寬度改由 CSS 變數 `--sidebar-w`／`--board-w` 驅動，`.collapsed` 仍強制 0；拖曳時即時 refit 終端機（rAF throttle），面板收合時自動隱藏對應 divider。
- 需 `sfctl restart`（改到 main.py）才完全生效。

## v0.14.0 (2026-06-08)

### Features
- **交換區 / 任務看板（實驗性）** — 新增可開關的實驗功能：右側可展開／收合面板顯示任務卡片（title／assignee／status／難度，未結案優先排序），agent 可透過 harness inject 用 `[[SF:TASK:...]]` marker 自己維護 todo／認領。架構：
  - 新增 `board.py` 共用 store（仿 `main.save_config` 的 atomic write + lock），狀態存 `~/.local/state/shellframe/board.json`；task 欄位 id／title／assignee／status(todo/assigned/in_progress/done)／difficulty(easy/medium/hard)／created_at／updated_at／notes。
  - `main.py`：新增 Api 方法 `board_list/board_add/board_update/board_remove`（前端 polling 用）＋ sfctl `_execute_sfctl` elif 串同名指令（agent/remote 用）；`DEFAULT_CONFIG.settings.experimental_board`（預設 False）。
  - `bridge_telegram.py`：仿 `_SIGNAL_RE` 新增 `_BOARD_RE` 與 `_detect_and_apply_board`，在既有兩處 signal 偵測點攔截 agent 輸出的看板 marker（add/claim/update/done/remove），寫入 board 後把 marker 行從轉發文字剝除；受實驗 flag 守門，關閉時為 no-op。
  - `web/index.html`：右側 `#board-panel`（仿 `#sidebar`，📋 鈕展開／收合）＋ 每 2.5s polling `board_list`；Settings → General 新增「任務看板／交換區（實驗性）」toggle。
  - marker 格式：`[[SF:TASK:add|title=接 webhook 線|difficulty=medium|notes=...]]`、`[[SF:TASK:claim|id=ab12cd34]]`（認領→assignee=分頁 label、in_progress）、`[[SF:TASK:update|id=ab12cd34|status=done]]`、`[[SF:TASK:done|id=ab12cd34]]`、`[[SF:TASK:remove|id=ab12cd34]]`。
  - 需 `sfctl restart`（改到 main.py / bridge）才生效。

## v0.13.9 (2026-06-07)

### Fixes
- **遺漏回覆 bug：`[[TG_REPLY]]` 後接 tool 輸出時 reply 被靜默吞掉** — `_extract_marked_mobile_reply` 的 tail guard 原本只要 end marker 後還有任何「非雜訊」內容就 `return ""`（視為沒抓到、繼續等），要等 30s 後 `_force` 版才忽略 tail 強抽。但總控分頁的常見模式是「先輸出 `[[TG_REPLY]]…[[/TG_REPLY]]`、再跑 Bash/Read 等工具」，那些工具的指令與輸出落在 marker 之後就被當成 tail 內容，於是 reply 被擋住；若該 turn 在 30s 內結束 idle、slot 狀態又被下一則 user 訊息重置，這則 reply 就永久遺漏。修法：tail guard 改成**只在 tail 還含另一組 `reply_start_marker`（代表後面有更新的回應）時才放棄**；end marker 已閉合即視為回應完整，後續純 tool 輸出/操作/雜訊不再擋住送出。`reload` 即生效。

### Fixes
- **INIT_PROMPT no longer injected into the middle of the first user message (web UI path)** — when a worker/AI tab received its first message by typing or pasting in the GUI, the session INIT_PROMPT could land *after* (i.e. in the middle of) the user's text instead of in front of it. Root cause is in `main.py.write_input`: xterm.js delivers a message's text and the Enter that submits it as **separate** `write_input` calls (each keystroke / paste flushes on its own; Enter arrives as a bare `\r`). The injection was gated on `'\r' in data`, so it only fired on that trailing bare Enter — by which point the user's text had already been written to the PTY — and then appended the prompt with an empty `user_text` (`prompt + "\n\n---\nUser's first message: " + "" + "\r"`), so the order on the wire became `<user text><INIT_PROMPT>`. Fix: added `Api._is_user_content()` (printable text / bracketed paste = content; bare Enter / control keys / arrow & F-key escape sequences = not content) and moved injection to fire on the **first content-bearing chunk**, prepending the prompt before that chunk so INIT_PROMPT is always first and the user's text (and its later bare `\r`) flow after. Works for split keystrokes, single combined writes, bracketed paste, and IME multi-char input; still no-ops while the CLI is on a login/auth screen (stays pending). The delegate (`_send_text_to_session`, tmux paste-buffer) and Telegram (`bridge_telegram` consume/concat) paths already ordered the prompt first and are unchanged. Requires `sfctl restart` (main.py change) to take effect.

### 修正
- **INIT_PROMPT 不再被注入到第一則使用者訊息中間（web UI 路徑）** — 在 GUI 用打字或貼上送 worker/AI 分頁的第一則訊息時，session 的 INIT_PROMPT 會落在使用者文字**之後**（即訊息中間），而非最前面。根因在 `main.py.write_input`：xterm.js 把「訊息文字」與送出的 Enter 拆成**不同的** `write_input` 呼叫（每個按鍵／貼上各自 flush，Enter 單獨送 bare `\r`）。原本注入用 `'\r' in data` 當條件，只在那個尾端 bare Enter 觸發——此時使用者文字早已寫進 PTY——然後用空的 `user_text` 把 prompt 接在後面（`prompt + "\n\n---\nUser's first message: " + "" + "\r"`），wire 上順序變成 `<使用者文字><INIT_PROMPT>`。修法：新增 `Api._is_user_content()`（可印字元／bracketed paste＝內容；bare Enter／控制鍵／方向鍵・功能鍵 escape sequence＝非內容），把注入改成在**第一個帶內容的 chunk** 觸發，prompt 前置於該 chunk，使 INIT_PROMPT 永遠在最前面、使用者文字（及之後的 bare `\r`）接在後面。涵蓋拆鍵打字、單次合併寫入、bracketed paste、IME 多字輸入；CLI 還在登入／驗證畫面時仍不注入（維持 pending）。delegate（`_send_text_to_session`，tmux paste-buffer）與 Telegram（`bridge_telegram` consume／concat）路徑本就 prompt 在前，未更動。需 `sfctl restart`（改到 main.py）才生效。

## v0.13.6 (2026-06-04)

### 變更
- **修 UI 凍住：webview 推送加單次字元上限（防爆量輸出灌爆主執行緒）** — `main.py._start_output_pusher` 的 `pusher()` 原本把每個 session 累積的 `pending[sid]` 整包 `json.dumps` 後一次 `evaluate_js('_pushOutput…')`。當 worker 爆量輸出（cat 大檔/base64 字體/長 log，可達數 MB）時，單次 evaluate_js 要在主執行緒序列化超大 CFString（sample 抓到卡在 `WKWebView evaluateJavaScript` + `CFStringGetBytes`），WebKit 主執行緒被灌爆 → 整個 UI 凍住（main CPU 卡 15%、webview render thread 0%）。修法：單次推送超過 `MAX_PUSH_CHARS`(65536) 只送尾端，從換行邊界切避免截斷 ANSI escape，並標一行「已略過 N 字元」。前端對未註冊 session 已有 `PENDING_OUTPUT_CAP`，此補的是已註冊 session 的後端推送缺口。tmux session 不受影響。
## v0.13.7 (2026-06-04)

### Fixes
- **TG → master input no longer mangled into the delegation preamble / `[Request interrupted]`** — pasting text to the master (總控) tab from Telegram could submit the master delegation preamble with `User message: [Request interrupted]`, and occasionally produced malformed master tool calls. Root cause was three compounding issues in the `_send` PTY-write path (`bridge_telegram.py`): (1) **no write serialization** — each TG message spawned its own daemon `_send` thread, so a paste split into several messages (or rapid messages) wrote `payload`/`sleep`/`\r` concurrently to the same PTY and interleaved into one mangled buffer; (2) **no busy guard** — writing payload + Enter while Claude Code was mid-turn aborted the in-flight turn (`[Request interrupted]`) and submitted a mixed/empty buffer; (3) **no bracketed paste + input-box residue** — multi-line payloads were written raw (embedded newlines could prematurely submit) and appended to whatever stale content was left in the input line. Fix: added `SessionSlot.write_lock` and wrapped the whole send in it (serialization); the `_send` worker now waits up to 120s for the CLI to leave the `esc to interrupt` state before injecting (busy guard); clears the input line with Ctrl-U (`\x15`) before writing (residue); and wraps the payload in bracketed paste (`\x1b[200~ … \x1b[201~`) so multi-line input is ingested atomically. Requires a bridge reload to take effect.

### 修正
- **TG 貼文字給總控不再被組成 delegation preamble / `[Request interrupted]`** — 從 Telegram 貼文字給總控分頁，送出的可能是 master delegation preamble 且 `User message: [Request interrupted]`，總控 tool call 偶爾 malformed。根因是 `bridge_telegram.py` 的 `_send` 寫 PTY 路徑三個問題疊加：(1) **無寫入序列化**——每則 TG 訊息各開一條 daemon `_send` thread，貼文字被 TG 切成多則（或連續送）時，`payload`/`sleep`/`\r` 並發寫同一 PTY 交錯成混血 buffer；(2) **無 busy 守門**——在 Claude Code mid-turn 寫 payload+Enter 會中斷進行中的 turn（`[Request interrupted]`）並送出混血/空 buffer；(3) **無 bracketed paste＋輸入框殘留**——多行 payload 直接寫（內嵌換行會提前送出），且接在輸入行殘留後面。修法：新增 `SessionSlot.write_lock` 並把整段送字包進去（序列化）；`_send` 改為先等 CLI 離開 `esc to interrupt` 狀態（最多 120s）再注入（busy 守門）；寫入前送 Ctrl-U（`\x15`）清輸入行殘留；payload 用 bracketed paste（`\x1b[200~ … \x1b[201~`）包起來原子化送入。需 reload bridge 才生效。

## v0.13.5 (2026-06-02)

### Changes
- **Restored「report to the user by tab label, not sid」to both master preambles** — the v0.13.3 refactor rewrote `bridge_telegram.py._MASTER_TURN_PREAMBLE` (into the 5-step delegation flow) and `main.py.MASTER_TURN_PREAMBLE`, and in doing so dropped the v0.12.25 rule that tells the master to refer to workers by their human-readable tab label (e.g.「點裝備優化」) rather than sid (e.g. s48) when reporting to Howard. Re-appended to both: sid is only for the master's own sfctl calls; if a handoff/report or sfctl output gives only a sid, run `sfctl list` to map it to the tab label before relaying.

### 變更
- **兩份 master preamble 補回「對使用者用 tab 名稱、不用 sid」規則** — v0.13.3 重構改寫了 `bridge_telegram.py._MASTER_TURN_PREAMBLE`（成 5 步派工流程）與 `main.py.MASTER_TURN_PREAMBLE`，過程中掉了 v0.12.25 那條「總控回報給 Howard 一律用人類可讀 tab 名稱（如「點裝備優化」）、不要用 sid（如 s48）」規則。兩份都補回：sid 只用於總控自己呼叫 sfctl；若交接/回報或 sfctl 輸出只給 sid，先 `sfctl list` 對照翻成 tab 名再轉述。

## v0.13.4 (2026-06-02)

### Changes
- **Four-state tab signal lights — re-integrated onto v0.13.3 + GREEN-first rule** — the signal system (originally v0.12.28/29) was lost when the v0.13.3 batch commit fast-forwarded over its uncommitted working tree; recovered from the auto-stash and re-applied surgically onto the v0.13.3 bridge/wrapper refactor. A worker prints one line-anchored marker to set its own tab light and the bridge turns it into the matching notification: `[[SF:WORKING]]` → 🔵 blue (running), `[[SF:GREEN]]` → 🟢 green (done; TG「✅ <tab> 已完成（可回收）」+ macOS banner), `[[SF:RED]]` → 🔴 red (needs decision; pairs with the numbered menu + macOS banner), `[[SF:YELLOW:reason]]` → 🟡 orange (stuck; TG「🟡 <tab> 卡住：<reason>」+ macOS banner). Bridge: `_SIGNAL_RE` / `_detect_signal_in_lines` / `_detect_and_fire_signal` (fires in BOTH the has_user_msg drain branch — where master-delegated workers live — and the forward path; broadcasts to all known chats since delegated workers have no active-chat routing), `_signal_desktop_notify`, `slot.last_signal` dedup. UI (`web/index.html`): `.sig-working/done/decision/stuck` dots (blue/green/red/orange), `scanTerminalActivity` scans the last 60 viewport rows for the marker; **heuristic activity clears a stale resting signal so a running tab is always blue** (fixes a 🟢 lingering over an actively-working tab). NEW **GREEN-first** rule in `_delegate_prompt` + `INIT_PROMPT.md`: the instant the main deliverable is done (shipped/verified/memory written), print `[[SF:GREEN]]` FIRST, THEN do optional cleanup — never let cleanup (closing tabs, clearing temp) block the green light and leave the dot stuck blue. Includes positive/negative examples; markers are shown inline-in-prose so the instructions never self-trigger detection. Also restores the numbered closing-menu 收尾規則 to `_delegate_prompt` (dropped in the v0.13.3 refactor). Requires a restart (wrapper + web changes); only affects newly delegated workers.

### 變更
- **四態 tab 燈號信號 — 重新整合回 v0.13.3 ＋ 綠燈優先規則** — 燈號系統（原 v0.12.28/29）在 v0.13.3 批次 commit 快進時被未提交工作樹蓋掉，已從 auto-stash 救回、手術式重貼到 v0.13.3 的 bridge/wrapper 重構上。worker 單獨輸出一行行錨定標記即設燈號，bridge 轉對應通知：`[[SF:WORKING]]`→🔵藍、`[[SF:GREEN]]`→🟢綠（TG「✅已完成可回收」＋桌面）、`[[SF:RED]]`→🔴紅（配編號選單＋桌面）、`[[SF:YELLOW:原因]]`→🟡橘（TG「🟡卡住:原因」＋桌面）。bridge：`_SIGNAL_RE`/`_detect_signal_in_lines`/`_detect_and_fire_signal`（在 has_user_msg drain 分支——總控派工的 worker 都在這——與轉發路徑都會觸發；因派工 worker 無 active-chat 路由，改 broadcast 給所有已知 chat）、`_signal_desktop_notify`、`slot.last_signal` 去重。UI：`.sig-working/done/decision/stuck` 四色燈（藍/綠/紅/橘），`scanTerminalActivity` 掃末 60 行；**啟發式偵測到活動就清掉 stale resting 燈，跑起來的 tab 一定藍**（修掉綠燈黏在正在跑的 tab 上）。新增**綠燈優先**規則於 `_delegate_prompt`＋`INIT_PROMPT.md`：主要交付一完成（上線/驗證/記憶已沉澱）就先印 `[[SF:GREEN]]`，再去做可選清理——別讓清理（關分頁、清暫存）擋住綠燈、害 dot 卡藍。附正反例；標記在說明文字一律 inline，不會自我誤觸。同時把 v0.13.3 重構中掉的編號收尾選單規則一併補回 `_delegate_prompt`。需 restart（wrapper＋web），只對之後新派 worker 生效。

## v0.13.3 (2026-06-02)

### Fixes
- **注音 (IME) composition no longer reset by the refocus guard** — the window-focus refocus guard and the periodic focus-steal `grab()` used to call `ta.focus()` mid-composition, wiping the in-progress 注音 buffer on WKWebView. Both now early-return while `_imeComposing` is true (set on `compositionstart`/`compositionend`), so Chinese input survives Cmd+Tab and refocus events.
- **TG marker replies no longer blocked forever by Claude Code's session-end / rating UI** — the tail guard treated `✻ Cooked for Xs` / `─ Worked for Xs`, the `How is Claude doing this session?` rating prompt, and its `1: Bad  2: Fine ...` options line as "meaningful output after the marker", so it kept deferring the send indefinitely. These lines are now recognised as bridge noise, and a 30s fallback (`_extract_marked_mobile_reply_force`) force-extracts the reply if the tail guard is still blocking, so replies always reach mobile.

### 修正
- **注音輸入不再被 refocus guard 打斷** — 視窗 refocus 與週期性搶 focus 的 `grab()` 過去會在組字中途呼叫 `ta.focus()`，在 WKWebView 上清掉正在輸入的注音。兩者現在於 `_imeComposing`（由 `compositionstart`/`compositionend` 設定）為 true 時提早返回，Cmd+Tab 或視窗 refocus 都不會吃掉中文輸入。
- **TG 標記回覆不再被 Claude Code 結束/評分畫面永久擋住** — tail guard 過去把 `✻ Cooked for Xs`／`─ Worked for Xs`、`How is Claude doing this session?` 評分提示與 `1: Bad  2: Fine ...` 選項列當成「marker 後還有正常輸出」，導致一直延後送出。現在這些列被視為 bridge 噪音，並加上 30s fallback（`_extract_marked_mobile_reply_force`）：tail guard 卡超過 30s 就強制截取回覆，確保訊息一定送達手機。

## v0.13.2 (2026-06-01)

### Fixes
- **Marked TG replies no longer collapse to the word「和」** — the wrapper-injected instruction `…請放在 [[TG_REPLY_xxx]] 和 [[/TG_REPLY_xxx]] 之間` contains an example marker pair using the *same* token as the real reply markers. When the real reply markers got fragmented by TUI repaint, `rfind` matched the instruction's pair instead and extracted only the「和」between them. The extractor now strips that instruction pair (`start 和 end`) before locating the real reply.

### 修正
- **TG 標記回覆不再被截成「和」一個字** — wrapper 注入的指示文字 `…請放在 [[TG_REPLY_xxx]] 和 [[/TG_REPLY_xxx]] 之間` 裡的範例 marker 與真實標記同字串；當真實標記被終端重繪打斷時，`rfind` 會誤中指示裡那組、只截出中間的「和」。截取前先移除指示那組（`start 和 end`），再定位真實回覆。

## v0.13.1 (2026-05-29)

### Fixes
- **Telegram replies now wait for the marker tail to settle** — the bridge no longer sends a marked TG reply as soon as it sees `[[TG_REPLY_...]] ... [[/...]]` if there is still meaningful output after the closing marker. This avoids half-finished messages reaching mobile when the CLI keeps writing after the marker.

### 修正
- **Telegram 回覆會等 marker 尾端穩定後再送出** — bridge 不會在看到 `[[TG_REPLY_...]] ... [[/...]]` 就立刻送出；如果關閉 marker 後面還有正常輸出，會先等，避免 CLI 還在輸出時手機就收到半截訊息。

## v0.13.0 (2026-05-28)

### Features
- **Settings architecture cleanup** — Delegation settings (auto-delegate toggle, custom preamble textarea) moved from Telegram tab to General tab. Delegation is a master behavior, not TG-specific.
- **Completion notifications for local sessions** — `_arm_awaiting_response()` now fires on local Enter keystrokes in AI sessions, so macOS notifications work regardless of whether input came from Telegram or the local terminal.
- **Improved Master Delegation Protocol** — Default preamble rewritten as a structured 5-step evaluation (understand intent → check workers → delegate criteria → handle-directly criteria → delegate syntax).
- **Versioning policy** — Added to README.md: MINOR for features, PATCH for fixes.

### 新增
- **設定架構整理** — 派工設定（自動派工 toggle、自訂派工提示 textarea）從 Telegram 分頁搬到「一般」。派工是總控行為，不是 TG 專屬。
- **本地操作完成通知** — 本地鍵入 Enter 也會 arm bridge slot，macOS 通知不再只限 TG 來源訊息。
- **改進 Master Delegation Protocol** — 預設 preamble 改為結構化 5 步判斷流程。
- **版號規則** — 寫入 README.md：MINOR = 新功能，PATCH = 修 bug。

## v0.12.21 (2026-05-28)

### Features
- **Telegram Bridge advanced settings UI** — Settings → Telegram Bridge now exposes `show_tg_wrapper`, `master_turn_preamble_enabled`, `master_turn_preamble`, `tg_prompt`, and experimental `auto_delegate_enabled`, all persisted under `settings` in `~/.config/shellframe/config.json`.
- **Empty TG prompt fields use built-in defaults** — clearing `master_turn_preamble` or `tg_prompt` now falls back to ShellFrame's built-in prompt instead of disabling it.

### 新增
- **Telegram Bridge 進階設定 UI** — Settings → Telegram Bridge 現在可設定 `show_tg_wrapper`、`master_turn_preamble_enabled`、`master_turn_preamble`、`tg_prompt` 與實驗性的 `auto_delegate_enabled`，並寫入 `~/.config/shellframe/config.json` 的 `settings`。
- **TG prompt 留空使用內建預設** — 清空 `master_turn_preamble` 或 `tg_prompt` 會回到 ShellFrame 內建提示，不再代表關閉。

## v0.12.20 (2026-05-28)

### Features
- **`show_tg_wrapper` setting** — when `true` (default), TG messages injected into the local terminal are prefixed with `[SF-TG wrapper]` so the operator can see that a wrapper prompt was injected. Set to `false` to hide the tag.
- **`master_turn_preamble` setting** — customizable text for the per-turn master delegation prompt. Falls back to the built-in default when not set. Use this to tune delegation aggressiveness.
- **`tg_prompt` setting** — already existed, now documented: override the per-turn TG formatting/coordination prompt. Empty string disables it.

### Fixes
- **Local terminal output no longer forwarded to Telegram** — after a TG_REPLY marked response is extracted and sent, `has_user_msg` is reset to `False`. Subsequent local-only terminal activity is no longer flushed to TG, preventing noisy echo when the user switches to local operation.

### 修正與新增
- **`show_tg_wrapper` 設定** — 預設開啟，TG 訊息注入本地終端時會加上 `[SF-TG wrapper]` 前綴標記。設 `false` 可隱藏。
- **`master_turn_preamble` 設定** — 可自訂每輪派工提示文字，不設就用內建預設值。
- **本地操作不再轉發 TG** — TG_REPLY 送出後 `has_user_msg` 重設，避免本地噪音。

## v0.12.19 (2026-05-28)

### Fixes
- **Idle reaper handoff messages now sent via Telegram** — when `keep_bridge_active` is true, lifecycle handoff messages (session closed notifications) are also sent to all connected Telegram users, so the operator doesn't have to watch the terminal to see handoffs.

### 修正
- **Idle reaper 交接訊息同步送 Telegram** — 當 `keep_bridge_active` 為 true 時，session 關閉的交接通知除了注入總控 terminal 外，也會同步推送到所有已連線的 Telegram 使用者，不需要手動轉貼。

## v0.12.18 (2026-05-27)

### Fixes
- **Spec-site delegation is part of the default roster** — new ShellFrame configs now include the `規格站` role by default, mapped to `規格站-CDX` with the shared Codex launcher command, and role aliases such as `spec`, `garden`, and `garden-cms` resolve to it.

### 修正
- **規格站派工納入預設 roster** — 新 ShellFrame config 現在會預設包含 `規格站` 角色，對應 `規格站-CDX` 並使用共用 Codex launcher command；`spec`、`garden`、`garden-cms` 等 alias 也會解析到此角色。

## v0.12.17 (2026-05-27)

### Features
- **Master sessions get a per-turn orchestration reminder** — user-facing master tabs now prepend a short configurable preamble before each submitted AI turn, reminding the agent to understand the request first, run `sfctl list` for non-trivial or parallel work, consider `sfctl delegate`, and avoid hard keyword routing. Telegram-forwarded turns follow the same rule, and Settings → General now has a localized toggle for `settings.master_turn_preamble_enabled`.

### 新增
- **總控 session 每輪輸入會收到派工提醒** — user-facing master tab 現在每次送出 AI 輸入前會加上一段可由 config 關閉的短 preamble，提醒 agent 先理解需求，非 trivial 或可並行時跑 `sfctl list`、考慮 `sfctl delegate`，且禁止硬 keyword routing。Telegram 轉送輸入也套用同規則，Settings → General 也新增中英切換開關對應 `settings.master_turn_preamble_enabled`。

## v0.12.16 (2026-05-27)

### Features
- **Rokid Bridge is no longer enabled by default** — ShellFrame now gates plugins through `plugins.installed` and `plugins.enabled` config lists. Bundled marketplace entries stay available, but Rokid only loads after the user installs/enables it; existing Rokid setups with a LaunchAgent or channel directory are migrated as enabled.

### 新增
- **Rokid Bridge 不再預設啟用** — ShellFrame 現在以 `plugins.installed` 與 `plugins.enabled` config 清單控制 plugin。Marketplace 仍會列出 bundled plugin，但 Rokid 必須由使用者安裝/啟用後才載入；已有 LaunchAgent 或 channel 目錄的既有 Rokid 設定會自動遷移為啟用。

## v0.12.15 (2026-05-27)

### Features
- **Local user instructions are injected into new AI sessions** — ShellFrame config now supports `user_prompt_paths` with `~/.claude/CLAUDE.md` as the default. New AI sessions append existing user prompt files after the ShellFrame init prompt under `## User Instructions`, and delegated worker prompts include a 2000-character excerpt so workers inherit the user's language, style, and delegation preferences.

### 新增
- **本機使用者指令會注入新 AI session** — ShellFrame config 現在支援 `user_prompt_paths`，預設為 `~/.claude/CLAUDE.md`。新 AI session 會在 ShellFrame init prompt 後以 `## User Instructions` 追加存在的使用者 prompt；派工 worker prompt 也會附上前 2000 字元摘要，讓 worker 繼承使用者語言、風格與派工偏好。

## v0.12.14 (2026-05-27)

### Fixes
- **Worker results can return before full aggregation** — delegation prompts and docs now tell workers to return user-ready drafts, reports, lookup results, and operation conclusions immediately in a ready-to-forward form, so the master can keep the conversation responsive while other parallel work continues.

### 修正
- **Worker 可先回可用成果，不必等總控完整統整** — 派工 prompt 與文件現在要求 worker 若產出可直接給使用者的草稿、報告、查詢結果或操作結論，要先用可轉貼格式回覆，讓總控能一邊回應使用者、一邊繼續等其他平行工作。

## v0.12.13 (2026-05-27)

### Fixes
- **Worker file searches avoid macOS privacy prompts** — delegated worker prompts and the built-in ShellFrame init prompt now tell agents to search from known project roots and avoid broad scans of `/Users`, `~/Library`, iCloud Drive, Mail, Messages, Photos, and other protected folders. This prevents the ShellFrame Python process from repeatedly triggering macOS data-access popups during research tasks.

### 修正
- **Worker 查檔避免觸發 macOS 隱私權彈窗** — 派工 wrapper prompt 與 ShellFrame 內建 init prompt 現在會要求 agent 從已知專案根目錄查找，避免廣掃 `/Users`、`~/Library`、iCloud Drive、Mail、Messages、Photos 等受保護資料夾，降低研究任務時 ShellFrame Python process 反覆跳資料存取權限視窗的情況。

## v0.12.12 (2026-05-27)

### Fixes
- **`sfctl send` and `sfctl delegate` now submit large prompts reliably** — orchestrator dispatch now uses tmux paste buffers with bracketed paste for tmux-backed sessions, then sends Enter only after the paste completes. This avoids the previous direct PTY write path where large or multiline AI prompts could appear in the worker input box but fail to submit until Howard pressed Enter manually.

### 修正
- **`sfctl send` 與 `sfctl delegate` 現在能穩定送出大段 prompt** — 總控派工改用 tmux paste buffer 加 bracketed paste，貼上完成後才送 Enter。避免舊的 PTY raw write 在長文字或多行 AI prompt 時，文字已進入 worker 輸入框但沒有自動送出、需要 Howard 手動按 Enter 的問題。

## v0.12.11 (2026-05-27)

### Fixes
- **macOS Dock identity now uses one canonical app bundle** — restart, `sfctl restart`, and update refresh now prefer `/Applications/ShellFrame.app` before the user-level `~/Applications` fallback, and update refresh removes the stale user-level copy after refreshing `/Applications`. This prevents Dock pins that point at one ShellFrame bundle from showing a second running icon from another bundle path.

### 修正
- **macOS Dock 身份收斂到單一 app bundle** — restart、`sfctl restart` 與 update refresh 現在優先使用 `/Applications/ShellFrame.app`，不存在時才退到 `~/Applications`；成功刷新 `/Applications` 後也會移除 stale 的使用者層級副本。避免 Dock 固定項指向一份 ShellFrame，但實際啟動另一份 bundle 時旁邊又多出第二個運行 icon。

## v0.12.10 (2026-05-27)

### Fixes
- **General settings now fully follow the selected language** — idle tab cleanup controls, session prompt labels/help, and related General toggles now use the ShellFrame i18n table instead of hard-coded English. The cleanup save status also localizes after switching language.

### 修正
- **一般設定會完整跟著語言切換** — 閒置頁籤自動清理、工作階段提示標籤/說明，以及相關的一般設定開關現在都走 ShellFrame i18n 字典，不再硬寫英文。清理設定儲存狀態也會依語言顯示。

## v0.12.9 (2026-05-27)

### Fixes
- **Worker lifecycle rule is now part of ShellFrame prompts** — the built-in init prompt, Telegram per-turn prompt, and README now tell every master agent to keep finished worker tabs by default for follow-up, relying on idle cleanup to summarize and close unused tabs later. This makes the behavior portable across new Codex/Claude sessions instead of depending on one agent's memory.

### 修正
- **Worker 生命週期規則納入 ShellFrame prompt** — 內建 init prompt、Telegram per-turn prompt 與 README 現在都要求總控預設保留已完成 worker tab 供後續追問，讓 idle cleanup 稍後自行摘要與關閉。這樣換 Codex/Claude 或重開 session 也會運作，不再依賴單一 agent 記憶。

## v0.12.8 (2026-05-27)

### Fixes
- **Global hotkey no longer depends on Accessibility first** — macOS now registers `Ctrl+Option+Space` with Carbon `RegisterEventHotKey` before falling back to the old `NSEvent` global monitor. ShellFrame still keeps the local `NSEvent` monitor so the foreground app can swallow the shortcut, and debug logs now show whether Carbon or the fallback path is active.

### 修正
- **全域快捷鍵優先不再依賴 Accessibility** — macOS 現在會先用 Carbon `RegisterEventHotKey` 註冊 `Ctrl+Option+Space`，失敗才 fallback 到舊的 `NSEvent` global monitor。ShellFrame 前景時仍保留 local `NSEvent` monitor 來攔截快捷鍵，debug log 也會明確記錄 Carbon 或 fallback 路徑是否啟用。

## v0.12.7 (2026-05-27)

### Features
- **Manual worker delegation via `sfctl delegate`** — ShellFrame now exposes an `agent_roster` config and a one-line delegation command that creates or reuses a role tab, applies the role label, sends a responsibility wrapper prompt, and returns the sid for `sfctl peek`. This keeps `總控-*` in charge without hard-routing user messages by keyword.
- **Startup handoff notes are quiet by default** — lifecycle handoff still works for close/failure/done notes, but scheduled/delegated tab startup notes now require `idle_reaper.handoff_on_start: true`, reducing `[ShellFrame 交接]` noise in the master tab.

### 功能
- **新增 `sfctl delegate` 手動 worker 派工** — ShellFrame 新增 `agent_roster` 設定與一行派工指令，可建立或重用角色 tab、套用角色命名、送入職責 wrapper prompt，並回傳 sid 讓總控用 `sfctl peek` 回收進度。總控仍先理解需求，不做關鍵字硬路由。
- **啟動交接預設靜音** — lifecycle handoff 仍保留 close/failure/done 回寫，但排程/派工開 tab 的啟動交接需明確設定 `idle_reaper.handoff_on_start: true` 才會寫入，避免總控被 `[ShellFrame 交接]` 洗版。

## v0.12.6 (2026-05-27)

### Features
- **Settings UI for idle tab cleanup** — General settings now expose idle AI tab cleanup controls: enable/disable, idle time with minute/hour units, summary wait time, and handoff-to-master notes. The config migration also writes missing `idle_reaper` keys into `~/.config/shellframe/config.json`, so the setting is discoverable instead of living only as a code default.

### Fixes
- **sfctl IPC no longer wedges on concurrent commands** — `sfctl` now writes unique queued command files and unique result files instead of racing on one shared JSON file. The ShellFrame watcher also deletes malformed command files after logging them, so a partial write cannot block all future `sfctl` calls.

### 功能
- **Settings 可設定閒置頁籤自動關閉** — 一般設定新增 idle AI tab cleanup 介面，可開關自動清理、用分鐘/小時設定閒置時間、設定摘要等待時間，以及是否回寫總控交接。設定檔 migration 也會補齊 `idle_reaper` 缺漏欄位，避免功能只藏在程式預設值。

### 修正
- **sfctl IPC 不會因並行指令卡死** — `sfctl` 改用每個請求獨立的 queue command file 與 result file，不再搶寫同一個 JSON。ShellFrame watcher 讀到壞掉的 command file 也會記錄後刪除，避免半寫入檔案堵住所有後續 `sfctl`。

## v0.12.5 (2026-05-26)

### Features
- **Default prompt now teaches master/worker orchestration** — new AI sessions now receive a persistent ShellFrame operating contract: keep `總控-*` as the coordinating session, choose `CDX` workers for coding/local operations and `CLD` workers for research/writing/knowledge work, label tabs by function first, dispatch with wrapper prompts, poll with `sfctl peek`, and close orchestrated workers with lifecycle handoff notes.

### 功能
- **預設 prompt 內建總控/worker 分派規則** — 新 AI session 現在會吃到持久化的 ShellFrame 操作契約：由 `總控-*` 維持使用者對話與派工，程式/本機操作用 `CDX`，研究/寫作/知識整理用 `CLD`，tab 命名採功能優先，派工時包 wrapper prompt，用 `sfctl peek` 回收進度，完成後以 lifecycle handoff 關閉。

## v0.12.4 (2026-05-26)

### Features
- **Lifecycle handoff notes to the main session** — when the idle reaper closes a tab, ShellFrame now writes a short `[ShellFrame 交接]` note into the main/control session with the closed tab label, sid, reason, idle time, command, and captured summary path. Scheduled `sfctl new/close` flows can also opt in with `--handoff --source scheduler`, so automatically started or cleaned-up tabs leave a lightweight handoff trail instead of disappearing silently.

### 功能
- **頁籤生命週期會回寫總控交接** — idle reaper 關閉頁籤時，ShellFrame 會在總控 session 寫入簡短 `[ShellFrame 交接]`，包含被關閉 tab 的 label、sid、原因、閒置時間、指令與摘要檔路徑。排程透過 `sfctl new/close` 啟動或清理 tab 時，也可用 `--handoff --source scheduler` 主動留下交接紀錄，避免自動開關頁籤後總控不知道發生什麼事。

## v0.12.3 (2026-05-26)

### Fixes
- **Idle reaper now uses input activity** — background terminal output and AI TUI redraws no longer reset idle timers. A session is considered active only when ShellFrame, Telegram, LINE, or `sfctl send` writes new input to it.

### 修正
- **Idle reaper 改用輸入活動判斷** — terminal 背景輸出與 AI TUI 重繪不再重置閒置計時。只有 ShellFrame、Telegram、LINE 或 `sfctl send` 寫入新 input 時，才會視為 session 有活動。

## v0.12.2 (2026-05-26)

### Fixes
- **Idle reaper protects active TG/LINE bridge targets dynamically** — ShellFrame now keeps the sessions currently selected by Telegram or LINE bridge users, plus the configured main session. Switching a bridge back to main no longer keeps older gateway/worker tabs alive just because they are bridge-enabled.

### 修正
- **Idle reaper 動態保護 TG/LINE bridge 目前目標** — ShellFrame 現在只固定保留 TG 或 LINE bridge 使用者目前指向的 session，以及設定中的 main session。當 bridge 切回 main 後，舊的 gateway / worker tab 不會因為曾經 bridge-enabled 就一直被保留。

## v0.12.1 (2026-05-26)

### Features
- **Idle reaper self-sediment option** — idle AI sessions can now be configured to append a concise reflection to a shared `Agent Reflections.md` file before ShellFrame captures the pane and closes the tab. The option is config-gated so other installs do not inherit a user-specific memory path.

### 功能
- **Idle reaper 可設定自動沉澱** — 閒置 AI session 現在可透過設定，在 ShellFrame 擷取 pane 並關閉 tab 前，先追加精簡反思到共用 `Agent Reflections.md`。此功能以 config 控制，避免其他安裝吃到使用者專屬的記憶路徑。

## v0.12.0 (2026-05-26)

### Features
- **Dynamic LINE worker sessions from PR #2** — LINE messages now route through named gateway worker tabs such as `LINE-Gateway`, `LINE-Dev`, `LINE-Ops`, `LINE-KGI`, and `LINE-Reminder`, creating a worker tab on demand and reusing it by label instead of sending every LINE request through one active tab.
- **Idle session reaper** — ShellFrame now has a configurable idle reaper that can ask inactive AI sessions to produce a final Traditional Chinese summary/reflection, capture recent terminal history to `~/.config/shellframe/session_summaries/`, and close the tab after a grace period while preserving protected main/gateway sessions.

### 功能
- **整合 PR #2 的動態 LINE worker sessions** — LINE 訊息現在會依內容派到 `LINE-Gateway`、`LINE-Dev`、`LINE-Ops`、`LINE-KGI`、`LINE-Reminder` 等具名 worker tab，需要時自動建立，之後依 label 重用，不再把所有 LINE 請求都塞到單一 active tab。
- **Idle session reaper** — ShellFrame 新增可設定的閒置回收器，可要求閒置 AI session 先輸出繁中總結與反思，將近期 terminal history 存到 `~/.config/shellframe/session_summaries/`，再於 grace period 後關閉 tab，同時保留主 session / gateway session。

## v0.11.99 (2026-05-26)

### Fixes
- **App refresh keeps the native macOS launcher** — update-time `.app` copying now recompiles the Mach-O ShellFrame launcher from `scripts/macos_app_launcher.c` instead of copying the shell script executable into `/Applications`. This prevents macOS TCC prompts from regressing to `python3.13 wants to access data from other apps` after updates.

### 修正
- **更新 `.app` 時會保留 macOS native launcher** — update 流程複製 `.app` 後，會重新從 `scripts/macos_app_launcher.c` 編譯 Mach-O ShellFrame launcher，不再把 shell script executable 複製進 `/Applications`。避免更新後 macOS TCC 權限提示又退回 `python3.13 想要取用其他 App 的資料`。

## v0.11.98 (2026-05-26)

### Features
- **Plugin system PR integrated onto current ShellFrame** — adds the `shellframe_plugins/` loader, settings-panel injection, sidebar badges, plugin marketplace metadata, and the Rokid Bridge plugin without reverting the newer LINE/TG/session work on main.

### 功能
- **整合 plugin system PR 到目前 ShellFrame** — 新增 `shellframe_plugins/` 載入器、設定面板注入、側邊欄 badge、plugin marketplace metadata，以及 Rokid Bridge plugin，同時保留目前 main 上較新的 LINE/TG/session 改動。

## v0.11.97 (2026-05-26)

### Fixes
- **LINE poll mode returns ShellFrame-tab replies** — LINE responses extracted from active ShellFrame tabs now enqueue into `/line/poll` instead of trying the direct LINE push API. Normal poll-mode messages also stop emitting the noisy `Sent to ...` ack before the real agent reply.

### 修正
- **LINE poll mode 會回傳 ShellFrame tab 的回覆** — 從目前 ShellFrame tab 擷取出的 LINE 回覆現在會放進 `/line/poll` outbox，不再誤走直連 LINE push API。一般 poll-mode 訊息也不再先送出干擾閱讀的 `Sent to ...` ack。

## v0.11.96 (2026-05-26)

### Fixes
- **Telegram mobile uses ShellFrame-tab reply markers** — Telegram-originated normal messages still go into the selected ShellFrame tab, but now include a per-turn reply marker and the bridge returns only the marked final text. This keeps the live tab context while avoiding Codex tool-log polling noise.

### 修正
- **Telegram 手機橋接改用 ShellFrame tab 內回覆標記** — Telegram 來源的一般訊息仍會送進目前選到的 ShellFrame tab，但每回合會加唯一回覆標記，bridge 只回傳標記內的最終文字。這保留 live tab context，同時避開 Codex 工具 log polling 噪音。

## v0.11.95 (2026-05-26)

### Fixes
- **Mobile bridge keeps the selected Codex session** — Telegram and LINE messages now go back through the active ShellFrame tab instead of spawning a separate `codex exec --json` runner, preserving the live Hermes/session context.

### 修正
- **手機 bridge 保留目前選到的 Codex session** — Telegram 與 LINE 訊息現在會送回 ShellFrame 目前的 tab，不再另外啟動 `codex exec --json`，避免取代 Hermes / 原本的 session context。

## v0.11.94 (2026-05-25)

### Fixes
- **Codex mobile replies use JSONL instead of TUI polling** — Telegram and LINE messages routed to Codex/CDX tabs now run through `codex exec --json` and return only final `agent_message` text, preventing tool logs and terminal redraws from being sent back to mobile chats.

### 修正
- **Codex 手機回覆改走 JSONL，不再 poll TUI** — Telegram 與 LINE 傳到 Codex/CDX tab 的一般訊息現在會走 `codex exec --json`，只回傳最終 `agent_message`，避免工具 log 與終端重繪一起送回手機聊天室。

## v0.11.93 (2026-05-25)

### Fixes
- **Mobile bridges suppress Codex tool-log noise** — Telegram and LINE cleanup now drop common command/tool transcript lines (`Ran ...`, `curl`, `tmux`, `... +N lines`, token/tool metadata) so polling replies are more readable.

### 修正
- **手機 bridge 會壓掉 Codex 工具輸出雜訊** — Telegram 與 LINE cleanup 現在會丟掉常見 command/tool transcript 行（`Ran ...`、`curl`、`tmux`、`... +N lines`、工具 metadata），讓 polling 回覆更可讀。

## v0.11.92 (2026-05-25)

### Fixes
- **LINE marker detection strips terminal control sequences first** — unique LINE reply markers are now searched in cleaned terminal text, fixing missed markers when Codex redraw output includes control bytes.

### 修正
- **LINE marker 偵測會先移除終端控制序列** — 唯一 LINE 回覆標記現在會在清理後的終端文字中搜尋，修正 Codex 重繪輸出含控制 bytes 時抓不到 marker 的情況。

## v0.11.91 (2026-05-25)

### Fixes
- **LINE poll checks recent terminal state for reply markers** — when waiting for a marked LINE response, the bridge now combines pending output with the session peek buffer before deciding whether a reply is ready.

### 修正
- **LINE polling 會檢查近期終端狀態中的回覆標記** — 等待 LINE 標記回覆時，bridge 現在會把 pending output 與 session peek buffer 合併檢查，再判斷是否可以回傳。

## v0.11.90 (2026-05-25)

### Fixes
- **LINE accepts isolated unique reply markers** — the bridge now extracts a response when only the agent's unique marker pair appears in the latest output chunk, while still ignoring prompt self-matches.

### 修正
- **LINE 可接受獨立出現的唯一回覆標記** — bridge 現在會在最新輸出片段只有 agent 的唯一 marker pair 時正確擷取回覆，同時仍忽略 prompt 自己造成的誤命中。

## v0.11.89 (2026-05-25)

### Fixes
- **LINE marker wait no longer falls back to stale redraws** — the bridge now waits for a second start marker and a following end marker from the agent response instead of timing out into old terminal content.

### 修正
- **LINE marker 等待不再 fallback 到舊畫面** — bridge 現在會等待 agent 回覆中的第二個起始 marker 與後續結尾 marker，不再逾時後把舊終端內容送回 LINE。

## v0.11.88 (2026-05-25)

### Fixes
- **LINE final-reply markers are per message** — the bridge now generates a unique reply marker for each LINE message and waits for the second occurrence, so the prompt's own marker text cannot be mistaken for the agent's answer.

### 修正
- **LINE 最終回覆標記改為每則唯一** — bridge 現在每則 LINE 訊息產生唯一 reply marker，並等待第二次出現，避免 prompt 裡的標記文字被誤判成 agent 回覆。

## v0.11.87 (2026-05-25)

### Fixes
- **LINE marker prompt no longer self-matches** — the bridge now describes the final-reply marker without embedding the literal marker pair in the prompt, and waits for both marker sides before returning a reply.

### 修正
- **LINE marker prompt 不再自己命中** — bridge 現在用文字描述最終回覆標記，不在 prompt 內放入完整 literal marker pair，並且會等開頭與結尾標記都出現才回傳。

## v0.11.86 (2026-05-25)

### Fixes
- **LINE waits for a marked final reply** — per-turn LINE prompts now ask agents to wrap the mobile reply in `>>> ... <<<`, and the bridge waits for that marker before polling returns. This avoids sending stale screen redraws while Codex is still composing.

### 修正
- **LINE 會等待標記後的最終回覆** — 每則 LINE prompt 現在會要求 agent 用 `>>> ... <<<` 包住手機端回覆，bridge 會等到標記出現才讓 poll 回傳，避免 Codex 還在產生時先送出舊畫面重繪。

## v0.11.85 (2026-05-25)

### Fixes
- **LINE replies suppress prompt/input echoes** — LINE bridge cleanup now removes repeated LINE wrapper prompts, `[LINE ...]` input echoes, and common Codex suggestion placeholders from mobile replies.

### 修正
- **LINE 回覆會壓掉 prompt / input 回聲** — LINE bridge cleanup 現在會移除重複的 LINE wrapper prompt、`[LINE ...]` 輸入回聲，以及常見 Codex 建議 placeholder，避免手機端收到舊畫面殘影。

## v0.11.84 (2026-05-25)

### Fixes
- **LINE messages submit in full-screen TUIs** — the LINE bridge now writes the message body and Enter as separate keystrokes, fixing cases where Codex showed the LINE text in its input box but did not send it.

### 修正
- **LINE 訊息會在全螢幕 TUI 中真正送出** — LINE bridge 現在會分開送訊息本文與 Enter，修正 Codex 只把 LINE 文字顯示在輸入框、但沒有送出的情況。

## v0.11.83 (2026-05-25)

### Fixes
- **LINE/TG reply extraction ignores tmux status bars** — bridge filtering now drops tmux status-line fragments such as `[sf_s1] 0:... [0,0] "host" ...`, preventing mobile replies from showing terminal chrome instead of an agent response.

### 修正
- **LINE/TG 回覆擷取會忽略 tmux 狀態列** — bridge filter 現在會丟掉像 `[sf_s1] 0:... [0,0] "host" ...` 這類 tmux status-line 片段，避免手機端收到終端機狀態列而不是 agent 回覆。

## v0.11.82 (2026-05-25)

### Fixes
- **Codex preset is platform-specific again** — Windows defaults and migrations now use `codex ...` instead of the Unix-only `sf-codex` wrapper. macOS/Linux still use `sf-codex ...` so Rosetta/native-binary handling remains available there.
- **Codex command cleanup preserves existing arguments** — migration now replaces only the first executable token when converting old absolute `sf-codex` paths, leaving the rest of the command untouched instead of rebuilding it with POSIX quoting.

### 修正
- **Codex preset 恢復依平台決定** — Windows 的預設值與 migration 現在會使用 `codex ...`，不再使用 Unix-only 的 `sf-codex` wrapper。macOS/Linux 仍使用 `sf-codex ...`，保留 Rosetta/native binary 的處理。
- **Codex 指令清理會保留既有參數** — migration 轉換舊的絕對 `sf-codex` 路徑時，只替換第一個 executable token，後面的指令參數原樣保留，不再用 POSIX quoting 重組。

## v0.11.81 (2026-05-25)

### Fixes
- **macOS installer now repairs non-git installs cleanly** — installs that were copied from zip files or agent-built folders are backed up and replaced with a clean clone instead of being converted in place, avoiding mixed app bundles, stale launchers, and untracked files that survive reset.
- **ShellFrame.app keeps its Dock identity while Python runs** — the installer now compiles a small Mach-O launcher that stays alive as the ShellFrame app process and spawns the Python UI as a child, so Dock/menu bar identity no longer collapses into Python.
- **Dock pinning is idempotent** — macOS installs now clear stale LaunchServices registrations, re-register the active app, and keep a single ShellFrame Dock item unless `SHELLFRAME_SKIP_DOCK=1` is set.
- **macOS restart now prefers the installed app bundle** — `sfctl restart` reopens `~/Applications/ShellFrame.app` or `/Applications/ShellFrame.app` before falling back to the source-tree template, preventing restarts from losing the fixed Dock identity.

### 修正
- **macOS installer 會乾淨修復非 git 安裝** — zip 複製或 agent 手工建立的安裝目錄會先備份再重新 clone，不再原地轉 git，避免混到舊 app bundle、舊 launcher 與 reset 後仍殘留的 untracked files。
- **ShellFrame.app 會保留 Dock 身份並把 Python 當子程序跑** — installer 現在會編譯一個常駐的 Mach-O launcher，ShellFrame app process 不會被 `exec` 成 Python，Dock/menu bar identity 不再掉成 Python。
- **Dock 固定可重複執行** — macOS 安裝會清掉舊 LaunchServices 註冊、重新註冊目前 app，並維持單一 ShellFrame Dock 項目；可用 `SHELLFRAME_SKIP_DOCK=1` 跳過。
- **macOS restart 會優先使用已安裝的 app bundle** — `sfctl restart` 會先重開 `~/Applications/ShellFrame.app` 或 `/Applications/ShellFrame.app`，再 fallback 到 source tree template，避免重啟後又失去已修好的 Dock identity。

## v0.11.80 (2026-05-25)

### Features
- **LINE bridge supports company webhook forward + poll** — LINE can now run without direct LINE credentials by accepting forwarded webhook payloads and queueing agent replies for an upstream service to poll from `/line/poll`. Direct LINE Messaging API push/reply mode remains available.
- **LINE gets its own per-turn wrapper prompt** — Settings → LINE now has a wrapper prompt textarea so every LINE-originated message can tell the agent to reply in LINE-friendly plain text.
- **Sidebar shows separate TG and LINE routing badges** — TG and LINE badges now render side by side; green means connected and blue means that platform currently routes to that session.

### 功能
- **LINE bridge 支援公司 webhook forward + poll** — LINE 現在可以不直連 LINE credentials，改由公司既有 webhook forward event 進 ShellFrame，再讓上游從 `/line/poll` polling agent 回覆。原本直連 LINE Messaging API push/reply 模式仍保留。
- **LINE 有自己的每則訊息 wrapper prompt** — Settings → LINE 新增 wrapper prompt textarea，每則 LINE 來源訊息都能先提示 agent 用適合 LINE 的純文字格式回覆。
- **Sidebar 分開顯示 TG / LINE routing badge** — TG 與 LINE badge 現在會並排顯示；綠色代表已連線，藍色代表該平台目前指向該 session。

## v0.11.79 (2026-05-25)

### Fixes
- **macOS launchers avoid the Python Dock name** — the `.app`, `run.sh`, and installed `shellframe` launcher now create a venv-local `ShellFrame` symlink to the framework `Python.app` executable and run that symlink. Python still uses ShellFrame's venv, but macOS derives the visible application process name from `ShellFrame` instead of `Python`.

### 修正
- **macOS launcher 避免 Dock 顯示 Python** — `.app`、`run.sh`、install 產生的 `shellframe` launcher 現在會建立 venv-local 的 `ShellFrame` symlink 指到 framework `Python.app` executable，並執行這個 symlink。Python 仍會使用 ShellFrame 的 venv，但 macOS 可見 application process 名稱會取自 `ShellFrame`，不再是 `Python`。

## v0.11.78 (2026-05-25)

### Fixes
- **Python framework bundle metadata is patched before Cocoa startup** — ShellFrame now overrides the runtime main bundle name, display name, identifier, and icon metadata before pywebview creates the Cocoa app. This covers Homebrew framework Python cases where the visible application process would otherwise remain labelled `Python` even after opening `ShellFrame.app`.

### 修正
- **Cocoa 啟動前會修正 Python framework bundle metadata** — ShellFrame 現在會在 pywebview 建立 Cocoa app 前覆寫 runtime main bundle 的 name、display name、identifier 與 icon metadata。這補上 Homebrew framework Python 即使用 `ShellFrame.app` 開啟，可見 application process 仍顯示為 `Python` 的情況。

## v0.11.77 (2026-05-25)

### Fixes
- **Dock identity is forced back to ShellFrame on macOS** — startup now sets the AppKit process name to `ShellFrame` and applies the bundled `shellframe.icns` as the application icon, so Homebrew/Python framework launches do not leave the visible app labelled as Python.

### 修正
- **macOS Dock 身份會主動設回 ShellFrame** — 啟動時會把 AppKit process name 設成 `ShellFrame`，並套用 bundle 內的 `shellframe.icns`，避免 Homebrew/Python framework 啟動後可見 app 顯示成 Python。

## v0.11.76 (2026-05-25)

### Fixes
- **macOS restart preserves the ShellFrame app identity** — restart no longer uses in-place `execv()` on macOS, which caused Dock/Cmd-Tab to show a separate Python icon. It now schedules a small external relauncher that waits for the old PID to exit, then opens `ShellFrame.app` via LaunchServices so the app icon and bundle identity stay correct.

### 修正
- **macOS restart 會保留 ShellFrame app 身份** — restart 不再於 macOS 使用 in-place `execv()`，避免 Dock/Cmd-Tab 變成另一個 Python icon。現在會排一個外部 relauncher，等舊 PID 退出後透過 LaunchServices 開 `ShellFrame.app`，讓 icon 與 bundle identity 維持正確。

## v0.11.75 (2026-05-25)

### Fixes
- **Codex preset no longer persists install-specific paths** — built-in and migrated Codex presets now store `sf-codex ...` instead of an absolute ShellFrame install path. ShellFrame prepends its own `bin/` to the session PATH at launch time, so the wrapper resolves locally without writing another user's filesystem path into `config.json` or the session manifest.
- **Codex wrapper no longer hardcodes one nvm Node version** — `bin/sf-codex` now discovers global npm/nvm installs dynamically before falling back to `codex` on PATH.

### 修正
- **Codex preset 不再持久化特定安裝路徑** — 內建與 migration 後的 Codex preset 會存成 `sf-codex ...`，不再存 ShellFrame 安裝目錄的絕對路徑。ShellFrame 啟動 session 時會把自己的 `bin/` 加進 PATH，所以 wrapper 可在本機解析，不會把某個使用者的 filesystem path 寫進 `config.json` 或 session manifest。
- **Codex wrapper 不再寫死單一 nvm Node 版本** — `bin/sf-codex` 會動態搜尋 global npm/nvm 安裝，再 fallback 到 PATH 上的 `codex`。

## v0.11.74 (2026-05-25)

### Fixes
- **Telegram / LINE settings now follow the UI language** — bridge setup labels, placeholders, help text, status text, and buttons now use ShellFrame's i18n table, so switching Settings language updates the Telegram and LINE configuration panels instead of leaving them in English.

### 修正
- **Telegram / LINE 設定頁會跟著介面語言切換** — bridge 設定的 label、placeholder、說明文字、狀態文字與按鈕已接到 ShellFrame i18n table，切換 Settings 語言時 Telegram / LINE 設定頁不再固定顯示英文。

## v0.11.73 (2026-05-25)

### Fixes
- **Update prompts now respect restart level consistently** — manual About updates, startup updates, and periodic update banners now all check `needs_restart` before considering a UI reload. Python/core updates show a restart prompt; frontend-only updates can still reload the UI.
- **Windows restart no longer silently kills live sessions** — on Windows, ShellFrame cannot preserve live PTY contents across a process restart because there is no tmux-backed detach/reattach. `restart_app()` now blocks when live sessions exist and reports why, preventing update flows from terminating active agent sessions and recreating only empty fresh commands.

### 修正
- **更新提示現在一致依重啟等級處理** — About 手動更新、啟動更新、週期更新 banner 都會先看 `needs_restart`，再決定是否可只 reload UI。Python/core 更新會提示重啟；純前端更新仍可只重載 UI。
- **Windows restart 不再默默殺掉 live sessions** — Windows 沒有 tmux detach/reattach，process restart 無法保留 live PTY 內容。現在 Windows 有 live sessions 時 `restart_app()` 會阻止重啟並說明原因，避免更新流程把正在跑的 agent session 關掉後只重建空的新命令。

## v0.11.72 (2026-05-25)

### Fixes
- **`sfctl restart` now restarts in-place on macOS/Linux** — instead of relying on LaunchServices to create a second app instance, restart now returns the RPC response, detaches from tmux, and `execv()` replaces the current Python process with a fresh `main.py`. This removes the failure mode where ShellFrame closed itself and never came back unless reopened manually.

### 修正
- **`sfctl restart` 在 macOS/Linux 改為原地重啟** — restart 現在會先回傳 RPC、detach tmux，然後用 `execv()` 把目前 Python process 直接替換成新的 `main.py`，不再依賴 LaunchServices 另外建立第二個 app instance。這移除 ShellFrame 自己關掉後沒有回來、必須手動打開的失敗模式。

## v0.11.71 (2026-05-25)

### Fixes
- **Restart now has a verified fallback relaunch** — `sfctl restart` still prefers reopening the ShellFrame `.app`, but now schedules a short watchdog that starts `main.py` directly if LaunchServices accepts `open -n` without actually creating a new ShellFrame process. This prevents the app from closing itself and staying down until it is manually opened again.

### 修正
- **restart 現在有確認式 fallback relaunch** — `sfctl restart` 仍優先重開 ShellFrame `.app`，但會同時排一個短 watchdog；若 LaunchServices 接受 `open -n` 卻沒有真的建立新的 ShellFrame process，就直接啟動 `main.py`。避免 app 自己關掉後沒有成功重開、必須手動再開一次。

## v0.11.70 (2026-05-25)

### Features
- **LINE bridge plugin** — ShellFrame now has a separate `bridge_line.py` plugin that starts a local LINE webhook server, verifies LINE signatures, routes text messages into ShellFrame sessions, and pushes AI replies back through the LINE Messaging API. Settings now includes a LINE tab for Channel access token, Channel secret, allowed LINE user/group IDs, local webhook port/path, public webhook URL, and setup guidance for LINE Developers.

### 功能
- **LINE bridge 外掛** — ShellFrame 新增獨立 `bridge_line.py` plugin，可啟動本機 LINE webhook server、驗證 LINE 簽章、把文字訊息送進 ShellFrame session，並透過 LINE Messaging API 推送 AI 回覆。Settings 現在有 LINE 頁籤，可填 Channel access token、Channel secret、允許的 LINE user/group ID、本機 webhook port/path、公開 webhook URL，並提供 LINE Developers 設定引導。

## v0.11.69 (2026-05-25)

### Fixes
- **Claude/Codex startup trust prompts are auto-accepted in trusted cwd only** — new AI agent tabs launched from ShellFrame now watch startup output and tmux pane history for Claude Code's `Quick safety check` / `Is this a project you trust` prompt and confirm the selected trust option when the launch cwd is the trusted home directory (`/Users/neux`). The handler is startup-only, command-scoped to Claude/Codex, deadline-limited, and disabled as soon as real user input is sent so normal task approval prompts are not answered automatically.

### 修正
- **Claude/Codex 啟動 trust prompt 只在可信 cwd 自動確認** — ShellFrame 新開 AI agent tab 後，會從啟動輸出與 tmux pane history 偵測 Claude Code 的 `Quick safety check` / `Is this a project you trust` prompt；只有啟動 cwd 是可信 home 目錄（`/Users/neux`）時才確認目前選取的 trust 選項。此處理限定啟動期、限定 Claude/Codex 指令、有時間窗，且一旦使用者真的送出輸入就停用，避免自動回答一般任務 approval prompt。

## v0.11.68 (2026-05-24)

### Fixes
- **Backend renames now repaint existing tabs/sidebar immediately** — `sfctl rename` and other non-UI rename paths already persisted labels in `config.json`, but the live web UI only applied labels while attaching brand-new sessions. Existing visible sessions kept showing command basenames like `claude`, `Codex`, or the full Codex wrapper path until a full reload happened. `syncSessionsFromBackend()` now reconciles label and bridge state for already-attached sessions, and `reconnectSession()` accepts the backend label before first render so restored tabs never flash the wrong names.

### 修正
- **後端 rename 會即時重畫既有 tab/sidebar** — `sfctl rename` 等非 UI rename 路徑其實已經把 label 寫進 `config.json`，但前端只在新 session attach 時套用 label；已經顯示中的 session 仍會停在 `claude`、`Codex` 或 Codex wrapper 路徑，直到完整重載。現在 `syncSessionsFromBackend()` 會同步既有 session 的 label / bridge 狀態，`reconnectSession()` 也會在第一次 render 前吃後端 label，重啟恢復時不再先畫錯名字。

## v0.11.67 (2026-05-24)

### Fixes
- **Telegram approval menus for CLI action prompts** — when Claude/Codex shows a numbered approval / action-required prompt, ShellFrame now sends it to Telegram as inline buttons. Tapping a button writes the selected number back into the correct session, so Howard can resolve approvals from mobile without typing raw digits.
- **Red pending-decision dot survives UI re-render** — tabs and sidebar now preserve the `attention` dot class during render, and the screen scanner detects Codex `Action Required` / `Would you like to run...` prompts in addition to Claude-style `❯ 1.` menus.
- **Codex preset path is absolute** — the built-in Codex preset now uses ShellFrame's absolute `bin/sf-codex` path instead of a literal `~`, and existing saved presets / session manifests are normalized on startup.
- **Codex launch flags no longer conflict** — the preset no longer combines `--dangerously-bypass-approvals-and-sandbox` with `-a never`, which newer Codex rejects before the TUI can start.
- **Failed tmux creates no longer leave fake tabs** — if tmux cannot create the backend session, ShellFrame logs the real error and the web UI removes the temporary pane instead of persisting a dead tab.

### 修正
- **TG 顯示 CLI 待決策選單** — Claude/Codex 出現 numbered approval / action-required prompt 時，ShellFrame 會把它轉成 Telegram inline buttons。手機點按鈕就會把對應數字送回正確 session，不用手打 `1/2/3`。
- **紅色待決策點不再被 UI 重繪吃掉** — tab/sidebar render 會保留 `attention` class；screen scanner 也補上 Codex 的 `Action Required` / `Would you like to run...` 偵測。
- **Codex preset 改用絕對路徑** — 內建 Codex preset 不再把 literal `~` 丟給 tmux，既有 saved preset / session manifest 會在啟動時自動修正。
- **Codex 啟動參數不再互斥** — preset 不再同時放 `--dangerously-bypass-approvals-and-sandbox` 和 `-a never`，新版 Codex 會直接拒絕這種組合、導致 TUI 還沒啟動就退出。
- **tmux 建立失敗不再留下假 tab** — backend session 建不起來時會記錄真實錯誤，web UI 也會移除暫時 pane，不再把死掉的 tab 寫進設定。

## v0.11.66 (2026-05-24)

### Fixes
- **Autonomous Claude/Codex presets** — stock AI presets now launch in low-friction execution mode. Claude uses `--permission-mode bypassPermissions --dangerously-skip-permissions`; Codex uses ShellFrame's `bin/sf-codex` wrapper with `--dangerously-bypass-approvals-and-sandbox -a never --search --no-alt-screen`. Existing bare `claude` / `codex` presets are migrated once.
- **Codex launcher wrapper** — added `bin/sf-codex` to prefer the native arm64 Codex binary when the Node wrapper is running under Rosetta/x64 and cannot find the matching optional dependency. Fresh Codex tabs should no longer die with `Missing optional dependency @openai/codex-darwin-x64`.
- **TG restore no longer erases saved prompt** — auto-reconnect still skips sending the initial prompt, but keeps the saved `bridge.initial_prompt` in config instead of overwriting it with an empty string.

### 修正
- **Claude/Codex 預設改成自動執行模式** — 內建 AI presets 會用少確認的啟動參數。Claude 使用 `--permission-mode bypassPermissions --dangerously-skip-permissions`；Codex 使用 ShellFrame 的 `bin/sf-codex` wrapper 並加上 `--dangerously-bypass-approvals-and-sandbox -a never --search --no-alt-screen`。既有裸 `claude` / `codex` preset 會自動遷移一次。
- **Codex launcher wrapper** — 新增 `bin/sf-codex`，當 Node wrapper 在 Rosetta/x64 下跑、但只裝了 arm64 optional dependency 時，優先走原生 arm64 Codex binary。新開 Codex tab 不應再因 `Missing optional dependency @openai/codex-darwin-x64` 掛掉。
- **TG restore 不再清空 prompt 設定** — 自動重連仍然不會重送 initial prompt，但會保留 `bridge.initial_prompt`，不再把 config 覆寫成空字串。

## v0.11.65 (2026-05-24)

### Fixes
- **Reboot-safe ShellFrame session manifest + Telegram restore** — sessions now write a durable `session_manifest` / `session_order` into `~/.config/shellframe/config.json` on create, rename, reorder, bridge toggle, and bridge start. On a full machine reboot, when tmux has no surviving sessions, ShellFrame recreates the same tabs from disk instead of opening empty. tmux sessions also store stable `SF_SID`, so readable tmux auto-slugs no longer change tab identity after restart. The Telegram bridge now auto-restores from saved config even when no session has been restored yet.
- **Atomic config writes** — `config.json` is written through a temp file, fsynced, then atomically replaced to reduce half-written config risk during reboot or crash.

### 修正
- **ShellFrame session manifest + Telegram 重開機恢復** — 建立、命名、排序、bridge 開關、bridge 啟動時，都會把 `session_manifest` / `session_order` 寫進 `~/.config/shellframe/config.json`。完整重開機後如果 tmux session 已不存在，ShellFrame 會從硬碟設定重建同樣 tabs，不會直接空掉。tmux 內也會保存穩定 `SF_SID`，所以可讀的 tmux auto-slug 不會再讓 tab identity 於重啟後跑掉。Telegram bridge 現在即使尚未恢復任何 session，也會從保存設定自動重連。
- **設定檔 atomic write** — `config.json` 改成先寫 temp file、fsync，再 atomic replace，降低重開機或 crash 時半寫入設定的風險。

## v0.11.64 (2026-05-23)

### Features
- **Auto-slug tmux session names** — on the first Enter in a new session, a background thread calls `claude --model claude-haiku-4-5 --print` to summarise the prompt into a 3-5 word slug, then renames the tmux session from `sf_sNN` to `sf_<slug>` (e.g. `sf_fix-ng0203-white-scr`) and syncs the bridge label. Falls back silently to `sf_sNN` if haiku is unavailable or times out. Collision-safe: appends `-2`, `-3`, ... when a session with the same slug already exists.

### 功能
- **tmux session 自動命名** — 新 session 第一個 Enter 觸發背景執行 `claude --model claude-haiku-4-5 --print`，把 prompt 摘成 3-5 字 slug，然後把 tmux session 從 `sf_sNN` 改名為 `sf_<slug>`（例如 `sf_fix-ng0203-white-scr`）並同步更新 bridge label。haiku 無法呼叫或超時時靜默 fallback，維持原 `sf_sNN`。同名衝突自動補 `-2`、`-3` 尾巴。

## v0.11.63 (2026-05-23)

### Fixes
- **False Telegram "Bot conflict" warnings after `sfctl reload`** — hot-reload stops the old bridge and immediately starts the new one. The old poll thread can still be inside Telegram's 30s `getUpdates` long-poll; when the new poller starts, Telegram terminates the old request with HTTP 409. The stopped old thread then interpreted that expected shutdown race as a real external conflict and pushed a scary warning to TG. Fix: after every `getUpdates` return, the poll loop checks `stop_event`/`active` before processing the result, and `stop()` briefly joins the poll thread to shrink the overlap window.

### 修正
- **`sfctl reload` 後誤報 Telegram「Bot conflict」** — hot-reload 會停舊 bridge 並立刻啟新 bridge；舊 poll thread 可能還卡在 Telegram 30 秒 `getUpdates` long-poll。新 poller 一啟動，Telegram 會用 HTTP 409 結束舊 request。已經 stop 的舊 thread 卻把這個正常 shutdown race 當成外部 poller 衝突，推一則嚇人的警告到 TG。修法：每次 `getUpdates` 回來後先檢查 `stop_event` / `active`，已停止就直接退出；`stop()` 也短暫 join poll thread，縮短重疊窗口。

## v0.11.62 (2026-05-23)

### Fixes
- **TG bridge could treat ShellFrame's own TG preamble bullets as Codex replies** — Codex renders long prompts as a first `› ...` line plus indented continuation lines. ShellFrame's TG preamble contains bullet items (`• sfctl reload`, `• sfctl restart`), and `_extract_new_text()` stripped indentation before checking `AI_MARKERS`, so those prompt bullets were misread as assistant output. The result was noisy Telegram replies containing `Straightforward asks...`, `Bash(...)`, `Noodling`, `auto mode on`, and other TUI/tool status. Fix: only accept `•` / `⏺` as AI markers when they start at column 0, and drop known ShellFrame preamble/tool/status lines from fallback previews.

### 修正
- **TG bridge 會把 ShellFrame 自己的 TG preamble bullet 誤判成 Codex 回覆** — Codex 長 prompt 會渲染成第一行 `› ...` 加上縮排 continuation。ShellFrame 的 TG preamble 裡剛好有 bullet（`• sfctl reload`、`• sfctl restart`），而 `_extract_new_text()` 先 strip 縮排再判斷 `AI_MARKERS`，所以 prompt 裡的 bullet 被當成 assistant reply 起點。結果 TG 會送出 `Straightforward asks...`、`Bash(...)`、`Noodling`、`auto mode on` 等雜訊。修法：只有第 0 欄的 `•` / `⏺` 才算 AI marker，並在 fallback preview 補濾 ShellFrame preamble / tool / status 行。

## v0.11.61 (2026-05-22)

### Adds
- **`sfctl history-audit` — self-check tool so I can diagnose "上滾看到不對的歷史" instead of guessing** — Howard's complaint after v0.11.60 didn't fix it: "你自己這個對話就是壞的, 你能不能設計一個自檢的機制, 你可以自己上滾複製文字然後比對". This is exactly that. Bridge now stashes every extracted AI reply on the slot (`last_extracted_text` + 5-deep `recent_extractions` rolling window). New `history_audit(sid)` API gathers four parallel snapshots — (1) `last_extracted` ground truth, (2) `tmux_cleaned` (what the overlay returns), (3) `tmux_raw` (pre-dedup capture-pane), (4) `pyte_history` (independent source) — normalises them, computes `missing_from_overlay` (reply lines absent from overlay = the actual bug class) and `noise_in_overlay` (overlay lines that match neither raw bytes nor reply = dedup residue / cross-tab bleed), dumps everything to `~/.config/shellframe/diag/history-audit_<sid>_<ts>.txt`, and returns a one-line verdict. `sfctl history-audit [sid]` is the CLI entry — defaults to first session. Workflow: reproduce the bad scroll-up, run `sfctl history-audit`, share the dump path, and the next debug pass has measured evidence instead of theories.

### Fixes
- **New Codex sessions sometimes required a UI reload before the conversation appeared** — root cause was a race between backend session creation/output and frontend attachment. `new_session()` can push output and notify `syncSessionsFromBackend` before `openSession()` has registered the returned sid locally; the existing duplicate-pane guard correctly blocked that sync, but if the notify was the only one the frontend saw, the new Codex tab stayed invisible until reload. Fix: `_pushOutput` now buffers output for unknown sids and flushes it once `openSession`/`reconnectSession` attaches the xterm, guarded sync calls are replayed after `_uiCreatingSession` drops, and a 1.5s reconciliation safety net catches non-UI creations from TG or `sfctl`.

### 新增
- **`sfctl history-audit` 自檢工具，讓我能用「實際比對」而不是「猜」來修上滾顯示錯誤的問題** — Howard 在 v0.11.60 沒修好後直接點破：「你自己這個對話就是壞的，你能不能設計一個自檢的機制，你可以自己上滾複製文字然後比對」。就是這個。bridge 把每次提取的 AI 回應存到 slot 上（`last_extracted_text` + 5 筆 rolling window）。新 API `history_audit(sid)` 同時抓四份快照：(1) `last_extracted`（reply 真實內容）、(2) `tmux_cleaned`（overlay 看到的）、(3) `tmux_raw`（去 dedup 前的 capture-pane）、(4) `pyte_history`（獨立來源），正規化後算出 `missing_from_overlay`（reply 有但 overlay 沒有的行 = 真正的 bug class）跟 `noise_in_overlay`（overlay 有但 raw bytes / reply 都對不上的行 = dedup 殘骸或 cross-tab 串味），完整快照存到 `~/.config/shellframe/diag/history-audit_<sid>_<ts>.txt`，回傳一行 verdict。CLI：`sfctl history-audit [sid]`，預設第一個 session。流程：重現上滾爆掉的狀況 → `sfctl history-audit` → 把 dump 路徑丟給我，下一次 debug 就有實證可看。

### 修正
- **新增 Codex session 有時要重載 UI 才看得到對話** — 根因是 backend 建 session / 推 output 跟 frontend attach terminal 之間有 race。`new_session()` 可能在 `openSession()` 還沒把回傳 sid 寫進本地 `sessions` 前就推 output、通知 `syncSessionsFromBackend`；既有 duplicate-pane guard 會正確擋掉這次 sync，但如果這是 frontend 唯一收到的通知，新 Codex tab 就會隱形直到 reload。修法：`_pushOutput` 對未知 sid 先暫存，等 `openSession` / `reconnectSession` attach xterm 後 flush；guard 擋過的 sync 在 `_uiCreatingSession` 歸零後補跑；另外加 1.5s 低成本 reconcile，補住 TG / `sfctl` 這種非 UI 建立 session 的通知漏失。

## v0.11.60 (2026-05-21)

### Fixes
- **Scrolling up after a long single AI reply showed the WRONG history — Howard's exact words: "我單次拿到的回應超過一個畫面 我上滾一定是看到不對的歷史"** — root cause was the scroll-history overlay always reading from `tmux capture-pane`, even when the pane was in the alternate-screen buffer. Claude Code / Codex / vim all enter alt-screen on startup via `\x1b[?1049h`; in that mode tmux's scrollback contains the NORMAL-screen history (whatever was on the terminal BEFORE the TUI took over), NOT the rows that just scrolled out of the alt-screen viewport during the current long reply. So the overlay dutifully returned the previous shell prompt / unrelated session contents — looked like the dedup was broken, but it was a buffer-mismatch problem we'd never noticed because earlier symptoms ("往上滑完全不會動") had pushed us to make tmux primary in v0.11.40. Fix: `get_clean_history` now probes `#{alternate_on}` first. In alt-screen mode it serves from pyte's HistoryScreen (the bridge feeds every PTY byte through pyte, including alt-screen line-feeds, so its `history.top` deque actually has the recent reply text). Outside alt-screen tmux remains primary — its 10000-row scrollback dwarfs pyte's 3000-row cap and carries colour. Response gains a `source` field surfaced in the overlay header so future "wrong history" reports tell us which buffer to investigate.

### 修正
- **單次 AI 回應超過一個畫面後上滾看到的是「不對的歷史」** — 根因是 scroll-history overlay 一律用 `tmux capture-pane`，沒有偵測 pane 是不是在 alternate-screen buffer。Claude Code / Codex / vim 啟動時都會切到 alt-screen（`\x1b[?1049h`）；在那個模式下 tmux 的 scrollback 是 alt-screen **進入前**的 normal-screen 歷史，**不是**當前長 reply 滾出視窗的內容。所以 overlay 老老實實回傳「之前 shell prompt / 上一段對話」 — 看起來像 dedup 壞掉，其實是 buffer 拿錯了。先前 v0.11.40 為了修「往上滑完全不會動」把 tmux 改成 primary，當時沒考慮到 alt-screen case。修法：`get_clean_history` 先用 `#{alternate_on}` 偵測；alt-screen 時改吃 pyte 的 HistoryScreen（bridge 把每個 PTY byte 都餵給 pyte，alt-screen 的 line-feed 也會推進 `history.top`，所以那裡才是當前 reply 真正的上半段）。非 alt-screen 維持 tmux（10000 行容量比 pyte 3000 行大、有顏色）。回傳多帶 `source` 欄位顯示在 overlay header 上，下次再看到不對直接看標籤就知道是哪邊出問題。

## v0.11.59 (2026-05-21)

### Fixes
- **TG typing indicator felt unstable — bubble kept blanking out mid-reply even though Claude was clearly still working** — root cause was `_send_typing` being called *inside* `slot.output_lock` on every 0.5s flush tick, with `tg_api`'s default 35s urlopen timeout. Any slow `sendChatAction` round-trip held the lock long enough to backpressure `feed_output` (PTY ingest takes the same lock), which then delayed the next typing refresh past TG's 5s auto-clear → bubble disappears, user thinks the bridge is dead. Compounded by zero throttle (10× more API calls than needed for a 5s-TTL indicator) and sequential per-uid sends that stacked latency across multiple watchers. Fix: `_send_typing` moved out of `output_lock`; internal 4s throttle keyed off `slot.last_typing_ts`; each recipient gets its own fire-and-forget thread with a 3s timeout so a single slow chat can't cascade. `tg_api` now accepts an optional `timeout=` kwarg — long-poll `getUpdates` still uses 35s, fire-and-forget calls pass 3.

### 修正
- **TG typing 動畫飄忽不定，Claude 還在打字氣泡卻已消失** — 根因是 `_send_typing` 被擺在 `slot.output_lock` 內、每 0.5s flush tick 都同步呼叫，而 `tg_api` 預設 urlopen timeout 35s。`sendChatAction` 一慢就把 lock 壓住，`feed_output`（PTY 進來也要拿同一把鎖）跟著卡 → 下一次 typing 來不及刷 → TG 5s 後氣泡消失 → 看起來像 bridge 死了。再加上完全沒節流（5s TTL 的指示器被 0.5s 打一次，10× 浪費），多個 watcher 又是序列送 → latency 疊起來。修法：`_send_typing` 抽出 `output_lock`；slot 內建 4s 節流（`last_typing_ts`）；每個收件人各自開背景 thread + 3s timeout，單一慢 chat 不會拖累其他人。`tg_api` 加上 `timeout=` 參數，長輪詢 `getUpdates` 仍走 35s，fire-and-forget 一律 3s。

## v0.11.58 (2026-05-15)

### Fixes
- **Multi-file paste from Finder still dropped — Howard's repro: 2 PDFs → only 1 reached Claude, with 0-byte clipboard_*.pdf files on disk** — the Cmd+V handler's order of operations was wrong. v0.11.56 fixed filename collisions but the real Finder-paste path was being misrouted: `clipboardData.items` reports kind='file' for Finder-copied items, BUT WKWebView doesn't materialize NSPasteboard file URLs into byte data, so `item.getAsFile()` returns 0-byte stub blobs. The in-browser fileBlobs branch happily wrote those empty data URLs to disk and returned, never reaching the osascript-backed `get_clipboard_files()` fallback that would have found the real file paths. Swapped branch order: osascript path now comes FIRST for non-image files; in-browser fileBlobs is the fallback and now skips `blob.size === 0` blobs so any leftover Finder stubs are ignored.

### Changes
- **`/fetch` no longer pins the reply** — Howard: pinned banner in chat is noisy and rarely useful. Message is still sent normally; user scrolls if they need to find it. Updated slash-menu description and `/help` text accordingly.

### 修正
- **Finder 多檔複製貼上還是只有一張進 Claude — Howard 實測：2 個 PDF 變成 0 bytes 落地、只有一個進 Claude** — Cmd+V 路徑的分支順序錯了。v0.11.56 修好檔名碰撞，但 Finder 複製的真實路徑被誤導：`clipboardData.items` 對 Finder 複製的檔案會回 kind='file'，但 WKWebView 不會把 NSPasteboard 的 file URL materialize 成 byte data，`item.getAsFile()` 拿到的是 0-byte stub blob。in-browser fileBlobs branch 把空的 data URL 寫到磁碟然後 return，永遠走不到 `get_clipboard_files()` (osascript) 那條真正能拿到 file path 的 fallback。把 branch 順序交換：osascript 路徑放最前面（非圖片時走得到），in-browser fileBlobs 降為 fallback 且加 `blob.size === 0` 過濾，殘留的 Finder stub blob 直接忽略。

### 變更
- **`/fetch` 不再 pin 訊息** — Howard 回報置頂訊息很吵、用處不大。訊息照常送出，要找的話自己往上滾。同步更新 slash 選單敘述跟 `/help` 文字。

## v0.11.57 (2026-05-15)

### Fixes
- **TG long-poll felt unstable but the log was useless for diagnosing it** — `_poll_loop`'s exception handler was `except Exception: time.sleep(5)` with zero logging. Any wifi blip / TLS reset / sleep-wake event silently produced 5+ seconds of dead bridge time and left no trace. Worse, the per-flush debug path dumped the full screen on every empty poll across 8 sessions, so the 1MB log cap rotated every few minutes and real signals got buried. Fix: log exceptions with type+message (first 3 verbosely, then every 10th to coalesce sustained outages); exponential backoff 1→2→4→8→15s instead of fixed 5s so transient blips recover ~3× faster; emit a `[poll] recovered after N consecutive errors` line on the comeback. Empty-flush log entries dropped — only log when there's actual content to forward.

### Changes
- **TG slash menu now puts `/1`, `/2`, ... session switchers BEFORE the generic ops** — Howard reported the picker is mostly used to swap sessions on mobile; bumped numbered switchers to the top so they're thumb-reachable. Generic ops (`/fetch`, `/list`, `/restart`, `/update`, `/new`, `/close`) move below.

### 修正
- **TG long-poll 感覺不穩但 log 完全沒線索** — `_poll_loop` 的 exception 處理是 `except Exception: time.sleep(5)`，完全沒 log。任何 wifi 抖動／TLS reset／sleep-wake 都靜默吞掉 5+ 秒、毫無痕跡。更糟的是每次 flush 對 8 個 session 都 dump 整個 screen，1MB log 上限每幾分鐘就 rotate 一次、真實訊號全被淹沒。修法：exception 路徑 log 出 type+message（前 3 次完整、之後每 10 次一筆避免風暴），改成指數 backoff 1→2→4→8→15s 取代固定 5s，連續錯誤後復原時 emit `[poll] recovered after N consecutive errors`。空 flush 不再寫 log，只在有實際內容要 forward 時寫。

### 變更
- **TG slash 選單把 `/1`、`/2`、... session 切換放最前面** — Howard 回報手機開選單主要是切 session；把數字命令往前推到拇指可達範圍。其他通用命令（`/fetch`、`/list`、`/restart`、`/update`、`/new`、`/close`）移到後面。

## v0.11.56 (2026-05-14)

### Fixes
- **Multi-image paste only attached the LAST image — Howard saw a single chip when pasting 3** — `save_image()` / `save_file_from_clipboard()` built filenames at second precision (`%Y%m%d_%H%M%S`). The Cmd+V handler's image loop runs each `save_image` call back-to-back; three images pasted in the same second got identical filenames, each rewrote the previous on disk, and `save_image` returned the same path three times. The JS `attachFile()` dedup (`s.attachments.some(a => a.path === path)`) then collapsed the three identical paths into one chip → only one `[image #N]` reached Claude / Codex. Bumped to microsecond precision (`%Y%m%d_%H%M%S_%f`); verified 5 back-to-back calls now produce 5 distinct filenames. No other change — the iteration / chip-rendering / PTY-write logic was always correct; the collision was the only thing dropping images on the floor.

### 修正
- **多張圖片貼上實際只附第一張 — Howard 看到只有一個 chip 以為其他沒貼進去** — `save_image()` 與 `save_file_from_clipboard()` 用秒精度的時間戳當檔名（`%Y%m%d_%H%M%S`）。Cmd+V 的 image loop 對每張 blob 連續呼叫 `save_image`，同一秒內三張圖會拿到一樣的檔名，磁碟上後者覆蓋前者、`save_image` 三次都回傳同一個 path。JS 端 `attachFile()` 的 dedup（`s.attachments.some(a => a.path === path)`）就把三個一樣的 path 縮成一個 chip → 只送一個 `[image #N]` 給 Claude / Codex 看。改用微秒精度（`%Y%m%d_%H%M%S_%f`）；實測五次 back-to-back 拿到五個不同檔名。其他邏輯（iteration、chip 渲染、寫入 PTY）原本就對，只有檔名碰撞這一處在丟圖。

## v0.11.55 (2026-05-14)

### Changes
- **Trim Telegram bot slash-command menu — drop `/help`, `/pause`, `/resume`, `/reload`** — Howard reported the picker had too many entries that he never used. Removed those four from `_set_bot_commands()`'s registered list so the TG client's command menu only shows the ones he actually uses (`/fetch`, `/list`, `/restart`, `/update`, `/new`, `/close`, plus the numbered `/1` `/2` ... session switchers). Handlers stay in place — typing the commands by hand still works, they just aren't suggested.

### 變更
- **精簡 Telegram bot 的 slash 選單 — 拿掉 `/help`、`/pause`、`/resume`、`/reload`** — Howard 回報選單太雜、這四個用不到。從 `_set_bot_commands()` 註冊清單移除，TG 選單只剩會用的（`/fetch`、`/list`、`/restart`、`/update`、`/new`、`/close`，加上 `/1` `/2` ... session 切換）。Handler 還在 — 手動打還能跑，只是不會主動推薦。

## v0.11.54 (2026-05-13)

### Fixes
- **Scroll-up history overlay showed the wrong frame's context — Howard saw user-message followed by Claude Code splash banner instead of the actual reply** — `Api.get_clean_history()`'s two dedup gates (CJK-heavy + ≥3× generic-line repeat) kept the FIRST occurrence of each duplicated line. Claude Code's TUI re-renders the whole viewport on every state change (splash → conversation, scroll-up, every stream tick), so tmux captures the SAME line in multiple frames with different surrounding context. For Howard's `sf_s20`, the user's prompt `❯ 我有傳訊息了 你可以去log 看一下...` appeared at line 795 (frame T1: followed by Claude Code v2.1.112 splash + Write tool call) AND at line 1858 (frame T2: followed by the real Bash(ssh) log query and `⏺ Log 證據（你訊息進來了 ✓）`). Keep-first deduper picked T1 — the overlay showed the splash-banner version and dropped the canonical T2 reply, hence the "scroll up doesn't connect to the live view" complaint Howard's been raising for releases. Switched both gates to keep-LAST: precompute `last_idx[key]` over the cleaned list, emit each line only at its last index. The most-recent frame is the canonical one (final stream tick, post-redraw state); earlier instances are partial / mis-contextualized. Prefix-collapse pass (Pass 1) unchanged — it already keeps the longest of consecutive prefix-duplicates.

### 修正
- **往上滾的歷史 overlay 抓到錯誤 frame 的 context — Howard 看到的是 user 訊息接 Claude Code 啟動 banner，而不是真實的回覆** — `Api.get_clean_history()` 的兩個 dedup gate（CJK-heavy 行、≥3× 重複行）原本保留**第一次**出現。Claude Code 的 TUI 每次狀態變化都會整個 viewport 重繪（splash → 對話、scroll up、每個 stream tick），tmux 把每個 frame 都記下來、同一行會在多個 frame 出現但 context 不同。Howard 的 `sf_s20`：`❯ 我有傳訊息了 你可以去log 看一下...` 同時出現在 line 795（T1 frame：後接 Claude Code v2.1.112 啟動 banner + Write 工具呼叫）跟 line 1858（T2 frame：後接真實的 Bash(ssh) log 查詢 + `⏺ Log 證據（你訊息進來了 ✓）`）。keep-first 選了 T1 → overlay 顯示 splash banner 版本、把正確的 T2 回覆丟掉，所以滾上去「銜接不上」。改成 keep-LAST：先建 `last_idx[key]` map，最後一次出現的 index 才 emit。最新 frame 是最終狀態（stream 結束、重繪完）；早期 instance 通常是 partial 或 context 不對的快照。Pass 1 prefix-collapse 不動（已經正確保留兩個連續 prefix-dup 中較長的那個）。

## v0.11.53 (2026-05-11)

### Fixes
- **Enter silently lost after `paste image → type Chinese → Enter to confirm IME candidate → Enter to submit`** — Howard reported the second Enter wouldn't submit. Root cause: the document-level Enter safety net (`_ensurePasteEnterListener`) was gated on `_refocusGuardId !== null`, but the refocus guard auto-clears after 240ms of stable focus. So once the user spent time typing Chinese, the guard was long gone. When IME confirmation fires, WKWebView briefly blurs the xterm helper textarea — and the next Enter lands on body / image-bar with no listener to catch it. Made the safety net always-on (renamed `_ensureStealEnterListener`, attached at init); added `e.isComposing || keyCode === 229` guard so IME-commit Enter isn't intercepted (it must reach the IME naturally to commit the candidate). All other guards stay (Enter only, no modifiers, `_isStealableFocus` skips real form controls / xterm textarea), so normal typing is untouched.

### 修正
- **「貼圖 → 打中文 → Enter 確認選字 → Enter 送出」第二個 Enter 默默丟失** — Howard 回報送不出去。根因：document-level 的 Enter 兜底 listener（`_ensurePasteEnterListener`）被 `_refocusGuardId !== null` 守住，但 refocus guard 在 focus 穩定 240ms 後就自動清掉。使用者打中文這段時間 guard 早就 null 了，IME 確認選字時 WKWebView 短暫 blur xterm helper textarea，下一個 Enter 落到 body / image-bar 又沒 listener 接 → 靜默丟失。把兜底 listener 改成 always-on（更名 `_ensureStealEnterListener`、init 時掛上）；新增 `e.isComposing || keyCode === 229` 守衛，IME-commit Enter 不偷（要讓它自然送到 IME 完成候選確認）。其他守衛保留（只接 Enter、不接修飾鍵、`_isStealableFocus` 跳過真表單與 xterm textarea），正常打字不受影響。

## v0.11.52 (2026-05-11)

### Fixes
- **Codex preset silently launched without `--full-auto` because macOS smart-substitution turned `--` into `—`** — Howard's saved Codex preset had `"cmd": "codex —full-auto"` (em-dash, U+2014). `shlex.split` preserved the em-dash, codex didn't recognize it as a flag, and the token landed in the prompt textarea as a queued user message instead of activating full-auto mode. Added `_normalize_dashes()` — collapses em-dash / en-dash at token boundaries back to `--`. Applied at three points: (1) `save_preset` so new presets typed in the UI get corrected on save, (2) `new_session` so existing dirty configs auto-correct on spawn, (3) a one-shot `_dash_normalized_v1` migration in `load_config` that rewrites all stored presets the first time the new version loads. Howard's live config was migrated to `codex --full-auto`.
- **Right-click paste of a clipboard image (e.g. `⌃⇧⌘4` screenshot) silently did nothing** — `_rightClickPaste()` relied on `navigator.clipboard.read()`, but WKWebView gates clipboard reads behind permission AND doesn't expose `image/*` MIME types reliably, so the try/catch swallowed the failure with no user feedback. Added backend `read_clipboard_image()` that reads NSPasteboard directly via PyObjC — prefers `NSPasteboardTypePNG`, falls back to `NSPasteboardTypeTIFF` and re-encodes through `NSBitmapImageRep` (macOS screenshots land on the pasteboard as TIFF, not PNG). UI now falls back to this backend when the browser API yields nothing. Cmd+V was already fine — its `e.clipboardData.items` comes from a trusted user gesture and exposes images correctly; only right-click was broken.

### 修正
- **Codex preset 默默啟動但 `--full-auto` 沒生效，因為 macOS 智慧型替換把 `--` 換成 `—`** — Howard 存的 Codex preset cmd 是 `codex —full-auto`（em-dash, U+2014），`shlex.split` 保留 em-dash、codex 不認得它是 flag、整個 token 變成排隊送進 prompt 的 user message，full-auto 模式根本沒被啟動。新增 `_normalize_dashes()` — 把 token 邊界的 em-dash / en-dash 還原成 `--`。三個地方套用：(1) `save_preset` 寫入時、(2) `new_session` 啟動時（既存髒 config 自動修）、(3) `load_config` 一次性 `_dash_normalized_v1` migration（首次跑新版會掃過所有 preset）。Howard 的 live config 已被遷移成 `codex --full-auto`。
- **右鍵貼上剪貼簿圖片（例如 `⌃⇧⌘4` 截圖）默默無反應** — `_rightClickPaste()` 仰賴 `navigator.clipboard.read()`，但 WKWebView 對 clipboard 讀取有 permission gate 且不會穩定暴露 `image/*` MIME，try/catch 把錯誤吞掉、使用者完全沒回饋。新增後端 `read_clipboard_image()` 直接走 PyObjC 讀 NSPasteboard — 優先抓 `NSPasteboardTypePNG`、抓不到再 fallback `NSPasteboardTypeTIFF` 經 `NSBitmapImageRep` 重編碼成 PNG（macOS 截圖在 pasteboard 上是 TIFF 不是 PNG）。前端在 browser API 拿空時 fallback 到這條後端路徑。Cmd+V 不受影響 —— 它的 `e.clipboardData.items` 來自 trusted user gesture、image blob 直接可拿，問題只在右鍵。

## v0.11.51 (2026-05-11)

### Changes
- **Esc in Codex sessions now clears the composer instead of cancelling the agent** — Codex's native Esc cancels the current run, but the muscle memory most users want is "wipe the half-typed prompt." Codex is now treated like a plain terminal for the Esc keybind: ShellFrame intercepts Esc and writes `Ctrl+U` (line-kill), which Codex's textarea reads as "clear input." Other AI CLIs (Claude, aider, gemini, ...) keep their native Esc behavior.

### 變更
- **Codex session 的 Esc 改成清空輸入框，不再取消 agent** — Codex 原生 Esc 會中斷目前的執行，但大多數人按 Esc 是想清掉「打到一半的提示詞」。現在 ShellFrame 把 Codex 視為一般 terminal 攔截 Esc，改送 `Ctrl+U`（line-kill），Codex 的輸入區會清空。其他 AI CLI（Claude、aider、gemini…）維持原生 Esc 行為。

## v0.11.50 (2026-05-09)

### Fixes
- **TG bridge spammed false "macOS popup detected (loginwindow)" stall warnings** — Howard reported polling looked unstable, but the polling loop was healthy; the noise came from `_detect_blocking_popup()`. `_POPUP_OWNERS` listed `loginwindow`, which is always running and frequently owns on-screen system-management windows during normal operation (sleep/wake transitions, screen-lock manager, Touch ID prep). Any long Claude response (>25s silent) tripped `_warn_stalled` → `CGWindowListCopyWindowInfo` matched a loginwindow-owned window → user got a TG warning telling them to dismiss a popup that wasn't there. Removed `loginwindow` from the popup-owner set; the remaining owners (`UserNotificationCenter`, `CoreServicesUIAgent`, `SecurityAgent`, `universalAccessAuthWarn`) all spawn on-demand for real auth/permission dialogs. If the Mac is genuinely lock-screened, ShellFrame can't be brought to front anyway, so detecting it had no upside.

### 修正
- **TG bridge 一直噴假的「macOS popup detected (loginwindow)」stall 警告** — Howard 以為 polling 不穩，但 poll loop 本身健康；噪音是 `_detect_blocking_popup()` 出來的。`_POPUP_OWNERS` 把 `loginwindow` 列為「擋路 popup owner」，但 loginwindow 是 macOS 永遠在跑的 process，正常使用中常持有 on-screen 系統管理 window（睡眠/喚醒切換、鎖螢幕管理、Touch ID prep）。Claude 任何 >25s 沒輸出的長思考 → `_warn_stalled` 觸發 → `CGWindowListCopyWindowInfo` 命中 loginwindow → user 收到「快去關掉不存在的 popup」TG 警告。把 `loginwindow` 從 popup owner 名單移除，留下的（`UserNotificationCenter` / `CoreServicesUIAgent` / `SecurityAgent` / `universalAccessAuthWarn`）都是按需 spawn 的真權限/認證 dialog。Mac 真的被鎖了 ShellFrame 也召不回前景，偵測它沒意義。

## v0.11.49 (2026-05-03)

### Fixes
- **Global hotkey still dead after v0.11.48 — second TCC identity leak via Apple's Python framework** — forcing `arch -arm64` in the launcher (v0.11.48) flipped the live process arch to ARM64 but `lsappinfo` still reported `bundleID="com.apple.python3"` instead of `com.h2ocloud.shellframe`. Root cause: Apple's bundled Python (Xcode CLT, `/usr/bin/python3`, `/Library/Frameworks/Python.framework`) self-rewraps at runtime into `Python.app` to grant itself a GUI/Dock identity. Once the kernel knows the executable image lives inside `Python.app/Contents/MacOS/Python`, LaunchServices binds the process to `com.apple.python3` regardless of who exec'd it — TCC then refuses Accessibility (which was granted to `com.h2ocloud.shellframe`) and `NSEvent.addGlobalMonitorForEventsMatchingMask_handler_` returns nil. Symptom: ⌃⌥Space could hide a foregrounded ShellFrame (local monitor needs no Accessibility) but couldn't summon it from the background.

  Homebrew's Python ships as a plain framework binary that does NOT self-wrap, so when the .app launcher exec's it the python process inherits ShellFrame.app's bundle identity from LaunchServices and TCC behaves correctly.

  Fixes layered into `install.sh` and `run.sh` so this doesn't keep biting on cross-device installs:
  - **`install.sh`** explicitly picks `/opt/homebrew/bin/python3` (or `/usr/local/bin/python3` on Intel) and rejects any candidate that resolves under `/Xcode.app/`, `/usr/bin/python3`, or `/Library/Frameworks/Python.framework/`. If no non-Apple python is present it `brew install`s `python@3.14`.
  - **Auto-rebuilds existing `.venv`** if `.venv/bin/python` resolves into Apple's Python — the broken venv is moved aside as `.venv.applepython-bak.<ts>`.
  - **`run.sh`** uses the same selection logic so manual `bash run.sh` paths don't recreate the bad venv.

### 修正
- **v0.11.48 之後熱鍵還是死的 — Apple Python framework 又是另一層 TCC 身份漏 leak** — 上一版強制 `arch -arm64` 把 process 換成 ARM64，但 `lsappinfo` 報的 `bundleID` 仍是 `com.apple.python3`，不是 `com.h2ocloud.shellframe`。根因：Apple 自帶的 Python（Xcode CLT、`/usr/bin/python3`、`/Library/Frameworks/Python.framework`）在啟動時**會把自己重新 wrap 成 `Python.app`** 以取得 GUI/Dock 身份。一旦 kernel 看到 executable image 位於 `Python.app/Contents/MacOS/Python`，LaunchServices 就把 process 綁定為 `com.apple.python3`，不管誰 exec 它 — TCC 給 `com.h2ocloud.shellframe` 的 Accessibility 對它無效，`NSEvent.addGlobalMonitorForEventsMatchingMask_handler_` 直接回 nil。症狀：⌃⌥Space 可以把前景的 ShellFrame 隱藏（local monitor 不需 Accessibility）但背景叫不回來。

  Homebrew 的 Python 是純 framework binary，不做 self-wrap，所以從 .app launcher exec 它時 process 會繼承 ShellFrame.app 的 bundle identity，TCC 行為正常。

  修法寫進 `install.sh` 跟 `run.sh`，讓跨裝置安裝時不會再踩同個雷：
  - **`install.sh`** 明確選 `/opt/homebrew/bin/python3`（Intel 上是 `/usr/local/bin/python3`），剔除任何解析到 `/Xcode.app/`、`/usr/bin/python3`、`/Library/Frameworks/Python.framework/` 的候選。本機沒有非 Apple python 就 `brew install python@3.14`。
  - **既存 `.venv` 偵測自動重建**：若 `.venv/bin/python` 解析到 Apple Python，舊 venv 改名 `.venv.applepython-bak.<ts>` 後重建。
  - **`run.sh`** 用同一份選擇邏輯，避免手動 `bash run.sh` 又把壞 venv 建回來。

## v0.11.48 (2026-05-03)

### Fixes
- **Global hotkey silently dead when launched from a Rosetta-translated parent shell** — root cause was identity-leak via process arch inheritance, not anything in the hotkey code itself. Howard's interactive shell runs x86_64 under Rosetta; when the `ShellFrame.app` bash launcher exec'd `.venv/bin/python` it inherited that arch, so the live process ran as **x86_64** with kernel-reported bundle id `com.apple.python3` (not `com.h2ocloud.shellframe`). TCC scopes Accessibility permission per code identity → `NSEvent.addGlobalMonitorForEventsMatchingMask_handler_` returns nil → `⌃⌥Space` only worked when ShellFrame was already foreground (local monitor doesn't need Accessibility), and "summon from background" silently no-op'd. Symptom: 「快捷鍵叫不出來」. Fix: all three launchers (`ShellFrame.app/Contents/MacOS/shellframe`, `run.sh`, the `~/.local/bin/shellframe` written by `install.sh`) now detect Apple Silicon via `sysctl hw.optional.arm64` and prepend `arch -arm64` to the python exec, breaking inheritance and stabilising the TCC subject as the .app bundle.

### 修正
- **從 Rosetta shell 啟動時 ⌃⌥Space 全域熱鍵默默失效** — 根因是 process arch 繼承造成 TCC 身份漂移，hotkey code 本身沒問題。Howard 的互動 shell 跑在 Rosetta（x86_64），`ShellFrame.app` 的 bash launcher exec `.venv/bin/python` 時 arch 繼承過去，結果整個 process 以 **x86_64** 執行，kernel 看到的 bundle id 變成 `com.apple.python3` 而不是 `com.h2ocloud.shellframe`。TCC 的 Accessibility 權限按 code identity 綁定 → `NSEvent.addGlobalMonitorForEventsMatchingMask_handler_` 直接 return nil → `⌃⌥Space` 只有在 ShellFrame 已經在前景時還能動（local monitor 不需 Accessibility），背景叫回視窗就完全沒反應。對應症狀：「快捷鍵叫不出來」。修法：三個 launcher（`ShellFrame.app/Contents/MacOS/shellframe`、`run.sh`、`install.sh` 寫到 `~/.local/bin/shellframe` 的那份）統統用 `sysctl hw.optional.arm64` 偵測 Apple Silicon，命中就 `exec arch -arm64 .venv/bin/python …`，斷掉 arch 繼承鏈，讓 TCC subject 穩定回到 .app bundle。

## v0.11.47 (2026-05-01)

### Fixes
- **Hotkey instability — three independent bugs in the global ⌃⌥Space path**:
  - **Rate-limit ate legitimate user presses**: `_toggle_visibility`'s summon branch shared the 2-second `_last_summon_ts` floor with the SIGUSR1 path. The hide branch never reset that timestamp, so the common flow "summon → use → hide → resummon" within 2s silently dropped the second press — hotkey looked dead. Removed the throttle from the user-keypress path; the SIGUSR1 / LaunchServices feedback loop the throttle was meant to break is still gated inside `_summon_self_main_thread`, where spurious sources actually originate.
  - **Key repeat triggered toggle storms**: `_matches()` didn't filter `event.isARepeat()`, so holding ⌃⌥Space for a fraction of a second produced multiple KeyDowns and the window flickered hide↔summon, ending in whichever state the last repeat left. Added `if event.isARepeat(): return False`.
  - **`_move_windows_to_mouse_screen` warped invisible helper windows**: it iterated `NSApp.windows()` blindly, including pywebview's hidden panels/utility windows, calling `setFrameOrigin_` on them. Filter `w.isVisible()` first.

### 修正
- **快捷鍵不穩 — `⌃⌥Space` 路徑上三個獨立 bug**:
  - **Rate-limit 把使用者按鍵吃掉**: `_toggle_visibility` summon 分支跟 SIGUSR1 路徑共用 2 秒節流，而 hide 分支不會重置 `_last_summon_ts`。最常見情境「summon → 用一下 → hide → 馬上再 summon」在 2 秒內第二下被默默丟掉，看起來像快捷鍵壞了。把使用者按鍵這條的節流拿掉；原本要擋的 SIGUSR1 / LaunchServices 反饋迴圈在 `_summon_self_main_thread` 自己還有節流。
  - **Key repeat 連發造成 toggle 抖動**: `_matches()` 沒過 `event.isARepeat()`，輕輕按住 ⌃⌥Space 就會連發多個 KeyDown，視窗在 hide↔summon 之間翻轉，最終狀態看心情。加上 `if event.isARepeat(): return False`。
  - **`_move_windows_to_mouse_screen` 連隱藏視窗也搬**: 直接掃 `NSApp.windows()` 全集，包括 pywebview 自己的 hidden panel，會對它們呼叫 `setFrameOrigin_`。先濾掉 `w.isVisible() == False`。

## v0.11.46 (2026-04-29)

### Fixes
- **Enter died after applying a large paste through the confirm bar** — flow that was broken: paste big text → confirm bar opens → Enter (apply) → bracketed paste lands in PTY → Claude Code compresses it to "[Pasted text #N]" → user presses Enter to submit → nothing happens. The confirm bar's Enter handler hid the bar but never restored focus to the xterm textarea, and the original paste event's focus guard had already expired by the time the bar opened. Now the apply path immediately force-focuses the textarea AND re-arms `_refocusActive()` (3-second guard + Enter-forwarder), so the post-apply Enter actually submits. Esc cancel does the same so the user can keep typing without clicking back into the terminal.

### 修正
- **大文案經 confirm bar 套用後，下一個 Enter 失效** — 重現流程：貼大文案 → confirm bar 出現 → Enter 套用 → bracketed paste 進 PTY → Claude Code 壓成「[Pasted text #N]」→ 想再按 Enter 送出 → 沒反應。confirm bar 的 Enter handler 把 bar 隱藏了，但**沒把 focus 拉回 xterm textarea**；原本 paste event 啟動的 focus guard 也早就到期。現在套用路徑會強制把 focus 推回 textarea，並**重新 arm `_refocusActive()`**（3 秒 guard + Enter forwarder），第二個 Enter 真的會送出。Esc 取消也一樣補 refocus，user 不用再點對話框就能繼續打字。

## v0.11.45 (2026-04-29)

### Fixes
- **Window kept popping to the front without the hotkey** — three layered guards added to suppress spurious activations from background sources we couldn't fully attribute (LaunchServices launch attempts, Dock animation, paste-driven NSWorkspace events…):
  - **Removed `open -b com.h2ocloud.shellframe` belt-and-braces** from both `_ensure_single_instance` and `_toggle_visibility`. The LaunchServices `open -b` form treats this as a relaunch intent that can come back round to spawn another shellframe → re-enters `_ensure_single_instance` → re-sends SIGUSR1 → re-activates… SIGUSR1 alone is the canonical wake path; if it fails to land, we'd rather drop one summon than risk a feedback loop.
  - **Rate-limit on summon paths** — both `_summon_self_main_thread` (SIGUSR1 handler) and `_toggle_visibility`'s summon branch share `_last_summon_ts` with a 2-second floor. A real user press fires once anyway; runaway sources are throttled to one activation per 2s.
  - **Skip activate when already active** — `_summon_self_main_thread` now no-ops if `NSApp.isActive() && !isHidden()`. A background SIGUSR1 while the user already sees the window won't repaint or warp it.

### 修正
- **沒按熱鍵視窗也一直跳出來蓋板** — 三層防護一起加，截斷各種背景來源（LaunchServices 重啟意圖、Dock 動畫、paste 觸發的 NSWorkspace 事件…）造成的非預期 activate：
  - **移除 `_ensure_single_instance` 跟 `_toggle_visibility` 兩處的 `open -b com.h2ocloud.shellframe` 保險**。`open -b` 對 LaunchServices 是一個「重啟意圖」，可能繞回去 spawn 另一個 shellframe → 進入 `_ensure_single_instance` → 再送 SIGUSR1 → 再 activate… 反饋循環。SIGUSR1 自己是夠用的喚醒路徑；它若沒到，寧可漏掉一次 summon 也不要踩 loop。
  - **summon 加 rate-limit** — `_summon_self_main_thread`（SIGUSR1）跟 `_toggle_visibility` summon 共用 `_last_summon_ts`，2 秒內第二次 summon 會被擋。真正的使用者按鍵只觸發一次，所以無感；失控來源會被節流。
  - **已在前景就跳過 activate** — `_summon_self_main_thread` 進入時若 `NSApp.isActive() && !isHidden()` 直接 return，不會再做 move 或 activate。背景 SIGUSR1 在使用者已經看到視窗時完全靜音。

## v0.11.44 (2026-04-29)

### New Features
- **Global hotkey summons shellframe to the cursor's monitor** — `⌃⌥Space` used to bring the window forward on whichever display it last lived on, regardless of where the user's mouse pointer was. With multiple monitors that meant the user often had to hop displays to find it. Now the hotkey first reads `NSEvent.mouseLocation()`, finds the matching `NSScreen`, and re-centres every shellframe NSWindow on that screen before activate fires. The MoveToActiveSpace flag still handles Spaces, so the combined effect is "wherever you are physically AND virtually, that's where the window appears." Both summon paths covered: in-process hotkey toggle (`_toggle_visibility`) and signal-driven duplicate-launch summon (`_summon_self_main_thread`).

### 新功能
- **熱鍵把 shellframe 叫到滑鼠所在那塊螢幕** — `⌃⌥Space` 之前是視窗在哪就在那邊冒出來，多螢幕設定下要自己跳螢幕找它。現在熱鍵先讀 `NSEvent.mouseLocation()` 找出滑鼠在哪個 `NSScreen`，把所有 shellframe NSWindow 重新置中到那塊螢幕上之後才 activate。MoveToActiveSpace 還是負責 Space 軸，加總起來就是「人在哪、視窗在哪」。兩條 summon 路徑都套：in-process 熱鍵切換（`_toggle_visibility`）跟信號觸發的重複啟動 summon（`_summon_self_main_thread`）。

## v0.11.43 (2026-04-29)

### Fixes
- **Scroll-history overlay still ate numbered outline items + same heading repeated** — two related dedup mistakes Howard caught in a slide-naming outline:
  - "英文短句、有電影感" (a section heading) appeared **twice** because the dedup key was the raw stripped line — residual ANSI bytes / different leading whitespace between the two captures made them compare unequal. Tightened the ANSI strip regex (now also catches OSC hyperlinks, charset designates, and CSI sequences ending in `~`/`?`/`>`) and normalised whitespace runs to a single space when computing the dedup key, so visually-identical lines really do collide.
  - Short numbered subtitles like "3. Year One" (vw 12–14) used to fall under the ≥3-occurrence gate when several similar outline blocks existed in the buffer, and got collapsed away. Raised that gate's minimum width from 12 to 20 cells — wide redraw rows (audit logs, sentences) still get folded; short bullets / numbered headings always pass through.

### 修正
- **上滾 overlay 還是會吃掉編號列點 + 同一行 heading 重複出現** — 兩個 dedup 邏輯破口，Howard 在簡報命名 outline 截圖抓到：
  - 「英文短句、有電影感」（章節標題）出現**兩次**：dedup key 用 raw stripped line，但兩次 capture 殘留的 ANSI bytes / 縮排不同，比對不相等就漏抓。新 ANSI 規則加抓 OSC 超連結、charset designates、CSI 收尾 `~`/`?`/`>`；dedup key 也把空白合併成單空格，視覺相同的行才真的命中 set。
  - 「3. Year One」這種短編號副標（寬度 12-14）在 outline 多塊類似結構時會落到 ≥3 重複規則被砍。`REPEAT_GATE_MIN_WIDTH` 從 12 拉到 20，長 row（audit / 完整句子）照樣摺，短 bullet / 編號標題不再被誤殺。

## v0.11.42 (2026-04-29)

### Fixes
- **Preset's nickname disappeared after restart** — when a user opened a session via the "+ New" preset menu, the preset's display name (Claude / Codex / Garden cms / etc.) was only set on the frontend `sessions[sid].label`. It never reached `Session._custom_label` and was never persisted to `config.session_labels`. On next launch `restore_tmux_sessions` had no entry for that sid in the labels dict, so it fell back to the bare cmd name. Manually-renamed sessions worked because `rename_session()` already writes both. UI's `openSession` now fires `pywebview.api.rename_session(sid, label)` after creating the session, so preset nicknames survive restart on equal footing.

### 修正
- **從「+」預設 preset 開的 session，nickname 重啟就不見** — UI 端 `openSession` 把 preset.name（例如 Claude / Codex / Garden cms）放進 frontend 的 `sessions[sid].label`，但**沒寫進 `Session._custom_label`、也沒存進 `config.session_labels`**，下次 restart 從 config 讀 label 對照就找不到，fallback 變成原始 cmd 名。手動 rename 過的 session 沒事是因為 `rename_session()` 一條龍存好。修法：UI `openSession` 拿到 sid 後立刻 call 一次 `rename_session(sid, label)`，跟手動命名走同一條持久化路徑，重啟後 preset 名字保留。

## v0.11.41 (2026-04-29)

### Fixes
- **Duplicate-instance guard never actually triggered → still got two shellframes after summon** — v0.11.31's `_ensure_single_instance` looked up `runningApplicationsWithBundleIdentifier_("com.h2ocloud.shellframe")`. But the launcher exec's `python main.py` directly, so the kernel sees the process as Python.app — bundle id `org.python.python`, not `com.h2ocloud.shellframe`. The lookup never matched the running instance, the guard never fired, and Howard kept seeing two-instance TG 409 conflicts whenever a click / hotkey path raced against a still-shutting-down instance. Replaced with a PID-file approach: each instance writes `/tmp/shellframe.pid` on startup and registers a `SIGUSR1` handler that brings the window forward; a duplicate launch reads the file, probes the PID with `kill(pid, 0)`, and — if alive — signals the existing instance instead of booting itself, then `os._exit(0)`. Stale PID files (last shutdown crashed) are detected by the liveness probe and overwritten cleanly.

### 修正
- **單一 instance 防護根本沒觸發 → 仍會多開** — v0.11.31 的 `_ensure_single_instance` 用 `runningApplicationsWithBundleIdentifier_("com.h2ocloud.shellframe")` 找重複，但 launcher 直接 exec `python main.py`，kernel 看到的是 Python.app（bundle id `org.python.python`），**不會匹配 `com.h2ocloud.shellframe`**。lookup 永遠空，guard 從來沒生效，所以 hotkey 喚出時若新 instance 跟舊 instance 重疊，TG 409 衝突就發生。改成 PID-file：每個 instance 啟動時寫 `/tmp/shellframe.pid` + 註冊 `SIGUSR1` handler（收到就把視窗叫到前景）；新 launch 讀 PID file → `kill(pid, 0)` 探活 → 若活著就 signal 那個 PID 來前景 + `os._exit(0)` 不啟動。Stale PID file（上次 crash 留下）被探活步驟識別出來覆蓋掉，不會卡死。

## v0.11.40 (2026-04-29)

### Fixes
- **Scroll-history overlay had nothing to scroll back into after pyte switch** — v0.11.38 made pyte's `HistoryScreen` the primary source for the overlay. pyte only knows about bytes the bridge has fed it since startup, which on short conversations (or sessions that were running before the bridge was launched) is a few dozen lines — already smaller than the overlay viewport, so "往上滑" did literally nothing. Reverted priority: tmux `capture-pane` is primary again with the v0.11.37 dedup heuristics (consecutive prefix → CJK ≥ 90% → ≥ 3 occurrences) doing their best to collapse streaming-redraw noise. pyte stays as a Windows / no-tmux fallback. Yes, the heuristics still miss the occasional table edge case — but a stable approximation of the full backlog beats a clean rendering of 30 lines you can't move past.

### 修正
- **改用 pyte 後上滾根本沒得滾** — v0.11.38 把 pyte `HistoryScreen` 當 overlay 主要資料源，但 pyte 只記得 bridge 啟動之後 feed 的 bytes，短對話或 bridge 啟動之前的 session 內容只有幾十行，比 overlay 本身還短，「往上滑完全不會動」就是這個。優先順序改回來：tmux `capture-pane` 還是主，仍套 v0.11.37 的 dedup（連續 prefix → CJK ≥ 90% → 出現 ≥ 3 次）盡力處理 streaming-redraw 噪音。pyte 降為 Windows / 沒 tmux 時的 fallback。Heuristics 偶爾會漏掉某種表格邊角，但「能滾整個 backlog 大致正確」勝過「30 行內容很乾淨但完全滾不動」。

## v0.11.39 (2026-04-29)

### Fixes
- **Scroll-history overlay opened with a wall of empty space at the top** — pyte pre-allocates a fixed 50-row grid the moment a screen is created, and when the bridge starts feeding mid-conversation the cursor sits near the bottom while the upper half stays blank. v0.11.38 only trimmed trailing blanks; the leading run carried into the overlay as a tall empty block before the first real line. Added a leading-blank trim so the overlay opens straight on content. Internal blank lines (between paragraphs) are preserved.
- **Enter still got eaten right after pasting** — v0.11.35's focus guard yanks the textarea back within 80ms, but if the user presses Enter in the first frame BEFORE the guard has won, the keydown lands on body / image-bar / drop-overlay and never reaches xterm. Added a document-level keydown intercept that runs only while the guard is active: if Enter fires while focus is in any "stealable" zone, we forward `\r` to the active session ourselves and pull focus. So even on the worst-case post-paste race, the first Enter submits.

### 修正
- **上滾 overlay 開頭一大段空白** — pyte 一建好 screen 就先 pre-allocate 一個 50 行的固定 grid，bridge 中途接手 feed 的時候 cursor 落在底部，上半 grid 全是空 row。v0.11.38 只 trim 尾端空白，**前面那段空白照舊跑進 overlay**。新增頭端空白 trim，overlay 一開就直接看到內容。段落之間的合法空行保留。
- **貼圖完打 Enter 還是會被吃** — v0.11.35 的 focus guard 80ms 內會把 textarea 拉回，但若使用者在 guard 搶到 focus **之前**就按 Enter，那個 keydown 落在 body / image-bar / drop-overlay 直接被吞，xterm 根本沒收到。新增 document keydown 攔截器，只在 guard 啟動期間生效：Enter 若打在 stealable 範圍（body / 我們自己的浮動 bar），直接幫你 forward `\r` 到 active session 並把 focus 拉回。Worst-case 賽跑下第一個 Enter 也保證送出。

## v0.11.38 (2026-04-29)

### Fixes
- **Scroll-history overlay rebuilt on top of pyte instead of tmux scrollback** — every layered dedup heuristic on top of `tmux capture-pane` (consecutive prefix → CJK 90% → ≥3 occurrences) kept missing a new edge case in real captures: tables losing rows, mixed-content blocks repeated 4×, redraw frames intercut between unrelated conversation segments. Fundamental cause: tmux records every cursor-positioned redraw in scrollback, so a streaming TUI bleeds dozens of intermediate frames into the buffer that no line-level dedup can reliably untangle. Switched the overlay's primary source to the bridge's pyte `HistoryScreen`. pyte is a real terminal emulator — it consumes raw PTY bytes and exposes only the FINAL rendered state of every cell, so streaming redraws never leave duplicate lines for us to fight in the first place. Trade-off: pyte stores pre-styled chars, so the overlay loses ANSI colour. Correctness > prettiness for scroll-back, especially after the repeat reports of "跑版". tmux path is kept as a fallback for sessions where the bridge isn't running (still goes through the dedup heuristics).

### 修正
- **上滾 overlay 改用 pyte 渲染後的畫面，徹底繞過 tmux scrollback 的 redraw 噪音** — 試了一堆 dedup（連續 prefix → CJK 90% → 出現 ≥3 次）都還是有 edge case：表格少行、混合內容重複 4 次、不同對話段相鄰串在一起。根本原因：tmux 把每個 cursor-positioned redraw 都記進 scrollback，streaming TUI 會把幾十個中間 frame 灌進去，line-level dedup 怎麼修都漏一塊。改成 overlay 主要 source 走 bridge 的 pyte `HistoryScreen` —— pyte 是真正的 terminal emulator，吃原始 PTY bytes 後只暴露**最終渲染後**的每個 cell，streaming redraw 在那層就被吃掉了，根本沒有重複行可以給我們處理。代價：pyte 存的是去除樣式後的字符，overlay 失去 ANSI 色彩。考量你已經多次抱怨「跑版」，正確性優先於配色。tmux fallback 保留給 bridge 沒跑的 session（仍走 dedup heuristics）。

## v0.11.37 (2026-04-28)

### Fixes
- **Scroll-history overlay still showed 4× duplicate rows on tables / audit logs** — v0.11.25's CJK ≥ 90% gate only collapsed pure-Chinese streaming redraw. It missed mixed-content rows that ALSO get redrawn during streaming, like Howard's "4/2 | Warren 寄 V1.5.1 部版資訊" appearing 4× in a row. Added a second gate: any line ≥ 12 cells wide that occurs ≥ 3 times in the capture is collapsed to its first occurrence (count threshold = 3, not 2, so legitimate two-time repeats — `return null;` twice, two adjacent table rows that genuinely share a date — are preserved).

### 修正
- **上滾 history 表格 / audit log 還是會出現 4× 重複** — v0.11.25 的 CJK ≥ 90% 規則只抓純中文 streaming redraw。Howard 截圖中混合內容（例如 `4/2 | Warren 寄 V1.5.1 部版資訊` 連續 4 次）逃過閘門，照樣多次寫入 scrollback。新增第二條閘門：任何 ≥ 12 cells 寬的行在這份 capture 出現 ≥ 3 次 → 只保留第一次。閾值用 3 不是 2，避免誤砍合法的兩次重複（同一段 code 出現兩次 `return null;`、相鄰兩列 table 同一天日期都保留）。

## v0.11.36 (2026-04-28)

### Fixes
- **Spawned sessions no longer inherit shellframe's install dir as cwd** — `claude`, `codex`, bash, etc. used to open in `~/.local/apps/shellframe/` because the launcher script `cd`'s there before exec'ing main.py and child PTYs inherited that cwd. Confusing — agents asked to "fix this bug" would default to working on shellframe internals, and shells dropped you in a directory you don't own. Now every PTY (tmux `new-session -c $HOME`, plain Unix pty.fork → `os.chdir`, Windows pywinpty / Popen `cwd=`) starts at `$HOME`. The init prompt still names `~/.local/apps/shellframe/` as the location for self-modification, so AI agents can still find shellframe source when explicitly asked to tune it.

### 修正
- **新 session 不再開在 shellframe 的安裝目錄** — `claude` / `codex` / bash 之前都繼承 shellframe launcher `cd $DIR` 之後的 cwd，全都從 `~/.local/apps/shellframe/` 開起，agent 被問「fix 一下這個 bug」會誤以為要去動 shellframe 本體；純 shell 也是落在使用者根本不擁有的目錄。改成所有 PTY（tmux `new-session -c $HOME`、Unix `pty.fork` 後 `os.chdir`、Windows pywinpty / Popen `cwd=`）一律從 `$HOME` 起跑。Init prompt 仍保留 `~/.local/apps/shellframe/` 路徑指引，使用者要 agent 改 shellframe 本體還是知道去哪。

## v0.11.35 (2026-04-28)

### Fixes
- **Focus-stealing guard during paste — stop losing the textarea on big / image pastes** — v0.11.24's "fire 4 setTimeouts up to 200ms" approach was fine for keyboard text paste but lost the race for heavier flows: multiple images, large text, or any chain that hits FileReader → `save_image` IPC → `write_input` settles well over a second after the paste event, and WKWebView keeps re-blurring the helper textarea throughout. Result: "pasted, can't type, can't Enter, have to click back into the terminal." Replaced the fixed schedule with a 3-second guard that checks `document.activeElement` every 80ms and pulls focus back when it's parked on `body` / `image-bar` / `drop-overlay` / `paste-confirm-bar`. User-initiated focus on real inputs / modals is left alone (the guard recognises stealable vs. user-driven targets), and the guard self-stops after 3 stable ticks on the textarea so it doesn't run forever.

### 修正
- **大段 paste / 貼圖時 focus 一直被搶走，沒辦法直接打字 / Enter 送出** — v0.11.24 那版「200ms 內連發 4 個 setTimeout」對純文字 paste 還行，但碰到多張圖、大段文字、或 FileReader → `save_image` IPC → `write_input` 這條長鏈會落在 1 秒之後才穩定，期間 WKWebView 會反覆 blur helper textarea，使用者就遇到「貼完打不出字、Enter 沒反應、要再點一下對話才能繼續」。改成 3 秒 focus guard：每 80ms 檢查 `document.activeElement`，若卡在 body / image-bar / drop-overlay / paste-confirm-bar 就拉回 textarea；使用者主動 focus 真的 input / modal 不會被搶；連續 3 tick 看到 textarea focused 就自動停，最多 3 秒。

## v0.11.34 (2026-04-27)

### New Features
- **Right-click paste now handles images / files like Cmd+V** — the right-click "no selection → paste" branch used to call `paste_text()` which only sees `pbpaste`'s text projection of the clipboard, so images / Finder-copied files just dropped through. Right-click paste now drives the same intake pipeline as keyboard paste: tries `navigator.clipboard.read()` for image blobs first (saves to `~/.claude/tmp/` and attaches), falls back to `get_clipboard_files()` for Finder-copied files, then plain text last. The text path also goes through the large-paste confirm gate (≥ 1000 chars or ≥ 10 lines → Esc to cancel) and bracketed-paste wrapping, matching Cmd+V behaviour exactly.

### 新功能
- **右鍵貼上現在跟 Cmd+V 一樣會處理圖片 / 檔案** — 之前右鍵 paste 走 `paste_text()` 只拿純文字，剪貼簿裡的圖片或 Finder 複製的檔案直接掉。改成跟鍵盤 Cmd+V 同一條路：先試 `navigator.clipboard.read()` 抓圖片 blob → 存到 `~/.claude/tmp/` 並 attach；其次試 `get_clipboard_files()` 抓 Finder 路徑；最後才走純文字。文字路徑也走長 paste 確認 bar（≥ 1000 字或 ≥ 10 行 → Esc 取消）+ bracketed-paste wrapping，跟 Cmd+V 行為完全一致。

## v0.11.33 (2026-04-27)

### New Features
- **Claude + Codex are now built-in presets** — fresh installs see Claude (🚀 `claude`) and Codex (🤖 `codex`) in the "+" new-tab menu out of the box, no manual preset setup. Existing installs migrate one-shot on next launch: if neither preset is already present, both are appended; a `_default_ai_presets_migrated` flag in `config.json` makes the migration idempotent so users who explicitly delete one don't get it back next launch. Bash / PowerShell stays as the first preset for non-AI shell access.

### 新功能
- **Claude / Codex 變成預設 preset** — 新安裝開 shellframe 第一次按 "+" 就看到 Claude（🚀 `claude`）跟 Codex（🤖 `codex`），不用手動加 preset。已安裝的使用者下次啟動會做一次 migration：若兩個都不在 preset list 就附加上去，`config.json` 寫一個 `_default_ai_presets_migrated` 旗標確保只跑一次，使用者後來刪掉不會被自動加回來。Bash / PowerShell 仍排第一作為純 shell 用途。

## v0.11.32 (2026-04-24)

### Fixes
- **Auto /compact threshold spinner was unreadable on dark background** — WebKit draws `<input type="number">` spinner arrows in a near-black default that disappears on the `#1a1b26` field. Added a `sf-bright-spin` class that sets `color-scheme: dark` + `accent-color` and runs `filter: invert(1) brightness(1.5)` on the native spinner pseudo-elements so up/down arrows render as bright light-grey.

### 修正
- **Auto /compact 門檻的上下箭頭在深色底看不見** — WebKit 預設的 number input spinner 是近黑色，跟 `#1a1b26` 底幾乎重疊。加了 `sf-bright-spin` CSS class：`color-scheme: dark` + `accent-color` 讓 WebKit 挑 dark-theme 的箭頭，再疊一層 `filter: invert(1) brightness(1.5)` 把 spinner 亮到淺灰，一眼就看得到。

## v0.11.31 (2026-04-24)

### Fixes
- **SIGTRAP on launch under macOS 26 — shellframe died silently before the window appeared** — v0.11.30 added `setCollectionBehavior_()` on every `NSApp.windows()` entry inside `_on_loaded`, which pywebview fires on its event-dispatcher thread. macOS 26 (Tahoe) tightened the AppKit main-thread-only rule from "undefined behaviour" to hard `EXC_BREAKPOINT` / SIGTRAP, so any user on 26+ who upgraded to v0.11.30 hit an immediate crash with no Python traceback (ObjC-level abort bypasses `try/except` and `_write_crash_log`, so `~/.shellframe-crash.log` stayed empty — the silent-failure mode). Fixed by wrapping the `setCollectionBehavior_` loop in a block and dispatching it to `NSOperationQueue.mainQueue()` so mutation happens on the main thread regardless of which thread `_on_loaded` fires on.
- **Rapid hotkey toggle could spawn a second instance → TG bot 409 Conflict** — when the user hammered ⌃⌥Space while a previous instance was still shutting down, the old process still had the TG bridge polling while a new process booted and started its own poller. Two pollers on the same bot token immediately 409-conflict each other and messages stop flowing. Two fixes: (1) `_ensure_single_instance()` runs first thing in `main()` — if another shellframe is already registered with the bundle id, we activate it and `os._exit(0)` without setting up any state. (2) `cleanup_all()` tears down NSEvent hotkey monitors before stopping the bridge, so a late ⌃⌥Space during shutdown can't ping `open -b` and race a second instance into the window where the bridge is still alive.

### 修正
- **快速熱鍵開關會重開第二個 instance → TG bot 409 Conflict** — Howard 連按 ⌃⌥Space，原 instance 還在 cleanup、bridge 還沒 stop 完，新 instance 已經開起來各自 polling 同一個 bot token → Telegram 直接 409 打架、訊息卡住。兩個防線：(1) `main()` 最早期跑 `_ensure_single_instance()`，若 bundle id 已有 instance 在跑，直接 activate 它然後 `os._exit(0)`，新 process 不配置任何資源。(2) `cleanup_all()` 先拆 NSEvent 熱鍵 monitor 再 stop bridge，避免 shutdown 中被 ⌃⌥Space 觸發 `open -b` 跟還活著的 bridge 搶 token。

### 修正
- **macOS 26 上 v0.11.30 的 `⌃⌥Space` 新功能讓 shellframe 一啟動就 SIGTRAP、視窗完全沒出來** — v0.11.30 在 `_on_loaded` 裡對所有 `NSApp.windows()` 呼叫 `setCollectionBehavior_()`，但 pywebview 的 loaded event 是在背景 thread 觸發的。macOS 26 (Tahoe) 把 AppKit 「NSWindow mutation 只能在主執行緒」的規則從「未定義行為」升級成硬性 `EXC_BREAKPOINT` / SIGTRAP，所以已經升級到 26 的使用者升到 v0.11.30 後會一啟動就死；而且因為 crash 發生在 ObjC 層，Python 的 `try/except` 跟 `_write_crash_log` 都攔不到，`~/.shellframe-crash.log` 是空的（沉默失敗模式，最難 debug 的那種）。修法是把 `setCollectionBehavior_` 迴圈包進 block 再用 `NSOperationQueue.mainQueue()` 派回主執行緒，這樣不論 `_on_loaded` 跑在哪個 thread，mutation 都在 main thread 上執行。

## v0.11.30 (2026-04-24)

### New Features
- **Spaces-aware `⌃⌥Space` — window always comes to YOU, not you to window** — on macOS each window lives in a specific Space; the default `activateIgnoringOtherApps` jumps the user's viewport to wherever shellframe's window happens to live, which breaks flow for heavy Mission Control users. Now shellframe's NSWindows are tagged with `NSWindowCollectionBehaviorMoveToActiveSpace`, so hotkey activation pulls the window into the user's current space instead. The hide/show decision also factors in the current space: if shellframe is NOT visible in the space you're on, the hotkey treats it as "hidden" and summons it; only when the window is visibly present in your current space AND focused does it hide. Visible-on-current-space detection uses Quartz's on-screen window list filtered by our PID.

### 新功能
- **`⌃⌥Space` 支援虛擬桌面 — 視窗跟著你跑，不是你跟著視窗跑** — macOS 每個視窗屬於某個 Space；`activateIgnoringOtherApps` 預設會把使用者的視角切到視窗所在的 Space，對大量用 Mission Control 的人（Howard）流程會被打斷。現在 shellframe 的 NSWindow 加上 `NSWindowCollectionBehaviorMoveToActiveSpace`，熱鍵 activate 時視窗會跑到「你當下這個 Space」。隱藏 / 喚出的判斷也加進 current-space 檢查：當下 Space **看不到** shellframe → 視為隱藏，熱鍵把它叫到眼前；當下 Space **看得到** 且有 focus → 才真的 hide。用 Quartz on-screen window list 過濾自己 PID 判斷「當下 Space 是否有我的視窗」。

## v0.11.29 (2026-04-24)

### New Features
- **macOS notification when an AI session finishes while shellframe is hidden** — bridge now posts a native banner ("ShellFrame · <session label> · AI reply ready") the moment a session finishes delivering a reply AND `NSApp.isActive()` reports the app isn't in the foreground (minimised, Cmd+H'd, or behind another app). Click the banner and macOS activates the shellframe bundle, bringing you straight back to the waiting session. Per-slot 30s cooldown so multi-chunk extractions don't stack. Toggle in Settings → General → Completion notifications (default on). macOS only.

### Fixes
- **`/restart` spawned the new instance as bare Python, not a proper .app — two Dock icons, wrong name/icon** — v0.11.13 worked around a stale LaunchServices bundle-id registration by exec'ing `APP_DIR/ShellFrame.app/Contents/MacOS/shellframe` directly. That bypassed bundle wrapping, so the child process showed up as a generic Python icon and the user couldn't tell which Dock entry was shellframe. Switched to `/usr/bin/open -n <absolute .app path>` as Strategy 1 — gives the new process full bundle context (right name, right icon, Cmd-Tab shows "ShellFrame") while still avoiding the bundle-id resolution that was the original v0.11.13 target. `-n -a` kept as Strategy 2 fallback.

### 新功能
- **macOS 通知 — shellframe 在背景時 AI 完成作業會彈右上角 banner** — bridge 抽到一則 AI 回覆時，如果 `NSApp.isActive()` 顯示 shellframe 不在前景（縮小、Cmd+H、被其他 app 蓋掉），就送 macOS 原生通知「ShellFrame · <session 標籤> · AI reply ready」。點通知 macOS 會把 shellframe 拉回前景，直接回到等你的 session。每個 slot 30 秒 cooldown 避免 multi-chunk 連發。Settings → General → Completion notifications 可關（預設開）。目前只支援 macOS。

### 修正
- **`/restart` 開出來的 app 是純 Python、不是 ShellFrame icon** — v0.11.13 為了繞過過期的 LaunchServices 註冊，直接 exec `APP_DIR/ShellFrame.app/Contents/MacOS/shellframe`；這條路繞過 bundle wrapping，新 process 被 macOS 當成 Python，Dock 出現兩個 icon（原本你點的 ShellFrame + 新的 Python）讓人困惑。改用 `/usr/bin/open -n <絕對 .app 路徑>` 作為 Strategy 1 —— 保留完整 bundle 身份（正確名字、icon、Cmd-Tab 顯示 "ShellFrame"），又避開 bundle-id 解析那條舊路徑踩雷。`-n -a` 降為 Strategy 2 fallback。

## v0.11.28 (2026-04-24)

### Fixes
- **`⌃⌥Space` hid shellframe but couldn't bring it back** — after `NSApp.hide_(None)` the app is both *hidden* AND *not active*; `activateWithOptions_` alone doesn't reliably reverse that from a background event callback. Summon path now: `unhide_` → `activateWithOptions_` → `/usr/bin/open -b com.h2ocloud.shellframe` as a belt-and-braces fallback (works regardless of Accessibility / Automation state). Also prints an `active=/hidden=` diagnostic on each toggle so it's easy to see which branch fired.

### 修正
- **`⌃⌥Space` 可以隱藏但叫不回來** — `NSApp.hide_(None)` 之後 app 同時是 **hidden** 且 **非 active**；光 `activateWithOptions_` 從背景 callback 呼叫常常被 macOS 無視。喚回流程改成：`unhide_` → `activateWithOptions_` → 再保險 `/usr/bin/open -b com.h2ocloud.shellframe`（任何狀態、任何權限組合都能把 app 拉回前景）。另外在每次 toggle 印出 `active=/hidden=` 診斷 log，方便看是走哪條路。

## v0.11.27 (2026-04-24)

### New Features
- **Global hotkey `⌃⌥Space` — show / hide shellframe from anywhere** — press Ctrl+Option+Space from any app to bring shellframe forward; press again while shellframe is active to hide it (equivalent to Cmd+H). Implemented via `NSEvent.addGlobalMonitorForEventsMatchingMask` + a local monitor, so it also fires cleanly when shellframe itself has focus. Toggle on/off in Settings → General → Global hotkey; change takes effect immediately (no restart). macOS only for now. Requires Accessibility permission for the global path; users who've run `sfctl permissions` already have it.

### 新功能
- **全域快捷鍵 `⌃⌥Space` — 隨時喚出 / 收起 shellframe** — 在任何 app 裡按 Ctrl+Option+Space 把 shellframe 叫到前景；shellframe 已在前景再按一次收起（等同 Cmd+H）。用 `NSEvent.addGlobalMonitorForEventsMatchingMask` + local monitor 實作，shellframe 自己有 focus 時也能正常觸發。Settings → General → Global hotkey 可關，改設定立即生效不用重開。目前只支援 macOS。全域監聽需要 Accessibility 權限；跑過 `sfctl permissions` 的人已經有。

## v0.11.26 (2026-04-24)

### New Features
- **Auto `/compact` for Claude Code when context is running out** — bridge's flush loop now watches for Claude's status-bar token gauge (`<model> … <N>% left`) in each slot's rendered screen. When `N` drops to the configured threshold (default 15%) and the slot is idle (no in-flight response, ≥ 2s of PTY silence, cooldown ≥ 90s since the last auto-compact), it writes `/compact\r` into the PTY so Claude summarises context and frees tokens without the user having to notice. Settings → General adds a toggle + threshold input (3–50%); flip off to disable. Strictly Claude-only — detection binds to the model name in the status bar (`sonnet` / `opus` / `haiku` / `claude-…`), so Codex / plain shells are never triggered.

### 新功能
- **Claude Code 快沒 token 時自動 `/compact`** — bridge 的 flush loop 每 0.5s 掃每個 slot 渲染後的畫面，找 Claude 的 status bar「`<model> … <N>% left`」。`N` 跌破設定門檻（預設 15%）且 slot idle（沒在回應、PTY 2 秒沒輸出、距上次自動 compact 至少 90s）就把 `/compact\r` 寫進 PTY，Claude 自動做 context summarise 騰 token，不用使用者自己盯。Settings → General 新增開關 + 門檻（3-50%）；關掉即停。**只對 Claude 生效** —— 偵測綁在 status bar 的 model 名（`sonnet` / `opus` / `haiku` / `claude-…`），Codex / bash / 其他 CLI 完全不會被誤觸。

## v0.11.25 (2026-04-23)

### Fixes
- **Scroll-history overlay still swallowed mixed-CJK report labels** — v0.11.16's CJK-dominance gate (≥ 50% fullwidth) was lenient enough to catch headings like `PM 卡改善 (Mentor Bridge 命題有效)` and bank lists `彰銀/新新併/華南/台壽` when they legitimately repeated in a long audit report. Tightened to ≥ 90%: only near-pure-CJK prose (streaming redraw noise is 100% CJK anyway) still triggers dedup; any line with ASCII, digits, slashes, or brackets is preserved in full.

### 修正
- **上滾 overlay 還是會把「含 ASCII 的中文 heading」吃掉** — v0.11.16 的 CJK 門檻是 ≥ 50% 全形字元，不夠嚴，像 `PM 卡改善 (Mentor Bridge 命題有效)`、銀行列表 `彰銀/新新併/華南/台壽` 這類在長 audit report 裡合法重複的行會被誤砍。改成 ≥ 90%：只有**幾乎純中文**的行（streaming redraw noise 本來就 100% CJK）才進 dedup，任何帶 ASCII / 數字 / 斜線 / 括號的行完整保留。

## v0.11.24 (2026-04-21)

### Fixes
- **Enter-after-paste focus fix (take 2) — multi-shot refocus + direct textarea target** — v0.11.21's single `setTimeout(0)` + `term.focus()` still lost the focus race on WKWebView: the browser does its own post-paste focus ping-pong for a few hundred ms after the paste event settles, and our one-shot refocus landed before that finished, so the textarea ended up blurred by the time the user hit Enter. Now fires four times (immediate, 0ms, 50ms, 200ms) and directly calls `.focus()` on the `xterm-helper-textarea` DOM node in addition to `term.focus()` so the event handler gate (`customKeyEventHandler` only runs while the textarea is the active element) actually sees focus land.

### 修正
- **貼圖後 Enter 第一次還是沒反應（第二次修法）** — v0.11.21 單一 `setTimeout(0)` + `term.focus()` 還是跟 WKWebView 的 focus 搶輸：browser 自己 paste event 後會持續 ping-pong focus 數百毫秒，我們只搶一次剛好落在它之前，之後 textarea 又被它 blur 掉，Enter 當然收不到。改成連發 4 次（立即 / 0ms / 50ms / 200ms），而且除了 `term.focus()` 之外，也直接對 DOM `xterm-helper-textarea` 下 `.focus()`，確保 xterm 的 `customKeyEventHandler` 真的看到 textarea 是 active element。

## v0.11.23 (2026-04-21)

### Fixes
- **Startup crash on saved x/y (third recurrence) — real fix this time** — v0.11.19's monkey-patch of `BrowserView.windowDidMove_` was ineffective because PyObjC binds method tables at class creation, so replacing the Python attribute didn't change ObjC dispatch. Cocoa still called the original IMP and crashed on `None.frame()`. Dropped passing x/y to `create_window` entirely; window now spawns centered, then moves to the saved position in the `loaded` event handler via `window.move(x, y)` — at that point cocoa has a valid `screen()` for the window and the move doesn't crash.

### 修正
- **存的 x/y 害第三次啟動 crash — 這次真的修了** — v0.11.19 的 `BrowserView.windowDidMove_` monkey-patch 其實沒生效：PyObjC 在 class 建立時就把 method table 綁死，在 Python 層換 attribute 完全影響不了 ObjC dispatch，cocoa 仍呼叫原本的 IMP、在 `None.frame()` 炸掉。拿掉 `create_window` 的 x/y 參數，視窗先**中央生成**，再在 `loaded` 事件裡用 `window.move(x, y)` 搬到存的位置；這時 cocoa 已經有合法的 `screen()`，搬動不會 crash。

## v0.11.22 (2026-04-21)

### Fixes
- **Scroll-history overlay clipped the right half of wide content** — overlay xterm used `fit.fit()` to size cols to the container width. When the live session's tmux pane was wider (e.g. 140 cols rendering a table), capturing at 140 into a 100-col overlay made xterm re-wrap / clip and the right half of every line vanished. Now the overlay pins cols to the LIVE session's current cols and wraps the xterm mount in a horizontal scroll container, so tables, code, and wrap-sensitive output render at their original width (horizontal scroll kicks in when the session was wider than the overlay).

### 修正
- **上滾 overlay 會把寬內容右半截掉** — overlay 的 xterm 用 `fit.fit()` 把 cols 縮到 overlay 容器寬度。live session 的 tmux pane 若更寬（例如 140 cols 渲染表格），140 col 內容丟進 100 col overlay 會被 xterm 重 wrap / 截斷，右半行就消失（Howard 看到的 `/Prod/FundSelectList | 說明` 表格右邊切掉）。現在 overlay 把 cols 鎖定成 **live session 當下的 cols**，xterm mount 外層加水平 scroll 容器，表格 / code / 對寬度敏感的輸出都能保留原本寬度，overlay 比 session 窄時自動出水平 scrollbar。

## v0.11.21 (2026-04-21)

### Fixes
- **Enter after image/file paste was swallowed — had to click the terminal first** — browser paste/drop flows land focus on body / image-bar / drop-overlay, not on xterm's helper textarea. xterm's `customKeyEventHandler` (which owns Enter-submit logic in AI sessions) only fires while the textarea is focused, so the first Enter after paste did nothing. `attachFile`, the document-level paste handler, and the drop handler all now call a common `_refocusActive()` that pulls focus back to the active session's textarea (setTimeout 0 so it runs after the browser's own post-paste focus ping-pong).

### 修正
- **貼圖/檔案後第一次 Enter 送不出去，要重新點對話才行** — 瀏覽器 paste / drop 流程結束後，focus 會留在 body / image-bar / drop-overlay，沒回到 xterm 的 helper textarea。AI session 的 Enter 送出邏輯走 xterm `customKeyEventHandler`，textarea 沒 focus 就完全收不到。`attachFile` / document paste handler / drop handler 三條路都呼叫同一個 `_refocusActive()`，把 focus 拉回當前 session 的 textarea（setTimeout 0 讓瀏覽器先跑完自己的 focus ping-pong 再被我們搶回來）。

## v0.11.20 (2026-04-21)

### Fixes
- **TG typing indicator went quiet during long AI replies** — `_send_typing` was only called inside the `idle < 3.0` branch of `_flush_loop`, so when the AI went silent for more than 3s (thinking / tool call / long generation) the indicator blanked out. Now fires on every 0.5s flush tick while `awaiting_response` is True, regardless of current output state, so TG's 5s auto-clear never wins. Also fires before the first PTY chunk so the "..." bubble shows up the moment the user submits.
- **`_user_chat` not persisted across full restart** — typing indicator + flush forwarding both need uid → chat_id mapping. Previously only stored in memory, so after `sfctl restart` the indicator was silently no-op'd until the user sent another message. Added to the `tg_offset.json` save/restore cycle alongside `_user_active`.

### 修正
- **TG 正在輸入動畫在 AI 回應中段斷掉** — `_send_typing` 原本只在 `idle < 3.0` 分支裡呼叫，AI 沉默超過 3 秒（思考 / tool call / 長回覆）typing 就消失。現在每 0.5s flush 都會打一次，只要 `awaiting_response` 還是 True 就持續刷新，TG 5s 自動清除追不上。也會在第一塊 PTY 輸出前就開始打，使用者按送出瞬間就看得到動畫。
- **`_user_chat` 沒跨重啟保存** — typing indicator 跟 flush forward 都靠 uid → chat_id 對照表。以前只放記憶體，`sfctl restart` 後完全沒了，直到使用者再送訊息才恢復（期間 typing 靜音）。現在跟 `_user_active` 一起寫進 `tg_offset.json`。

## v0.11.19 (2026-04-20)

### Fixes
- **Startup crash on multi-monitor Macs fixed** — pywebview's cocoa `windowDidMove_` callback does `i.window.screen().frame()`. During the initial move-to-saved-coords on a multi-display setup, the window can be transiently off every attached display, at which point `screen()` returns `None` and `.frame()` raises `AttributeError` before the UI ever paints. Our own pre-validator (checks the saved centre lands on an attached display) was passing, but the pywebview-internal transient still crashed. Added a defensive monkey-patch that wraps pywebview's `windowDidMove_` to no-op when `screen()` is None — the window still lands at its final position, we just skip the bogus mid-move event. Saved `(-102, -756)` from an unplugged portrait display was the trigger on Howard's setup; config's stale x/y were also scrubbed so the next launch centres cleanly.

### 修正
- **多螢幕 Mac 啟動就 crash 的問題修掉** — pywebview cocoa 後端的 `windowDidMove_` 在裡頭跑 `i.window.screen().frame()`。多螢幕環境第一次把視窗移到上次存的座標時，視窗會有一瞬間落在任何一塊螢幕之外，這時 `screen()` 回 `None`、`.frame()` 直接丟 `AttributeError`，UI 還沒畫就整個 app 死。我們自己的前置驗證（檢查中心是否在任一螢幕上）有過，但 pywebview 內部那個瞬間 transient 還是會中。加了一層 monkey-patch 包住 pywebview 的 `windowDidMove_`，`screen()` 是 None 就直接 no-op — 視窗最終還是會落在該在的位置，我們只是跳過那個假的中間事件。這次觸發源是當初直式螢幕拔掉後留下的 `(-102, -756)`，config 順手清掉，下次開會回到置中。

## v0.11.18 (2026-04-20)

### Fixes
- **First reply after `sfctl reload` leaked preamble echo back to TG** — hot-reload rebuilt each `SessionSlot` from scratch so `sent_texts` / `sent_responses` started empty. The echo filter had nothing to compare against, so the AI's first response (which typically contains a preamble fragment because reload happens mid-thinking) got forwarded unchanged. `hot_reload_bridge` now snapshots `sent_texts`, `sent_responses`, and `pending_menu` per slot before `stop()` and restores them after `register_session()` rebuilds the slots. Any v0.11.17 echo-filter improvement now actually has history to work against.

### 修正
- **`sfctl reload` 後第一則回覆會把 preamble 整段回送到 TG** — hot-reload 把每個 `SessionSlot` 重建成空的，`sent_texts` / `sent_responses` 全空。echo filter 沒東西可比 → AI 第一則回覆（通常是 reload 發生在思考中途、reply 含 preamble 片段）就原汁原味傳回去。現在 `hot_reload_bridge` 在 `stop()` 之前 snapshot 每個 slot 的 `sent_texts` / `sent_responses` / `pending_menu`，`register_session()` 重建後還原。v0.11.17 的 30-char sliding window 終於有東西可以比對。

## v0.11.17 (2026-04-20)

### Fixes
- **Preamble / user-message echo leaked back to TG** — echo filter only caught full nesting (`nr in ns`) or 25-char prefix match. When the AI emitted a mid-preamble fragment ("sfctl restart — full restart for main.py / web/index.html…") plus the user's original message and tacked on new text, neither rule fired and the whole thing got forwarded back. Added a 30-char sliding-window substring check against each sent text — any 30-char run copied out of preamble / forwarded is now treated as echo.
- **sent_texts cap was too small** — stored only last 10 entries, but per-turn preamble injection means each user message consumes 2 slots, so echo history only covered ~5 turns. Bumped to 30 so the filter still has the preamble + forwarded text in hand when the AI response straggles in later.

### 修正
- **Preamble / 用戶訊息被 echo 回 TG** — 舊 echo filter 只抓「reply 整個被 sent 包住」或「sent 前 25 字出現在 reply 開頭」。AI 如果吐出 preamble **中段** + 用戶原訊息 + 額外內容，兩種規則都沒命中，整段又被轉回 TG。現在加一條：對每個 sent_text 跑 30-char sliding window，任何 30 字連續片段被 AI reply 覆蓋就判 echo。
- **sent_texts 容量太小** — 本來只存最後 10 筆，但 per-turn 要塞 preamble 跟 forwarded 各一筆，等於只記得 5 個對話 turn 的 echo 來源。拉到 30，AI reply 晚到也還抓得到。

## v0.11.16 (2026-04-20)

### Fixes
- **Scroll-history overlay no longer eats legitimate code-line duplicates** — v0.11.8's non-consecutive dedup pass also collapsed ASCII lines wider than 8 cells, so real code with repeated `return null;` / `}` / `if (x) {` lost those repeats and rendered as a torn-up mess. Gate now requires the line to be CJK-dominant (≥ half its visual width from fullwidth chars) before it's a dedup candidate. Chinese prose redraw frames still get folded; ASCII source code passes through untouched.

### 修正
- **歷史卷動 overlay 不再把 code 裡的重複行吃掉** — v0.11.8 加的跨行 dedup 對 >= 8 cells 的 ASCII 也會觸發，結果像 `return null;` / `}` / `if (x) {` 這種 code 合法重複的行被誤砍，overlay 看起來缺一塊一塊。門檻多加一條：**只有 CJK 字元佔視覺寬度過半**的行才進 dedup set。中文 redraw frame 照舊會被摺掉，ASCII 程式碼保留原樣。

## v0.11.15 (2026-04-20)

### Fixes
- **Drag-and-drop files now attach with their real absolute path** — drop handler used to go straight to `FileReader → save_file_from_clipboard → ~/.claude/tmp/…` copy path. WKWebView's `File` objects occasionally hand back a 0-byte blob or silently stall the FileReader, which manifested as "dragged a file, nothing happened". Now reads `text/uri-list` / `public.file-url` / `text/plain` off `dataTransfer` FIRST — for Finder-originated drops this gives a proper `file:///…` URL that we decode into the original absolute path and attach directly (no tmp copy, no FileReader round-trip). Blob-based FileReader path kept as fallback for in-memory drags from browsers.

### 修正
- **拖曳檔案現在會顯示真實絕對路徑** — 以前 drop handler 一律走 `FileReader → save_file_from_clipboard → ~/.claude/tmp/…` 這條複製路徑。WKWebView 的 `File` 物件拖 Finder 檔時偶爾會回傳 0 byte 或 FileReader 永遠不觸發 onload，導致「拖進去沒反應」。現在優先從 `dataTransfer` 抓 `text/uri-list` / `public.file-url` / `text/plain` —— Finder 拖曳會給完整 `file:///...` URL，解碼成原始絕對路徑直接 attach，不用複製檔、不用經過 FileReader。Blob / FileReader 路徑保留作為瀏覽器內拖的 fallback。

## v0.11.14 (2026-04-20)

### New Features
- **Large paste confirm — Esc to cancel before the text hits the AI** — any plain-text paste ≥ 1,000 chars or ≥ 10 lines now pauses on a yellow confirm bar ("Enter 送出 · Esc 取消") instead of dumping straight into the PTY. Prevents the "pasted the wrong clipboard into Claude and it auto-submitted" regret. Small pastes still flow through xterm.js normally. Image / file pastes unchanged.

### 新功能
- **長文字 paste 前置確認 — Esc 取消、Enter 送出** — 貼上 ≥ 1000 字或 ≥ 10 行的純文字會先停在黃色確認 bar，不會直接灌進 PTY。按 Esc 取消、Enter 才送（包 bracketed paste）。避免「貼錯剪貼簿、AI 直接送出」這種慘案。小段 paste 照舊穿過；圖片/檔案 paste 行為不變。

## v0.11.13 (2026-04-20)

### Fixes
- **`/restart` sometimes failed to spawn a new instance on macOS** — `restart_app` ran `open -n -a <path>` first, which resolves against the bundle ID (`com.h2ocloud.shellframe`) rather than the path. If LaunchServices had the bundle registered elsewhere (stale iCloud copy, old `/Applications` version, `~/Downloads` leftover), `open` routed there and the launch silently no-op'd, forcing the user to click the Dock / Launchpad icon manually. Now executes the canonical `APP_DIR/ShellFrame.app/Contents/MacOS/shellframe` launcher directly as primary strategy; `open -n -a` kept only as fallback.

### 修正
- **`/restart` 在 macOS 偶爾跑不起新 instance** — `restart_app` 以前優先用 `open -n -a <path>`，但 `open` 會用 bundle ID (`com.h2ocloud.shellframe`) 查 LaunchServices，若 bundle 被註冊到別份（iCloud 同步的舊副本、舊 `/Applications` 版本、`~/Downloads/` 殘檔），`open` 會去開那邊 → 當前 process 退出後沒新 instance 冒出來，使用者只能去 Dock / Launchpad 手動點。現在直接 exec `APP_DIR/ShellFrame.app/Contents/MacOS/shellframe` launcher，`open -n -a` 降為備援。

## v0.11.12 (2026-04-19)

### Fixes
- **Local STT no longer fails when the model is already present under `~/.cache/whisper-models/`** — `_stt_local_model_path` only checked `~/.local/share/shellframe/whisper-models/ggml-base.bin`, so users who already had whisper.cpp models from yt-notion / brew saw local STT reported as missing and were asked to re-download the same ~141MB file. Added fallbacks to `~/.cache/whisper-models/ggml-base.bin` and `/opt/homebrew/share/whisper-cpp/ggml-base.bin`.

### 修正
- **本地 STT 不再因「模型檔不在 shellframe 專屬路徑」就被判斷為缺模型** — 原本 `_stt_local_model_path` 只看 `~/.local/share/shellframe/whisper-models/ggml-base.bin`，但使用者若為了 yt-notion / brew 已經把模型放在 `~/.cache/whisper-models/`，shellframe 完全看不到，還會要你再下一份 ~141MB 的重複檔。現在會依序回退到 `~/.cache/whisper-models/ggml-base.bin` 與 `/opt/homebrew/share/whisper-cpp/ggml-base.bin`。

## v0.11.11 (2026-04-19)

### Fixes
- **First Enter after Chinese IME / image paste no longer gets swallowed** — two separate races collapsed into one user-visible bug:
  1. On WKWebView, the IME `compositionend` event sometimes fires *after* the commit-Enter keydown has already reached xterm's `onData`. The blanket `if (composing) return;` guard dropped that Enter, so the user had to press Enter twice after typing Chinese. Guard now lets `\r` / `\n` / single control chars through while still dropping IME pre-edit text.
  2. Clipboard image paste is async (FileReader → `save_image` IPC → `attachFile` writes bracketed paste). If Enter arrived while that chain was still running, it raced ahead of the attachment and submitted the prior text with no image. Added a `pastePending` counter; Enter presses during an in-flight paste now wait on `pasteDone` before being written to the PTY.
- **Scroll-history overlay no longer shows garbage** — two capture issues causing the recent fragmented / red-rectangle artifact:
  1. Bare `\r` chars that survived `tmux capture-pane -J` caused xterm.js (with `convertEol: true`) to jump back to column 0 mid-line and let the next line's content overwrite the earlier text, leaving only line tails visible (e.g. `"    112 -)"` replacing `"    109  async def setup(..."`). Now stripped in Python.
  2. Unclosed `\x1b[41m` (or any SGR) bled its background across every subsequent row until a reset happened, producing the dark-red rectangle across the overlay. Each dedup'd line now gets a `\x1b[0m` reset appended.

### 修正
- **打中文/貼圖後按 Enter 第一次沒反應** — 兩個 race 合成同一個現象：
  1. WKWebView 上 `compositionend` 有時比 Enter keydown 晚一拍送到 xterm 的 `onData`，`if (composing) return;` 把那個 Enter 吃掉，使用者得按兩次。改成只擋 IME 組字中的多字元輸入，Enter / 控制字元一律放行。
  2. 貼圖是非同步流程（FileReader → `save_image` IPC → `attachFile` 寫 bracketed paste）。Enter 若在這段期間被打進來，會比附件先到 PTY，變成送出一則沒附圖的訊息。加了 `pastePending` 計數；paste 進行中的 Enter 會等 `pasteDone` resolve 後才寫進 PTY。
- **歷史卷動 overlay 不再出現亂碼與紅色方塊** — 兩個 capture 層面的問題:
  1. `tmux capture-pane -J` 輸出中殘留的裸 `\r` 會讓 xterm.js（`convertEol: true`）把游標拉回第 0 欄，被下一行內容覆寫，結果只剩行尾（像是「112 -)」蓋過「109 async def setup(...」）。Python 端直接移掉裸 `\r`。
  2. 有 SGR 跳脫（例如 `\x1b[41m`）沒收尾時，背景色會一路洩到後續每一行，渲染成那塊暗紅方塊。dedup 後每行尾端補上 `\x1b[0m` reset。

## v0.11.10 (2026-04-17)

### Fixes
- **`_blog` was a silent no-op (recursion bug since v0.9.3)** — bridge log (`/tmp/shellframe_bridge.log`) stopped updating on 2026-04-12 because `_blog` was calling itself recursively instead of opening the file. Every log write raised `RecursionError` and was swallowed by the outer `try/except`. Debugging stall / restore / echo issues was blind. Fixed to actually append to the log file.

### 修正
- **`_blog` 5 天前就徹底沒作用（v0.9.3 引入的遞迴 bug）** — `/tmp/shellframe_bridge.log` 自 2026-04-12 起就停在同一份內容。原因是 `_blog` 內部呼叫的是自己而不是開檔寫入，每次進去立刻 `RecursionError`、被外層 `try/except` 吃掉。debug stall / restore / echo 時看 log 一片靜音，完全沒線索。改成真的 append 到 log file。

## v0.11.9 (2026-04-17)

### Fixes
- **Pasted image paths no longer appear as typed text in Claude Code / Codex** — `attachFile` wrote file paths to the PTY with a direct `write_input`, so Claude Code saw typed characters and couldn't compress the attachment into `[image #N]`. Now wrapped with bracketed-paste escapes (`\x1b[200~` … `\x1b[201~`) so AI CLIs detect the paste and show their short `[image #N]` / `[Pasted text #N +Y lines]` previews. Plain (non-AI) sessions still get the raw path unchanged.

### 修正
- **貼圖檔名不再以「打字輸入」顯示在 Claude Code / Codex 裡** — `attachFile` 原本用 `write_input` 直接把路徑送進 PTY，AI CLI 看到的是一串字元而不是 paste，沒辦法壓成 `[image #N]` 附件預覽。現在 wrap 成 bracketed-paste 跳脫（`\x1b[200~` … `\x1b[201~`），AI CLI 能正確識別是 paste，顯示 `[image #N]` / `[Pasted text #N +Y lines]` 這種短標籤。純 terminal（非 AI）session 的貼入路徑維持原樣不包。

## v0.11.8 (2026-04-17)

### New Features
- **Window geometry persists across restart** — x/y/width/height are saved on move/resize (debounced) and on close, restored on launch. Absolute coords preserve the monitor on multi-display setups. Falls back to centered default if the saved position is no longer on any screen.
- **Sidebar state moved to config** — sidebar open/closed now persists in `config.settings.sidebar_open` instead of WKWebView localStorage (which was flaky across restarts). localStorage kept as fast-path / backward-compat fallback.
- **UI-editable session prompts** — both the one-shot UI session prompt (new AI sessions) and the per-turn TG preamble are now edited in Settings → General / Telegram Bridge. Empty textarea falls back to built-in defaults; explicit empty string turns TG preamble off. Anthropic prompt-caching makes per-turn injection effectively free after first turn, so feel free to make the preamble long.
- **Per-turn TG preamble** — every non-command TG message is now wrapped with a short mobile-format reminder before reaching the AI. Keeps replies skimmable over a long conversation (init-prompt drift was real). Defaults emphasise bullets, fenced code blocks, no tables / ASCII-art, and now also remind the AI that it can self-modify shellframe source + how to reload.
- **`sfctl permissions`** — new subcommand. macOS: opens Privacy panes (Files & Folders, Accessibility, Automation, Screen Recording, Full Disk Access) and optionally whitelists python / bun in ALF so "accept incoming connections" popups stop. Windows: adds Defender Firewall inbound allow rules for the bundled Python. `install.sh` / `install.ps1` print a hint to run it once post-install.

### Fixes
- **Startup crash when saved window position is off-screen** — pywebview's cocoa backend calls `window.screen()` in `windowDidMove_` during init and crashes with `AttributeError: 'NoneType' object has no attribute 'frame'` if no display hosts the initial point (e.g. after unplugging an external monitor). ShellFrame now pre-validates the saved x/y against `NSScreen.screens()` before passing them to `create_window`, drops stale coords from `config.json`, and falls back to centered. A defensive `try/except` around `create_window` itself provides a second retry without coords if anything slips past.
- **New-session race — couldn't type, tabs "stuck on latest session"** — `new_session` in main.py pings `_syncSessionsFromBackend` *before* returning, which ran while `openSession` was still awaiting the sid. Sync saw "backend has sid, frontend doesn't" and spawned a duplicate hidden-pane term via `reconnectSession`. Result: two terms for the same sid split the input. Fixed with `_uiCreatingSession` counter that blocks sync during the await window; externally-created sessions still get picked up on the next interval poll.
- **Restart always switched TG user to first session** — `_restore_user_routing()` existed but was never called. `_poll_loop` now invokes it on startup, so `_user_active` survives full app restarts (not just `sfctl reload`).
- **Stall warning fired on every long-running task** — the "no reply for 60s — macOS popup" warning used to fire any time the AI was just thinking. Now `_detect_blocking_popup()` checks `CGWindowListCopyWindowInfo` for real permission / auth dialog owners (`UserNotificationCenter`, `CoreServicesUIAgent`, `SecurityAgent`, etc.) and only fires TG / notification when one is actually visible. No popup → silent log-only.
- **Scroll-history overlay repeated CJK blocks 2–3×** — consecutive-prefix dedup couldn't collapse exact-duplicate redraw frames interleaved with spinner/status lines. Added second-pass visual-width dedup (CJK chars count 2 cells, threshold 8) so 4+ Chinese char lines get collapsed while short artifacts / dividers stay.

### 新功能
- **視窗位置跨 restart 保留** — x/y/寬/高 在拖拉/縮放時 debounce 存檔，關閉時再存一次，下次開啟讀回來。絕對座標保留你本來所在的螢幕（多螢幕設定仍在的前提下）。座標飄到螢幕外 → fallback 中央預設。
- **側欄狀態搬進 config** — 側欄開合狀態改存 `config.settings.sidebar_open`，不再只靠 WKWebView localStorage（WKWebView 在 app 重啟時常洗掉 localStorage）。仍寫一份到 localStorage 做 fast-path / 舊版相容。
- **UI 可編輯的 session prompt** — UI session 的一次性 init prompt 跟 TG 的 per-turn preamble 都搬到 Settings → General / Telegram Bridge 面板可編輯。空白就走內建預設；TG preamble 存成 `""` 代表關閉。Anthropic prompt cache 會把不變 prefix cache 住，per-turn 成本趨近於 0，放心寫長。
- **TG per-turn preamble** — 非指令的 TG 訊息會被前置一段 mobile-format 提醒再丟給 AI，解決長對話下 init prompt 漂移造成 AI 回覆越來越冗長、愛用 table / ASCII art 的問題。預設強調 bullets、fenced code、無表格，也會提醒 AI 可以自己改 shellframe source + 怎麼 reload。
- **`sfctl permissions`** — macOS 一鍵開 Privacy 各面板 + ALF 防火牆白名單 python/bun；Windows 幫 bundled Python 加 Defender 防火牆 inbound allow rule。`install.sh` / `install.ps1` 收尾會提示跑一次。

### 修正
- **儲存的視窗位置不在任何螢幕上時開不起來** — pywebview cocoa backend 啟動時會呼叫 `window.screen()`，若沒螢幕就 `None.frame()` 崩潰（外接螢幕拔掉、多螢幕設定改過等常見情境）。ShellFrame 現在在丟 x/y 給 `create_window` 之前，先用 `NSScreen.screens()` 驗證座標落在某台螢幕上；不在就從 `config.json` 刪掉、fallback 置中。另外 `create_window` 外包一層 try/except，真的還擋不住的話 retry 一次不帶座標。
- **開新 session 打不出字、切 tab 卡在最新的那個** — `main.py:new_session` 在 return 之前就通知 UI 同步，結果 `openSession` 還在 await 時 `syncSessionsFromBackend` 已經跑完、看到「backend 有、frontend 沒」就用 `reconnectSession` 造了一個 hidden 0x0 canvas 的重複 pane。同一個 sid 兩個 term 搶輸入。加 `_uiCreatingSession` counter 封住 await 窗口，外部 sfctl/TG 建的 session 下一輪 interval poll 還是會接。
- **Restart 後 TG 一律切到第一個 session** — `_restore_user_routing()` 有寫但從頭沒被 call 過。改成在 `_poll_loop` 開頭呼叫，full restart 也能保留 `_user_active`。
- **長任務就被誤判彈窗** — 以前 60s 沒回就警告「macOS popup 擋住」，AI 只是在想事情也會觸發。現在用 `CGWindowListCopyWindowInfo` 真的掃 `UserNotificationCenter` / `CoreServicesUIAgent` / `SecurityAgent` 等 popup owner，看到才發 TG；沒看到只寫 log。
- **上滑 scroll history 整塊中文行重複 2-3 次** — 連續 prefix dedup 抓不到被 spinner / status 打斷的「完全相同 redraw frame」。加第二輪 visual-width dedup（CJK 算 2 cells，門檻 8 cells），4 字以上中文行被摺掉，短分隔符 / 碎片保留。

## v0.11.7 (2026-04-17)

### New Features
- **`/fetch` TG command** — fetches the latest AI reply from the active session and sends it as a pinned message in your Telegram chat. Quick way to grab the most recent response without scrolling.

### 新功能
- **`/fetch` TG 指令** — 從目前 active session 擷取最新 AI 回覆，傳到 Telegram 並自動置頂。不用滑螢幕就能看到最新回覆。

## v0.11.6 (2026-04-16)

### New Features
- **INIT_PROMPT.md now teaches sessions about `sfctl` orchestration** — every new AI CLI session that gets the init prompt is told about the 6 orchestration verbs (`list`, `new`, `send`, `peek`, `rename`, `close`) and the master-session pattern (decompose → spin up workers → poll → aggregate → cleanup). No user-side prompting needed; Claude knows from session start.
- **Updated TG command cheatsheet in INIT_PROMPT** — reflects the audited command set (`/help`, merged `/update`, `/close` with confirm).

### 新功能
- **INIT_PROMPT.md 補上 sfctl orchestration 教學** — 每個新 AI CLI session 拿到 init prompt 時就會被告知 6 個 orchestration verb（`list` / `new` / `send` / `peek` / `rename` / `close`）跟 master-session 工作流（拆任務 → 開 worker → poll → 整合 → 收尾）。使用者不用每次自己講，Claude 開場就知道。
- **TG 指令表同步更新**（`/help`、合併的 `/update`、有 confirm 的 `/close`）。

## v0.11.5 (2026-04-16)

### Fixes
- **Scroll history overlay flashed and vanished** — v0.11.4's auto-close-on-bottom logic fired immediately on overlay open: `term.write(text)` emits `onScroll` per line while the content streams in, so the overlay hit its 2-bottom-touch threshold before the user even saw it. Removed the `onScroll` watcher entirely; the wheel-past-bottom handler now suffices and only fires on real user input (after content is already drawn).

### 修正
- **向上滑 overlay 只閃一下就消失** — v0.11.4 的 auto-close-on-bottom 在 overlay 打開瞬間就觸發：`term.write(text)` 每寫一行都會 `onScroll`，內容還在進來時已經累積過 2 次觸底門檻，使用者根本看不到。把 `onScroll` 監聽拔掉，只保留 wheel 往下滾超過 tail 的自動關，這只會在使用者真的操作時才觸發。

## v0.11.4 (2026-04-16)

### Fixes
- **Scroll history overlay no longer covers the sidebar** — moved from `document.body` with `position:fixed` to inside `#terminal-wrap` with `position:absolute`, so whatever sidebar state the user had (open/collapsed) stays visible and interactive behind the overlay.
- **Auto-close on scroll-to-bottom** — once you scroll back down to the tail of history, the overlay closes and live view returns. Two bottom-touches required so the initial `scrollToBottom` on open doesn't auto-close.
- **Auto-close on typing** — any non-navigation keystroke (printable char / Enter / Backspace) closes the overlay and forwards that keystroke to the live session, so typing feels continuous instead of "dead key, then have to dismiss, then retype". Arrow keys / PageUp/Down / modifiers still scroll the history terminal.

### 修正
- **上滾 overlay 不會再蓋到側欄** — 從 `document.body` `position:fixed` 搬到 `#terminal-wrap` 裡面 `position:absolute`，你原本開著的側欄就不會被遮。
- **滑到底自動關** — 滾到 history 最底自動關閉、回到 live view。需要「兩次觸底」才會關，所以開 overlay 時的初始 scrollToBottom 不會誤觸。
- **打字自動關** — 任何非導航按鍵（可見字元 / Enter / Backspace）都會關 overlay 並把那個按鍵轉送到 live session，打字不會斷。方向鍵 / PageUp/Down / 修飾鍵還是走 history terminal 的捲動。

## v0.11.3 (2026-04-16)

### Fixes
- **Scroll history overlay now renders as a real terminal, not a plain `<pre>`** — the v0.11.0–v0.11.2 dedupe overlay lost all ANSI colors, used the wrong font, and generally looked like a text modal instead of "looking at scrollback". Now the overlay embeds a second xterm.js instance with the same theme, font family, and unicode/fit addons as live sessions; `get_clean_history` captures with `tmux capture-pane -e` so ANSI escapes survive and are rendered by the history terminal. Dedup still works because comparison strips ANSI first. The history terminal is read-only (`disableStdin: true`) and scrollback is sized to the content.

### 修正
- **上滾 overlay 改用真正的 xterm.js 渲染** — v0.11.0–v0.11.2 用 `<pre>` 顯示，丟了 ANSI 顏色、字體也錯，看起來像文字 modal 不是「看 scrollback」。現在 overlay 內嵌第二個 xterm.js 實例，主題、字體、fit/unicode addon 都跟 live session 一致；`get_clean_history` 改用 `tmux capture-pane -e` 保留 ANSI escape，history terminal 原生渲染。dedup 照舊（比對前先 strip ANSI）。History terminal 是唯讀（`disableStdin: true`），scrollback 會根據內容自動放大。

## v0.11.2 (2026-04-16)

### Fixes
- **Scroll-history overlay survived tab switches** — v0.11.1 attached the overlay to the session pane, so switching tabs only hid the pane (and overlay with it) via CSS; switching back re-revealed the overlay. Moved overlay to a global `ScrollHistory` singleton attached to `document.body` with `position:fixed`, and `switchTab()` now calls `ScrollHistory.close()` so tab switches always recover into a clean state.

### 修正
- **上滾 overlay 切 tab 也活著** — v0.11.1 把 overlay 掛在 session pane 裡，切 tab 只是 CSS `display:none` 把整個 pane 連 overlay 一起藏起來，切回來又露出。改成全域 `ScrollHistory` 單例、掛 `document.body` 用 `position:fixed`，`switchTab()` 會主動呼叫 `ScrollHistory.close()`，切 tab 一定乾淨。

## v0.11.1 (2026-04-16)

### Fixes
- **Scroll history overlay left the terminal unresponsive after closing** — v0.11.0's overlay closed with `display:none`, but focus never went back to xterm.js, so keystrokes landed on `document.body` and the pane felt dead until the user switched tabs. Now closing the overlay calls `term.focus()`, the overlay is fully `.remove()`d each time (no stale listeners), wheel events while open go to overlay instead of triggering a re-open, and Esc uses a one-shot capture listener scoped to the current overlay.

### 修正
- **向上滾 overlay 關掉後終端機變死** — v0.11.0 overlay 關掉用的是 `display:none`，但 focus 沒回到 xterm.js，按鍵全掉到 `document.body`，要切 tab 才會恢復。改為：關 overlay 時主動 `term.focus()`、overlay 每次真的 `.remove()`（不留殘留 listener）、overlay 開著時滾輪只作用在 overlay 自己、Esc 綁成一次性 capture listener 跟當次 overlay 綁死。

## v0.11.0 (2026-04-16)

### New Features
- **Master-session orchestration via `sfctl`** — `sfctl` now exposes verbs for driving other ShellFrame sessions from inside one: `sfctl new <cmd> [--label X]`, `sfctl send <sid> "<text>"`, `sfctl peek <sid> [--lines N]`, `sfctl rename <sid> <name>`, `sfctl list`, `sfctl close <sid>`. Enables "master Claude session dispatches work to worker sessions and polls results" pattern without touching tmux directly. `sfctl peek` uses the same prefix-dedup logic as the scroll overlay, so output is clean even for streaming TUI apps.

### Fixes
- **Scroll-up no longer shows duplicated streaming frames** — tmux copy-mode was capturing every intermediate frame of Claude Code's streaming (partial lines like `1. 想一下...` → `1. 想一下你...` → `1. 想一下你哪...`), making scrollback look like the same line pasted 20 times. Scroll-up at the xterm top now snapshots the pane via `tmux capture-pane -p -J`, collapses consecutive prefix-duplicate lines (longest wins), and shows the cleaned text in a native overlay modal. Select + copy supported; Esc or click-backdrop to close. Copy-mode avoided entirely.

### 新功能
- **Master session orchestration 透過 `sfctl`** — `sfctl` 新增一組 verb 讓你從某個 session 裡指揮其他 session：`sfctl new <cmd> [--label X]`、`sfctl send <sid> "<text>"`、`sfctl peek <sid> [--lines N]`、`sfctl rename <sid> <name>`、`sfctl list`、`sfctl close <sid>`。讓「master Claude session 指派工作給 worker session、再 poll 結果」的流程不用直接碰 tmux。`sfctl peek` 套用跟 scroll overlay 同一套 prefix-dedup，streaming TUI 輸出也乾淨。

### 修正
- **向上滾不會再看到重複的 streaming frame** — tmux copy-mode 會 capture Claude Code streaming 的每個中間狀態（`1. 想一下...` → `1. 想一下你...` → `1. 想一下你哪...`），所以滾上去是一堆幾乎一樣的行。改用：滾到 xterm 頂端時，`tmux capture-pane -p -J` 抓 pane snapshot，連續 prefix-duplicate 行壓縮成最長的那行，用 native overlay modal 顯示。支援選取複製；Esc 或點背景關閉。完全繞過 copy-mode。

## v0.10.12 (2026-04-16)

### Changes
- **Slash command audit — 11 → 9 commands**:
  - `/status` folded into `/list` — `/list` output now starts with a bridge state header (`connected ● @ @bot`). `/status` still works as an alias but is no longer in the BotFather menu.
  - `/update_now` collapsed into `/update` — `/update` now shows an inline keyboard with "⬇️ Update Now" / "Cancel" buttons when a new version is available. `/update_now` still works as a back-compat alias that skips the check step.
  - `/close` now requires inline-keyboard confirmation — accidental `/close` in the middle of a chat no longer instantly kills the active session.

### 變更
- **Slash 指令精簡 — 11 → 9 個**：
  - `/status` 合併到 `/list` — `/list` 開頭多了一行 bridge state header（`connected ● @ @bot`）。`/status` 還是通的（alias），但不再出現在 BotFather 選單。
  - `/update_now` 合併到 `/update` — 檢查到有新版時直接吐出 inline keyboard「⬇️ Update Now / Cancel」兩顆按鈕，一次點到位。`/update_now` 保留當 alias（直接套用、跳過檢查）。
  - `/close` 現在要 inline confirm — 聊天聊一半不小心 `/close` 不會再瞬殺 active session。

## v0.10.11 (2026-04-16)

### Fixes
- **Slash commands now give instant visible ACK** — every recognized bridge command (`/reload`, `/restart`, `/update`, `/list`, …) now reacts with 👀 on the user's message the moment it's dispatched, before any processing. User sees confirmation even if the command takes a while or subsequent `sendMessage` calls are delayed.
- **`/help` added** (alias for `/start`) — full command cheat sheet. Registered in BotFather command menu so it shows up in the TG client's slash-menu. `/start` response rewritten to be more structured (sessions / bridge control / app control / forward-to-CLI).
- **Watchdog stall threshold halved** — 120s → 60s. If the poll loop wedges (e.g. mid-bot-conflict, bad wake from sleep), `/reload` is reachable ~2x faster.

### 修正
- **Slash 指令立刻有視覺回饋** — 所有認得的 bridge 指令（`/reload`、`/restart`、`/update`、`/list` 等）一進來就立刻對原訊息加 👀 reaction，在任何處理開始之前。使用者不會再有「沒反應」的錯覺，就算後續 sendMessage 慢也看得到「收到了」。
- **加 `/help`** — `/start` 的 alias，完整指令清單。有登記到 BotFather 命令選單，TG client 的 slash menu 直接看得到。`/start` 訊息也重寫得更有結構（sessions / bridge control / app control / forward-to-CLI）。
- **Watchdog stall 門檻砍半** — 120s → 60s。polling 卡死時（例如 bot 衝突中、sleep 醒來 socket 掉），`/reload` 大約 1 分鐘內就能再通，而不是 2 分鐘。

## v0.10.10 (2026-04-15)

### Fixes
- **Surface Telegram 409 Conflict loudly** — if another process is polling the same bot token (same token on a second machine / old instance not killed / colleague running the same bot), Telegram returns HTTP 409 and rotates which poller gets each update. Before, `_poll_loop` silently retried every 5s and the bridge status stayed "connected" even though messages were being eaten by the other poller. Now detect 409, emit an error status with `conflict: True`, notify allowed users via TG, and back off to 30s retry so we don't spam Telegram with conflicting requests.

### 修正
- **TG 409 Conflict 明確報警** — 同一個 bot token 被多個 process polling（同 token 跑在兩台機器、舊 instance 沒關乾淨、同事測試用了同一個 bot）時，Telegram 回 HTTP 409，訊息會被其他 poller 截走。舊版 `_poll_loop` 每 5 秒靜默重試、狀態還顯示 "connected"，使用者只覺得「TG 都沒反應」。現在偵測到 409 會 emit error status（含 `conflict: True`）、透過 TG 通知 allowed users、並 back off 到 30 秒避免互相干擾。

## v0.10.9 (2026-04-15)

### Fixes
- **Bridge polling watchdog** — if the TG poll loop goes >120s without a network round-trip (hung DNS, stuck socket, long sleep + wake hiccup), a watchdog thread now auto-triggers `hot_reload_bridge()` to rebuild the polling. Prevents "TG completely silent, even `/reload` doesn't work" situations.
- **`sfctl restart`** — added alongside existing `sfctl reload` / `status`. Lets Howard (or any user with terminal access) force a full app restart even when TG is totally wedged. `sfctl` IPC uses file-based command passing through `_start_command_watcher`, so it works independent of bridge polling state.

### 修正
- **TG polling watchdog** — TG poll loop 超過 120 秒沒任何 network round-trip（DNS 卡死、socket hang、長 sleep 醒來斷線），watchdog thread 會自動觸發 `hot_reload_bridge()` 重建 polling。避免「TG 完全沒反應、連 `/reload` 都沒用」的情境。
- **`sfctl restart`** — 新增，跟既有的 `sfctl reload` / `status` 並列。在 TG 完全死掉時還能從 terminal 強制完整重啟（`sfctl` 走 file IPC，跟 bridge polling 狀態無關）。

## v0.10.8 (2026-04-15)

### Fixes
- **Bridge stalled when display slept** — macOS App Nap throttled the Python process to ~1 tick/minute once the screen turned off or the window was backgrounded, so TG polling and PTY readers effectively froze. Now opt out via `NSProcessInfo.beginActivityWithOptions_reason_` with `NSActivityUserInitiated | NSActivityLatencyCritical` at startup. Lid-close full system sleep still sleeps the Mac (that's intentional) — Telegram holds messages 24h and redelivers on wake.

### 修正
- **螢幕關掉 bridge 就停擺** — macOS App Nap 把 Python process 節流到約每分鐘才跑一次，TG polling 跟 PTY reader 實質都凍住。啟動時透過 `NSProcessInfo.beginActivityWithOptions_reason_` 以 `NSActivityUserInitiated | NSActivityLatencyCritical` 退出 App Nap。闔蓋整機 sleep 還是會睡（這是該睡的），但 Telegram 保留訊息 24 小時、醒來會重送。

## v0.10.7 (2026-04-15)

### Fixes
- **Ctrl+Click on hard-wrapped URLs** — WebLinksAddon only scans one buffer row, so CLI tools like Claude Code that hard-wrap long URLs across multiple lines broke Ctrl+Click. New link provider walks adjacent full-width lines ending on URL-safe chars, stitches them, and registers per-row link ranges that activate with the reconstructed full URL. Added `Api.open_url()` in Python for http(s) — `open_local_file` can't handle URLs because it checks `p.exists()`.

### 修正
- **Ctrl+Click 斷行的 URL** — WebLinksAddon 只看單一 buffer row，所以 Claude Code 之類會硬換行的 CLI 把長 URL 斷到兩行後 Ctrl+Click 失靈。新增 link provider：往前後掃連續滿行且結尾是 URL 字元的行，拼回完整 URL 再分別在每一行註冊 link。Python 端新加 `Api.open_url()` 處理 http(s)（原本的 `open_local_file` 會因為 `p.exists()` 判斷失敗）。

## v0.10.6 (2026-04-15)

### Fixes
- **`/update` failed with "fatal: not a git repository" for zip-based installs** — users who extracted a zip (no `.git` dir) couldn't update at all. `do_update` now pre-checks for `.git` and, if missing, auto-runs `install.sh` via curl|bash; install.sh in turn converts a non-git install dir into a git clone in-place (`git init` + add remote + `git reset --hard origin/main`). Also hardened install.sh's existing git update path with auto-stash + force-sync fallback to match `do_update`.

### 修正
- **zip 安裝的使用者 `/update` 會爆 "fatal: not a git repository"** — 沒 `.git` 的目錄根本無法更新。`do_update` 現在會先檢查 `.git`，沒有就自動 curl|bash 跑 `install.sh`；install.sh 本身也升級了：偵測到目錄有檔案但沒 `.git`，會 `git init` + `fetch` + `reset --hard` 原地轉成 git clone。install.sh 原本的更新路徑也補上 auto-stash + force-sync fallback，跟 `do_update` 行為對齊。

## v0.10.5 (2026-04-15)

### Fixes
- **`/update` no longer bricks on dirty tree or divergent HEAD** — `do_update` now auto-stashes local changes before pulling and, if `git pull --ff-only` fails, falls back to `git fetch && git reset --hard origin/main` so users never get stuck on an old version with no way forward.
- **`pip install` now recoverable** — use `python -m pip` (portable across Win/Mac venv layouts) and if install fails, recreate `.venv` from scratch and retry once. Same hardening in `_self_heal_venv` at startup.
- **Startup crash now surfaces recovery hint** — top-level try/except in `main()` writes `~/.shellframe-crash.log` and, on macOS, pops an `osascript` dialog with the install.sh one-liner. Windows under pythonw previously swallowed crashes silently.
- **Update errors return a recovery field** — `do_update` result now includes `recovery` with the install.sh one-liner on any failure path, so the UI can show users a concrete next step.

### 修正
- **`/update` 再也不會把髒樹或 diverge 的 HEAD 搞死** — `do_update` 先 auto-stash 本地改動，`git pull --ff-only` 若失敗自動 fallback 到 `git fetch && git reset --hard origin/main`，使用者不會卡在舊版走不下去。
- **`pip install` 可救援** — 改用 `python -m pip`（Win/Mac venv 結構通用），失敗時砍掉 `.venv` 重建再試一次。startup 的 `_self_heal_venv` 也上同一套邏輯。
- **啟動當掉會吐救援指令** — `main()` 外層 try/except 會把 traceback 寫到 `~/.shellframe-crash.log`，macOS 還會跳 `osascript` dialog 顯示 install.sh 一行救命指令。Windows 的 pythonw 原本會靜默吞掉 crash。
- **更新失敗會回 recovery 欄位** — `do_update` 任何失敗路徑現在都會帶 `recovery` 欄位附上 install.sh 一行指令，UI 可直接顯示給使用者。

## v0.10.4 (2026-04-15)

### Fixes
- **New sessions sometimes needed UI reload to appear** — UI learned about non-UI session changes (TG `/new`, sfctl) only via the 1.5s bridge-status poll, which could miss on slower machines or when the bridge polling hiccuped. Now `new_session()` / `close_session()` push directly to the window via `evaluate_js` so the UI reconciles immediately.

### 修正
- **新 session 有時要 reload UI 才看得到** — UI 原本只靠 1.5s 一次的 bridge status polling 來偵測非 UI 建立的 session（TG `/new`、sfctl），在慢機或 polling 卡到時會漏掉。改為 `new_session()` / `close_session()` 主動 `evaluate_js` 通知 UI 立即 reconcile。

## v0.10.3 (2026-04-15)

### Fixes
- **Windows `cp950` UnicodeEncodeError on session add** — `save_config` used `pathlib.write_text()` without `encoding=`, so zh-TW Windows hit the `cp950` codec which can't encode preset icons like `▶`. Every `open()`/`read_text()`/`write_text()` for config/log/IPC/filter files now forces `encoding='utf-8'`.
- **TG `/reload` silenced replies** — `hot_reload_bridge()` rebuilt `TelegramBridge` without restoring `_user_active` / `_user_chat` / `_default_active_sid`, so the flush loop had no chat_ids to send AI responses back to. Now snapshots user routing state before stop and restores it (filtering out sids that disappeared).
- **TG-created AI sessions missed init prompt** — Init prompt injection lived in `write_input()` (web UI path only); TG `slot.write_fn` bypassed it, so sessions started via TG `/new` didn't know about the bridge. New `consume_init_prompt_if_ready()` helper exposed to the bridge via `on_consume_init`; `_handle_message` injects on the first forwarded message once CLI is ready.
- **setup.py version hardcoded to 0.2.5** — py2app plist stamped the wrong version. Now reads `version.json` at build time.

### New Features
- **Report Issue button in About modal** — Opens pre-filled GitHub issue with current version + platform.

### 修正
- **Windows 新增 session 炸 `cp950` 錯誤** — `save_config` 寫檔沒指定 `encoding=`，繁中 Windows 走 `cp950` 編不動 preset icon `▶`。所有 config / log / IPC / filter 檔的 open/read/write 一律 `encoding='utf-8'`。
- **TG `/reload` 後沒有回覆** — `hot_reload_bridge()` 重建 bridge 時沒還原 `_user_active` / `_user_chat` / `_default_active_sid`，flush loop 找不到 chat_id 送不出 AI 回覆。現在 stop 前先 snapshot、start 前還原（並過濾已消失的 sid）。
- **TG 建的 AI session 缺 init prompt** — init 注入只在 web UI 的 `write_input()`；TG 的 `slot.write_fn` 直接 bypass，導致 TG `/new` 開的 claude session 不知道 bridge 存在。新增 `consume_init_prompt_if_ready()` 經 `on_consume_init` 曝給 bridge，首封訊息在 CLI ready 時注入。
- **setup.py 版號寫死 0.2.5** — py2app 產出的 plist 版號錯誤。改為 build 時讀 `version.json`。

### 新功能
- **About modal 加 Report Issue 按鈕** — 直接開 GitHub issue，預填版本與平台。

## v0.10.2 (2026-04-14)

### Fixes
- **Full upgrade on update** — `do_update()` now runs pip install + refreshes .app bundle after git pull (previously only did git pull, leaving stale venv and .app). Users upgrading from v0.3.0 had missing APIs and no app icon because these steps were skipped.
- **Startup self-heal** — On launch, if key packages (pyte) are missing, auto-runs `pip install -r requirements.txt`. Catches users who upgraded via `git pull` without re-running install.sh.
- **Info.plist version stamp** — install.sh now writes the current version from version.json into the .app bundle's Info.plist (was hardcoded to v0.1.0 forever).
- **Ctrl+Click paths with spaces** — Two-pass regex: quoted paths (`"..."`, `'...'`, `` `...` ``) match fully including spaces; unquoted paths support backslash-escaped spaces (`path\ with\ spaces`).

### 修正
- **完整升級流程** — `do_update()` 在 git pull 之後會跑 pip install + 重新複製 .app bundle（之前只做 git pull，venv 和 .app 都是舊的）。從 v0.3.0 升級的使用者因為缺了這些步驟，拖放功能和 app icon 都壞了。
- **啟動自我修復** — 啟動時如果偵測到 pyte 沒裝，自動跑 `pip install -r requirements.txt`。讓只跑 `git pull` 沒跑 install.sh 的使用者也能正常啟動。
- **Info.plist 版號同步** — install.sh 會把 version.json 的版號寫進 .app 的 Info.plist（之前永遠是 v0.1.0）。
- **Ctrl+Click 有空格的路徑** — 兩階段 regex：引號包住的路徑完整匹配（含空格）；裸路徑支援反斜線 escape 空格。

## v0.10.1 (2026-04-13)

### Fixes
- **Drag & drop non-image files broken** — `file.path` is an Electron-only property; WKWebView's File API only exposes `file.name` (no directory). Non-image files dragged into ShellFrame got just the filename, not the full path. Fix: read file content via FileReader → save to `~/.claude/tmp/` via `save_file_from_clipboard` → use the saved full path. Also supports dropping multiple files in one gesture.

### 修正
- **拖放非圖片檔案路徑遺失** — `file.path` 只有 Electron 才有，WKWebView 的 File API 只提供 `file.name`（沒有目錄路徑）。非圖片檔案拖入 ShellFrame 只拿到檔名。修法：用 FileReader 讀內容 → 存到 `~/.claude/tmp/` → 使用完整路徑。同時支援一次拖放多個檔案。

## v0.10.0 (2026-04-12)

### New Features
- **Selection auto-scroll** — Drag to select text near the top/bottom edge of the terminal and the viewport scrolls automatically to extend the selection. 30px edge zone, 3 lines per 80ms tick.

### Fixes
- **Invisible typing on new session** — `term.open()` was called while the pane had `display: none`, causing xterm.js to initialize with a 0×0 canvas. Keystrokes were sent to the PTY but not rendered. Fix: make the pane visible (`active` class) before calling `term.open()`.
- **Cmd+] jumped to unbridged tabs** — Keyboard shortcut cycling included unbridged sessions (e.g., "claude TG") mixed between numbered tabs. Now skips unbridged sessions when bridge is active; they're still reachable by click.
- **Bridge-disabled sessions reset on restart** — `_bridge_enabled` was only stored in memory. On restart all sessions defaulted back to enabled. Now persists disabled session IDs to `config.bridge_disabled_sessions`.

### 新功能
- **選取自動滾動** — 拖拉選取文字到終端機邊緣時 viewport 會自動滾動延伸選取範圍。30px 邊緣區，每 80ms 滾 3 行。

### 修正
- **新 session 打字看不到** — `term.open()` 在 `display: none` 的 pane 上執行，xterm.js canvas 初始化為 0×0，按鍵有送到 PTY 但畫面沒渲染。修法：在 `term.open()` 之前先讓 pane visible。
- **Cmd+] 跳到 unbridged tab** — 鍵盤切換包含了 unbridged session（如 "claude TG"）夾在有編號的 tab 之間。改成 bridge 啟用時只在 bridged sessions 之間切。
- **Bridge-disabled session 重啟後重置** — `_bridge_enabled` 只存在記憶體，重啟後全部回到 enabled。改為持久化到 `config.bridge_disabled_sessions`。

## v0.9.3 (2026-04-12)

### Fixes
- **Memory leak prevention: xterm.js `term.dispose()`** — Closing a tab removed the DOM pane but didn't dispose the xterm.js Terminal instance, leaking WebGL contexts, buffers, and addon state. Now calls `term.dispose()` in both `closeTab` and `syncSessionsFromBackend`.
- **Log file auto-truncation** — Debug log (`shellframe_debug.log`) and bridge log (`shellframe_bridge.log`) now auto-truncate at 1MB (keeps the last half). Previously grew unbounded.
- **pyte history buffer capped** — Bridge's per-session pyte HistoryScreen reduced from 10,000 to 3,000 lines. At 6 sessions with full history, this cuts worst-case memory from ~960MB to ~288MB.
- **Bridge log refactored** — All 21 direct `open(_LOG_FILE, 'a')` calls replaced with `_blog()` helper that handles the auto-truncation centrally.

### 修正
- **記憶體洩漏防治：xterm.js `term.dispose()`** — 關分頁時只移除了 DOM pane 但沒 dispose xterm Terminal 實例，WebGL context、buffer、addon 都會洩漏。現在 `closeTab` 和 `syncSessionsFromBackend` 都會呼叫 `term.dispose()`。
- **Log 自動截斷** — debug log 和 bridge log 超過 1MB 自動砍半。之前無上限持續長大。
- **pyte 歷史 buffer 封頂** — Bridge 的 per-session pyte HistoryScreen 從 10,000 行降到 3,000 行。6 個 session 全跑滿時記憶體從 ~960MB 降到 ~288MB。
- **Bridge log 重構** — 21 處直接 `open(_LOG_FILE)` 改用 `_blog()` 集中處理截斷邏輯。

## v0.9.2 (2026-04-12)

### New Features
- **Shift+Enter = newline** — Press `Shift+Enter` to insert a new line without submitting the message. Works in Claude Code, Codex, and other AI CLIs. Toggle in Settings → General. Sends `\n` instead of `\r`.
- **Hardened `.gitignore`** — `.claude/`, personal draft files, `.env`, `config.json`, and runtime artifacts are now gitignored to prevent accidental commit of private data to the public repo.

### Fixes
- **GitHub Releases created** — Tags v0.4.0 through v0.9.1 now have proper GitHub Release objects with bilingual release notes. Previously only tags existed with no Release page.

### 新功能
- **Shift+Enter 換行不送出** — 按 `Shift+Enter` 可以插入換行但不送出訊息。支援 Claude Code、Codex 等 AI CLI。在設定 → 一般可以開關。送 `\n` 而非 `\r`。
- **強化 `.gitignore`** — `.claude/`、個人草稿、`.env`、`config.json` 和 runtime 產物全部加入 gitignore，防止私人資料被推到公開 repo。

### 修正
- **GitHub Releases 補建** — v0.4.0 到 v0.9.1 的 tag 都補建了 GitHub Release，附完整雙語 release notes。之前只有 tag 沒有 Release 頁面。

## v0.9.1 (2026-04-12)

### New Features
- **Preset drag reorder** — Settings presets now have a ☰ grip handle. Drag to reorder; saved to config immediately. Uses mouse-based drag (HTML5 drag/drop is unreliable in WKWebView).
- **Auto-detect OS language** — First-time users get `zh-TW` on Chinese systems, `en` on everything else. Saved preference still overrides.
- **PR/issue review workflow** — `.github/REVIEW_WORKFLOW.md` added as the playbook for incoming PRs and issues. Used by the daily Claude Code review agent.

### Fixes
- **Enter key latency** — Single keystrokes (including Enter) now bypass the `setTimeout(0)` microbatch and send immediately to the PTY. Debug log file I/O also skipped for single-char writes.
- **File path underline misaligned with CJK text** — Wide characters (中文) before a path shifted the link underline left. Fixed by building a char-to-column map using `getCell().getWidth()`.
- **Rename modal: IME Enter submitted prematurely** — Safari/WKWebView fires `compositionend` before `keydown`, so `isComposing` was already `false`. Added `_justComposed` 150ms guard + `keyCode === 229` fallback.
- **New-tab modal showed stale presets** — Presets added in Settings didn't appear until page reload. Now calls `renderPresets()` every time the modal opens.
- **Sidebar "TG off" section UX** — Sessions below the divider are no longer grayed out (they're functional, just not TG-bridged). Divider text and tooltip explain the purpose. Badge changed from "TG" to "own"/"自管". Drag highlight optimized from O(N) querySelectorAll to O(1) single-element tracking.
- **Sidebar divider + badge i18n** — All sidebar text now uses `t()` for proper English/Chinese switching.

### 新功能
- **Preset 拖拉排序** — 設定裡的 preset 列表有 ☰ 把手，拖拉排序後自動存檔。使用 mouse-based drag（WKWebView 不支援 HTML5 drag/drop）。
- **自動偵測 OS 語言** — 第一次啟動的使用者，中文系統預設 `zh-TW`，其他一律 `en`。手動選過的語言優先。
- **PR/issue 審查流程** — 新增 `.github/REVIEW_WORKFLOW.md` 作為 PR 和 issue 的審查 checklist，給 daily Claude Code review agent 使用。

### 修正
- **Enter 鍵延遲** — 單一按鍵（含 Enter）不再經過 `setTimeout(0)` microbatch，直接送到 PTY。debug log 也不再對單字元寫入做檔案 I/O。
- **檔案路徑底線在中文後偏移** — 寬字元佔 2 columns 但 `translateToString` 只回 1 字元，用 `getCell().getWidth()` 建 char→column 映射修正。
- **改名 modal IME Enter 提前送出** — Safari 的 `compositionend` 在 `keydown` 之前 fire，加了 `_justComposed` 150ms 保護 + `keyCode === 229` fallback。
- **新增 tab 的 preset 列表沒更新** — 在 Settings 新增的 preset 要 reload 才出現。改成每次開 modal 都 `renderPresets()`。
- **側邊欄「TG off」區 UX** — 不再灰掉（這些 session 能正常用，只是不走 ShellFrame TG bridge）。divider 文字 + tooltip 說明用途。badge 從 "TG" 改成 "own" / "自管"。拖拉高亮從 O(N) 優化到 O(1)。
- **側邊欄 divider + badge 雙語** — 所有側邊欄文字改用 `t()` 走 i18n 系統。

## v0.9.0 (2026-04-12)

### New Features
- **Ctrl+Click to open file paths** — Local file paths in terminal output (Unix `/foo/bar`, Windows `C:\foo\bar`, `~/foo`, `./relative`) are now clickable. Ctrl+Click (Cmd+Click on macOS) opens them in the OS default app via `os.startfile` / `open` / `xdg-open`. URL schemes like `https://` are excluded by lookbehind.
- **Cross-platform `tempfile.gettempdir()` for IPC + logs** — On Windows, `_CMD_FILE`, `_RESULT_FILE`, and `_LOG_FILE` now live in `%TEMP%` instead of the hardcoded `/tmp` path that didn't exist. macOS/Linux still use `/tmp` for backward compat with existing installs.
- **Windows clipboard support** — `copy_text` and `paste_text` now use `clip.exe` (UTF-16LE) and PowerShell `Get-Clipboard -Raw` on Windows, plus xclip/wl-copy fallback on Linux. Was macOS-only (pbcopy/pbpaste).
- **Windows-aware `restart_app`** — TG `/restart` and the manual restart button now spawn `cmd /c start shellframe.bat` (detached) on Windows, with `pythonw.exe main.py` as a second fallback. The macOS path (`open -n -a ShellFrame.app`) still wins on macOS.
- **Windows-aware STT install** — The "安裝本地 STT" button now picks the right package manager: Homebrew on macOS, `winget install ggerganov.whisper.cpp` then chocolatey on Windows. Model download is shared (urllib).
- **Windows soft session persistence** — On platforms without tmux, ShellFrame writes the open session list (`{sid, cmd}`) to `config.session_list` whenever sessions are created/closed, and recreates them as fresh PTYs on next launch. UX-equivalent to "where I left off" but without scrollback. tmux platforms are unaffected.
- **`WINDOWS.md`** — New top-level doc covering install, requirements, what works, known limitations, file locations, and troubleshooting on Windows.

### Fixes
- **Self-restart loop on Windows** — Same `_save_offset()` race fix as v0.7.2 now applies cross-platform via the new tmp dir path.
- **`_tmux_capture` early return on Windows** — Returns immediately if `IS_WIN` or `tmux` not on PATH instead of letting `subprocess` raise `FileNotFoundError` repeatedly. The pyte fallback path was already wired up but now skips the noise.

### 新功能
- **Ctrl+Click 開啟檔案路徑** — 終端機輸出裡的本地檔案路徑（Unix `/foo/bar`、Windows `C:\foo\bar`、`~/foo`、`./relative`）現在可以點選。Ctrl+Click（macOS 是 Cmd+Click）會用 OS 預設程式開啟（macOS 的 `open` / Windows 的 `os.startfile` / Linux 的 `xdg-open`）。URL scheme 如 `https://` 會被 lookbehind 排除。
- **跨平台暫存目錄** — Windows 上 IPC 和 log 改用 `%TEMP%`，不再硬寫死 `/tmp`（Windows 沒這路徑）。macOS / Linux 維持 `/tmp` 維持向下相容。
- **Windows 剪貼簿** — `copy_text` / `paste_text` 在 Windows 改用 `clip.exe` 和 PowerShell `Get-Clipboard -Raw`，Linux 加 xclip/wl-copy fallback。原本只支援 macOS。
- **Windows `restart_app`** — TG `/restart` 和手動重啟按鈕在 Windows 會用 `cmd /c start shellframe.bat`（detached）；fallback 是 `pythonw.exe main.py`。macOS 維持 `open -n -a`。
- **Windows STT 安裝** — 「安裝本地 STT」按鈕在 Windows 用 `winget install ggerganov.whisper.cpp`，沒 winget 才試 chocolatey。模型下載走 urllib 跨平台共用。
- **Windows session 軟性持久化** — 沒 tmux 的平台會把 session 列表寫到 `config.session_list`，下次啟動時重建為全新 PTY。功能上等於「打開時恢復我上次的 tab」，但拿不回 scrollback。tmux 平台不受影響。
- **`WINDOWS.md`** — 新的頂層文件，說明 Windows 安裝、需求、可用功能、已知限制、檔案位置、疑難排解。

### 修正
- **Windows 自重啟迴圈** — v0.7.2 的 `_save_offset()` 修法現在跨平台都生效。
- **`_tmux_capture` 在 Windows 早退** — 偵測到 `IS_WIN` 或 PATH 上沒 `tmux` 就直接回空字串，不會讓 subprocess 一直 raise `FileNotFoundError`。pyte fallback 路徑早就接好了，現在只是不再有干擾 log。

## v0.8.0 (2026-04-11)

### Breaking — STT is now plugin-driven
- **No hardcoded STT servers in the repo.** The previous build shipped specific intranet IPs (192.168.51.151, 192.168.51.197) baked into `bridge_telegram.py`. That made the project unusable for anyone else and leaked a personal infra detail. Removed.
- **Provider chain via config** — `config.bridge.stt_providers` is now a JSON list. Each provider entry: `{name, url, field, health?, query?, result_keys?}`. Bridge tries them in order; first non-empty response wins.
- **Plugin file hook** — Drop a Python module at `~/.config/shellframe/stt_plugin.py` exporting `transcribe(audio_path: str) -> str`. Tried before built-in backends. Lets you wire any STT (cloud API, custom binary, sub-process) without modifying ShellFrame source.
- **Backends**: `auto` (plugin → local → remote chain) / `plugin` / `local` (whisper.cpp) / `remote` / `off`.
- **Settings UI** rewritten: providers are edited as a JSON textarea with placeholder example. Status panel shows each provider's individual reachability.
- **Migration**: if you used the v0.7 hardcoded chain, paste your endpoints into Settings → Telegram Bridge → 🎙 STT → Providers and save.

### Fixes
- **Dropped `stt_remote_url`** legacy field — replaced by the provider list.
- **`_transcribe_voice` failure message** now lists each endpoint individually with its error so you can see which one(s) failed.

### 重大改動 — STT 改為 plugin 架構
- **Repo 不再硬寫 STT 伺服器位址。** 上一版把私人內網 IP（192.168.51.151、192.168.51.197）寫進 `bridge_telegram.py`，這對其他使用者完全沒用而且洩漏個人 infra 設定。移除。
- **改用 config 設定 provider chain** — `config.bridge.stt_providers` 是 JSON 陣列，每筆 provider：`{name, url, field, health?, query?, result_keys?}`。Bridge 依序嘗試，第一個有回應的勝出。
- **Plugin 檔案介面** — 在 `~/.config/shellframe/stt_plugin.py` 放一個 Python module 並 export `transcribe(audio_path: str) -> str`，會在內建後端之前先試。可以接任何 STT（雲端 API、自製 binary、子進程）而不用改 ShellFrame 原始碼。
- **後端**: `auto`（plugin → local → remote chain）/ `plugin` / `local`（whisper.cpp）/ `remote` / `off`。
- **設定 UI 改寫**：providers 用 JSON textarea 編輯，附 placeholder 範例。狀態面板顯示每個 provider 各自的連線狀況。
- **遷移**：v0.7 hardcoded chain 的使用者，把端點貼到 設定 → Telegram Bridge → 🎙 STT → Providers 然後存檔即可。

## v0.7.1 (2026-04-11)

### New Features
- **STT backend selection** — Settings → Telegram Bridge gains a 🎙 STT panel: pick `Auto` (local first → remote), `Local` (whisper.cpp), `Remote` (faster-whisper server), or `Off`. Local backend uses `whisper-cli` + a downloaded `ggml-base.bin` model. Status pill shows which backends are reachable; an "安裝本地 STT" button runs `brew install whisper-cpp` and downloads the model into `~/.local/share/shellframe/whisper-models/`.
- **TG `/restart`** — Trigger full app restart from Telegram. Sessions persist via tmux reattach.
- **TG `/update`** + **`/update_now`** — Check for ShellFrame updates from Telegram. `/update_now` pulls + restarts (if Python changed) or reports UI-only changes.

### 新功能
- **STT 後端選擇** — 設定 → Telegram Bridge 多了 🎙 STT 面板：可選 `Auto`（本地優先 → 遠端）、`Local`（whisper.cpp）、`Remote`（faster-whisper 伺服器）或 `Off`。本地後端用 `whisper-cli` + `ggml-base.bin` 模型。狀態 pill 顯示哪些後端可用；「安裝本地 STT」按鈕會跑 `brew install whisper-cpp` 並下載模型到 `~/.local/share/shellframe/whisper-models/`。
- **TG `/restart`** — 從 Telegram 直接觸發完整重啟，session 會透過 tmux 自動 reattach。
- **TG `/update`** + **`/update_now`** — 從 Telegram 檢查更新。`/update_now` 會 pull + 重啟（若有 Python 改動）或回報純 UI 改動。

## v0.7.0 (2026-04-11)

### New Features
- **TG voice messages** — Send a voice note via Telegram and the bridge downloads it, transcribes via local STT server (`192.168.51.197:8765`, faster-whisper), and forwards the text to the active AI session. Bridge replies with a `🎙 轉錄中…` placeholder then `✓ <preview>` once transcribed. Audio files (`audio` type) supported too.

### 新功能
- **TG 語音訊息** — 在 Telegram 按著麥克風錄語音，bridge 會自動下載、送到本地 STT 服務（`192.168.51.197:8765`，faster-whisper）轉文字後轉發給 AI session。Bridge 會先回 `🎙 轉錄中…`，完成後回 `✓ <preview>`。也支援一般音訊檔。

## v0.6.0 (2026-04-10)

### New Features
- **Two-tier reload** — Updates now distinguish between UI-only changes and core (Python/filters) changes. Web-only updates trigger a hot UI reload (current behavior); Python changes prompt a restart confirmation modal that explicitly tells you sessions will be preserved (tmux reattaches automatically).
- **Manual reload chooser** — Clicking ↻ in About now opens a small chooser: "Reload UI" (frontend only) or "Restart ShellFrame" (full app restart, sessions preserved). Lets you decide instead of guessing.
- **`restart_app` API** — New Python API spawns the launcher then exits cleanly. Detaches from tmux without killing sessions.

### 新功能
- **兩段式重新載入** — 更新時會分辨改動範圍：純 web 改動走 UI hot-reload；Python / 核心改動會跳重啟確認框，明確告訴你 session 會被保留（tmux 自動 reattach）。
- **手動重載選單** — About 裡點 ↻ 現在會跳小選單：「重載 UI」（只重整前端）或「重啟 ShellFrame」（完整重啟，session 保留），自己決定。
- **`restart_app` API** — 新 Python API 會 spawn launcher 再乾淨退出，detach tmux 但不殺 session。

## v0.5.5 (2026-04-10)

### Fixes
- **Renaming a session no longer interrupts the running CLI** — Double-clicking a tab to rename and pressing `Esc` to cancel (or `Enter` to save) used to leak the keystroke into the underlying xterm helper textarea after the modal closed. Claude Code interprets a stray `Esc` as "interrupt current operation", so the user's conversation got cancelled mid-response. Fixed by `preventDefault + stopPropagation` inside the rename modal's keydown handler, and by deferring `term.focus()` to the next tick so the original keystroke fully unwinds first.
- **Global Esc modal handler same leak** — `Esc` to close the Settings/About/New-tab modals also bubbled into xterm. Now only swallows the key if a modal was actually open; otherwise lets it through so plain Esc still reaches Claude as the interrupt signal.

### Internal
- **Debug log at `/tmp/shellframe_debug.log`** — Captures every PTY write (sid, length, escaped preview), every tmux scroll/copy-mode call, every session lifecycle event (`new_session`, `close_session`, `rename_session`, `restore_tmux_sessions`), and every resize. Used to retroactively diagnose "what just interrupted my session" — the rename leak above was caught by spotting a stray 1-byte `\e` write in the log right after a tab interaction.

### 修正
- **重命名 session 不再中斷對話** — 雙擊 tab 改名，按 `Esc` 取消或 `Enter` 確認時，鍵盤事件原本會在 modal 關閉後 bubble 到 xterm 的 helper textarea，xterm 把它送進 PTY。Claude Code 把單獨的 `\e` 解讀成「中斷當前操作」，所以對話會在回應一半被掛掉。用 `preventDefault + stopPropagation` 在 rename modal 的 keydown handler 內擋掉，並用 `setTimeout(0)` 把 `term.focus()` 延後到下一個 tick，等原本的 keystroke 走完才換 focus。
- **全域 Esc 關 modal 也有同樣洩漏** — 關 Settings/About/New-tab modal 用的 Esc 也會 bubble 到 xterm。現在只在「真的有 modal 開著」時 swallow 該鍵，沒 modal 開著就放行讓 Esc 正常傳到 Claude 當中斷信號。

### 內部
- **`/tmp/shellframe_debug.log` 偵錯日誌** — 紀錄每次 PTY write（sid、長度、escape preview）、每次 tmux scroll/copy-mode 呼叫、每次 session 生命週期事件（`new_session` / `close_session` / `rename_session` / `restore_tmux_sessions`）、每次 resize。可以事後追查「剛剛是什麼把對話打斷的」— 上面那個 rename 洩漏 bug 就是從 log 裡看到 tab 互動後跑出一個孤立的 1-byte `\e` write 才定位出來的。

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
