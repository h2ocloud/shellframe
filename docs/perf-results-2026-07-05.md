# ShellFrame CPU 效能優化結果（2026-07-05）

對應規格：`docs/perf-optimization-2026-07-05.md`。v0.27.0 → v0.28.0。

## 量測方法

- `perf_debug` 開關（`settings.perf_debug=true`）：`_flush_loop` 每階段以
  `time.monotonic()` 累積耗時，每 60s 寫一行 `[perf]` 進 `/tmp/shellframe_bridge.log`。
- `sample <pid> N`：native leaf 分佈，確認 thread 是否在 blocking wait。
- `top -l 2 -pid <pid>`：進程 CPU%（取第二次取樣）。
- 量測負載：8–9 個 sessions 掛載（含活躍 claude/codex tab）。

## Before / After（flush loop 每 60s 累積）

| 指標 | Before (item 0 baseline) | After (item 1+2+3) | 變化 |
|---|---|---|---|
| `screen_display` 總耗時 | 759 ms / 196 次 | 45–87 ms / 14–24 次 | **~90% ↓** |
| `auto_compact` 總耗時 | 814 ms / 28 次 | 30–38 ms / 8 次 | **~95% ↓** |
| `extract_new_text` | 70 ms / 7 次 | 47–62 ms / 7–9 次 | 持平（負載相關） |
| `detect_signal`/`detect_board`/`stall` | ≈ 0 | ≈ 0 | — |
| 進程 idle CPU | 32%（量測時）/ 71%（10h 高載峰值） | **0–4%** | 達標 <5% |

熱點結論：flush loop 的實質成本 100% 集中在 pyte `screen.display` 的全螢幕
render（3.9 ms/次），而觸發來源是每 2s 對每個 slot 跑的 auto-compact 掃描。
regex（signal/board/menu）在實測負載下 ≈ 0，並非熱點——但仍依 item 2 預編譯，
降低重載輸出路徑上的 per-call 成本。

## 各項落地

- **item 0**：`perf_debug` instrumentation（永久保留，回歸量測用）。
- **item 1**：`scan_dirty` dirty-flag（idle slot 完全不碰 `screen.display`）＋
  `_slot_display` generation-counter 快取＋`_read_settings` 1s TTL 快取。
- **item 2**：hot-path 字面 regex 全部 hoist 成模組層預編譯常數。
- **item 3**：自適應 flush cadence（idle 放寬 0.5s→2s，`_flush_wake` Event 立即
  喚醒）＋auto-compact 掃描 2s→8s。
- **item 5**：webview `cursorBlink: false`（消除前景 idle 每 ~530ms 的游標重繪
  → WindowServer composite）＋`renderLoops`/`loadSchedules` 加 `document.hidden`
  守衛（背景視窗零週期性 JS/DOM）。

## item 4：Thread 盤點 —— 結論「不做合併」（數據支持）

盤點（8–9 sessions 掛載時共 26 threads）：

| Thread | 數量 | 職責 | 週期 | idle 成本 |
|---|---|---|---|---|
| `_reader_unix` (main.py) | 每 session 1 | select PTY fd、讀 output | adaptive 0.05s(活躍)/0.3s(idle) | 阻塞於 `select`，零 |
| `pusher` (main.py) | app 1 | 推 output 到 webview | event-driven，0.5s idle floor，背景 tab 節流 4Hz | 阻塞於 event.wait，零 |
| `bridge_feeder` (main.py) | app 1 | 餵 `feed_output`→pyte `stream.feed` | 阻塞於 `queue.get()` | idle 零；重載時 ANSI 解析在此序列化（終端模擬固有成本） |
| `_poll_loop` (bridge) | bridge 1 | TG getUpdates long-poll | HTTP long-poll | 阻塞於 `poll`，零 |
| `_flush_loop` (bridge) | bridge 1 | 擷取/轉發（本次優化主體） | 自適應 0.5–2s | 已優化 |
| `_watchdog_loop` (bridge) | bridge 1 | poll 存活監測 | 每 30s（sleep 1s 累加） | 阻塞於 `sleep`，零 |

`sample` 佐證（idle）：leaf 分佈 `__select`(20667) / `__semwait_signal` /
`__psynch_cvwait` / `poll` / `mach_msg2_trap` / `read` 幾乎全在 kernel blocking
wait；實際執行 `_PyEval_EvalFrameDefault` 僅 23 sample。

**結論**：所有週期性 thread 皆 event-driven 或阻塞於 select/poll/queue，並已
adaptive backoff，idle CPU 貢獻趨近零。將 per-session `_reader_unix` 合併成單一
select-over-all-fds scheduler 只會增加複雜度與 race 風險，換不到可測收益——
**不做**。重載時真正的單執行緒成本在 `bridge_feeder` 的 pyte `stream.feed`
（ANSI 解析），屬終端模擬固有，非本次可無損消除的範圍。
