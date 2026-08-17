# 設計文件：TG 送達／回覆確認機制 ＋ 自有 Harness 主動程度

> 2026-08-17　SA 設計稿。**本文件不含 production code 變更**，RD 依「實作步驟」施工。
> 前置必讀：`docs/perf-optimization-2026-07-05.md`「回歸守則」、`docs/extending-and-harness-plan.md` C/D 節。

---

## 0. 效能標註格式（本文件所有新增掃描一律附此四項）

本 repo 有 96% CPU 事故前科（`_flush_loop` 熱路徑未節流掃描）。因此本文件每一個新增的
掃描／輪詢／定期檢查都必須填滿下表四欄，缺一即視為設計不完整：

| 欄位 | 說明 |
|---|---|
| **dirty-gate** | 是否靠 `slot._feed_gen` / `slot.scan_dirty` 擋掉「沒有新 bytes」的重掃 |
| **節流** | 明確的最小重跑間隔（常數名 + 秒數） |
| **最壞成本** | 每 slot 每分鐘幾次 × 每次幾 ms → 換算 CPU%／slot |
| **perf 計時** | 是否掛 `_perf_t()` / `_perf_end("phase", t0)`（未計時路徑＝下次事故的藏身處） |

**基準實測**（2026-08-17，本機 M-series，`.venv/bin/python`）：

| 操作 | 5KB | 20KB | 120KB（`pending_raw` 上限） |
|---|---|---|---|
| `str.find()` 純子字串 | 0.0013 ms | 0.005 ms | **0.032 ms** |
| ANSI regex `sub` 單發 | 0.038 ms | 0.152 ms | 0.905 ms |
| `strip_ansi()` 完整管線 | 1.37 ms | 5.10 ms | **29.98 ms** |

（repo 註解記載的 45ms 是 v0.29.5 時點的量測，本機今日為 30ms，同一量級。）

---

# 主題 A：TG 訊息「送達 / 回覆」確認機制

## A1. 現況

### A1.1 一則 TG 訊息的完整生命週期

```
手機
 │ getUpdates (long-poll 30s)                    _poll_loop        BT:3193
 ▼
_save_offset()  ←── ⚠ 先存 offset                                  BT:3278
 │ _update_queue.put(update)
 ▼
_dispatch_loop  (FIFO，一次一則)                                    BT:3301
 │
 ▼ _handle_update                                                  BT:4244
 ├─ 媒體下載 / 語音 STT + LLM 潤稿
 ├─ 白名單、paused、active slot 檢查
 ├─ slash cmd → 立刻 setMessageReaction 👀  ←── 只有 slash 有        BT:4417-4426
 ├─ 組 payload：TG preamble + [[TG_REPLY_<uuid8>]] wrapper 指示      BT:4552-4572
 │  ├─ slot.expect_marker = True
 │  ├─ slot.marker_forwarded = False（新 epoch）
 │  └─ ⚠ 清空 slot.pending_raw / first_output_time / last_output_time  BT:4495-4499
 ▼ threading.Thread(_send_tracked)                                 BT:4747
   ├─ slot.inject_pending = True（/fetch 用來回報「排隊中」）
   └─ with slot.write_lock:
      ├─ prepare_fn()（退出 tmux copy-mode）
      ├─ busy guard：等 live tail 沒有 'esc to interrupt'，最長 120s
      │    └─ 等 ≥8s 才發文字「⏳ 已收到、排隊中」                  BT:4629-4639
      ├─ _inject()：Ctrl-U → bracketed paste → CR                   BT:4642
      │    └─ log `[send] <sid> submit CR len=N`                    BT:4653
      └─ _verify_injection（8s 窗，只認強訊號）                     BT:4829
         ├─ delivered           → 靜默（使用者什麼都沒看到）
         ├─ !delivered + 殘留   → 補 Enter → 重貼 → 仍失敗才文字警告
         └─ !delivered + 無殘留 → _deferred_delivery_verdict（再看 45s）BT:4796

── 回程 ──────────────────────────────────────────────────────
PTY chunk → feed_output（pyte feed + pending_raw 累加，上限 120KB）  BT:1474
 │  scan_dirty=True、_feed_gen+=1、_flush_wake.set()
 ▼
_flush_loop（0.5s busy / 2.0s idle 自適應）                          BT:2404
 ├─ slow_tick 2s：_prune_stale_slots / stall 偵測 / rate-limit 偵測
 ├─ compact_tick 8s：auto-compact（dirty-gated）
 └─ per slot：
    if last_output_time == 0: continue
    if idle < 3.0 and total < 120.0: continue                       BT:2565
    if slot.expect_marker:
       marked = _try_marker_extract()   ← 節流 3s + _feed_gen dirty-gate
       if not marked:
          turn_ended = 'esc to interrupt' 不在 live tail            BT:2588
          if slot.marker_forwarded: continue                        BT:2593
          if not (turn_ended and waited >= 30s): continue           BT:2598
          fb = _marker_fallback_text()  ← 走 tmux capture-pane
    → split_for_telegram → tg_api sendMessage（⚠ 不看回傳值）        BT:2772
```

### A1.2 既有機制盤點（設計時一律沿用，不重造）

| 機制 | 位置 | 現況行為 |
|---|---|---|
| `_verify_injection` | BT:4829 | 8s 窗；**只在失敗時通知**，成功完全靜默 |
| `_deferred_delivery_verdict` | BT:4796 | 45s 背景觀察；全程無訊號才警告 |
| 排隊中通知 | BT:4629 | busy guard 等滿 **8s** 才發文字 |
| `slot.inject_pending` | BT:754 | 只有 `/fetch` 會讀 |
| stall watchdog `_warn_stalled` | BT:1431 | **只在偵測到 macOS 阻擋彈窗時才叫**；非 macOS 永不觸發 |
| `_detect_rate_limit` | BT:2189 | slow_tick 2s，`rate_limit_notified` 一次性旗標 |
| marker fallback | BT:2578-2620 | 需 turn 結束 ＋ 等 ≥30s ＋ 3s 節流 |
| follow-up 多 block | BT:2630-2640 | `marker_forwarded` + `sent_responses` 去重 |
| `setMessageReaction` | BT:4420 | **已在用**（僅 slash command，emoji 👀，背景 thread） |
| `agent_status.StatusTracker` | main.py:1279 | 已產品化；main.py 有 0.6s monitor thread 在算 state + detail |

---

## A2. 問題：為什麼 tab 11（s87「調研者」）愛回不回

### A2.1 觀測事實

`/tmp/shellframe_bridge.log`：s87 有 **14 筆** `[send] s87 submit CR len≈1100`，
**0 筆** `flush s87`、**0 筆** `[send] s87 delivery …`。
→ 訊息確實注入，`_verify_injection` 也判定 delivered（所以連警告都沒有），
但**回覆一次都沒轉回 TG**。

`tmux capture-pane -t sf_s87` 顯示畫面上有**完整閉合**的區塊：

```
⏺ [[TG_REPLY_aa614c53]]
  已經切到 35B 無審查模型，slash 指令系統交給 sub 在背景刻。
  …
  [[/TG_REPLY_aa614c53]]

✻ Waiting for 1 background agent to finish
  ◯ fork  Wiring _parse_presets into config.py     5m 10s · ↓ 933.7k tokens
```

**回覆是寫好的、閉合的、就在畫面上——bridge 卻沒抽走。** 這不是「模型沒吐 marker」。

### A2.2 根因（依把握度排序）

#### ① 【實測證實・P0】`strip_ansi()` 的 `>>>…<<<` 劫持會整段吞掉 marker

`_pick_marker_reply`（BT:1900）先做 `clean_raw = strip_ansi(raw, sent_texts=[])`，
再在結果裡找 `[[TG_REPLY_x]]`。但 `strip_ansi` 開頭有一段 **legacy Strategy 1**：

```python
# BT:178-180
marker_match = re.search(r'>>>\s*(.*?)\s*<<<', clean, re.DOTALL)
if marker_match:
    return marker_match.group(1).strip()      # ← 直接 return，丟掉其餘全部
```

只要 120KB `pending_raw` 裡**任何位置**出現一個 `>>>` 後面接一個 `<<<`，
整個 buffer 就只剩那一小段，`[[TG_REPLY_…]]` 區塊被完全抹除。實測：

```
輸入： ">>> some python repl\nprint(1)\n<<< end\n[[TG_REPLY_ab]]真正的回覆[[/TG_REPLY_ab]]"
strip_ansi 輸出： 'some python repl\nprint(1)'          ← marker 區塊消失
```

`>>>` / `<<<` 在 agent 工作畫面上極常見：Python REPL 提示、bash here-string
（`cmd <<< "text"`）、git conflict、markdown 引言、diff 工具輸出。
`s87` 是「調研者」，跑 shell / 讀 config / 改 `.env`，命中機率很高。

**致命之處在於它會自我維持**：marker 路徑永遠抽不到 → `marker_forwarded` 永遠 False
→ 走 fallback → 但 fallback 需要 `turn_ended`（見 ③）→ 也永遠不觸發 →
**完全靜默，沒有任何 log、沒有任何通知**。這與 s87 的 log 樣態（0 筆 flush、0 筆 delivery）完全吻合。

這段 Strategy 1 是舊 `>>> response <<<` 方案的殘骸，現行 marker 是 `[[TG_REPLY_x]]`，
**已是死程式碼但保有破壞性副作用**。

#### ② 【實測證實・P0】pyte history deque 飽和後，scrollback 永久失明

`_extract_new_text`（BT:1651-1660）：

```python
htop = slot.screen.history.top        # deque(maxlen=800)
hlen = len(htop)
if slot._history_offset > hlen: slot._history_offset = 0
if slot._history_offset < hlen:
    ...
    slot._history_offset = hlen
```

`slot.screen = pyte.HistoryScreen(200, 50, history=800)`（BT:789），
`history.top` 是 `deque(maxlen=800)`。**deque 滿了之後 `len()` 恆為 800**，
內容從左邊被擠掉、長度不變。於是 `_history_offset` 卡死在 800：
`> hlen` 為假（相等）、`< hlen` 也為假 → **這個 slot 此後永遠不再掃描任何 scrollback**。

