# Changelog

> Format and language rules: [`docs/changelog-guide.md`](docs/changelog-guide.md).
> Enforced by `tests_changelog_format.py` (runs in `./run_tests.sh`).
>
> 撰寫規範見 [`docs/changelog-guide.md`](docs/changelog-guide.md)，
> 由 `tests_changelog_format.py` 強制檢查。

## v0.34.3 (2026-09-05)

### Fixes

- **Capturing an account now refuses to save mismatched credentials.** A tab's
  `~/.claude.json` account metadata (email/org) and the Keychain OAuth token can
  drift apart after account switching — the account reads as one identity while
  the token belongs to another. Capturing that state stored a profile whose
  token was a *different* account's, which is why two profiles could both report
  the same person's usage. Account discovery now cross-checks the token's
  `rateLimitTier` against the tiers recorded in `~/.claude.json`; when they
  belong to different accounts, "重新整理已登入" blocks with an explanation
  (log in to the right account and capture again) instead of saving, and startup
  discovery skips the bad profile rather than recording it. Missing tier fields
  fail open so normal logins are never blocked. Successful capture now also
  reports which account it saved. Regression tests in `tests_accounts.py`.

  **抓取帳號時會擋下對不上的憑證。** 分頁的 `~/.claude.json` 帳號資料（email/org）
  與 Keychain 的 OAuth token 在切帳號後可能會對不上——帳號看起來是一個人、token
  其實是另一個帳號的。把這種狀態抓下來，存進去的 profile 就握著別人的 token，
  這正是「兩個 profile 都顯示同一個人用量」的原因。帳號探測現在會把 token 的
  `rateLimitTier` 和 `~/.claude.json` 記錄的 tier 交叉比對；判定是不同帳號時，
  「重新整理已登入」會擋下並說明（請登入正確帳號後再抓一次），不再存錯；開機探測
  也會跳過這種壞 profile 不記錄。缺 tier 欄位時採 fail-open，不會擋到正常登入。
  抓取成功也會回報存下的是哪個帳號。回歸測試在 `tests_accounts.py`。

## v0.34.2 (2026-09-05)

### Fixes

- **Switching a Claude tab's account now keeps the conversation — it resumes
  the same session instead of starting blank.** Each account is a separate
  `CLAUDE_CONFIG_DIR`, so the running conversation's transcript lives under the
  *old* account's config home; relaunching under the new account simply
  couldn't see it, and the tab came back empty. The switch now copies the
  current session's transcript (`<uuid>.jsonl`) into the new account's
  `projects/<same cwd slug>/` and relaunches with `--resume <uuid>`, so the
  same history continues under the new account — the point being to keep one
  conversation going across an account/quota change. Same-account re-pins and
  non-Claude tabs are unaffected. Regression tests in `tests_accounts.py`.

  **切換 Claude 分頁的帳號現在會保留對話——用 --resume 接回同一段，不再空白重開。**
  每個帳號是獨立的 `CLAUDE_CONFIG_DIR`，正在進行的對話 transcript 存在**舊**帳號
  的 config 家目錄下，用新帳號重開根本看不到，分頁就空了。現在切換會把當前
  session 的 transcript（`<uuid>.jsonl`）複製進新帳號的 `projects/<相同 cwd slug>/`，
  並以 `--resume <uuid>` 重開，同一段歷史就在新帳號底下續接——目的就是讓一段對話
  跨帳號／配額切換還能繼續。同帳號重釘與非 Claude 分頁不受影響。回歸測試在
  `tests_accounts.py`。

## v0.34.1 (2026-09-05)

### Fixes

- **Switching a tab's Claude/Codex account now actually takes effect — no more
  forced re-login.** Account switching relaunches the tab's process with the
  account pinned through env vars (`CLAUDE_CONFIG_DIR` /
  `CLAUDE_CODE_OAUTH_TOKEN`, or `CODEX_HOME`). Those were passed only through
  the subprocess environment of the `tmux new-session` call — but tmux spawns
  the new pane from the **server's** environment, so whenever a tmux server was
  already running (i.e. any time a second tab exists) the account vars were
  silently dropped and the relaunched tab came up on the default/previous
  account, looking logged-out. Only `SF_SID` survived, because it was the one
  var passed the correct way (`-e KEY=VAL`). The account overrides are now
  passed the same way, so the switched tab launches on the right, already
  authenticated profile. Regression test in `tests_accounts.py` captures the
  `tmux new-session` argv and asserts the account env is present.

  **切換分頁的 Claude／Codex 帳號現在真的會生效——不再被迫重新 /login。**
  切帳號是把分頁行程重啟、用環境變數釘住帳號（`CLAUDE_CONFIG_DIR`／
  `CLAUDE_CODE_OAUTH_TOKEN`，或 `CODEX_HOME`）。這些變數之前只透過
  `tmux new-session` 的 subprocess 環境傳——但 tmux 是從**server**的環境
  spawn 新 pane，所以只要 tmux server 已在跑（有第二個分頁時必然），帳號變數
  就被默默丟掉，重啟的分頁用預設／前一個帳號起來，看起來像沒登入。只有
  `SF_SID` 活著，因為它是唯一用對方法（`-e KEY=VAL`）傳的。現在帳號 override
  也照這個方法傳，切換的分頁就會用正確、已登入的 profile 啟動。
  `tests_accounts.py` 加了回歸測試：攔 `tmux new-session` 的 argv、確認帳號
  env 有在裡面。

  切帳號仍是把該分頁重啟（換帳號＝換一組憑證／對話記錄），所以那一刻畫面上的
  對話會重來——這是換帳號的本質，不是這次修的登入問題。

## v0.34.0 (2026-09-05)

### Added

- **OpenCode joins the default AI preset list.** Every other supported CLI
  (Claude, Codex, Antigravity, Pi) has shown up as a ready-to-open preset on a
  brand-new install since the preset-migration mechanism was built, but
  OpenCode was never added to that list — a fresh computer had no OpenCode
  button to open, and the only way to reach it was the Accounts panel's
  install prompt. Added to `_DEFAULT_AI_PRESETS`; the existing per-preset
  migration (`_default_ai_presets_offered`) retrofits it into every already-
  running install on next launch, same as a new one. Bare `opencode` command —
  it manages its own model provider and login, so no extra flags are needed;
  a computer without the binary yet gets the existing "not installed → install
  here" gate the same as any other provider.

  **OpenCode 加入預設 AI 清單。** 其他每個支援的 CLI（Claude、Codex、
  Antigravity、Pi）自從 preset 遷移機制做好之後，全新安裝就會直接看到可開的
  預設按鈕，唯獨 OpenCode 從沒被加進這份清單——全新電腦沒有 OpenCode 按鈕可
  點，只能從帳號面板的安裝提示找到它。已加進 `_DEFAULT_AI_PRESETS`；既有的
  per-preset 遷移機制（`_default_ai_presets_offered`）下次啟動就會把它補進
  所有已經在跑的安裝，跟全新安裝一樣。指令用裸的 `opencode`——它自己管模型
  provider 與登入，不需要額外旗標；本機還沒裝執行檔時走跟其他 provider 一樣
  的「未安裝→就地安裝」引導。

## v0.33.0 (2026-09-05)

### Added

- **Pairing shows a QR code, and the entry point leads with the phone app.**
  Generating a pairing code only ever produced a text code — pairing a phone
  meant typing the host address, port and code by hand, and the entry chooser
  read as computer-to-computer only ("配對另一台 ShellFrame") with no mention
  of a phone app. `pairing_begin()` now also returns a `pair_url`
  (`shellframe://pair?d=<base64url JSON>` carrying the host addresses, port,
  code and binding mode), which the pairing modal draws as a QR code next to
  the text code. The "＋ 配對" entry now leads with a "📱 手機／平板 App" button
  that jumps straight to the QR, skipping the duplex/master/slave binding
  picker — a phone app always connects as a full peer, so that choice was
  never meaningful for it.

  **配對現在會出現 QR code，入口也把手機 App 放在最前面。** 產生配對碼過去只給
  一段純文字，手機端得手動輸入位址、port、碼；「＋ 配對」的選單看起來也只是
  電腦對電腦（「配對另一台 ShellFrame」），完全沒提到手機 App。`pairing_begin()`
  現在多回傳一個 `pair_url`（`shellframe://pair?d=<base64url JSON>`，帶位址、
  port、碼、綁定模式），配對視窗會把它畫成 QR code、與文字碼並列顯示。「＋ 配對」
  入口現在把「📱 手機／平板 App」放在最前面，點了直接跳到 QR，略過單向／雙向
  綁定選單——手機 App 一律以完整 peer 身分連線，那個選擇對它從來就沒有意義。

## v0.32.8 (2026-09-05)

### Added

- **`/delay` — schedule a prompt to send into a tab later (Telegram).**
  `/delay 30m <prompt>` queues `<prompt>` and injects it into the active tab
  after the delay; handy for waiting out a usage-quota reset. Duration accepts
  `30m` / `2h` / `90s` / `1h30m`, or a bare number as minutes. `/delay` (or
  `/delay list`) shows what's queued with time remaining and an id; `/delay
  cancel <id>` takes one back before it fires. The queue is persisted to
  `~/.local/state/shellframe/tg_delays.json` and driven by a scheduler in the
  app process, so pending sends survive a restart; when one fires you get a
  Telegram confirmation (or a warning if the target tab is gone). Also fixed a
  latent issue where `/link` was forwarded to the tab instead of handled by the
  bridge.

  **`/delay` — 排程晚點把 prompt 送進分頁（Telegram）。** `/delay 30m <prompt>`
  會把 `<prompt>` 排隊、延遲後注入當前 active 分頁；適合等用量配額重置再送。
  時間吃 `30m`／`2h`／`90s`／`1h30m`，或裸數字當分鐘。`/delay`（或 `/delay list`）
  列出排程中的項目、剩餘時間與 id；`/delay cancel <id>` 可在送出前收回。佇列存到
  `~/.local/state/shellframe/tg_delays.json`、由 app 進程內的排程器驅動，restart
  也不會遺失；到點送出會回 Telegram 確認（分頁已不在則回警告）。順帶修掉
  `/link` 之前被當一般指令轉進分頁、而非由 bridge 處理的問題。

## v0.32.7 (2026-09-05)

### Added

- **OpenCode local-Spark preset self-provisions on first run.** New launcher
  `sf-opencode-spark`: if `~/.config/opencode/opencode.json` has no Spark
  provider yet, it writes one (OpenAI-compatible, tunnel 11439, key from
  `~/.codex/spark.env`) before launching `opencode --model spark/spark-main`.
  So after installing the opencode binary the preset works with no manual
  provider editing. Registered as an opencode binary for detection, and the
  install note points at it. (Install of the binary itself was already wired
  via the provider registry.)

  **OpenCode 地端 Spark preset 首次執行自我佈建。** 新增啟動器
  `sf-opencode-spark`：若 `~/.config/opencode/opencode.json` 還沒有 Spark
  provider，就先寫入（OpenAI 相容、tunnel 11439、key 取自 `~/.codex/spark.env`）
  再啟動 `opencode --model spark/spark-main`。裝好 opencode 二進位後直接開這個
  preset 就能通，不必手動編 provider 設定。已註冊為 opencode 的偵測 binary、
  安裝說明也指向它。（二進位本身的安裝原本就由 provider registry 接好。）

### Changes

- **Tidied the New-Session presets and removed a duplicate.** The bare "Pi"
  preset (`pi`) was redundant with "Pi-Spark" — on this machine pi only has the
  Spark provider, so both launched the same local backend. Dropped "Pi", and
  renamed the local group for clarity: "Pi (Spark)", "SparkAgent (Spark)",
  "OpenCode (Spark)". Cloud presets (Claude Code, Codex) and Bash unchanged.
  The old config was backed up next to config.json.

  **整理新增 session 的 preset、移除重複項。** 裸的「Pi」preset（`pi`）與
  「Pi-Spark」重複——這台 pi 只設了 Spark provider，兩者其實同一個地端後端。
  已移除「Pi」，並把地端群組改成一致命名：「Pi (Spark)」「SparkAgent (Spark)」
  「OpenCode (Spark)」。雲端 preset（Claude Code、Codex）與 Bash 不變。舊設定已
  備份在 config.json 旁。

## v0.32.6 (2026-09-05)

### Fixes

- **Unpairing a peer while viewing one of its remote sessions no longer leaves
  a zombie poller.** `confirmUnpair` didn't stop the stream or dispose the
  peer's open remote panes, so the 4 Hz stream loop kept hitting a now-unknown
  peer forever. Unpair now disposes every `rmt:` pane for that peer, stops the
  stream, closes the message/file panel if it was on that peer, and drops its
  sidebar-collapse state. The remote stream loop also backs off exponentially
  (250 ms → 3 s) on errors instead of hammering at 4 Hz, and a successful pair
  now clears the pairing-code countdown instead of firing a late cancel.

  **在檢視某台 peer 的遠端 session 時把它斷開配對，不再殘留殭屍輪詢。**
  `confirmUnpair` 之前沒停串流、也沒收掉該 peer 開著的遠端 pane，4Hz 串流迴圈
  會一直打一個已不存在的 peer。現在斷開會 dispose 該 peer 所有 `rmt:` pane、
  停串流、若訊息／檔案面板停在那台就一併收起、並清掉側欄收合狀態。遠端串流迴圈
  出錯時改成指數退避（250ms→3s）不再固定 4Hz 空打；配對成功也會清掉配對碼倒數，
  不再送出遲來的 cancel。

### Internal

- **Dead-code sweep of the Frame Link feature.** Removed the superseded
  bottom-panel remote viewer (`openRemoteView`) and its now-orphaned peek loop
  (`linkPeekTimer` / `stopLinkPeek`), the no-op `renderLinkSections` and its
  call sites, an unused `linkStatusTimer`, the duplicate lock-free
  `_stream_open` in frame_link.py, and the dead CSS left from the old tab-bar
  🔗 button and two-column panel (`#btn-link`, `#link-side`, `.link-sec-head`,
  `#link-remote-screen`, …). Remote panes now dispose their ResizeObserver on
  close, the stream buffer map is swept for expired entries in the poll loop,
  and `unpair` clears the peer's saved poll cursor. No behaviour change.

  **Frame Link 死碼清掃。** 移除被取代的底部面板遠端檢視（`openRemoteView`）與其
  孤兒 peek 迴圈（`linkPeekTimer`／`stopLinkPeek`）、no-op 的 `renderLinkSections`
  及呼叫點、未用的 `linkStatusTimer`、frame_link.py 裡重複的無鎖 `_stream_open`，
  以及舊 tab-bar 🔗 鈕與雙欄面板殘留的死 CSS（`#btn-link`、`#link-side`、
  `.link-sec-head`、`#link-remote-screen` 等）。遠端 pane 關閉時會 disconnect
  ResizeObserver、串流 buffer map 在 poll loop 週期清除過期項、`unpair` 也清掉該
  peer 的輪詢 cursor。行為不變。

## v0.32.5 (2026-09-05)

### Fixes

- **Remote alt-screen apps (Claude / Codex / opencode) now paint correctly on
  attach.** The first frame of a remote pane came from the cleaned scrollback
  reconstruction, which is wrong for an alternate-screen TUI, so the view was
  garbled until the next redraw. On attach the peer now returns a real snapshot
  of the current screen via `tmux capture-pane -e` (ANSI intact) prefixed with
  a clear+home, and the client paints that before streaming increments. Same
  path is reused after a resize, so a reflow repaints cleanly.

  **遠端 alt-screen app（Claude／Codex／opencode）一開就畫得對了。** 遠端 pane
  的第一畫面本來是拿 cleaned scrollback 重建版，對 alt-screen TUI 是錯的，畫面
  會破到下次重繪才恢復。現在 attach 時對方用 `tmux capture-pane -e`（保留 ANSI）
  回傳當前畫面快照、前置 clear+home，client 先畫這個再串增量。resize 後也走同一
  條路，reflow 會乾淨重畫。

- **Screenshots (and other images) can be pasted into a remote session.**
  Pasting into a remote pane did nothing useful before — the image was saved to
  a local temp path the peer can't read. The image bytes are now sent to the
  peer (`/link/paste`), saved there, and the peer injects the path with the
  same bracketed-paste escape a local paste uses, so the remote Claude/Codex
  picks it up as `[image #N]`. Arbitrary (non-image) files still go through the
  sidebar's 📁 file transfer.

  **截圖（與其他圖片）可以貼進遠端 session 了。** 之前貼進遠端 pane 沒作用——
  圖片被存到對方讀不到的本機暫存路徑。現在圖片位元組會送到對方（`/link/paste`）
  落地，再由對方用跟本機貼圖相同的 bracketed-paste escape 注入路徑，遠端的
  Claude/Codex 就會辨識成 `[image #N]`。一般（非圖片）檔案仍走側欄的 📁 檔案傳送。

- **Author name restored in the About panel and license.** A privacy scrub had
  mangled the author line into an impersonal placeholder; the About panel and
  the MIT copyright line now show the correct author name and @h2ocloud handle.

  **關於面板與授權的作者名修正。** 一次隱私替換把作者行洗成不具名的佔位字串，
  現在關於面板與 MIT 版權行都顯示正確的作者名與 @h2ocloud handle。

## v0.32.4 (2026-09-05)

### Changes

- **Remote panes now fill the window, and remote sessions can be created and
  closed from the UI like local ones.** A remote pane was locked to the peer's
  reported terminal size and skipped the fit addon, so it only painted the top
  portion of a taller window and alt-screen apps wrapped wrong. A remote pane
  now fits the window like any local tab and pushes its own cols/rows to the
  peer (`/link/resize` → the peer's PTY reflows), on open and on every resize.
  The sidebar peer section gained ＋ 新增 session (opens Claude / Codex / bash
  or a custom command on the peer) and a ✕ on each remote session that closes
  it on the peer after a confirm — mirroring the local sidebar. The tab-bar
  copy of a remote tab still just detaches the view. New `/link/resize`,
  `/link/new`, `/link/close` endpoints (control-gated) with cases in
  `tests_frame_link.py`.

  **遠端 pane 現在會撐滿視窗，遠端 session 也能像本機一樣新增／關閉。** 之前遠端
  pane 被鎖成對方回報的終端尺寸又不跑 fit，較高的視窗只畫得到上半部、alt-screen
  app 還會換行錯位。現在遠端 pane 跟本機分頁一樣 fit 撐滿，並把自己的 cols/rows
  推給對方（`/link/resize` → 對方 PTY 跟著 reflow），開啟時與每次縮放都會同步。
  側欄 peer 區多了「＋ 新增 session」（在對方開 Claude／Codex／bash 或自訂指令）
  與每個遠端 session 的 ✕（確認後關閉對方那個 session），跟本機側欄一致；tab bar
  上的遠端分頁 ✕ 仍只是收掉檢視。新增 `/link/resize`、`/link/new`、`/link/close`
  端點（受主從權限管制），`tests_frame_link.py` 補上案例。

- **Removed the 🔗 button from the tab bar.** Pairing and all peer actions live
  on the sidebar divider now, so the top button was redundant; taking it out
  keeps the tab bar clean. New-message / new-file attention now briefly
  highlights the peer's row in the sidebar instead of pulsing the old button.

  **移除 tab bar 上的 🔗 按鈕。** 配對與所有 peer 操作都在側欄隔離線了，頂部那顆
  是多餘的，拿掉讓 tab bar 更乾淨。有新訊息／新檔案時，改成短暫高亮側欄該 peer
  那一列，取代原本閃爍那顆按鈕。

## v0.32.3 (2026-09-05)

### Fixes

- **Clicking a remote session no longer bounces back to a local tab.** The
  1.5 s `syncSessionsFromBackend` reconciler treated any tab missing from the
  local backend's session list as "closed elsewhere" and disposed it — but a
  Frame Link remote pane (`rmt:<peer>:<sid>`) is by design never in the local
  list, so on every tick its pane was destroyed, `activeId` was reset to null,
  and the view immediately snapped back to whichever local tab came first. You
  could open a remote session but never stay on it. The reconciler now skips
  remote sids entirely, and a remote pane only goes away when you close it or
  the peer link drops.

  **點遠端 session 不再彈回本機分頁。** 每 1.5 秒的 `syncSessionsFromBackend`
  會把「不在本機後端 session 清單裡」的分頁當成「在別處被關掉」清除——但 Frame
  Link 的遠端 pane（`rmt:<peer>:<sid>`）本來就不在本機清單，於是每一輪都被砍、
  `activeId` 歸零、畫面彈回本機分頁。現在這個對帳流程完全跳過遠端 sid。

## v0.32.2 (2026-09-05)

### Fixes

- **Removed the duplicate remote-session tree; the bottom panel no longer
  shadows the sidebar.** After sessions became sidebar-first, the bottom split
  panel still rendered its own copy of every peer and its sessions, and those
  copies were not clickable — two lists of the same thing, one of them dead.
  The bottom panel is now content-only: it carries a message thread or a file
  transfer view for one peer, opened from that peer's 💬 / 📁 row in the
  sidebar, and closes with an ✕. The peer/session tree lives in exactly one
  place (the sidebar). The 🔗 tab-bar button now opens the sidebar and expands
  the Frame Link zone (and starts pairing when there are no peers yet) instead
  of toggling the panel.

  **移除重複的遠端 session 樹；底部面板不再和側欄疊影。** session 改成側欄為主
  之後，底部分割面板還是各自畫了一份 peer 與其 session，而且那份點不動——同一
  份東西兩個清單、其中一個還是死的。底部面板現在只放內容：某台 peer 的訊息對話
  或檔案傳輸，從側欄該 peer 的 💬／📁 列點開、用 ✕ 收起。peer／session 樹只留
  側欄一處。tab bar 的 🔗 改成打開側欄並展開 Frame Link 區（沒有 peer 時直接
  進配對），不再開那個面板。

- **Remote panes match the peer's terminal size, so remote TUIs render
  correctly.** A remote Claude/Codex tab draws to the alternate screen at the
  peer's cols×rows; showing that stream in a pane fitted to this window's size
  garbled the layout. `list` now reports each session's cols/rows, and a remote
  pane locks its xterm to those dimensions instead of running the fit addon.

  **遠端 pane 對齊對方的終端尺寸，遠端 TUI 才畫得對。** 遠端的 Claude/Codex
  分頁是照對方的 cols×rows 畫 alt-screen 的，用本機視窗尺寸 fit 過的 pane 去顯示
  那串輸出會整個亂掉。`list` 現在回報每個 session 的 cols/rows，遠端 pane 把
  xterm 鎖成那個尺寸、不跑 fit addon。

## v0.32.1 (2026-09-05)

### Changes

- **Frame Link is now seamless: a remote session opens like any local tab.**
  The first cut put remote viewing in a separate bottom split panel, which read
  as a different mode of operation. Now the pairing entry lives on the sidebar
  divider (＋ 配對), the divider and everything below it is the "other
  ShellFrames" zone, and clicking a peer's session opens a real xterm in the
  main terminal area — it streams the remote PTY's raw output (a new
  `/link/stream` incremental channel, only buffering tabs someone is actually
  watching) and sends keystrokes straight to the remote PTY (`/link/input`,
  raw bytes: arrows, Ctrl-C, Enter pass through). Remote tabs also appear in the
  top tab bar with a 🌐 prefix; closing one detaches the view without killing
  the remote session. Regression cases for stream attach/increment and raw
  input added to `tests_frame_link.py`.

  **Frame Link 改成無縫：遠端 session 就像本機分頁一樣點開。** 第一版把遠端檢視
  放在獨立的下方分割面板，操作感是「另一種模式」。現在配對入口移到側欄隔離線
  （＋ 配對），隔離線以下整區就是「其他 ShellFrame」，點對方的 session 會在主
  終端區開一個真正的 xterm——串流對方 PTY 的原始輸出（新增 `/link/stream`
  增量通道，只緩衝有人在看的分頁）、鍵盤直送對方 PTY（`/link/input`，原始位元組：
  方向鍵、Ctrl-C、Enter 照原樣）。遠端分頁也會在上方 tab bar 以 🌐 露出；關掉
  只是收掉檢視、不會殺對方的 session。串流 attach／增量與 raw input 的回歸案例
  加進 `tests_frame_link.py`。

