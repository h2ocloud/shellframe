# Changelog

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
  「雜事」，Howard 是 `/10` 打不開才發現分頁早就不在。現在改指之前會先發一則
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
  語意那條）。全套 31 檔綠。main.py 有動 → 需 `sfctl restart`。
  Howard 2026-08-26 回報（「雜事」分頁連續 6 則全誤報）。

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
  獨立使用者訊息，各自跑一次模型、各回一則「收到，已設定」，Howard 真正的問題
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
     每則回覆都得等 30s fallback 兜底（Howard「愛回不回」的其中一條路徑），
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
- **用量膠囊加 token 配速**（Howard 提：「wk 29%」單看不知道是快還是慢）。把週額度
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
- **同一則回覆一直重送（Howard：「對話1跳針」）＋ 假的「送出失敗」警告**。
  根因是 v0.29.34 P0-3 commit 模型的副作用：送出判定是**整批**的
  （任一收件人失敗＝整批 FAILED → 不進去重集合 → 下一輪重抽重送）。
  實際 log：兩個 chat（`5582043292`、`5617995311`）**把 bot 封鎖了**
  （HTTP 403 `bot was blocked by the user`），於是每輪 flush 都判定失敗，
  Howard 這個唯一收得到的收件人**每輪都再收一次同樣內容**，還附一則
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
  跑數小時這條路就靜默數小時（Howard 說的「愛回不回」）。現在 3 分鐘沒消息就
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
`tests_tg_marker_hijack.py`（P0-1，用 Howard 的實測輸入當測資）、
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
- **側欄分頁點兩下改名被誤判成拖曳**（Howard 回報：最末端的分頁尤其中招）。
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
- **AI 帳號面板列出每個帳號的用量**（Howard 提：點開要看到全部帳號的用量）。原本面板
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
- **語音「Apply 確認」可關閉**（Howard 提：TG 傳語音每次都跳 ✅ Apply 很煩，
  記得有開關但其實沒接線）。原本語音轉錄後**一律**泊住＋跳 Apply/Cancel（寫死、
  無設定）。新增 `settings.voice_apply_gate`（預設 True＝維持確認；STT 有誤差時
  較安全），關閉時轉錄完直接把文字自動送進分頁、不再跳 Apply。設定頁 🎙 語音
  轉錄 區新增「語音送出前先確認（Apply）」toggle。Howard 的設定已設為關。
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
- **側欄模型徽章判讀不準**（Howard 08-06 截圖：tab13 顯示「Opus 4.6 ·
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
- **拖曳檔案沒帶路徑・第二層根因**（v0.29.26 修了 uri-list 缺檔名，Howard
  重測仍無反應）。js:drop 足跡顯示這次更徹底：`types=["Files"]`——
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
- **拖曳檔案進來沒帶上路徑**（Howard 08-05：拖 Finder 檔案毫無反應）。
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
- **側欄縮窄時把「命名」擠掉、卻保留重複的模型徽章**（Howard 回報：應保留
  命名資訊，不是後面重複性高、低識別度的資訊）。根因：`.sb-label` 是
  `flex:1`（會收縮 + ellipsis），但 `.sb-model` 是 `flex-shrink:0`（永不收縮）
  → 窄的時候 label 先被吃掉、模型徽章反而全留（幾乎每列都是 Opus 4.8·xhigh，
  無識別度）。
  修法：`#sidebar-sessions` 設 CSS container query，側欄窄到 ≤215px 先隱藏
  模型徽章、≤165px 再隱藏 TG 徽章與編號，label（flex:1）自動取回空間 →
  縮窄時優先看得到分頁名稱。生效需 `sfctl restart`（web/index.html）。

## v0.29.24 (2026-08-03)

### Fixes
- **版號衝突會讓其他機器偵測不到 update**（Howard 回報）：`check_update` 原本
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

<<<<<<< HEAD
## v0.29.23 (2026-08-01)

### Fixes
- **剛送出訊息就立刻收到「上一則回覆」的重複**（Howard 回報）：v0.29.21 的
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
>>>>>>> 48b4da0 (v0.29.23: 修「剛送出就重送上一則回覆」——fallback 時鐘改用 msg_sent_ts(非stale first_output_time)+對 sent_responses 去重+新訊息清 buffer)
  純 bridge 改動，`sfctl reload` 生效。

## v0.29.22 (2026-07-27)

### Fixes
- **TG `/effort`／`/model` 明明套用成功卻回「已送出但沒在畫面看到確認」**
  （Howard 07-27：tab13 選 ultracode，畫面實際已顯示
  `Set effort level to ultracode…`、狀態列也變 ultracode）。
  根因：確認回讀用 `_slot_display(slot)[-N:]`——pyte 虛擬螢幕固定 50 列，
  實際終端較矮（~36-44 列）時內容只佔上半部，**尾端切片幾乎全是空白列**，
  確認行（在 composer 上方幾行）永遠不在切片裡。共 4 處同款：
  `_apply_effort_claude`、`_apply_effort_codex` ×2、`/model` picker 確認。
  修法：統一改用 `_live_tail(slot, rows=N)`（先濾空列再取尾端，v0.29.1 就是
  為此而生）。測試 harness 同步拿掉假 `_live_tail` 改走真實實作；
  `tests_tg_effort.py` 新增「確認行＋30 列空白尾」回歸案例。
=======
## v0.29.21 (2026-07-26)

### Fixes
- **Follow-up 連續訊息只回一則、背景 subagent 完成的訊息漏掉**（Howard 回報）：
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
- **TG `/effort` — 遠端調 active 分頁的推理深度（claude + codex 統一）**（Howard 提）。
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
- **假警報「⚠ 無法確認訊息已送進」——實際有送進、回覆隨後就到**（Howard
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
- **介面內語音輸入（STT）麥克風按鈕**（Howard 提：不必再透過 Telegram 傳語音）。
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
- **/fetch 之後容易「斷掉」、變成不自動回覆**（Howard 回報）：`_flush_loop`
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
- **初次對話的 INIT_PROMPT 注入改為預設關閉＋新增全域開關**（Howard 2026-07-14：
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
- **回覆傳不回 TG、像失聯、都要自己 /fetch**（Howard 回報）：TG-wrap 分頁的
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
- **容易掉 TG 訊息、影片檔尤其**（Howard 回報）：三個靜默丟棄點一起修——
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
- **靜默自動更新反覆重啟、卡「本次更新」彈窗、TG 收不到**（Howard 回報「非常嚴重」）：
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
