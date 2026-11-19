#!/usr/bin/env python3
"""The icon set (web/icons.js): every name a call site asks for is drawn, an icon is only decoration (aria-hidden,
currentColor, thin line), the places that used an emoji or a symbol as an icon have none any more, and what is
protocol or the person's own content still has its characters. Reads the files; no browser.

Run: .venv/bin/python tests_icons.py
"""
import re
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
WEB = HERE / "web"
passed = failed = 0


def check(name, ok, detail=""):
    global passed, failed
    if ok:
        passed += 1
        print(f"  PASS  {name}")
    else:
        failed += 1
        print(f"  FAIL  {name}  {detail}")


def read(p):
    return Path(p).read_text(encoding="utf-8")


SRC = {p.name: read(p) for p in sorted(WEB.glob("*")) if p.suffix in (".js", ".html", ".css") and p.name != "icons.js"}
ICONS_JS = read(WEB / "icons.js")
DRAWN = dict(re.findall(r'^\s*"([a-z0-9-]+)": "(.*)",?$', ICONS_JS, re.M))

print("1. the set")
check("icons.js draws a set of icons", len(DRAWN) >= 50, len(DRAWN))
check("every icon is plain SVG shapes (no script, no external reference, no fixed colour)",
      all(not re.search(r"<script|href=|url\(|#[0-9a-fA-F]{3,6}\b|rgb", d.replace('\\"', '"')) for d in DRAWN.values()))
check("…and only currentColor or none as a colour", all(set(re.findall(r'(?:fill|stroke)=\\"([^"\\]+)\\"', d)) <= {"currentColor", "none"} for d in DRAWN.values()))
check("the markup is decorative: aria-hidden and not focusable", 'aria-hidden="true"' in ICONS_JS and 'focusable="false"' in ICONS_JS)
base = read(WEB / "icons.css")
check("the base rule is the thin line: 1.5px, round caps and joins, no fill, currentColor",
      all(x in base for x in ("stroke-width: 1.5px", "stroke-linecap: round", "stroke-linejoin: round", "fill: none", "stroke: currentColor")))
check("no style is injected from the script, nothing is fetched", "createElement('style')" not in ICONS_JS and "fetch(" not in ICONS_JS and "http" not in ICONS_JS)
check("index.html loads icons.js before bots.js, and the stylesheet",
      (lambda h: 0 <= h.find('src="icons.js"') < h.find('src="bots.js"') and 'href="icons.css"' in h)(SRC["index.html"]))
check("office.html loads both too", 'src="icons.js"' in SRC["office.html"] and 'href="icons.css"' in SRC["office.html"])

print("2. every name that is asked for is drawn")
asked = {}                                              # name -> where


def ask(name, where):
    asked.setdefault(name, where)


for fname, text in SRC.items():
    for m in re.finditer(r"SFIcons\.html\(", text):
        depth, i, arg = 1, m.end(), ""
        while i < len(text) and depth:
            c = text[i]
            depth += (c == "(") - (c == ")")
            if depth == 0 or (depth == 1 and c == ","):
                break
            arg += c
            i += 1
        arg = re.sub(r"[=!]==\s*'[^']*'", "", arg)
        for n in re.findall(r"'([a-z0-9-]+)'", arg):
            ask(n, fname)
    for n in re.findall(r'data-sfi="([a-z0-9-]+)"', text):
        ask(n, fname)
    for n in re.findall(r"\bicon: '([a-z0-9-]+)'", text):          # tables: entry.js FEATURES, the maintenance actions, a chip's state
        ask(n, fname)
m = re.search(r"const FOUND = \{(.*?)\n  \};", SRC["connect.js"], re.S)
for n in re.findall(r"\['([a-z0-9-]+)',", m.group(1)):
    ask(n, "connect.js")
check("the call sites ask for something", len(asked) >= 40, len(asked))
missing = {n: w for n, w in asked.items() if n not in DRAWN}
check("every icon name used in web/ exists in icons.js", not missing, missing)
unused = sorted(set(DRAWN) - set(asked))
check("…and every drawn icon is used somewhere (nothing dead in the set)", not unused, unused)

