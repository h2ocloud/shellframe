#!/usr/bin/env python3
"""Settings 「不分享任務給隔壁電腦」 (settings.link_share_tasks_off, default off = tasks are shared as before).

  1. Over the real signed link (the pattern of tests_link_peer_tasks): with the setting off the paired
     machine gets its lists; with it on, GET /link/tasks answers 403 and the store is not read
     (the serving side's tasks_link is not even asked), so the asking side's PeerTasks state is `denied`
     with no lists (the board says the other computer does not share them); turned off again it is `ok`.
     /link/bots is not touched by the setting. A missing or non-true value (false, "yes") shares.
  2. The page: the switch is in the 連結 (Connect) category with one line of help, shows the saved value,
     and saves settings.link_share_tasks_off through save_settings with the other settings kept.
Demo data only. Run: .venv/bin/python tests_link_share_tasks_off.py
"""
import json
import os
import pathlib
import shutil
import socket
import sys
import tempfile
import time
import uuid
from unittest.mock import MagicMock

HERE = pathlib.Path(__file__).resolve().parent
TMP = pathlib.Path(tempfile.mkdtemp(prefix="sf-sharetasks-"))
sys.modules.setdefault("webview", MagicMock())
sys.path.insert(0, str(HERE))
import sf_log  # noqa: E402
sf_log.DEBUG_LOG = str(TMP / "debug.log")
import api_link  # noqa: E402
import api_tasks  # noqa: E402
import frame_link  # noqa: E402
import state_store as ss  # noqa: E402

passed = failed = 0


def check(name, ok, detail=""):
    global passed, failed
    if ok:
        passed += 1
        print(f"  [PASS] {name}")
    else:
        failed += 1
        print(f"  [FAIL] {name}  {detail}")


def free_port():
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    port = s.getsockname()[1]
    s.close()
    return port


api_tasks._ = lambda en, zh: en


class App(api_tasks.TasksApiMixin):
    pass


class Node:
    def __init__(self, name):
        self.tmp = tempfile.mkdtemp(prefix=f"sf-sharetasks-{name}-", dir=TMP)
        self.port = free_port()
        self.asked = []
        self.bots_asked = []
        self.app = App()
        self.config = {"frame_link": {"enabled": True, "listen_host": "127.0.0.1", "listen_port": self.port,
                                      "frame_name": name, "frame_id": uuid.uuid4().hex, "peers": {}}}

        def execute(cmd, args):
            if cmd == "tasks_link":
                self.asked.append(dict(args or {}))
                return self.app._tasks_for_peer(args or {})
            if cmd == "bots_link":
                self.bots_asked.append(1)
                return {"ok": True, "success": True, "bots": []}
            return {"success": False, "message": f"unknown {cmd}"}

        def update_config(mutator):
            mutator(self.config)
            return self.config

        self.link = frame_link.FrameLink(get_config=lambda: self.config, update_config=update_config,
                                         execute_fn=execute, log=lambda m: None, version="test",
                                         state_dir=os.path.join(self.tmp, "state"), downloads_dir=os.path.join(self.tmp, "dl"))

    @property
    def fid(self):
        return self.config["frame_link"]["frame_id"]


class Page(api_link.LinkApiMixin):
    def __init__(self, link):
        self._l = link

    def _link(self):
        return self._l


def state_of(page, pid, want, tries=100):
    got = {}
    for _ in range(tries):
        got = json.loads(page.link_peer_tasks())["peers"][pid]
        if got["state"] == want:
            break
        time.sleep(0.05)
    return got


