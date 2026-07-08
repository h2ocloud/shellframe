# Changelog

## v0.29.12 (2026-07-08)

### Fixes
- **分頁撞到 Claude 額度上限時 TG 端完全靜默**：根因有二，一起修復：
  1. **橫幅被 noise filter 過濾**：`_extract_new_text` 的 `_is_bridge_noise_line`
     把所有以 `⎿` 開頭的行視為工具結果雜訊過濾掉，導致
     `⎿  You've hit your session limit · resets 3pm (Asia/Taipei)` 永遠不會轉發到 TG。
     修：新增 `_detect_rate_limit(slot)` 直接讀 `_slot_display`（繞過 extract 路徑），
     用 `_RATE_LIMIT_RE` 掃描 `hit your (session|usage) limit`、`/rate-limit-options`、
     `/usage-credits to finish` 等訊號，並以 `_RATE_LIMIT_RESET_RE` 抓 reset 時間。
  2. **stall watchdog 沉默**：`_warn_stalled` 只在偵測到 macOS 阻擋彈窗時才通知，
     rate-limit 無彈窗 → 完全沉默。修：在 flush loop 的 slow_tick（2s 週期）加
     rate-limit 掃描，逐 slot 呼叫 `_detect_rate_limit`；命中時推 TG 通知，若為
     `/rate-limit-options` 互動選單則附 inline 按鈕（`rlchoice:` prefix）讓使用者
     遠端選「⏳ 等待重置」或「💳 改用 usage credits」。
- **去重**：`slot.rate_limit_notified` 旗標確保同一 episode 只通知一次；訊號消失
  （重置或使用者操作）後旗標清除，下次 episode 會重新通知。
- **雙重通知防護**：`_extract_new_text` 呼叫 `_detect_menu_prompt` 前先檢查 rate-limit
  狀態，有 rate-limit 橫幅時跳過通用 menu prompt 偵測，避免「1. Stop / 2. Switch」
  選單被當普通 numbered menu 重複轉發。
- **設定開關**：`settings.rate_limit_notify`（預設 `true`）可關閉此功能。
- 新增 `tests_rate_limit.py`（6 案例全 PASS）；既有 5 個測試檔全數通過。

## v0.29.11 (2026-07-08)

### Fixes
- **側邊欄模型徽章判定不準確**：根因有四，逐一修復：
  1. **全域 fallback 污染（主因）**：`detect_model_info` claude 分支原本在
     transcript 解析不到時退回 `~/.claude/settings.json` 的 `model`——那個值是
     「最後一次 /model 存的 session 預設」（如 `opus[1m]`），導致大量分頁顯示
     同一個錯誤模型。修：拿掉全域 fallback，無 transcript 且無 `--model` flag
     的分頁一律回 None（不顯示），寧可空白也不顯示錯的。
  2. **全域 model 有 ANSI／`[1m]` 髒字**：`~/.claude/settings.json` 實測值為
     `opus[1m]`（含 tag），舊版 `_pretty_model` 只 strip 結尾 `[1m]` 且不認
     bare alias。修：任意位置 strip `[1m]` 與 ANSI escape；bare alias
     （opus／sonnet／haiku／fable）直接首字大寫顯示（如 "Opus"），不硬湊版號。
  3. **`--model` 啟動旗標被忽略**：cmd 如 `claude --model fable` 是最可靠的
     per-tab 訊號（在 transcript 產生前），但舊版完全沒解析。修：新增
     `_parse_model_flag(cmd)` 解析 `--model <x>`／`--model=<x>`，支援 alias
     與完整 `claude-*` id；transcript 無法取得時退而採用此值。
  4. **sidechain 模型污染主 chain**：`_parse_claude_transcript_model` 沒過濾
     `isSidechain=True`，主 agent 跑 opus 但 spawn sonnet subagent 時徽章會
     錯顯為 Sonnet。修：加 `isSidechain is True` 過濾，只採 main-chain
     assistant 記錄的 `message.model`。
  回歸測試：`tests_agent_status.py` 新增 17 案例（含 4 成因端對端），26/26 PASS；
  `tests_tg_model_menu.py` 7/7 PASS。