- **Pairing can be one-way or two-way; both sides always see each other.**
  When generating a code you pick 雙向 (either side can drive the other),
  這台當主 (only this machine drives the peer), or 這台當從 (only the peer
  drives this machine). The mode is bound into the host's pairing proof, so a
  middleman can't flip it. After pairing both machines list each other; a
  master/slave badge shows the direction, and the controlled side is refused at
  `/link/info`, `/link/peek`, `/link/stream`, `/link/send` and `/link/input`.
  Messages and file transfer stay bidirectional regardless of mode. Covered by
  new directional-pairing cases in `tests_frame_link.py`.

  **配對可單向或雙向，兩邊都會看到彼此。** 產碼時選 🔄 雙向（互相能操作）、
  ➡️ 這台當主（只有這台能操作對方）、⬅️ 這台當從（只有對方能操作這台）。模式
  綁進 host 的配對 proof，中間人改不了。配對後兩台互相列出對方；主／從徽章標
  方向，受控端在 `/link/info`、`/link/peek`、`/link/stream`、`/link/send`、
  `/link/input` 一律被拒。訊息與檔案互傳不受模式限制。回歸案例已加進
  `tests_frame_link.py`。

- **Disabling a tab's Telegram bridge no longer moves it to another section.**
  Clicking the chip on the active TG tab dims that row in place (and the dim
  chip re-enables on click); the sidebar divider and the area below it are now
  reserved for other machines' sessions, so a local tab never lands there. The
  old "drag a row under the divider to disable" gesture is gone — drag is
  reorder-only now.

  **停用分頁的 Telegram 橋接不再把它搬到別區。** 點 active 分頁的 chip 會讓那列
  原位變暗（暗 chip 點一下重新啟用）；側欄隔離線與其下已改留給其他電腦的
  session，本機分頁不會再掉到那裡。舊的「拖到隔離線下停用」手勢移除——拖曳現在
  只做排序。

## v0.32.0 (2026-09-05)

### Added

- **Frame Link — pair two ShellFrame instances across machines.** A 🔗 button
  in the tab-bar gap (dim = no peer connected, lit = at least one reachable)
  opens a resizable bottom split panel. Pairing uses a short-lived one-time
  code (10 chars, 120 s, max 5 bad attempts): the code never travels in
  cleartext — both sides exchange HMAC proofs and independently derive a
  256-bit long-term secret. After pairing you can browse the peer's tabs with
  a live screen view, inject prompts into its tabs, exchange chat messages,
  and transfer files (received files land in `~/Downloads/ShellFrame/<peer>/`).
  Every request is HMAC-SHA256 signed (timestamp + nonce against replay,
  responses signed too); traffic is not encrypted — see
  [`docs/frame-link.md`](docs/frame-link.md) for the security model. Works
  over the public internet with only ONE reachable side: the unreachable peer
  polls a signed outbox, so messages, tab injections and file offers queue up
  and get pulled through NAT. Local and each remote frame render as separately
  collapsible, colour-coded sections; each peer row has an ✕ with a confirm
  popup to unpair, and an ✎ to fix the address when the peer's IP drifts.
  End-to-end regression: `tests_frame_link.py` (two instances in one process:
  pairing, replay/tamper rejection, remote ops, direct + store-and-forward
  message/file paths).

  **Frame Link — 跨機配對兩台 ShellFrame。** tab bar 分頁間隔多了一顆 🔗
  （暗＝沒有連上的 peer、亮＝至少一台可達），點開可拖高的下半部分割面板。
  配對用短效一次性配對碼（10 字元、120 秒、連錯 5 次作廢）：配對碼不走網路
  明文——雙方互出 HMAC proof、各自導出 256-bit 長期金鑰。配對後可以看對方
  分頁清單與即時畫面、把指令注入對方分頁、互傳訊息與檔案（收到的檔存
  `~/Downloads/ShellFrame/<peer 名>/`）。所有請求都有 HMAC-SHA256 簽章
  （timestamp＋nonce 防重放、回應也簽章）；流量未加密——安全模型見
  [`docs/frame-link.md`](docs/frame-link.md)。跨公網只要**一邊可達**就能全雙工：
  不可達的那台輪詢帶簽章的 outbox，訊息／分頁指令／檔案 offer 會排隊被拉走、
  穿過 NAT。本機與每台遠端各是可收合的色條區塊，一眼可分；peer 列右邊的 ✕
  斷開配對（跳確認 popup）、✎ 在對方 IP 漂移時直接改位址免重配。端到端回歸：
  `tests_frame_link.py`（同 process 兩個 instance：配對、重放／竄改拒絕、
  遠端操作、直連與 store-and-forward 訊息／檔案）。

- **Pair over Telegram — `/link` command.** `/link pair` opens a pairing
  window and replies with the code + addresses; `/link join <host[:port]>
  <code>` pairs from the other machine's bot; `/link` shows listener state and
  peer reachability; `/link unpair <name>` disconnects. Both machines can be
  paired remotely without touching either desktop.

  **TG 遠端配對——`/link` 指令。** `/link pair` 開配對窗口並回覆配對碼與位址；
  在另一台的 bot 下 `/link join <host[:port]> <碼>` 完成配對；`/link` 看
  listener 狀態與 peers 可達性；`/link unpair <名稱>` 斷開。人在外面
  兩台桌機都不用碰就能完成配對。

### Changes

- **Clicking the active tab's TG chip now disables that tab's Telegram
  bridge.** Turning a tab's TG connection off used to require dragging its
  sidebar row down into the "not bridged" section — the chip itself only
  switched the active session. Now the chip on the already-active TG tab acts
  as a one-click disable (tooltip says so); dragging the row back up
  re-enables, unchanged. Covered by the existing bridge-toggle path
  (`set_session_bridge`).

  **已是 TG active 的分頁再點一下 TG chip＝直接停用該分頁的 TG 連線。** 以前
  要把側欄那列拖到「不由 ShellFrame 橋接」區才能關，chip 本身只能切換 active。
  現在對已 active 的分頁點 chip 就是一鍵停用（tooltip 有註明）；拖回上區恢復，
  行為不變。走既有的 `set_session_bridge` 路徑。

## v0.31.4 (2026-09-05)

### Fixes

- **Table cells no longer show their markdown markers in scroll-up history.**
  A cell written as `**Multi-AZ**` came through with the asterisks intact, and
  because column widths are measured from the cell text, those four extra
  columns also skewed the whole table away from the width the live view draws.
  Inline markdown is now rendered inside cells the same way it is in ordinary
  text, and the width is measured after the markers are removed. Found by
  rendering a real tab's conversation and diffing it against its live screen,
  which now matches line for line. Case added to `tests_overlay_skin.py`.

  **上滑歷史的表格儲存格不會再露出 markdown 標記。** 寫成 `**Multi-AZ**` 的
  儲存格連星號一起顯示出來；而欄寬是照儲存格文字量的，那多出來的四欄還會讓整張
  表跟活畫面畫出來的寬度對不上。現在儲存格裡的行內 markdown 比照一般內文渲染，
  寬度也在拿掉標記之後才量。這是把真實分頁的對話渲染出來、跟它的活畫面逐行比對時
  抓到的，現在逐行完全相同。`tests_overlay_skin.py` 補一個案例。

## v0.31.3 (2026-09-05)

### Fixes

- **Scroll-up history in opencode tabs now looks like the tab it came from.**
  The overlay opened, but it read as a different application: markdown tables
  came through as raw pipe characters, horizontal rules were a fixed 40 columns
  wide regardless of the pane, bullets were rewritten to a different glyph than
  the one on screen, and the whole conversation sat flush against the left edge
  while the live view indents it. The renderer was one generic markdown-to-ANSI
  approximation applied to every CLI, and an approximation shared by two
  differently-styled TUIs matches neither.

  Layout and palette are now per-CLI, and the opencode values were measured off
  the running application rather than guessed: content indents five columns with
  a two-column right margin, bullets keep their original marker, tables are
  drawn as boxes stretched to the available width, rules match the table width,
  text wraps after punctuation rather than after any wide character, and the
  colours are the app's own 24-bit values. Column widths follow the formula
  recovered from two live tables — natural content width, then the leftover
  space split evenly with the remainder handed out left to right — which
  reproduces both measured cases exactly. Rendering the whole conversation and
  diffing it against the live screen now comes out identical line for line over
  the overlapping region.

  The pane's column count is passed in from the front end, since sizing tables
  and rules the way a TUI does is impossible without knowing how wide the pane
  is. Tabs on the default skin are unchanged. Regression tests:
  `tests_overlay_skin.py`, which pins the measured numbers.

  **opencode 分頁的上滑歷史，現在看起來就是那個分頁。** overlay 打得開了，
  但讀起來像另一個應用程式：markdown 表格原樣吐出管線字元、水平線不管分頁多寬
  都固定 40 欄、列點被換成跟畫面上不同的符號、整段對話貼齊左緣而活畫面是有縮排的。
  問題在於渲染器是一套通用的 markdown→ANSI 近似值、套用在所有 CLI 上，而兩個
  風格不同的 TUI 共用一套近似值的結果是兩邊都不像。

  排版與配色改成逐一 CLI 各自一套，opencode 的值是從執行中的應用程式量出來的，
  不是猜的：內容縮排五欄、右緣留兩欄、列點保留原本的符號、表格畫成方框並撐滿
  可用寬度、水平線與表格同寬、文字斷在標點之後而不是任意全形字之後，顏色直接用
  它自己的 24-bit 值。欄寬公式是從兩張實機表格反推出來的——先取內容自然寬，再把
  剩餘寬度平分、餘數由左往右各加一——兩組實測都完全重現。把整段對話渲染出來跟
  活畫面逐行比對，重疊區間現在完全一致。

  分頁寬度改由前端傳入：不知道分頁多寬，就不可能照 TUI 的方式決定表格與水平線
  的尺寸。使用預設樣式的分頁行為不變。回歸測試 `tests_overlay_skin.py`，把量到的
  數字釘住。

## v0.31.2 (2026-09-05)

### Fixes

- **Scroll-up history now actually opens in opencode tabs.** v0.30.29 fixed
  which source the overlay reads for those tabs, but the overlay was never
  asked to open: the wheel listener that triggers it is attached to the pane and
  runs in the bubble phase, and opencode's TUI enables mouse tracking (1003 plus
  SGR 1006). With mouse tracking on, xterm.js turns the wheel into a mouse
  report for the application and stops it propagating, so that listener was
  never called once. Claude and Codex tabs do not enable mouse tracking, which
  is why only this one CLI looked broken. The listener now runs in the capture
  phase, where it is reached first. Measured against real xterm.js 5.5.0: with
  mouse tracking on, a bubble listener fires zero times and a capture listener
  fires once; with it off — the Claude and Codex case — both fire, so nothing
  that already worked changes. Regression test:
  `tests_scroll_wheel_capture.py`, which also pins the listener options in the
  page so this cannot silently revert to a passive bubble listener.

  **opencode 分頁的上滑歷史真的會打開了。** v0.30.29 修好的是那些分頁該讀哪個
  來源，但 overlay 根本沒被叫起來過：觸發它的滾輪監聽掛在 pane 上、跑在 bubble
  階段，而 opencode 的 TUI 會開 mouse tracking（1003 加 SGR 1006）。mouse
  tracking 一開，xterm.js 就把滾輪轉成給應用程式的滑鼠回報並擋掉冒泡，那個監聽器
  因此一次都沒被呼叫過。Claude 與 Codex 分頁不開 mouse tracking，所以看起來只有
  這一支 CLI 壞掉。監聽改掛 capture 階段，事件會先經過那裡。用真實 xterm.js
  5.5.0 量過：開著 mouse tracking 時 bubble 監聽觸發 0 次、capture 觸發 1 次；
  關掉時（Claude 與 Codex 的情況）兩者都會觸發，所以原本會動的分頁不受影響。
  回歸測試 `tests_scroll_wheel_capture.py`，同時把頁面上的監聽選項釘住，避免哪天
  又被改回 passive 的 bubble 監聽而沒人發現。

  Known limit, recorded in the test rather than only in a comment: taking the
  event in the capture phase still does not stop the mouse report reaching the
  application — xterm sends it from higher up the capture chain than any
  listener on the pane can reach. It is harmless here; the overlay covers the
  pane, and a mouse-tracking TUI redraws continuously anyway.

  已知限制，寫進測試而不是只寫在註解裡：在 capture 階段接管仍然擋不掉送往應用程式
  的滑鼠回報——xterm 是從比 pane 上任何監聽器都更外層的地方送出去的。這裡無害：
  overlay 會蓋住畫面，開著 mouse tracking 的 TUI 本來就持續重繪。

## v0.31.1 (2026-09-04)

### Fixes

- **A second opencode tab in the same directory no longer shows the first
  tab's conversation.** Matching a tab to its opencode session tried the pane
  title first — opencode stamps it as `OC | <session title>` — and then fell
  back to "the most recently updated session in this working directory". That
  fallback is wrong the moment two opencode tabs share a directory: a freshly
  opened tab has no title of its own yet, so the fallback handed it the
  neighbouring tab's session, and its scroll-up history, status dot and model
  badge all described the other conversation. The fallback is gone; without a
  pane title the lookup now returns nothing and the caller falls back to the
  terminal pipeline. A tab that has not started a conversation has none to
  show anyway. Verified with two live tabs — each now resolves to its own
  session — and covered by two cases in `tests_opencode_provider.py`.

  **同一個目錄開第二個 opencode 分頁，不會再看到第一個分頁的對話。** 分頁對應到
  opencode session 的方式是先比 pane title（opencode 會蓋成 `OC | <session 標題>`），
  比不到就退回「這個工作目錄裡最近更新的 session」。一旦同目錄開了兩個 opencode
  分頁，那條退路就是錯的：新開的分頁還沒有自己的標題，退路直接把隔壁分頁的
  session 給了它，於是上滑歷史、狀態燈、模型徽章講的全是另一段對話。這條退路已經
  移除；沒有 pane title 就回報查不到，呼叫端落回終端管線。還沒開始對話的分頁本來
  就沒有歷史可顯示。已用兩個實際分頁驗證各自對到自己的 session，並在
  `tests_opencode_provider.py` 補兩個案例守住。

## v0.31.0 (2026-09-04)

### Added

- **opencode is a recognised provider.** It used to be just another command you
  could run in a tab: not counted as an AI tab, no status dot, no model badge,
  no init prompt. It now has a registry entry, so tabs running it get the
  AI-tab affordances the other CLIs have. It reports no usage figure on
  purpose — opencode delegates the model to whatever provider the user
  configured, local endpoints included, and those budgets are separate or
  absent, so there is no single number that represents the tab. Reporting
  nothing beats inventing one. Tests: `tests_opencode_provider.py`.

  **opencode 正式成為認得的 provider。** 它原本只是「可以在分頁裡跑的一個指令」：
  不算 AI 分頁、沒有狀態燈、沒有模型徽章、不吃 init prompt。現在有了 registry
  條目，跑它的分頁就享有其他 CLI 既有的待遇。用量刻意回報「沒有」——opencode 把
  模型委給使用者自己設定的 provider（含地端端點），各自的配額互相獨立、甚至根本
  沒有配額概念，不存在一個能代表這個分頁的數字。不報比亂編好。
  測試：`tests_opencode_provider.py`。

- **Status dot and model badge for opencode tabs.** Both read its session
  SQLite, which is where opencode keeps the conversation instead of a JSONL
  transcript, so the shared event state machine had nothing to work with. The
  distinction that matters is what `finish` means: a message that ended with
  `tool-calls` has only paused to run a tool and the turn is still going, while
  anything else ends it. Freshness comes from the message timestamps rather
  than the database file's mtime, because every session shares one file — a
  neighbouring tab's writes would otherwise read as this tab making progress.
  A turn with no writes for longer than the stall threshold is reported stuck
  rather than working forever.

  **opencode 分頁有狀態燈與模型徽章了。** 兩者都讀它的 session SQLite——opencode
  的對話存在那裡而不是 JSONL transcript，共用的事件狀態機無從判起。關鍵在 `finish`
  的語意：以 `tool-calls` 結束的訊息只是停下來跑工具、turn 還沒結束，其餘才算結束。
  新鮮度取訊息自己的時間戳而非資料庫檔案的 mtime，因為所有 session 共用一個檔，
  否則隔壁分頁一寫入就會被讀成這個分頁有進展。超過卡住門檻沒有任何寫入的 turn
  回報為卡住，而不是永遠停在執行中。

### Fixes

- **pi tabs show which model they are running.** pi has been a registered
  provider with a working status dot since v0.29.41, but the model badge was
  never wired up, so those tabs sat blank while every other AI tab named its
  model. The label now comes from the session file's own `model_change` and
  `thinking_level_change` records, which means an in-session model switch is
  reflected instead of the badge being pinned to the launch flags. Thinking
  level is shown only when it is on, since off is the default and would
  otherwise decorate every pi tab. Matching a tab to its session file is
  anchored on the creation time encoded in the file name: pi closes the file
  between appends, so the open-file trick used for another CLI does not apply,
  and the OS will not hand over another process's environment. Tests in
  `tests_pi_provider.py`.

  **pi 分頁看得到自己跑的是哪個模型。** pi 從 v0.29.41 起就是註冊過的 provider、
  狀態燈也正常，但模型徽章一直沒接上，於是別的 AI 分頁都標著模型名，只有它是空的。
  標籤現在取自 session 檔自己的 `model_change` 與 `thinking_level_change` 紀錄，
  因此 session 中途換模型會跟著反映，而不是被釘在啟動參數上。thinking 等級只在
  開啟時顯示，因為預設就是關閉、否則每個 pi 分頁都會掛一個沒有資訊量的標記。
  分頁與 session 檔的對應錨定在檔名裡的建立時刻：pi 每次 append 完就關檔，
  另一個 CLI 用的「檔案一直開著」那招對它無效，作業系統也不會交出別的 process
  的環境變數。測試在 `tests_pi_provider.py`。

### Internal

- **One session resolver for opencode, shared by the status light and the
  scroll-up history.** Both need to answer "which session is this tab?", and
  the overlay already had its own copy; two implementations of that question
  drift apart. The resolver now lives next to the other tab-to-transcript
  lookups and both callers use it.

  **opencode 的 session 對應收斂成一份，狀態燈與上滑歷史共用。** 兩邊都要回答
  「這個分頁是哪個 session」，而 overlay 已經自己有一份；同一個問題兩份實作必然
  走鐘。對應邏輯移到其他「分頁 → transcript」查找的旁邊，兩個呼叫端共用它。

## v0.30.29 (2026-09-04)

### Fixes

- **Scroll-up history works in opencode tabs.** Scrolling up there showed the
  same screen that was already visible, so a tab had no readable history at all.
  Root cause: opencode reached the overlay through the same sparse floor as the
  Claude and Codex tabs — a transcript shorter than 400 characters is discarded
  in favour of the terminal capture, because for those CLIs the live pane is the
  better read. For opencode that fallback is the live pane itself: its TUI
  redraws in place, so rows that scroll out of the viewport never enter the
  terminal scrollback nor the emulator history, and an alternate-screen capture
  returns exactly the frame the user is already looking at. The floor is now a
  content test rather than a length test — the transcript is used whenever it
  holds at least one user or assistant message, and a tool-call-only transcript
  still falls through to the terminal pipeline. Regression test:
  `tests_opencode_history.py`, whose main case is the reported 167-character
  conversation that the old threshold rejected.

  **opencode 分頁的上滑歷史可以用了。** 在那種分頁往上滑，看到的還是眼前這一屏，
  等於整個分頁沒有歷史可讀。根因是 opencode 沿用了 Claude 與 Codex 分頁那條稀疏
  門檻：transcript 不足 400 字就丟掉、改用終端擷取，因為對那兩種 CLI 來說活畫面
  讀感較好。但 opencode 的這個 fallback 就是活畫面本身——它的 TUI 原地重繪，捲出
  視窗的內容既不進終端 scrollback 也不進模擬器歷史，alt-screen 下的擷取拿到的正是
  使用者眼前那一幀。門檻因此從「長度」改成「內容」：只要 transcript 裡有一則使用者
  或助理訊息就採用，而只有工具呼叫、沒有對話的 transcript 仍然讓路給終端管線。
  回歸測試 `tests_opencode_history.py`，主案例就是被舊門檻擋掉的那段 167 字對話。

### Internal

- **The tab-label alignment test reads the page from its own directory.** It
  had an absolute path to one machine's checkout hard-coded, so the suite went
  red on every other machine before running a single assertion.

  **分頁名對齊測試改從自己所在目錄讀頁面。** 它寫死了某一台機器 checkout 的絕對
  路徑，在其他機器上還沒跑到任何斷言就整支紅燈。

