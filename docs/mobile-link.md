# ShellFrame Mobile — iPhone / iPad / Apple Watch 配對 App

手機、iPad 是電腦端 **Frame Link** 的一個 peer（見 [`frame-link.md`](frame-link.md)）：
配對一次，之後像本機分頁一樣看電腦上每個 session 的終端畫面、直接打字，
一支手機可綁多台電腦。Apple Watch 掛在 iPhone 底下，只做一件事：錄音送出。

專案在 [`ios/`](../ios/)（`xcodegen generate` 產生 Xcode 專案；SwiftUI + SwiftTerm）。

## 一分鐘配對

1. 電腦：ShellFrame 側欄「＋ 配對」→ 產生配對碼 → 畫面上出現 **QR code**。
2. 手機：ShellFrame App 右上 ＋ → 掃 QR。掃到就自動配對，不用打任何字。
3. 側欄出現這台電腦與它的 session，點一個就是終端。

沒有相機（或人在外面）：電腦的 TG bot 下 `/link pair`，回覆裡有一條
`shellframe://pair?d=…` 連結，手機點開即配對（需先設定 relay，見下）。
也可以手動輸入 IP／port／配對碼，跟另一台 ShellFrame 加入配對一樣。

QR／連結內容 = `{fid, name, hosts[], port, code, mode, relay?}` 的 base64url JSON，
配對碼仍是一次性、120 秒、連錯 5 次作廢；握手協定與 Frame Link 完全相同
（joiner 先證明、host 再證明、雙方各自導出 256-bit 長期金鑰，碼不走明文）。

## 忠實鏡射

- 串流：`/link/stream` 增量原始 PTY 輸出 → 手機端 SwiftTerm 渲染（同一套 ANSI、
  同一組 Tokyo Night 16 色）。attach 時先拿 `/link/snapshot`（`tmux capture-pane -e`，
  含顏色與游標）畫底，舊版電腦退回純文字 `peek`。
- 鍵盤：`/link/input` 原始位元組直送 PTY（方向鍵、Ctrl-C、Enter 照原樣）。iPad
  外接鍵盤由 SwiftTerm 處理；軟體鍵盤上方有 Esc／Ctrl／Tab／方向鍵列。
- 尺寸兩種模式（工具列箭頭鈕切換）：
  - **照電腦尺寸**（iPhone 預設）：以電腦回報的 cols×rows 畫，捏合縮放、拖曳，
    **不會動到電腦上的終端**。
  - **撐滿這台裝置**（iPad 預設）：把手機／iPad 的可視格數推給電腦
    （`/link/resize`），電腦上該分頁會跟著 reflow——跟桌面版遠端分頁一樣。
    切回照電腦尺寸時會把原尺寸還回去。
- 訊息框（💬）：走 `/link/send`，也就是 Telegram 訊息那條注入路徑（等 AI 空檔、
  tmux bracketed paste、再 Enter），長 prompt 比逐字敲穩。
- 「要你決定」：`/link/signals` 讀 agent 的 `[[SF:RED/YELLOW]]`，側欄該 session
  會亮橘色驚嘆號。
- 多台電腦：每台是獨立 peer、獨立金鑰；側欄一台一區。

## 公網：三條路，預設走 Tailscale

手機在外面、電腦在 NAT 後 → 兩邊都連不進對方。三種解法，門檻差很多：

| 做法 | 誰適合 | 要準備什麼 |
|---|---|---|
| **Tailscale**（建議、預設） | 所有人 | 兩邊各裝一次 App 並登入同一帳號 |
| relay | 自己有主機的人 | 一台主機 + 網域 + TLS |
| port-forward／反向代理 | 電腦位址固定的情況 | 固網 IP 或網域、路由器設定 |

### Tailscale（建議寫進使用說明的那條）

Tailscale 把你自己的裝置接成一個私人網路，每台拿到一個固定的 `100.x.x.x`
位址，不管在家、在公司還是走 4G 都連得到，中間是 WireGuard 加密——剛好補上
Frame Link 只簽章、不加密這件事。

對 ShellFrame 來說**完全不用改任何設定**：那個 `100.x` 位址就跟區網 IP 一樣，
在 App 的「編輯位址」填進去、port 仍是 8767 就好。

1. 電腦：`brew install --cask tailscale`（安裝要 sudo），開啟並登入。
2. 手機／平板：App Store 裝 Tailscale，登入**同一個帳號**。
3. 電腦上看它給的 `100.x.x.x`。
4. App →（該台電腦的）⋯ → 編輯位址 → 填 `100.x.x.x`，port `8767`。

