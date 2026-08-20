# ShellFrame 自動偵測 agent 狀態 — 研究 + 方案 + POC 報告

> 分支：`feat/auto-agent-status-detect`　|　POC：`docs/poc/agent_state_detector.py`
> 目標：右側欄 agent 狀態從「agent 自報 `[[SF:GREEN]]`」改成「**從實際活動科學偵測**」。
> 兩層、同一偵測：**燈號**＝是否在動 + 四態；**右側細節**＝正在忙什麼。涵蓋 Claude 與 Codex。

---

## 1. 現況：busy-dot 資料流

```
PTY (main.py Session)  ──evaluate_js 推 bytes──▶  webview xterm.js (web/index.html)
                                                          │
                                          每 400ms setInterval 偵測（瀏覽器端）
```

偵測**全在瀏覽器端** `web/index.html` L4435–4563，三條來源疊加：

| 來源 | 程式位置 | 判什麼 |
|---|---|---|
| Path A push-rate | L4514–4522 | 近 3s 內 ≥2 次輸出、間隔≥600ms → busy（純活動率） |
| Path B 畫面 wording | `scanTerminalActivity` L4472，`ACTIVITY_MARKERS` L4442 | 視窗底部 12 行有 `esc to interrupt`/`Working (Xs`/`Running…` → active；`ATTENTION_MARKERS` 選單/approval → attention |
| 自報 marker | `SIGNAL_RE` L4457 | agent 自己印 `[[SF:WORKING/GREEN/RED/YELLOW]]` → 覆蓋成 4 色（`sig-working/done/decision/stuck`，CSS L53–73） |

**關鍵結論**：
- 「**在不在動（藍燈）其實已是自動偵測**」（Path A+B），不靠自報也會亮藍。
- **真正不穩的是「語意靜止態」**——綠(done)/紅(decision)/黃(stuck) 完全靠 agent 自印 `[[SF:...]]`。reuse tab 多輪後 agent 忘記印 → 燈卡在藍、或停在上一輪的綠。這正是 使用者講的痛點。
- 瀏覽器端**沒有檔案系統權限**，讀不到 transcript。→ **要用 transcript 偵測，邏輯必須搬到 main.py server 端**，算好狀態再推回 webview。這是本方案的核心架構決策。

---

## 2. 偵測方案對比

### 2.1 三種來源

| 來源 | 準確度 | 延遲 | 成本 | 取得什麼 |
|---|---|---|---|---|
| **A. Transcript / rollout JSONL**（server tail） | 高（語意真相源：工具名、檔案、turn 起訖、錯誤） | 中（落盤後 0.1–1s；JSONL 有時落後實際運算） | 低（append-only，seek 檔尾增量讀） | working/done/stuck/decision **＋細節層**（哪個工具、哪個檔、本輪任務） |
| **B. pyte 畫面 wording**（已有） | 中（spinner=在動很可靠；但讀不到語意） | 低（即時，畫面一變就到） | 低（已在跑） | 「在不在動」的即時補強；`esc to interrupt`、選單 |
| **C. 進程活動 CPU%**（claudectl 路線） | 中（補 transcript 落後的最後一哩） | 低 | 低 | 「真的在算」vs「卡住」 |

### 2.2 建議：**A 為主（語意+細節）、B 為即時補強、C 選配**

開源驗證（見 §5）：純 transcript 路線（pixel-agents）自承「常把 done 誤判成 waiting」；claudectl 用「CPU/PTY 活動 + transcript stop_reason」融合才穩。**SF 已有 pyte/PTY 層，是相對其他 OSS 的最大優勢**，務必納入判定。

- **語意態（done/decision/stuck/細節）→ transcript 為準**（解決自報不穩）。
- **即時「在動」→ pyte spinner 優先**（transcript 落盤前先亮藍，零延遲）。
- 兩者衝突時：spinner 存在 → 一律 working（最可靠的「正在動」訊號）。