## v0.29.10 (2026-07-07)

### Fixes
- **「自動派工」開關關了還是會派工**（Howard 回報）：根因有二——
  1. `auto_delegate_enabled`（設定頁「自動派工（實驗性）」）**後端沒有任何
     consumer**，是顆沒接線的死開關；
  2. 真正每回合推派工的指令不在 master preamble（Howard 早已關掉
     `master_turn_preamble_enabled`），而是藏在 **TG per-turn prompt 的
     「Default coordination: … prefer `sfctl delegate` …」段落**，不受任何
     開關管。
  修：開關正式接線。關閉（預設）時 TG prompt 協調段落換成「在本分頁處理，
  僅在使用者明確要求（派工/開分頁/開 worker）時才 delegate」；master
  per-turn preamble 同樣改中性版（grounding／tab label 規則保留），且自訂
  Delegation Protocol 文字不再繞過開關。開啟時行為與過去相同。自訂
  `tg_prompt` 原文照用不做手術。`sfctl delegate` 手動派工不受影響。
  回歸測試：`tests_auto_delegate_gate.py` 5 案例。

## v0.29.9 (2026-07-07)

### Fixes
- **Windows：TG 橋接訊息卡在輸入框送不出去（codex 最嚴重）**（Howard 2026-07-07 回報）。
  根因鏈：Windows/ConPTY 把注入的 payload 逐字合成 key events，client 端 drain 大
  payload 遠超過固定 0.3s——codex（crossterm 讀 win32 事件，拿不到 bracketed-paste
  框架，靠「連續輸入 burst」偵測貼上）在 burst 窗內收到提交的 CR 會把它當**換行**
  插進 composer 而不是送出 → 整段訊息卡在對話框。四段修法：
  1. `_inject` 送 CR 前改等 **echo 靜止**（`_wait_paste_drain`：最後輸出 chunk
     安靜 ≥0.25s 才送，Windows cap 較高、按 payload 長度放大），取代固定 0.3s。
  2. 送達驗證階梯加**裸 Enter nudge**：residue（字還躺在 composer）時先補一個
     Enter——已送出時 composer 是空的、冪等 no-op；比直接全量重貼安全（Ctrl-U
     對多行 composer 可能只清一行，重貼會疊字）。nudge 無效才走原本的重貼重試。
  3. `_verify_injection` 把 codex 的 `[Pasted Content …]` chip 視為 residue——
     chip 摺疊時 payload 尾段不在畫面上，舊判定會落入「不確定」靜默放棄。
  4. **`_reader_winpty` 漏餵 `_recent` ring buffer**（Windows 專屬實 bug）：
     peek_fn、startup-trust 自動接受、送達驗證 fallback 在 Windows 整組失明，補上。
  另 `_send_text_to_session`（sfctl/delegate/init-prompt 路徑）Windows fallback 分支
  同步補：按長度放大的 CR 前等待＋畫面驗證後的 Enter nudge。
  回歸測試：`tests_tg_inject.py` 新增 chip-residue、drain quiet/noisy 兩案例。

## v0.29.8 (2026-07-07)

### Fixes
- `/fetch` 狀態感知——訊息排隊中（inject_pending）/回合生成中先標示，舊回覆不再被誤讀成「沒收到」。

## v0.29.7 (2026-07-06)

### Fixes
- **App 內「檢查更新」偵測不到剛推的版本**（Howard 2026-07-06：遠端撞版那台抓不到更新）：`check_update` 讀 `raw.githubusercontent.com/.../main/version.json`，這個 Fastly CDN 有 ~5 分鐘快取，剛 push 完會餵**舊的 version.json** → 該時段檢查的機器看不到新版。修法：加 cache-bust query（`?t=<epoch>`）＋ `Cache-Control/Pragma: no-cache`，永遠讀到剛推的值。順手硬化版本比較：非數字段（channel 後綴／WIP tag）不再讓整個檢查拋例外而誤判「無更新」。