之後配對與日常使用完全一樣，只是位址換成永遠連得到的那一個。這也是同類工具
（例如 OpenClaw）的官方建議做法——它們同樣不提供資料 relay。

⚠️ 用 MagicDNS 名稱（`xxx.ts.net`）而不是 `100.x` 數字位址時，走的是網域名稱，
需要 App 的 ATS 允許非 HTTPS 網域——這點已修（見下方「App 端注意」）。

### relay（TG 式出站長輪詢）

自己有主機時才需要。解法跟 Telegram bridge 一樣：**電腦只出不進**，去一個
relay 長輪詢；手機把請求投到 relay，電腦拉回來、在本機 loopback 對自己的
Frame Link listener 重放、把回應貼回去。

```
手機 ── POST /r/<fid>/call ──▶ relay ◀── GET /r/<fid>/pull (long-poll 25s) ── 電腦
      ◀── {status,headers,body} ──      ──▶ POST /r/<fid>/reply/<id>  ──▶
```

- relay = [`relay_server.py`](../relay_server.py)（stdlib、in-memory）。
  ```bash
  python3 relay_server.py --port 8790 --token "$(python3 -c 'import secrets;print(secrets.token_urlsafe(32))')"
  ```
  前面放 TLS（Caddy：`relay.example.com { reverse_proxy 127.0.0.1:8790 }`）。
  同一個 relay 可服務多台電腦（以 frame_id 分流）與多支手機。
- 電腦端設定：`config.json`
  ```json
  "frame_link": { "relay": { "url": "https://relay.example.com", "token": "…" }, "public_host": "" }
  ```
  或 `pywebview.api.link_set_relay(url, token)`。設定後產生的 QR 會帶 relay，
  手機拿到就會「直連優先、直連不到走 relay」自動切換（側欄顯示「經 relay」）。
- `public_host` 是有 port-forward 時對外的 IP／網域，也會一起放進 QR 的 hosts。

### App 端注意

App 的 ATS 只保留 `NSAllowsArbitraryLoads`。**不要**再加回
`NSAllowsLocalNetworking`：iOS 10 起只要有那個 key，`NSAllowsArbitraryLoads`
會整個被忽略，結果只有 IP 位址連得通，任何網域名稱（DDNS、relay 網址、
MagicDNS）都會在送出前就被擋掉，畫面上只顯示「連不上」。

### 信任邊界（請看）

Frame Link 是 **HMAC 簽章、未加密** 的 HTTP。relay 只搬簽過章的 envelope、
偽造不了任何一方，但**看得到明文**（終端內容、輸入）。所以：
relay 要自己架、走 HTTPS、把 relay 主機當成電腦本身來看待。端對端加密是
後續項目。relay 只放行 `/link/*`，envelope 上限 8MB（語音可過、檔案傳輸不走 relay）。

## Apple Watch：錄音送出

Watch 不配對、不持金鑰，掛在 iPhone 下：

1. Watch App 選目標（電腦／分頁清單由 iPhone 經 WatchConnectivity 同步）。
2. 按一下錄音、再按一下送出（AAC m4a 16 kHz 單聲道）。
3. 檔案傳到 iPhone → iPhone 以簽章 `POST /link/voice?sid=…` 送到電腦
   （簽章蓋 sha256，body 邊收邊驗，跟 `/link/file` 同一套）。
4. 電腦端 `voice_inject` = 與 **TG 語音、桌面麥克風完全相同**的鏈：
   whisper（本地／plugin／remote）→ 精煉 → AI 分頁帶語音 tag 送出、shell 分頁只貼不送。
5. 逐字稿回到 Watch 顯示。iPhone／iPad 工具列的 🎙 也是同一條路。

## 建置 / 安裝

```bash
cd ios && xcodegen generate            # brew install xcodegen
xcodebuild -scheme ShellFrameMobile -destination 'platform=iOS Simulator,name=iPhone 16 Pro' \
  -skipPackagePluginValidation -skipMacroValidation build
```

- Team `487V8X9AHS`（個人開發者帳號）、bundle `com.howard.shellframe`
  （watch：`.watchkitapp`）、自動簽章。裝實機：Xcode 開 `ios/ShellFrameMobile.xcodeproj`
  選裝置 Run。SwiftTerm 需要 Metal toolchain（`xcodebuild -downloadComponent MetalToolchain`）。