## v0.30.28 (2026-09-04)

### Fixes

- **Tables keep their row separators in scroll-up history.** A 13-row table has
  12 identical `├───┼───┼───┤` lines, and the repeat gate — any sufficiently wide
  line seen three or more times in one capture is collapsed to its last
  occurrence — ate every one of them, leaving only the outer border and the rows
  fused into a single block. That gate exists to collapse streaming redraw
  frames, where repetition means a stale partial render; a table rule is the
  opposite, a legitimate repeat, exactly like the blank lines fixed in v0.30.16.
  Lines composed entirely of box-drawing or ASCII rule characters are now exempt
  from the repeat gate. Data rows that merely *contain* those characters are not
  exempt, so a genuinely repeated table row still collapses.
  Verified against the reported table: 27 lines in, 27 lines out, all 12
  separators intact. Three cases in `tests_history_dedup.py`.

  **上滑歷史的表格會保留列分隔線。** 一張 13 列的表有 12 條一模一樣的
  `├───┼───┼───┤`，而重複摺疊規則——同一行在一次 capture 裡出現三次以上就只留最後
  一份——把它們全部吃掉，只剩最外框，所有列黏成一整團。那條規則是為了摺疊串流重繪
  的殘影，在那個情境裡重複代表過期的半成品畫面；表格框線恰好相反，它是**合法的
  重複**，跟 v0.30.16 修的空行完全同類。現在整行只由 box-drawing 或 ASCII 框線字元
  組成的行不受重複摺疊約束；而只是**包含**這些字元的資料列不豁免，所以真正重複的
  表格資料列仍會被收成一份。
  用回報的那張表驗證：進去 27 行、出來 27 行，12 條分隔線全部完整。
  `tests_history_dedup.py` 新增三項。

## v0.30.27 (2026-09-04)

### Internal

- **Opening a new tab is now timed in stages.** Reported alongside the naming
  dialog: the whole view goes blank while a new tab is being created, switching
  tabs does nothing, and it clears after roughly thirty seconds. The backend log
  does not explain it — `new_session` to front-end ready measured 3.6s, and there
  is no slow-`evaluate_js` record at all, so the stall is elsewhere. Each stage
  is now recorded to `[js:newtab]`: the provider install gate, the round trip
  through `new_session`, and the total to first paint. Not a fix; it is the
  evidence needed to find one.

  **開新分頁改為分段計時。** 與命名對話框一併回報：新分頁建立期間整個畫面會白掉、
  切分頁也沒反應，大約三十秒後才恢復。後端 log 解釋不了——`new_session` 到前端就緒
  實測只有 3.6 秒，而且完全沒有慢 `evaluate_js` 的記錄，所以卡點在別處。現在每個
  階段都會寫進 `[js:newtab]`：provider 安裝檢查、`new_session` 的往返、以及到畫面
  首次繪出的總時間。這不是修復，是找出修法所需的證據。

## v0.30.26 (2026-09-04)

### Fixes

- **Preset tabs get the naming dialog too, and no longer keep the preset name
  forever.** Two mistakes in v0.30.25. First, only the New Session box asked for
  a name; opening a tab from a preset is the common path and it still appeared
  unnamed-and-unasked. Second and worse: opening from a preset also calls
  `rename_session` to apply the preset's own name, and v0.30.25 treated any
  rename as a deliberate one — so it cancelled the pending auto-slug and every
  preset tab was stuck on the preset name, never renamed from its content again.
  `rename_session` now takes `manual`, set only by the naming dialog, and preset
  tabs ask for a name with the preset's name pre-filled.
  14 cases in `tests_new_tab_naming.py`.

  **preset 分頁也會跳命名對話框，而且不會再永遠停在 preset 名稱。** v0.30.25 有兩
  個錯。第一，只有 New Session 那條會問名字，而從 preset 開分頁才是常走的路徑，那
  條依然沒被問。第二個更嚴重：從 preset 開分頁時也會呼叫 `rename_session` 去套用
  preset 自己的名稱，而 v0.30.25 把任何改名都當成使用者的刻意決定——於是取消了待
  處理的 auto-slug，每個 preset 分頁就此卡在 preset 名稱上，再也不會依內容改名。
  `rename_session` 現在多一個 `manual` 參數，只有命名對話框會設定它；preset 分頁
  則會跳出命名對話框並預先填好 preset 名稱。
  `tests_new_tab_naming.py` 共 14 項。

## v0.30.25 (2026-09-04)

### Added

- **A new session asks for its name straight away, with Skip to keep the old
  behaviour.** The habitual flow is "open a tab, immediately rename it", and
  having to go and double-click every time is friction. The dialog now appears
  as soon as a tab opened from the New Session box is ready, pre-filled with the
  command name; Skip or Esc leaves everything as it was, so the first message
  still triggers the haiku auto-slug. Preset tabs already carry a name and are
  not asked. Auto-accepting the startup trust dialog runs in the backend and
  does not depend on focus, so the dialog cannot interfere with a tab still
  starting up.

  **新分頁一建立就先問名字，按「跳過」則維持原本行為。** 實際的使用習慣是「開一個
  分頁、馬上改名」，每次都要自己去雙擊是多餘的摩擦。現在從 New Session 開出來的
  分頁一就緒就會跳出命名對話框，預先填好指令名稱；按「跳過」或 Esc 就什麼都不變，
  第一句話仍會觸發 haiku 的自動命名。preset 分頁本來就帶名字，不會被問。自動回答
  啟動信任對話框是在後端進行、不看焦點，所以這個對話框不會干擾還在啟動的分頁。

### Fixes

- **A name you typed yourself is no longer overwritten by the automatic one.**
  The auto-slug renames a tab from the content of its first message, and it did
  so regardless of whether the tab had already been named by hand. That was
  survivable while renaming was a deliberate act after the fact; with the new
  dialog appearing up front it would have wiped the name one message later,
  making the feature pointless. Renaming a session now clears the pending
  auto-slug. 10 cases in `tests_new_tab_naming.py`.

  **自己輸入的名字不會再被自動命名蓋掉。** auto-slug 會依第一則訊息的內容重新命名
  分頁，而且原本不管這個分頁是否已經被手動命名過。在「改名是事後的刻意動作」的年代
  還撐得住；但新的命名對話框改成一開始就跳出來之後，名字會在一句話之後就被抹掉，
  功能等於白做。現在改名會一併取消待處理的 auto-slug。
  `tests_new_tab_naming.py` 共 10 項。

## v0.30.24 (2026-09-04)

### Fixes

- **The startup trust dialog is answered in the right direction instead of
  closing the tab.** The cursor-aware answer read its screen text from a buffer
  that concatenates the raw PTY ring buffer with a tmux snapshot, so a
  half-drawn frame — options painted, cursor not yet — could sit ahead of the
  finished one. Pairing the first `Yes, I trust this folder` row with the first
  cursor row across two different frames inverted the result: with the cursor
  resting on `No, exit`, the computed key was `Up`, which does not wrap at the
  top of the list, so the following `Enter` selected `No, exit` and closed the
  tab. Navigation now reads a single tmux frame and refuses any frame that does
  not contain exactly one cursor row. The keystrokes are verified as well —
  after sending, the screen is re-read, and a dialog that is still up (common
  when the keys arrive before the TUI has taken the keyboard) keeps the tab
  pending for a retry instead of being reported as answered. Regression tests:
  `tests_trust_dialog.py`.

  **啟動信任對話框會按對方向，不會反而把分頁關掉。** 游標感知的作答邏輯，讀的
  是「PTY ring buffer ＋ tmux 快照」接起來的文字，於是一個只畫了選項、還沒畫上
  游標的半成品幀，可能排在完整幀前面。用第一個 `Yes, I trust this folder` 去配
  第一個游標行，跨幀配對就把方向算反了：游標本來停在 `No, exit`，卻算出要按
  `Up`，而清單頂端不會 wrap，等於原地不動，接著那個 `Enter` 就是選 `No, exit`
  把分頁關掉。現在定位只讀單一 tmux 幀，且該幀必須剛好有一個游標行，否則不敢
  按。按鍵本身也會驗證——送出後重讀畫面，對話框還在（按鍵搶在 TUI 接手鍵盤前
  送出時很常見）就保持 pending 等重試，而不是謊報已作答。回歸測試：
  `tests_trust_dialog.py`。

- **Tabs restored after an app restart or a reboot answer the dialog too.** The
  restore paths cleared the pending flag outright, so the auto-accept watcher
  never ran for a reattached or respawned tab — but those are freshly launched
  processes and do get asked. The result was every AI tab stalling on the same
  dialog after a restart, with no way forward from the keyboard: the cursor
  defaults to `No, exit`, so typing a message and pressing Enter closes the tab
  instead of sending anything. Restore now keeps the decision made in
  `Session.__init__` (trusted working directory plus a known AI command) and
  starts the watcher, for tmux reattach, disk-backed soft restore, and the
  account-switch respawn alike. Regression test: `tests_trust_dialog.py`.

  **重開 app／重開機接回來的分頁也會自動作答。** restore 路徑直接把 pending
  旗標關掉，reattach 或重新 spawn 的分頁因此完全沒有 watcher——但那些正是剛長
  出來、一定會被問的行程。結果是重啟後每個 AI 分頁都卡在同一個對話框，而且鍵盤
  救不回來：游標預設在 `No, exit`，打字後按 Enter 等於選它，分頁直接關掉。現在
  restore 沿用 `Session.__init__` 依「受信任工作目錄＋已知 AI 指令」算出的判斷
  並掛上 watcher，tmux reattach、磁碟 soft restore、換帳號重開三條路徑一致。
  回歸測試：`tests_trust_dialog.py`。

## v0.30.23 (2026-09-04)

### Fixes

- **Codex tabs reconnect to their session too, which matters most on Windows.**
  v0.30.22 restored `claude` tabs by uuid; this extends it to `codex` and closes
  the gap that hurts Windows specifically. There is no tmux there, so quitting
  ShellFrame kills every session outright — the disk-backed manifest is the only
  thing that survives, and without a session id each tab came back empty.
  `codex resume <SESSION_ID>` takes a uuid, and the uuid is already in the
  rollout filename, so restore now rewrites the command with it. `resume` is a
  subcommand and must follow the executable immediately, and any previous
  `resume <id>` / `--last` is stripped first so the command cannot degenerate
  into `codex resume resume`.
  Identifying *which* rollout belongs to *which* tab differs by platform. On
  macOS and Linux codex keeps the rollout file open, so `lsof` pins it exactly.
  Windows has neither `lsof` nor a tmux pane, and the existing fallback — newest
  rollout overall — would give every codex tab the same file. Restore there uses
  ordering plus a claim table: the earliest rollout created after that tab was
  spawned and not already claimed by another tab. Tabs record their spawn time
  for this. If nothing matches, the tab opens fresh rather than resuming someone
  else's conversation.
  27 cases in `tests_reboot_resume.py`, including the Windows claim path
  exercised against real rollout files with `IS_WIN` patched on.

  **Codex 分頁同樣會接回原本的 session，這對 Windows 尤其重要。** v0.30.22 讓
  `claude` 分頁用 uuid 接回；這一版擴及 `codex`，補上 Windows 特別痛的缺口——那裡
  沒有 tmux，關掉 ShellFrame 等於直接殺掉所有 session，只有落地的 manifest 活得下來，
  而少了 session id 每個分頁都會回到空白狀態。
  `codex resume <SESSION_ID>` 吃 uuid，而 uuid 本來就寫在 rollout 的檔名裡，所以還原
  時直接用它重寫指令。`resume` 是子指令、必須緊接在執行檔後面，且會先移除既有的
  `resume <id>` ／ `--last`，避免指令退化成 `codex resume resume`。
  至於「哪一份 rollout 屬於哪個分頁」，各平台做法不同。macOS 與 Linux 上 codex 會一直
  持有 rollout 的檔案控制代碼，`lsof` 可以精準定位。Windows 兩者都沒有，而既有的
  fallback「全域最新的一份 rollout」會讓所有 codex 分頁指到同一個檔。那裡改用時序加上
  認領表：取這個分頁 spawn 之後才建立、且尚未被其他分頁認領的最早一份。分頁為此會記下
  自己的 spawn 時間。完全對不上時就開新的，而不是接到別人的對話。
  `tests_reboot_resume.py` 共 27 項，含把 `IS_WIN` patch 成開啟、拿真實 rollout 檔跑
  的 Windows 認領路徑。

## v0.30.22 (2026-09-04)

### Fixes

- **Tabs reconnect to their existing conversation after a machine reboot.**
  A reboot takes the tmux server with it, so every tab has to be re-spawned from
  the session manifest. The manifest stores the command the tab was *originally*
  opened with — without `--resume` that means a brand-new, empty session, losing
  the context of every tab at once. Even a command that already carried
  `--resume` held the uuid from launch time, which goes stale: `/clear` rotates
  it and resume itself often forks a new file. Restore now rewrites the command
  with the session uuid the agent hook last reported (persisted in the manifest
  since v0.30.5), dropping any older `--resume` / `--session-id` first, and only
  for `claude` — codex, agy and plain shells are left alone.
  Existence is checked by locating `<uuid>.jsonl` under `~/.claude/projects`
  rather than trusting the stored transcript path: that path is where the hook
  last saw the file, and `/clear` moves it. A dry run over the real manifest
  found 5 of 14 tabs would have been treated as unresumable on the stored path
  alone; by uuid all 14 resolve. If nothing is found the tab opens fresh, since a
  failed resume would leave it unusable.
  14 cases in `tests_reboot_resume.py`.

  **重新開機後分頁會接回原本的對話。** 重開機會一併帶走 tmux server，所有分頁都得
  從 session manifest 重新 spawn。manifest 存的是這個分頁**當初**的啟動指令——沒有
  `--resume` 就等於開一個空白的新 session，一次丟掉所有分頁的上下文。就算指令本來
  帶著 `--resume`，那個 uuid 也是啟動當時的，會過期：`/clear` 會輪替它，resume 本身
  也常 fork 出新檔。現在還原時改用 agent hook 最後回報的 session uuid（自 v0.30.5
  起就落地在 manifest）重寫指令，先移除舊的 `--resume` ／ `--session-id`，而且只對
  `claude` 動手——codex、agy 與一般 shell 不碰。
  是否存在改為在 `~/.claude/projects` 底下尋找 `<uuid>.jsonl`，而不是相信存下來的
  transcript 路徑：那個路徑只是 hook 最後看到檔案的位置，`/clear` 會讓它搬家。對真實
  manifest 做乾跑，光看存下來的路徑會有 14 個分頁中的 5 個被判定無法接回；改用 uuid
  則 14 個全部找得到。完全找不到時就開新的，因為 resume 失敗會讓分頁根本起不來。
  `tests_reboot_resume.py` 共 14 項。

## v0.30.21 (2026-09-04)

### Changes

- **The terminal no longer intercepts any keystroke during IME composition.**
  v0.30.7 withheld keydown events from xterm while a composition was active, to
  stop `CompositionHelper.keydown` treating candidate-selection keys as "composition
  finished" and pushing the unconverted buffer to the PTY. That approach broke
  input-source switching in two escalating ways — first requiring a second press,
  then failing outright — so it has been removed entirely.
  Measurement shows the interception was never necessary for the switching path:
  against real xterm.js 5.5.0, neither keydown nor keyup is `preventDefault`ed for
  CapsLock, Shift, Ctrl or Cmd, whether or not a composition is open. The IME
  always receives those keys; withholding them only disturbed the event stream
  macOS relies on.
  Filtering now happens purely on the data side: while a composition is open,
  output that consists only of Bopomofo letters and tone marks, or a single digit
  1-9, is dropped — those are the half-finished buffer and the candidate-selection
  keypress, never something the user meant to type. A real commit is Han
  characters and passes through. Deliberately narrower than "drop everything
  during composition": WKWebView can deliver `compositionend` after xterm has
  emitted the committed text, and dropping wholesale would lose characters.
  22 cases in `tests_ime_seq.py`, including the full candidate-picker sequence
  end-to-end.

  **終端不再於 IME 組字期間攔截任何按鍵。** v0.30.7 曾在組字進行中把 keydown 從
  xterm 手上收走，避免 `CompositionHelper.keydown` 把選字按鍵當成「組字結束」而把
  未轉換的緩衝推進 PTY。這個做法讓輸入來源切換連續壞了兩次——先是要按第二次才生效，
  接著完全切不過去——因此整段移除。
  量測顯示這種攔截對切換路徑從來就不必要：以真實 xterm.js 5.5.0 驗證，CapsLock、
  Shift、Ctrl、Cmd 的 keydown 與 keyup 都不會被 `preventDefault`，組字中與否都一樣。
  IME 一直都收得到那些鍵，把它們收走只是干擾了 macOS 依賴的事件流。
  過濾現在完全在資料側：組字進行中，只由注音字母與聲調組成、或單一個 1-9 數字的
  輸出一律丟棄——那是半成品緩衝與選字動作本身，不是使用者想輸入的內容。真正 commit
  出來的是漢字，照樣通過。這比「組字中一律丟棄」刻意更窄：WKWebView 可能在 xterm
  已送出 committed text 之後才派送 `compositionend`，全丟會掉字。
  `tests_ime_seq.py` 共 22 項，含完整的候選清單選字端對端流程。

## v0.30.20 (2026-09-04)

### Fixes

- **Switching between Chinese and English input works on the first press again.**
  A regression from v0.30.7: while a composition was active the terminal handed
  *every* keydown to the IME, including the modifier that switches input source.
  macOS needs the full event stream to act on that key, so taking it away meant
  the switch only landed on the second press, and typing felt sticky throughout.
  Modifiers (Shift, Ctrl, Alt, CapsLock, Cmd) are now always left alone; only the
  keys that xterm mistakes for "composition finished" — space, digits, Enter,
  letters — are still withheld, and those are exactly the ones Bopomofo
  candidate selection uses.

  Letting CapsLock through re-opens the original hole, since xterm then calls
  `_finalizeComposition(false)` and pushes the unconverted buffer to the PTY. A
  second, narrower guard catches that on the data side: while a composition is
  open, output consisting **only** of Bopomofo letters and tone marks is dropped,
  because a real commit is always Han characters. The guard is deliberately not
  "drop everything during composition" — WKWebView can deliver `compositionend`
  after xterm has already emitted the committed text, and dropping wholesale
  would lose characters. Two new cases pin both halves: modifiers pass through,
  and a leaked phonetic buffer is dropped while the committed character still
  gets through (22 cases in `tests_ime_seq.py`).

  **中英文輸入切換恢復按一次就生效。** 這是 v0.30.7 引入的回歸：組字進行中，終端把
  **每一個** keydown 都讓給 IME，包含用來切換輸入來源的那顆修飾鍵。macOS 需要完整的
  事件流才能處理該鍵，被收走之後切換得按第二次才生效，整體打字手感也變鈍。現在修飾鍵
  （Shift、Ctrl、Alt、CapsLock、Cmd）一律不碰；只有會被 xterm 誤判成「組字結束」的鍵
  ——空白、數字、Enter、字母——仍然攔著，而那些正是注音選字會用到的鍵。

  放行 CapsLock 會讓原本的漏洞重開，因為 xterm 接著就呼叫
  `_finalizeComposition(false)`，把未轉換的緩衝區推進 PTY。第二道較窄的防線改在資料
  側處理：組字進行中，**只由**注音字母與聲調符號組成的輸出一律丟棄，因為真正 commit
  出來的一定是漢字。這道防線刻意不是「組字中一律丟棄」——WKWebView 可能在 xterm 已經
  送出 committed text 之後才派送 `compositionend`，全丟會掉字。兩個新測項各守一半：
  修飾鍵放行、漏出的注音被丟而 commit 的漢字照樣通過（`tests_ime_seq.py` 共 22 項）。

## v0.30.19 (2026-09-03)

### Fixes

- **Chinese input no longer degrades into a stream of raw phonetic symbols.**
  Reported as intermittent in daily use: every character arrived as unconverted
  Bopomofo instead of the selected word. This is the full form of the fault
  v0.30.7 addressed — when the keyboard hand-off is not in effect, xterm calls
  `_finalizeComposition(false)` on *every* keystroke during composition, pushing
  the half-finished composition buffer to the PTY each time. The hand-off depends on
  composition state, and that state was tracked by listeners attached directly
  to xterm's helper textarea, with a silent skip when the element could not be
  found. That textarea belongs to xterm: once it is replaced, the listeners are
  gone for good and composition state never updates again — hence "intermittent".
  Composition events are now delegated on the pane, which they bubble to, so
  replacing the textarea cannot break them. State is also per-tab now: a single
  global flag meant a tab left mid-composition could swallow keystrokes in
  whichever tab you switched to. `tests_ime_seq.py` grew a case that replaces the
  textarea outright and asserts the hand-off still engages (18 cases).

  **中文輸入不再退化成一串未選字的注音符號。** 日常使用中回報為間歇發生：每個字都
  以注音形式出現，而非選定的漢字。這是 v0.30.7 處理的那個問題的完整形態——鍵盤讓渡
  一旦失效，注音的**每一個**按鍵都會讓 xterm 走 `_finalizeComposition(false)`，把當下
  還沒選字的組字內容送進 PTY。而讓渡依賴 composition 狀態，該狀態原本由直接綁在
  xterm helper textarea 上的 listener 維護，抓不到元素時還會靜默跳過。那個 textarea
  由 xterm 管理：一旦被重建，listener 就永久消失，composition 狀態從此不再更新
  ——這正是「偶而」的來源。現在 composition 事件委派在 pane 上（事件本來就會冒泡到
  這裡），換掉 textarea 也不會斷。狀態同時改為 per-tab：單一全域旗標會讓停在組字中
  的分頁吃掉你切過去的另一個分頁的按鍵。`tests_ime_seq.py` 新增一項把 textarea 整個
  換掉、驗讓渡仍生效（共 18 項）。

## v0.30.18 (2026-09-03)

### Fixes