## v0.29.6 (2026-07-06)

### Features
- **TG 指令選單／`/list` 帶上模型＋思考深度**（Howard 提，比照桌面側邊欄的 model badge）：Telegram 的 `/1 /2 …` 切換選單描述與 `/list` 輸出，每個 session 現在都顯示「模型 · effort」，如 `Switch to SF · Opus 4.8 · xhigh`、`/4 HR 〔Sonnet 5 · xhigh〕`。逐分頁準確（走 main.py `get_session_model_info`，用該 session 真實 cwd/session_id 偵測，與側邊欄同一來源），Claude／Codex 皆支援；非 AI 分頁或偵測不到就不加、不炸。後端新增 bridge callback `on_model_info`。回歸測試 `tests_tg_model_badge.py`。

## v0.29.5 (2026-07-06)

### Fixes
- **96% CPU 回歸（總控實測：重啟後 55 分鐘 `_flush_loop` thread 88% 時間燒 regex）**。
  真凶不是 v0.29.1 的 live screen 訊號源（`_live_tail` 不在 flush loop、有 sleep 有界），
  而是 `expect_marker` 重試路徑——一條從未被 perf_debug 計時的舊路徑：
  `idle<3 and total<120` 閘門在 `total ≥ 120s` 後**永久失效**（`first_output_time`
  只在抽取成功時歸零），之後每 0.5s tick 對 ≤120KB `pending_raw` 跑**兩次**
  `strip_ansi`（normal+force，實測 45ms/次 = 18% CPU/slot）；失敗路徑
  `last_output_time = now` 又把 slot 鎖死在 0.5s 快 tick。觸發面：v0.29.1 起
  delivery UNCONFIRMED 不重試（防重複送出，保留），但 expect_marker 永遠等不到
  回覆 → stuck slot 常態化（當日 s57/s58/s66 三個 ≈ 54% + 主線程 pyte feed = 96%）。
  修法 `_try_marker_extract`：單次 `_pick_marker_reply`（原本掃兩次）＋失敗後
  節流 3s ＋ dirty gate（`_feed_gen` 沒前進＝buffer 沒新 bytes，直接跳過）＋
  成功即重置；掛上 `extract_marker` perf phase（消滅計時盲區）。worst case
  90ms/0.5s → 45ms/3s（1.5%/slot），idle slot 0。v0.29.1/v0.29.3 功能行為不變
  （串流等待/30s force/防重複送出全保留）。
  回歸測試：`tests_tg_marker_throttle.py` 4 案例；`tests_tg_reply.py` 改走新入口。
  回歸守則新增於 `docs/perf-optimization-2026-07-05.md`。


## v0.29.4 (2026-07-06)

### Fixes
- **TG 誤報「popup detected (UserNotificationCenter)」** (Howard 2026-07-06，人不在電腦前一直收到)：stall 警告的阻擋彈窗偵測把 `UserNotificationCenter` 當成 TCC 對話框，但它其實擁有**所有 macOS 通知橫幅**（Slack/Mail/行事曆…）。任何 app 跳個橫幅、又剛好有 session 在等回覆，就誤報成「有彈窗擋住、去把它關掉」——橫幅根本不擋前景，訊息也無從執行。修法：把 `UserNotificationCenter` 移出阻擋清單，只留真正會 modal 阻擋的 `SecurityAgent`（密碼/鑰匙圈）、`CoreServicesUIAgent`（隔離確認）、`universalAccessAuthWarn`（輔助使用）；並要求命中視窗需有實際尺寸（≥120×60）且非透明，過濾 0x0／幽靈系統視窗。回歸測試 `tests_stall_popup.py`（8 情境，含假 Quartz 視窗清單）。

## v0.29.3 (2026-07-06)

