#!/usr/bin/env python3
"""設定裡的「角色群組」開關與編輯器，實際在瀏覽器裡跑一遍。

這半邊一直只用 sfctl 驗過。但使用者要的是「介面要有地方可以開關」——後端通了、
面板有個 JS 錯誤的話，對他來說就是沒做。所以這支把 index.html 裡真正的那段
markup 與真正的 handler 抓出來跑，不重寫一份會走樣的複製品：

  - 預設關閉時編輯器是收起來的
  - 按下開關會寫進設定、把編輯器打開、並去讀群組清單
  - 勾角色 + 取名 → 新增，清單上看得到，輸入框會清空
  - 後端拒絕（例如名稱空白）時錯誤訊息要顯示出來，不能默默沒反應
  - 刪除鍵真的會呼叫 group_delete

需要 playwright；沒裝就 SKIP。

跑法：.venv/bin/python tests_groups_ui.py
"""
import json
import pathlib
import re
import sys

HERE = pathlib.Path(__file__).parent
SHOT = HERE / "qa-shots" / "groups-editor.png"

passed = failed = 0


def check(name, ok, detail=""):
    global passed, failed
    if ok:
        passed += 1
        print(f"  [PASS] {name}")
    else:
        failed += 1
        print(f"  [FAIL] {name}  {detail}")


try:
    from playwright.sync_api import sync_playwright
except ImportError:
    print("SKIP  tests_groups_ui.py（沒裝 playwright）")
    sys.exit(0)

html = (HERE / "web/index.html").read_text(encoding="utf-8")
STYLE = re.search(r"<style>(.*?)</style>", html, re.S).group(1)


def grab(pattern):
    m = re.search(pattern, html, re.S)
    assert m, f"index.html 找不到：{pattern}"
    return m.group(0)


# The real markup block and the real handlers, lifted verbatim.
MARKUP = grab(r'<div>\s*<label[^>]*>角色群組.*?</div>\s*</div>\s*</div>')
REFRESH = grab(r"  async function refreshGroupsEditor\(\) \{.*?\n  \}\n")
SAVE = grab(r"  document\.getElementById\('group-save'\)\.addEventListener.*?\n  \}\);\n")
TOGGLE = grab(r"  document\.getElementById\('setting-experimental-groups'\)\n"
              r"    \.addEventListener.*?\n    \}\);\n")

PAGE = """<!doctype html><meta charset="utf-8">
<style>__STYLE__
  body { background:#24283b; margin:0; padding:16px; width:520px; }
</style>
__MARKUP__
<script>
window.__errors = [];
window.onerror = (m) => { window.__errors.push(String(m)); };
const esc2 = (s) => { const d = document.createElement('div');
                      d.textContent = s == null ? '' : s; return d.innerHTML; };
let _groupRoles = [];
const config = { settings: { experimental_groups: false } };
window.__saved = null;
window.__calls = [];
// A stand-in for the real store: the dispatch's own rules (a role must exist in
// the roster, a name is required) are what the panel has to surface, so they are
// reproduced here rather than stubbed into always-success.
let GROUPS = [];
const ROLES = ['時程信件', 'Coding', '研究', '知庫', '規格站'];
const pywebview = { api: {
  save_settings: (s) => { window.__saved = JSON.parse(s); return Promise.resolve('{}'); },
  sfctl_call: (cmd, argsJson) => {
    const a = JSON.parse(argsJson || '{}');
    window.__calls.push([cmd, a]);
    if (cmd === 'group_list') {
      return Promise.resolve(JSON.stringify(
        { success: true, details: { groups: GROUPS, roles: ROLES } }));
    }
    if (cmd === 'group_save') {
      if (!a.name) return Promise.resolve(JSON.stringify(
        { success: false, message: '群組名稱必填' }));
      if (!(a.roles || []).length) return Promise.resolve(JSON.stringify(
        { success: false, message: '群組至少要有一個角色' }));
      GROUPS = GROUPS.filter(g => g.name !== a.name).concat([{ name: a.name, roles: a.roles }]);
      return Promise.resolve(JSON.stringify(
        { success: true, message: '已儲存群組「' + a.name + '」' }));
    }
    if (cmd === 'group_delete') {
      GROUPS = GROUPS.filter(g => g.name !== a.name);
      return Promise.resolve(JSON.stringify({ success: true, message: '已刪除' }));
    }
    return Promise.resolve('{"success":false,"message":"unknown"}');
  },
}};
__REFRESH__
__SAVE__
__TOGGLE__
</script>"""
PAGE = (PAGE.replace("__STYLE__", STYLE).replace("__MARKUP__", MARKUP)
            .replace("__REFRESH__", REFRESH).replace("__SAVE__", SAVE)
            .replace("__TOGGLE__", TOGGLE))