實測驗證（`pyte.HistoryScreen(80,5,history=10)` 餵 40 行）：

```
after 40 lines: len(top)=10  simulated _history_offset=10
-> offset pinned at maxlen; further lines never scanned: True
```

長壽命分頁（s87 建於 8/15，已跑兩天）必然早就飽和。之後只剩 50 行 live screen 可抽，
兩次 flush tick 之間捲過去的回覆就永久遺失。

#### ③ 【結構性・P0】fallback 的 `turn_ended` 前提，在「主 agent 等背景 sub」時永遠不成立

```python
turn_ended = not re.search(r'esc to interrupt', self._live_tail(slot), re.I)   # BT:2588
if not (turn_ended and waited >= self._MARKER_FALLBACK_SECS): continue          # BT:2598
```

`✻ Waiting for 1 background agent to finish` 期間，Claude Code 的 footer 仍掛著
`esc to interrupt` → `turn_ended` 恆為 False → 30s fallback **永遠不會觸發**。
背景 agent 跑 5 分鐘、30 分鐘、數小時，這條路就靜默數小時。
這正是 Howard 講的「愛回不回」的體感來源。

#### ④ 【高度可能・P1】`pending_raw` 120KB 驅逐掉 marker 區塊

`feed_output` 保留最後 120KB（BT:1489）。背景 agent 執行時，footer
（`5m 10s · ↓ 933.7k tokens` + spinner）持續整區重繪。以每次重繪 2–20KB 估算、
每秒數次，120KB ring **4–30 秒就整輪替換一次**；marker 掃描節流是 3s，
時序不巧就會在兩次掃描之間把整個 `[[TG_REPLY]]` 區塊擠出 buffer。
一旦擠掉，`_marker_spans` 只會看到孤兒 end marker（無 start）→ 跳過 → 永久遺失。

RD 需以診斷驗證此項（見 A6 步驟 0）。

#### ⑤ 【設計缺陷・P0】送 TG 失敗＝回覆永久蒸發

```python
# BT:2611 / 2629  —— 先加入去重集合
slot.sent_responses.add(fb / marked_reply)
...
# BT:2772  —— 再送出，且完全不看回傳
tg_api(self.config.bot_token, "sendMessage", {"chat_id": chat_id, "text": part})
```

`tg_api` 把所有失敗（429 flood-wait、400、逾時、DNS）都轉成 `{"ok": False, …}` 回傳值，
而這裡**不看**。全 repo `grep 429|retry_after` 零命中——沒有任何重試或退避。
回覆已經進了 `sent_responses`，於是**永不重送、也永不會被重新抽取**。

#### ⑥ 【設計缺陷・P0】沒人收的回覆，照樣被標記成已送

```python
target_chats = set()          # BT:2750
for uid, active_sid in list(self._user_active.items()):
    if active_sid == sid and uid in self._user_chat: target_chats.add(...)
if sid == self._slot_order[0]: ...
```

若某分頁沒有任何使用者 active、也不是 `_slot_order[0]`，`target_chats` 是空集合：
回覆被抽出、加進 `sent_responses`、然後**送給零個人**。之後 `/fetch` 也救不回來
（去重集合已污染）。master 派工的 worker 分頁天生落在這個洞裡。

### A2.3 送達方向的問題

| 情境 | 使用者看到什麼 | 應該是什麼 |
|---|---|---|
| 注入成功、turn 開始 | **什麼都沒有** | 一個輕量已讀訊號 |
| 排隊等 0–8s | **什麼都沒有** | 立刻就該有訊號 |
| 排隊等 8s+ | 文字「⏳ 已收到、排隊中」 | 保留（這則有資訊量） |
| busy guard 等滿 120s 強制注入 | **什麼都沒有**（會打斷 agent 回合） | 必須明講 |
| 回合超長（背景 sub） | **什麼都沒有** | 心跳 |

---

## A3. 設計 A-1：送達回執（reaction 狀態機）

### A3.1 原則

- **用 reaction，不用新訊息**。Telegram 的 reaction 是「就地標記在使用者自己那則訊息上」，
  不佔對話列、不推播、不洗版。repo 已有先例（BT:4420 slash command 用 👀），API 路徑已驗證。
- **一則使用者訊息＝一個 reaction 槽**。Bot 只能設一個 reaction，後設的取代先設的——
  這正好是一個狀態機，不需要額外去重。
- **只表達「送達」，不表達「回覆好了」**。回覆本身就是最好的完成訊號；
  再加一個 ✅ 只是雜訊。

### A3.2 狀態機

| 時機 | Reaction | 觸發點 |
|---|---|---|
| T0 已收下，準備注入 | 👀 | `_handle_update` 決定要轉發之後（白名單過、有 active slot、`forwarded` 非空） |
| T1 確認送進去了 | 🫡 | `_verify_injection` 回 `delivered=True`；或 `_deferred_delivery_verdict` 提前判定 OK |
| T2 送不進去 | **清空 reaction** ＋ 既有文字警告 | `notify_failed` 或 deferred verdict 逾時 |
| T3 busy guard 等滿 120s 被迫注入 | 保持 👀 ＋ **新增**文字警告 | busy guard `while` 迴圈因 deadline 而非 break 退出 |

T0 立刻發出，把現在「0–8s 完全靜默」的空窗補掉；8s 的文字「⏳ 排隊中」保留不動
（它帶有「對方正在忙」這個 reaction 表達不了的資訊）。

### A3.3 Emoji 選用限制（RD 必讀）

Telegram Bot API 的 `setMessageReaction` **只接受一份固定的 emoji 白名單**
（`ReactionTypeEmoji`）。任務單提到的 **✅ 不在白名單內，會被 API 拒絕**。

- 👀 —— 本 repo BT:4423 已實際在用，**已驗證可行**。
- 🫡 / 👌 / 🤝 / ⚡ —— 屬白名單，但 RD 施工時**必須先實測**：

```bash
curl -s -X POST "https://api.telegram.org/bot$TOKEN/setMessageReaction" \
  -H 'Content-Type: application/json' \
  -d '{"chat_id":<自己的 chat>,"message_id":<自己剛發的訊息>,"reaction":[{"type":"emoji","emoji":"🫡"}]}'
```

回 `{"ok":true}` 才可寫進程式；回 `REACTION_INVALID` 就退回 👌。

### A3.4 失敗處理（不可以變成新的洗版來源）

```
reaction API 失敗
 ├─ 記一行 [reaction] <chat> <emoji> failed: <desc>
 ├─ 不退回文字（單則失敗沒有告知價值，退回文字反而製造雜訊）
 └─ 同一 chat 連續失敗 ≥ 3 次
     ├─ 發一次性文字：「送達回執功能不可用（TG API 拒絕），已停用；
     │    訊息本身仍正常轉送。」
     └─ self._reaction_disabled = True（直到 bridge reload/restart 才復原）
```

**T2 的文字警告不受此開關影響**——那條是「訊息可能沒送進去」的實質告警，
本來就是文字，必須永遠送得出去。

### A3.5 需要的資料流改動

`message_id` 目前只在 `_handle_update` 區域變數裡（BT:4417），沒往下傳。RD 需要：

1. `_handle_update` 取 `origin_msg_id = msg.get("message_id")`，
2. 存進 slot：`slot.pending_reaction = (chat_id, origin_msg_id)`（覆蓋式，只留最新一則），
3. `_send()` / `_verify_injection` 後續路徑從 `slot.pending_reaction` 取，
4. 收到新使用者訊息時覆蓋（舊的那則不再更新 reaction，停在它最後的狀態，符合直覺）。

### A3.6 效能標註

| 項目 | 值 |
|---|---|
| **dirty-gate** | 不適用——這不是掃描，是事件驅動（每則使用者訊息各 1–2 次 API 呼叫） |
| **節流** | 天然節流：每則使用者訊息最多 2 次（T0 一次、T1/T2 一次）。失敗 3 次後全域關閉 |
| **最壞成本** | 對 `_flush_loop` = **0**（完全不在該迴圈）。呼叫全在既有背景 thread（`_handle_update` 的 dispatch thread、`_send_tracked` thread）。HTTPS 往返約 100–300ms，不持鎖 |
| **perf 計時** | 不需進 `_perf_*`（不在 flush loop）。改以 `[reaction] …` log 行記錄 |

⚠ 硬性要求：reaction 的 `tg_api` 呼叫**一律 `timeout=5`、一律在 `slot.write_lock` 之外**。
repo 已有血案註解（BT:4720「Notify OUTSIDE write_lock — tg_api can block up to 35s」）。

---

## A4. 設計 A-2：長回合心跳

### A4.1 資料源決策：複用 `agent_status.StatusTracker`，不新增任何掃描

心跳最容易寫壞的地方是「為了知道現在在幹嘛，去掃畫面」。**不要。**

`agent_status.py` 已經產品化：`StatusTracker.status_for(sid, worker, screen_tail, now)`
從 Claude transcript JSONL / Codex rollout JSONL 解析出 `state` 與
`_detail()` 的 `(action, task, narration)`（agent_status.py:757），
而 main.py 已有一條 **0.6s 的 monitor thread**（main.py:2321-2437）在跑它，
結果推給側欄。bridge 與 main.py **在同一個進程**（main.py:44 `import agent_status`，
bridge 由 main.py 持有）。

→ **心跳只讀 monitor thread 已經算好的最近一次結果，零額外解析、零額外 I/O。**

RD 要加的是一個唯讀 callback（比照既有 `on_model_info` 的注入方式，
`TelegramBridge.__init__` BT:819 已有 8 個同型 callback）：

```python
# main.py 建 bridge 時注入
on_agent_status=lambda sid: self._status_tracker.last_result(sid)   # 只讀快取，不觸發解析
```