### Features
- **TG 端 `/model` 互動選單**（Howard 提）：手機發 `/model` → bridge 把原生指令送進 active 分頁開 picker → 解析選項後回 TG **inline 按鈕**（含目前模型 ✔ 標記、effort 狀態、取消鈕）。點按鈕即選定——實測 CC 2.1.x picker **數字鍵＝立即選定並存為新 session 預設（免 Enter）**，所以按鈕只送數字；取消鈕送 Esc 關閉 picker、模型不變。分頁忙碌中（回合進行）會擋下並提示，不會把指令戳進生成中的畫面。
- 附帶修正：**通用選單偵測被 picker chrome 行 reset**——「◉ xHigh effort ←/→ to adjust」這類行會把已收集的選項清空，這正是 /model 選單過去偵測不到的根因；現在 chrome 行直接略過（◉/←→/to adjust）。
- 回歸測試：`tests_tg_model_menu.py` 6 案例（picker 測資為實機截取畫面）。

## v0.29.2 (2026-07-06)

### Fixes
- **sfctl/TG 改名不會反映到畫面**（Howard:「你說 tab 有 rename 我怎麼看都沒有」）：
  `rename_session` 只更新後端＋bridge＋config，從未推給 webview——UI 靠
  1.5s 輪詢撿，輪詢失效時 tab 名永遠停在舊值，形成「後端說改了、畫面沒變」
  各說各話。現在 rename 直接 `evaluate_js` 推 `__sfApplyLabel`（改 label
  ＋renderTabs＋renderSidebar），輪詢降為備援。實測 sfctl rename 後 1s 內
  UI 同步。

### Tools
- **新增 `ui_sessions` 診斷 IPC**：回傳 webview「眼中」的 tabs/labels/順序，
  專治「後端說有、畫面沒有」——直接問 UI 而不是用後端狀態推論。gotcha：
  `evaluate_js` 的回傳值在背景 thread 會卡死（WKWebView round-trip 不回），
  改走 fire-and-forget＋JS 回呼 `report_ui_state` 存值；且 `sessions/
  sessionOrder` 是 script 作用域，需經 `window.__sfUiState` closure 取。

## v0.29.1 (2026-07-06)

### Fixes
- **TG 訊息偶發送不進分頁（Howard:「/fetch 之後 prompt 沒反應、/fetch 也沒變化」）**。
  根因：busy guard 與送達驗證的訊號源是 `peek_fn()`（最後 ~1KB 原始 PTY
  bytes）——那是「歷史」不是「現在」。turn 結束的收尾重繪若不足 1KB，舊的
  `esc to interrupt` footer 殘留在 ring 裡：
  - idle 分頁被誤判 busy → 訊息卡 busy guard 最多 **120s**（體感=沒反應）；
  - 送達驗證拿殘影假 delivered → 真失敗**不重試也不通知**（無聲掉訊）。
  修法：新增 `_live_tail()`（live pyte screen 尾端非空行，footer 在就是在、
  無記憶效應），busy guard / 送達驗證 / paste-chip 檢查三處改用；ring 僅
  作無 screen 時的 fallback。
- **長 turn 中注入造成重複訊息**：busy guard 120s 超時後注入，CC 其實已
  排隊（queued），舊驗證誤判未送達 → retry 重貼 → 同一則訊息送兩次（實測
  s57 重現）。live screen 顯示 mid-turn 即視為 delivered，不再誤 retry。
- **驗證不確定（無殘留）從靜默改為通知**：「⚠ 無法確認訊息已送進…請重發」
  ——不重試（避免重複送出風險）但不再無聲。
  回歸測試：`tests_tg_inject.py` 新增 stale-ring / mid-turn-queued 兩案例。

## v0.29.0 (2026-07-05)

