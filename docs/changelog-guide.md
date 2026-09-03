# Release notes / CHANGELOG 撰寫規範

> **這是一個 public repo。** CHANGELOG 是外部使用者與貢獻者唯一會讀的變更記錄，
> 不是開發過程的私人筆記。
>
> 這份規範由 `tests_changelog_format.py` 強制檢查，`./run_tests.sh` 會跑它。
> 格式不符就是紅燈，發版前擋得住。

## 1. 每個版本的骨架

```markdown
## vX.Y.Z (YYYY-MM-DD)

### Fixes            ← 修 bug。沒有就整段省略
### Changes          ← 行為／介面調整
### Added            ← 新功能
### Internal         ← 重構、測試、文件（使用者無感的變動）
```

- 版本號用 semver，**最新版置頂**。
- 標題行格式固定 `## vX.Y.Z (YYYY-MM-DD)`，檢查腳本會比對
  `version.json` 的版本是否等於 CHANGELOG 的最新版——**漏 bump 會紅燈**。

## 2. 每個條目：英文先，中文後

同一個 bullet 內兩段，**英文在前**（國際讀者優先），空一行接中文：

```markdown
- **Scroll-up history no longer jumps horizontally when it opens.**
  The overlay stayed at `left: 0` after v0.30.11 moved the live pane to start at
  `--hint-gutter`, so opening history shifted the whole view left by the gutter
  width. It now measures the live pane's actual left edge instead of assuming 0.
  Regression test: `tests_scroll_overlay_align.py`.

  **上滑歷史開啟時不再橫向跳動。** v0.30.11 把活畫面左緣推到 `--hint-gutter`，
  overlay 卻仍停在 `left: 0`，一開就整個往左位移一條 gutter。現在改為量測 live
  pane 的實際左緣，而不是假設它是 0。回歸測試：`tests_scroll_overlay_align.py`。
```

兩段講同一件事，不是一段的摘要——中文段不能只寫「同上」。

## 3. 一個條目要回答四件事

| | 要寫什麼 | 反例 |
|---|---|---|
| **症狀** | 使用者看得到的現象 | 「修了一個 bug」 |
| **根因** | 為什麼會這樣 | 「調整了樣式」 |
| **修法** | 改了什麼機制 | 「已修正」 |
| **驗證** | 哪支測試守著 | （空白） |

寫得出根因才代表真的查清楚了。查不出來就誠實寫「尚未確診」，並說明目前的
繞法與量測手段——**不要假裝修好了**。

## 4. 不准出現的東西

- **人名**。維護者、回報者、同事的名字都不行。要指涉來源就寫
  「reported in daily use」／「日常使用中回報」。
- **貼上原始 prompt 或聊天記錄**。使用者當下的口語（「好爛」「太小了」
  「這樣很暴力」）是需求輸入，不是變更記錄。把它翻譯成客觀症狀描述。
- **內部代號、客戶名、專案代稱、內部主機／路徑**。
- 情緒字眼、對自己的檢討（「我又搞錯了」）。要記錄「這是自己造成的回歸」
  可以寫成事實：「regression introduced in vX.Y.Z」／「vX.Y.Z 引入的回歸」。

### 好 / 壞對照

```markdown
✗ 壞：- 修好 Howard 說的「太小了」的問題，字級改回 11px。

✓ 好：- **Tab-name label is legible again.** v0.30.12 shrank it to 9px while
        reducing visual weight, which made it unreadable. Back to 11px; the
        label now overlaps the sidebar edge by 4px so the larger type costs no
        terminal width.

        **分頁名標籤恢復可讀。** v0.30.12 為了降低視覺壓迫把字級砍到 9px，
        結果看不清。字級回到 11px，並讓標籤往側欄那側疊 4px，所以放大字級
        不必多佔終端寬度。
```

## 5. 檢查腳本擋什麼

`tests_changelog_format.py`（納入 `./run_tests.sh`）對**最新版段落**檢查：

1. 標題符合 `## vX.Y.Z (YYYY-MM-DD)`
2. `version.json` 的版本 == CHANGELOG 最新版
3. 有 `### ` 分區，且分區名在允許清單內
4. 同時具備足量中文與足量英文內容（雙語缺一不可）
5. 不含人名與私人對話痕跡（黑名單 + 引號口語偵測）

舊版條目不追溯——規範從導入的那一版往後生效。
