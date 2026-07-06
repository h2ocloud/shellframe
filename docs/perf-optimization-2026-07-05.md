# ShellFrame CPU 效能優化規格（2026-07-05）

## 背景與實測證據（總控已量測，勿重複爭論是否存在問題）

量測時間 2026-07-05 08:30，ShellFrame v0.27.0（HEAD 939cc43），8 個 sessions 掛載中：

1. **主進程 CPU 71–73% 持續 10 小時以上**（PID 當時 18366，uptime 10:16h）。
2. `ps -M` 顯示單一 thread 累積 **95 分鐘 user CPU**，遠超其他 thread。
3. `sample` 5 秒取樣，熱點 leaf 集中在 **Python regex engine**：
   `sre_ucs4_match`(375) / `sre_ucs4_charset`(343) / `sre_ucs4_count`(234) / `sre_category`(181)，
   全部落在 **`_flush_loop` thread**（bridge 的 0.5s tick 掃描迴圈）。
   sre_ucs4 = 含寬字元（CJK/emoji）的字串比對，與終端畫面內容相符。
4. 全進程 **51 條 threads**（每 session 有 `_reader_unix` / `_poll_loop` / `_watchdog_loop`，加上 bridge 的 `_flush_loop` 等）。
5. 連鎖效應：WindowServer 高達 34% CPU，疑似 webview 高頻 repaint。
6. 本機只有 8GB RAM 且 swap 已用 3.8GB/5GB — CPU 空轉會加劇整機卡頓，此優化是為了讓 ShellFrame 在 8 tabs 常駐下接近零 idle 成本。

## 目標（驗收標準）

- **Idle 基線**：所有 tab 靜止時，ShellFrame 主進程 CPU **< 5%**。
- **忙碌場景**：8 tabs、其中 2–3 個 claude 持續輸出時，主進程 CPU **< 25%**（現況 ~71%）。
- **零功能回歸**：TG bridge 收發、[[SF:*]] 燈號偵測（打字時一律 inline 引用避免自我誤觸）、選單偵測、board、auto-compact、stall 偵測、歷史去重全部照常。

## 工作項目

### 0. 先量測、不要猜（必做，其餘項目以此為準）

macOS 上 py-spy attach 需要 sudo（不可用），改用：
- `sample <pid> 5` 看 native leaf 分佈（總控已做過一輪，可重複驗證）。
- **在程式內加輕量 instrumentation**：config 加 `perf_debug` 開關（預設 off），
  對 `_flush_loop` 內每個階段（`_extract_new_text`、`_detect_and_fire_signal`、
  `_detect_menu_prompt`、`_detect_and_apply_board`、`_maybe_auto_compact`、
  `screen.display` 存取）累積 `time.monotonic()` 耗時，每 60s 寫一行摘要進 bridge log。
  用實際數據排出 top 3 熱點再動手改。改完保留這個開關，日後回歸量測用。

### 1. Dirty-flag：沒有新輸出的 slot 完全跳過掃描（預期最大收益）

- `feed_output`（PTY ingest）時對 slot 設 `dirty` flag；`_flush_loop` tick 時
  只處理 dirty 的 slot，處理完清 flag。idle slot 連 `screen.display` 都不要碰。
- 注意 pyte 的 `screen.display` 是 **property，每次存取都全螢幕重新 render**
  （rows × cols 的字串組裝）。8 slots × 每 0.5s 存取就是固定 CPU floor。
  確認所有存取點（bridge_telegram、bridge_line、main.py、agent_status 等）都
  收斂到 dirty-gated 路徑，或加一層以 screen generation counter 失效的 cache。

### 2. Regex 熱點治理

- 盤點 `_flush_loop` 呼叫鏈上所有 regex：迴圈內的字面 pattern（如
  `_extract_new_text` 內 `re.match(r'^(\w+):\s', sent)` 對每個 sent_texts 每次重跑）
  全部預編譯成模組層 constant；`sent_texts` 的 username prefix 可在寫入時就算好存起來。
- `_detect_menu_prompt` / `_detect_and_fire_signal` / `_SIGNAL_RE` 確認只掃「新增行」
  （drained lines），不重掃整個畫面或 history。
- `_is_bridge_noise_line`、spinner 判斷等每行都跑的函式，檢查是否可用
  startswith/集合查表取代 regex；純裝飾線判斷 `all(c in '─━═…')` 可先用
  `len` + 首字元快篩。

### 3. Tick 頻率自適應

- 現況 `_flush_loop` 固定 0.5s。改成：有任一 slot dirty → 0.5s；全部 idle 連續
  N tick → 放寬到 2s；`feed_output` 一進資料立即恢復 0.5s（可用 Event 喚醒，
  不必等 sleep 走完）。stall/auto-compact 的 slow_tick(2s) 節奏維持。

### 4. Thread 盤點（第二優先，收益確認後再做）