print("3. the places that were an emoji or a symbol have none")
COLOUR = re.compile("[\U0001F300-\U0001FAFF✅✨⭐⏳⏹⏸⏰♻⬅➡⬇⬆☁⚡⚙⚠✔️]")
MONO = re.compile("[✕✎★↻⟳⟲☰✦☆]|&times;")
# What stays, by the exact words of the line: content the person chose, protocol or Telegram's own button, a rule shared
# with Python, a plain-text tooltip, a status line that is text.
KEEP = [
    "EMOJI_LIST = [",                    # the preset icon picker: content
    "id=\"save-icon\"", "|| '⚙️'",       # a preset's own icon
    "✅ Apply",                          # Telegram's button, named in the settings text
    "'⏳\\u2060 ' : '⏳ '", "/⏳\\u2060/g",  # botView: [[SF:YELLOW:why]] reads "⏳ why" (agent_link.preview_of does the same); drawn as an icon on screen
    "tip.push('⚠ 本次更新失敗",          # a plain-text tooltip
    "⚠ binary 已裝但缺少模型",           # one line of the STT check's text output
]
left = []
for fname, text in SRC.items():
    for i, line in enumerate(text.split("\n"), 1):
        if re.match(r"\s*(//|/\*|\*|<!--)", line):
            continue
        code = re.sub(r"/\*.*?\*/", "", re.sub(r"\s//\s.*$", "", line))
        if (COLOUR.search(code) or MONO.search(code)) and not any(k in line for k in KEEP):
            left.append(f"{fname}:{i}: {line.strip()[:110]}")
check("no colour emoji or icon-like symbol (✕ ✎ ★ ↻ ⟳ ⟲ ☰ ✦ &times;) is left in web/ outside the kept list", not left, left[:8])
check("the entry row, the mic and the foot draw icons: no glyph left in their markup",
      "☰" not in SRC["index.html"] and 'data-sfi="mic"' in SRC["index.html"] and "SFIcons.html('bot')" in SRC["entry.js"])
check("entry.js stores icon names, not emoji", re.findall(r"icon: '([^']*)'", SRC["entry.js"]) == ["bot", "office"], re.findall(r"icon: '([^']*)'", SRC["entry.js"]))
check("the two loop sites draw the loop icon: the tab bar button and the overview count",
      'id="btn-board"' in SRC["index.html"] and re.search(r'id="btn-board"[^>]*data-sfi="loop"', SRC["index.html"]) is not None
      and "SFIcons.html('loop')} ${c.loop}" in SRC["index.html"])
sentences = {"bots.js": ["the ✎ on", "⭐ the primary", "的 ✎", "⭐ 主要"], "chat.js": ["press 🎙", "按 🎙"],
             "index.html": ["▲ =", "▲ 表示", "介面錄音按鈕 🎙", "設定 → 🎙", "Settings → 🎙", "「📁 檔案」", "✕ 斷開配對", "💡 <span"]}
bad = [(f, s) for f, ss in sentences.items() for s in ss if s in SRC[f]]
check("the sentences that named an icon by its emoji are reworded", not bad, bad)

print("4. protocol and content keep their characters")
py = {p.name: read(p) for p in HERE.glob("*.py") if not p.name.startswith("tests_")}
check("the mic's voice label sent to the tab still starts 🎙[", any("🎙[" in t for t in py.values()))
check("Telegram's confirm button still says ✅ Apply", any("✅ Apply" in t for t in py.values()))
check("the [[SF:…]] markers are untouched in the page's parser", "[[\\s*SF" in SRC["chat.js"] or "SF\\s*:" in SRC["chat.js"])
check("the yellow marker still reads ⏳ in a preview (the Python rule's twin)", "'⏳ '" in SRC["chat.js"] and '"⏳ "' in py["agent_link.py"])
check("the preset icon picker still offers its emoji", "'🚀'" in SRC["index.html"] and "'🤖'" in SRC["index.html"])
check("the bot avatars (emoji the person picked) are not touched: bots.py keeps its default preset icons", "🚀" in py["main.py"])

print()
print(f"Results: {passed} passed, {failed} failed")
sys.exit(1 if failed else 0)
