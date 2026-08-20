# 效能基線 — 2026-08-17（送達機制 + harness 開工前）

使用者三次強調「效能效能效能，不能搞掛」。這份是**改動前的量測快照**，
QA 驗收時拿同樣方式再量一次對比；沒有對比數據的「應該沒變慢」不算驗收通過。

## 量測環境
- 13 個 session（含活躍中的 AI 分頁），`perf_debug = true` 已開啟。
- 量測時間 2026-08-17，機器：8GB Mac。

## 基線數據

### 進程層級（`ps aux` 三次取樣）
| 取樣 | CPU | RSS |
|---|---|---|
| 1 | 12.2% | 120 MB |
| 2 | 10.3% | 81 MB |
| 3 | 18.6% | 55 MB |

CPU 在 10–19% 之間浮動（有分頁在跑回合時偏高）。
**紅線：改動後同等負載下，穩態 CPU 不得高於 25%。**
（歷史事故參考：2026-07-06 曾達 96%，根因是 flush loop 未節流的掃描。）

### flush loop 分項（`[perf]` 60s 視窗，13 slots）
```
extract_new_text  22.9–27.9ms / 13x  (1765–2144µs 每次)
screen_display    19.8–25.9ms / 26x  (763–998µs)
auto_compact       2.1–3.0ms  / 7x   (303–423µs)
stall_detect       1.0–1.1ms  / 29x  (34–39µs)
detect_signal      ~0.0ms     / 13x  (1–3µs)
detect_board       ~0.0ms     / 13x  (1–2µs)
```
全部 phase 合計約 **50–58 ms / 60 秒 ≈ 0.1% CPU**。

**紅線：任何新增的 phase，單獨計時不得超過 50ms/60s（≈0.08%）；
所有 phase 合計不得超過 150ms/60s（≈0.25%）。**

### 回歸測試
`tests_*.py` + `test_*.py` 共 **21 個測試檔全綠**（0 紅）。
**紅線：改動後必須仍是 21 綠 + 新增測試也綠。**

## QA 驗收時的量法（照抄即可）

```bash
cd ~/.local/apps/shellframe
# 1) CPU / RSS 三次取樣
for i in 1 2 3; do ps aux | grep "[S]hellFrame main.py" \
  | awk '{printf "cpu=%s%% rss=%dMB\n",$3,$6/1024}'; sleep 2; done
# 2) perf 分項（改完至少等兩個 60s 視窗）
grep "\[perf\]" /tmp/shellframe_bridge.log | tail -3
# 3) 全套測試
for f in tests_*.py test_*.py; do .venv/bin/python "$f" >/dev/null 2>&1 \
  && echo "PASS $f" || echo "FAIL $f"; done
```

## 額外必查（收工前，非選項）
1. **新增的掃描有沒有掛 perf 計時**——沒掛的路徑＝下次事故的藏身處
   （2026-07-06 事故時 perf 摘要一片乾淨、CPU 卻 96%，因為熱點在未計時路徑）。
2. **有沒有 respawn 進程殘留**（LaunchAgent / cron / KeepAlive）。
3. **重現原故障確認消失**：tab 11（s87）那種「主 agent 等背景 sub、長時間無回覆」
   的情境，改完要能真的收到心跳/回執。
