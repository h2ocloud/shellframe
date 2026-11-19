"""Api mixin — the office's objects (settings.experimental_office): web/office_objects.js's calls.

The office draws furniture and one shared popup; what each piece of furniture holds comes from an
office object (office_objects.py: a declaration and four hooks; built in: office_bookshelf.py,
office_personnel.py; or from an enabled plugin's manifest `office_objects`). This mixin is the page's
way in and the only place that gives a hook the host's data (`Ctx`: the roster, which bot has which
tab, the open tasks, the schedules, the office's groups; each one a callable, read only when a hook
asks) and acts on an answer: `open` goes to the app's own opener (`open_local_file`), the page never
gets the path. Refused while the office switch is off; the page calls them only while the office is
open (a summary at most every 30 s, the rest when a popup is open or used).

js_api (every answer is JSON; `lang` is the page's language, en or zh, so a text is in the words the
person reads):
  office_objects(lang)                          the objects' declarations, each with its `badge`
  office_object_list(obj_id, args_json, lang)   args: {query, filters, sort, offset, limit}
  office_object_item(obj_id, item_id, lang)     one entry (its file is read here, masked, capped)
  office_object_action(obj_id, item_id, action_id, args_json, lang)
"""
import json
import os
import subprocess
import sys

import api_office
import office_bookshelf
import office_deliverables
import office_objects
import office_personnel
import sf_lang
import state_store as ss
from api_host import main
from sf_log import _swallow

_ = sf_lang.pick


def builtin_objects():
    return [office_bookshelf.Bookshelf(), office_personnel.Personnel(), office_deliverables.Deliverables()]


class OfficeObjectsApiMixin:
    def _oo_registry(self):
        reg = getattr(self, "_oo_reg", None)
        if reg is None:
            reg = office_objects.Registry(builtin_objects(), self._oo_plugins)
            self._oo_reg = reg
        return reg

    def _oo_plugins(self) -> list:
        reg = getattr(self, "_plugins", None)
        return list(getattr(reg, "plugins", None) or [])

    def _oo_off(self, lang: str):
        if self._office_on():
            return None
        return json.dumps({"success": False, "code": "off", "objects": [],
                           "message": _("The office is off.", "辦公室沒開。")}, ensure_ascii=False)

    def _oo_ctx(self, lang: str):
        """What a built-in hook may ask for. Each is a callable: nothing is read until a hook calls it."""
        def roster():
            return (main.load_config().get("agent_roster") or {}) if self._bots_enabled() else {}

        def bot_states():
            cfg = main.load_config()
            order, out = self._ordered_sids(cfg), {}
            for sid in self._live_sids():
                role = self._bots_role_of(sid, cfg)
                if role and role not in out:
                    out[role] = {"state": "awake", "sid": sid, "position": order.index(sid) + 1 if sid in order else 0}
            return out

        def open_tasks():
            try:
                return list(self._tasks_lists().get("open") or [])
            except Exception:
                return []

        def schedules():
            try:
                return list(json.loads(self.schedules_list()).get("schedules") or [])
            except Exception:
                return []

        return office_objects.Ctx(
            lang, roster=roster, bot_states=bot_states, open_tasks=open_tasks, schedules=schedules,
            groups=lambda: api_office.read_prefs().get("groups") or {},
            pi_hosts=office_personnel.pi_provider_hosts,
            live_tabs=self._deliv_live_tabs, tab_labels=self._deliv_tab_labels,
            task_note=lambda tid, text: ss.note(int(tid), text, actor=ss.USER))

    @staticmethod
    def _oo_json(res: dict) -> str:
        return json.dumps(res, ensure_ascii=False, default=str)

    def office_objects(self, lang: str = "") -> str:
        off = self._oo_off(lang)
        if off:
            return off
        lang = office_objects.lang_of(lang)
        try:
            return self._oo_json({"success": True, "objects": self._oo_registry().describe(self._oo_ctx(lang), lang)})
        except Exception:
            _swallow("office_objects")
            return self._oo_json({"success": False, "code": "unavailable", "objects": [],
                                  "message": office_objects.words("unavailable", lang)})

    def office_object_list(self, obj_id: str, args_json: str = "{}", lang: str = "") -> str:
        off = self._oo_off(lang)
        if off:
            return off
        lang = office_objects.lang_of(lang)
        try:
            a = json.loads(args_json or "{}")
            a = a if isinstance(a, dict) else {}
        except ValueError:
            a = {}
        return self._oo_json(self._oo_registry().list(
            obj_id, self._oo_ctx(lang), lang, a.get("query") or "", a.get("filters"), a.get("sort") or "",
            a.get("offset") or 0, a.get("limit") or office_objects.LIST_MAX))

    def office_object_item(self, obj_id: str, item_id: str, lang: str = "") -> str:
        off = self._oo_off(lang)
        if off:
            return off
        lang = office_objects.lang_of(lang)
        return self._oo_json(self._oo_registry().item(obj_id, self._oo_ctx(lang), lang, item_id))

    def office_object_action(self, obj_id: str, item_id: str, action_id: str, args_json: str = "{}",
                             lang: str = "") -> str:
        off = self._oo_off(lang)
        if off:
            return off
        lang = office_objects.lang_of(lang)
        try:
            a = json.loads(args_json or "{}")
        except ValueError:
            a = {}
        res = self._oo_registry().action(obj_id, self._oo_ctx(lang), lang, item_id, action_id,
                                         a if isinstance(a, dict) else {})
        self._oo_open_result(res, lang)             # `open` (a file), `reveal` (in the file manager), `url` (the browser)
        return self._oo_json(res)

    def _oo_open_result(self, res: dict, lang: str = "") -> None:
        """Carry out what an answer asked to be opened (a path, a folder to show, a link) and take it off the answer."""
        for key in ("open", "reveal", "url"):
            target = res.pop(key, "")
            if not target:
                continue
            try:
                got = json.loads(self.open_url(target) if key == "url" else
                                 self._oo_reveal(target) if key == "reveal" else self.open_local_file(target))
                if not got.get("success"):
                    res.update(ok=False, message=office_objects.clip(got.get("message") or "", 120))
            except Exception:
                _swallow("office_object_action:open")
                res.update(ok=False, message=office_objects.words("unavailable", lang))

    @staticmethod
    def _oo_reveal(path: str) -> str:
        """Show a file or folder in the system's file manager (nothing is opened or unpacked)."""
        try:
            if not os.path.exists(path):
                return json.dumps({"success": False, "message": "not found"})
            if main.IS_WIN:
                subprocess.Popen(["explorer", "/select,", os.path.normpath(path)])
            elif sys.platform == "darwin":
                subprocess.Popen(["/usr/bin/open", "-R", path], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            else:
                subprocess.Popen(["xdg-open", path if os.path.isdir(path) else os.path.dirname(path)],
                                 stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            return json.dumps({"success": True})
        except Exception as e:
            return json.dumps({"success": False, "message": str(e)})