print("1. the link")
a, b = Node("desk-a"), Node("desk-b")
try:
    a.link.start()
    b.link.start()
    pair = a.link.pairing_begin()
    check("A and B pair", b.link.join("127.0.0.1", a.port, pair["code"]).get("success") is True)
    b.link.ping_peer(a.fid)
    ss.add("示範：整理週報", actor="示範助理", owner="示範助理")

    res = b.link.remote_tasks(a.fid)
    check("default (no setting): the lists come", res.get("ok") is True and len(res.get("open") or []) == 1, res)
    a.config["settings"] = {"link_share_tasks_off": False}
    check("the setting false: the lists come", b.link.remote_tasks(a.fid).get("ok") is True)
    for odd in ("yes", 1, "true"):
        a.config["settings"] = {"link_share_tasks_off": odd}
        check(f"a value that is not True ({odd!r}) shares", b.link.remote_tasks(a.fid).get("ok") is True)

    page = Page(b.link)
    a.config["settings"] = {"link_share_tasks_off": False}
    first = state_of(page, a.fid, "ok")
    check("the page sees the other computer's lists", first["state"] == "ok" and len(first["open"]) == 1, first)

    a.config["settings"] = {"link_share_tasks_off": True}
    before = len(a.asked)
    res = b.link.remote_tasks(a.fid)
    check("on: GET /link/tasks is refused with 403 and carries no lists",
          res.get("code") == 403 and res.get("success") is False and "decisions" not in res and "open" not in res, res)
    check("…the store was not read (the serving side was not asked)", len(a.asked) == before, (before, len(a.asked)))
    page._peer_tasks._kept[a.fid]["next_at"] = 0
    denied = state_of(page, a.fid, "denied")
    check("…the asking side's board state is denied, with no lists", denied["state"] == "denied" and denied["open"] == []
          and denied["decisions"] == [] and denied["stuck"] == [], denied)
    check("the setting does not touch /link/bots", b.link._signed_request(b.link.peers()[a.fid], "GET", "/link/bots").get("success") is True
          and len(a.bots_asked) == 1)

    a.config["settings"] = {"link_share_tasks_off": False}
    page._peer_tasks._kept[a.fid]["next_at"] = 0
    back = state_of(page, a.fid, "ok")
    check("off again: ok, with the lists", back["state"] == "ok" and len(back["open"]) == 1, back)
finally:
    a.link.stop()
    b.link.stop()

print("2. the page")
try:
    from playwright.sync_api import sync_playwright
except ImportError:
    print("SKIP  the page part (no playwright)")
    sync_playwright = None

if sync_playwright:
    import tests_settings_ui as S
    with sync_playwright() as pw:
        browser = pw.chromium.launch()
        try:
            page, errors = S.open_page(browser, "zh-TW", {"experimental_bots": True, "link_share_tasks_off": False, "usage_pace": False})
            S.open_settings(page, "connect")
            sel = "#settings-panel-connect #setting-link-share-off"
            page.wait_for_selector(sel, timeout=5000)
            label = page.inner_text("#settings-panel-connect .cg-share .sx-label")
            hint = page.inner_text("#settings-panel-connect .cg-share .sx-hint")
            check("the switch is in the 連結 category, with its label and one line of help",
                  page.inner_text('#settings-tabs-bar [data-tab="connect"]').startswith("連結") and label == "不分享任務給隔壁電腦" and len(hint) > 10, (label, hint))
            check("…visible, and off as saved", page.is_visible(sel) and not page.evaluate("s => document.querySelector(s).classList.contains('on')", sel))
            page.click(sel)
            page.wait_for_timeout(200)
            saved = page.evaluate("window.__cfg.settings")
            check("a click saves link_share_tasks_off true and keeps the other settings",
                  saved.get("link_share_tasks_off") is True and saved.get("usage_pace") is False and saved.get("experimental_bots") is True, saved)
            check("…and the switch is on", page.evaluate("s => document.querySelector(s).classList.contains('on')", sel))
            page.click(sel)
            page.wait_for_timeout(200)
            check("a second click turns it off again", page.evaluate("window.__cfg.settings.link_share_tasks_off") is False)
            check("no page errors", not errors, errors)
            page.close()
            page, errors = S.open_page(browser, "zh-TW", {"link_share_tasks_off": True})
            S.open_settings(page, "connect")
            page.wait_for_selector(sel, timeout=5000)
            check("a saved true shows as on", page.evaluate("s => document.querySelector(s).classList.contains('on')", sel))
            page.close()
        finally:
            browser.close()

shutil.rmtree(TMP, ignore_errors=True)
print(f"\nResults: {passed} passed, {failed} failed")
print("ALL PASS" if not failed else "FAILED")
sys.exit(1 if failed else 0)
