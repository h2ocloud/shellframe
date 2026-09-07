#!/usr/bin/env python3
"""同一支 CLI 的幾個啟動器要收成一組，不要讀起來像重複（v0.35.10）。

回報：「新增工作階段」的清單看起來有很多重複。資料其實沒錯——同一支 CLI 有
兩個啟動器（雲端、以及接家中地端模型閘門的 sf-*-home），但在 8 列平面清單裡
名稱只差一個括號，讀起來就是同一個東西出現兩次。

順帶修掉一個真的分類缺陷：`sf-opencode-home` 這種 wrapper 名稱在 provider
registry 裡沒有登記，`worker_kind` 因此回 other——那個分頁的 provider 標示、
帳號判斷、以及這裡的分組全都會落到錯的地方。改成從 `sf-` 前綴推導。

這支驗後端分組與前端渲染兩半，並存截圖。

需要 playwright；沒裝就 SKIP（後端那半照樣會跑）。

跑法：python3 tests_preset_groups.py
"""
import json
import os
import pathlib
import re
import subprocess
import sys
from unittest.mock import MagicMock

HERE = pathlib.Path(__file__).parent
SHOT = HERE / "qa-shots" / "preset-groups.png"

passed = failed = 0


def check(name, ok, detail=""):
    global passed, failed
    if ok:
        passed += 1
        print(f"  [PASS] {name}")
    else:
        failed += 1
        print(f"  [FAIL] {name}  {detail}")


# ── 後端：分類與分組 ─────────────────────────────────────────────────────────
sys.modules['webview'] = MagicMock()
sys.modules['bridge_telegram'] = MagicMock()
sys.path.insert(0, str(HERE))
import agent_status as A          # noqa: E402
import main as _main             # noqa: E402

check("sf-<provider>-<變體> 認得出 CLI（sf-opencode-home 原本是 other）",
      A.worker_kind("sf-opencode-home") == "opencode",
      A.worker_kind("sf-opencode-home"))
check("sf-claude-home 也認得", A.worker_kind("sf-claude-home") == "claude")
check("registry 裡登記過的照舊", A.worker_kind("sf-pi-spark") == "pi")
check("沒有 sf- 前綴的不亂猜",
      A.worker_kind("my-opencode-thing") == "other",
      A.worker_kind("my-opencode-thing"))
check("sf- 後面不是已知 CLI 的也不亂猜",
      A.worker_kind("sf-notes") == "other", A.worker_kind("sf-notes"))
# 地端閘門的啟動器不該去顯示雲端額度——分類與額度讀取器是兩件事
import usage_probe as U          # noqa: E402
check("額度讀取器不受影響（sf-claude-home 仍不套 Claude 雲端額度）",
      U.detect_ai("sf-claude-home") is None, str(U.detect_ai("sf-claude-home")))

api = object.__new__(_main.Api)
PRESETS = [
    {"name": "Bash", "cmd": "bash", "icon": "▶"},
    {"name": "Claude", "cmd": "claude --permission-mode bypassPermissions", "icon": "🚀"},
    {"name": "Claude (家用地端)", "cmd": "sf-claude-home", "icon": "🏠"},
    {"name": "Codex", "cmd": "sf-codex --search", "icon": "🤖"},
    {"name": "OpenCode", "cmd": "/opt/bin/opencode", "icon": "🧩"},
    {"name": "OpenCode (家用地端)", "cmd": "sf-opencode-home", "icon": "🏠"},
]
_orig_load = _main.load_config
try:
    _main.load_config = lambda: {"presets": [dict(p) for p in PRESETS]}
    groups = json.loads(api.preset_groups())
finally:
    _main.load_config = _orig_load

titles = [g["title"] for g in groups]
check("組數＝4（Bash／Claude／Codex／OpenCode）", len(groups) == 4, str(titles))
check("順序沿用 config，組的位置＝第一個成員的位置",
      titles == ["Bash", "Claude Code", "Codex", "OpenCode"], str(titles))
claude = next(g for g in groups if g["title"] == "Claude Code")
check("Claude 那組有兩個成員", len(claude["items"]) == 2, str(claude))
check("預設那個的區別字是空的",
      claude["items"][0]["variant"] == "", str(claude["items"][0]))
check("變體的區別字剝掉了組名與括號",
      claude["items"][1]["variant"] == "家用地端", str(claude["items"][1]))
oc = next(g for g in groups if g["title"] == "OpenCode")
check("sf-opencode-home 收進 OpenCode 那組（不再自己一組）",
      len(oc["items"]) == 2, str(oc))
check("認不出 CLI 的自己一組，組名就是它的名字",
      groups[0]["items"][0]["name"] == "Bash" and len(groups[0]["items"]) == 1)
check("cmd 有完整帶出來（列底下要顯示）",
      all(i["cmd"] for g in groups for i in g["items"]))

# ── 前端：渲染 ───────────────────────────────────────────────────────────────
try:
    from playwright.sync_api import sync_playwright