`StatusTracker` 需補一個 `last_result(sid)`（回最近一次 `status_for` 的輸出，
或 `None`）。**明確禁止**在這個 callback 裡呼叫 `status_for()`——那會觸發
transcript 解析，把成本帶進 flush loop。

### A4.2 觸發條件（全部在既有 `slow_tick`，2s cadence，不新增迴圈）

```python
# _flush_loop 內，緊接在既有 stall 偵測區塊之後（BT:2464 後）
if slow_tick:
    t0 = self._perf_t()
    for sid in sids:
        slot = self.slots.get(sid)
        if not slot: continue
        # G1 有等待中的使用者訊息
        if not slot.awaiting_response: continue
        # G2 這個 epoch 還沒回過任何東西
        if slot.marker_forwarded: continue
        if slot.last_extraction_ts > slot.msg_sent_ts: continue
        waited = now - (slot.msg_sent_ts or now)
        # G3 首次門檻
        if waited < self.HEARTBEAT_FIRST_S: continue
        # G4 間隔節流（指數退避）
        if now < slot._hb_next_ts: continue
        # G5 有人收
        if not self._target_chats_for(sid): continue
        slot._hb_next_ts = now + min(
            self.HEARTBEAT_MAX_S,
            self.HEARTBEAT_INTERVAL_S * (self.HEARTBEAT_BACKOFF ** slot._hb_count))
        slot._hb_count += 1
        threading.Thread(target=self._send_heartbeat,
                         args=(sid, waited), daemon=True).start()
    self._perf_end("heartbeat_gate", t0)
```

常數（放在 BT:2394 那批常數旁）：

```python
HEARTBEAT_FIRST_S    = 180.0   # 3 分鐘沒消息才開始心跳（正常回合不會被打擾）
HEARTBEAT_INTERVAL_S = 300.0   # 基礎間隔 5 分鐘
HEARTBEAT_BACKOFF    = 1.5     # 每發一次拉長 1.5 倍
HEARTBEAT_MAX_S      = 1800.0  # 上限 30 分鐘一則
```

節奏：3min → 8min → 15.5min → 26.75min → 之後每 30min。跑一小時的背景任務約收 4 則。

### A4.3 停止條件

| 事件 | 動作 |
|---|---|
| 任何回覆被轉發成功 | `slot._hb_count = 0`、`_hb_next_ts = 0`（`awaiting_response=False` 已自動關閘） |
| 新使用者訊息 | 同上重置（在 BT:4500 那批 epoch 重置裡一起做） |
| slot 被 prune / session 死掉 | `_remove_slots_locked` 自然帶走 |
| `target_chats` 空 | G5 直接不發（不要對空氣心跳） |
| 使用者下 `/quiet`（新增，見 A4.5） | 該 slot 本 epoch 停發 |

### A4.4 心跳內容

```
⏳「調研者」還在跑 · 已 8 分 12 秒
   working · Delegating "Wiring _parse_presets into config.py"
   在等 1 個背景 agent（↓ 933.7k tokens）
   /11 切過去看 · /fetch 抓現況 · /quiet 這輪別再提醒
```

- 第 2 行來自 `StatusTracker` 的 `state` + `_detail().action`。
- 第 3 行來自畫面尾端的 `Waiting for N background agent` / `↓ … tokens`——
  **用既有的 `_live_tail(slot)`**（走 `_slot_display` 的 `_feed_gen` 快取，不新增 render），
  一個預編譯 regex 掃 6 行。
- 拿不到 status（transcript 還沒落盤、shell 分頁）→ 降級成只有第 1 行 + 第 4 行。**絕不因此不發**。

**內容去重**：算一個 `hash(state + action + 背景 agent 數)`，
與 `slot._hb_last_hash` 相同**且**距上次未滿 `2 × 當前間隔` → 跳過（但仍推進退避計數）。
避免「連三則一模一樣的話」。

### A4.5 新增 `/quiet` 指令

加進 BT:4413 的 bridge-own 指令清單。語義：對當前 active slot 的**這一個 epoch**
停發心跳（下一則使用者訊息自動復原）。這是給「我知道它在跑、別吵我」的出口，
比讓使用者去設定頁關掉整個功能好。

### A4.6 效能標註

| 項目 | 值 |
|---|---|
| **dirty-gate** | **不需要 `_feed_gen` gate**——閘門本身不碰 buffer、不碰 `screen.display`，只讀 slot 上的 float／bool 欄位。等價的 gate 是 `slot.awaiting_response`（idle slot 直接跳過）。`_live_tail` 只在「已決定要發」之後、在背景 thread 裡呼叫，且走既有 `_feed_gen` display 快取 |
| **節流** | `HEARTBEAT_FIRST_S=180` 首次門檻 ＋ `_hb_next_ts` 指數退避（300s→1800s）＋ 內容 hash 去重 |
| **最壞成本** | 閘門：8 個欄位比較 + 1 次 dict get ≈ **5 µs**／slot／tick。slow_tick 是 2s → 30 次/分鐘 → **0.15 ms/分鐘/slot = 0.00025% CPU/slot**。發送：最快 300s 一次，`_live_tail` 走快取最壞 1 次 120KB render ≈ 0.9 ms → **0.0003% CPU/slot**，且在背景 thread |
| **perf 計時** | **是**，`_perf_end("heartbeat_gate", t0)`；發送端另記 `[heartbeat] <sid> waited=Ns state=…` |

對照守則第 3 條：任何 O(buffer) 操作 × tick 頻率要 < 1–2%/slot。
本設計閘門是 **O(1)**，唯一的 O(buffer) 操作（`_live_tail`）頻率是 **每 5–30 分鐘一次**。

---

## A5. 設計 A-3：turn 未結束時要不要放行 fallback

### A5.1 利弊分析

**支持放行**：A2.2 ③ 證明「主 agent 等背景 sub」時 turn 永遠不結束。
這不是罕見情境——ShellFrame 的 master/worker 契約鼓勵派工，
主 agent 等 sub 是**設計上的常態**。不放行等於這類回合永遠靠使用者手動 `/fetch`。

**反對放行**（現行門檻存在的理由，都成立）：

1. `_marker_fallback_text` → `_peek_last_response` 取「畫面上最後一個 AI block」。
   turn 進行中，那可能是工具描述、思考片段、半句話。
2. 送出後立刻 `slot.sent_responses.add(fb)`（BT:2611）。**半成品一旦進去，
   真正的完整回覆若與之相似就被永久壓制**（洞 #13/#10）。這是最嚴重的副作用。
3. `_peek_last_response` 走 `tmux capture-pane -S -3000`（BT:2253）——
   fork + 3000 行擷取，成本遠高於畫面掃描，不能高頻跑。

### A5.2 建議：**分成兩件事，只放行其中一件**

#### ✅ 放行：turn 未結束時的 **marker 區塊**轉發

這一件其實**不需要新設計**——`_try_marker_extract` 本來就只認**閉合**的
`[[start]]…[[end]]` 配對（`_marker_spans` BT:1849，未閉合的 span `end_idx=-1` 會被
`has_open` 標記並在 `total < 30s` 時擋掉）。閉合的 marker 區塊就是模型**明確宣告
「這段是要給手機的最終文字」**——它已經寫完了，turn 有沒有結束無關。

而且 `total >= 120s` 這個分支**本來就會**在 turn 未結束時進入 marker 抽取。
所以真正的問題不是門檻，是 A2.2 的 ①②④ 讓它抽不到東西。
**修好那三個 bug，這條路自動就通了。** 這是本設計最重要的判斷：
不要為了繞過 bug 而新增一條更危險的路徑。

需要補的只有一處：把 `has_open` 的強制等待從 `total < 30.0`
改成「以**這則使用者訊息**為起點」（`now - slot.msg_sent_ts < 30.0`），
理由與 BT:2597 的 `waited` 註解相同——忙碌分頁的 `total` 會停在很久以前。

#### ❌ 不放行：turn 未結束時的**純文字 peek** 當成回覆轉發

理由就是 A5.1 的第 2 點。但也不能什麼都不做，所以：

#### 🟡 折衷：超長回合的「進行中預覽」，走心跳、不走回覆路徑

當 `waited >= PREVIEW_AFTER_S`（預設 **900s / 15 分鐘**）且本 epoch 仍零回覆，
心跳訊息**附帶**畫面上最後一個 AI block 的前 300 字：

```
⏳「調研者」還在跑 · 已 17 分 3 秒
   working · Delegating "Wiring _parse_presets…"
   ── 進行中預覽（非最終回覆）──
   已經切到 35B 無審查模型，slash 指令系統交給 sub…
   /fetch 看完整畫面
```

安全條件（缺一不可，RD 必須全部實作）：

| # | 條件 | 為什麼 |
|---|---|---|
| S1 | **不得**加入 `slot.sent_responses` | 否則真回覆來時被壓制——這是唯一不可退讓的一條 |
| S2 | **不得**設 `slot.expect_marker = False`、不得清 `pending_raw`、不得動 `marker_forwarded` | 不能中止真正的 marker 監聽 |
| S3 | 必須帶「進行中預覽（非最終回覆）」字樣 | 使用者不能誤以為這是答案 |
| S4 | 每個 epoch 最多送 **2 次**預覽 | `slot._preview_count` |
| S5 | `_peek_last_response` 節流 ≥ 900s／slot（沿用 `_fb_next_ts` 機制的形狀） | tmux capture 3000 行很貴 |
| S6 | 預覽內容與上次相同 → 跳過 | 卡死的分頁不該重複貼同一段 |

### A5.3 效能標註（進行中預覽）

