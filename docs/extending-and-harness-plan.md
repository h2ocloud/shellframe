# ShellFrame 擴充機制 + 自有 Harness 規劃

2026-08-15 起草。對應四項需求：眼鏡外掛卸載根治、擴充 SDK/skill、
自有 harness（疑難雜症/調度）、地端 AI 入口明朗化。

---

## 0. 現況實測（2026-08-15，別憑記憶）

| 端點 | 狀態 | 內容 |
|---|---|---|
| LiteLLM Gateway `190:4000` | 活著 | **掛 0 個模型**（`/v1/models` 回空陣列） |
| vLLM `190:8000` | **掛掉** | 無回應（35B 的老 OOM crash loop） |
| Ollama `190:11434` | 活著 | 12 個模型：`gemma4:26b`、`gemma4-uncensored:31b`、`gpt-oss:20b`、`huihui_ai/gpt-oss-abliterated:120b-q3_K_M`、`deepseek-ocr`、`qwen3-embedding:0.6b` … |

**結論**：「入口不明朗」不是感覺問題——統一總機（Gateway）目前是空殼，
真正有模型的是 Ollama。27B/35B 之爭還沒定案，所以**任何寫死模型名的設計都會很快過期**。

---

## A. 眼鏡外掛「卸載不了」根治

### 已完成（本次）
- 根因：`com.h2ocloud.rokid-bridge-listener` LaunchAgent 帶 `KeepAlive=true`，
  設定頁停用 plugin **完全不碰它** → listener 一直被 launchd 復活。
- 處置：`launchctl unload` + plist 移到 `.disabled-agents/`（可回復）、
  進程確認消失、config `plugins.installed/enabled` 移除 rokid-bridge。
- 次要根因：`marketplace_uninstall` 對 `bundled:true` 的 plugin 不刪目錄，
  只翻設定旗標；目錄留著會讓人以為沒卸載。

### 待做（根治）
1. **manifest 宣告 side-effect**。`manifest.json` 新增：
   ```json
   "services": [
     {"type": "launchagent", "label": "com.h2ocloud.rokid-bridge-listener"},
     {"type": "process", "match": "channels/rokid-bridge/listen.ts"}
   ]
   ```
2. **SDK 生命週期 hook**：`on_install(api)` / `on_uninstall(api)`，plugin 自己
   收尾（停服務、刪快取）。host 在 uninstall 時：先呼叫 hook → 再依 manifest
   `services` 兜底清理（unload LaunchAgent、kill 殘留進程）→ 才翻設定旗標。
3. **卸載後驗證**：uninstall 回傳前實際檢查「LaunchAgent 已不在 `launchctl list`、
   宣告的進程不存在」，失敗就明講哪一項沒清掉，而不是回 `ok:true`。
   （教訓：Hermes gateway 復活血案、本次 rokid。）
4. bundled plugin 的 UI 文案改成「停用（內建，保留檔案）」，跟真正的 Uninstall 分開。

---

## B. 擴充 SDK / skill

### 現有骨架（`plugin_sdk.py`，已相當完整）
`SFPlugin` hooks：`on_load` / `on_session_change` / `on_session_open` /
`on_session_close` / `sidebar_badge` / `settings_panel` / `asset`；
`PluginRegistry` 負責載入與 dispatch；`plugin_action()` 供前端呼叫。

### 待補
1. **生命週期**：`on_install` / `on_uninstall`（見 A-2），加 `on_unload`
   讓 plugin 在 reload 時放掉資源（thread/socket）。
2. **穩定的 host API 介面**：目前 `PluginHostAPI` 能力未文件化。至少開放
   `send_to_session(sid, text)`、`peek(sid)`、`list_sessions()`、
   `notify(text)`、`read_setting/write_setting(ns, key)`（namespace 隔離，
   plugin 不能亂寫別人的 key）。
3. **文件 + 範本**：
   - `docs/plugin-authoring.md`：manifest 欄位表、hook 生命週期圖、
     「Hello World」20 行範例、除錯方式（log 在哪、怎麼 reload）。
   - `shellframe_plugins/_template/`：可直接 copy 的骨架（manifest + plugin.py
     + settings.html），`sfctl plugin new <name>` 一鍵產生。
4. **skill 形式**：另外給一份 `~/.claude/skills/shellframe-plugin/SKILL.md`，
   讓 Claude/Codex 被要求「幫我寫個 ShellFrame 外掛」時，自動照規範產出
   （含 manifest、hook、卸載清理）。這是「讓想擴充的人方便」最直接的一步。