### 2.3 兩種 worker 的偵測來源（狀態機統一、底層分流）

| Worker | 主來源 | 路徑 | 對應方式 |
|---|---|---|---|
| **Claude** | transcript JSONL | `~/.claude/projects/<cwd-slug>/<uuid>.jsonl` | 見 §3 |
| **Codex** | rollout JSONL | `~/.codex/sessions/YYYY/MM/DD/rollout-<ts>-<uuid>.jsonl` | lsof 直接命中（見 §3） |
| 兩者 | pyte 畫面 | main.py 既有 PTY/pyte | 直接讀該 tab 畫面底部 |

**Claude transcript 事件**（實測）：`assistant.stop_reason=end_turn`→turn 結束；`assistant` 帶 `tool_use`→工具呼叫（`name`/`input`）；`user` 帶 `tool_result`→工具完成；`AskUserQuestion` tool→等決策。注意 SF 用 `--dangerously-skip-permissions`，**transcript 幾乎不會有 permission 事件**，decision 多來自 `AskUserQuestion` 或畫面選單。

**Codex rollout 事件**（實測 + openai/codex source，見 §5 引用）：外層 `type` = `session_meta`/`response_item`/`event_msg`/`turn_context`；`event_msg.task_started`→working、`response_item.function_call`→工具呼叫、`function_call_output`→結果、`event_msg.task_complete`→done、`*_approval_request`→decision、`event_msg.error`→stuck。**逐行 flush，tail 友善**。

---

## 3. tab → transcript 對應（可行性關鍵，已實測）

cwd 多半 = `$HOME`，slug `-Users-neux` 被所有 home-cwd 的 claude tab 共用 → **光靠目錄無法分辨**。實測三種對應法：

| 方法 | Claude | Codex | 說明 |
|---|---|---|---|
| **`--session-id <uuid>` spawn**（最佳，確定性） | ✅ `claude --session-id <uuid>` flag 存在 | ⚠️ codex 未提供同等 flag | SF spawn 時自帶 uuid → transcript 路徑 `<slug>/<uuid>.jsonl` 唯一確定 |
| **lsof 進程開檔** | ❌ claude **不持續持有 fd**（每次 append 開關，實測 lsof=0） | ✅ codex **持續持有 rollout fd**（實測直接命中） | 從 tmux pane_pid → 子進程 → lsof 抓 `.jsonl` |
| **newest-mtime + 首 prompt 比對**（fallback） | ✅ | ✅ | slug 目錄內取最新 mtime；多 tab 同 cwd 衝突時，用首個 user prompt 比對 SF 送的 init prompt |

**建議**：
- **Claude → `--session-id`**（需小改 spawn cmd，main.py L76/90/161 等）；過渡期或既有 tab 用 newest-mtime fallback。
- **Codex → lsof**（零改動，已驗證可靠）；spawn 時記 pane_pid 即可。

> POC 的 `--scan` 用 newest-mtime fallback（不需改 spawn）就已能正確列出全部 active worker，證明 fallback 可用；`--session-id` 是把它升級成 100% 確定。

---

## 4. 狀態機：偵測訊號 → 狀態 → 燈號

四態統一適用 claude/codex（POC 已實作 `compute_state()`）。

### 4.1 判定優先序（高 → 低）

| 序 | 條件（normalize 後事件 + 畫面） | 狀態 | 燈號 class |
|---|---|---|---|
| 1 | 最後事件 = `decision_req`（AskUserQuestion/approval）**或** 畫面選單 wording | **decision** | `sig-decision` 🟠 |
| 2 | 最後事件 = `error` | **stuck** | `sig-stuck` 🔴 |
| 3 | 最後事件 = `turn_end`（claude `end_turn` / codex `task_complete`），安定 ≥3s | **done** | `sig-done` 🟢 |
| 4 | 畫面有 spinner（`esc to interrupt`/`Working(Xs`） | **working** | `sig-working` 🔵 |
| 5 | tool 開出去 > 90s 無結果且無 spinner | **stuck** | `sig-stuck` 🔴 |
| 6 | 最後事件在 8s 內 | **working** | `sig-working` 🔵 |
| 7 | turn 未結束但 > 45s 完全無事件 | **stuck** | `sig-stuck` 🔴 |
| 8 | 其餘（剛收工具結果、還沒 end_turn） | **working** | `sig-working` 🔵 |