| 項目 | 值 |
|---|---|
| **dirty-gate** | 是——前提是心跳閘門（含 `awaiting_response` ＋ `marker_forwarded` 為假 ＋ `last_extraction_ts <= msg_sent_ts`）。另加 `slot._feed_gen != slot._preview_gen`：buffer 沒新 bytes 就不重取 |
| **節流** | `PREVIEW_AFTER_S = 900`（15 分鐘才第一次）＋ 每 epoch 上限 2 次 ＋ 內容去重 |
| **最壞成本** | `_peek_last_response` 含 `tmux capture-pane -S -3000` ＋ 逐行解析，估 **30–80 ms**／次。每 slot 每 epoch 最多 2 次、間隔 ≥15 分鐘 → **2 × 80ms / 1800s ≈ 0.009% CPU/slot**。且在背景 thread，`_flush_loop` 完全不阻塞 |
| **perf 計時** | 是——`_perf_end("preview_peek", t0)`（雖在背景 thread，仍計進同一個 `_perf` dict，讓 60s 摘要看得到；`_perf_end` 的 dict 更新在 CPython 下是原子的，可接受） |

---

## A6. 設計 A-4：其餘靜默丟訊的洞

以下為程式碼審查所得（不只任務單列的）。**P0 = 會造成整段訊息／回覆無聲蒸發**。

| # | 洞 | 位置 | 修法（給 RD 的方向） |
|---|---|---|---|
| **P0-1** | `strip_ansi` Strategy 1 `>>>…<<<` 劫持整個 buffer | BT:178-180 | **直接刪除這三行**。現行 marker 是 `[[TG_REPLY_x]]`，Strategy 1 是無人使用的殘骸。刪掉同時省下每次 marker scan 一次 DOTALL 全 buffer 搜尋 |
| **P0-2** | pyte history deque 飽和 → scrollback 永久失明 | BT:1651-1660 | 偵測 `hlen == htop.maxlen` 時，改為固定掃描 history 尾端 K 行（K=64，`itertools.islice(htop, hlen-64, hlen)`），靠既有 `sent_responses` 去重。成本 O(64 行) 且僅在飽和後 |
| **P0-3** | 送 TG 不看回傳＋已先進 `sent_responses` → 失敗永久遺失 | BT:2611/2629 送出於 BT:2772 | ①`sent_responses.add()` 移到 **`ok:true` 之後**；②檢查 `resp.get("ok")`，`description` 含 `retry after N` 時 `sleep(min(N,30))` 重試一次；③最終失敗 → 不加去重集合 ＋ log ＋ 對該 chat 發一則「⚠ 回覆送出失敗，用 /fetch 重取」 |
| **P0-4** | `target_chats` 為空仍加進 `sent_responses` | BT:2750-2758 | `if not target_chats:` → log `[flush] <sid> no target chat, keep for /fetch` 且**不要**加入去重集合、**不要**清 `pending_raw` |
| **P0-5** | 語音下載失敗完全無 `else` | BT:4304-4309 | `if audio_path:` 補 `else:` 發「⚠ 語音檔下載失敗」 |
| **P0-6** | `_inject()` 的 `slot.write_fn` 無例外保護，pane 死掉時例外逸散到 daemon thread | BT:4646-4654 | 包 try/except，失敗 → 清 reaction ＋ 發「⚠ 分頁已不存在／寫入失敗」 |
| **P0-7** | `_save_offset()` 早於 `_update_queue.put()`；reload/restart 時佇列內訊息永久遺失 | BT:3278 / 3310 | offset 改在 `_dispatch_loop` 處理完該則之後才存；或 `stop()` 時把殘留佇列寫進 `_save_state()` 並於下次啟動重播 |
| **P0-8** | busy guard 等滿 120s 仍強制注入（會打斷 agent 回合），使用者完全不知情 | BT:4620-4640 | `while` 因 deadline 退出時設旗標，注入後發「⚠ 對方回合逾時未結束，訊息已強制送入，可能打斷它上一個回合」 |
| P1-9 | `marker_forwarded=True` 後 fallback 永久停用；模型之後改用無 marker 回覆就再也不轉發 | BT:2593 | 改成「距上次 marker 轉發 > 180s 且有新的 AI block」時允許一次 fallback |
| P1-10 | 新訊息 epoch 清 `pending_raw` ＋ 換 marker token，殺掉在途的前一則回覆 | BT:4495-4499 / 4572 | 清空前先跑一次 `_try_marker_extract`；舊 token 存進 `slot.prev_markers`（保留 1 組），marker 掃描時一併比對 |
| P1-11 | `_TUI_SENTINEL_RE` 的 `\w+(ed\|ing) for \d+s` 會截斷正常句子 | BT:256 | 加行首錨定條件：只在該行**同時**含 spinner 字元或位於區塊最後 3 行時才截斷 |
| P1-12 | `echo_keywords` 用子字串比對，會刪掉含關鍵字的正常句 | BT:229、`filters.json` | 改成整行比對或加 word-boundary；保留一個 `echo_keywords_substring` 子清單給真正需要的 |
| P1-13 | `sent_responses` 的 superset/subset 迴圈依 set 迭代順序 `break`，行為不確定 | BT:1774-1782 | 改用 `list` 保序，或只做精確比對 + 明確的「前綴包含」規則 |
| P1-14 | `_warn_stalled` 在非 macOS 永不觸發（`_detect_blocking_popup` 直接回 None） | BT:1401-1402 / 1441 | 非 macOS 改為「無法偵測彈窗」的降級訊息，而非完全靜默 |
| P2-15 | LINE `_reply_text` 只送 `chunks[0]`，超長直接截斷無提示 | bridge_line.py:421-429 | 比照 TG 的 `split_for_telegram` 多則送出 |
| P2-16 | `feed_output` 的 `stream.feed` 例外被 `pass` 吞掉，該 chunk 永久消失 | BT:1485-1487 | 至少 log 一行（目前完全無聲） |

**修復順序建議**：P0-1 → P0-2 → P0-3 → P0-4，這四項就能讓 s87 這類分頁恢復；
其餘依序。P0-1／P0-2 兩項有本文件的實測重現腳本可直接寫成單元測試。

### A6.0 步驟 0：先加診斷，別憑猜

在動任何修法之前，RD 先加一條**只在 `settings.perf_debug` 開啟時生效**的診斷 log，
放在 `_try_marker_extract` 失敗分支（BT:1962）：

```python
if not reply:
    if self._perf_enabled:
        raw_has = slot.reply_start_marker in slot.pending_raw          # 原始 buffer 有沒有
        clean_has = slot.reply_start_marker in clean_raw               # 清洗後還有沒有
        _blog(f"[marker-miss] {slot.sid} raw={raw_has} clean={clean_has} "
              f"rawlen={len(slot.pending_raw)} gen={gen} open={has_open}\n")
```

判讀：

| `raw` | `clean` | 結論 |
|---|---|---|
| False | — | 根因 ④（120KB 驅逐）或模型真的沒吐 marker |
| True | **False** | **根因 ①（`strip_ansi` 劫持）確認** |
| True | True | `_marker_spans` 配對或 `clean_mobile_marker_response` 過濾問題 |

成本：僅 `perf_debug=on` 時、僅在失敗分支、兩次 `str.find`（120KB → 0.032 ms×2）。
`perf_debug=off` 時為一次 bool 判斷。

---

## A7. 主題 A 效能總表

| 新增項目 | dirty-gate | 節流 | 最壞成本／slot | perf 計時 |
|---|---|---|---|---|
| 送達 reaction | N/A（事件驅動） | 每則訊息 ≤2 次；失敗 3 次全域關 | flush loop **0%**；背景 thread HTTPS ≤300ms | `[reaction]` log |
| 心跳閘門 | `awaiting_response`＋`marker_forwarded`＋`last_extraction_ts` | 首次 180s、退避 300→1800s、內容 hash | 5 µs × 30 次/分 = **0.00025%** | ✅ `heartbeat_gate` |
| 心跳發送 | 同上 | ≥300s/次 | 0.9 ms／5 分鐘 = **0.0003%**（背景 thread） | `[heartbeat]` log |
| 進行中預覽 | 同上 ＋ `_feed_gen != _preview_gen` | 900s 後才首次、每 epoch ≤2 次 | 80 ms × 2 ／1800s = **0.009%**（背景 thread） | ✅ `preview_peek` |
| `[marker-miss]` 診斷 | 僅失敗分支 | 僅 `perf_debug=on` | 0.064 ms × ≤20 次/分 = **0.002%** | 併入 `extract_marker` |
| **合計新增** | | | **< 0.02% CPU/slot** | |

**同時是效能改善**：刪掉 P0-1 的 `>>>…<<<` DOTALL 搜尋，
每次 marker scan（節流 3s）省下一次 120KB 全 buffer 正則搜尋。
以 8 slots 全部 stuck 的最壞情境估算，可回收約 **0.3–0.8% CPU**。
沒有任何一項是 O(buffer) × 高頻。

## A8. 主題 A 風險

| 風險 | 影響 | 緩解 |
|---|---|---|
| reaction emoji 被 API 拒絕 | T1 狀態永遠設不上 | A3.3 的 curl 驗證列為施工第一步；退回 👌 |
| 心跳變成新的洗版來源 | 使用者更煩 | 180s 首次門檻 ＋ 指數退避 ＋ 內容 hash ＋ `/quiet` 出口。**寧可漏發不可多發** |
| P0-3 改動 `sent_responses.add()` 時序，可能引入重送 | 使用者收到重複回覆 | `ok:true` 後才 add，時序上仍在下一次 flush tick 之前（0.5s），重送窗極窄；QA 需針對「TG 回 429」情境做故障注入 |
| P0-2 掃描 history 尾端 64 行，可能重抽舊內容 | 重複轉發 | 完全依賴 `sent_responses`——所以 P0-3、P1-13 必須先修好，否則去重集合本身不可靠。**實作順序不可顛倒** |
| A5.2 判斷錯誤（其實 marker 路徑還有第 4 個 bug） | 修完仍不會回 | A6.0 的 `[marker-miss]` 診斷就是為此設計，先量再改 |
| `StatusTracker.last_result` 回舊資料 | 心跳講的活動過期 | 訊息裡標注資料時間；`last_result` 超過 30s 未更新就只印第 1 行 |

## A9. 主題 A 實作步驟（給 RD）

> 全部集中在 `bridge_telegram.py`；`web/index.html` 不需改動 → **可用 `sfctl reload` 迭代**，
> 最終驗收才 `sfctl restart`。