- 手機端 ATS 開 `NSAllowsArbitraryLoads`：Frame Link 本身就是 http。
- QA 掛鉤（只在模擬器用）：環境變數 `SF_QA_AUTOSELECT=1|<sid>` 自動開某 session、
  `SF_QA_FIT=0|1` 強制顯示模式，方便無人工點擊的截圖。

## 電腦端新增的東西（v0.33.0）

| 項目 | 位置 |
|---|---|
| `pair_url`（QR／深連結）、`joiner_kind`、`relay`／`public_host` 設定 | `frame_link.py` |
| `/link/snapshot`、`/link/signals`、`/link/voice` | `frame_link.py` handler |
| client 端「直連失敗→relay」、`join_url()` | `frame_link.py` |
| relay 長輪詢 worker、`relay_call()` | `link_relay.py` |
| relay 伺服器 | `relay_server.py` |
| `snapshot`／`voice_inject` sfctl 指令、`link_pair` 回覆帶連結、`link_set_relay` 等 API | `main.py` |
| 配對 modal 畫 QR | `web/index.html` |
| TG `/link join shellframe://…` | `bridge_telegram.py` |
| 回歸測試 | `tests_relay.py`（relay 全流程）、`tests_frame_link.py` |

## 對話畫面讀多少（v0.41.0）

`/link/conversation` 從紀錄檔尾端往前讀，直到湊滿 `limit` 個 turn（預設 80，最多 400），或讀到 8 MB 為止（`api_remote.conversation_turns`）。原本固定只讀最後 256 KB，一筆大的 attachment 紀錄就能把使用者剛送的那句擠出去。紀錄檔沒變時直接用上次的結果；變長時只從上次夠用的範圍再讀一次。

## Bots（v0.41.0）

手機的 bot 介面用下面這幾條路由。全部都要簽章，而且對方必須能操作這台；單向配對中無權的那一方回 403，手機當成「這台沒有 bot 介面」。

**`GET /link/bots?rev=<上次的 rev>`**：每個 bot 和每個分頁現在的樣子，規則跟桌面一樣。

- 讀的是狀態監控的快照，所以手機每幾秒問一次也不會讀對話紀錄。只有停在選單上的分頁會讀一次畫面，拿選項。
- 內容跟上次一樣時只回 `{"success": true, "unchanged": true, "rev": N}`。正在跑的分頁 `last_at` 等於現在，這個時間不算變化。
- bots 沒開時，`bots` 是 `[]`、`tabs` 是 `{}`。

| 欄位 | 內容 |
|---|---|
| `v`, `rev`, `now`, `enabled`, `primary` | 版本（加欄位不升版）、內容的 hash、電腦的時間、bots 有沒有開、主要 Bot |
| `bots[]` | `role`, `label`, `responsibility`, `model`, `runs_on`, `avatar`, `primary`, `external`, `state`（`awake`／`asleep`／`external`）, `sid`, `position`, `activity`, `doing`, `latest`, `last_at`, `blocked`, `closing`, `decision` |
| `model`, `runs_on` | `model` 是 bot 設定裡填的模型，空的表示用 CLI 的預設，等於桌面 bot 編輯器的「模型」欄；`runs_on` 是它現在實際跑的模型 |
| `choices` | 可以選的頭像：`shapes`（12 種形狀，順序同桌面）、`colors`（`[{"key", "hex"}]`，14 種顏色和它畫出來的色碼，同 `bots.js` 的 `PALETTE`）、`custom_color: true`（也接受 `#RRGGBB`）。手機不用自己寫死一份 |
| `tabs[sid]` | `bot`, `avatar`, `plain`, `closing`, `activity`, `doing`, `latest`, `last_at`, `blocked`, `decision` |
| `avatar` | 已經解析好的頭像，等於桌面 `bots.js` 的 `avatarFor`：bot 自己的、分頁自己挑的，否則是 `defaultAvatar`，用 role 或 sid 當種子。Python 版在 `bots.default_avatar`，`tests_link_bots.py` 會拿它跟真的 `web/bots.js` 對照 |
| `activity` | `working`／`idle`／`done`／`decision`／`stuck`，等於桌面的燈號；睡著或外部的 bot 是 `""` |
| `doing`, `latest` | 跑的時候在做什麼；最新一句的取法同桌面的 `previewOf`。bot 的分頁沒有可說的話時，用它的執掌。最多 140 字，已經遮掉憑證 |
| `last_at` | 最後一次回應的 epoch 秒；跑的時候等於現在；0 表示不知道 |
| `closing` | `""`／`waiting`／`writing`：關閉前正在寫記憶 |
| `decision` | 在等人決定時是 `{"id", "reason", "options": [{"n", "text"}]}`，否則 `null`。兩種情況：畫面上有選單（權限提示等）；或回合最後問了 `[[SF:RED]]`，有沒有開 Telegram 都一樣，之後送進那個分頁的 prompt 就算回答了。`id` 是這一題的指紋：選單取整個對話框的內容（同樣的問句，問的指令不同就是不同題），RED 取提問的時間。回答請用下面的 `POST /link/answer` |

