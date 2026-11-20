#!/usr/bin/env python3
"""The office's ops board, in the real page against a stand-in backend (the contract's JSON: office_objects with the `ops` object's
summary fields, office_object_view, office_object_live, office_object_action, ops_state, window.__sfOpsLevel). Demo data only.

  - the switch is off (no `ops` object): no TV, no 維運 button, nothing to hit; on: the TV hangs on the lobby wall (a hit target
    with the tooltip) and the button is in the toolbar
  - the TV's five faces, told apart by the pixels of its quad: ok (green, calm), warn (yellow banner), alert (red banner, red glow,
    a red mark at the corner), muted and none (grey, no red, no yellow, no green)
  - alert pulses (the banner's red moves), "reduce motion" holds it still, and the idle frame rate stays 5 fps with an alert up
  - the TV and the button both open the detail window; it asks the backend `live` on (id, true) with no language, a heartbeat on its
    timer, `live` off at Esc, at the close button and at a click outside, and when another object's popup takes over
  - the window draws the eight kinds of widget, leaves out a kind it does not know and a widget with missing fields (no console error),
    patches by section on every refresh, and never one that holds the focus
  - the rules: a switch and a number send `set_rule` with the right args (the number clamped to min..max), 靜音 1 小時 sends
    `mute` {minutes: 60}, the defaults button `reset_rules`; each ends with the office reading the objects again
  - at 1280 three sections to a row, at 900 two columns, nothing spills sideways
  - temperatures: the TV's face has one more line 「CPU 46° · GPU 43°」 (a reading that is missing is left out, neither leaves no line, a number
    at or over its line, 90 °C for the CPU and 85 °C for the GPU, is in the warning colour), in the 3D and the flat view; the window has a
    「溫度」 section after network and disk in three shapes (CPU and GPU, CPU only, nothing readable: one line that says why), in both languages
  - English: the window, the button, the tooltip and every string drawn on the TV have no Chinese in them
  - the sidebar's 辦公室 button gets a red dot for `alert` only (not muted, ok, warn, none), from window.__sfOpsLevel and from
    ops_state() at load, and the entry row's width does not move; Settings -> Office has the switch and it writes experimental_ops
Headless Chromium and WebKit when playwright has it. Screenshots (Chromium, zh-TW): qa-shots/office-ops-*.png (the TV up close: office-ops-tv-{ok,alert}-{iso,flat}-zoom.png,
office-ops-temp-tv.png; the temperature section: office-ops-temp-detail.png).
Run: .venv/bin/python tests_office_ops_ui.py
"""
import json
import pathlib
import re
import sys
import time
import types

HERE = pathlib.Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
passed = failed = 0


def check(name, ok, detail=""):
    global passed, failed
    if ok:
        passed += 1
        print(f"  [PASS] {name}")
    else:
        failed += 1
        print(f"  [FAIL] {name}  {str(detail)[:500]}")


try:
    from playwright.sync_api import sync_playwright
except ImportError:
    print("SKIP  tests_office_ops_ui.py（沒裝 playwright）")
    sys.exit(0)

_src = (HERE / "tests_office_board_ui.py").read_text(encoding="utf-8")
H = types.ModuleType("office_board_harness")
H.__file__ = str(HERE / "tests_office_board_ui.py")
exec(compile(_src[:_src.index("\ndef run(")], H.__file__, "exec"), H.__dict__)   # its stand-in Python side and helpers, not its run
SHOTS = H.SHOTS
SHOTS.mkdir(exist_ok=True)
NOW = int(time.time())
CJK = re.compile(r"[一-鿿　-〿＀-￯]")

# ── the stand-in backend (the contract's shapes) ─────────────────────────────
PATCH = """
if (window.top === window) {
  const base = window.pywebview.api;
  const F = window.__fake = { objects: [], view: {}, state: {on: true, level: 'none', reason: '', since: null}, live: [], acts: [], views: [], actRes: {ok: true, message: 'ok'}, objCalls: 0, stateCalls: 0 };
  const R = v => Promise.resolve(JSON.stringify(v));
  window.pywebview.api = new Proxy({}, { get: (t, prop) => {
    if (prop === 'then') return undefined;
    switch (prop) {
      case 'office_objects': return () => { F.objCalls++; return R({success: true, objects: F.objects}); };
      case 'office_object_view': return (...a) => { F.views.push(a); return R(F.view[a[a.length - 1]] || F.view.zh || null); };
      case 'office_object_list': return () => R({success: true, groups: [], items: [], total: 0});
      case 'office_object_live': return (...a) => { F.live.push(a); return R({success: true}); };
      case 'office_object_action': return (...a) => { F.acts.push(a); return R(F.actRes); };
      case 'ops_state': return () => { F.stateCalls++; return R(F.state); };
    }
    return base[prop];
  }});
}
"""
BOOKSHELF = {"id": "bookshelf", "title": "書櫃", "furniture": "bookshelf", "place": "lobby:wall", "button": True, "badge": 0, "filters": [], "sorts": [], "actions": []}
PERSONNEL = {"id": "personnel", "title": "人事檔案櫃", "short": "人事", "furniture": "cabinet", "place": "lobby:wall", "button": True, "badge": 0, "filters": [], "sorts": [], "actions": []}


def decl(level="ok", reason="", lang="zh", **over):
    face = None if level == "none" else {"cpu": 23.5, "mem": 93.0 if level == "alert" else 61.0, "pressure": "critical" if level == "alert" else "normal",
                                         "net_in": 1200000, "net_out": 30000, "online": True, "disk_free_pct": 42.3, "spark": [55, 57, 60, 61, 70, 93 if level == "alert" else 62]}
    d = {"id": "ops", "title": "維運" if lang == "zh" else "Ops", "furniture": "tv", "place": "lobby:wall", "button": False, "badge": 1 if level == "alert" else 0,
         "filters": [], "sorts": [], "actions": [], "hint": "", "level": level, "reason": reason, "since": NOW - 720 if reason else None,
         "muted_until": NOW + 2400 if level == "muted" else None, "updated": NOW - 20, "face": face}
    d.update(over)
    return d


def top_switch(lang="zh", value="auto", note=None):
    T = (lambda en, zh: zh) if lang == "zh" else (lambda en, zh: en)
    return {"action": "top_sort", "value": value, "options": [{"id": "auto", "label": T("Auto", "自動")}, {"id": "cpu", "label": "CPU"}, {"id": "mem", "label": T("Memory", "記憶體")}],
            "note": T("Now ranked by memory (memory 81% used)", "現在照記憶體排（記憶體已用 81%）") if note is None else note}