- **Opening scroll-up history no longer shifts content vertically.**
  Geometry was already correct — the overlay is deliberately one row shorter so
  the tmux status bar stays visible, and measurement confirmed its row count and
  content bottom line up exactly. The jump came from *content*: history is
  deduplicated, so the same passage occupies fewer lines than on the live screen
  and `scrollToBottom()` left it at a different height. The overlay now takes up
  to four anchor lines from the live viewport — skipping the spinner row, rules
  and the tmux status bar, none of which survive into history — finds the first
  one in the captured text and scrolls so it lands on the same screen row.
  Anchors that cannot fit (the overlay is one row shorter) are skipped rather
  than clamped, which previously pushed the anchor off screen entirely. With no
  match it falls back to `scrollToBottom()`, i.e. the previous behaviour.
  `tests_scroll_overlay_align.py` reproduces a 3-row offset and asserts the
  anchor returns to the live row.

  **上滑歷史開啟時不再垂直位移。** 幾何本來就是對的——overlay 刻意矮一行讓 tmux 綠條
  露出，量測確認它的行數與內容底部完全對齊。跳動來自**內容**：歷史經過去重，同一段
  文字佔的行數比活畫面少，`scrollToBottom()` 之後就落在不同高度。現在 overlay 會從
  活畫面取最多四行錨點——略過 spinner 那行、分隔線與 tmux 綠條，這些都不會留在歷史裡
  ——在擷取的文字中找到第一個相符者，捲動到讓它落在同一個螢幕行。對不進來的錨點
  （overlay 矮一行）直接換下一個而不是硬夾，先前硬夾會把錨點整個推出畫面。完全找不到
  時退回 `scrollToBottom()`，也就是原本的行為。`tests_scroll_overlay_align.py` 重現
  3 行的錯位並驗證錨點回到活畫面的那一行。

### Internal

- **Release notes now have a written standard and a gate that enforces it.**
  `docs/changelog-guide.md` defines the structure (semver heading with date,
  `Fixes` / `Changes` / `Added` / `Internal` sections), requires each entry to be
  written in English first and then Chinese, and requires it to answer four
  things: symptom, root cause, fix, and which test guards it. It also bans what
  had been creeping in: personal names, pasted chat excerpts, internal
  codenames, and self-flagellation. `tests_changelog_format.py` (part of
  `./run_tests.sh`) checks the heading format, that `version.json` matches the
  top entry, that section names are known, that both languages are substantially
  present, and that no names or quoted complaints appear. Entries for
  v0.30.5–v0.30.17 were rewritten to the standard and 55 occurrences of a
  maintainer's name were removed across the whole file, so the name check can
  guard the entire document rather than just the latest release.

  **Release notes 有了成文規範，以及擋得住的檢查。** `docs/changelog-guide.md` 定義
  骨架（semver 標題帶日期，`Fixes`／`Changes`／`Added`／`Internal` 分區）、要求每個
  條目英文先中文後，並要求回答四件事：症狀、根因、修法、哪支測試守著。同時明文禁止
  先前逐漸滲入的東西：人名、貼上的對話片段、內部代號、對自己的檢討。
  `tests_changelog_format.py`（納入 `./run_tests.sh`）檢查標題格式、`version.json`
  是否等於最新版、分區名是否已知、兩種語言是否都有足量內容、以及是否出現人名或引號
  裡的抱怨。v0.30.5–v0.30.17 的條目已按規範重寫，並清掉全檔 55 處維護者姓名，因此
  人名檢查得以守住整份文件而非只有最新版。

## v0.30.17 (2026-09-03)

### Fixes

- **Scroll-up history no longer shifts the view sideways when it opens.**
  A regression introduced in v0.30.11: that release moved the live terminal pane
  to start at `--hint-gutter` to make room for the tab-name label, but the
  history overlay was still pinned to `left: 0`, so opening it displaced every
  line by the gutter width. The overlay now measures the live pane's actual left
  edge instead of assuming zero, so it follows future layout changes too and
  falls back to 0 when there is no gutter. Regression test:
  `tests_scroll_overlay_align.py`.

  **上滑歷史開啟時不再橫向位移。** 這是 v0.30.11 引入的回歸：該版把活畫面左緣推到
  `--hint-gutter` 以容納分頁名標籤，overlay 卻仍固定在 `left: 0`，一開就讓每一行
  整體位移一條 gutter 的寬度。現在改為量測 live pane 的實際左緣，而不是假設它是 0
  ——之後版面再變也跟得上，沒有 gutter 時自動退回 0。回歸測試：
  `tests_scroll_overlay_align.py`。

## v0.30.16 (2026-09-03)

### Fixes

- **Blank lines between paragraphs survive in scroll-up history.**
  The strict-prefix dedup pass dropped every internal blank line, because an
  empty string is a prefix of any string: a blank current line looked like a
  prefix of the previous one and was skipped, and a blank previous line was
  overwritten by the next. History therefore rendered as one dense block that
  did not match the live screen. Blank lines are now excluded from that
  comparison; collapsing between non-blank lines is unchanged. `_render_rows`
  had always documented that internal blank lines are preserved, so the
  implementation contradicted its own contract with no test guarding it.
  Four new cases in `tests_history_dedup.py`.

  **上滑歷史保留段落之間的空行。** strict-prefix 去重會吃掉所有內部空行，因為空字串
  是任何字串的前綴：空的當前行被判定為前一行的前綴而跳過，空的前一行則被下一行蓋掉。
  歷史因此擠成一整團，與活畫面的排版完全不同。現在空行不參與該比較，非空行之間的
  摺疊不變。`_render_rows` 本來就註明內部空行要保留，實作與自己的約定打架，而且沒有
  測試守著。`tests_history_dedup.py` 新增四項。

## v0.30.15 (2026-09-03)

### Added

- **Manual and periodic re-evaluation of the per-tab status dot.**
  Interrupting an agent (Ctrl+C / Esc) does not reliably emit a Stop hook, so the
  cached state stayed `working`; the status monitor's idle gating then skipped
  recomputation because the tab's PTY had printed nothing since the last pass.
  The two safeguards covered for each other and the dot stayed on "running"
  indefinitely. The existing 15-second force-refresh could not help, as it only
  revisits tabs that produced output. New `refresh_agent_status(sid="")` clears
  both the status cache and the hook state so the heuristics re-derive it from
  the screen; a tab that really is busy gets marked `working` again, which makes
  clearing safe. A ↻ button beside the sidebar's Sessions heading calls it, and
  the monitor now does the same every 300 seconds so silent tabs recover without
  user action. 8 cases in `tests_status_refresh.py`.

  **狀態燈號可手動、也會定期重判。** 中斷 agent（Ctrl+C／Esc）時不一定會發出 Stop
  hook，快取狀態就停在 `working`；而 status monitor 的 idle gating 又因為該分頁自上
  一輪起沒有任何輸出而跳過重算。兩層防護互相掩護，燈號於是永遠停在「執行中」。原有
  的 15 秒強制重算救不了，它只重訪有輸出過的分頁。新增
  `refresh_agent_status(sid="")`：清掉狀態快取與 hook 狀態，讓 heuristic 從畫面重新
  判斷；真的還在忙的分頁會被重新標回 `working`，所以清掉是安全的。側欄 Sessions
  標題旁的 ↻ 會呼叫它，monitor 也每 300 秒自己做一次，安靜的分頁不必等使用者動作。
  `tests_status_refresh.py` 共 8 項。

## v0.30.14 (2026-09-03)

### Added

- **Double-click the tab-name label to rename the session.**
  Reuses the existing `renameSession()` dialog, the same one the tab bar and the
  sidebar open. The label previously had `pointer-events: none`; it now takes
  clicks and shows a pointer cursor with a hover highlight, while text selection
  stays disabled so a double-click cannot select the label's own text. One
  listener is enough, because unlike the sidebar the label is static DOM and is
  never rebuilt by a render pass.

  **雙擊分頁名標籤即可改名。** 沿用既有的 `renameSession()` 對話框，與分頁列、側欄
  走同一條路。標籤原本是 `pointer-events: none` 的純提示，現在收回點擊、加上 pointer
  游標與 hover 提亮，並維持關閉文字選取，避免雙擊選到標籤自己的文字。只需綁一次，
  因為與側欄不同，標籤是靜態 DOM，不會被 render 重建。

## v0.30.8 – v0.30.13 (2026-09-02 – 2026-09-03)

### Added

- **A persistent label shows which tab you are typing into.**
  Typing into the wrong tab is expensive: the message lands in an unrelated
  agent's context. The label sits in a 17px gutter left of the terminal, set
  vertically, and tracks the row the cursor is on by reading the xterm DOM row
  for `buffer.active.cursorY` rather than guessing a fixed corner or a fixed row
  offset. The input row genuinely moves: the permission-hint row, the presence of
  a tmux status line and multi-line input all shift it. The label covers no
  terminal content and overlaps the sidebar edge by 4px, so legible 11px type
  costs no terminal width. `tests_tab_hint_align.py` asserts vertical centring on
  the cursor row, that the label never overlaps the terminal, and that Latin
  names are not stacked one letter per line.

  **常駐標籤顯示目前正在對哪個分頁打字。** 打錯分頁的代價很高：訊息會落進不相干的
  agent 的脈絡裡。標籤直排於終端左側 17px 的 gutter 中，並跟隨游標所在的那一行
  ——讀取 `buffer.active.cursorY` 對應的 xterm DOM row，而不是猜固定角落或固定行號。
  輸入行的位置確實會移動：權限提示行、tmux status line 是否存在、以及多行輸入都會
  影響它。標籤不遮任何終端內容，並往側欄那側疊 4px，使 11px 的可讀字級不必多佔終端
  寬度。`tests_tab_hint_align.py` 驗證垂直對齊游標行、標籤絕不壓到終端、以及拉丁
  文字不會被逐字母堆疊。

### Changes

- **Telegram hides the model badge whenever the desktop sidebar hides it.**
  The two surfaces kept separate state, so the bridge went on showing a stale
  value after the badge had been switched off locally. The slash-command menu
  dropped the model entirely: `setMyCommands` is a snapshot taken at registration
  time, so a tab left idle for days freezes on whichever model it last talked to,
  with nothing on the phone to indicate the value is out of date. The model is
  now computed at switch time and shown in the reply header instead.

  **Telegram 的模型徽章跟隨桌面側欄的開關。** 兩邊原本各有一套狀態，本地關掉徽章後
  bridge 仍顯示一個過期的值。slash 指令選單則完全移除模型：`setMyCommands` 是註冊
  當下的快照，久未使用的分頁會凍結在它上次對話的模型上，而手機端看不出這個值已經
  過期。模型改在切換分頁時即時計算，放進回覆表頭。

### Internal

- `evaluate_js` pushes slower than 400ms now log the tab, chunk size and tab
  count. An intermittent UI freeze reported in daily use could not be captured by
  sampling after the fact, so this records evidence instead of guessing. Not a
  fix; the cause is still open.

  超過 400ms 的 `evaluate_js` 推送會記錄分頁、chunk 大小與分頁數。日常使用中回報的
  間歇性 UI 凍結無法事後取樣重現，因此先留下證據而非猜測。這不是修復，原因仍未確診。

## v0.30.7 (2026-09-02)

### Fixes

- **Chinese input no longer leaks composition text while picking a candidate.**
  xterm's `CompositionHelper.keydown` only exempts Shift/Ctrl/Alt; every other
  key calls `_finalizeComposition(false)`, which pushes whatever is currently in
  the textarea straight to the PTY. Bopomofo candidate selection presses exactly
  those keys: space opens the candidate list and digits pick an entry. Verified
  against real xterm.js 5.5.0 — typing `ㄧ`, pressing space, then `2` emitted
  `['ㄧ', '2', '依', '依']`, i.e. the raw phonetic symbol, the selection digit and
  a duplicated commit. The keyboard is now handed entirely to the IME while a
  composition is active, leaving `['依', '依']`, which the dedup below collapses
  to one. A 30-second staleness cap means a missed `compositionend` cannot
  swallow the keyboard permanently. 16 cases in `tests_ime_seq.py`.

  **中文輸入在選字過程不再漏出組字內容。** xterm 的 `CompositionHelper.keydown` 只
  放行 Shift/Ctrl/Alt，其他任何按鍵一律呼叫 `_finalizeComposition(false)`，把當下
  textarea 裡的內容直接送進 PTY。而注音的選字流程按的正是那些鍵：空白鍵叫出候選
  清單、數字鍵挑選。以真實 xterm.js 5.5.0 驗證：打「ㄧ」→ 空白 → 按 `2`，送出
  `['ㄧ', '2', '依', '依']`，即注音符號、選字數字與重複的 commit。現在 composition
  進行中把鍵盤完全交給 IME，只剩 `['依', '依']`，再由下述去重收成一個。另設 30 秒
  過期上限，避免漏收 `compositionend` 時鍵盤被永久吃掉。`tests_ime_seq.py` 共 16 項。

## v0.30.5 – v0.30.6 (2026-09-02)

### Fixes

- **Committing a Chinese character no longer types it twice.**
  Switching to English mid-composition commits the pending text, and because
  macOS switches on the modifier's keyup, xterm's `_keyDownSeen` has already been
  cleared — so both `_inputEvent` and the `setTimeout` scheduled by
  `_finalizeComposition` pass the same text through. The existing dedup only
  looked at `data.length > 1`, and a Chinese character has length 1, so the rule
  never applied. Dedup is now keyed on this specific commit (the
  `compositionend` payload) rather than a time window: the first copy passes,
  later identical copies within the same composition are dropped, and the next
  `compositionstart` resets it, so typing the same character twice on purpose
  still yields two. An earlier attempt used a 150ms window and ate real
  keystrokes; measured separation between the duplicate writes is under 1ms, so
  that window was two orders of magnitude too wide. A backstop in `write_input`
  covers the same shape for the other input paths, and only when no other input
  is interleaved. Tests: `tests_ime_dedup.js`, `tests_ime_seq.py`,
  `tests_ime_backstop.py`.

  **中文字送出時不再重複一次。** 在組字途中切換中英文會 commit 未完成的內容，而
  macOS 是在修飾鍵的 keyup 才切換，此時 xterm 的 `_keyDownSeen` 已被清除，於是
  `_inputEvent` 與 `_finalizeComposition` 排的 `setTimeout` 兩條路都把同一段文字送
  出去。原有去重只看 `data.length > 1`，而中文字長度為 1，規則從未生效。現在去重
  綁定「這一次 commit」（`compositionend` 的內容）而非時間窗口：第一份放行，同一次
  composition 內後續完全相同的重複丟棄，下一次 `compositionstart` 重置，所以刻意連
  打同一個字仍會得到兩個。先前一版用 150ms 窗口，會吃掉真實輸入；實測兩次寫入的
  間隔不到 1ms，該窗口大了兩個數量級。另在 `write_input` 加保底，涵蓋其他輸入路徑
  的同一形狀，且僅在中間沒有夾雜其他輸入時生效。測試：`tests_ime_dedup.js`、
  `tests_ime_seq.py`、`tests_ime_backstop.py`。

- **The model badge no longer degrades after a restart.**
  Detection relies on the transcript path reported by the agent hook, which only
  lived in memory. After a restart it fell back to the `--resume` UUID on the
  command line, i.e. the transcript named at launch rather than the one being
  written now — `/clear` rotates the UUID and resume often forks a new file. The
  path and current session UUID are now persisted in the session manifest and
  restored on startup, written only when the hook reports a different file rather
  than on every tool call.

  **模型徽章在重啟後不再退化。** 偵測依賴 agent hook 回報的 transcript 路徑，而它
  只存在記憶體中。重啟後便退回命令列裡的 `--resume` UUID，也就是啟動時指定的那份
  transcript，而不是當下正在寫入的那份——`/clear` 會輪替 UUID，resume 也常 fork 出
  新檔。該路徑與當前 session UUID 現在會寫入 session manifest 並於啟動時還原，且僅
  在 hook 回報換檔時落地，而非每次工具呼叫都寫。

### Changes

- **Drag-and-drop reads the native drag pasteboard on `dragenter`.**
  This removes an IPC round-trip at drop time and avoids depending on `dt.files`.
  It also closes a wrong-file hazard found while implementing it: the drag
  pasteboard retains the previous drag's contents — with no drag in progress it
  still returns a file dropped ten minutes earlier — and sources that hand over
  an in-memory blob never write to it, so matching on path count alone would
  attach the stale file. `drag_pasteboard_snapshot()` also returns the
  pasteboard's `changeCount`, which only advances on a real write; the preread is
  trusted only when that value is newer than the last one used, the path count
  matches exactly, and files are actually present.

  This is **not** a fix for slow path resolution on drop. The earlier hypothesis
  (WebKit copying the file while materialising `dt.files`) was disproved: the
  `BlobRegistryFiles-*` directories cited as evidence are created once per WebKit
  launch and are empty. Measurement showed the handler itself takes 18ms and the
  delay sits before WebKit dispatches the drop event.

  **拖放改在 `dragenter` 就讀取原生 drag pasteboard。** 這省掉 drop 當下的一趟 IPC，
  也不再依賴 `dt.files`。同時修掉實作過程中發現的附錯檔風險：drag pasteboard 會保留
  上一次拖曳的內容——在沒有任何拖曳進行時，仍讀得到十分鐘前拖入的檔案——而交付
  in-memory blob 的來源根本不寫這塊 pasteboard，因此僅比對路徑數量會把殘留的舊檔
  附上去。`drag_pasteboard_snapshot()` 另外回傳 pasteboard 的 `changeCount`，它只在
  真的被寫入時遞增；唯有該值比上次採用過的更新、路徑數量精確相符、且確實有檔案時
  才信任預讀結果。

  這**不是**拖放取得路徑緩慢的修復。先前的假設（WebKit 具現化 `dt.files` 時複製
  檔案）已被推翻：當作證據的 `BlobRegistryFiles-*` 目錄是每次 WebKit 啟動就建立且
  皆為空。量測顯示 handler 本身只花 18ms，延遲發生在 WebKit 派送 drop 事件之前。

### Internal

- Window close and SIGINT/SIGTERM each write a `[lifecycle]` line. A scheduled
  macOS update quit every GUI app during an unattended restart that another
  application subsequently cancelled; the debug log recorded nothing, so
  establishing that the app had not crashed required a second-by-second read of
  the unified log.

  視窗關閉與 SIGINT／SIGTERM 各會寫一行 `[lifecycle]`。macOS 排程更新曾在無人時
  發起重新開機並 quit 掉所有 GUI app，隨後該重啟又被另一個應用程式取消；當時 debug
  log 什麼都沒留，要確認 app 並非自行崩潰得逐秒比對 unified log。

- `./run_tests.sh` runs `tests_*.py` and `tests_*.js` together. Front-end logic
  tests are `.js`, and a Python-only glob silently skipped them.

  `./run_tests.sh` 會一併執行 `tests_*.py` 與 `tests_*.js`。前端邏輯測試是 `.js`，
  只用 Python 的 glob 會靜默漏掉它們。

## v0.30.4 (2026-09-01)

- 眼鏡（Agent Relay）改成**外掛、預設隱藏**。它需要另外安裝 bridge 才有作用，
  沒裝的人在每個分頁上看到一顆按不出東西的按鈕只是困惑。設定裡新增
  「眼鏡操控（Agent Relay 外掛）」開關，預設關。
- ⚠️ 這個開關只控制**要不要顯示**那顆按鈕，**不授權任何分頁**。既有的
  `glasses_allowed_sessions` 授權完全沒動：關掉顯示不會撤銷已開放的分頁，
  打開顯示也不會開放任何分頁。授權語意（allow list、預設關、fail-closed）不變。

## v0.30.3 (2026-08-31)

### Fixes
- **Claude 分頁（s88「日常」）的長回覆在 TG 反覆出現、逐句變長**。
  與同日 pi 的兩個問題**都不同**：這次洩漏的是正文而非 marker，發生在
  Claude Code 分頁而非 line-oriented agent。
  根因：TUI 逐步畫出一段長回覆時，每次重繪都在原始 PTY 串流留下一份
  「畫到一半」的版本，而它們全落在 marker span 內 → 被當成正文送出。
  特徵是**後一版是前一版的嚴格延伸**（`…是否仍維` → `…是否仍維持「文件
  齊備」…的狀態？`），所以 `clean_mobile_marker_response` 的逐行去重完全
  擋不住——每一行都不一樣。實測輸入 5 行中間狀態，5 行原封不動全數送出。
  修法：新增 `_fold_streaming_prefixes()`——丟掉「是後面某行嚴格前綴」的
  行，只保留最完整的那一版。
  兩道護欄：
  1. **12 字長度門檻**（`_STREAM_FOLD_MIN_LEN`）。短行的前綴關係在正常內容
     很常見——清單裡 `- todo` 與 `- todo list 要整理` 是兩個合法項目，
     無條件摺疊會變成**靜默刪內容**（開發過程中確實先踩到、才補這道）。
  2. **40 行比較窗口**（`_STREAM_FOLD_WINDOW`）。中間狀態必然彼此相鄰，
     限制窗口讓成本維持線性；全量兩兩比較在數百行的長回覆會是 O(n²)。
     實測 800 行 < 60ms。

回歸測試：新增 `tests_tg_stream_fold.py`（5 案例：摺疊、誤刪防護、
不相關內容全留、長回覆效能、常數具名）。全套 40 檔綠。
bridge 改動，`sfctl reload` 生效。

## v0.30.2 (2026-08-31)

### Fixes
- **留痕本身可以被一行洗掉**。v0.30.1 的 `set_session_glasses` 是無條件 append，
  所以對同一個 sid 連下 40 次 **no-op** deny（`sfctl glasses` 的 sid 是 `nargs="*"`，
  也沒有限流）就能把環狀緩衝擠滿、把真正的授權紀錄全部推出去——而授權本身
  一動也沒動。獨立稽核當場示範了這件事。
  這正好打臉 v0.30.1 自己的論點「擋不住的就要看得見」：看得見的那部分太好擦。
  修法：只有**真的改變狀態**才寫一筆（先讀現況比對）。

### Corrections
- v0.30.1 的說明把 13:26 那次「十一個分頁一次開完」寫成「被違規全開」。**那是錯的**
  ——那是維護者要求的。我在不知情下把它當異常，還一次 deny 掉，毀了他要的狀態。
  發現的問題本身仍然成立（那十一次變更在正式稽核軌跡上是隱形的），但事件的
  性質寫反了，已在該條目更正。

回歸測試：`tests_glasses_allowlist.py` 補 no-op 不留痕兩案。全套 38 檔綠。
`main.py` 有動，**需要 `sfctl restart`**。

## v0.30.1 (2026-08-31)

