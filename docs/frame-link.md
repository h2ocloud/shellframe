# Frame Link — 跨機配對與互通

兩台 ShellFrame 一次配對後，可以跨 LAN／公網互看分頁、互送指令、互傳訊息與檔案。
入口是 tab bar 分頁間隔的 🔗（暗＝沒有連上的 peer、亮＝至少一台可達），
點開下半部分割面板操作。

## 入口位置

配對入口在**左側欄的隔離線**（＋ 配對）；隔離線以下整區就是「其他 ShellFrame」
的 session。tab bar 分頁間隔那顆 🔗 現在只開「訊息／檔案」面板。

## 配對流程

1. **A 機**：側欄隔離線「＋ 配對」→「產生配對碼」→ 選綁定方式（見下）→ 顯示
   一次性配對碼（10 字元、120 秒有效、連錯 5 次作廢）＋這台的位址與 port（預設 8767）。
2. **B 機**：「＋ 配對」→「加入配對」→ 輸入 A 的 `IP/網域`、port、配對碼。
3. 配對成功後兩邊各自存下 256-bit 長期金鑰，之後完全不需要再輸入任何東西。

遠端（TG）也可以：對 A 機的 bot 下 `/link pair` 拿碼，對 B 機的 bot 下
`/link join <host[:port]> <碼>`。`/link` 看狀態、`/link unpair <名稱>` 斷開。

斷開配對：側欄 peer 標題列右邊的 ✕（會跳確認）。對方那台的記錄要在對方
機器上自行移除。

## 單向 / 雙向綁定

產碼時選一種（**兩邊配對後都會看到彼此**，差別在誰能操作誰的 session）：

- **🔄 雙向**：兩邊都能操作對方。
- **➡️ 這台當主**：只有產碼這台能操作對方；對方看得到這台但不能操作。
- **⬅️ 這台當從**：只有對方能操作這台。

模式綁進 host 的配對 proof，中間人改不了。受控端在 `/link/info`、`/link/peek`、
`/link/stream`、`/link/send`、`/link/input` 一律被拒。**訊息與檔案互傳不受模式限制。**

## 無縫遠端 session

點側欄「其他 ShellFrame」下的某個 session → 在**主終端區**開一個真正的 xterm，
跟本機分頁一模一樣：串流對方 PTY 原始輸出（`/link/stream` 增量通道，只緩衝有人
在看的分頁）、鍵盤直送對方 PTY（`/link/input`，方向鍵／Ctrl-C／Enter 照原樣）。
開著的遠端分頁也會在上方 tab bar 以 🌐 露出；關掉只是收掉檢視，不會殺對方的 session。

## 訊息 / 檔案面板（tab bar 🔗）

- 💬 訊息：兩台互傳文字。📁 檔案：拖放或選檔互傳，收到的檔案存
  `~/Downloads/ShellFrame/<peer 名>/`。
- peer 標題列的 ✎ 可改位址——對方 IP 漂移（DHCP）時改這裡即可，不用重新配對。

## 公網／NAT 拓撲

**只要有一邊可達就是全雙工。** 可達端持有 per-peer outbox；不可達（NAT 後）的
那台每 15 秒帶簽章輪詢 `GET /link/events`，把排隊的訊息／分頁指令／檔案 offer
拉回來執行（檔案再用 `GET /link/outbox/file?id=` 取staging 副本，確認送達後
自動清除）。跨公網時，可達端要 port-forward listener port（預設 8767），
另一台在「加入配對／編輯位址」填公網 IP 或網域即可。

兩邊都不可達（都在不同 NAT 後、都沒 port-forward）就無法互通——需要其中一邊
開通對外。

## 安全模型

- **配對碼不走網路明文**：joiner 先用 HMAC proof 證明知道碼，host 再回自己的
  proof（互證），雙方各自從碼＋雙 nonce＋雙 frame_id 導出長期金鑰。
- **所有 peer 請求都簽章**：HMAC-SHA256 over method+path+timestamp+nonce+body
  hash；timestamp ±90 秒、nonce 防重放；回應也簽章防竄改。
- **檔案完整性**：sha256 隨附驗證，寫入採 `.part` 暫存再 rename。
- **沒有加密**（stdlib-only，無 TLS）：內容在線上是可讀的。敏感環境建議配對與
  流量走 VPN／SSH tunnel，或在可達端前面架 TLS reverse proxy。
- listener 預設關閉（`config frame_link.enabled`）；第一次按「產生配對碼」會
  自動開啟。收檔上限 512MB。

## 設定（config.json `frame_link` 區塊）

```json
{
  "enabled": false,
  "listen_host": "0.0.0.0",
  "listen_port": 8767,
  "frame_name": "",        // 空 = hostname
  "frame_id": "…",          // 首次啟動自動產生，勿手改（peers 金鑰綁定它）
  "peers": { "<peer frame_id>": { "name", "host", "port", "secret", "added" } }
}
```

state 檔在 `~/.local/state/shellframe/`（inbox 記錄、outbox 佇列、staging 檔）。
模組是 `frame_link.py`——改動需要 `sfctl restart`（不是 reload）。
回歸測試：`tests_frame_link.py`（兩個 instance 同 process 端到端）。
