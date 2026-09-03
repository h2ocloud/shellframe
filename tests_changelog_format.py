#!/usr/bin/env python3
"""CHANGELOG 格式守門（規範見 docs/changelog-guide.md）。

這是一個 public repo，CHANGELOG 是外部使用者唯一會讀的變更記錄。導入這支檢查之前，
條目長期直接引用維護者的口語 prompt（「好爛」「太小了」「有點暴力」），而
DEVELOPMENT.md 早就要求「中英對照」卻一直沒被執行——沒有機制的規範等於沒有規範。

只檢查**最新版**段落，舊條目不追溯。

跑法：.venv/bin/python tests_changelog_format.py
"""
import json
import re
import sys
from pathlib import Path

HERE = Path(__file__).parent
CHANGELOG = HERE / "CHANGELOG.md"
VERSION_JSON = HERE / "version.json"
GUIDE = "docs/changelog-guide.md"

ALLOWED_SECTIONS = {"Fixes", "Changes", "Added", "Internal"}

# 人名與私人對話痕跡。要指涉回報來源就寫 reported in daily use／日常使用中回報。
NAME_BLACKLIST = ("Howard", "howard", "HOWARD")
# 口語抱怨：中文引號裡出現這些字，幾乎都是把 prompt 直接貼進來
PROMPT_TELLS = ("好爛", "太小", "太擠", "很暴力", "有點暴力", "壓迫", "破圖",
                "你這樣", "我希望", "幫我", "可以嗎", "沒辦法這樣")

passed = failed = 0


def check(name, ok, detail=""):
    global passed, failed
    if ok:
        passed += 1
        print(f"  [PASS] {name}")
    else:
        failed += 1
        print(f"  [FAIL] {name}")
        if detail:
            for line in str(detail).splitlines()[:6]:
                print(f"         {line}")


def latest_section(text):
    """回傳 (版本號, 日期, 段落內容)。"""
    m = re.search(r"^## v(\d+\.\d+\.\d+) \((\d{4}-\d{2}-\d{2})\)\s*$", text, re.M)
    if not m:
        return None, None, None
    start = m.end()
    nxt = re.search(r"^## v", text[start:], re.M)
    body = text[start:start + nxt.start()] if nxt else text[start:]
    return m.group(1), m.group(2), body


def cjk_chars(s):
    return len(re.findall(r"[一-鿿]", s))


def latin_words(s):
    """英文句子用的字：至少三個字母、排除純技術 token（含底線/點/斜線的）。"""
    plain = re.sub(r"`[^`]*`", " ", s)                 # 去掉 inline code
    plain = re.sub(r"[\w./_-]*[./_][\w./_-]*", " ", plain)  # 去掉檔名/路徑/識別字
    return len(re.findall(r"\b[A-Za-z]{3,}\b", plain))


text = CHANGELOG.read_text(encoding="utf-8")
ver, date, body = latest_section(text)

check("最新版標題符合 `## vX.Y.Z (YYYY-MM-DD)`", bool(ver),
      "找不到符合格式的版本標題；規範見 " + GUIDE)

if not ver:
    print(f"\nResults: {passed} passed, {failed} failed\n{failed} FAILED")
    sys.exit(1)

pkg = json.loads(VERSION_JSON.read_text(encoding="utf-8")).get("version")
check(f"version.json ({pkg}) == CHANGELOG 最新版 ({ver})", pkg == ver,
      "bump 漏了一邊；兩邊要一起改")

sections = re.findall(r"^### (.+?)\s*$", body, re.M)
check("有 ### 分區", bool(sections), "至少要有一個 Fixes/Changes/Added/Internal")
bad = [s for s in sections if s not in ALLOWED_SECTIONS]
check("分區名都在允許清單內", not bad,
      f"不認得的分區: {bad}；允許: {sorted(ALLOWED_SECTIONS)}")

zh, en = cjk_chars(body), latin_words(body)
check(f"有足量中文內容（{zh} 字）", zh >= 60, "雙語缺一不可，中文段落太短")
check(f"有足量英文內容（{en} 詞）", en >= 60,
      "雙語缺一不可——英文段落太短。這是 public repo，"
      "每個條目要英文先、中文後（見 " + GUIDE + "）")

# 人名改成**全檔**檢查：導入規範時已把舊條目一併清乾淨（55 處），所以這條
# 可以守全檔而不只是最新版——留一個乾淨的基線，之後任何一版寫進人名都會紅燈。
# 檢查前剝掉 inline code：技術識別字裡本來就可能帶名字（LaunchAgent 的
# `com.howard.` label 前綴就是一例），那不是人名引用。
text_prose = re.sub(r"`[^`]*`", " ", text)
hits = [n for n in NAME_BLACKLIST if n in text_prose]
check("全檔不含人名", not hits,
      f"出現 {hits}；要指涉回報來源請寫 reported in daily use／日常使用中回報")

quoted = re.findall(r"[「『]([^」』]{2,40})[」』]", body)
tells = sorted({t for q in quoted for t in PROMPT_TELLS if t in q})
check("引號裡沒有原始 prompt 的口語", not tells,
      f"疑似直接貼上對話：{tells}；請翻譯成客觀症狀描述")

check(f"規範文件存在（{GUIDE}）", (HERE / GUIDE).exists())

print(f"\nResults: {passed} passed, {failed} failed")
print("ALL PASS" if not failed else f"{failed} FAILED")
sys.exit(1 if failed else 0)