### Fixes
- **眼鏡授權的變更完全沒有留痕**。v0.30.0 只有透過手機端 bridge 的那條路會寫
  稽核；`sfctl glasses allow`、本機 API、側邊欄那顆 👓 三條路都只進 debug log，
  而 debug log 會滾掉。
  怎麼被抓到的：13:26:06–13:26:11 之間有十一次 `sfctl glasses allow`
  （每 0.5 秒一次、一個 sid 一次）把**全部 11 個分頁**都開了。
  ⚠️ **更正**：這是維護者要求的，不是違規操作——當時寫成「被違規全開」是錯的，
  我在不知情下把它當成異常還一次 deny 掉，反而毀了他要的狀態。
  但抓到的問題本身成立：**唯一的線索只有 `/tmp/shellframe_debug.log` 裡的
  `[glasses]` 行**，那個檔會滾掉、也在 `/tmp`。授權變更了十一個分頁，
  正式的稽核軌跡上完全是隱形的。
  **這也修正了一個過度宣稱**：「沒有全開按鈕」只是 UI 的性質，不是強制的限制
  （`sfctl glasses allow` 本來就吃多個 sid，就算不吃，一個 shell 迴圈也一樣）。
  正確的說法是「沒有單一控制項能一次全開，而且每一筆授權都留得下來」。
  修法：`set_session_glasses` 多收一個 `source`（`sfctl` / `api` / `ui`），
  每次成功的變更寫進 `config.glasses_audit`（環狀，保留最後 40 筆），
  `sfctl glasses` 印出最近 5 筆。失敗的變更不留痕。

- **白名單有可能被「靜靜」清空**。`_persist_session_manifest` 以前是
  `getattr(s, '_glasses_enabled', False)` 然後 discard——也就是說**任何**建立
  Session 卻忘了設這個旗標的路徑（未來新增一條就中），都會在下一次持久化時
  把那個分頁的授權悄悄拿掉。使用者看到的是「眼鏡突然送不進去了」，
  然後很自然地去把一堆分頁重開一次，反而擴大暴露。
  修法：旗標改用 `None` 當哨兵——「這個物件沒有意見」不得推翻設定檔，
  只有明確的 `True` / `False` 才會加入或移除。另外加一條 tripwire：
  持久化時若把一個非空的白名單寫成空的，就在 debug log 留一行說明當下看到
  幾個 session，下次再發生才查得出來。`deny` 要清空仍然清得掉。

回歸測試：`tests_glasses_allowlist.py` 補第 5、6 組（11 案例：留痕內容、
來源標記、上限、失敗不留痕；沒有意見的 session 不得推翻設定檔、明確 deny
仍能清空）。全套 38 檔綠。`main.py` / `api_server.py` / `web/index.html`
有動，**需要 `sfctl restart`**。

## v0.30.0 (2026-08-31)

### Features
- **眼鏡（Agent Relay）白名單搬進 ShellFrame**。以前要開放某個分頁給 G2 眼鏡
  下語音指令，得跑另一支 CLI 的 `evenclaude allow s88`，狀態只能自己 curl。
  現在 sid 語彙統一，開關與狀態都在 ShellFrame 裡：
  - 側邊欄每列多一顆 👓，點一下開／關；**開的時候會跳確認對話框**，
    收回不會問。設計上刻意不做「全開」按鈕。
  - `sfctl glasses` 看狀態（bridge 心跳／relay 連線／已配對眼鏡／開放中的分頁），
    `sfctl glasses allow s88`、`sfctl glasses deny s88` 一次一個 sid。
  - 本機 API 加 `GET /glasses`、`POST /sessions/{sid}/glasses-allow|glasses-deny`，
    `GET /sessions` 多回 `glasses_enabled` / `provider` / `tmux_name` / `transcript`。
  - 為什麼這是安全設計而不是 UX：這裡每個分頁都跑
    `--dangerously-skip-permissions`，把分頁開給眼鏡＝「在外面講的話會在這台
    機器上執行」。所以是 **allow list 不是 deny list**（`glasses_allowed_sessions`），
    新分頁預設關，manifest 缺欄位一律解讀成關，重開／換帳號重生分頁都沿用原值。
    `bridge_disabled_sessions` 是 deny list，這支刻意反過來。

- **`GET /sessions` 回報 provider**（`claude` / `codex` / `pi` / `other`）。
  沿用 `agent_status` 既有的判定（`_worker_kind` 開了公開別名 `worker_kind`），
  不另寫第二套字串比對——那正是兩邊會走鐘的方式。眼鏡端靠它分辨「這則回覆
  是誰講的」，因為現在 Claude 與 Codex 分頁都能接。

- **glasses-enabled 的分頁順便回 transcript 路徑**。codex 沒有 `--session-id`，
  唯一可靠的 tab→rollout 對應是它開著的 fd（`lsof`）——這段邏輯 `agent_status`
  已經有，所以留在這裡解析（20 秒快取）而不是讓橋接端再抄一份。
  **只對已開放的分頁解析**，沒開放時成本是零。

回歸測試：新增 `tests_glasses_allowlist.py`（24 案例，含 provider 判定、
只動指定 sid、不存在的 sid 不得污染設定檔、manifest 往返、升級路徑預設關）。
全套 38 檔綠。`main.py` / `web/index.html` / `sfctl.py` / `api_server.py` 都有動，
**需要 `sfctl restart`**。

## v0.29.52 (2026-08-31)

### Fixes
- **pi 傳到 TG 的訊息塞滿 marker 碎片**（實案截圖：一則訊息裡有 20 行
  `[[` / `[[/` / `[[/TG` / `[[/TG_REPLY_3ca` … 逐字元增長的殘骸）。
  這與 v0.29.51 修的「進度被當成回覆」是**不同的洩漏**。
  根因：pi 是**逐字元 flush** end marker，原始 PTY 串流因此留下
  `[[` → `[[/` → … → `[[/TG_REPLY_3ca65bb9]` 整串中間狀態，而它們全都落在
  start／end 之間 → `_pick_marker_reply` 的 span 配對本身是對的（它找到的是
  完整 end marker），但 **span 內容**把那些中間狀態一起帶了出來。
  既有防線都擋不住：`_REPLY_MARKER_TOKEN_RE` 要求 `]]` 結尾，攔不到半截；
  `clean_mobile_marker_response` 的逐行去重也沒用——每一行都不一樣。
  Claude Code 一次寫完 marker，所以從不觸發。
  修法：新增 `_is_marker_fragment()`——判斷某行是否只是本輪 marker 的
  **前綴**，是就整行丟掉；清洗函式改吃 `markers` 參數（未傳時行為完全不變，
  其他呼叫端不受影響）。以 marker 前綴判定而非萬用 regex，所以正文裡正常的
  `[[weird]]`／`[[note]]` 不會被誤刪（有測試釘住）。

調研過程：先用 log 確認那則訊息走的是**正常 marker 路徑**（不是 fallback、
不是 /fetch），再用逐字元串流重現出與截圖一模一樣的 20 行碎片，才動手。

回歸測試：新增 `tests_tg_marker_fragment.py`（5 案例，含誤刪防護與
向後相容）。全套 38 檔綠。bridge 改動，`sfctl reload` 生效。

## v0.29.51 (2026-08-31)

### Fixes
- **pi 分頁在 TG 還是跳針**（v0.29.50 修掉送達驗證那條之後仍在）。
  這次不是 bridge 的 bug：pi 會**逐階段**把輸出包進 marker——
  先回一則 `[[TG_REPLY_x]] 正在檢查… [[/TG_REPLY_x]]`，做完事再包一次
  最終回覆。bridge 照 v0.29.21 的 follow-up 語意，每個 marker 區塊各轉發
  一次（那是為了「背景 worker 完成通知」刻意加的），使用者端就變成一則
  進度＋一則結果＝跳針。Claude Code 只在收尾包一次，所以從不踩到。
  修法：對 line-oriented agent 在 marker 指示後面追加一句語意說明——
  標記只用於**最終回覆**，進度／「正在檢查…」寫在標記外。
  **follow-up 能力本身保留**（背景任務真的完成時再包一次仍會送出），
  TUI agent 的指示一字未改。

### Known
- log 中偶見 `[rate-limit] <sid> detect failed: string index out of range`。
  已被 try/except 接住、不影響功能，且用空白／空列 display 重現不出來，
  故本輪不動它（避免為了「看起來完整」亂改沒把握的路徑），先記錄在此。

回歸測試：`tests_tg_line_oriented.py` 補第 7 案（指示必須在 line-oriented
閘門內、TUI 不受影響）。全套 37 檔綠。bridge 改動，`sfctl reload` 生效。

## v0.29.50 (2026-08-29)

### Fixes
- **TG 送訊息給 pi 分頁會「跳針」**（pi 多回一次，接著再收到一則「無回應」
  通知）。根因不在 pi：`_verify_injection` 判定 delivered 只認兩個
  **Claude Code TUI 專屬**訊號——畫面出現 `esc to interrupt` footer、或
  bridge 抽到新回覆（`last_extraction_ts`）。pi 是 line-oriented REPL，兩者
  皆無 → delivered 永遠 False → 觸發兩段補償：
  1. residue 判為真 → 補送裸 Enter（agent 多收一次空輸入、多回一次）；
  2. 走 deferred verdict → 45 秒後通知「無法確認送達」。
  訊息其實每次都成功送到了。
  修法：新增 `_DEFAULT_LINE_ORIENTED_AGENTS` ＋ `line_oriented_agents()`
  （可由 `settings.line_oriented_agents` 覆寫）＋ `is_line_oriented(cmd)`，
  送出路徑對這類分頁直接視為送達，跳過 nudge／retry／deferred verdict，
  並留下 `[send] <sid> line-oriented agent → skip delivery verification`。
  與 `system_directive_agents` **刻意分開**：那份管「指令要不要加框」，
  這份管「送達驗證適不適用」，兩者未必重疊。
  比對用**啟動指令第一個 token 的 basename 做完整相等**，不用 substring——
  pi 分頁的指令就是裸字串 `pi`，`in` 比對會把 `pip install …`／`api-server`／
  `raspi-config` 一起誤判、害那些分頁失去送達驗證。
  清單同時涵蓋 preset 用的 wrapper（`sf-pi-spark`／`sf-sparkagent`）——
  精確比對的代價就是 wrapper 必須列出，否則只有裸 `pi` 生效、preset 開的
  分頁照樣跳針。
  Claude／Codex／agy 分頁的驗證與重試行為完全不變（有測試釘住 TUI 分支）。

回歸測試：新增 `tests_tg_line_oriented.py`（6 案例）＋`tests_tg_inject.py`
補 gate 分流素材。全套 32 檔綠。bridge 改動，`sfctl reload` 生效。

## v0.29.49 (2026-08-28)

### Fixes
- **自動接受信任對話框＝自動關掉新分頁**。Claude Code 這版的
  「Quick safety check」游標**預設停在 `No, exit`**（Yes 在下面一行）：

        ❯ No, exit
          Yes, I trust this folder

  舊的 `_auto_accept_startup_trust_prompt` 是「送一個 Enter」，等於替使用者
  選了 exit。現在改成先讀游標在哪一行、再決定按幾次方向鍵，確定落在
  「Yes, I trust this folder」才 Enter；選項讀不出來就**維持 pending 不亂按**，
  留給 TG 帶回手機。順帶讓 `startup_dialog_blocking` 在任何時候看到這個對話框
  都能補答（不受開機那幾秒的 deadline 限制），卡住的分頁自己救得回來。

### Features
- **信任對話框帶回 Telegram**。純手機操作時桌面不在手邊，而這個對話框既不能
  盲按 Enter、也不能靠「硬送一則訊息」清掉（兩者都會關分頁）。現在 `/new` 開的
  分頁若停在對話框，會直接在 TG 推一組 inline 按鈕
  「✅ 信任這個資料夾 / ✕ 關掉分頁」，按下去由 app 端用游標感知的方式作答；
  被 ready gate 擋下的訊息也改成附上同一組按鈕，並提醒回答完重發。
  同一分頁只推一次，不洗版。`/new` 的成功訊息也補上分頁編號（`/N`）。

作答後 5 秒內不重複作答（對話框文字還留在畫面時再按一次，就會變成對著正常
  composer 按 Up＋Enter，把上一則輸入叫回來又送出去）。

  實測：17:16 新開的分頁停在對話框，新邏輯判定 `keys=['Up','Enter']`（那次是
  Yes 在上、游標在 No），分頁順利進到正常輸入狀態。

回歸測試：新增 `tests_trust_dialog.py`（8 案例）。全套 34 檔綠。
main.py 有動 → 需 `sfctl restart`。維護者 2026-08-28 回報＋截圖。

## v0.29.48 (2026-08-28)

### Fixes
- **手機端新開的分頁，訊息會把它自己關掉**。TG 的注入是 Ctrl-U ＋整段文字 ＋
  Enter——打進還停在啟動對話框的 CLI，等於幫使用者「選一個選項」，而 Claude
  Code 信任對話框第 2 項是 **No, exit**。實例：2026-08-28 15:29 手機 `/new`
  開的分頁，15:32 丟進去的任務沒有殘留、沒開回合（正是打進對話框的樣子），
  3 分鐘後 tmux session 直接消失，任務石沉大海。現在**每個分頁的第一次注入**
  前會先確認畫面不是卡在啟動對話框（`startup_dialog_blocking`），是的話不注入、
  清掉 👀 並回報原因，請使用者點掉對話框再重發；確認安全過一次就記住
  （`slot.ready_confirmed`），之後不再 capture-pane。
  刻意做成**偵測危險狀態**而不是「偵測就緒」：既有的 `_AI_READY_RE` 對 Claude
  Code 2.x 的 `❯` composer 根本配不到（實測 9 個分頁有 5 個被判成沒就緒），
  拿它當閘門會把正常分頁的訊息全擋死。偵測不到危險就放行 ＝ 退回舊行為。

回歸測試：新增 `tests_tg_ready_gate.py`（8 案例）。全套 33 檔綠。
main.py 有動 → 需 `sfctl restart`。

## v0.29.47 (2026-08-28)

### Fixes
- **分頁在你手上死掉，訊息被靜默改送到別的分頁**。分頁沒了（CLI 退出／被關）
  時，bridge 會把使用者的 active 指到第一格——但**完全不講**。純手機操作時
  你只看得到 👀／🫡，下一則工作指令就悄悄落進別的分頁。實例：2026-08-28
  15:29 開的新分頁 3 分鐘後消失，15:34 丟的「台壽展場案 API 規格」任務跑進
  「雜事」，維護者是 `/10` 打不開才發現分頁早就不在。現在改指之前會先發一則
  「⚠ 分頁『X』已經結束，之後的訊息會送到『Y』(/N)」並附上現在的編號表；
  通知走背景 thread（呼叫端還握著 slot 鎖，tg_api 可能卡 35s）。

- **`/N` 打錯只回一句英文死巷子**。編號是位置制，分頁關掉後面全部往前遞補，
  聊天室裡舊的 `/list` 立刻過期。原本回
  「Invalid session number. Use /list to see available sessions.」——手機上等於
  還要再敲一次指令才知道能選什麼。現在直接附上現在的編號表（含目前在哪一格），
  一則訊息就能改點。順帶把送出移到 slot 鎖之外（`_slot_menu_text` 自己要拿同
  一把非 reentrant 的鎖，原地呼叫會死鎖）。

- **分頁消失後查不出死因**。tmux session 一沒現場就全蒸發。移除 slot 前先把
  最後 8 行畫面寫進 `/tmp/shellframe_bridge.log`（`[slot-gone]`），下次有得查。

回歸測試：新增 `tests_tg_slot_gone.py`（5 案例）。全套 33 檔綠。
只動 `bridge_telegram.py` → `sfctl reload` 即生效。

## v0.29.46 (2026-08-26)

### Fixes
- **「⚠ 無法確認訊息已送進『X』」一直誤報 —— bridge 的虛擬終端比真實
  終端高，看到的是殘影不是現在的畫面**。每個 slot 有自己的 pyte 螢幕，
  送達驗證／busy guard／卡住偵測全靠它讀「現在畫面尾端」（`_live_tail`）。
  但螢幕寫死 200x50，真實 pane 常是 101x31 —— CLI 只重畫 viewport 內的
  列，**viewport 以下那些列永遠停在上一次被畫到的內容**。於是
  `_live_tail` 取最後幾行非空列時，撈到的是幾小時前的殘影
  （`✻ Worked for 3m 50s · done 12:49 PM`），永遠配不到 `esc to interrupt`：

  - 送達驗證兩個強訊號（turn footer／新 extraction）全瞎 → 每則訊息都在
    8s 窗＋45s 延遲判定後噴「無法確認」，而訊息其實好好地送進去了。
  - busy guard 同樣瞎掉 → 對方回合進行中照樣注入（會打斷上一個回合）。
  - marker fallback 的 `turn_ended` 恆為 True。

  修法：slot 的 pyte 螢幕**高度對齊真實 PTY**（`register_session` 帶
  `cols`/`rows`，`App.resize` 改視窗大小時同步呼叫新的
  `resize_session()`）。不用 pyte 自己的 `screen.resize()`——它縮列是從
  **上面**砍（50→31 保留第 19~49 列），等於把 live viewport 丟掉、殘影
  留著，剛好相反；改成換一張新螢幕並把 scrollback 搬過去，SIGWINCH 之後
  CLI 會自己重畫。寬度仍保持寬鬆（≥200），因為 CLI 早就照真實寬度折行了，
  虛擬螢幕比較窄反而會多折。高度不明／荒謬時退回舊的 50 列。
  `_live_tail` 取樣窗同時由 6 行放寬到 10 行：footer 區在 tmux 狀態列 +
  「bypass permissions」提示 + composer 上下框線 + 輸入行之後，spinner
  剛好卡在第 6 行，任何一條額外 chrome（`✔ Update installed`）就把它擠掉。

- **模型把 `[[TG_REPLY_x]]` 寫成 `<<TG_REPLY_x>>` 就整條分頁不再自動轉發**。
  這個筆誤有黏性：同一個 session 寫過一次，往後每回合都照抄自己上一輪，於是
  該分頁**每一則**回覆都配不到 marker，只能等 30s fallback 兜底——體感是
  「回覆解析不到、要自己 /fetch」。token 是 8 位 hex 亂數，換個括號不可能撞
  到別的東西，所以抽取前先把 `<<…>>` 正規化回 `[[…]]`（`normalize_reply_markers`），
  殘留 token 的清理正則也一併吃這個別名。實例：2026-08-26「雜事」分頁連 5
  回合全是 `<<>>`，一次都沒自動轉發。回歸測試：`tests_tg_marker_alias.py`（5 案例）。

  回歸測試：新增 `tests_tg_screen_geometry.py`（6 案例，含釘住 pyte 縮列
  語意那條）。全套 31 檔綠。main.py 有動 → 需 `sfctl restart`。維護者 2026-08-26 回報（「雜事」分頁連續 6 則全誤報）。

## v0.29.45 (2026-08-25)

### Features
- **5 小時配速顯示**。原本只有每週配速，程式註解的理由是「5h 窗口重置太
  頻繁，配速沒意義」——但撞 5 小時上限時，「這一輪燒太快嗎」才是最急的
  問題。現在兩個窗口各自在自己的用量後面顯示 `pc`：
  `5h 32%｜pc 48%▼｜wk 58%｜pc 46%▲`。
  `_usagePace()` 本來就是通用的，缺 `reset_epoch`／`window_minutes` 時回
  null，所以來源沒提供 5h 配速資料就自動不顯示，不會編造數字。
  順帶修掉配速文案寫死「天」的問題：5 小時窗口原本會顯示「週期第 0.1／0 天」，
  現在依窗口長度自動切換小時／天（≤24 小時用小時）。兩個 `pc` 標籤同名，
  tooltip 加上「— 5 小時配速 —」「— 每週配速 —」分隔以免混淆。

- **`//` 逃逸前綴：把同名 slash 指令原文送進分頁**。bridge 攔截了一批跟
  CLI 撞名的指令（`/new` `/model` `/status` `/help` `/close`…），導致要送給
  **分頁**的同名指令永遠到不了。現在 `//new` ＝ 剝一層斜線後**跳過 bridge
  攔截**，以一般 CLI 指令原文送入（`//model opus` 這類帶參數也可以）。
  單獨的 `//` 不視為逃逸（剝完是空指令）；一般文字與既有 `/xxx` 行為皆不變。
  已加入 `/help` 說明。

回歸測試：新增 `tests_tg_slash_escape.py`（6 案例）。全套 31 檔綠。
`//` 逃逸屬 bridge 改動（`sfctl reload` 已生效）；5h 配速在 `web/index.html`，
**需 `sfctl restart`**。

## v0.29.44 (2026-08-24)

### Features
- **支援 pi coding agent**（`@earendil-works/pi-coding-agent` 0.84.3）作為
  provider。registry 加一筆 `pi`（binaries: `pi`、`sf-pi-spark`），因此自動
  取得 AI-tab 語意、`+` 選單 preset、前端 provider 對映——`AI_CLI_TOOLS` 與
  `ai_providers()` 都由 registry 推導，不需改 main.py／前端。
  pi 接的是使用者自訂 provider（地端 vLLM／Ollama，見
  `~/.pi/agent/models.json`），**沒有配額概念**，故 `probe` 明確回 None
  （不顯示水位 pill，不是錯誤）。已驗證不誤判 `pip`／`pipenv`／`python -m pip`。

### Fixes
- **pi 分頁的燈號永遠停在「工作中」、跑完也不變**（回報：蒸餾任務跑完、
  檔案已產出、token 停在 ↑79k 不動，燈號沒反映完成）。
  根因：共用的 `SPINNER_RE` 含 `"↑"`，而 pi 狀態列**固定**顯示
  `↑79k ↓1.5k 14.5%/128k (auto)   spark-vision`——那個 ↑ 是 token 計數、
  不是進度指示，於是每個 pi 分頁都被釘死在 working。
  修法：新增 pi 專屬狀態機 `_pi_status`（照 `_agy_status` 的模式，pi 同樣
  沒有 JSONL transcript），只看兩個訊號——
  1. `⠧ Working...`（braille spinner）＝ 正在跑；
  2. 狀態列 token 數**變動中**＝還在產出；停住超過 6 秒＝這一輪結束
     （done 亮 90 秒後轉 idle）。
  共用路徑對 pi 的誤判由 `test_shared_path_would_misjudge` 釘住，日後共用
  邏輯若改動會提醒重新評估。

### Docs
- registry 補上 pi 的安裝引導（v0.29.40 的機制）：
  `npm i -g @earendil-works/pi-coding-agent`、需 **Node 22.19+**；
  computer-use 擴充 `pi install npm:@injaneity/pi-computer-use`，並註明
  **postinstall 會被 npm allowScripts 擋下、要 approve 才會裝 helper app**；
  另註明 vLLM 相容開關 `compat.supportsDeveloperRole=false`。