### 4.2 去抖 / timeout（POC 常數，可調）

- `WORKING_FRESH_S=8`：最後事件 8s 內視為仍在動。
- `DONE_QUIET_S=3`：turn_end 後安定 3s 才報 done（避免下一輪馬上又動造成綠→藍閃爍）。
- `STUCK_TOOL_S=90`：長命令容忍 90s（配合 spinner：有 spinner 永遠 working，不誤判 stuck）。
- `STUCK_IDLE_S=45`：turn 未結束又全無活動的保守 stuck 門檻。
- **dot 翻轉去抖**：working↔done 需穩定 2 個取樣（~0.8s）才翻；decision/stuck 立即浮現（要 使用者 注意）。

### 4.3 POC 實測（真實 log，§見終端輸出）

```
[claude] working  · Writing agent_state_detector.py      (fresh event)   ← 本 session 自己
[claude] working  · search_emails "Tom 報到..."          (fresh event)   ← 另一 worker 在搜信
[claude] stuck    · Running echo "..."  idle 166s         (turn not ended)
[claude] done     ×3                                      (turn_end)
[codex ] done                                             (turn_end)      ← s28
```
四態、claude/codex 雙格式、細節層（工具+目標）全數正確。

---

## 5. 視覺化大改版設計（兩層 UI）

同一偵測、兩種粒度。參考開源（claudectl 五信號融合、claude-hud 緊湊活動行、claude-code-otel 頂部總覽、disler 色帶識別、pixel-agents 反例）。

### 5.1 燈號層（busy-dot，沿用現有 4 class）

- 4 態用**形狀+顏色雙編碼**（不只靠色）：working 🔵實心緩脈動 / decision 🟠空心或 `?`（唯一要你動作，最顯眼）/ done 🟢✓ / stuck 🔴`!`。
- calm 取向：只有 decision/stuck 允許動畫吸睛，working 極緩脈動，done/idle 靜止。

### 5.2 右側細節層（新增）

每 agent 一行活動行：

```
┃● 研究-CLD   · Editing main.py — 重構狀態機
┃● 信件-CLD   · Searching "Tom 報到" — 找報到通知
┃◍ sf-codex   · 等核可: rm -rf build/          ← decision，琥珀
┃✓ QA-CLD     · 完成（3 個案例 PASS）
```

- 格式：`[燈] tab-label · 動詞 + 目標 — 本輪任務`。動詞由 tool 映射（Read→Reading…），目標取檔名/指令首段，任務取本輪 user prompt 或 TodoWrite 標題（截斷 ~40 字）。
- 用 **tab label 不用 sid**（符合既有偏好）；左側 2px 色帶對應分頁色，眼睛免讀字即可對位。
- 頂部一行 who's-working 總覽：`▶ 2 working · 1 waiting · 3 done`，waiting>0 標琥珀。

### 5.3 資料推送

main.py server 端每 ~500ms 算好各 tab 的 `{state, dot, activity, task}`，用既有 evaluate_js 推一包 JSON 給 webview，webview 只負責渲染（不再自己掃 transcript，瀏覽器也讀不到）。pyte spinner 偵測可留在瀏覽器端做即時 working 補強。

---

## 6. system prompt 調整（INIT_PROMPT 自報規則）

改偵測式後，自報與自動偵測會衝突（例：agent 印了 GREEN 但其實又動起來）。現有 POC 邏輯已處理「resting signal 在 busy 時清掉」，但根本解是**降級自報**。