**階段 0 — 量測**
1. `_try_marker_extract`（BT:1939）失敗分支加 A6.0 的 `[marker-miss]` 診斷。
2. 開 `settings.perf_debug`，觀察 s87 型分頁 30 分鐘，記錄 `raw`/`clean` 分佈。
3. commit。

**階段 1 — P0 靜默丟訊（先修，這是根治）**
4. 刪 `strip_ansi` BT:178-180 三行；補單元測試 `tests_tg_marker_hijack.py`
   （斷言含 `>>>…<<<` 的 buffer 仍能抽出 `[[TG_REPLY_x]]` 區塊）。
5. `_extract_new_text`（BT:1651-1660）修 deque 飽和；補測試
   （`pyte.HistoryScreen(80,5,history=10)` 餵 40 行後仍能抽到新內容）。
6. `_flush_loop` BT:2611/2629/2772：`sent_responses.add()` 後移到 `ok:true` 之後；
   加 429 `retry after` 單次重試；最終失敗發告警。
7. `_flush_loop` BT:2750：`target_chats` 空時不污染去重集合。
8. `_handle_update` BT:4304 語音失敗補 `else`；`_inject` BT:4646 包 try/except。
9. `_poll_loop`/`_dispatch_loop` offset 時序（BT:3278）。
10. busy guard deadline 逾時告警（BT:4620）。
11. `sfctl reload` + 實測 s87 型分頁能否收到回覆。**commit。**

**階段 2 — 送達回執**
12. `Slot.__init__`（BT:706）加 `self.pending_reaction = None`、`self._reaction_fail = 0`。
13. `TelegramBridge` 加 `self._reaction_disabled = False`。
14. 新增 `_set_reaction(chat_id, message_id, emoji_or_none)`：背景 thread、`timeout=5`、
    失敗計數與全域關閉邏輯（A3.4）。
15. `_handle_update`（BT:4437 之後、BT:4747 之前）：存 `slot.pending_reaction`，T0 發 👀。
16. `_send()`（BT:4681-4736）：delivered → T1 🫡；`notify_failed` / deferred 逾時 → T2 清空。
17. 補 `tests_tg_reaction.py`（mock `tg_api`，斷言狀態轉移與失敗 3 次後停用）。**commit。**

**階段 3 — 心跳**
18. `agent_status.StatusTracker` 加 `last_result(sid)`（唯讀快取，**不得**觸發 `status_for`）。
19. main.py 建 bridge 處加 `on_agent_status` callback（比照 `on_model_info`）。
20. `Slot.__init__` 加 `_hb_next_ts / _hb_count / _hb_last_hash / _preview_count / _preview_gen`。
21. BT:2394 常數區加 `HEARTBEAT_*` / `PREVIEW_AFTER_S`。
22. `_flush_loop` slow_tick 區塊（BT:2464 之後）加 A4.2 閘門 ＋ `_perf_end("heartbeat_gate")`。
23. 新增 `_send_heartbeat(sid, waited)`（背景 thread）＋ `_target_chats_for(sid)`
    （把 BT:2750 與 BT:1451 兩處重複的邏輯抽出來共用）。
24. 新增 `/quiet` 指令（BT:4413 清單 ＋ `_handle_command`）。
25. 新訊息 epoch（BT:4500）重置心跳狀態。
26. 補 `tests_tg_heartbeat.py`（fake slot，斷言 180s 前不發、退避序列、`marker_forwarded` 後停）。**commit。**

**階段 4 — 進行中預覽（A5.2 折衷）**
27. `_send_heartbeat` 內加 S1–S6 全部條件的預覽段。
28. `_perf_end("preview_peek", t0)`。
29. 補測試斷言 **S1**（預覽內容絕不進 `sent_responses`）。**commit。**

**階段 5 — P1**
30. 依 A6 表格 P1-9 ～ P1-14 逐項，每項一次 commit。

---
# 主題 B：自有 Harness 的「主動程度」可調設計

## B1. 現況

### B1.1 端點實測（2026-08-17，**更正** `extending-and-harness-plan.md` 第 0 節）

| 端點 | 狀態 | 實測內容 |
|---|---|---|
| LiteLLM Gateway `190:4000` | 活著，**需 API key** | 無 key 打 `/v1/models` → **HTTP 401** `{"error":{"message":"Authentication Error, No api key passed in.","type":"auth_error"}}`。規劃文件寫的「掛 0 個模型／回空陣列」**不正確**——是驗證失敗，不是沒模型 |
| vLLM `190:8000` | **活著**（規劃文件寫「掛掉」，已復活） | `qwen3.6-35b-heretic`（`AEON-7/Qwen3.6-35B-A3B-heretic-NVFP4`，`max_model_len` 65536） |
| Ollama `190:11434` | 活著 | 17 個模型，含 `qwen38-27b-heretic:latest`、`gemma4-unc`、`gpt-oss:20b`、`huihui_ai/gpt-oss-abliterated:120b-q3_K_M`、`deepseek-ocr`、`qwen3-embedding:0.6b` |
| Ollama OpenAI 相容層 `190:11434/v1/models` | **HTTP 200** | ✅ 三個端點**全部**講 OpenAI 相容協定 → 探測鏈可以只寫一套 client，不需要 Ollama 專用的 `/api/tags` 轉換（規劃文件 D-3 可簡化） |

**結論**：27B／35B 未拍板 → 設計**不得寫死模型名**；Gateway 不是空殼而是要 key
→ 401 必須**明講「需要 API key」**，不能當成「沒模型」靜默跳過（否則 Howard 會第二次
得到「入口不明朗」的錯誤印象）。

### B1.2 repo 內已有的 LLM 呼叫（直接沿用，不要重造）

`_refine_transcript`（BT:3724）—— 語音逐字稿潤稿，是一條**完整可用的 OpenAI 相容 client**：

- `_refine_settings()` BT:3696 —— 從 `_read_settings()` 讀 `voice_refine_url / _model / _style`
- `_refine_pick_model(base_url)` BT:3705 —— `GET /models`、`timeout=5`、
  過濾 `embed|ocr|vision|-vl|rerank`、回第一個可用 chat model
- 請求：`{"model", "messages":[system,user], "temperature":0.2, "stream":False}`、`timeout=45`
- 回應：`(data.get("choices") or [{}])[0]["message"]["content"]`，剝掉 code fence
- **失敗一律回原文**（BT:3759），`_blog` 記一行

Harness 的 HTTP 層應該是這段的泛化版（多一個 `Authorization` header、多一個 JSON schema
解析、多一個探測鏈），而不是另起爐灶。**注意它目前沒有帶 `api_key`——Gateway 需要 key，
所以泛化時必須補上。**

### B1.3 設定基礎建設

- 檔案：`~/.config/shellframe/config.json`（main.py:82）
- 主進程讀寫：`load_config()` main.py:437 / `save_config()` main.py:541（原子寫 + fsync）
- bridge 端獨立讀取：`_read_settings()` BT:494，**1 秒 TTL 快取**；寫入 `_update_settings()` BT:514
- Web UI：`get_config()` main.py:2439 → JS 全域 `config`；存檔 `save_settings()` main.py:2522
  —— ⚠ **整包覆蓋 `cfg["settings"]`，沒有 schema、沒有驗證**
- 現成的 dropdown 範本：`#setting-lang`（`web/index.html:837-840`，
  同步於 `:4587`、handler 於 `:5376-5382`）——這是新增下拉選單最乾淨的參照
- ⚠ `web/index.html` 任何改動都需要 **`sfctl restart`**（webview 不 hot-reload）

---

## B2. 設計 B-1：主動程度四級

### B2.1 分級

只給自己用 → 略過對外安全邊界；但**「不要它自己亂動」仍然是硬需求**，
因為 harness 動的是 Howard 正在用的活分頁。

| 級別 | 值 | 能做 | 不能做 |
|---|---|---|---|
| **L0 關閉** | `off` | 什麼都不做。連端點探測都不跑 | 全部 |
| **L1 觀察** | `observe` | 呼叫模型判讀、寫 `[harness]` log、在側欄／設定頁顯示判讀結果與命中的端點 | **不得**發任何 TG／桌面通知，不得改任何 slot 狀態，不得寫 config |
| **L2 建議** | `suggest` | L1 全部 ＋ 發通知給 Howard 本人（TG／macOS banner）＋ 在通知上掛 inline 按鈕讓 Howard 一鍵確認執行某個動作 | **不得**在沒有 Howard 點擊的情況下執行任何動作 |
| **L3 協助** | `assist` | L2 全部 ＋ **自動執行白名單內的動作**（見 B2.2） | 白名單以外一律只能建議 |

**預設 `off`**。分級是單調遞增的（L3 ⊃ L2 ⊃ L1），所以 UI 只需要一個下拉，不需要一堆開關。

### B2.2 L3 自動執行白名單（**窮舉，不得擴充於程式碼之外**）

判準：**唯讀 或 純顯示 或 完全可逆，且不改變任何 AI session 的對話狀態**。

| 動作 | 為什麼安全 |
|---|---|
| `peek(sid)` 讀畫面 | 唯讀 |
| `read_bridge_log(n)` 讀 log 尾段 | 唯讀 |
| `set_signal(sid, state, reason)` 更新側欄燈號 | 純 UI，下一次真實訊號就覆蓋 |
| `set_status_detail(sid, text)` 更新側欄細節文字 | 純 UI |
| `notify_owner(text)` 發通知給 **Howard 本人** 的 chat_id | 收件人只有他自己；等同 L2 已允許的能力 |
| `rescan_marker(sid)` 強制重置 `marker_next_scan_ts` 觸發一次重掃 | 冪等、無副作用、不送出任何東西 |

實作上白名單應是一個模組層 `frozenset` + 一個 dispatch dict，
**不是** `getattr(self, action_name)`——後者等於把整個 bridge 的方法表交給模型。

### B2.3 永遠不允許自動執行（**任何級別，含 L3**）