回歸測試：新增 `tests_pi_provider.py`（6 案例）。全套 30 檔綠。
**生效需 `sfctl restart`**（`usage_probe.py`／`agent_status.py`／`main.py`）。

## v0.29.43 (2026-08-23)

### Fixes
- **修好 SparkAgent 分頁「逐條回覆 wrapper 規則」**：TG 每回合注入的 preamble
  （回覆標記、手機格式、自我修改說明、協作規則）會被 sparkagent 當成 7-9 則
  獨立使用者訊息，各自跑一次模型、各回一則「收到，已設定」，維護者真正的問題
  排到最後才處理。**與模型智能無關**——模型從沒拿到完整 preamble，每次只看到
  一塊碎片。
  - 根因有兩層：① sparkagent 的 channel 是 `input()` 逐行 REPL，不解析我們送的
    bracketed paste（`ESC[200~…ESC[201~`），payload 每個換行都 submit 一次；
    ② 就算整段進得去，它也沒有 system prompt 管道，指示與使用者訊息長得一模一樣。
  - 本側修法：對 line-oriented agent（`system_directive_agents`，預設
    `sparkagent`）把 payload 中「使用者訊息之前的所有指示」包成
    `<<<SF:SYSTEM>>>…<<<SF:/SYSTEM>>>`，讓對方能把它導進 system prompt、把 turn
    留給真正的問題。**claude / codex / shell 分頁完全不受影響**（gate 看啟動指令），
    純轉發訊息也不會長出標記。
  - 標記字串一併記進 `slot.sent_texts`，echo filter／前綴剝除照舊運作。
  - sparkagent 側的對應修法（bracketed paste 組裝 + directive→system）在
    `h2ocloud/sparkagent`：新增 `input_stream.py`、`directives.py`，17 個新測試。
  - 新測試 `tests_system_directive.py`（6 項）；端到端實測：13 行的真實 payload
    從「7 個 turn」變成 **1 個 turn**。

## v0.29.42 (2026-08-23)

### Changes
- **新增 SparkAgent preset**（`sf-sparkagent`）：`+` 選單開得出來的地端 agent
  harness。模型跑 190 的 Ornith-1.5-35B-A3B-Abliterated（經 LiteLLM gateway 別名
  `spark-main`），agent 進程留在 Mac，所以檔案／shell 工具碰得到這台的東西。
  smoke test 全過：tool calling、檔案讀寫、deny-list 阻擋危險指令都實測過。
- **`sf-codex` 會載入 `~/.codex/spark.env`**（gateway key，該檔不進版控），Codex
  側的 provider／profile 接線可用。但**不建議拿 Codex 跑地端**：同一顆模型，
  Codex 每次請求送 368KB／約 100K tokens（24 個 tool 定義就佔 342KB），塞不進
  65K context；就算把 `max-model-len` 開到 131072 裝得下，每輪 prefill 100K 在
  GB10 上也要好幾分鐘。這也是為什麼 harness 選 sparkagent。

## v0.29.41 (2026-08-20)

### Fixes
- **「登入已失效」是誤判**（回報：agy 明明登入了也能用，面板卻說沒登入；codex 也
  一樣）。兩個獨立原因：
  1. **快照憑證過期 ≠ 帳號不能用**。每個 CLI 都會自己用 refresh token 換新的
     access token，ShellFrame 手上的 profile 快照過期時，那個帳號通常還好用得很
     ——過期的只是「我們讀用量的能力」。實測本機三個 codex profile 有兩個 access
     token 已過期、但全都還有 refresh token，而 CLI 正常運作。訊息從「登入已失效，
     請重新登入」改成「這個帳號的快照憑證過期，查不到用量（帳號本身可能還好用：
     切到它跑一次，或按『重新整理已登入』）」，error code 也改為 `snapshot_stale`。
  2. **前端拿不到某 provider 的資料時，不再假裝「未登入」**。常見成因是版本錯配：
     app 更新後網頁重載成新版、但 Python 端還是舊 process，於是它還不認識新
     provider。現在會直接說「後端未回報——通常是更新後 Python 端還沒重啟，不是
     登入問題」，而不是把人送去重新登入。


## v0.29.40 (2026-08-20)

### Fixes
- **未安裝的 CLI 不再開出一個秒死的分頁**（回報：另一台更新後 `+` 選單預設就有
  Antigravity，一點就壞）。內建 preset 對還沒安裝的 CLI 會開出 command not found
  的分頁，看起來像 ShellFrame 壞了。現在開 AI 分頁前先確認執行檔存在，缺少時改
  跳安裝引導：官方安裝指令、說明、「看官方文件」與**「在新分頁安裝」**（指令在
  真分頁裡跑，看得到、可中斷）。三個 provider 的安裝資訊都在 registry 裡，新增
  provider 時一併帶上即可。
- **狀態轉移失效（`_debounce`）**：候選預設值寫成 `(state, now)`，於是
  `cand != state` 永遠不成立、pending 從不寫入、每次呼叫都把 first 重設成 now →
  除了第一次判定，**任何狀態轉移都不會生效**（狀態點會凍在首次判定）。改成候選
  預設 None。影響 claude/codex 的側欄狀態點。
- **執行檔查找不再只看 PATH**：GUI 啟動的 app 拿到的是精簡 PATH，`~/.local/bin`
  通常不在裡面，會把已安裝的 CLI 誤判成沒裝。改為 PATH ＋ 常見安裝目錄。

### Features
- **AI 帳號面板列出所有 provider**，包含只有單一登入帳號的（agy → 一個 Google
  帳號）：顯示帳號與水位／配速，但不顯示切換鍵（沒有可切的東西）。未安裝的
  provider 顯示「未安裝」＋一鍵安裝，而不是一列空白。區塊標題與順序都來自
  registry，新增 provider 無需改前端。
- `install.sh` 結尾盤點三個 AI CLI 的安裝狀態，缺的列出安裝指令。

## v0.29.39 (2026-08-20)

### Features
- **支援 Antigravity CLI（`agy`）**，並把「再多支援一個 AI CLI」變成一處註冊。
  - 用量／配速：`agy` 沒有本機額度快照，改跑它自己的 print-mode `/usage`
    （`--output-format json`）。它回的是**剩餘**比例，UI 顯示的是已用，所以會反轉；
    只有週窗口，因此膠囊的 `5h` 段自動消失、`wk` + `pc` 照常。額度分兩組
    （Gemini／Claude+GPT），膠囊跟主要那組、tooltip 與帳號面板列出全部。
    spawn 一顆大 binary 不便宜，因此有 TTL 120s 快取與 20s 退避。
  - **Provider registry**：`usage_probe.PROVIDER_SPECS` 成為單一事實來源，
    `detect_ai()`、`probe_data()`、`main.AI_CLI_TOOLS` 都改由表驅動；前端透過新的
    `Api.ai_providers()` 取得對應表，不再自己寫死 provider 名稱。新增一個 CLI
    = 一筆 registry ＋ 兩個 adapter，`main.py` 與 `web/index.html` 都不用動。
  - `+` 選單多一個 Antigravity preset。預設 preset 的補齊改用 seen-list
    （`_default_ai_presets_offered`），所以之後追加 provider 時既有安裝會自動拿到
    新 preset，不必再寫一次 migration；使用者刪掉的 preset 仍然不會被塞回來。
    preset 指令刻意不寫死模型（各家 CLI 自己記住選擇，寫死很快過期）。
  - 新增 `docs/adding-a-provider.md`：資料合約、配速 metadata、錯誤要講原因不推估、
    快取責任、選用的帳號／狀態偵測擴充點、檢查清單。

### Fixes
- **膠囊初始渲染撞 TDZ**：provider 對應表以 `let` 宣告在膠囊程式碼之後，而膠囊在
  init 階段就會畫一次 → `Cannot access 'AI_PROVIDERS' before initialization`。
  宣告移到使用點之前。（UI 截圖驗收時抓到。）
- 用量讀數為 stale 時保留「本次失敗原因」：CLI 被移除或登出，不該看起來只是
  一次更新失敗。

- **agy 分頁也有狀態點與模型徽章**。agy 沒有 JSONL transcript，改讀它自己的
  資料：每個對話是一個 SQLite（`conversations/<id>.db`），`steps` 最後一筆的
  `status` 就是真相（實測 8＝執行中、3＝完成）；分頁對應靠執行中的 process
  open 著的 `presence/<id>.lock`（跟 codex 用 lsof 認 rollout 同一手法，兩個
  同目錄的 agy 分頁才不會互相認錯）。模型徽章取該 process log 最後宣告的
  `label="Gemini 3.7 Flash (High)"`，會跟著 session 內的 `/model` 切換。
  - 「剛完成」用**距離最後一次看到執行中**的窗口判定，不用檔案 mtime：agy
    活著時會持續碰 `-wal`，用 mtime 會讓閒置分頁永遠顯示剛完成。
  - 還停在歡迎畫面、沒有 conversation 的新分頁會退回共用的 screen-only 判定，
    不會變成 unknown 而從 feed 消失。

### Housekeeping
- `usage_probe.py` 移除範例 docstring 內的個人 email／組織名，改用
  `you@example.com`；一處 migration 註解改成中性描述。
- **開源可讀性：註解／docstring／文件內的個人識別改為中性稱呼**（維護者、
  使用者、回報＋日期），涵蓋 34 個檔案。刻意不動三類：`CHANGELOG` 歷史、
  會注入給 agent 的內容（`INIT_PROMPT.md`、`main.py` 的 preamble 字串）、
  README 的作者署名。行為零改動，14 套回歸測試全過。

### Known issues
- `agent_status._debounce()` 的候選預設值取 `(state, now)`，導致 pending 永遠
  不寫入、`now - first` 恆為 0 → 「已建立狀態 → 新狀態」的轉移不會生效
  （影響 claude/codex 的狀態點）。已在函式 docstring 標註；修它會改變核心
  行為，另開一輪驗證處理。agy 的狀態不走這條路徑。

## v0.29.38 (2026-08-20)

### Performance
- **marker 掃描是目前唯一的 CPU 熱點，81% 的掃描是註定失敗的白工**。
  實測現況（14 slots）：`extract_marker` **473ms/60s、每分鐘 115 次、每次
  4.1ms**——其他所有 phase 加起來才 ~50ms。深挖 production log：
  **405 筆 `rawlen=120000`**（buffer 幾乎永遠撐滿上限）、
  **358 筆 `raw=False` vs 83 筆 `raw=True`**——也就是 81% 的掃描在
  「marker 根本不在 buffer」的情況下，仍對 120KB 跑完整 `strip_ansi`
  （單獨實測 **31ms**）。三個常用分頁（研究報告 / ShellFrame開發 / 救援總控）
  都持續踩在這個循環裡。
  兩個修法（效能與功能同一個根因）：
  1. **截斷時保住 start marker**：長輸出（研究報告、長 build log）把 buffer
     撐到上限後，開頭的 `[[TG_REPLY_x]]` 被擠出去 → span 永遠配不出來 →
     每則回覆都得等 30s fallback 兜底（維護者「愛回不回」的其中一條路徑），
     而且每次重掃都註定失敗。現在截斷時把 start marker 接回保留區開頭。
  2. **便宜預檢**：掃描前先用 `str.find` 確認 marker 痕跡（實測 **0.016ms**，
     比全量掃描快 **1900 倍**）。找不到時**不直接放棄**——改用較長節流
     （15s）而非跳過，並保留第二層（尾端 16KB 的 `strip_ansi`，4.2ms）作為
     ANSI 打斷 marker 時的救援，避免為了省 CPU 而製造新的靜默丟訊。
  回歸測試：新增 `tests_tg_marker_perf.py`（4 案例，含「預檢不得破壞真實抽取」
  與「ANSI 夾住 marker 仍要救回」）；`tests_tg_marker_throttle.py` 測資更新
  （原本用無 marker 的字串，現在會被預檢提前攔下，改用未閉合 start marker
  才測得到節流與 dirty gate）。

## v0.29.37 (2026-08-20)

### Features
- **用量膠囊加 token 配速**（維護者提：「wk 29%」單看不知道是快還是慢）。把週額度
  平均攤到整個窗口，算出「今天應該用到幾 % 才會剛好用完」，疊在 `wk` 右邊：

  ```
  5h 7%  wk 30%  pc 32%      剛好在配速線上（綠）
  5h 7%  wk 45%  pc 32%▲     燒太快（黃；超過 +25% 轉紅）
  5h 7%  wk 10%  pc 32%▼     額度用不完（藍）
  ```

  `pc` 是**目標值**，所以顏色看的是「偏離」而不是「水位」——藍色不是警告，是
  「額度沒用完」，月費制方案用不完也是浪費。滑過膠囊的 tooltip 給可行動的說法：
  `週期第 2.3／7 天 → 今天應累積 32%`、`目前 30%（落後 2%，約 0.1 天的量）`、
  `照這個速度會在重置前用完（剩 4.7 天）`。AI 帳號面板的每個帳號也各有一條。
  實例：codex team 帳號 7d **92%** 看起來很紅，但週期已到第 6 天、配速線 84%，
  其實只超前 8%——這正是單看百分比會誤判的情況。
  - 可關閉：`settings.usage_pace`（預設開）。設定頁一個 toggle，帳號面板 footer
    再一顆「配速 開／關」——膠囊是看到它的地方，就地能關比翻設定好。
  - 後端：窗口的 reset 時刻與長度原本被格式化成字串就丟了。`_pace_meta()` 用
    side-channel dict 帶 reset epoch + `window_minutes`，**刻意不動
    `(pct, reset)` tuple**（那個形狀在十幾處被解包、又存在磁碟快取裡，舊快取必須
    照樣載入，只是沒有配速）。claude live／profile／legacy script、codex
    rollout／sqlite／app-server 六條路徑都帶；codex 直接用它自己回報的
    `windowDurationMins`（Team 的 primary 就是 10080 分＝一週，不能假設
    primary=5h）。
  - 資料不足或不合理就不顯示配速、不推估：舊快取沒有 meta、stale 讀數的 reset
    已經過去、reset 比整個窗口還遠，三種都不畫。

### Fixes
- **設定關不掉配速**（開發中自己抓到）：`_paceEnabled()` 一開始讀 `window.config`，
  但 `config` 是模組變數（`let config = null`），永遠讀不到 → 開關無效。另外
  config 是 async 載入，膠囊可能在設定到位前先畫一次，`loadConfig()` 完成後補一次
  重繪。

### Tests
- `tests_usage_probe.py` 新增 4 案（配速 meta／跨重啟存活／舊版快取無 meta 不炸／
  codex app-server 回報真實窗口長度）；五套回歸全過。
- 前端配速邊界用 node 驗 6 案；UI 用 Playwright 載真實 `web/index.html` 截 5 種
  膠囊狀態＋帳號面板。

## v0.29.36 (2026-08-19)

### Fixes
- **同一則回覆一直重送（維護者：「對話1跳針」）＋ 假的「送出失敗」警告**。
  根因是 v0.29.34 P0-3 commit 模型的副作用：送出判定是**整批**的
  （任一收件人失敗＝整批 FAILED → 不進去重集合 → 下一輪重抽重送）。
  實際 log：兩個 chat（`5582043292`、`5617995311`）**把 bot 封鎖了**
  （HTTP 403 `bot was blocked by the user`），於是每輪 flush 都判定失敗，維護者這個唯一收得到的收件人**每輪都再收一次同樣內容**，還附一則
  「回覆送出失敗」警告——而他其實每次都收到了。
  修法：改為**逐收件人判定**——
  1. `_send_text_checked` 回傳加 `permanent` 旗標，辨識永久性投遞失敗
     （bot was blocked / user is deactivated / chat not found / bot was kicked
     / no rights to send / PEER_ID_INVALID）；
  2. commit 條件改成「**有人真的收到**，或雖然沒人收到但**全是永久失敗**」
     ——只有可重試的失敗（429／逾時／網路）才 rollback 重抽；
  3. 永久失效的 chat 直接從 `_user_chat`／`_user_active` 移除，不再每則回覆
     都撞一次 403，失敗警告也不再發給收不到的人。
  回歸測試：新增 `tests_tg_blocked_chat.py`（5 案例）＋
  `tests_tg_send_commit.py` 補端對端案例；並更正該檔測資——原本用
  `chat not found` 當「可重試的硬失敗」，那其實是永久性失敗。

<<<\n[[TG_REPLY_ab]]真正的回覆[[/TG_REPLY_ab]]"`
  → 修前 `strip_ansi` 只剩 `'print(1)'`，回覆與 marker 全被丟棄；修後整段保留、
  `_pick_marker_reply` 抽得到「真正的回覆」。
  致命之處在於它**會自我維持**：marker 永遠抽不到 → `marker_forwarded` 永遠
  False → 走 fallback → fallback 又要求 turn 已結束 → 也永遠不成立 →
  **完全靜默、零 log 的永久失聯**。修法：刪除該三行（`tests_tg_marker_hijack.py`
  另有一條測試盯著它別被加回來）。

- **P0-2 pyte history deque 飽和後 scrollback 永久失明**。
  `screen.history.top` 是 `deque(maxlen=800)`，**滿了之後 `len()` 恆為 800**，
  舊行從左邊被擠掉、長度不變 → `_history_offset` 卡死在 800，`> hlen` 為假
  （相等）、`< hlen` 也為假 → 該 slot 此後**永遠不再掃描任何 scrollback**，
  只剩 50 行 live screen 可抽，兩次 flush tick 之間捲過去的回覆永久遺失。
  長壽命分頁（跑了兩天的 s87）必然早就飽和。實測：飽和後舊邏輯掃到 **0 行**；
  修後改為重掃 history 尾端 64 行，捲走的回覆抽得回來。
  另加一道廉價 dirty gate（history 最後一行的 signature 沒變就跳過），
  實測有新行 0.90ms／gate 命中 0.136ms。

- **P0-3 送 TG 不看回傳值 ＋ 先進去重集合＝回覆永久蒸發**。
  舊碼是 `sent_responses.add(reply)` 在前、`tg_api(... "sendMessage")` 在後，
  而 `tg_api` 把 429 flood-wait / 400 / 逾時 / DNS 全部轉成 `{"ok": False}`
  回傳值，呼叫端**完全不看**（全 repo grep `429|retry_after` 零命中）。
  於是一次失敗＝永不重送、也永不會被重新抽取。改成 commit 模型：抽取階段只
  決定「要送什麼」，所有不可逆副作用（進去重集合、清 `pending_raw`、關 marker
  監聽、`marker_forwarded`）一律等 `ok:true` 才套用；新增 `_send_text_checked()`
  檢查回傳值，短 flood-wait（≤5s）就地重試一次，長的回報秒數由呼叫端退避後
  重抽——**不在 flush loop 裡 sleep**（那是所有分頁共用的單一執行緒）。
  最終失敗會告訴使用者「內容沒有遺失，用 /fetch 重取」。

- **P0-4 沒有收件人的回覆照樣被標記成已送**。`target_chats` 為空集合時
  （沒有使用者 active、又不是 `_slot_order[0]` 的 master 派工 worker 分頁），
  舊版仍把回覆加進 `sent_responses` 然後送給零個人，之後連 `/fetch` 都救不回。
  修後：log `[flush] <sid> no target chat, keep for /fetch`，不污染去重集合、
  不清 `pending_raw`。

- **P1-13 去重集合的迭代順序不確定**（P0-2 的前置條件，SA 標為順序不可顛倒）。
  `sent_responses` 原本是 plain `set`，兩處行為因此隨機：溢位裁切
  `set(list(s)[-100:])` 的「最後 100 筆」是任意順序（可能丟掉最新回覆、留下
  遠古的）；superset/subset 迴圈兩種關係各自 `break`，先撞到誰看運氣。
  P0-2 會**刻意重掃 scrollback**、完全依賴這個集合擋重複，不修就會把「靜默
  丟訊」換成「隨機重複洗版」。改用保序容器 `_OrderedSet`（dict 為底，O(1)
  membership），並明訂優先序：**已被送過的內容包含 → 不重送**。
  `sfctl reload` 後 main.py 會還原成 plain set，`_extract_new_text` 開頭一次
  isinstance 把它正規化回來。

- **P0-5 語音檔下載失敗完全無 `else`**：TG `getFile` 沒拿到檔就一路往下，
  最後被「沒文字也沒檔案」那條 `return` 靜默吃掉。現在明講並中止。
- **P0-6 `_inject()` 的 `write_fn` 無例外保護**：pane 已關 / tmux session 不在
  時 OSError 直接逸散到 daemon thread（traceback 只噴 stderr、log 一行都沒有）。
  現在包 try/except，失敗 → 清空回執 ＋「寫入失敗，訊息沒有送出」。
- **P0-7 offset 早於 enqueue 儲存**：`reload`/`restart` 時還躺在 `_update_queue`
  裡的訊息永遠不會再被 `getUpdates` 取回。改動 offset 時序會把重啟迴圈的舊病
  帶回來，所以改為純加法：`stop()` 把殘留佇列落盤（`tg_pending.json`，上限 20
  則），下次啟動重播並濾掉自我重啟指令。
- **P0-8 busy guard 等滿 120s 仍強制注入卻不告知**：可能打斷對方上一個回合，
  使用者完全不知情。現在保持 👀 ＋ 明講「已強制送入，可能打斷它上一個回合」。

### Features — 讓狀態可見

- **送達回執（reaction 狀態機）**：T0 收下 👀 → T1 確認送進 session 🫡 →
  T2 送不進去則清空 reaction ＋ 既有文字警告。用 reaction 而非新訊息：就地標
  在使用者自己那則訊息上，不佔對話列、不推播、不洗版；bot 只能有一個 reaction、
  後設的取代先設的，天生就是狀態機。補掉了「注入成功到 8s 排隊通知之間完全
  靜默」的空窗（8s 的「⏳ 排隊中」文字保留，它帶有 reaction 表達不了的資訊）。
  ⚠ **✅ 不在 Telegram 的 reaction 白名單**——2026-08-17 用真實 bot token 實測：
  👀 `ok` / 🫡 `ok` / 👌 `ok` / ✅ `400 REACTION_INVALID`。
  單則失敗不退回文字（沒有告知價值、只製造雜訊）；同一 chat 連續失敗 3 次才
  一次性告知並全域停用（T2 的實質告警是文字，不受此開關影響）。
  所有 reaction 呼叫 `timeout=5` 且一律在 `slot.write_lock` 之外。