def view(level="alert", lang="zh", rule_value=10, extra=None, sort="auto"):
    T = (lambda en, zh: zh) if lang == "zh" else (lambda en, zh: en)
    cells = ["ok"] * 50 + ["warn"] * 4 + ["alert"] * 5 + ["none"]
    v = {"success": True, "refresh_ms": 600, "title": T("Ops board", "維運戰情"),
         "banner": {"level": level, "text": T("Memory pressure high for 12 minutes", "記憶體壓力高 12 分鐘") if level != "ok" else T("All normal", "一切正常"),
                    "since": NOW - 720 if level != "ok" else None, "rule": "mem_pressure_high" if level != "ok" else ""},
         "sections": [
             {"id": "cpu", "title": "CPU", "widgets": [
                 {"kind": "gauge", "label": T("All cores", "全部核心"), "value": 23.5, "text": "24%", "tone": "ok"},
                 {"kind": "bars", "label": T("Each core", "每顆核心"), "items": [{"label": "P1", "value": 40.0, "group": "p"}, {"label": "P2", "value": 22.0, "group": "p"}, {"label": "E1", "value": 5.0, "group": "e"}]},
                 {"kind": "spark", "label": T("Last 60 seconds", "最近 60 秒"), "unit": "%", "max": 100, "series": [{"label": "CPU", "values": [20, 22, None, 25, 31], "tone": "info"}]}]},
             {"id": "mem", "title": T("Memory", "記憶體"), "widgets": [
                 {"kind": "stack", "label": T("Used 61%", "已用 61%"), "total": 17179869184, "parts": [{"label": "App", "value": 6e9, "tone": "info"}, {"label": T("Wired", "固定"), "value": 2e9, "tone": "warn"}, {"label": T("Compressed", "壓縮"), "value": 1e9, "tone": "dim"}, {"label": T("Cache", "快取"), "value": 3e9, "tone": ""}]},
                 {"kind": "strip", "label": T("Pressure (last 30 minutes)", "壓力（最近 30 分鐘）"), "start": NOW - 1800, "step_s": 30, "cells": cells},
                 {"kind": "stat", "label": T("Swap", "交換區"), "value": "1.2 GB", "sub": T("+0.3 GB in 30 minutes", "30 分鐘 +0.3 GB"), "tone": ""}]},
             {"id": "net", "title": T("Network and disk", "網路與磁碟"), "widgets": [
                 {"kind": "spark", "label": T("Down / up", "下載／上傳"), "unit": "MB/s", "series": [{"label": T("Down", "下載"), "values": [1.0, 1.2, 0.8], "tone": "info"}, {"label": T("Up", "上傳"), "values": [0.2, 0.3, 0.1], "tone": "dim"}]},
                 {"kind": "stat", "label": T("Disk free", "磁碟剩餘"), "value": "42%", "sub": "210 GB", "tone": "ok"}]},
             {"id": "top", "title": T("Top 5 by use", "吃資源前 5 名"), "widgets": [
                 {"kind": "table", "columns": [T("Program", "程式"), "CPU", T("Memory", "記憶體")], "rows": [["示範瀏覽器" if lang == "zh" else "Demo browser", "12%", "1.1 GB"], ["示範編輯器" if lang == "zh" else "Demo editor", "7%", "0.9 GB"]],
                  "switch": top_switch(lang, sort)}]},
             {"id": "day", "title": T("Last 24 hours", "最近 24 小時"), "widgets": [
                 {"kind": "strip", "label": T("Memory pressure (every 5 minutes)", "記憶體壓力（每 5 分鐘）"), "start": NOW - 86400, "step_s": 300, "cells": ["ok"] * 280 + ["warn"] * 4 + ["alert"] * 4}]},
             {"id": "rules", "title": T("What counts as wrong", "什麼算異常"), "widgets": [
                 {"kind": "rules", "items": [
                     {"id": "mem_pressure_high", "label": T('Memory pressure "high" for', "記憶體壓力「高」連續"), "on": True, "value": rule_value, "unit": T("min", "分鐘"), "level": "alert", "min": 1, "max": 120, "step": 1},
                     {"id": "cpu_high", "label": T("CPU above 90% for", "CPU 超過 90% 連續"), "on": False, "value": 5, "unit": T("min", "分鐘"), "level": "warn", "min": 1, "max": 60, "step": 1}]}]}]}
    if extra:
        extra(v)
    return v


def set_objects(page, objs, wait_level=None):
    page.evaluate("(o) => { window.__fake.objects = o; }", objs)
    page.evaluate("async () => { SFOffice.host.objectsChanged(); await new Promise(r => setTimeout(r, 300)); await SFOffice.refresh(); }")
    if wait_level is not None:
        page.wait_for_function("""(lv) => { const f = document.querySelector('#sfo-overlay iframe'); try { const s = f.contentWindow.__office.ops().state(); return s.level === lv; } catch (e) { return false; } }""",
                               arg=wait_level, timeout=8000)
    page.wait_for_timeout(350)


def fake(page):
    return page.evaluate("window.__fake")


def office_page(browser, base, lang="zh", reduced=False, level="ok", reason="", size=(1280, 800), on=True, state=None):
    ctx = browser.new_context(viewport={"width": size[0], "height": size[1]}, reduced_motion="reduce" if reduced else "no-preference")
    page = ctx.new_page()
    page.errors = []
    page.on("pageerror", lambda e: page.errors.append(f"pageerror {e}"))
    page.on("console", lambda m: page.errors.append(f"console {m.text}") if m.type == "error" else None)
    page.add_init_script(H.stub())
    if lang == "en":
        page.add_init_script("if (window.top === window) window.__cfg.settings.language = 'en';")
    page.add_init_script(PATCH)
    page.add_init_script("""if (window.top === window) { window.__fake.objects = %s; window.__fake.view = %s; window.__fake.state = %s; }""" % (
        json.dumps([BOOKSHELF, PERSONNEL] + ([decl(level, reason, lang)] if on else []), ensure_ascii=False),
        json.dumps({"zh": view(level if level in ("ok", "warn", "alert") else "ok", "zh"), "en": view(level if level in ("ok", "warn", "alert") else "ok", "en")}, ensure_ascii=False),
        json.dumps(state or {"on": on, "level": level, "reason": reason, "since": None}, ensure_ascii=False)))
    page.goto(base + "/index.html")
    page.wait_for_selector("#sb-footer-row", timeout=20000)
    page.wait_for_function("window.SFOffice && window.SFBots && SFBots.enabled()", timeout=20000)
    return page


def open_lobby(page):
    fr = H.open_office(page)
    page.wait_for_function("""() => { const f = document.querySelector('#sfo-overlay iframe'); try { return f.contentWindow.__office.objects().objects.length >= 2; } catch (e) { return false; } }""", timeout=10000)
    page.wait_for_timeout(500)
    return fr


# pixels of the TV's quad on the canvas, by colour family
TV_PIX = """() => {
  const pts = __office.opsTv(); if (!pts) return null;
  const cv = document.getElementById('stage'), r = cv.getBoundingClientRect(), k = cv.width / r.width, ctx = cv.getContext('2d');
  const xs = pts.map(p => p.x - r.left), ys = pts.map(p => p.y - r.top);
  const x0 = Math.max(0, Math.floor(Math.min(...xs) * k)), x1 = Math.min(cv.width, Math.ceil(Math.max(...xs) * k)), y0 = Math.max(0, Math.floor(Math.min(...ys) * k)), y1 = Math.min(cv.height, Math.ceil(Math.max(...ys) * k));
  const w = x1 - x0, h = Math.max(1, y1 - y0), d = ctx.getImageData(x0, y0, w, h).data; let red = 0, yel = 0, grn = 0, sat = 0, n = 0, rs = 0, gs = 0, bs = 0;
  const inside = (px, py) => { let c = false; for (let i = 0, j = pts.length - 1; i < pts.length; j = i++) { const a = pts[i], b = pts[j], ay = (a.y - r.top) * k, by = (b.y - r.top) * k, ax = (a.x - r.left) * k, bx = (b.x - r.left) * k;
    if ((ay > py) !== (by > py) && px < (bx - ax) * (py - ay) / (by - ay) + ax) c = !c; } return c; };   // only the TV's own quad, not the wall and the lamp around it
  for (let i = 0; i < d.length; i += 4) { const q = i / 4; if (!inside(x0 + (q % w) + .5, y0 + Math.floor(q / w) + .5)) continue;
    const R = d[i], G = d[i + 1], B = d[i + 2]; n++; rs += R; gs += G; bs += B;
    if (R > 170 && G < 120 && B < 140) red++; else if (R > 190 && G > 140 && B < 130) yel++; else if (G > 150 && R < 190 && B < 130) grn++;
    if (Math.max(R, G, B) - Math.min(R, G, B) > 70) sat++; }
  return { red, yel, grn, sat, n, avg: [rs / n, gs / n, bs / n], box: [x0, y0, w, h] };
}"""

# the strings drawn onto the canvas during one frame (the TV's face is drawn text, so its words are read here)
CAPTURE = """async () => { const out = [], f = CanvasRenderingContext2D.prototype.fillText, ops = __office.ops(), draw = ops.draw;
  ops.draw = function (x, g) { CanvasRenderingContext2D.prototype.fillText = function (s) { out.push(String(s)); return f.apply(this, arguments); };
    try { return draw.call(this, x, g); } finally { CanvasRenderingContext2D.prototype.fillText = f; } };
  await new Promise(r => setTimeout(r, 450)); ops.draw = draw; return out; }"""


# temperatures: the strings the TV draws with the colour each was drawn in
CAPTURE_STYLE = """async () => { const out = [], f = CanvasRenderingContext2D.prototype.fillText, ops = __office.ops(), draw = ops.draw;
  ops.draw = function (x, g) { CanvasRenderingContext2D.prototype.fillText = function (s) { out.push([String(s), String(this.fillStyle)]); return f.apply(this, arguments); };
    try { return draw.call(this, x, g); } finally { CanvasRenderingContext2D.prototype.fillText = f; } };
  await new Promise(r => setTimeout(r, 450)); ops.draw = draw; return out; }"""
HOT = "#ff6b86"


