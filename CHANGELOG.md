# Changelog

## v0.11.31 (2026-04-24)

### Fixes
- **SIGTRAP on launch under macOS 26 — shellframe died silently before the window appeared** — v0.11.30 added `setCollectionBehavior_()` on every `NSApp.windows()` entry inside `_on_loaded`, which pywebview fires on its event-dispatcher thread. macOS 26 (Tahoe) tightened the AppKit main-thread-only rule from "undefined behaviour" to hard `EXC_BREAKPOINT` / SIGTRAP, so any user on 26+ who upgraded to v0.11.30 hit an immediate crash with no Python traceback (ObjC-level abort bypasses `try/except` and `_write_crash_log`, so `~/.shellframe-crash.log` stayed empty — the silent-failure mode). Fixed by wrapping the `setCollectionBehavior_` loop in a block and dispatching it to `NSOperationQueue.mainQueue()` so mutation happens on the main thread regardless of which thread `_on_loaded` fires on.

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