- **長回合心跳**：主 agent 在等背景 sub 時，Claude Code 的 footer 一直掛著
  `esc to interrupt` → turn 永遠不算結束 → 30s fallback 永遠不觸發，背景任務
  跑數小時這條路就靜默數小時（回報說的「愛回不回」）。現在 3 分鐘沒消息就
  開始回報進度：
  ```
  ⏳「調研者」還在跑 · 已 8 分 12 秒
     working · Delegating — wiring _parse_presets
     在等 1 個背景 agent（↓933.7k tokens）
     /11 切過去看 · /fetch 抓現況 · /quiet 這輪別再提醒
  ```
  **零新增掃描**：閘門掛既有 2s slow_tick，只讀 slot 上的 float/bool 欄位；
  狀態行讀 main.py 那條 0.6s monitor thread 已經算好的 `StatusTracker` 快取
  （新增 `StatusTracker.last_result(sid)` 唯讀介面，**不觸發** transcript 解析）。
  防洗版四道：180s 首次門檻、指數退避 300→1800s（3min→8min→15.5min→26.75min→
  之後每 30min）、內容 hash 去重、`/quiet` 出口。回覆送出或新訊息即自動重置。
  拿不到狀態資料（transcript 還沒落盤、shell 分頁、資料超過 30s 沒更新）就降級
  成只印第一行，**絕不因此不發**。

- **`/quiet`（`/安靜`）**：對當前 active 分頁的這一個 epoch 停發心跳，下一則
  訊息自動恢復。已加進 TG 指令選單。

- **超長回合的「進行中預覽」**：等滿 15 分鐘且本 epoch 仍零回覆時，心跳附帶
  畫面上最後一個 AI block 的前 300 字，並標明「進行中預覽（非最終回覆）」。
  安全條件全部實作：**絕不進 `sent_responses`**（否則真回覆來時被永久壓制——
  這是唯一不可退讓的一條）、不動 marker 監聽、每 epoch 上限 2 次、
  `_feed_gen` dirty gate、內容相同就跳過。

- **`[marker-miss]` 診斷**（僅 `perf_debug=on`）：marker 抽取失敗時記錄
  `raw=` / `clean=`，用來分辨「`strip_ansi` 吃掉」「120KB 驅逐或模型沒吐」
  「span 配對問題」三種假說。關閉時只有一次 bool 判斷。

### 效能

紅線：穩態 CPU ≤25%、新增 phase 單項 ≤50ms/60s、全部 phase 合計 ≤150ms/60s。

| 新增項目 | 實測 | 佔紅線 |
|---|---|---|
| `heartbeat_gate`（13 slots × 30 次/分） | **0.038 ms/60s** | 0.08% |
| `preview_peek` | 每 epoch ≤2 次、間隔 ≥15 分 | ~0 |
| `extract_new_text` 飽和重掃（有新行） | 0.90 ms/次 | — |
| 同上，dirty gate 命中（無新行） | 0.136 ms/次 | — |
| P0-1 省下的 DOTALL 全 buffer 搜尋 | −0.020 ms/次 marker scan | — |

送達回執與心跳的網路 I/O **全部在背景 thread**，`_flush_loop` 同步路徑成本為 0。
`_send_text_checked` 的就地重試上限 5s，長 flood-wait 不在 flush loop 裡 sleep。

### 測試

新增 5 個測試檔（全套 21 → **26 檔，全綠**）：
`tests_tg_marker_hijack.py`（P0-1，用維護者的實測輸入當測資）、
`tests_tg_history_saturation.py`（P0-2，含「舊邏輯掃到 0 行」的失明重現）、
`tests_tg_send_commit.py`（P0-3/P0-4/P1-13，真的跑一輪 `_flush_loop`）、
`tests_tg_reaction.py`（回執狀態機、失敗 3 次停用）、
`tests_tg_heartbeat.py`（門檻、退避序列、`/quiet`、預覽 S1–S6）。

### 生效方式

`bridge_telegram.py` 走 `sfctl reload` 即生效。
`main.py` / `agent_status.py` 的改動（`on_agent_status` 注入 ＋
`StatusTracker.last_result`）**需要 `sfctl restart`**；未重啟前心跳自動降級成
只印第一行，其餘功能不受影響。

## v0.29.32 (2026-08-15)

### Fixes
- **側欄分頁點兩下改名被誤判成拖曳**（回報回報：最末端的分頁尤其中招）。
  根因：拖曳門檻只有 5px 且不分方向，雙擊的**第二次按下**只要手抖幾 px 就
  進入 drag → 改名永遠觸發不了。修法：(1) 雙擊時間窗（400ms）內的第二次
  mousedown **完全不啟動拖曳**（意圖是改名）；(2) 門檻 5px→9px 且要求
  「垂直位移為主」（dy≥9 且 dy>dx）——側欄 reorder 本來就是上下移動，點擊
  時的水平抖動不該觸發。生效需 `sfctl restart`（web/index.html）。

### Changes
- **眼鏡外掛（rokid-bridge）卸載**：從設定頁卸載無效的根因是
  `com.h2ocloud.rokid-bridge-listener` LaunchAgent 帶 `KeepAlive=true`——
  plugin 停用完全不碰它，listener 一直被系統復活。已 unload + plist 備份到
  `.disabled-agents/`、進程確認消失、config 的 plugins installed/enabled
  移除 rokid-bridge。根治（讓卸載自動清 side-effect）見
  `docs/plugin-sdk-plan.md`。

## v0.29.31 (2026-08-12)

### Features
- **AI 帳號面板列出每個帳號的用量**（維護者提：點開要看到全部帳號的用量）。原本面板
  只有帳號名稱與切換按鈕，用量只有右上膠囊那一個（目前 tab 的帳號）。現在每個已登入
  帳號各自帶一條水位：`5h 20% 重置 15:50　7d 17% 重置 08-18 03:59　查詢 12:30`，
  顏色沿用膠囊的門檻（60% 黃、85% 紅），footer 多一顆「重新查用量」。
  面板先畫帳號、用量非同步補上，開窗不會被網路卡住。
  - 後端 `Api.account_usage_all(refresh)` 平行查各 provider×帳號（各自 token /
    `CODEX_HOME`，彼此無共用額度）；`usage_probe.account_usage()` 做 per-account
    快取（TTL 120s）與 per-provider 退避（claude 60s、codex 10s）。**實測 Claude
    OAuth usage API 對同一 token 一分鐘內重查就回 429**，所以面板一次列 N 個帳號必須
    靠快取，不能每次現查。
  - 目前登入中的那個帳號改走膠囊的共享快取，避免面板與膠囊互相把同一顆 token 打到 429。
  - 查不到就講原因、不推估：profile token 過期 → 「請重新登入這個帳號」（本地先看
    `expiresAt`，不拿必然失敗的 token 去吃額度）、429 → 「稍後重試」、退避窗內重播上次
    真因而不是「剛查過」。有舊讀數就顯示並標 ⚠ stale。Codex Team 只有週限額，5h 顯示
    「—」而不是錯誤。

### Fixes
- **右上膠囊「用量 查不到」**：v0.29.30 起舊 tab 的 `account_refs` 會是 None，而
  `env_for(provider, None)` 直接 TypeError（`PosixPath / None`），整個
  `tab_usage_brief` 掛掉——膠囊因此對所有 reattach 的舊 tab 都顯示查不到。`env_for`
  對空 ref 回 `{}`，呼叫端改吃 provider 全域憑證。
- **用量快取檔互相清掉**：膠囊（`claude` 區）與帳號面板（`accounts` 區）共用
  `usage_cache.json`，原本整檔覆寫；改為 read-modify-write。

### Tests
- `tests_usage_probe.py` 新增 8 案（過期 token 不打 API／退避窗重播真因／per-account
  快取不互相污染／429 回該帳號自己的 stale／目前帳號共用膠囊快取／codex 週限額不算錯／
  兩區快取共存）、`tests_accounts.py` 新增 `env_for` 空 ref；六套回歸全過。
- UI 驗收：Playwright 載入真實 `web/index.html` + 真實後端資料截圖（實機四個帳號、
  合成邊界案例各一張）。

## v0.29.30 (2026-08-11)

### Fixes
- **舊 tab 切換帳號被鎖住**：舊 tmux session 沒有 `SF_ACCOUNT_*` runtime marker 時，不再把 manifest 快照誤當成實際帳號；切換 Team/Pro 會重新注入正確 profile，避免第四個 tab 顯示 Pro 卻把 Team 按鈕鎖住。

## v0.29.29 (2026-08-08)

### Features
- **語音「Apply 確認」可關閉**（維護者提：TG 傳語音每次都跳 ✅ Apply 很煩，
  記得有開關但其實沒接線）。原本語音轉錄後**一律**泊住＋跳 Apply/Cancel（寫死、
  無設定）。新增 `settings.voice_apply_gate`（預設 True＝維持確認；STT 有誤差時
  較安全），關閉時轉錄完直接把文字自動送進分頁、不再跳 Apply。設定頁 🎙 語音
  轉錄 區新增「語音送出前先確認（Apply）」toggle。維護者的設定已設為關。
  bridge 邏輯 `sfctl reload` 即生效；UI toggle 需 `sfctl restart`。
  回歸測試：`tests_voice_gate.py` 4 案例。

### Fixes
- **Codex 用量查不到**：新增 rollout JSONL 與 app-server 即時 quota 讀取，舊版 SQLite 保留為相容 fallback；token 失效時明確顯示需要重新登入。
- **AI 帳號 profile**：支援 Codex/Claude 的全域與單一 session 帳號指派，session 啟動時套用正確的 profile，面板只顯示安全 metadata、不暴露 credential。
- **Codex 帳號辨識**：用實際 `CODEX_HOME` 讀取帳號與用量，避免不同 profile 共用錯誤的全域資料。

### Tests
- `tests_usage_probe.py`、`tests_accounts.py` 全部通過；Python 編譯與 `git diff --check` 通過。

## v0.29.28 (2026-08-06)

### Fixes
- **側欄模型徽章判讀不準**（維護者 08-06 截圖：tab13 顯示「Opus 4.6 ·
  xhigh」、實際跑「Opus 5 · ultracode」）。三個根因逐一修：
  1. **`/clear` 會在同一個 claude process 裡輪替 session uuid**——spawn 的
     `--session-id` 與 nearest-birth 都釘在舊 transcript，badge 永遠顯示
     /clear 前的模型。修：sf_agent_hook 每個事件本來就帶
     `session_id`/`transcript_path`，`_on_agent_event` 現在存回 Session
     （唯一跟得上輪替的即時真相），解析鏈最優先吃這個 hint。
  2. **`--resume` 是同檔續寫**（birth 是最初建立日，可能一個月前），
     nearest-birth 必錯。修：直接抽 cmd 的 `--resume/--session-id` uuid
     對檔名；nearest-birth 錨點同時從「pane 首個 process」改成「pane 樹裡
     最新啟動的 claude process」（同 pane 退出重開的案例）。
  3. **effort 只讀全域 settings.json 的 effortLevel**——`/effort <level>`
     是 session-only 不寫全域，所有分頁都顯示同一個 xhigh。修：新增
     per-tab transcript 的 `/effort` 痕跡掃描（Set/Kept effort level to X，
     **增量掃描**：標記常離檔尾十幾 MB，固定 tail 窗會漏；首掃 ~30ms、
     之後只掃新增 bytes ~0.1ms），全域值退為 fallback。
  設定頁「Session 顯示模型標籤」改標**（實驗性）**——偵測屬盡力而為，
  不想看可關（`settings.show_model_badge`，原開關就存在）。
  回歸測試：`tests_agent_status.py` 新增 8 組（34/34）。生效需 `sfctl restart`。

## v0.29.27 (2026-08-05)

### Fixes
- **拖曳檔案沒帶路徑・第二層根因**（v0.29.26 修了 uri-list 缺檔名，維護者重測仍無反應）。js:drop 足跡顯示這次更徹底：`types=["Files"]`——
  **新版 macOS Finder 拖曳放上 pasteboard 的是 file-reference URL
  （`file:///.file/id=…`），WebKit 完全轉不出 text/uri-list**，DOM 端一條
  路徑都拿不到；blob fallback 又靜默失敗（無 onerror 監聽）。
  修法：**繞過 WebKit，後端直讀 macOS drag pasteboard**——新增
  `drag_pasteboard_paths` API（NSPasteboard「Apple CFPasteboard drag」＋
  `NSURL.path()` 把 file-id URL 解回真實路徑，drop 結束後內容仍在，已用
  實案 pptx 驗證解出 `/Users/neux/Downloads/遠東商銀_….pptx`）。前端在
  uri-list 抽不到／不齊時改問 pasteboard，檔名（NFC 正規化）對得上
  `dt.files` 且 `paths_exist` 驗過才注入。blob fallback 補 `onerror`＋
  save 失敗 log（js:drop-blob），不再靜默。
  回歸測試：`tests_drop_paths.py` 增至 6 案例。生效需 `sfctl restart`。

## v0.29.26 (2026-08-05)

### Fixes
- **拖曳檔案進來沒帶上路徑**（維護者 08-05：拖 Finder 檔案毫無反應）。
  debug log 還原真相：drop 有觸發、也有寫入，但注入的是
  `/Users/neux/Downloads/`——**WebKit 對含非 ASCII（CJK）檔名的拖放，
  text/uri-list 可能只給到資料夾、檔名整段消失**（實案：遠東商銀_官網改版
  _AI銜接.pptx）。舊版拿這個壞路徑就 early-return，連本來能救的 blob
  fallback 都到不了 → 體感「沒帶路徑」。
  修復鏈：1) 資料夾結尾的候選用 `dt.files` 的檔名補回完整路徑；2) 新增後端
  `paths_exist` 驗證存在，驗過才注入（保留原始路徑，AI 讀真檔非複本）；
  3) 補不齊（有效路徑數 < 拖入檔案數）→ 退回 blob fallback 存
  `~/.claude/tmp`（帶原檔名）。dataTransfer 快照全部移到 await 之前
  （WebKit 在 await 後會清空 dataTransfer）。
  另新增 `js_debug` API：前端拖放/貼上這類 WebKit 行為差異從此在
  debug log 有足跡（這次查案全靠事後 log 推斷）。
  回歸測試：`tests_drop_paths.py` 4 案例。生效需 `sfctl restart`。

## v0.29.25 (2026-08-05)

### Fixes
- **側欄縮窄時把「命名」擠掉、卻保留重複的模型徽章**（回報回報：應保留
  命名資訊，不是後面重複性高、低識別度的資訊）。根因：`.sb-label` 是
  `flex:1`（會收縮 + ellipsis），但 `.sb-model` 是 `flex-shrink:0`（永不收縮）
  → 窄的時候 label 先被吃掉、模型徽章反而全留（幾乎每列都是 Opus 4.8·xhigh，
  無識別度）。
  修法：`#sidebar-sessions` 設 CSS container query，側欄窄到 ≤215px 先隱藏
  模型徽章、≤165px 再隱藏 TG 徽章與編號，label（flex:1）自動取回空間 →
  縮窄時優先看得到分頁名稱。生效需 `sfctl restart`（web/index.html）。

## v0.29.24 (2026-08-03)

### Fixes
- **版號衝突會讓其他機器偵測不到 update**（回報回報）：`check_update` 原本
  比對 version.json 的 semver（`remote_v > local_v`）。多個並行 session 撞同一
  版號時（近期常發生），舊機器看到 `remote_v == local_v` → 判定沒更新 →
  **永遠不更新**，即使程式碼其實變了。
  修法（根治）：`check_update` 改用 **git commit SHA** 當權威訊號——比對
  `git ls-remote origin main` 的 SHA vs 本機 HEAD，不同就是有更新；版號變純
  顯示用，撞號再也不影響偵測。用 `merge-base --is-ancestor` 免 fetch 排除
  「本機領先遠端」誤報；git 不可用時退回原本的 semver 比對。
  生效需 `sfctl restart`（main.py）；其他機器經一次正常更新後即獲得此邏輯。

### Tooling
- 新增 `scripts/bump_version.py`：取 本機＋origin/main＋tags 的最大 semver
  再 +1，commit 前跑它就拿到「比所有已知版號都大」的號，避免撞號的手動
  renumber。（撞號已不影響偵測，此為避免版號重複的雙保險。）

## v0.29.23 (2026-08-01)

### Fixes
- **剛送出訊息就立刻收到「上一則回覆」的重複**（回報回報）：v0.29.21 的
  follow-up 監聽讓 has_user_msg 保持 True，觸發了 fallback 的一個潛在 bug——
  fallback 的等待時鐘用 `total = now - first_output_time`，但 `first_output_time`
  在忙碌分頁（持續有輸出）會停在很久以前 → `total` 變幾萬秒（log 實測 36902s）→
  `total >= 30` 永遠成立。於是一送新訊息（重置 marker_forwarded、重新開放
  fallback），在新回覆還沒生成前，fallback 就用「畫面上還是上一則回覆」的 peek
  立刻重送 = 重複。
  修法：(1) fallback 時鐘改用 `slot.msg_sent_ts`（這則使用者訊息送出的時刻），
  不再用 stale 的 first_output_time；(2) fallback 內容對 `sent_responses` 去重，
  已送過的回覆絕不重送（直接防線）；(3) 每則新訊息清 pending_raw ＋重置輸出
  時鐘，新 epoch 從乾淨開始。回歸測試：`tests_tg_followup.py` +1。
  純 bridge 改動，`sfctl reload` 生效。

## v0.29.22 (2026-07-27)

### Fixes
- **TG `/effort`／`/model` 明明套用成功卻回「已送出但沒在畫面看到確認」**
  （維護者 07-27：tab13 選 ultracode，畫面實際已顯示
  `Set effort level to ultracode…`、狀態列也變 ultracode）。
  根因：確認回讀用 `_slot_display(slot)[-N:]`——pyte 虛擬螢幕固定 50 列，
  實際終端較矮（~36-44 列）時內容只佔上半部，**尾端切片幾乎全是空白列**，
  確認行（在 composer 上方幾行）永遠不在切片裡。共 4 處同款：
  `_apply_effort_claude`、`_apply_effort_codex` ×2、`/model` picker 確認。
  修法：統一改用 `_live_tail(slot, rows=N)`（先濾空列再取尾端，v0.29.1 就是
  為此而生）。測試 harness 同步拿掉假 `_live_tail` 改走真實實作；
  `tests_tg_effort.py` 新增「確認行＋30 列空白尾」回歸案例。
## v0.29.21 (2026-07-26)

### Fixes
- **Follow-up 連續訊息只回一則、背景 subagent 完成的訊息漏掉**（回報回報）：
  TG-wrap 分頁在**第一則 marker 回覆後就清掉** `expect_marker`/`has_user_msg`，
  之後 AI 再包的 `[[TG_REPLY]]` 訊息（例如「背景 worker 已啟動…好了通知你」
  後，worker 跑完的完成通知）落進 drain 路徑、只做 signal 偵測、**不轉發** →
  體感「只回一則」。
  修法：第一則回覆後**保持 marker 監聽**（不清 expect_marker/markers/
  has_user_msg），AI 之後每包一個「新的」marker block 都轉發一次；去重在
  `_try_marker_extract`（已在 `sent_responses` 的 block 當「沒有新的」、走
  節流等下一個真正的新 block）。只有新使用者訊息才重置 token。新增
  `slot.marker_forwarded` 旗標讓「模型漏 marker」的 fallback 只在整個 epoch
  從未用過 marker 時才觸發（避免對已用 marker 的分頁重送 peek）。
  回歸測試：`tests_tg_followup.py` 5 案例。純 bridge 改動，`sfctl reload` 生效。

## v0.29.20 (2026-07-26)

### Features
- **TG `/effort` — 遠端調 active 分頁的推理深度（claude + codex 統一）**（維護者提）。
  兩邊原生 UX 不同，收斂成一組 TG inline 按鈕：
  - **claude**：原生 `/effort` 是滑桿（low/medium/high/xhigh/max/ultracode）。
    帶參數 `/effort <level>` 會跳 Yes/No 確認——`_apply_effort_claude` 送層級後
    自動答「1」確認，回讀「effort level / thinking with <level>」。
  - **codex**：`/model` →Enter 保留目前模型→「Select Reasoning Level」編號選單
    （1 Low／2 Medium／3 High／4 Extra high／5 Max）——`_apply_effort_codex`
    自動走完並回讀 header 的 effort。
  用法：TG 打 `/effort`（alias `/推理`）→ 依分頁是 claude/codex 顯示對應層級
  按鈕→點一下套用，回合進行中會擋下。非 claude/codex 分頁會提示不適用。
  已加入 bot 指令選單與 /help。回歸測試：`tests_tg_effort.py` 6 案例。
  純 bridge 改動，`sfctl reload` 生效。

## v0.29.19 (2026-07-24)

### Fixes
- **假警報「⚠ 無法確認訊息已送進」——實際有送進、回覆隨後就到**（維護者
  07-21/07-24 截圖，HR 分頁連兩天中招）。根因：8s 驗證窗有結構性盲區——快
  回合在兩次 0.5s poll 之間就開始又結束（'esc to interrupt' footer 抓不到），
  extraction 又走 marker 路徑、模型漏吐 marker 時 fallback 最長等 30s →
  兩個強訊號都 miss → 立刻發⚠，然後回覆才進來。
  修法：「不確定且無殘留」不再立刻通知，改交**背景延遲判定**
  （`_deferred_delivery_verdict`）：再觀察最長 45s，期間看到 turn 訊號或
  「這次注入之後」的 extraction 就靜默收工；全程無聲才發警告。有殘留的
  真卡死路徑（nudge→重貼→仍失敗）維持立即通知。
- **轉發回覆夾雜畫面 chrome**：`[[TG_REPLY_xxx]]` marker 行、`✳ Cogitated
  for 1m 38s`／`✻ Crunched for…` footer、`new task? /clear to save …`、
  「──── 分頁標題 ────」分隔線全混進 TG 訊息。根因：footer 動詞會輪換
  （Cooked/Crunched/Cogitated/…）但 `_TUI_SENTINEL_RE`/`_NOISE_SESSION_END_RE`
  用固定動詞清單、符號 ✳ 也不在字元集；meaningful-lines fallback 更完全
  不濾 marker 行與標題分隔線。
  修法：兩個 regex 改認「`<verb>ed/ing for <時長>`」形狀＋補 `/clear to save`；
  新增共用 `_is_forward_noise_line`（marker token 行／footer／規則線佔比
  ≥50% 的標題分隔線），接進 `_extract_meaningful_lines`、`_peek_last_response`
  AI-block 過濾、`_marker_fallback_text` 三個轉發出口（fallback 與 /fetch
  同源，一起乾淨）。
  回歸測試：`tests_tg_inject.py` 新增雜訊過濾＋延遲判定 2 組案例。