- 51 threads：列出每類 thread 的職責與 tick 頻率，評估 per-slot 的
  `_watchdog_loop`/`_poll_loop` 能否合併成單一 scheduler thread 輪詢所有 slot。
  若量測顯示這些 thread 幾乎都在 sleep/select（成本低），此項可標註「不做」並寫明數據。

### 5. WindowServer 連鎖（web/index.html）

- 檢查 webview 端輪詢/重繪頻率（`scanTerminalActivity` 等）：視窗非前景或 tab
  非 active 時是否仍高頻 repaint；idle 時降頻。
- 注意 gotcha：**web/index.html 改動必須 full `sfctl restart` 才生效**（webview 不會 hot-reload）。

## 驗證（必做，缺一不可）

1. 單元測試全過：`tests_tg_inject.py`、`tests_tg_reply.py`、`tests_agent_status.py`、`tests_history_dedup.py`、`tests_usage_probe.py`、`test_init_prompt.py`。
2. 改完 bridge Python 可 `sfctl reload` 快速迭代；**最終驗收要乾淨 `sfctl restart`**（sessions 會保留，你自己這個 tab 也會活著）。
3. Before/after 對比表：idle 場景與忙碌場景各量 `top -l 2 -pid <pid>` CPU%，附 `perf_debug` 60s 摘要數據。
4. 功能實測：restart 後派一個測試訊息確認 TG 回覆、燈號偵測（實際輸出一次標記行驗證 bridge log 出現 `[signal]`）。
5. CHANGELOG 記錄 + version bump（minor），**commit + push**（教訓：功能只活在未提交工作樹會被 auto-stash 蓋掉，做完一個子項就 commit 一次）。

## 邊界

- 只動效能，不改功能行為、不重構無關模組、不加超出需求的抽象。
- 記憶體（RSS ~106MB）不在本次範圍。
- 修改期間 ShellFrame 持續在跑（你就在它裡面）：頻繁 commit、避免留長時間未提交的工作樹。

## 回歸守則（2026-07-06 事故後新增，任何進 `_flush_loop` 熱路徑的改動必讀）

**事故**：v0.28.0 優化後 CPU 仍回升到 96%（重啟後 55 分鐘）。sample 顯示
`_flush_loop` thread 88% 時間在 sre regex。真凶不是 v0.29.1 的 live screen
（`_live_tail` 只在注入路徑、有 sleep 有界），而是 `expect_marker` 重試路徑
——一條**從未被 perf_debug 計時**的舊路徑：

1. `if idle < 3.0 and total < 120.0: continue` 這個閘門在 `total ≥ 120s` 後
   永久失效（`first_output_time` 只在抽取成功時歸零）。
2. 之後每 0.5s tick 對 ≤120KB `pending_raw` 跑兩次 `strip_ansi`
   （normal + force 各一次，實測 45ms/次 = 90ms/tick = **18% CPU / stuck slot**）。
3. 失敗路徑 `last_output_time = now` 讓 slot 永遠 pending → 鎖死 0.5s 快 tick。
4. 觸發面：v0.29.1 起 delivery UNCONFIRMED 不重試（防重複送出，正確），但
   `expect_marker` 留著永遠等不到回覆 → stuck slot 常態化（當日 s57/s58/s66
   三個 ≈ 54% + 主線程 pyte feed = 96%）。

**修法（v0.29.5）**：`_try_marker_extract` —— 單次 `_pick_marker_reply`（原本
跑兩次）、失敗後節流 `_MARKER_RESCAN_INTERVAL=3s`、dirty gate（`_feed_gen`
沒前進 = buffer 沒新 bytes，重掃不可能有新結果，直接跳過）、成功即重置。
worst case 從 90ms/0.5s 降到 45ms/3s（1.5%/slot），idle slot 為 0。

**守則**：
1. **任何進 `_flush_loop` 熱路徑的新掃描，必須 dirty-gated（`_feed_gen` /
   `scan_dirty`）+ 節流（明確的最小重掃間隔）+ 預編譯 regex。**
   「閘門會擋住」不算數——本次事故就是閘門在邊界條件（total≥120s）下失效。
2. **必須掛 perf_debug 計時**（`_perf_t()` / `_perf_end("phase", t0)`）。
   本次事故 perf 摘要一片乾淨、CPU 卻 96%，就是因為熱點在未計時路徑：
   計時的盲區 = 下次事故的藏身處。marker 路徑現已計入 `extract_marker` phase。
3. 掃描成本要以「最壞 buffer」估：`pending_raw` 上限 120KB、`strip_ansi`
   實測 ~45ms（v0.29.5 時點）。任何 O(buffer) 操作乘上 tick 頻率算 CPU%，
   超過 1-2%/slot 就要重新設計。
4. 重試迴圈的退出條件要檢查「失敗時狀態有沒有推進」：本次 `last_output_time
   = now` + `first_output_time` 不歸零 = 永動機。失敗路徑必須讓下次嘗試
   變便宜（節流）或變不必要（dirty gate），二者至少其一。