要改的位置：
- `INIT_PROMPT.md` L93–102（`Tab signal lights (worker self-signalling)` 整段）
- `main.py` L1316–1323（中文版自報指引）

**建議改法（降為輔助，不全移除）**：
- **移除** `[[SF:WORKING]]` / `[[SF:GREEN]]` 的「義務自報」——working/done 改由偵測自動判定，agent 不需印。
- **保留**但改為「可選」：`[[SF:RED]]`（需決策）與 `[[SF:YELLOW:原因]]`（卡住等外部條件）——這兩個是 agent **才知道的語意**（例如「在等 使用者 回 LINE」偵測看不出來），保留讓 agent 主動標記 + 觸發 bridge 推播。但燈號顏色以偵測為主，自報只在偵測沒抓到時補。
- 新文字草案（取代 L93–102）：
  > **Tab status is auto-detected** from your activity (tool calls, turn boundaries) — you do NOT need to print `[[SF:WORKING]]` or `[[SF:GREEN]]`; the cockpit infers working/done automatically.
  > Only when you are **blocked on an external condition** the cockpit cannot see (waiting on a person/another team/an external event), print one line `[[SF:YELLOW:one-line reason]]`; and when you need **the user's decision**, print `[[SF:RED]]` followed by a numbered menu. These are optional hints, not status reporting.

> ⚠️ 此為「大改方向 + 動正式檔」，**報告先給 維護者 review，核可後才改 INIT_PROMPT.md / main.py**。

---

## 7. 實作計劃（分段交付）

| 段 | 內容 | 動到的檔 | 風險 |
|---|---|---|---|
| P1 | server 端偵測器模組（POC 已成）＋ tab→transcript 對應表 | 新檔 `agent_status.py` | 低（獨立模組） |
| P2 | main.py 接線：每 tab 記 pane_pid + transcript 路徑，500ms 算狀態推 webview | main.py（少量） | 中（碰主迴圈，需 try/except 包好，偵測失敗不可影響終端） |
| P3 | claude spawn 加 `--session-id`（確定性對應） | main.py spawn cmd | 中（改啟動參數，需測 resume/續接不壞） |
| P4 | webview 渲染：busy-dot 改吃 server 狀態 + 右側細節層 UI | web/index.html | 中（UI 改版） |
| P5 | INIT_PROMPT / main.py 自報規則降級 | INIT_PROMPT.md، main.py | 中（影響所有 worker 行為，需觀察） |

### 風險 / 回滾

- **偵測失敗不可影響終端**：所有偵測碼包 try/except，異常時 fallback 回現有 push-rate+wording heuristic（保底）。
- **feature flag**：server 偵測用設定開關，可一鍵關回現狀。
- **回滾**：本分支獨立，未動 main；棄用直接切回 main 分支即可。
- **效能**：transcript 只 seek 檔尾增量讀、500ms 一次、active tab 才掃，成本可忽略。
- **版號**：不自行 bump，等 維護者主導。

---

## 8. 結論與待決策

1. 現況「在不在動」已自動，**不穩的是語意態靠自報** → 用 transcript 根治，POC 已驗證可行（claude+codex 雙格式、四態、細節層全對）。
2. 架構必走 **server 端偵測 → 推 webview**（瀏覽器讀不到 transcript）。
3. tab 對應：claude 建議 `--session-id`、codex 用 lsof，皆已實測。

**需維護者拍板**：
- (a) 是否採「transcript 為主 + pyte 補強」這條主線？
- (b) claude 是否接受加 `--session-id` spawn 參數（換取確定性對應）？
- (c) 自報規則降級方案（§6）是否照走（移除 WORKING/GREEN 義務、保留 RED/YELLOW 可選）？

核可後我按 §7 P1→P5 分段實作，每段獨立 commit 在本分支。