### Features
- **OpenCode 分頁支援上滾歷史對話**（Howard requested：第 9 個 tab 用另一套
  harness 跑開源模型，上滑查不到歷史）。根因：opencode 的 TUI 原地重繪
  （Bubble Tea 式），捲出視窗的內容從不進 terminal scrollback / pyte history
  （實測 pyte 只有一屏 25 行）——terminal 來源天生只有「目前這一屏」。
  - 新增 opencode transcript 來源：直讀 `~/.local/share/opencode/opencode.db`
    （SQLite，read-only）的 session→message→part，normalize 成與 claude/codex
    相同的事件形狀，走**同一個** `_render_transcript_overlay` → 樣式與一般
    分頁一致（❯ user 行、工具行收合、markdown 上色）。
  - session↔pane 對應：opencode 把 pane title 設成 `OC | <session.title>`，
    以 title 反查（前綴比對容忍 tmux 截斷），fallback 同 cwd 最近 session。
    多個 opencode 分頁各自對到自己的 session。
  - 來源順序：opencode 分頁 alt-screen 時 transcript 優先、terminal fallback
    （claude/codex 維持 v0.23.2 terminal-first 不動）。

### Fixes
- **tmux status bar（綠條）洩漏進上滾 overlay**：`history-audit` 對 s69 抓到
  `[sf_s69] 0:opencode.exe* [0,0] "OC | …"` 直出 overlay 內容。共用去重管線
  加行首錨定過濾（兩個來源都吃到）；對話內文「提及」sf_ 標籤的行不受影響。
  回歸測試：`tests_history_dedup.py::test_tmux_status_bar_filtered`。

## v0.28.0 (2026-07-05)

### Performance — 8 tabs 常駐近零 idle 成本（規格 `docs/perf-optimization-2026-07-05.md`）

實測熱點（`perf_debug` instrumentation）：flush loop 的 CPU 100% 集中在 pyte
`screen.display` 全螢幕 render（3.9ms/次），由每 2s 對每個 slot 的 auto-compact
掃描觸發。改法與成效：

- **Dirty-flag idle slot**：`feed_output` 設 `scan_dirty`，settled 掃描後清；
  auto-compact 跳過非 dirty 的 slot → 真正 idle 的 tab 完全不碰 `screen.display`。
- **`screen.display` 快取**：`_slot_display` 以 `_feed_gen` generation counter
  快取，同一 settled 狀態多處存取只 render 一次。
- **自適應 flush cadence**：全靜止連續 6 tick → sleep 0.5s 放寬到 2s，
  `_flush_wake` Event 讓新輸出立即喚醒（不增延遲）；auto-compact 掃描 2s→8s。
- **`_read_settings` 1s TTL 快取**：消除 flush loop 每 2s×N slot 的 config 讀取。
- **Hot-path regex 預編譯**：`_is_bridge_noise_line`/`_is_tool_call`/
  `_extract_new_text`/`_detect_menu_prompt` 內每行每塊的字面 pattern 全 hoist
  成模組常數。
- **Webview**：xterm `cursorBlink: false`（消除前景 idle 每 ~530ms 游標重繪
  → WindowServer composite）；`renderLoops`/`loadSchedules` 加 `document.hidden`
  守衛（背景視窗零週期性 JS/DOM）。

成效：`screen_display` 759ms/196x → 45–87ms/14–24x（~90%↓）、`auto_compact`
814ms → 30–38ms（~95%↓）、進程 idle CPU 32% → **0–4%**（達標 <5%）。詳見
`docs/perf-results-2026-07-05.md`。`perf_debug` 開關永久保留供日後回歸量測。

零功能回歸：TG 收發、`[[SF:*]]` 燈號、選單偵測、board、auto-compact、stall
偵測、歷史去重全部照常；單元測試 6/6 全過。

## v0.27.0 (2026-07-05)

### Features
- **STT 新增 `remote_first` 模式**（Howard requested：中英夾雜要更準）：先打遠端 STT provider，連不到才 fallback 回本機 whisper。用來把語音辨識導到 Spark（190）GPU 上的 **Qwen3-ASR-1.7B** server（:9700，含 s2twp 繁體轉換），中英夾雜辨識實測明顯優於 Mac 端 mlx-whisper（範例句 Spark/Whisper/Turbo 專有名詞全對，3.5s）。
  - 對比：原本想部署 whisper-large-v3 到 Spark，但 GPU 已被 vLLM(64G)+Ollama(22G) 佔滿而 OOM；改用既有的 Qwen3-ASR server（本就更適合中文/code-switching），零額外 GPU 成本。
  - 設定走 `config.bridge.stt_backend = "remote_first"` + `stt_providers`（Spark :9700，field `audio`，result key `text`）。