tmp = HERE / ".groups_qa.html"
tmp.write_text(PAGE, encoding="utf-8")
SHOT.parent.mkdir(exist_ok=True)
try:
    with sync_playwright() as pw:
        browser = pw.chromium.launch()
        page = browser.new_page(viewport={"width": 520, "height": 420},
                                device_scale_factor=2)
        console = []
        page.on("console", lambda m: console.append(f"{m.type}: {m.text}")
                if m.type == "error" else None)
        page.goto(tmp.resolve().as_uri())

        editor = page.locator("#groups-editor")
        check("預設是收起來的", editor.is_hidden())

        page.click("#setting-experimental-groups")
        page.wait_for_selector(".group-role-cb", timeout=5000)
        check("按下開關就展開", editor.is_visible())
        check("開關把設定寫回去",
              page.evaluate("window.__saved && window.__saved.experimental_groups") is True)
        check("名冊角色全部列成勾選框",
              page.locator(".group-role-cb").count() == 5,
              str(page.locator(".group-role-cb").count()))
        check("還沒有群組時給的是下一步，不是空白",
              "還沒有群組" in page.locator("#groups-list").inner_text())

        # 空名稱：後端會拒絕，面板必須把理由顯示出來
        page.click("#group-save")
        page.wait_for_function("document.getElementById('group-msg').textContent.length > 0",
                               timeout=5000)
        check("空名稱被拒絕且說得出原因",
              "必填" in page.locator("#group-msg").inner_text(),
              page.locator("#group-msg").inner_text())

        # 正常建立
        page.fill("#group-new-name", "小隊")
        page.locator(".group-role-cb").nth(1).check()
        page.locator(".group-role-cb").nth(2).check()
        page.click("#group-save")
        page.wait_for_function(
            "document.getElementById('groups-list').innerText.includes('小隊')", timeout=5000)
        listed = page.locator("#groups-list").inner_text()
        check("新群組出現在清單上", "小隊" in listed)
        check("成員也一起顯示", "Coding" in listed and "研究" in listed, listed)
        check("存好之後名稱欄清空", page.input_value("#group-new-name") == "")
        check("勾選也一起清掉",
              page.evaluate("document.querySelectorAll('.group-role-cb:checked').length") == 0)

        page.screenshot(path=str(SHOT))

        # 刪除
        page.click("[data-group-del]")
        page.wait_for_function(
            "!document.getElementById('groups-list').innerText.includes('小隊')", timeout=5000)
        check("刪除鍵真的送出 group_delete",
              any(c[0] == "group_delete" for c in page.evaluate("window.__calls")))

        check("沒有 JS 例外", page.evaluate("window.__errors").__len__() == 0,
              str(page.evaluate("window.__errors")))
        check("console 沒有錯誤", not console, str(console))
        browser.close()
finally:
    tmp.unlink(missing_ok=True)

print(f"\n截圖：{SHOT}")
print(f"Results: {passed} passed, {failed} failed")
print("ALL PASS" if not failed else "FAILED")
sys.exit(1 if failed else 0)
