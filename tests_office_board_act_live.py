#!/usr/bin/env python3
"""「我做好了」/「留言」 on a task card, in the real page with the office embedded (not the standalone demo).

Asked for: when the person finishes something on the board they can mark it from the card, and the bot hears about it.
The standalone demo covers the card's rules (tests_office_whiteboard_ui.py); this covers the path through ShellBot:
  - the card calls SFOffice.host.board.act → pywebview task_user_act(id, action, text)
  - after a success the store is asked again at once (tasks_overview within ~2 s, not the 9 s pace), and the board
    shows the store's answer (the card for a task that left the board closes)
  - 「也告訴 X」 sends one line into X's tab through the chat view's route (chat_send(sid, line, false)); unchecked: nothing
  - a refusal from the store is shown in the card and nothing is sent
Demo data only. Headless Chromium. Run: .venv/bin/python tests_office_board_act_live.py
"""
import copy
import json
import pathlib
import sys
import types

HERE = pathlib.Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
_src = (HERE / "tests_office_board_ui.py").read_text(encoding="utf-8")
H = types.ModuleType("office_board_harness")
H.__file__ = str(HERE / "tests_office_board_ui.py")
exec(compile(_src[:_src.index("\ndef run(")], H.__file__, "exec"), H.__dict__)

passed = failed = 0


def check(name, ok, detail=""):
    global passed, failed
    if ok:
        passed += 1
        print(f"  [PASS] {name}")
    else:
        failed += 1
        print(f"  [FAIL] {name}  {detail}")


ACT = """      case 'task_user_act': {
        const id = +args[0], r = window.__actReply || {success: true, message: 'ok', tell: ''};
        if (r.success && args[1] === 'done') for (const k of ['decisions', 'stuck', 'open'])
          window.__tasks[k] = window.__tasks[k].map(t => t.id === id ? Object.assign({}, t, {waiting_on: '', status: 'doing', updated_at: Date.now() / 1000}) : t);
        window.__actAt = Date.now(); return R(r); }
      case 'chat_send': return R({success: true});
      case 'tasks_overview': window.__ovAt = (window.__ovAt || []).concat(Date.now()); return R(window.__tasks);
      default: return R('{}');"""


def stub():
    s = H.stub()
    assert s.count("      default: return R('{}');") == 1 and s.count("      case 'tasks_overview': return R(window.__tasks);\n") == 1
    return s.replace("      case 'tasks_overview': return R(window.__tasks);\n", "").replace("      default: return R('{}');", ACT)


def args_of(page, name):
    return page.evaluate("(n) => window.__args.filter(a => a[0] === n).map(a => a[1])", name)


try:
    from playwright.sync_api import sync_playwright
except ImportError:
    print("SKIP: playwright not installed")
    sys.exit(0)

srv = H.serve()
base = f"http://127.0.0.1:{srv.server_address[1]}"
with sync_playwright() as p:
    b = p.chromium.launch()
    page = b.new_page(viewport={"width": 1440, "height": 900})
    errors = []
    page.on("pageerror", lambda e: errors.append(f"pageerror {e}"))
    page.add_init_script(stub())
    page.goto(base + "/index.html")
    page.wait_for_function("window.SFOffice && window.SFBots && SFBots.enabled()", timeout=20000)
    page.wait_for_function("window.__sessionsLoaded !== false && Object.keys(window.__sfHost.sessions()).length >= 3", timeout=20000)
    fr = H.open_office(page, copy.deepcopy(H.TASKS))
    fr.evaluate("__office.openView('board')")
    page.wait_for_timeout(500)

    print("done on a task waiting on you (#15: the intern's, waiting on you)")
    fr.click('#vBody .nb[data-id="15"]')
    fr.wait_for_selector('#vOver [data-act="adone"]', timeout=5000)
    fr.click('#vOver [data-act="adone"]')
    fr.fill("#vOver textarea", "示範：回覆好了")
    tell = fr.evaluate("(() => { const l = document.querySelector('#vOver .wtell'); return l ? l.innerText.trim() : ''; })()")
    check("the card offers 也告訴 the intern whose task it is", "示範臨時分頁" in tell, tell)
    page.evaluate("window.__args.length = 0; window.__ovAt = []")
    fr.click("#vOver .wform .ok")
    page.wait_for_function("window.__actAt && (window.__ovAt || []).some(t => t >= window.__actAt)", timeout=4000)
    acts = args_of(page, "task_user_act")
    check("the card calls task_user_act(15, done, the text)", acts == [["15", "done", "示範：回覆好了"]], acts)
    lag = page.evaluate("Math.min(...window.__ovAt.filter(t => t >= window.__actAt)) - window.__actAt")
    check("…and the store is asked again at once (not at the 9 s pace)", lag < 2500, lag)
    page.wait_for_timeout(600)
    sends = args_of(page, "chat_send")
    check("one line goes into the intern's tab (s702) through chat_send, not submitted as a draft-clear",
          len(sends) == 1 and sends[0][0] == "s702" and sends[0][1].startswith("（看板）#15「") and "我做好了：示範：回覆好了" in sends[0][1]
          and sends[0][2] is False, sends)
    page.wait_for_function("""() => { const f = document.querySelector('#sfo-overlay iframe').contentWindow;
        const n = f.document.querySelector('#vBody .nb[data-id="15"]'); return n && !/等你|等待中/.test(n.closest('.note, .wn, [data-id]').innerText); }""", timeout=12000)
    check("the board shows the store's answer: #15 no longer waits on you", True)

    print("a note on someone else's task, without telling them (#12: the engineer's)")
    if fr.evaluate("!document.getElementById('vOver').hidden"):
        fr.click('#vOver [data-act="dclose"]')
    fr.click('#vBody .nb[data-id="12"]')
    fr.wait_for_selector('#vOver [data-act="anote"]', timeout=5000)
    check("not waiting on you, not yours: only 留言", fr.evaluate("!document.querySelector('#vOver [data-act=\"adone\"]')"))
    fr.click('#vOver [data-act="anote"]')
    fr.fill("#vOver textarea", "示範：先做表單")
    fr.uncheck("#vOver .wtell input")
    page.evaluate("window.__args.length = 0")
    fr.click("#vOver .wform .ok")
    page.wait_for_timeout(900)
    check("task_user_act(12, note, the text)", args_of(page, "task_user_act") == [["12", "note", "示範：先做表單"]], args_of(page, "task_user_act"))
    check("…and with 也告訴 unchecked nothing is sent", args_of(page, "chat_send") == [], args_of(page, "chat_send"))

    print("a refusal from the store")
    page.evaluate("window.__actReply = {success: false, code: 'not_waiting', message: '這件不是在等你'}")
    fr.click('#vOver [data-act="anote"]')
    fr.fill("#vOver textarea", "示範：再一句")
    page.evaluate("window.__args.length = 0")
    fr.click("#vOver .wform .ok")
    page.wait_for_timeout(700)
    msg = fr.evaluate("(document.querySelector('#vOver .wmsg') || {}).innerText || ''")
    check("the store's message is shown in the card", "這件不是在等你" in msg, msg)
    check("…and nothing is sent", args_of(page, "chat_send") == [], args_of(page, "chat_send"))
    check("no page error", not errors, errors[:3])
    b.close()
srv.shutdown()
print(f"\nResults: {passed} passed, {failed} failed")
sys.exit(1 if failed else 0)