## v0.26.0 (2026-07-05)

### Features
- **語音整理接 Spark AI 模型 + 可切模型**（Howard requested）：語音 Typeless 整理改指向 Spark（190）Ollama 的 `qwythos:9b`，補上完整中英文標點、去贅字、修辨識錯字。實測 warm ~1.8s、cold ~12s（Ollama keep-alive 後保持熱）。
  - 新增 TG `/voice` 指令：`/voice` 看目前設定 + 端點可用模型清單、`/voice <模型>` 切模型、`/voice on|off` 開關整理。
  - CLEAN prompt 強化標點指示（明列 `，。、？！：「」`）。
  - 模型自動挑選跳過 OCR / vision / embed / rerank 模型（避免 deepseek-ocr 被誤選）。
  - 端點/模型/開關持久化在 `config.json` settings（`voice_refine_url` / `voice_refine_model` / `voice_refine`）。

## v0.25.0 (2026-07-05)

### Features
- **語音 Apply 閘門（Typeless 式）**（Howard requested）：TG 傳語音轉錄+refine 後**不再自動送出**，改先顯示整理後文字 + inline `✅ Apply / ✕ Cancel`。按 Apply 才把 prompt 送進 session，按 Cancel 就丟棄。
  - STT 會糊，這道閘門讓你送出前先過目、避免錯字直接餵給 AI。
  - 目標 session 在**按 Apply 當下**才解析，中途切分頁也 OK。
  - Apply 走既有完整 forward pipeline（preamble 包裝、選單偵測、`_send` + 送達驗證），行為與手打訊息一致。
  - 重啟後未處理的待送語音會失效並提示重錄（pending 存記憶體）。

## v0.24.0 (2026-07-04)

### Features
- **`/break` — TG 遠端中斷 AI**（Howard requested）：手機在目前分頁送 `/break`（或 `/stop`、`/esc`、`/interrupt`、`/中斷`、`/打斷`）即對該分頁送出 ESC，打斷 AI 正在跑的 turn（Claude Code / Codex 都吃 ESC）。
  - 送 ESC 前先跑 `prepare_fn` 退出 tmux copy-mode，確保 ESC 落在 CLI 而非 copy-mode。
  - 只送單一 ESC——Claude 連按兩次 ESC 會進歷史導覽而非中斷。
  - 走 `write_lock` 序列化，不與其他注入交錯；`/help` 已補上說明。

## v0.23.3 (2026-07-06)

### Fixes
- **新分頁打 `/model` 被 INIT_PROMPT 灌爆——init 注入時機修正**（Howard 回報「都會被 prompt inject、好長好難用、觸發時機是錯的」）：
  - 根因：web UI 的 init 注入以「第一個含內容的 write_input chunk」觸發，而 xterm 逐鍵送字——你打 `/` 的那一鍵就被當成第一則訊息，INIT_PROMPT＋「User's first message: /」直接進 composer，斜線指令選單整個壞掉。
  - 修法：**斜線指令不是第一則訊息**。行首 `/` 的輸入不消耗 init prompt（留給下一則真實訊息），並以 `_init_hold` 狀態機撐過逐鍵輸入（`/`→`m`→`o`…），該行送出（Enter）才解除——中途任何一鍵都不會再觸發注入；`/model` 選單的方向鍵/Enter 也不受影響。
  - TG 路徑同步修正：`/model` 等 CLI 指令從手機轉發時同樣不消耗 init。
  - 回歸測試：test_init_prompt.py 新增 6 組鍵序案例（33/33 綠）。

## v0.23.2 (2026-07-03)