except ImportError:
    if os.environ.get("_PRESETGRP_QA_REEXEC") != "1":
        env = dict(os.environ, _PRESETGRP_QA_REEXEC="1")
        sys.exit(subprocess.call(["python3", str(pathlib.Path(__file__).resolve())], env=env))
    print("SKIP  tests_preset_groups.py 的前端那半（沒裝 playwright）")
    print(f"\nResults: {passed} passed, {failed} failed")
    print("ALL PASS" if not failed else f"{failed} FAILED")
    sys.exit(1 if failed else 0)

html = (HERE / "web/index.html").read_text(encoding="utf-8")
STYLE = re.search(r"<style>(.*?)</style>", html, re.S).group(1)


def grab(pattern):
    m = re.search(pattern, html, re.S)
    assert m, f"index.html 找不到：{pattern}"
    return m.group(0)


ROW = grab(r"  function presetRow\(item, grouped, groupTitle\) \{.*?\n  \}\n")
RENDER = grab(r"  async function renderPresets\(\) \{.*?\n  \}\n")

PAGE = """<!doctype html><meta charset="utf-8">
<style>__STYLE__
  body { background:#1a1b26; margin:0; padding:16px; }
  .modal { width: 460px; }
</style>
<div class="modal">
  <h2 style="font-size:15px;color:#c0caf5;margin:0 0 12px">新增工作階段</h2>
  <div class="preset-list" id="preset-list"></div>
</div>
<script>
const $presets = document.getElementById('preset-list');
const config = { presets: [] };
const esc = (s) => { const d = document.createElement('div');
                     d.textContent = s == null ? '' : s; return d.innerHTML; };
window.__opened = null;
function openSession(cmd, name) { window.__opened = { cmd, name }; }
function closeModal() { window.__closedModal = true; }
window.__deleted = null;
const pywebview = { api: {
  preset_groups: () => Promise.resolve(GROUPS_JSON),
  delete_preset: (name) => { window.__deleted = name;
                             return Promise.resolve('{"presets":[]}'); },
}};
__ROW__
__RENDER__
window.ready = renderPresets().then(() => true);
</script>"""
PAGE = (PAGE.replace("__STYLE__", STYLE).replace("__ROW__", ROW)
            .replace("__RENDER__", RENDER)
            .replace("GROUPS_JSON", json.dumps(json.dumps(groups, ensure_ascii=False))))

tmp = HERE / ".presetgrp_qa.html"
tmp.write_text(PAGE, encoding="utf-8")
SHOT.parent.mkdir(exist_ok=True)
try:
    with sync_playwright() as pw:
        browser = pw.chromium.launch()
        page = browser.new_page(viewport={"width": 500, "height": 640},
                                device_scale_factor=2)
        page.goto(tmp.resolve().as_uri())
        page.wait_for_selector(".preset-item", timeout=5000)

        # inner_text 拿到的是算繪後的字，組標題有 text-transform: uppercase
        heads = [h.strip().lower()
                 for h in page.locator(".preset-group-head").all_inner_texts()]
        check("只有兩個以上成員的才出組標題",
              sorted(heads) == ["claude code", "opencode"], str(heads))
        check("六個啟動器全部畫出來",
              page.locator(".preset-item").count() == 6,
              str(page.locator(".preset-item").count()))
        check("組內的列有縮排",
              page.locator(".preset-item.p-grouped").count() == 4,
              str(page.locator(".preset-item.p-grouped").count()))
        check("Bash 沒被當成一組（沒縮排）",
              page.locator(".preset-item:not(.p-grouped)").count() == 2)

        variants = page.locator(".p-variant").all_inner_texts()
        check("組內顯示區別字，預設那個寫「預設」",
              sorted(v.strip() for v in variants)
              == ["家用地端", "家用地端", "預設", "預設"], str(variants))

        # 點下去要帶「真正的指令」，不是組名
        page.locator(".preset-item.p-grouped").nth(1).click()
        opened = page.evaluate("window.__opened")
        check("點變體那一列開的是它自己的指令",
              opened and opened["cmd"] == "sf-claude-home", str(opened))
        check("開分頁時帶的名字是 preset 的全名（不是區別字）",
              opened and opened["name"] == "Claude (家用地端)", str(opened))

        page.locator(".preset-item.p-grouped").nth(0).locator(".p-del").click()
        check("✕ 刪的是那一列自己的 preset",
              page.evaluate("window.__deleted") == "Claude",
              str(page.evaluate("window.__deleted")))

        page.locator(".modal").screenshot(path=str(SHOT))
        check(f"截圖已存（{SHOT.relative_to(HERE)}）", SHOT.exists())
        browser.close()
finally:
    tmp.unlink(missing_ok=True)

print(f"\nResults: {passed} passed, {failed} failed")
print("ALL PASS" if not failed else f"{failed} FAILED")
sys.exit(1 if failed else 0)
