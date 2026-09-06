#!/usr/bin/env python3
"""Public-repo 衛生守門：原始碼裡不留人名、客戶名、私人對話。

`tests_changelog_format.py` 只守 CHANGELOG，但外部讀者看得到的不只 CHANGELOG——
註解、docstring、測試 fixture 一樣是公開的。導入這支之前，repo 裡散著 80 多處
維護者的名字加日期、幾十句直接貼進來的原始 prompt（「太小了」「有點暴力」
「我很常錯頻」），還有客戶與同事的真名躺在測試資料裡。

規範同 docs/changelog-guide.md：要指涉回報來源就寫 reported in daily use／
日常使用中回報；症狀要翻成客觀描述，不要貼原話。

豁免按「長什麼樣子」判斷，不是整個檔案放行：LaunchAgent label／bundle id
（`com.<name>.*`）、姓名全稱的作者署名、inline code 與 URL。**不豁免程式字串**
——客戶名躺在 routing table 的字串裡也是外洩，那正是要抓的東西。

跑法：.venv/bin/python tests_repo_hygiene.py
"""
import re
import subprocess
import sys
from pathlib import Path

HERE = Path(__file__).parent
GUIDE = "docs/changelog-guide.md"

SCAN_SUFFIXES = {".py", ".js", ".html", ".md", ".sh", ".json", ".css"}
SKIP_DIRS = {".git", "node_modules", ".venv", "web/vendor", "dist", "build"}
# 這支自己與 CHANGELOG 守門都必須寫出被禁的字才能檢查，跳過它們自身。
SELF = {"tests_repo_hygiene.py", "tests_changelog_format.py"}

# 人名。維護者的名字只在「署名」處合法（README 作者欄、About 對話框、授權條款、
# plugin marketplace 的 author 欄）——那是姓名全稱，不是把回報者寫進註解。
PERSON_NAMES = ("Howard", "howard", "HOWARD", "Warren", "Vivian")
ATTRIBUTION_RE = re.compile(r"Howard Wu")

# 客戶／內部代號。外部讀者不該從這個 repo 讀到我們服務哪些客戶。
CLIENT_NAMES = ("台壽", "台灣人壽", "台新", "永豐", "兆豐", "凱基", "南山",
                "新光", "遠銀", "中華郵政", "精誠", "新逸", "康和")

# 口語抱怨：中文引號裡出現這些字，幾乎都是把 prompt 直接貼進來。
PROMPT_TELLS = ("好爛", "太小了", "太擠", "有點暴力", "很暴力", "壓迫",
                "我很常", "我希望", "你這樣", "沒辦法這樣", "幫我修",
                "超不正常", "修好來")

passed = failed = 0


def check(name, ok, detail=""):
    global passed, failed
    if ok:
        passed += 1
        print(f"  [PASS] {name}")
    else:
        failed += 1
        print(f"  [FAIL] {name}")
        for line in str(detail).splitlines()[:12]:
            print(f"         {line}")


def tracked_files():
    """git 追蹤的檔案＝會被推上去的檔案。沒有 git 就走檔案系統。"""
    try:
        out = subprocess.run(["git", "ls-files"], cwd=HERE, capture_output=True,
                             text=True, timeout=20)
        if out.returncode == 0 and out.stdout.strip():
            names = out.stdout.splitlines()
        else:
            raise RuntimeError("git ls-files empty")
    except Exception:
        names = [str(p.relative_to(HERE)) for p in HERE.rglob("*") if p.is_file()]
    for rel in names:
        if Path(rel).suffix not in SCAN_SUFFIXES:
            continue
        if any(rel == d or rel.startswith(d + "/") for d in SKIP_DIRS):
            continue
        if rel in SELF:
            continue
        p = HERE / rel
        if p.exists():
            yield rel, p


# LaunchAgent label / bundle id：`com.<name>.<something>`。這種識別字沒得改，
# 但只在這個形狀下豁免——同一個名字寫在句子裡照樣紅燈。
IDENTIFIER_RE = re.compile(r"com\.[a-z0-9_]+\.[a-z0-9_.-]*", re.I)


def prose(text):
    """剝掉識別字會合法出現的地方。程式字串**不剝**——那也是公開內容。"""
    text = re.sub(r"```.*?```", " ", text, flags=re.S)
    text = re.sub(r"`[^`\n]*`", " ", text)          # markdown / 註解裡的 inline code
    text = re.sub(r"https?://\S+", " ", text)
    text = IDENTIFIER_RE.sub(" ", text)
    return text


name_hits, client_hits, quote_hits = [], [], []
scanned = 0
for rel, path in tracked_files():
    try:
        raw = path.read_text(encoding="utf-8")
    except (UnicodeDecodeError, OSError):
        continue
    scanned += 1
    body = prose(raw)
    body = ATTRIBUTION_RE.sub(" ", body)     # 作者署名合法
    for lineno, line in enumerate(body.splitlines(), 1):
        for n in PERSON_NAMES:
            if n in line:
                name_hits.append(f"{rel}:{lineno}  {n}")
        for c in CLIENT_NAMES:
            if c in line:
                client_hits.append(f"{rel}:{lineno}  {c}")
    # 規範文件的「壞例子」必須長成壞例子的樣子，否則教不了東西。人名與客戶名
    # 在它身上照樣要守。
    if rel != GUIDE:
        for q in re.findall(r"[「『]([^」』\n]{2,40})[」』]", body):
            for t in PROMPT_TELLS:
                if t in q:
                    quote_hits.append(f"{rel}  「{q}」")

check(f"掃過 {scanned} 個 git 追蹤檔案", scanned > 20, f"只掃到 {scanned} 個")
check("原始碼不含人名", not name_hits,
      "\n".join(sorted(set(name_hits))) +
      "\n要指涉回報來源請寫 reported in daily use／日常使用中回報（見 " + GUIDE + "）")
check("原始碼不含客戶／內部代號", not client_hits,
      "\n".join(sorted(set(client_hits))) + "\n測試資料請用中性名稱")
check("引號裡沒有原始 prompt 的口語", not quote_hits,
      "\n".join(sorted(set(quote_hits))) + "\n請翻譯成客觀症狀描述")
check(f"規範文件存在（{GUIDE}）", (HERE / GUIDE).exists())

print(f"\nResults: {passed} passed, {failed} failed")
print("ALL PASS" if not failed else f"{failed} FAILED")
sys.exit(1 if failed else 0)