### Fixes
- **上滾來源優先序反轉——終端來源為主，transcript 降為 fallback**（Howard 實測 v0.23.1 後定調：transcript 渲染整面工具行牆「越差越多」）：
  - 上滾 overlay 回到 pyte/tmux 終端 frame 為主——本來就跟活畫面同一個樣子，重複問題已由 v0.23.0 的統一去重管線處理。實測發現 **Claude Code v2.1.x 已不用 alt-screen**（normal buffer 渲染），tmux scrollback 就是完整正確的歷史，深度 1,000+ 行、原樣 SGR。
  - transcript 渲染只在終端來源拿不出內容時救場（典型：app 剛重啟、pyte 從零開始且 pane 在 alt-screen）。
  - fallback 用的 transcript 渲染同步改善：**連續工具呼叫收合成一行摘要**（`⏺ Bash ×6、Edit ×2`，不再是 20 行工具牆）、`[Image: …]` 縮為 📎 圖片。
  - 本次依「測過才發版」流程：六套測試全綠 → 重啟實測 live dump（來源/內容/重複數）→ 產出 overlay HTML 視覺預覽過目 → 才發版。

## v0.23.1 (2026-07-03)

### Fixes
- **上滾 transcript overlay「樣式跟活畫面不同」**（Howard 截圖回報）：
  - **markdown 現在渲染成 ANSI**：`**粗體**`、行內 `code`、`#` 標題（粗體青色）、`-`/`1.` 列點記號上色、`>` 引用淡化、``` 圍欄 code 區塊、`---` 轉分隔線——不再原樣露出星號反引號，讀起來接近活畫面 TUI。
  - **harness 雜訊不再直出**：transcript 裡 user 角色夾帶的 `<task-notification>…</task-notification>`（背景 agent 回報，含整包 result/usage XML）摺疊成一行 dim 摘要「⏺ <summary>（內容略）」、`<system-reminder>` 整段移除——活畫面 TUI 本來就不顯示這些，overlay 對齊。
  - 回歸測試 +1（`test_transcript_render_fidelity`）。

## v0.23.0 (2026-07-03)

一次完整復盤驅動的大版本：P0→P2 全清（Howard 核可的優化計畫），四套回歸測試全綠。

### Features
- **Session 列顯示模型＋thinking effort 徽章**：左側 session 列每個分頁自動偵測目前跑的模型與 effort（如 `Fable 5 · xhigh`、`GPT-5.5 · medium`），Claude（transcript 最新 assistant 的 model＋全域 effortLevel）與 Codex（rollout 最新 turn_context，退 config.toml）都支援；`/model` 切換後下一輪自動更新。stat/mtime 快取，500ms 輪詢無感。設定 → 「Session 顯示模型標籤」可關（預設開）。
- **AI 分頁上滾歷史改讀 transcript（source of truth）**：Claude/Codex 分頁的上滾 overlay 直接從 session transcript 渲染對話（user ❯ 前綴、assistant 原文、tool 呼叫 dim 一行、決策黃字）——終端 redraw frame、resize wrap 變體、串流殘影**整類問題從根源消失**。稀疏 transcript（新分頁）自動 fallback 回終端管線；非 AI 分頁不走此路。overlay header 的 source 標籤會顯示 `transcript (claude/codex)`。
- **TG 注入 fallback——終結「敲了訊息但沒真的送進來」**：
  - 注入前先恢復 pane：session 停在 tmux copy-mode（捲動狀態）時貼上的字會被整段吞掉、而 TUI 的 spinner 重繪讓輸出 stall 偵測永遠不觸發——這就是靜默掉訊息的主因。現在注入前偵測 `pane_in_mode` 並自動退出。
  - 注入後正向驗證送達（turn 開始 footer 或回覆已抽取）；驗證窗結束仍看到 payload 殘留在畫面 → 自動重試 1 次；再失敗 → **TG 直接通知你**哪個分頁沒送進去，不再無聲吞掉。
- **Windows 單實例保護**：named mutex＋FindWindow 前景喚醒，補齊 macOS PID file 防護的 Windows 缺口（雙實例 = TG 409 衝突）。

### Fixes
- **上滾歷史「對話重複＋樣式不一致」根因修正**：
  - pyte 路徑（alt-screen＝所有 AI 分頁走的那條）過去**完全沒有去重**——十多輪修的全是 tmux 路徑。現在兩條路共用同一條 `_dedupe_history_lines` 管線（redraw collapse＋prefix＋雙 gate），pyte 路徑也逐行補 SGR reset（顏色不再滲行）。
  - pyte SGR 重建補 blink、清色表 typo；dim（SGR 2）為 pyte 資料層限制（Char 無此欄位），已文件化——source=pyte 時亮度偏亮屬已知。
- sfctl `brew pin` 補 timeout。查核確認 main/bridge 全部 40 個 tmux subprocess 呼叫已有 timeout（早期復盤資訊過時）。
- **發版前對抗性審查（subagent）抓到並修正 3 個 CONFIRMED bug**：
  - TG 送達驗證誤判 delivered：extraction loop 抽到前一輪回覆會把 `last_write_ts` 歸零，任何舊 extraction 都讓驗證假通過。改跟「本次注入時間快照」比。
  - shell 分頁誤觸重複注入：shell 會 echo 輸入，慢而安靜的指令（build/ssh）被殘留判定當成沒送進去 → 重試會把 payload 重貼進執行中程序的 stdin。verify/retry 改為只對 AI 分頁（claude/codex）啟用；失敗通知移出 write_lock（tg_api 卡 35s 不再堵住後續訊息）。
  - `bfightmagenta` 不是我們的 typo 是 pyte 上游的（`BG_AIXTERM[105]` 真的拼錯），v0.23.0 rc 誤刪導致 SGR 105 背景消失——加回並註記。
  - 另修 4 個審查疑點：模型偵測快取 key 加 parser 名防污染；Windows mutex 改 `use_last_error`（`windll.GetLastError()` 官方文件明列不可靠）；transcript 上滾對 codex 只信 lsof 命中路徑（全域最新 fallback 可能渲染到別分頁的對話）；前端 `config` 未載入時 badge 不炸。audit 的重複偵測對 transcript 來源停用（工具行 ×N 是真實事件，live 實測抓到假陽性）。
  - 新增 `tests_tg_inject.py`（6 案例）鎖住上述誤判類回歸。

### Tests（本版起有真正的回歸防線）
- `tests_history_dedup.py`：上滾去重管線 10 案例，每個對映 CHANGELOG 上一輪真實踩過的坑（CJK 串流重繪、code 合法重複、短編號標題、resize wrap 變體、「只剩 banner」誤砍、keep-LAST、真實 capture 冒煙）。
- `tests_usage_probe.py`：水位模組（十個版本救火零測試的熱點）7 案例，mock API 蓋 429/stale/退避/no_data/磁碟快取，離線可跑。
- `history_audit` 新增重複偵測：≥3× 的寬行進 verdict（過去只驗 missing/noise，看不見「重複」這類 bug）。

### Refactors
- **Api God-class 分批拆解第一批**：上滾歷史域 → `api_history.py`（HistoryApiMixin）、排程面板域 → `api_schedules.py`（SchedulesApiMixin）、logging 原語 → `sf_log.py`；main.py 7,143 → ~6,000 行，行為不變。
- **76 處 `except: pass` 升級為 `_swallow()`**：吞錯照吞，但寫 debug log 麵包屑（`[swallow] 函式:行號: 例外`）——「感覺不穩又查無日誌」時代結束。pyte 逐格熱路徑排除。
- **config 讀改寫加鎖**：`_CONFIG_LOCK`＋`update_config(mutator)` 原子化，14 條 thread 併發 last-writer-wins 蓋設定的窗口收窄（視窗幾何/active tab/soft session 四熱站點已轉換；其餘站點漸進遷移）。
- CHANGELOG 分檔：280KB → 主檔 16KB＋`CHANGELOG-archive.md`（v0.19.x 以前）。
- board.py（無 UI 的任務看板後端）判定**保留**：其 `[[SF:TASK:]]` marker 處理兼任 TG 回覆的 marker 清除器，拆除會讓 marker 漏進手機，風險大於維護稅。

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
