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

## 公網：relay（TG 式出站長輪詢）

手機在外面、電腦在 NAT 後 → 兩邊都連不進對方。解法跟 Telegram bridge 一樣：
**電腦只出不進**，去一個 relay 長輪詢；手機把請求投到 relay，電腦拉回來、在
本機 loopback 對自己的 Frame Link listener 重放、把回應貼回去。

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
  `public_host` 是有 port-forward 時對外的 IP／網域，會一起放進 QR 的 hosts。
- 不用 relay 也可以：VPN／Tailscale 讓手機直連 8767，或 port-forward + `public_host`。

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