外部 bot 的 webhook 網址和授權，都不會出現在回應裡。

**`POST /link/answer {"sid", "n", "id"}`**：回答 `/link/bots` 給的那一題。

- **只在 `id` 還是那個分頁現在的題目時才送。** 送進去的是 `n` 加 Enter，跟 Telegram 的選擇按鈕一樣。成功回 `{"success": true}`。
- **擋下來時不送任何東西**，回 `success: false` 和 `code`：
  - `stale`：題目已經回答過，或已經換了；一併附上現在的 `decision`，可能是 `null`。
  - `no_option`：沒有這個選項。
  - `no_tab`：分頁不在。
- **也會擋下來的情況：** 同一題剛在 Telegram 或手機上回答過，3 秒內再送也回 `stale`，因為畫面可能還沒更新。
- **Telegram 那邊：** 回答之後，那一題在 Telegram 的每一則都會改成「✅ 已在手機上選：n」並拿掉按鈕；之後再按，會回「這題已經回答過了」。這樣舊按鈕不會把答案送進下一題。
- **`/link/input` 照舊：** 原始按鍵直送，不做這些檢查。

**改頭像和 bot 設定：** 走的都是桌面現有的函式。成功回 `{"success": true, …}`，`/link/bots` 的 `rev` 會跟著變。

- **不能改的：**
  - 外部 bot 一律不能改，請到它那邊改。
  - 不提供新增或刪除 bot。
- **`POST /link/bots/avatar`：** 改頭像，可以用分頁或 bot 名字指定。
  - **`{"sid", "avatar": {"shape", "color"}}`：** 一般分頁改的是它自己的頭像；`avatar: null` 會變回預設頭像。如果是 bot 的分頁，改的是 bot 本身的頭像，不接受 `null`。對應桌面的 `bots_set_avatar`。
  - **`{"role", "avatar": {…}}`：** 休息中的 bot 用名字改，對應 bot 編輯器的儲存。
  - `shape` 必須在 `choices.shapes` 裡；`color` 要在 `choices.colors` 裡，或是 `#RRGGBB`。
- **`POST /link/bots/settings {"role", "responsibility"?, "model"?}`：** 只改有給的欄位，skill、記憶、頭像、啟動指令都不變。對應 bot 編輯器的儲存（`bots_save`）。
  - `model` 是一個詞，例如 `sonnet`；空字串表示用 CLI 的預設。
  - 兩個欄位都沒給時，會被拒絕。
- **`POST /link/bots/primary {"role"}`：** 指定主要 Bot，對應 `bots_set_primary`。只能指定這台電腦上的 bot，不能指定外部 bot，也不能清空。

**`POST /link/bots/wake {"role"}`**：叫醒一個 bot，走的是桌面的 `bots_open`。回 `{"success", "details": {"sid", "existed"}}`；`existed: true` 表示它本來就醒著。外部 bot 回 `success: false` 和原因。

**`/link/signals` 的 RED 事件**多了兩個欄位：`role`（哪個 bot 在問；一般分頁是 `""`）和 `options`（`[{"n", "text"}]`，解析不出來就是 `[]`）。這些事件仍然只在 Telegram 橋接開著時才有；不靠橋接的版本是上面的 `decision`。

## 推播通知（電腦端）

手機和 iPad 不用開著 App，bot 有事時會像收到訊息一樣跳出通知。電腦**直接送 APNs**，不經第三方中繼：對話內容和 key 都留在這台電腦上。iOS 端的規格見 Coding 的〈推播規格〉；這一節只寫電腦端。

**設定（一次）**：Apple 開發者網站建一把 APNs Auth Key（.p8，Sandbox & Production），放在這台電腦，然後：