## v0.29.18 (2026-07-15)

### Features
- **介面內語音輸入（STT）麥克風按鈕**（維護者提：不必再透過 Telegram 傳語音）。
  終端右下角新增懸浮 🎙：點一下開始錄音（紅色脈動＋計時，上限 5 分鐘，✕ 可取消），
  再點一下停止 → 走既有 STT 鏈（plugin → 本地 whisper → 遠端 provider）＋
  LLM 潤稿 → 注入**當前分頁**：
  - AI 分頁：前置語音 tag（`🎙[語音輸入（STT 逐字稿）｜可能有辨識誤差，請先解析
    語意與意圖再執行]`）後自動送出——AI 知道這是語音轉換、會先解析意圖。
  - 非 AI 分頁（shell 等）：純文字貼進輸入行、**不送出**，避免誤執行。
  錄音走原生 ffmpeg（mac=avfoundation / win=dshow 自動挑裝置 / linux=alsa），
  不走 WKWebView getUserMedia——TCC 歸屬清楚、三平台同一條路、直接產出
  whisper 要的 16kHz mono WAV。
  **引導安裝**：沒 ffmpeg → 一鍵 brew/winget 裝；STT 後端全不可用 → 引導裝
  本地 whisper，**錄音會保留、裝完自動轉錄**，不用重講一次。
  **開關**：設定 → 🎙 語音轉錄 →「介面錄音按鈕」（`settings.stt_mic_button`，
  預設開）。macOS 麥克風權限：Info.plist 補 `NSMicrophoneUsageDescription`
  （repo bundle 已含；既有安裝由啟動自癒 `_ensure_mic_usage_plist` 補 key＋
  ad-hoc 重簽，免重跑 install.sh）。
  新 API：`mic_record_start/stop`、`mic_retry_transcribe`、`mic_install_ffmpeg`。
  回歸測試：`tests_mic_stt.py` 7 案例。生效需 `sfctl restart`。

## v0.29.17 (2026-07-14)

### Fixes
- **/fetch 之後容易「斷掉」、變成不自動回覆**（回報回報）：`_flush_loop`
  的 while-body 沒有頂層 try/except，per-slot 轉發路徑上 `_extract_new_text`、
  `_extract_file_paths`、`split_for_telegram`、主路徑的 board/signal detect
  都**沒有防護**——任一在怪異畫面/回覆內容上拋例外，就會衝出 flush 迴圈、
  **靜默殺掉整條 flush 執行緒**（daemon thread 例外進 stderr、不進 bridge log，
  所以查不到 traceback），結果所有分頁一起停止自動回覆，只能 reload 救回。
  這也解釋了為何常在傳媒體/長回覆後發生、且要手動 /fetch 才看得到。
  修法：per-slot 的抽取、file-path、split、board/signal、每個收件人的送訊/
  送檔各自 try/except，單次錯誤只記錄跳過，flush 執行緒永不因單一 slot/送出
  而死。純 bridge 改動，`sfctl reload` 生效。

## v0.29.16 (2026-07-14)

### Changes
- **初次對話的 INIT_PROMPT 注入改為預設關閉＋新增全域開關**（回報（2026-07-14）：
  觸發時機不對、已非必要、找不到開關）。原本只有 preset 層級的 `inject_init`
  override、沒有全域開關。新增 `settings.inject_init_prompt`（預設 `false`），
  設定頁新增「首次訊息注入 INIT 提示」toggle；只 gate `_init_pending` 的武裝
  （UI 首訊與 TG 首訊兩條注入路徑同一個閘），**不動 `_should_inject_init`**——
  它另被總控每輪提醒與完成通知借用為「AI 分頁」判定，在那裡關會誤傷。
  切換後對新開的分頁生效。preset 的 `inject_init: true` 在全域開啟時仍有效。
  回歸測試：`test_init_prompt.py` 新增全域開關 4 案例（37/37 PASS）。
  生效需 `sfctl restart`（main.py + web/index.html 改動）。

## v0.29.15 (2026-07-12)

### Fixes
- **回覆傳不回 TG、像失聯、都要自己 /fetch**（回報回報）：TG-wrap 分頁的
  回覆要靠模型吐出 `[[TG_REPLY_xxx]]` marker 才會轉發，但模型有時忘了或吐錯
  marker，舊版就**永遠等一個不會出現的 marker**、每 tick 重置計時 → 回覆
  無限卡住不轉發，使用者只能手動 /fetch 才看得到（/fetch 直接讀畫面、繞過
  marker，所以它有效）。
  修法：marker 抽取失敗時**不再重置 last_output_time**（讓 flush 每 tick 重入
  持續嘗試，掃描本身已節流），並在 **turn 結束（live tail 無 esc to interrupt）
  且等 ≥30s 仍無 marker** 時，改用 /fetch 那條純文字抽取（`_peek_last_response`）
  自動轉發，並清掉殘留的 marker token 與 wrapper 指示回顯；fallback 的 tmux
  capture 另節流到每 3s。使用者不必再手動 fetch。
  回歸測試：`tests_marker_fallback.py` 5 案例。純 bridge 改動，`sfctl reload` 生效。

## v0.29.14 (2026-07-11)

### Fixes
- **容易掉 TG 訊息、影片檔尤其**（回報回報）：三個靜默丟棄點一起修——
  1. **影片完全沒處理**：`_handle_update` 只認 photo/doc/voice/audio，
     `video`/`video_note`/`animation` 全漏 → 傳影片（無 caption）直接靜默
     丟棄。現在都會下載成檔案附件轉發。
  2. **20MB 下載上限**：Telegram bot `getFile` 只能下載 ≤20MB 的檔，影片
     常超過 → 舊版失敗回空字串又不講。新增 `_fetch_media` 先看 `file_size`，
     超過就明確告知「超過 20MB 上限，改貼路徑/壓縮再傳」，不再靜默。
  3. **下載失敗靜默 return**：任何媒體下載失敗（逾時/超限/TG 暫時錯誤）
     都會回一則說明「沒送進分頁，請重試或改貼路徑」，不再無聲消失。
  回歸測試：`tests_tg_media.py` 5 案例。純 bridge 改動，`sfctl reload` 生效。

## v0.29.13 (2026-07-09)

### Fixes
- **靜默自動更新反覆重啟、卡「本次更新」彈窗、TG 收不到**（回報回報「非常嚴重」）：
  根因是 web/index.html 每 5 分鐘的週期檢查在 `autoUpdate` 開啟時會**靜默
  `git pull`（do_update）**把未確認的遠端改動拉到磁碟，接著 reload/restart →
  啟動時版本一變就彈「本次更新」modal，整個過程把總控分頁的對話打斷，使用者
  傳的訊息落在重啟窗口/被打斷的回合裡 → 體感「收不到」。這幾天頻繁 push 讓它
  每隔幾分鐘就自動拉一次，症狀被放大。
  修法：(1) 週期檢查改為**只通知不拉取**——偵測到新版只顯示「vX 可更新（點我）」
  橫幅，實際 pull + reload/restart 只在使用者點擊後才執行；(2) `autoUpdate`
  預設改為**關閉（opt-in）**，開啟也只是通知；(3) 同步把現有 config
  `settings.autoUpdate` 設為 false。生效需 `sfctl restart`（web/index.html 改動）。

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
- **「自動派工」開關關了還是會派工**（回報回報）：根因有二——
  1. `auto_delegate_enabled`（設定頁「自動派工（實驗性）」）**後端沒有任何
     consumer**，是顆沒接線的死開關；
  2. 真正每回合推派工的指令不在 master preamble（維護者早已關掉
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
- **Windows：TG 橋接訊息卡在輸入框送不出去（codex 最嚴重）**（回報 2026-07-07）。
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
- **App 內「檢查更新」偵測不到剛推的版本**（回報（2026-07-06）：遠端撞版那台抓不到更新）：`check_update` 讀 `raw.githubusercontent.com/.../main/version.json`，這個 Fastly CDN 有 ~5 分鐘快取，剛 push 完會餵**舊的 version.json** → 該時段檢查的機器看不到新版。修法：加 cache-bust query（`?t=<epoch>`）＋ `Cache-Control/Pragma: no-cache`，永遠讀到剛推的值。順手硬化版本比較：非數字段（channel 後綴／WIP tag）不再讓整個檢查拋例外而誤判「無更新」。

## v0.29.6 (2026-07-06)

### Features
- **TG 指令選單／`/list` 帶上模型＋思考深度**（維護者提，比照桌面側邊欄的 model badge）：Telegram 的 `/1 /2 …` 切換選單描述與 `/list` 輸出，每個 session 現在都顯示「模型 · effort」，如 `Switch to SF · Opus 4.8 · xhigh`、`/4 HR 〔Sonnet 5 · xhigh〕`。逐分頁準確（走 main.py `get_session_model_info`，用該 session 真實 cwd/session_id 偵測，與側邊欄同一來源），Claude／Codex 皆支援；非 AI 分頁或偵測不到就不加、不炸。後端新增 bridge callback `on_model_info`。回歸測試 `tests_tg_model_badge.py`。

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
- **TG 誤報「popup detected (UserNotificationCenter)」** (回報 2026-07-06)：stall 警告的阻擋彈窗偵測把 `UserNotificationCenter` 當成 TCC 對話框，但它其實擁有**所有 macOS 通知橫幅**（Slack/Mail/行事曆…）。任何 app 跳個橫幅、又剛好有 session 在等回覆，就誤報成「有彈窗擋住、去把它關掉」——橫幅根本不擋前景，訊息也無從執行。修法：把 `UserNotificationCenter` 移出阻擋清單，只留真正會 modal 阻擋的 `SecurityAgent`（密碼/鑰匙圈）、`CoreServicesUIAgent`（隔離確認）、`universalAccessAuthWarn`（輔助使用）；並要求命中視窗需有實際尺寸（≥120×60）且非透明，過濾 0x0／幽靈系統視窗。回歸測試 `tests_stall_popup.py`（8 情境，含假 Quartz 視窗清單）。

## v0.29.3 (2026-07-06)

### Features
- **TG 端 `/model` 互動選單**（維護者提）：手機發 `/model` → bridge 把原生指令送進 active 分頁開 picker → 解析選項後回 TG **inline 按鈕**（含目前模型 ✔ 標記、effort 狀態、取消鈕）。點按鈕即選定——實測 CC 2.1.x picker **數字鍵＝立即選定並存為新 session 預設（免 Enter）**，所以按鈕只送數字；取消鈕送 Esc 關閉 picker、模型不變。分頁忙碌中（回合進行）會擋下並提示，不會把指令戳進生成中的畫面。
- 附帶修正：**通用選單偵測被 picker chrome 行 reset**——「◉ xHigh effort ←/→ to adjust」這類行會把已收集的選項清空，這正是 /model 選單過去偵測不到的根因；現在 chrome 行直接略過（◉/←→/to adjust）。
- 回歸測試：`tests_tg_model_menu.py` 6 案例（picker 測資為實機截取畫面）。

## v0.29.2 (2026-07-06)

### Fixes
- **sfctl/TG 改名不會反映到畫面**（維護者:「你說 tab 有 rename 我怎麼看都沒有」）：
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
- **TG 訊息偶發送不進分頁（維護者:「/fetch 之後 prompt 沒反應、/fetch 也沒變化」）**。
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
- **OpenCode 分頁支援上滾歷史對話**（維護者 requested：第 9 個 tab 用另一套
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
- **STT 新增 `remote_first` 模式**（維護者 requested：中英夾雜要更準）：先打遠端 STT provider，連不到才 fallback 回本機 whisper。用來把語音辨識導到 Spark（190）GPU 上的 **Qwen3-ASR-1.7B** server（:9700，含 s2twp 繁體轉換），中英夾雜辨識實測明顯優於 Mac 端 mlx-whisper（範例句 Spark/Whisper/Turbo 專有名詞全對，3.5s）。
  - 對比：原本想部署 whisper-large-v3 到 Spark，但 GPU 已被 vLLM(64G)+Ollama(22G) 佔滿而 OOM；改用既有的 Qwen3-ASR server（本就更適合中文/code-switching），零額外 GPU 成本。
  - 設定走 `config.bridge.stt_backend = "remote_first"` + `stt_providers`（Spark :9700，field `audio`，result key `text`）。

## v0.26.0 (2026-07-05)

### Features
- **語音整理接 Spark AI 模型 + 可切模型**（維護者 requested）：語音 Typeless 整理改指向 Spark（190）Ollama 的 `qwythos:9b`，補上完整中英文標點、去贅字、修辨識錯字。實測 warm ~1.8s、cold ~12s（Ollama keep-alive 後保持熱）。
  - 新增 TG `/voice` 指令：`/voice` 看目前設定 + 端點可用模型清單、`/voice <模型>` 切模型、`/voice on|off` 開關整理。
  - CLEAN prompt 強化標點指示（明列 `，。、？！：「」`）。
  - 模型自動挑選跳過 OCR / vision / embed / rerank 模型（避免 deepseek-ocr 被誤選）。
  - 端點/模型/開關持久化在 `config.json` settings（`voice_refine_url` / `voice_refine_model` / `voice_refine`）。

## v0.25.0 (2026-07-05)

### Features
- **語音 Apply 閘門（Typeless 式）**（維護者 requested）：TG 傳語音轉錄+refine 後**不再自動送出**，改先顯示整理後文字 + inline `✅ Apply / ✕ Cancel`。按 Apply 才把 prompt 送進 session，按 Cancel 就丟棄。
  - STT 會糊，這道閘門讓你送出前先過目、避免錯字直接餵給 AI。
  - 目標 session 在**按 Apply 當下**才解析，中途切分頁也 OK。
  - Apply 走既有完整 forward pipeline（preamble 包裝、選單偵測、`_send` + 送達驗證），行為與手打訊息一致。
  - 重啟後未處理的待送語音會失效並提示重錄（pending 存記憶體）。

## v0.24.0 (2026-07-04)

### Features
- **`/break` — TG 遠端中斷 AI**（維護者 requested）：手機在目前分頁送 `/break`（或 `/stop`、`/esc`、`/interrupt`、`/中斷`、`/打斷`）即對該分頁送出 ESC，打斷 AI 正在跑的 turn（Claude Code / Codex 都吃 ESC）。
  - 送 ESC 前先跑 `prepare_fn` 退出 tmux copy-mode，確保 ESC 落在 CLI 而非 copy-mode。
  - 只送單一 ESC——Claude 連按兩次 ESC 會進歷史導覽而非中斷。
  - 走 `write_lock` 序列化，不與其他注入交錯；`/help` 已補上說明。

## v0.23.3 (2026-07-06)

### Fixes
- **新分頁打 `/model` 被 INIT_PROMPT 灌爆——init 注入時機修正**（回報回報「都會被 prompt inject、好長好難用、觸發時機是錯的」）：
  - 根因：web UI 的 init 注入以「第一個含內容的 write_input chunk」觸發，而 xterm 逐鍵送字——你打 `/` 的那一鍵就被當成第一則訊息，INIT_PROMPT＋「User's first message: /」直接進 composer，斜線指令選單整個壞掉。
  - 修法：**斜線指令不是第一則訊息**。行首 `/` 的輸入不消耗 init prompt（留給下一則真實訊息），並以 `_init_hold` 狀態機撐過逐鍵輸入（`/`→`m`→`o`…），該行送出（Enter）才解除——中途任何一鍵都不會再觸發注入；`/model` 選單的方向鍵/Enter 也不受影響。
  - TG 路徑同步修正：`/model` 等 CLI 指令從手機轉發時同樣不消耗 init。
  - 回歸測試：test_init_prompt.py 新增 6 組鍵序案例（33/33 綠）。

## v0.23.2 (2026-07-03)

### Fixes
- **上滾來源優先序反轉——終端來源為主，transcript 降為 fallback**（維護者實測 v0.23.1 後定調：transcript 渲染整面工具行牆「越差越多」）：
  - 上滾 overlay 回到 pyte/tmux 終端 frame 為主——本來就跟活畫面同一個樣子，重複問題已由 v0.23.0 的統一去重管線處理。實測發現 **Claude Code v2.1.x 已不用 alt-screen**（normal buffer 渲染），tmux scrollback 就是完整正確的歷史，深度 1,000+ 行、原樣 SGR。
  - transcript 渲染只在終端來源拿不出內容時救場（典型：app 剛重啟、pyte 從零開始且 pane 在 alt-screen）。
  - fallback 用的 transcript 渲染同步改善：**連續工具呼叫收合成一行摘要**（`⏺ Bash ×6、Edit ×2`，不再是 20 行工具牆）、`[Image: …]` 縮為 📎 圖片。
  - 本次依「測過才發版」流程：六套測試全綠 → 重啟實測 live dump（來源/內容/重複數）→ 產出 overlay HTML 視覺預覽過目 → 才發版。

## v0.23.1 (2026-07-03)

### Fixes
- **上滾 transcript overlay「樣式跟活畫面不同」**（維護者截圖回報）：
  - **markdown 現在渲染成 ANSI**：`**粗體**`、行內 `code`、`#` 標題（粗體青色）、`-`/`1.` 列點記號上色、`>` 引用淡化、``` 圍欄 code 區塊、`---` 轉分隔線——不再原樣露出星號反引號，讀起來接近活畫面 TUI。
  - **harness 雜訊不再直出**：transcript 裡 user 角色夾帶的 `<task-notification>…</task-notification>`（背景 agent 回報，含整包 result/usage XML）摺疊成一行 dim 摘要「⏺ <summary>（內容略）」、`<system-reminder>` 整段移除——活畫面 TUI 本來就不顯示這些，overlay 對齊。
  - 回歸測試 +1（`test_transcript_render_fidelity`）。

## v0.23.0 (2026-07-03)

一次完整復盤驅動的大版本：P0→P2 全清（維護者核可的優化計畫），四套回歸測試全綠。

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
- **切換同供應商的分頁不再重查水位，直接沿用畫面上的讀數**：膠囊一次只跟著一個供應商（claude 或 codex）。原本每切一次分頁都重打一次 probe，但 claude→claude 切來切去數字根本一樣，白查。改成：切到的新分頁若對應的供應商跟膠囊現在顯示的相同 → 什麼都不做；只有 claude↔codex 真的換了供應商才動作，且優先沿用該供應商 2 分鐘內的快取讀數，沒有新鮮快取才真的去 probe。等於只有換供應商、且讀數過期時才會 call，來回切不會一直重打。供應商判定對齊後端（codex 分頁→codex，其餘含 claude／非 AI 分頁→claude）。維護者 2026-06-30 提。

## v0.22.2 (2026-06-30)

### Changes
- **水位膠囊改成事件驅動更新，不再固定每 5 分鐘輪詢**：水位只有在「跑了一個回合」之後才會變動，固定計時器多半是重撈同一個數字。改成跟著當前分頁的對話活動走：(1) 你**下了新 prompt／回合開始**（分頁由閒置轉忙）→ 立刻刷新（15 秒內不重複，避免一來一回狂打）；(2) 回合**結束**（由忙轉閒）→ 等 4 秒沉澱再刷新，短回合連發只會合併成一次、且抓得到回合後的最新數字；(3) **都沒動靜** → 每 15 分鐘輪詢一次當 fallback（每次刷新都重置這個倒數，只在真的安靜一段時間後才會跑）。切換分頁仍即時刷新。等於有事才查、沒事 15 分鐘看一次。維護者 2026-06-30 提。

## v0.22.1 (2026-06-30)

### Fixes
- **水位膠囊查不到時不再整顆消失**：v0.22.0 的右上角 AI 用量水位膠囊，只要 fetch 不到水位（沒登入供應商、抓不到資料、後端例外）就直接 `display:none` 把整顆膠囊藏掉——看起來像功能壞了，膠囊在頂列的位置也跟著消失。改為保留膠囊、改顯示灰字佔位狀態：(1) 抓到供應商但沒水位（多半沒登入）→ `用量 查不到`，tooltip 提示「請確認已登入 <claude/codex>」；(2) 後端 fetch 例外 → `用量 ⚠`，tooltip 帶錯誤訊息；(3) IPC/JS 例外 → 有上一次讀數就保留並淡化（沿用舊行為），沒有才顯示 `用量 ⚠`。三種狀態都維持原位置、仍可點擊開完整水位彈窗重試。維護者 2026-06-30 回報。

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
- **TG 收到 Codex 回覆被重複多次、混入「›Explain this codebase」**：當回覆比終端 viewport 長時，Codex/Claude 的 TUI 在串流中會捲動並重繪——把同一塊內容以重疊視窗一再吐進線性化的 PTY 流，於是 `[[TG_REPLY]]` 起訖標記之間夾了好幾幀重複行，原本只去重「相鄰重複行」的清理擋不掉非相鄰重複，整段被 `split_for_telegram` 切成多則超長重複訊息送出。修正三處：(1) `_marker_spans` 改為「每個 end 配對最近的 start」（tightest pairing），避免重繪插入的新 start 讓首個 start→遠端 end 貪婪吃進中間整段殘影；(2) `clean_mobile_marker_response` 改為全域行去重（保留首次出現）並清掉殘留的 `[[TG_REPLY_xxx]]` token，把捲動重繪壓回唯一行；(3) `filters.json` echo_keywords 補上 Codex 空輸入框預設提示 `explain this codebase`，連同既有 `summarize recent commits`／`switch models or reasoning` 一併在 strip 階段濾掉，標記內也不再殘留 composer footer。標記存在時 Telegram 仍只送「標記內最後一個完整 block」，絕不 fallback 整個終端畫面。維護者 2026-06-28 回報。

### Fixes
- **Idle-reaper 交接訊息卡在輸入框沒送出**：本機模式（TG bridge 未 active）下，`_write_lifecycle_handoff` 用 naive `target.write(compact + "\r")` 直接寫進總控 PTY，在 Claude/Codex TUI（總控 mid-turn 或輸入行有殘留）下那個 `\r` 常被忽略 → 交接文字累在輸入框、沒提交成一輪。改用既有可靠提交路徑 `_send_text_to_session(target, compact, submit=True)`（tmux bracketed-paste 一次成型 + 貼上完成才送分離的 Enter）。維護者 2026-06-27 實際踩到（idle_reaper 關閉 s75 後交接卡住）。

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