def decl_t(level="ok", cpu_t=46.0, gpu_t=43.0, lang="zh", reason="", **over):
    d = decl(level, reason, lang, **over)
    if d["face"] is not None:
        d["face"] = dict(d["face"], cpu_temp=cpu_t, gpu_temp=gpu_t, gpu_util=12.0 if gpu_t is not None else None)
    return d


def temp_section(lang="zh", variant="both"):
    T = (lambda en, zh: zh) if lang == "zh" else (lambda en, zh: en)
    series = [{"label": "CPU", "values": [44, 45, None, 46, 47, 46, 46, 47, 48, 46], "tone": "info"}]
    if variant == "none":
        ws = [{"kind": "stat", "label": T("Temperature", "溫度"), "value": "—", "tone": "dim",
               "sub": T("This computer cannot read temperatures (macOS needs administrator rights)", "這台電腦讀不到溫度（macOS 需要管理員權限）")}]
    else:
        ws = [{"kind": "stat", "label": T("CPU temperature", "CPU 溫度"), "value": "46.0°C", "sub": "acpitz", "tone": "ok"}]
        if variant == "both":
            ws += [{"kind": "stat", "label": "GPU · NVIDIA GB10", "value": "43°C", "tone": "ok",
                    "sub": T("Shared memory · Power 11.0 W", "共用記憶體 · 功耗 11.0 W")},
                   {"kind": "gauge", "label": T("GPU utilization", "GPU 使用率"), "value": 12.0, "text": "12%", "tone": "info"}]
            series.append({"label": "GPU", "values": [41, 42, 43, 43, 44, 43, 43, 42, 43, 43], "tone": "warn"})
        ws.append({"kind": "spark", "label": T("Last 30 minutes", "最近 30 分鐘"), "unit": "°C", "max": None, "series": series})
    return {"id": "temp", "title": T("Temperature", "溫度"), "widgets": ws}


def with_temp(variant, lang="zh"):
    def extra(v):
        i = [x["id"] for x in v["sections"]].index("net") + 1
        v["sections"].insert(i, temp_section(lang, variant))
    return extra


def tv_click_point(fr):
    return fr.evaluate("(k) => __office.wallPoint(k)", "tv")


def click_tv(page, fr):
    pt = tv_click_point(fr)
    page.mouse.move(pt["x"], pt["y"])
    page.wait_for_timeout(250)
    tip = fr.evaluate("__office.hoverTip()")
    cursor = fr.evaluate("document.getElementById('stage').classList.contains('pt')")
    page.mouse.click(pt["x"], pt["y"])
    page.wait_for_timeout(500)
    return pt, tip, cursor


def win(fr):
    return fr.evaluate("""() => { const v = document.getElementById('opspop'); if (!v || v.hidden) return null;
      const R = e => { const b = e.getBoundingClientRect(); return {l: b.left, t: b.top, r: b.right, b: b.bottom, w: b.width, h: b.height}; };
      const secs = [...v.querySelectorAll('.op-sec')].map(e => ({id: e.dataset.sec, ...R(e)})), body = v.querySelector('#opBody');
      return { title: v.querySelector('#opTitle').textContent, text: v.innerText, secs, box: R(v.querySelector('.oo-box')), sw: body.scrollWidth, cw: body.clientWidth,
               kinds: ['gauge', 'bars', 'stack', 'spark', 'strip', 'table', 'stat', 'rules'].map(k => v.querySelectorAll('.op-w-' + k).length),
               banner: (v.querySelector('.op-banner') || {}).className || '' }; }""")