```bash
sfctl push-setup --key ~/.config/shellframe/apns/AuthKey_<KEYID>.p8 --key-id <KEYID> --team-id <TEAMID>
sfctl push-status
```

- `push-setup` 會把 key 檔設成 600，存進 `~/.config/shellframe/push.json`（600），再用一個假的裝置 token 問一次 Apple：回 `400 BadDeviceToken` 代表 key 有效，`403` 代表 Key ID、team id 或 key 檔不對。
- 送的方式：用系統的 `openssl` 簽 ES256 JWT（40 分鐘換一次），用 `curl --http2` 送；不加任何套件。token 和內容不經過命令列參數。
- Windows 目前不支援推播，`status` 會回原因。

**路由**（都要簽章；單向配對中無權的一方回 403）：

| 路由 | 內容 |
|---|---|
| `POST /link/push/register` | `{"token", "env": "sandbox"/"production", "topic", "device_name", "prefs": {"kinds", "hide_content", "show_computer", "telegram", "done"}}`。每支手機一筆，再註冊就整筆換掉。回 `{"success": true, "details": {"registered": true, "enabled": <這台設好 key 沒>}}` |
| `POST /link/push/unregister` | `{}`：刪掉這支手機的註冊 |
| `GET /link/push/status` | `{"success", "enabled", "reason", "registered", "env", "prefs", "last_sent_at", "last_error"}` |
| `POST /link/push/test` | `{}`：馬上推一則測試通知給這支手機，回 Apple 的結果 `{success, message}` |
| `POST /link/peer/name` | `{"name"}`：手機改自己在這台電腦上的名字（最多 60 字），不用重新配對 |

`prefs`：`kinds` 是 `decision`、`stuck`、`done`、`info` 要哪幾種（預設全部）；`hide_content` 隱藏內容；`show_computer` 在標題下面寫電腦名稱；`telegram` 是 `quiet`（預設）或 `both`；`done` 是 `primary`（預設，只有主要 Bot 和派工結果）或 `all`。

**什麼時候推**（都不依賴 Telegram 橋接）：

| kind | 來源 | 標頭 |
|---|---|---|
| `decision` | 回合最後問了 `[[SF:RED]]`，也就是 `/link/bots` 的 `decision`。每次回合結束電腦都會自己抓，不靠 Claude Code 的 Stop hook。`sf.qid` 就是 `/link/bots` 的 `decision.id`，鎖定畫面選的答案照常走 `/link/answer` | priority 10，collapse `q.<qid>` |
| `stuck` | 分頁停在對話框（狀態監控的 BLOCKED），或 `[[SF:YELLOW:原因]]` | priority 10，collapse `stuck.<sid>` |
| `done` | 回合結束（預設只有主要 Bot），以及派工結果 `a2a-result` | priority 5，collapse `done.<sid>` |
| `info` | `sfctl notify`；標題是 bot 名稱，DONE／BLOCKED／QUESTION 會寫在標題 | priority 5，collapse `info.<sid>` |

- **只推一次**：同一題（qid）只推一次，電腦重啟後也一樣（記在 `push.json`，保留 24 小時）。
- **收回**：題目在任何地方被回答（手機、Telegram、電腦上直接打字開始下一輪）或換掉時，送一則背景推播 `{"sf": {"kind": "withdraw", "ids": ["q.<qid>"]}}` 把它拿掉。從某支手機回答時，其他手機馬上收回。
- **節流**：同一個分頁的 `stuck`、`done` 一分鐘內再來，用同一個 collapse-id 安靜取代，不再響；每台裝置每小時最多 30 則；同時最多 4 個送出。
- **Apple 的回應**：410 或 `BadDeviceToken` 就刪掉那筆註冊，App 下次打開會重新註冊；`InvalidProviderToken`／`ExpiredProviderToken` 會換一個新的 JWT；429 退避；其他錯誤記在 `last_error`。log 只印 token 的前 6 碼和後 4 碼，不寫內容。
- **Telegram**：推播正常（24 小時內成功過、之後沒有錯誤）而且手機選了 `quiet` 時，Telegram 照樣傳訊息但不響。推播一出錯，Telegram 就恢復會響。橋接自己發的 🟡 YELLOW 橫幅還是會響。
- **隱私**：`hide_content: true` 時不送題目、不送選項，本文只有「打開 ShellBot 查看」，category 用 `SF_OPEN`。文字一律先遮掉憑證，本文 180 字以內，整包 4 KB 以內。