| 類別 | 具體動作 |
|---|---|
| **改變 AI 對話** | `send_to_session` / 任何 PTY 注入 / `/compact` / `/model` / `/effort` / 回答畫面上的選單或 approval |
| **生命週期** | `restart` / `reload` / `close_session` / `new_session` / `delegate` / `rename` |
| **對外通訊** | 送訊給 Howard 本人以外的任何 chat_id、LINE、Email、任何第三方 API |
| **檔案／系統** | 檔案寫入、`git`、shell 執行、`launchctl`、安裝／卸載 plugin |
| **自我提權** | 讀寫 `settings.harness.*`（尤其**不得**改自己的 `level`）、改 `filters.json`、改任何 config |

這些在 L2 只能產生「建議 + inline 確認按鈕」，按鈕的 callback 走**既有**
`_handle_callback_query`（BT:3834）路徑，由既有程式碼執行，
**harness 不持有執行權**——它只能產生一個 callback_data 提案。

### B2.4 Fail-closed 規則

```python
_HARNESS_LEVELS = ("off", "observe", "suggest", "assist")

def harness_level() -> str:
    v = (_read_settings().get("harness") or {}).get("level")
    return v if v in _HARNESS_LEVELS else "off"      # 讀不到／非法值／型別錯 → off
```

任何例外、任何無法解析的設定，一律降到 `off`。
另外三個自動降級條件（都要發一次通知，不可靜默）：

1. 端點連續失敗 ≥ 3 次 → 進入 5 分鐘 cooldown（維持原級別，只是暫停呼叫）。
2. `max_calls_per_hour` 額度用盡 → 該小時內降為 `observe`。
3. 模型連續 3 次回不合 schema 的輸出 → 降為 `observe` 並通知（模型換掉了／端點串錯）。

---

## B3. 設計 B-2：設定介面

### B3.1 `settings.harness.*`

```jsonc
"harness": {
  "level":              "off",     // off | observe | suggest | assist
  "base_url":           "",        // 空字串 = 走 B4 探測鏈；有值 = 只用它，不 fallback
  "api_key":            "",        // UI 遮罩顯示；Gateway 需要
  "model":              "auto",    // "auto" = 探測；否則直接使用（不驗證存在與否）
  "model_prefer":       "",        // 子字串偏好，例 "35b" / "27b" / "gpt-oss"；空 = 用第一個
  "timeout_s":          20,
  "max_calls_per_hour": 60,
  "scenes": { "screen_triage": true }   // 場景開關；第一版只有這一個
}
```

**設計理由**

- 巢狀成一個 `harness` 物件（而非 `harness_level` / `harness_base_url` 一堆平鋪 key）：
  `save_settings` 是整包覆蓋（main.py:2522），巢狀物件讓 `harness_level()` 一次讀完，
  也讓未來加場景不污染頂層命名空間。
- `base_url` 有值時**刻意不 fallback**：Howard 明確指定了端點卻被悄悄換掉，
  比直接報錯更糟（「我以為在用 35B，其實跑 Ollama 的 0.6b」）。
- `model_prefer` 而非 `model` 白名單：27B/35B 未拍板，偏好字串讓切換不需改程式。
- `max_calls_per_hour` 是硬煞車，不是建議值。

### B3.2 UI

設定頁新增一區「🤖 Harness（本機 AI 助手）」，**一個下拉 + 一段狀態列**：

```
🤖 Harness（本機 AI 助手）
   主動程度  [ 關閉               ▾ ]
             ├ 關閉
             ├ 觀察（只寫 log，不打擾）
             ├ 建議（通知我，我按了才做）
             └ 協助（自動做唯讀／純顯示的事）

   ● 已連線 · vLLM 192.168.51.190:8000 · qwen3.6-35b-heretic
     探測於 14:32:07 · 本小時已用 3/60 次        [ 重新探測 ]

   ▸ 進階（level ≠ 關閉 時才展開）
     端點 base_url  [ 留空＝自動探測                    ]
     API key        [ ••••••••                          ]
     模型           [ auto                              ]
     偏好           [ 例：35b                           ]  逾時 [ 20 ] 秒
```

- 下拉照 `#setting-lang`（`web/index.html:837`）的 pattern 做，**不要**做成四個 toggle。
- 狀態列那一行就是規劃文件 D 節要的「入口明朗化」——
  它必須顯示**實際命中**的端點與模型，不是設定值。
- 「重新探測」按鈕強制清快取重跑。

### B3.3 需要改動的 7 個地方（依既有慣例）

| # | 位置 | 內容 |
|---|---|---|
| 1 | `main.py:181-188` `DEFAULT_CONFIG["settings"]` | 加 `"harness": {...}` 預設物件 |
| 2 | `web/index.html` `#settings-panel-general`（`:818-1000` 區） | 新區塊 HTML（含 `<select id="setting-harness-level">`） |
| 3 | `web/index.html:1325` `I18N.en` | `harnessLabel` / `harnessHint` / 四個層級名 / 狀態列字串 |
| 4 | `web/index.html:1492` `I18N['zh-TW']` | 同上 |
| 5 | `web/index.html:~4579-4644` modal-open sync | 讀 `s.harness`，設下拉值、展開/收合進階區、拉一次狀態 |
| 6 | `web/index.html:~4645-4720` change handler | 寫 `config.settings.harness.*` → `save_settings` |
| 7 | 消費端 | bridge 走 `_read_settings()`（1s TTL）；main.py 走 `load_config()` |

⚠ 第 2–6 項動到 `web/index.html` → **必須 `sfctl restart`**，不能只 `reload`。

---

## B4. 設計 B-3：端點與模型解析

### B4.1 探測鏈

```
harness_endpoint()  ← 有 300s 快取，見 B4.2
│
├─ settings.harness.base_url 非空？
│    └─ 是 → 只試這一個。失敗就是失敗，回 {"ok":False,"reason":"指定端點不可用: <err>"}
│            （不 fallback —— 明確指定不得被悄悄替換）
│
└─ 否 → 依序試以下候選，第一個成功即採用：
     ① http://192.168.51.190:4000/v1   LiteLLM Gateway（統一總機，優先）
     ② http://192.168.51.190:8000/v1   vLLM（35B）
     ③ http://192.168.51.190:11434/v1  Ollama（OpenAI 相容層，實測 200）
     ④ http://127.0.0.1:1234/v1        LM Studio（與 voice_refine 同源，本機兜底）

   對每個候選：GET {base}/models，timeout=5，有 api_key 就帶 Authorization: Bearer
     ├─ 200 且 data 非空 → 命中
     ├─ 401/403         → 記為 needs_key，**繼續試下一個**，但把這個狀態留在結果裡：
     │                     全鏈皆失敗時，reason 要明講「Gateway 需要 API key（401），
     │                     請到設定填入」——不可回「沒有可用模型」
     └─ 其他錯誤        → 記錄後試下一個
```

### B4.2 模型挑選

沿用並強化 `_refine_pick_model`（BT:3705）的規則：

1. 排除 `embed | ocr | vision | -vl | rerank`（既有規則，直接沿用）。
2. `model_prefer` 非空 → 優先取 **id 含該子字串**（大小寫不敏感）的第一個。
3. 否則取清單第一個。
4. `settings.harness.model` 不是 `"auto"` → 直接用它，跳過 1–3
   （但仍記錄「清單中不存在此 model」的警告到 log，不阻擋）。

### B4.3 快取策略

```python
_HARNESS_PROBE_TTL      = 300.0     # 成功結果快取 5 分鐘
_HARNESS_FAIL_BACKOFF   = (60, 120, 300, 600)   # 連續失敗第 n 次的重試間隔上限
```

- 規劃文件草案寫 60s；建議放寬到 **300s**：一次探測最多打 4 個端點 × 5s timeout，
  60s 太密，且端點清單是慢變化的。
- 失敗走**遞增退避**（60→120→300→600s 封頂），避免端點全掛時每分鐘打四次。
- 快取內容：`{"ok", "base_url", "model", "probed_at", "reason", "needs_key"}`。
- 「重新探測」按鈕與 `sfctl harness-status --refresh` 可強制清快取。

### B4.4 命中可見化（「入口明朗」的具體交付）

三個地方顯示**同一份** `harness_endpoint()` 結果：

1. 設定頁狀態列（B3.2）。
2. `sfctl harness-status` —— 新增子命令，走既有 `_rpc` 機制（sfctl.py:613）。
3. bridge log：每次探測後一行
   `[harness] endpoint=http://…:8000/v1 model=qwen3.6-35b-heretic probed=14:32:07`。

失敗時 reason 必須是人看得懂的一句話，例如
`Gateway 401（需要 API key）· vLLM 連線逾時 · Ollama 連線被拒 · LM Studio 未啟動`。

### B4.5 效能標註（探測）

| 項目 | 值 |
|---|---|
| **dirty-gate** | N/A（不掃 buffer、不碰 `screen.display`）。等價 gate：`level == "off"` 時**完全不執行**探測 |
| **節流** | 成功快取 300s；失敗退避 60→600s；只在「要真的呼叫模型」之前才 lazy 探測，**不做定期背景探測** |
| **最壞成本** | 對 `_flush_loop` = **0**（探測只發生在 harness worker thread）。單次探測 ≤ 4 × 5s timeout = 20s 牆鐘，但全在背景 thread，CPU 幾乎為 0（網路等待） |
| **perf 計時** | 不進 `_perf_*`（不在 flush loop）。以 `[harness] probe took=…ms` log 記錄 |

---

## B5. 設計 B-4：第一個落地場景

### B5.1 選定：**「卡住原因判讀」（`screen_triage`）**，而不是通用畫面判讀

我同意規劃文件選「畫面判讀」，但**必須收窄**，理由是：

`agent_status.py` 已經用 **transcript JSONL** 把 `working / done / decision / stuck`
判得又準又便宜（零網路、零 LLM）。讓 harness 再做一次通用 state 分類是重工，
而且會比現有機制更慢更貴更不準。