def run(browser, base, engine):
    shoot = engine == "chromium"

    # ───────────────────────── 1. the switch is off ─────────────────────────
    print(f"[{engine}] the switch is off: no TV, no button")
    page = office_page(browser, base, on=False, level="none")
    fr = open_lobby(page)
    check("no `ops` object: the TV is not on the wall (no quad, no hit target)", fr.evaluate("__office.opsTv()") is None and fr.evaluate("__office.ops().active()") is False)
    check("…and the 維運 button is hidden", fr.evaluate("document.getElementById('opsBtn').hidden") is True)
    check("…a point where the TV would hang is not a wall object", fr.evaluate("__office.wallPoint('tv')") is None)
    page.context.close()

    # ───────────────────────── 2. on, ok ─────────────────────────
    print(f"[{engine}] on: the TV, the button, the faces")
    page = office_page(browser, base, level="ok")
    fr = open_lobby(page)
    check("the `ops` object is on the wall: the TV has a quad and a button", fr.evaluate("__office.ops().active()") and fr.evaluate("__office.opsTv()") is not None
          and fr.evaluate("document.getElementById('opsBtn').hidden") is False and fr.evaluate("document.getElementById('opsBtn').textContent") == "維運")
    st = fr.evaluate("__office.objects().objects.map(o => [o.id, o.onWall])")
    check("the TV takes no floor slot (the object is not placed; the cabinet keeps its own)", dict(st).get("ops") is False and dict(st).get("personnel") is True, st)
    check("no red dot on the button while all is well", fr.evaluate("document.getElementById('opsBtn').dataset.alert") is None)
    pt, tip, cursor = click_tv(page, fr)
    check("the TV is a hit target: pointer cursor and the tooltip 維運戰情", tip == "維運戰情" and cursor, (tip, cursor))
    check("a click on it opens the detail window (not the whiteboard's sheet)", fr.evaluate("__office.ops().isOpen()") and fr.evaluate("__office.view()") is None and win(fr) is not None)
    page.keyboard.press("Escape")
    page.wait_for_timeout(300)
    check("Esc closes it", fr.evaluate("__office.ops().isOpen()") is False and win(fr) is None)
    fr.evaluate("document.getElementById('opsBtn').click()")
    page.wait_for_timeout(400)
    check("the 維運 button opens it too", fr.evaluate("__office.ops().isOpen()"))
    fr.evaluate("document.getElementById('opsBtn').click()")
    page.wait_for_timeout(300)
    check("…and a second press closes it", fr.evaluate("__office.ops().isOpen()") is False)

    pix = {}
    for lv, reason in (("ok", ""), ("warn", "CPU 偏高 6 分鐘"), ("alert", "記憶體壓力高 12 分鐘"), ("muted", ""), ("none", "")):
        set_objects(page, [BOOKSHELF, PERSONNEL, decl(lv, reason)], lv)
        pix[lv] = fr.evaluate(TV_PIX)
    print("     pixels:", {k: (v["red"], v["yel"], v["grn"], v["sat"]) for k, v in pix.items()})
    check("ok: calm and green (the dot and the rings), no red banner", pix["ok"]["grn"] > 20 and pix["ok"]["red"] < 40 and pix["ok"]["yel"] < 40, pix["ok"])
    check("warn: a yellow banner", pix["warn"]["yel"] > 300 and pix["warn"]["yel"] > 4 * pix["ok"]["yel"] and pix["warn"]["red"] < 150, pix["warn"])
    check("alert: a red banner (many red pixels, far more than ok)", pix["alert"]["red"] > 400 and pix["alert"]["red"] > 6 * max(1, pix["ok"]["red"]), pix["alert"])
    check("muted: grey, nothing red, yellow or green on the face", pix["muted"]["red"] < 20 and pix["muted"]["grn"] < 25 and pix["muted"]["sat"] < pix["ok"]["sat"], pix["muted"])
    check("none: grey, almost nothing coloured", pix["none"]["red"] < 20 and pix["none"]["grn"] < 5 and pix["none"]["sat"] < 0.3 * pix["ok"]["sat"] + 30, pix["none"])
    set_objects(page, [BOOKSHELF, PERSONNEL, decl("alert", "記憶體壓力高 12 分鐘")], "alert")
    check("alert: the 維運 button carries a red dot", fr.evaluate("document.getElementById('opsBtn').dataset.alert") == "1"
          and fr.evaluate("getComputedStyle(document.getElementById('opsBtn'), '::after').width") == "8px")
    for lv in ("muted", "warn", "ok", "none"):
        set_objects(page, [BOOKSHELF, PERSONNEL, decl(lv, "x" if lv == "warn" else "")], lv)
        if fr.evaluate("document.getElementById('opsBtn').dataset.alert") is not None:
            check(f"{lv}: no dot on the button", False)
            break
    else:
        check("muted, warn, ok and none: no dot on the button", True)

    # the switch turned off: the object leaves the list
    page.evaluate("window.__fake.objects = [%s, %s]" % (json.dumps(BOOKSHELF), json.dumps(PERSONNEL)))
    page.evaluate("async () => { SFOffice.host.objectsChanged(); await new Promise(r => setTimeout(r, 300)); await SFOffice.refresh(); }")
    page.wait_for_timeout(400)
    check("the switch turned off (the object leaves the list): the TV and the button go away", fr.evaluate("__office.opsTv()") is None and fr.evaluate("document.getElementById('opsBtn').hidden") is True)
    check("no page errors so far", not page.errors, page.errors)
    page.context.close()

    # ───────────────────────── 2b. the TV up close ─────────────────────────
    if shoot:
        print(f"[{engine}] the TV up close (screenshots)")
        page = office_page(browser, base, level="ok")
        fr = open_lobby(page)
        for mode in ("iso", "flat"):
            fr.evaluate(f"__office.setMode('{mode}', true); __office.setArea('lobby', true)")
            page.wait_for_timeout(600)
            set_objects(page, [BOOKSHELF, PERSONNEL, decl("ok")], "ok")
            pts = fr.evaluate("__office.opsTv()")
            page.mouse.move(sum(p["x"] for p in pts) / 4, sum(p["y"] for p in pts) / 4)
            for _ in range(14):
                page.mouse.wheel(0, -120)
                page.wait_for_timeout(50)
            page.wait_for_timeout(600)
            for lv, reason in (("ok", ""), ("alert", "記憶體壓力高 12 分鐘")):
                set_objects(page, [BOOKSHELF, PERSONNEL, decl(lv, reason)], lv)
                pts = fr.evaluate("__office.opsTv()")
                xs, ys = [p["x"] for p in pts], [p["y"] for p in pts]
                page.mouse.move(5, 400)
                page.wait_for_timeout(200)
                page.screenshot(path=str(SHOTS / f"office-ops-tv-{lv}-{mode}-zoom.png"),
                                clip={"x": max(0, min(xs) - 70), "y": max(0, min(ys) - 70), "width": max(xs) - min(xs) + 140, "height": max(ys) - min(ys) + 140})
        page.context.close()

    # ───────────────────────── 2c. temperatures on the TV ─────────────────────────
    print(f"[{engine}] temperatures on the TV's face")
    page = office_page(browser, base, level="ok")
    fr = open_lobby(page)
    set_objects(page, [BOOKSHELF, PERSONNEL, decl_t("ok", 46.4, 43.0)], "ok")
    st = fr.evaluate(CAPTURE_STYLE)
    texts = [a for a, _ in st]
    check("the face has the line: CPU 46° · GPU 43°", ["CPU ", "46°", " · ", "GPU ", "43°"] == list(dict.fromkeys(t_ for t_ in texts if t_ in ("CPU ", "46°", " · ", "GPU ", "43°"))), texts)
    check("…the numbers are bright, not the warning colour", all(c.lower() != HOT for a, c in st if a in ("46°", "43°")), st)
    set_objects(page, [BOOKSHELF, PERSONNEL, decl_t("ok", 93.0, 86.0)], "ok")
    st = fr.evaluate(CAPTURE_STYLE)
    check("CPU 93° (over 90) and GPU 86° (over 85) are in the warning colour", {a: c.lower() for a, c in st if a in ("93°", "86°")} == {"93°": HOT, "86°": HOT}, st)
    set_objects(page, [BOOKSHELF, PERSONNEL, decl_t("ok", 90.0, 84.0)], "ok")
    st = fr.evaluate(CAPTURE_STYLE)
    check("exactly on the line counts (CPU 90°); under it (GPU 84°) does not", {a: c.lower() == HOT for a, c in st if a in ("90°", "84°")} == {"90°": True, "84°": False}, st)
    set_objects(page, [BOOKSHELF, PERSONNEL, decl_t("ok", 46.0, None)], "ok")
    texts = [a for a, _ in fr.evaluate(CAPTURE_STYLE)]
    check("only a CPU reading: no GPU, no dot between", "CPU " in texts and "46°" in texts and "GPU " not in texts and " · " not in texts, texts)
    set_objects(page, [BOOKSHELF, PERSONNEL, decl_t("ok", None, 43.0)], "ok")
    texts = [a for a, _ in fr.evaluate(CAPTURE_STYLE)]
    check("only a GPU reading: just the GPU", "GPU " in texts and "43°" in texts and "CPU " not in texts, texts)
    set_objects(page, [BOOKSHELF, PERSONNEL, decl_t("ok", None, None)], "ok")
    texts = [a for a, _ in fr.evaluate(CAPTURE_STYLE)]
    check("neither (macOS, or an older version): the line is not drawn at all", not any("°" in t_ or t_ in ("CPU ", "GPU ") for t_ in texts), texts)
    set_objects(page, [BOOKSHELF, PERSONNEL, decl("ok")], "ok")
    texts = [a for a, _ in fr.evaluate(CAPTURE_STYLE)]
    check("a face with no temperature keys at all (an older computer's) draws no line either", not any("°" in t_ for t_ in texts), texts)
    set_objects(page, [BOOKSHELF, PERSONNEL, decl_t("alert", 93.0, 70.0, reason="CPU 溫度 90°C 以上 6 分鐘（現在 93°C）")], "alert")
    st = fr.evaluate(CAPTURE_STYLE)
    check("under a red banner the line is still there, the hot one red", {a: c.lower() == HOT for a, c in st if a in ("93°", "70°")} == {"93°": True, "70°": False}, st)
    set_objects(page, [BOOKSHELF, PERSONNEL, decl_t("muted", 46.0, 43.0)], "muted")
    check("muted: the line is there too", "46°" in [a for a, _ in fr.evaluate(CAPTURE_STYLE)])
    fr.evaluate("__office.setMode('flat', true); __office.setArea('lobby', true)")
    page.wait_for_timeout(500)
    set_objects(page, [BOOKSHELF, PERSONNEL, decl_t("ok", 46.0, 43.0)], "ok")
    st = fr.evaluate(CAPTURE_STYLE)
    check("the flat view (a low, wide screen): the line is in the header", ["CPU ", "46°", " · ", "GPU ", "43°"] == list(dict.fromkeys(a for a, _ in st if a in ("CPU ", "46°", " · ", "GPU ", "43°"))), st)
    fr.evaluate("__office.setMode('iso', true)")
    check("no page errors", not page.errors, page.errors)
    page.context.close()
    if shoot:
        page = office_page(browser, base, level="ok")
        fr = open_lobby(page)
        fr.evaluate("__office.setMode('iso', true); __office.setArea('lobby', true)")
        page.wait_for_timeout(600)
        set_objects(page, [BOOKSHELF, PERSONNEL, decl_t("ok", 46.0, 43.0)], "ok")
        pts = fr.evaluate("__office.opsTv()")
        page.mouse.move(sum(p["x"] for p in pts) / 4, sum(p["y"] for p in pts) / 4)
        for _ in range(14):
            page.mouse.wheel(0, -120)
            page.wait_for_timeout(50)
        page.wait_for_timeout(600)
        set_objects(page, [BOOKSHELF, PERSONNEL, decl_t("ok", 93.0, 43.0)], "ok")
        pts = fr.evaluate("__office.opsTv()")
        xs, ys = [p["x"] for p in pts], [p["y"] for p in pts]
        page.mouse.move(5, 400)
        page.wait_for_timeout(300)
        page.screenshot(path=str(SHOTS / "office-ops-temp-tv.png"),
                        clip={"x": max(0, min(xs) - 70), "y": max(0, min(ys) - 70), "width": max(xs) - min(xs) + 140, "height": max(ys) - min(ys) + 140})
        page.context.close()

    # ───────────────────────── 3. motion ─────────────────────────
    print(f"[{engine}] alert: the glow pulses, 'reduce motion' holds it still, the idle rate stays 5 fps")
    rng = {}
    for reduced in (False, True):
        page = office_page(browser, base, level="alert", reason="記憶體壓力高 12 分鐘", reduced=reduced)
        fr = open_lobby(page)
        set_objects(page, [BOOKSHELF, PERSONNEL, decl("alert", "記憶體壓力高 12 分鐘")], "alert")
        avgs = []
        for _ in range(10):
            p = fr.evaluate(TV_PIX)
            avgs.append(p["avg"][0] - p["avg"][2])
            page.wait_for_timeout(130)
        rng[reduced] = max(avgs) - min(avgs)
        if reduced:
            check("reduce motion: the banner's red does not move", rng[True] < 0.6, rng)
        else:
            fr.evaluate("__office.forceIdle()")
            page.wait_for_timeout(300)
            f0 = fr.evaluate("__office.frames()")
            page.wait_for_timeout(2000)
            f1 = fr.evaluate("__office.frames()")
            check("idle with a pulsing alert up: the office still draws at about 5 fps (not 20)", fr.evaluate("__office.isActive()") is False and 5 <= f1 - f0 <= 14, (f1 - f0, fr.evaluate("__office.isActive()")))
        page.context.close()
    check("normal motion: the banner's red pulses", rng[False] > rng[True] + 0.6 and rng[False] > 1.0, rng)

    # ───────────────────────── 4. the window ─────────────────────────
    print(f"[{engine}] the detail window")
    page = office_page(browser, base, level="alert", reason="記憶體壓力高 12 分鐘")
    fr = open_lobby(page)
    fr.evaluate("__office.ops().timing.hb = 350; __office.ops().timing.minRefresh = 200")
    set_objects(page, [BOOKSHELF, PERSONNEL, decl("alert", "記憶體壓力高 12 分鐘")], "alert")
    if shoot:
        page.screenshot(path=str(SHOTS / "office-ops-alert-1280.png"))
    click_tv(page, fr)
    fr.wait_for_selector(".op-sec", timeout=8000)
    page.wait_for_timeout(400)
    w = win(fr)
    F = fake(page)
    check("opening says `live` on with just (id, true): no language", F["live"] and F["live"][0] == ["ops", True] and all(c == ["ops", True] for c in F["live"]), F["live"])
    check("it asks office_object_view(id, lang) in the page's language", F["views"] and F["views"][0] == ["ops", "zh-TW"] or F["views"][0] == ["ops", "zh"], F["views"][:1])
    check("six sections: CPU, memory, network, top 5, 24 hours, rules", [s["id"] for s in w["secs"]] == ["cpu", "mem", "net", "top", "day", "rules"], [s["id"] for s in w["secs"]])
    check("all eight kinds of widget are drawn (gauge bars stack spark strip table stat rules)", all(n >= 1 for n in w["kinds"]) and w["kinds"][3] == 2 and w["kinds"][4] == 2 and w["kinds"][6] == 2, w["kinds"])
    check("the banner is red and says why, with the two buttons", "b-alert" in w["banner"] and "記憶體壓力高 12 分鐘" in w["text"] and "靜音 1 小時" in w["text"] and "調整規則" in w["text"], w["text"][:200])
    check("the banner names the rule and when it began", "規則：記憶體壓力「高」連續" in w["text"] and "起" in w["text"], w["text"][:300])
    check("the table's rows, the stack's legend and the gauge's text are there", "示範瀏覽器" in w["text"] and "6.0 GB".replace("6.0", "5.6") in w["text"] and "24%" in w["text"], w["text"][:600])
    check("the strip's cells: 60 for 30 minutes, 288 for the day", fr.evaluate("[...document.querySelectorAll('.op-strip')].map(s => s.children.length)") == [60, 288])
    check("the null in a spark breaks the line (two path segments)", fr.evaluate("document.querySelector('.op-spark .ln').getAttribute('d').split('M').length - 1") == 2)
    check("the window follows the shell: centred, inside the frame", abs((w["box"]["l"] + w["box"]["r"]) / 2 - 640) < 4 and w["box"]["t"] > 0 and w["box"]["b"] < 800, w["box"])
    if shoot:
        page.screenshot(path=str(SHOTS / "office-ops-detail-1280.png"))
    n0 = len(fake(page)["views"])
    page.wait_for_timeout(1500)
    n1 = len(fake(page)["views"])
    check("it re-reads on its own (refresh_ms 600 → several reads in 1.5 s)", n1 - n0 >= 2, (n0, n1))
    hb0 = fake(page)["live"]
    page.wait_for_timeout(1200)
    hb1 = fake(page)["live"]
    check("a heartbeat: `live` true again on its timer (not a flood)", 2 <= len(hb1) - len(hb0) + 1 and len(hb1) < 12 and all(c == ["ops", True] for c in hb1), (len(hb0), len(hb1)))

    # patching: a changed table row shows up; a field in focus is not rebuilt
    def bump(v):
        v["sections"][3]["widgets"][0]["rows"][0][0] = "示範瀏覽器（新）"
        v["sections"][5]["widgets"][0]["items"][0]["value"] = 11
    page.evaluate("(v) => { window.__fake.view.zh = v; }", view("alert", "zh", extra=bump))
    fr.focus(".op-num")
    fr.evaluate("(() => { const i = document.querySelector('.op-num'); i.focus(); i.value = '7'; })()")
    page.wait_for_timeout(1400)
    check("a refresh patches the section that changed", "示範瀏覽器（新）" in win(fr)["text"])
    check("a number being typed is not lost (the section that holds the focus is left alone)", fr.evaluate("document.querySelector('.op-num').value") == "7"
          and fr.evaluate("document.activeElement.classList.contains('op-num')"))
    fr.evaluate("document.activeElement.blur()")
    page.evaluate("(v) => { window.__fake.view.zh = v; }", view("alert", "zh"))   # back to the plain data (the value on the server is 10)

    # rules
    print(f"[{engine}] the rules and the buttons")
    page.wait_for_timeout(900)

    def serve(v):
        page.evaluate("(v) => { window.__fake.view.zh = v; }", v)

    def rules_view(mem=10, mem_on=True, cpu_on=False):               # what the backend would answer once a rule is changed
        def f(v):
            it = v["sections"][5]["widgets"][0]["items"]
            it[0]["value"], it[0]["on"], it[1]["on"] = mem, mem_on, cpu_on
        return view("alert", "zh", extra=f)

    oc0 = fake(page)["objCalls"]
    n_acts = len(fake(page)["acts"])
    fr.evaluate("(() => { const i = document.querySelector('.op-num'); i.focus(); i.value = '15'; i.dispatchEvent(new Event('change', {bubbles: true})); })()")
    page.wait_for_timeout(400)
    a = fake(page)["acts"][n_acts:]
    check("a number sends set_rule {id, on, value} through office_object_action(id, '', 'set_rule', json, lang)",
          a and a[-1][:3] == ["ops", "", "set_rule"] and json.loads(a[-1][3]) == {"id": "mem_pressure_high", "on": True, "value": 15} and a[-1][4] in ("zh", "zh-TW"), a)
    serve(rules_view(15))
    page.wait_for_timeout(900)
    check("…and the field keeps the focus and shows what the backend says", fr.evaluate("document.querySelector('.op-num').value") == "15" and fr.evaluate("document.activeElement.classList.contains('op-num')"))
    fr.evaluate("(() => { const i = document.querySelector('.op-num'); i.focus(); i.value = '999'; i.dispatchEvent(new Event('change', {bubbles: true})); })()")
    page.wait_for_timeout(300)
    a = fake(page)["acts"]
    check("a value over the field's max is clamped to it (120)", json.loads(a[-1][3])["value"] == 120, a[-1])
    serve(rules_view(120))
    page.wait_for_timeout(800)
    fr.evaluate("document.querySelectorAll('.op-sw')[0].click()")
    page.wait_for_timeout(300)
    a = fake(page)["acts"]
    check("the switch sends set_rule with on: false and the number it has", json.loads(a[-1][3]) == {"id": "mem_pressure_high", "on": False, "value": 120}, a[-1])
    serve(rules_view(120, False))
    page.wait_for_timeout(800)
    fr.evaluate("document.querySelectorAll('.op-sw')[1].click()")
    page.wait_for_timeout(300)
    check("a rule that was off: on: true", json.loads(fake(page)["acts"][-1][3]) == {"id": "cpu_high", "on": True, "value": 5}, fake(page)["acts"][-1])
    check("every change makes the office read the objects again (the TV follows at once)", fake(page)["objCalls"] > oc0, (oc0, fake(page)["objCalls"]))
    fr.click('[data-op="mute"]')
    page.wait_for_timeout(300)
    a = fake(page)["acts"][-1]
    check("靜音 1 小時 sends mute {minutes: 60}", a[2] == "mute" and json.loads(a[3]) == {"minutes": 60}, a)
    serve(rules_view(15))                                         # the person had changed things; the defaults are 10, on, off
    page.wait_for_timeout(800)
    serve(rules_view(10))
    fr.click('[data-op="reset"]')
    page.wait_for_timeout(900)
    check("the defaults button sends reset_rules", fake(page)["acts"][-1][2] == "reset_rules", fake(page)["acts"][-1])
    check("…and the rules section shows the defaults at once, though the focus is in it",
          fr.evaluate("document.querySelector('.op-num').value") == "10" and fr.evaluate("document.querySelectorAll('.op-sw')[0].getAttribute('aria-checked')") == "true"
          and fr.evaluate("document.querySelectorAll('.op-sw')[1].getAttribute('aria-checked')") == "false")
    # a refused change springs back
    page.evaluate("window.__fake.actRes = {ok: false, message: '這個沒成功'}")
    fr.evaluate("document.querySelectorAll('.op-sw')[0].click()")
    page.wait_for_timeout(300)
    check("a refused change is said in the footer (not silently)", "這個沒成功" in fr.evaluate("document.getElementById('opMsg').textContent") and fr.evaluate("document.getElementById('opMsg').classList.contains('bad')"))
    page.wait_for_timeout(900)
    check("…and the switch springs back to what the backend still has (on)", fr.evaluate("document.querySelectorAll('.op-sw')[0].getAttribute('aria-checked')") == "true"
          and fr.evaluate("document.querySelectorAll('.op-sw')[0].classList.contains('on')"))
    page.evaluate("window.__fake.actRes = {ok: true, message: 'ok'}")
    # muted: the backend's banner keeps the level it is under (alert); the object's level says muted
    set_objects(page, [BOOKSHELF, PERSONNEL, decl("muted", "記憶體壓力高 12 分鐘")], "muted")
    page.wait_for_timeout(900)
    check("muted (object level muted, banner still alert): the banner goes quiet and offers 取消靜音, not 靜音",
          fr.evaluate("document.querySelector('.op-banner').className.includes('b-muted')") and fr.query_selector('[data-op="unmute"]') is not None and fr.query_selector('[data-op="mute"]') is None)
    fr.click('[data-op="unmute"]')
    page.wait_for_timeout(300)
    check("取消靜音 sends unmute", fake(page)["acts"][-1][2] == "unmute")
    set_objects(page, [BOOKSHELF, PERSONNEL, decl("alert", "記憶體壓力高 12 分鐘")], "alert")
    serve(view("alert", "zh", extra=lambda v: v.update(banner=None)))
    page.wait_for_timeout(1000)
    check("no banner from the backend: the buttons are still there (靜音, 調整規則)", fr.query_selector('[data-op="mute"]') is not None and fr.query_selector('[data-op="rules"]') is not None)
    serve(view("alert", "zh"))
    page.wait_for_timeout(800)
    fr.click('[data-op="rules"]')
    page.wait_for_timeout(500)
    check("調整規則 goes to the rules and puts the focus on one", fr.evaluate("!!document.activeElement && document.activeElement.closest('.op-sec') && document.activeElement.closest('.op-sec').dataset.sec") == "rules")

    # the top five's order switch
    print(f"[{engine}] the top five's order switch")
    page.evaluate("(v) => { window.__fake.view.zh = v; }", view("alert", "zh"))
    page.wait_for_timeout(900)
    sw = fr.evaluate("""() => { const b = [...document.querySelectorAll('.op-w-table [data-op="switch"]')];
      return { labels: b.map(x => x.textContent), on: b.map(x => x.classList.contains('on')), pressed: b.map(x => x.getAttribute('aria-pressed')), note: (document.querySelector('.op-snote') || {}).textContent || '',
               tag: b.map(x => x.tagName), data: b.map(x => [x.dataset.act, x.dataset.val]) }; }""")
    check("the table's header draws a switch: 自動 / CPU / 記憶體 as buttons, auto is the one on", sw["labels"] == ["自動", "CPU", "記憶體"] and sw["on"] == [True, False, False]
          and sw["pressed"] == ["true", "false", "false"] and set(sw["tag"]) == {"BUTTON"}, sw)
    check("the small note beside it says why", sw["note"] == "現在照記憶體排（記憶體已用 81%）", sw["note"])
    n_acts = len(fake(page)["acts"])
    fr.focus('.op-w-table [data-op="switch"][data-val="mem"]')       # by keyboard: Safari does not focus a button on a mouse click
    page.keyboard.press("Enter")
    page.wait_for_timeout(300)
    a = fake(page)["acts"][n_acts:]
    check("a press sends top_sort {by: 'mem'} through office_object_action(id, '', 'top_sort', json, lang)",
          len(a) == 1 and a[0][:3] == ["ops", "", "top_sort"] and json.loads(a[0][3]) == {"by": "mem"} and a[0][4] in ("zh", "zh-TW"), a)
    nv = len(fake(page)["views"])
    serve(view("alert", "zh", sort="mem"))
    page.wait_for_timeout(1200)
    check("…then it reads the view again and the pressed button follows the backend (記憶體 on, note gone is the backend's call)",
          len(fake(page)["views"]) > nv and fr.evaluate("[...document.querySelectorAll('.op-w-table [data-op=\"switch\"]')].map(x => x.classList.contains('on'))") == [False, False, True])
    check("…and the keyboard focus stays on the button used", fr.evaluate("!!document.activeElement && document.activeElement.dataset.val") == "mem")
    fr.click('.op-w-table [data-op="switch"][data-val="cpu"]')
    page.wait_for_timeout(300)
    check("another button sends its own value", json.loads(fake(page)["acts"][-1][3]) == {"by": "cpu"}, fake(page)["acts"][-1])
    if shoot:
        fr.evaluate("document.querySelector('.op-sec[data-sec=\"top\"]').scrollIntoView({block: 'center'})")
        page.wait_for_timeout(300)
        page.locator("#sfo-overlay").screenshot(path=str(SHOTS / "office-ops-top-switch-1280.png"))
    serve(view("alert", "zh"))
    page.wait_for_timeout(900)
    # a table with no switch (an older backend) draws no buttons
    def noswitch(v):
        v["sections"][3]["widgets"][0].pop("switch")
    serve(view("alert", "zh", extra=noswitch))
    page.wait_for_timeout(1100)
    check("a table without a `switch` draws none (and keeps its rows)", fr.evaluate("document.querySelectorAll('.op-w-table [data-op=\"switch\"]').length") == 0
          and "示範瀏覽器" in win(fr)["text"] and not page.errors, page.errors)
    def badswitch(v):
        v["sections"][3]["widgets"][0]["switch"] = {"action": "top_sort", "options": [{"id": "only", "label": "x"}]}
    serve(view("alert", "zh", extra=badswitch))
    page.wait_for_timeout(1100)
    check("a switch with fewer than two options draws none and does not break the table", fr.evaluate("document.querySelectorAll('.op-w-table [data-op=\"switch\"]').length") == 0
          and "示範瀏覽器" in win(fr)["text"] and not page.errors, page.errors)
    serve(view("alert", "zh"))
    page.wait_for_timeout(900)

    # robustness: a kind it does not know, widgets with missing fields
    print(f"[{engine}] an unknown kind and missing fields")
    def junk(v):
        v["sections"][0]["widgets"] += [{"kind": "hologram", "label": "x"}, {"kind": "stack"}, {"kind": "spark"}, {"kind": "strip", "cells": [None, "??", "ok"]}, {"kind": "gauge"}, {"kind": "table"}, {"kind": "rules"}, None, 5, {"kind": "bars", "items": [None, {"value": "x"}]}, {"kind": "stat"}]
        v["sections"].append({"id": "weird", "title": "只有不認得的元件", "widgets": [{"kind": "hologram"}]})
        v["sections"].append("not a section")
    page.evaluate("(v) => { window.__fake.view.zh = v; }", view("alert", "zh", extra=junk))
    page.wait_for_timeout(1200)
    w = win(fr)
    check("an unknown kind is left out, missing fields are blanks: still six sections, the weird one (only unknown widgets) has no card",
          [s["id"] for s in w["secs"]] == ["cpu", "mem", "net", "top", "day", "rules"] and "hologram" not in w["text"], [s["id"] for s in w["secs"]])
    check("…and no page error", not page.errors, page.errors)
    page.evaluate("(v) => { window.__fake.view.zh = v; }", view("alert", "zh"))

    # failure and the 'off' answer
    page.evaluate("window.__fake.view.zh = {success: false, code: 'off', message: '維運戰情沒有開'}")
    fr.evaluate("__office.ops().close()")
    fr.evaluate("__office.ops().open()")
    page.wait_for_timeout(700)
    check("`off` from the backend: its message, a retry button, no sections", "維運戰情沒有開" in win(fr)["text"] and fr.query_selector('[data-op="retry"]') is not None and not win(fr)["secs"])
    fr.evaluate("__office.ops().close()")
    page.evaluate("(v) => { window.__fake.view.zh = v; }", view("alert", "zh"))

    # live: closing in every way
    print(f"[{engine}] live on / off")
    def last_live():
        return fake(page)["live"][-1]
    for how in ("esc", "x", "outside", "toggle", "other-object"):
        fr.evaluate("__office.ops().close()")
        page.wait_for_timeout(150)
        before = len(fake(page)["live"])
        fr.evaluate("__office.ops().open()")
        page.wait_for_timeout(350)
        check(f"[{how}] opening sends live on", fake(page)["live"][before] == ["ops", True], fake(page)["live"][before:])
        if how == "esc":
            page.keyboard.press("Escape")
        elif how == "x":
            fr.click("#opClose")
        elif how == "outside":
            page.mouse.click(8, 400)
        elif how == "toggle":
            fr.evaluate("document.getElementById('opsBtn').click()")
        else:
            fr.evaluate("__office.objOpen('bookshelf')")
        page.wait_for_timeout(350)
        n = len(fake(page)["live"])
        check(f"[{how}] closing sends live off, and the heartbeat stops", last_live() == ["ops", False] and fr.evaluate("__office.ops().isOpen()") is False, fake(page)["live"][before:])
        page.wait_for_timeout(900)
        check(f"[{how}] …no more calls after it", len(fake(page)["live"]) == n, (n, len(fake(page)["live"])))
        fr.evaluate("__office.objClose()")
    # the whole office closes with the window open: the iframe goes without passing through the window's own close
    fr.evaluate("__office.ops().open()")
    page.wait_for_timeout(300)
    n_open = len(fake(page)["live"])
    page.evaluate("SFOffice.close()")
    page.wait_for_timeout(500)
    check("closing the whole office with the window open still says `live` off", len(fake(page)["live"]) > n_open and fake(page)["live"][-1] == ["ops", False], fake(page)["live"][n_open - 1:])
    fr = open_lobby(page)
    fr.evaluate("__office.ops().timing.hb = 350; __office.ops().timing.minRefresh = 200")
    fr.evaluate("__office.ops().open()")
    page.wait_for_timeout(300)
    fr.evaluate("__office.objOpen('bookshelf')")
    page.wait_for_timeout(300)
    check("another object's popup takes over: the ops window is closed under it", fr.evaluate("__office.ops().isOpen()") is False and fr.evaluate("document.getElementById('opspop').hidden") is True)
    fr.evaluate("__office.objClose()")
    check("no page errors in the window", not page.errors, page.errors)
    page.context.close()

    # ───────────────────────── 4b. the temperature section ─────────────────────────
    print(f"[{engine}] the temperature section")
    for variant, kinds in (("both", ["stat", "stat", "gauge", "spark"]), ("cpu", ["stat", "spark"]), ("none", ["stat"])):
        for lang in ("zh", "en"):
            page = office_page(browser, base, lang=lang, level="ok")
            page.evaluate("(v) => { window.__fake.view = v; }", {"zh": view("ok", "zh", extra=with_temp(variant, "zh")), "en": view("ok", "en", extra=with_temp(variant, "en"))})
            fr = open_lobby(page)
            set_objects(page, [BOOKSHELF, PERSONNEL, decl("ok", lang=lang)], "ok")
            click_tv(page, fr)
            fr.wait_for_selector(".op-sec", timeout=8000)
            page.wait_for_timeout(400)
            w = win(fr)
            ids = [x["id"] for x in w["secs"]]
            sec = fr.evaluate("""() => { const e = document.querySelector('.op-sec[data-sec="temp"]'); return e ? { title: e.querySelector('h3').textContent, text: e.innerText,
                kinds: [...e.querySelectorAll('.op-w')].map(x => x.className.replace('op-w op-w-', '')) } : null; }""")
            check(f"{variant}/{lang}: the section sits after network and disk (before the top five)", ids == ["cpu", "mem", "net", "temp", "top", "day", "rules"], ids)
            check(f"{variant}/{lang}: its widgets are {'+'.join(kinds)}", sec and sec["kinds"] == kinds, sec)
            if variant == "both":
                want = ["溫度", "CPU 溫度", "46.0°C", "acpitz", "GPU · NVIDIA GB10", "43°C", "共用記憶體", "功耗 11.0 W", "GPU 使用率", "12%", "最近 30 分鐘"] if lang == "zh" else \
                       ["Temperature", "CPU temperature", "46.0°C", "acpitz", "GPU · NVIDIA GB10", "43°C", "Shared memory", "Power 11.0 W", "GPU utilization", "12%", "Last 30 minutes"]
                check(f"{variant}/{lang}: CPU temperature and its sensor, the GPU card, utilization, the 30 minutes", sec and all(x in (sec["title"] + sec["text"]) for x in want), sec)
                check(f"{variant}/{lang}: the spark has a CPU and a GPU line with the last values", fr.evaluate("""() => [...document.querySelectorAll('.op-sec[data-sec="temp"] .op-spark .ln')].length""") == 2
                      and "46.0°C" in sec["text"] and "43°C" in sec["text"] and "46 °C" in sec["text"] and "43 °C" in sec["text"])
            if variant == "none":
                check(f"{variant}/{lang}: one line that says why, not an empty section", sec and ("macOS 需要管理員權限" if lang == "zh" else "macOS needs administrator rights") in sec["text"] and "—" in sec["text"], sec)
            if lang == "en":
                check(f"{variant}/en: no Chinese in the section", sec and not CJK.search(sec["title"] + sec["text"]), sec)
            if shoot and variant == "both" and lang == "zh":
                fr.evaluate("document.querySelector('.op-sec[data-sec=\"temp\"]').scrollIntoView({block: 'center'})")
                page.wait_for_timeout(300)
                page.screenshot(path=str(SHOTS / "office-ops-temp-detail.png"))
            check(f"{variant}/{lang}: no page errors", not page.errors, page.errors)
            page.context.close()

    # ───────────────────────── 5. 900 wide ─────────────────────────
    print(f"[{engine}] 1280 and 900 wide")
    cols = {}
    for width in (1280, 900):
        page = office_page(browser, base, level="alert", reason="記憶體壓力高 12 分鐘", size=(width, 800))
        fr = open_lobby(page)
        set_objects(page, [BOOKSHELF, PERSONNEL, decl("alert", "記憶體壓力高 12 分鐘")], "alert")
        if shoot:
            page.screenshot(path=str(SHOTS / f"office-ops-alert-{width}.png"))
            set_objects(page, [BOOKSHELF, PERSONNEL, decl("ok")], "ok")
            page.screenshot(path=str(SHOTS / f"office-ops-normal-{width}.png"))
            set_objects(page, [BOOKSHELF, PERSONNEL, decl("alert", "記憶體壓力高 12 分鐘")], "alert")
        fr.evaluate("document.getElementById('opsBtn').click()")
        fr.wait_for_selector(".op-sec", timeout=8000)
        page.wait_for_timeout(600)
        w = win(fr)
        lefts = sorted({round(s["l"]) for s in w["secs"]})
        cols[width] = len(lefts)
        first_row = [s["id"] for s in w["secs"] if abs(s["t"] - w["secs"][0]["t"]) < 3]
        if width == 1280:
            check("1280: the CPU, memory and network cards share a row", first_row == ["cpu", "mem", "net"], first_row)
        else:
            check("900: two columns, the third card goes below", first_row == ["cpu", "mem"] and w["secs"][2]["t"] > w["secs"][0]["b"] - 2, (first_row, [(s["id"], round(s["t"])) for s in w["secs"]]))
        check(f"{width}: nothing spills sideways, the window fits", w["sw"] <= w["cw"] + 1 and w["box"]["r"] <= width + 1 and w["box"]["l"] >= -1, (w["sw"], w["cw"], w["box"]))
        over = fr.evaluate("""() => { const b = document.querySelector('.oo-box').getBoundingClientRect(); return [...document.querySelectorAll('.op-inner *')].filter(e => { const r = e.getBoundingClientRect(); return r.width > 0 && (r.right > b.right + 1 || r.left < b.left - 1); }).length; }""")
        check(f"{width}: no element sticks out of the window", over == 0, over)
        if shoot:
            page.screenshot(path=str(SHOTS / f"office-ops-detail-{width}.png"))
            fr.evaluate("document.getElementById('opBody').scrollTop = 99999")
            page.wait_for_timeout(300)
            page.screenshot(path=str(SHOTS / f"office-ops-rules-{width}.png"))
        page.context.close()
    check("1280 has three columns of cards, 900 has two", cols[1280] >= 3 and cols[900] == 2, cols)

    # ───────────────────────── 6. the sidebar's dot, the settings switch ─────────────────────────
    print(f"[{engine}] the sidebar's dot and the switch")
    page = office_page(browser, base, level="none", on=False, state={"on": False, "level": "none", "reason": "", "since": None})
    page.wait_for_selector("#btn-office", timeout=10000)
    page.wait_for_timeout(500)
    box0 = page.evaluate("(() => { const r = document.getElementById('btn-office').getBoundingClientRect(); return [r.left, r.width, r.top, r.height]; })()")
    check("ops_state() is asked once at load", fake(page)["stateCalls"] >= 1, fake(page)["stateCalls"])
    check("no dot at the start", page.evaluate("!document.querySelector('#btn-office .sfe-opsdot')"))
    dots = {}
    for lv in ("alert", "muted", "ok", "warn", "none", "alert"):
        page.evaluate("(lv) => window.__sfOpsLevel({level: lv, reason: '記憶體壓力高 12 分鐘', since: 1})", lv)
        page.wait_for_timeout(120)
        dots[lv] = page.evaluate("!!document.querySelector('#btn-office .sfe-opsdot')")
    check("window.__sfOpsLevel: a dot for alert only (muted, ok, warn, none: none), and it comes back", dots == {"alert": True, "muted": False, "ok": False, "warn": False, "none": False}, dots)
    page.evaluate("window.__sfOpsLevel({level: 'alert', reason: '記憶體壓力高 12 分鐘', since: 1})")
    page.wait_for_timeout(150)
    box1 = page.evaluate("(() => { const r = document.getElementById('btn-office').getBoundingClientRect(); return [r.left, r.width, r.top, r.height]; })()")
    check("the dot does not move or resize the button (the entry row is measured without it)", all(abs(a - b) < 0.6 for a, b in zip(box0, box1)), (box0, box1))
    tip = page.evaluate("document.querySelector('#btn-office .sfe-opsdot').getAttribute('aria-label')")
    check("the dot says what it is, with the reason", "維運異常" in tip and "記憶體壓力高 12 分鐘" in tip, tip)
    page.evaluate("SFEntry.paint()")
    check("it survives the entry row repainting", page.evaluate("!!document.querySelector('#btn-office .sfe-opsdot')"))
    if shoot:
        page.screenshot(path=str(SHOTS / "office-ops-sidebar-dot-1280.png"), clip={"x": 0, "y": 0, "width": 340, "height": 120})
    # the switch
    page.click("#btn-settings")
    page.wait_for_timeout(500)
    page.click('.settings-tab[data-tab="office"]')
    page.wait_for_selector("#setting-experimental-ops", timeout=5000)
    check("Settings → Office has the 維運戰情 (experimental) switch, off", "維運戰情" in page.evaluate("document.querySelector('#sfo-ops .sx-label').textContent") and "實驗" in page.evaluate("document.querySelector('#sfo-ops .sx-label').textContent")
          and page.evaluate("document.getElementById('setting-experimental-ops').classList.contains('on')") is False
          and page.evaluate("document.getElementById('setting-experimental-ops').getAttribute('data-guide')") == "office-ops")
    sc0 = fake(page)["stateCalls"]
    page.click("#setting-experimental-ops")
    page.wait_for_timeout(500)
    check("turning it on writes experimental_ops through save_settings and reads ops_state() again",
          page.evaluate("window.__cfg.settings.experimental_ops") is True and page.evaluate("window.__calls.includes('save_settings')") and fake(page)["stateCalls"] > sc0
          and page.evaluate("document.getElementById('setting-experimental-ops').getAttribute('aria-checked')") == "true")
    check("…without dropping the other settings", page.evaluate("window.__cfg.settings.experimental_office") is True)
    page.click("#setting-experimental-ops")
    page.wait_for_timeout(400)
    check("and off again", page.evaluate("window.__cfg.settings.experimental_ops") is False)
    if shoot:
        page.screenshot(path=str(SHOTS / "office-ops-settings-1280.png"))
    page.context.close()
    # the starting value from ops_state()
    page = office_page(browser, base, level="alert", reason="記憶體壓力高 12 分鐘", state={"on": True, "level": "alert", "reason": "記憶體壓力高 12 分鐘", "since": NOW - 700})
    page.wait_for_selector("#btn-office .sfe-opsdot", timeout=10000)
    check("a page loaded while an alert is up shows the dot at once (ops_state at load)", page.evaluate("!!document.querySelector('#btn-office .sfe-opsdot')"))
    page.context.close()

    # ───────────────────────── 7. English ─────────────────────────
    print(f"[{engine}] English")
    page = office_page(browser, base, lang="en", level="alert", reason="Memory pressure high for 12 minutes")
    fr = open_lobby(page)
    set_objects(page, [BOOKSHELF, PERSONNEL, decl("alert", "Memory pressure high for 12 minutes", "en")], "alert")
    btn = fr.evaluate("[document.getElementById('opsBtn').textContent, document.getElementById('opsBtn').title, document.getElementById('opsBtn').getAttribute('aria-label')]")
    check("the button, its tip and its label are in English", btn[0] == "Ops" and not CJK.search(" ".join(btn)), btn)
    words = fr.evaluate(CAPTURE)
    tv = [s for s in words if s.strip()]
    check("every string drawn on the TV is English (no Chinese), and the reason is on it", tv and not any(CJK.search(s) for s in tv) and any("Memory pressure" in s for s in tv), tv)
    pt, tip, _ = click_tv(page, fr)
    check("the tooltip is English", tip == "Ops board", tip)
    fr.wait_for_selector(".op-sec", timeout=8000)
    page.wait_for_timeout(400)
    w = win(fr)
    check("the window has no Chinese in it (title, banner, buttons, widgets, rules, footer)", not CJK.search(w["text"]), CJK.findall(w["text"])[:10])
    check("…and says its things in English", "Mute for 1 hour" in w["text"] and "Adjust rules" in w["text"] and "Back to the defaults" in w["text"] and "Live" in w["text"], w["text"][:300])
    sw = fr.evaluate("[...document.querySelectorAll('.op-w-table [data-op=\"switch\"]')].map(x => x.textContent)")
    check("the switch is in English: Auto / CPU / Memory, with the English note", sw == ["Auto", "CPU", "Memory"] and "Now ranked by memory (memory 81% used)" in w["text"]
          and fr.evaluate("document.querySelector('.op-seg').getAttribute('aria-label')") == "Order", sw)
    n_acts = len(fake(page)["acts"])
    fr.click('.op-w-table [data-op="switch"][data-val="mem"]')
    page.wait_for_timeout(300)
    a = fake(page)["acts"][n_acts:]
    check("English: the same args, language en", len(a) == 1 and a[0][2] == "top_sort" and json.loads(a[0][3]) == {"by": "mem"} and a[0][4] == "en", a)
    F = fake(page)
    check("it asked for the English view", F["views"] and F["views"][0][-1] == "en", F["views"][:1])
    page.keyboard.press("Escape")
    page.wait_for_timeout(200)
    # the zh strings the TV draws, for contrast
    page.context.close()
    page = office_page(browser, base, level="ok")
    fr = open_lobby(page)
    words = [s for s in fr.evaluate(CAPTURE) if s.strip()]
    check("zh-TW: the ok face says 維運 and 一切正常", "維運" in words and "一切正常" in words, words)
    page.context.close()


def main():
    srv = H.serve()
    base = f"http://127.0.0.1:{srv.server_port}"
    with sync_playwright() as pw:
        try:
            for engine in ("chromium", "webkit"):
                try:
                    browser = getattr(pw, engine).launch()
                except Exception as e:
                    if engine == "chromium":
                        raise
                    print(f"[{engine}] not installed, skipped: {str(e).splitlines()[0][:80]}")
                    continue
                try:
                    run(browser, base, engine)
                finally:
                    browser.close()
        finally:
            srv.shutdown()
    print(f"\nResults: {passed} passed, {failed} failed")
    sys.exit(1 if failed else 0)


main()