5. **安全邊界**（若要對外開放才需要）：plugin 執行在 host 進程內、有完整權限。
   對外前至少要：安裝前顯示 manifest 宣告的權限/services、marketplace 來源白名單。
   **決策點**：只給自己用 → 可跳過；要對外 → 這段必須先做。

---

## C. ShellFrame 自有 Harness

**定位**：專職處理 ShellFrame 自身的疑難雜症與調度判讀的小型 LLM 迴路——
不是通用聊天，而是「看 log/畫面 → 判斷 → 建議或執行既定動作」。

### 設定（`settings.harness.*`，設定頁新增一區）
```
harness_enabled      : bool（預設關）
harness_base_url     : str  例 http://192.168.51.190:4000/v1（OpenAI 相容）
harness_api_key      : str  （存 config；UI 遮罩顯示）
harness_model        : str  預設 "auto" → 走 D 的探測鏈
harness_timeout_s    : int  預設 30
```

### 適用場景（先做這三個，都是既有痛點）
1. **畫面判讀**：分頁卡住/等輸入/跳權限對話框/rate-limit 的分類——現在靠一堆
   手寫 regex（`_RATE_LIMIT_RE`、`_detect_menu_prompt`…），regex 一直被新 UI 打敗。
   harness 收「畫面尾端 N 行」回結構化 `{state, reason, suggested_action}`。
2. **掉訊/失聯自診**：把 bridge log 尾段 + slot 狀態丟給 harness，回「哪一環斷了」。
3. **調度建議**：`sfctl` 動作建議（該 peek 誰、該不該重啟），**只建議不自動執行**，
   除非明確開 `harness_autopilot`（預設關）。

### 邊界（重要）
- harness **絕不**碰使用者對話內容轉發路徑——它只讀畫面/log 做判讀，
  壞掉時降級回現有 regex，不能讓它變成新的單點故障。
- 逾時/失敗一律 fallback，且寫 log（`[harness] …`），不靜默。

---

## D. 地端 AI 入口明朗化

**目標**：一個入口、模型可換、掛掉能自己退。不寫死 27B/35B。

### 探測鏈（`harness_model = "auto"` 時）
1. `GET {base_url}/models` → 有非空清單就用**第一個**（或符合
   `harness_model_prefer` 的那個，例如偏好含 `27b`/`35b`/`gpt-oss`）。
2. Gateway 空/不可達 → 退 vLLM `:8000/v1/models`。
3. 再退 Ollama `:11434/api/tags`（名稱轉 OpenAI 相容呼叫）。
4. 全掛 → harness 停用、回報「地端無可用模型」，不阻塞任何既有功能。
- 探測結果**快取 60s**，避免每次判讀都打三個端點。
- 側欄/設定頁顯示目前實際命中的端點與模型（＝「入口明朗」的可見化）。

### 建議的根本解（一次到位，需維護者拍板）
現在 Gateway 掛 0 模型，等於統一總機沒接線。**把 Ollama 的模型註冊進
LiteLLM Gateway**，讓 `190:4000` 成為唯一入口：
- ShellFrame harness、tool79 對話工作台、其他工具全部只填一個 URL；
- 之後 27B/35B 誰勝出，只改 Gateway 設定，**所有 client 零改動**；
- vLLM 掛掉時 Gateway 可自動 fallback 到 Ollama（LiteLLM 原生支援 fallbacks）。

這件事屬於 190 機器的設定，不在 ShellFrame repo；建議獨立一個 tab 做，
ShellFrame 這邊只要「填一個 base_url」即可。

---

## 建議順序

1. ~~眼鏡外掛卸掉（止血）~~ ✅ 已完成
2. ~~雙擊改名誤判修掉~~ ✅ 已完成
3. **A 的根治**（uninstall 生命週期＋services 清理＋卸載後驗證）— 小、明確、防再犯
4. **D 的探測鏈**（純讀取、無風險，先讓入口可見）
5. **C 的 harness**（先只做「畫面判讀」一個場景，證明有用再擴）
6. **B 的文件/範本/skill**（擴充生態，可獨立進行）
7. Gateway 接線（190 機器，另開 tab）

## 待維護者拍板

- **Q1**：SDK 只給自己用，還是要對外開放？（影響 B-5 安全邊界要不要做）
- **Q2**：harness 端點——先直接指 Ollama（現在唯一活的），還是先把 Gateway
  接好再指 Gateway？（後者較一勞永逸）
- **Q3**：harness 要不要允許自動執行動作（autopilot），還是永遠只給建議？