harness 真正該補的是 regex 打不過、transcript 也看不到的那一塊：
**「這個分頁卡住了，是為什麼？」** —— 畫面上的自訂對話框、非預期的錯誤畫面、
新版 CLI 改過的 UI、模型自己印出來的求助訊息。這正是規劃文件 C 節說的
「regex 一直被新 UI 打敗」。

### B5.2 觸發條件（**稀疏**是設計核心）

**只在既有機制已經判定異常時才呼叫**，harness 不主動巡邏：

```
觸發點 A：_warn_stalled(sid, age) 被呼叫，但 _detect_blocking_popup() 回 None
          → 現況是「靜默不通知」（BT:1442），正是最需要判讀的黑洞
觸發點 B：心跳（A4）連續 3 次發出且 state 都沒變化
觸發點 C：_detect_rate_limit 回 None 但畫面明顯停滯（保留給第二版）
```

再疊三道節流：per-slot cooldown 300s、全域 `max_calls_per_hour`、queue `maxsize=8`。

### B5.3 輸入

- 畫面尾端 **40 行**：`self._slot_display(slot)[-40:]`
  —— 走既有 `_feed_gen` display 快取，**不新增任何 render**。
- bridge log 尾端 **5 行**（只在觸發點 A）。
- 硬上限 **4000 字元**（超過從前面截，保留尾端）。
- 送出前用既有 `_INJECT_ANSI_RE` 去掉殘餘 ANSI。

⚠ 邊界（沿用規劃文件 C 節）：**只送畫面／log，絕不送使用者對話內容本身**，
也絕不讓 harness 進入回覆轉發路徑。

### B5.4 輸出 schema

```json
{
  "state": "waiting_input | blocked_dialog | rate_limited | crashed | working_long | unknown",
  "confidence": 0.0,
  "reason": "一句中文說明，≤60 字",
  "evidence_line": "畫面上最關鍵的那一行原文",
  "suggested_action": {
    "kind": "none | peek | wait | answer_prompt | restart_suggest",
    "detail": "≤40 字"
  }
}
```

解析與防呆：

| 規則 | 行為 |
|---|---|
| 請求帶 `response_format: {"type":"json_object"}` | vLLM / Ollama 皆支援；端點不支援就靠 prompt 約束 |
| 非 JSON | 取第一個 `{` 到最後一個 `}` 再試一次；仍失敗 → 丟棄 + 計入「schema 失敗」 |
| `state` 不在列舉內 | 整筆丟棄 |
| `confidence < 0.6` | 當作 `unknown`，**不通知** |
| `evidence_line` 不是畫面上真實存在的子字串 | 視為幻覺，降 `confidence` 到 0（丟棄）——這條很重要，是最便宜的反幻覺檢查 |
| L2 | 只顯示 `reason` + 建議，附 inline 確認按鈕 |
| L3 | `suggested_action.kind` 只允許 `none / peek / wait`；其餘一律降為「建議」 |

### B5.5 失敗降級

```
任何失敗（連不上／逾時／非 JSON／schema 不符／confidence 低）
 ├─ _blog("[harness] <sid> <失敗原因> took=…ms")
 ├─ 完全回到現有 regex 行為（觸發點 A 就是 _warn_stalled 原本的靜默／彈窗訊息）
 ├─ slot 進入 300s cooldown
 └─ 連續 3 次失敗 → 全域 5 分鐘 cooldown；連續 3 次 schema 不符 → 降級 observe + 通知
```

**harness 不得成為新的單點故障**：所有既有機制在 harness 完全不存在時的行為必須不變。
建議 RD 用一個 `HARNESS_KILL = os.environ.get("SF_HARNESS_OFF")` 環境變數做緊急總開關。

---

## B6. 設計 B-5：執行緒與效能

### B6.1 絕對規則

> **harness 呼叫是網路 I/O，永遠不得出現在 `_flush_loop` 的同步路徑上。**

`_flush_loop` 端**只允許**做一件事：`queue.put_nowait(job)`。

### B6.2 架構

```
_flush_loop (0.5s/2s)                 harness worker thread（新增，1 條）
     │                                        │
     │ slow_tick 已判定異常                    │  while active:
     │ ┌─ 三道 gate 都過 ─┐                    │    job = self._harness_q.get(timeout=1.0)
     └─┤ put_nowait(job)  ├───────────────────▶│    ├─ level = harness_level(); off → 丟棄
       └─ Full → 丟棄+log ┘                    │    ├─ ep = harness_endpoint()  (300s 快取)
                                               │    ├─ POST /chat/completions  timeout_s
       ▲                                       │    ├─ 解析 + 防呆（B5.4）
       │                                       │    └─ 依 level 決定：log / 通知 / 白名單動作
   queue.Queue(maxsize=8)  ← 滿了直接丟，不阻塞
```

- **一條** worker thread 就夠（呼叫稀疏、且不希望多路併發打爆 190）。
- 比照既有 `_dispatch_loop`（BT:3301）的 `get(timeout=1.0)` 形狀，`stop()` 時自然退出。
- 為什麼用 queue 而不是像 `_warn_stalled` 那樣直接 spawn thread：
  spawn 沒有背壓，8 個 slot 同時卡住就是 8 條併發 HTTP 打 190
  （190 記憶體已在 110/121GB 告急）。queue + 單 worker 天然序列化。

### B6.3 效能標註

| 項目 | 值 |
|---|---|
| **dirty-gate** | **是（間接但明確）**。觸發前提是 `slot.stall_warned` 由 False 轉 True，而該轉態走既有 slow_tick 且需 `last_write_ts > 0` ＋ `silence > STALL_SILENCE_MIN(10s)` ＋ `write_age > 15s`。harness **自身不新增任何 buffer／screen 掃描**；輸入取自既有 `_slot_display` 的 `_feed_gen` 快取 |
| **節流** | per-slot cooldown **300s** ＋ 全域 `max_calls_per_hour=60` ＋ queue `maxsize=8`（滿即丟）＋ 失敗退避 300s／全域 5min |
| **最壞成本** | **flush loop 端**：`queue.put_nowait` ≈ 2 µs，最壞每 slot 每 2s 一次 → 30 次/分 × 0.002 ms = **0.00006 ms/分/slot ≈ 0%**。**worker 端**：取 40 行走快取（最壞一次 render 0.9 ms）＋ 網路等待（不佔 CPU）＋ JSON 解析 < 0.1 ms；每 slot 每 300s 至多一次 → **0.0003% CPU/slot**。8 slots 全部異常的最壞情境合計 **< 0.01% CPU** |
| **perf 計時** | **是**。flush loop 端 `_perf_end("harness_gate", t0)`；worker 端 `_perf_end("harness_call", t0)`（同一 `_perf` dict，會進 60s 摘要）＋ 每次呼叫一行 `[harness] <sid> state=… conf=… took=…ms` |

對照回歸守則第 4 條（「失敗路徑要讓下次嘗試變便宜或變不必要」）：
本設計失敗時**同時**做到兩者——進 300s cooldown（變便宜）
且 `slot.stall_warned` 保持 True 不會重觸發（變不必要）。

---

## B7. 主題 B 風險

| 風險 | 影響 | 緩解 |
|---|---|---|
| 模型判讀出錯，L3 自動做了不該做的事 | 干擾 Howard 的活分頁 | 白名單窮舉（B2.2）＋ dispatch dict 不用 `getattr` ＋ L3 只放行唯讀／純顯示。**最壞情況只是側欄燈號閃錯色** |
| 190 記憶體告急（110/121GB），harness 呼叫壓垮 vLLM | 35B crash loop、單字查詞/RAG 全掛（有前科） | 單 worker 序列化 ＋ `max_calls_per_hour=60` ＋ 稀疏觸發（只在異常時）。建議第一版 `model_prefer` 填小模型（如 `gpt-oss:20b`）避免搶 35B 的 GPU |
| Gateway 之後接好了，探測鏈順序讓它被跳過 | 又回到「入口不明朗」 | Gateway 排第一順位；401 明確回報而非靜默跳過；狀態列常駐顯示實際命中 |
| `save_settings` 整包覆蓋，舊版 UI 送上來的 settings 會抹掉 `harness` 物件 | 設定莫名消失 | 在 `Api.save_settings`（main.py:2522）加一層「未出現的既知子物件保留」的合併，或至少對 `harness` 特判。這是既有的結構性弱點，順手補 |
| harness 判讀寫進 side panel，與 `agent_status` 的判定打架 | 燈號跳動 | 明確定義優先序：**transcript（`agent_status`）> 畫面 spinner > harness**。harness 只在前兩者皆為 `unknown/stuck` 時才有發言權 |
| 「主動程度」被誤解成「聰明程度」 | 期待落差 | UI 文案用行為描述（「通知我，我按了才做」），不要用「低/中/高」 |

## B8. 主題 B 實作步驟（給 RD）

**階段 1 — 端點探測（純讀取，零風險，先做這個讓入口可見）**
1. 新增 `harness.py`（新模組 → 需 `sfctl restart`）：
   `harness_level()`、`harness_endpoint(force=False)`、`harness_chat(messages, schema=None)`。
   HTTP 層以 `bridge_telegram._refine_transcript`（BT:3724）為藍本，補 `Authorization` header。
2. 探測鏈 B4.1 ＋ 模型挑選 B4.2（沿用 `_refine_pick_model` BT:3705 的過濾規則）＋ 快取 B4.3。
3. `sfctl harness-status [--refresh]`：`sfctl.py` 加 subparser（`:517` 那批旁）＋ main.py `_rpc` handler。
4. 單元測試 `tests_harness_probe.py`：mock `urlopen`，覆蓋 200／401／逾時／全掛四種鏈路，
   斷言 401 的 reason 明確提到「需要 API key」。
5. commit。

**階段 2 — 設定 UI**
6. `main.py:181` `DEFAULT_CONFIG["settings"]["harness"]` 預設物件。
7. `web/index.html`：B3.3 的第 2–6 項（下拉照 `:837` `#setting-lang` pattern）。
8. `Api.save_settings`（main.py:2522）加子物件保留合併（B7 風險項）。
9. **`sfctl restart`** 後實測：切四個層級、填 base_url／key、按「重新探測」、狀態列正確。
10. commit。

**階段 3 — worker 骨架（還不接場景）**
11. `TelegramBridge.__init__`（BT:819）加 `self._harness_q = _queue.Queue(maxsize=8)`。
12. `start()`（BT:1062）啟 `self._harness_thread = threading.Thread(target=self._harness_loop, daemon=True)`；
    `stop()`（BT:1103）比照既有 thread 收尾。
13. `_harness_loop`：`get(timeout=1.0)`、level 檢查、cooldown、額度計數、
    `_perf_end("harness_call", t0)`、`[harness]` log。
14. 白名單 dispatch dict（B2.2），**不用 `getattr`**。
15. `SF_HARNESS_OFF` 環境變數總開關。
16. commit。

**階段 4 — `screen_triage` 場景**
17. `_warn_stalled`（BT:1431）在 `if not popup_owner:`（BT:1441）分支，
    改為：`level != off` → `self._harness_q.put_nowait(...)`；否則維持現有靜默 log。
    ⚠ `_warn_stalled` 本身已是背景 thread，但仍用 queue（B6.2 的背壓理由）。
18. `_flush_loop` slow_tick 若要加觸發點 B（心跳連 3 次無變化），
    在心跳閘門旁 `put_nowait` ＋ `_perf_end("harness_gate", t0)`。
19. prompt ＋ schema 解析 ＋ B5.4 全部防呆（含 `evidence_line` 真實性檢查）。
20. 依 level 分流輸出：L1 只 log；L2 通知 ＋ inline 按鈕（callback 走既有
    `_handle_callback_query` BT:3834）；L3 白名單動作。
21. `tests_harness_triage.py`：mock 端點回各種壞輸出（非 JSON／state 亂填／
    confidence 低／`evidence_line` 幻覺），斷言全部安全降級且**不影響既有 stall 行為**。
22. commit。

---

# 給 RD 的實作順序

| 順序 | 項目 | 為什麼排這裡 | 產出 |
|---|---|---|---|
| **1** | A 階段 0：`[marker-miss]` 診斷 | 先量再改。整份 A 的根因排序建立在此之上 | 一行診斷 log |
| **2** | A 階段 1：P0-1 ～ P0-8 | **這是 Howard 痛點的根治**。tab 11 不回話是 bug 不是缺功能；先修 bug，再談通知 | `reload` 即可驗證 |
| **3** | A 階段 2：送達回執 | 小、獨立、立即有感；API 路徑已有先例 | 👀 / 🫡 狀態機 |
| **4** | A 階段 3：長回合心跳 | 依賴 `StatusTracker.last_result`（要動 main.py → `restart`） | 心跳 + `/quiet` |
| **5** | B 階段 1：端點探測 | 純讀取零風險，先讓「入口明朗」可見；與 A 無依賴，**可與 3/4 平行** | `sfctl harness-status` |
| **6** | A 階段 4：進行中預覽 | 建立在心跳之上，且需要 P0-3/P1-13 的去重集合先可靠 | 預覽段 |
| **7** | B 階段 2：設定 UI | 需 `restart`，與 A 階段 4 併一次重啟 | 下拉 + 狀態列 |
| **8** | B 階段 3–4：worker + `screen_triage` | 最後做。前面都穩了才引入網路 I/O 元件 | harness L1–L3 |
| **9** | A 階段 5：P1 各項 | 收尾，每項一次 commit | — |

**紀律**（來自 perf 文件「邊界」節，血淚教訓）：
每完成一個階段就 commit + push，不要留長時間未提交的工作樹（會被 auto-stash 蓋掉）。
`bridge_telegram.py` 的改動可 `sfctl reload` 快速迭代；
動到 `main.py` / `web/index.html` / 新模組 `harness.py` 一律 `sfctl restart`。
**純文件與純 bridge 修復不 bump 版號；使用者可見的新功能（回執、心跳、harness UI）要 bump + CHANGELOG。**

---

# 給 QA 的驗收清單

## Q-A1 送達回執

| # | 案例 | 預期 |
|---|---|---|
| 1 | 對 idle 分頁發一則普通訊息 | 使用者那則訊息 **1 秒內**出現 👀；turn 開始後變 🫡 |
| 2 | 對忙碌中分頁（`esc to interrupt` 在畫面上）發訊息 | 立刻 👀；8 秒後另收到文字「⏳ 已收到、排隊中」；真的送進去後變 🫡 |
| 3 | 對已死掉的 pane 發訊息 | reaction 清空 ＋ 收到文字警告；**不得**靜默 |
| 4 | 連發 3 則訊息 | 每則各自有自己的 reaction；不互相覆蓋 |
| 5 | 把 emoji 改成不合法值（如 ✅）跑一次 | 記 `[reaction] … failed`；連 3 次後收到一次性停用通知；**訊息本身仍正常送達與回覆** |
| 6 | busy guard 撐滿 120s | 收到「已強制送入，可能打斷上一回合」的警告 |

## Q-A2 靜默丟訊修復（**必須用重現腳本，不可只看程式碼**）

| # | 案例 | 預期 |
|---|---|---|
| 7 | 單元測試：buffer 內含 `>>> x\n<<<` ＋ 完整 `[[TG_REPLY_ab]]…[[/TG_REPLY_ab]]` | 能抽出 marker 內容（修前會抽到 `x`） |
| 8 | 單元測試：`pyte.HistoryScreen(80,5,history=10)` 餵 40 行後再餵新回覆 | 新回覆能被 `_extract_new_text` 抽到（修前抽不到） |
| 9 | 故障注入：mock `tg_api` 回 `{"ok":false,"description":"HTTP 429: retry after 3"}` | 重試一次；最終失敗時回覆**不在** `sent_responses`，且 `/fetch` 仍能取回 |
| 10 | 開一個沒有任何使用者 active 的 worker 分頁，讓它產生回覆 | log 出現 `no target chat`；之後把使用者切到該分頁 `/fetch`，**內容仍在** |
| 11 | 傳一則超過 20MB 的語音檔 | 收到明確失敗訊息（不得靜默） |
| 12 | 端到端重現 s87：開一個分頁跑「派背景 sub 然後等它」，從 TG 發訊息 | **在背景 sub 還沒跑完之前**就收到 agent 的 `[[TG_REPLY]]` 回覆 |

## Q-A3 心跳

| # | 案例 | 預期 |
|---|---|---|
| 13 | 一般 30 秒回合 | **完全不發心跳**（這是最重要的負向測試） |
| 14 | 20 分鐘背景任務 | 約在 3 / 8 / 15.5 分鐘各收到一則；內容含 state、在忙什麼、已等多久 |
| 15 | 心跳期間 agent 回覆了 | 心跳立刻停；`_hb_count` 歸零 |
| 16 | 心跳期間下 `/quiet` | 本 epoch 不再收到；下一則訊息後恢復 |
| 17 | 連續兩次 status 完全相同 | 第二則被 hash 去重跳過（但退避計數仍推進） |
| 18 | shell 分頁（沒有 transcript） | 心跳仍發得出來，只是少了 state 那行 |
| 19 | 15 分鐘後的「進行中預覽」 | 帶「非最終回覆」字樣；**該內容之後真的被 agent 回覆時仍會正常轉發**（驗證 S1） |

## Q-A4 效能回歸（**缺一不可**）

| # | 案例 | 預期 |
|---|---|---|
| 20 | 8 tabs 全 idle，`top -l 2 -pid <pid>` | 主進程 CPU **< 5%**（與改動前對比表） |
| 21 | 8 tabs、2–3 個持續輸出 | **< 25%** |
| 22 | 開 `perf_debug`，跑 30 分鐘取 60s 摘要 | 出現 `heartbeat_gate` / `preview_peek` / `harness_gate` / `harness_call` 四個 phase；`heartbeat_gate` 的 mean 應在 **µs 等級** |
| 23 | 刻意製造 3 個 stuck slot（marker 永不出現）跑 1 小時 | CPU 不爬升；`extract_marker` 的 total 應**低於**改動前（刪掉 `>>>` 搜尋的收益） |
| 24 | 乾淨 `sfctl restart` 後全功能複驗 | TG 收發、`[[SF:*]]` 燈號、選單偵測、board、auto-compact、stall、歷史去重全部照常 |

## Q-B harness

| # | 案例 | 預期 |
|---|---|---|
| 25 | `level=off` | `sfctl harness-status` 回「已關閉」；**不發生任何網路連線**（用 `lsof`/封包確認） |
| 26 | `level=observe`，製造一個 stuck 分頁 | 只有 `[harness]` log；**零通知**、側欄狀態不變 |
| 27 | `level=suggest` | 收到 TG 通知 ＋ inline 按鈕；不按就什麼都不發生 |
| 28 | `level=assist` | 白名單動作自動執行；試著讓模型回 `suggested_action.kind = "restart_suggest"` → **只顯示不執行** |
| 29 | 把 `base_url` 指到一個關閉的 port | 狀態列顯示失敗原因；**既有 stall 行為完全不變**；不 fallback 到別的端點 |
| 30 | `base_url` 留空、Gateway 沒 key | reason 明確含「需要 API key（401）」；最終命中 vLLM 或 Ollama 並顯示實際 URL + 模型名 |
| 31 | mock 模型回非 JSON／`state` 亂填／`evidence_line` 是畫面上沒有的句子 | 三種都安全丟棄；連 3 次 schema 失敗後自動降 `observe` 並通知 |
| 32 | 8 個 slot 同時 stuck | queue 最多 8 筆、單 worker 序列處理；**同時對 190 的併發連線數為 1** |
| 33 | 設 `max_calls_per_hour=2` 然後觸發 5 次 | 第 3 次起該小時降為 observe 並通知一次 |
| 34 | 跑 `SF_HARNESS_OFF=1` 啟動 | 不論設定為何，harness 完全不動 |

