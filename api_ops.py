"""Api mixin — the ops TV (settings.experimental_ops): the page's calls into office_ops.py.

The TV is a built-in office object (office_ops.py: its declaration, summary and actions go through the
office's own `office_objects` / `office_object_action`; this mixin has the three calls that are not
those):

  office_object_view(obj_id, lang)  the detail screen as widgets (gauge bars stack spark strip table stat rules),
                                    cut and masked by office_objects.clean_view; the page draws them
  office_object_live(obj_id, on)    the detail screen is open (`on`): the collector reads every 2 s. The page
                                    says it again every 30 s; 60 s without word and the collector goes back to
                                    its slow pace by itself
  ops_state()                       {"on", "level", "reason", "since"}: memory only, cheap; the sidebar's red dot

and the collector's life: `_ops_start` (app start) and `_ops_sync` (the settings were saved, or any call here
sees the flag differ) start or stop the collector to match the flag. While the flag is off nothing runs and
every call answers `code: "off"`. When the level changes the collector thread calls `_ops_push`, which tells the
page (`window.__sfOpsLevel`); that thread holds no lock and the page call is `evaluate_js`, never a subprocess.
"""
import json

import office_objects
import office_ops
import sf_lang
from api_host import main
from sf_log import _swallow

_ = sf_lang.pick


class OpsApiMixin:
    def _ops_flag(self) -> bool:
        try:
            return (main.load_config().get("settings", {}) or {}).get("experimental_ops", False) is True
        except Exception:
            return False

    def _ops_sync(self) -> bool:
        """Make the collector match the flag; True when it is on."""
        on = self._ops_flag()
        if on != office_ops.enabled():
            office_ops.configure(on, self._ops_push)
        return on

    def _ops_start(self) -> None:
        try:
            self._ops_sync()
        except Exception:
            _swallow("ops:start")

    def _ops_shutdown(self) -> None:
        try:
            office_ops.configure(False, tell=False)           # the app is closing: no call into the page
        except Exception:
            _swallow("ops:shutdown")

    def _ops_push(self, snap: dict) -> None:
        """The level changed: tell the page (collector thread, no lock held)."""
        win = getattr(self, "_window", None)
        if not win:
            return
        since = snap.get("since")
        payload = json.dumps({"level": snap.get("level"), "reason": snap.get("reason") or "",
                              "since": int(since) if isinstance(since, (int, float)) else None}, ensure_ascii=False)
        try:
            win.evaluate_js(f"window.__sfOpsLevel && window.__sfOpsLevel({payload})")
        except Exception:
            _swallow("ops:push")

    @staticmethod
    def _ops_json(res: dict) -> str:
        return json.dumps(res, ensure_ascii=False, default=str)

    def _ops_off(self, lang: str):
        """The refusal while the switch (or the office) is off, else None."""
        off = self._oo_off(lang)
        if off:
            return off
        if not self._ops_sync():
            return self._ops_json({"success": False, "code": "off",
                                   "message": _("Ops is off.", "維運沒有打開。", office_objects.lang_of(lang))})
        return None

    def ops_state(self) -> str:
        if not self._ops_sync():
            return self._ops_json({"on": False, "level": "none", "reason": "", "since": None})
        st = office_ops.collector().state()
        since = st["since"]
        return self._ops_json({"on": True, "level": st["level"], "reason": st["reason"],
                               "since": int(since) if since is not None else None})

    def office_object_view(self, obj_id: str, lang: str = "") -> str:
        off = self._ops_off(lang)
        if off:
            return off
        lang = office_objects.lang_of(lang)
        office_ops.collector().lang = lang
        return self._ops_json(self._oo_registry().view(obj_id, self._oo_ctx(lang), lang))

    def office_object_live(self, obj_id: str, on: bool = True) -> str:
        off = self._ops_off("")
        if off:
            return off
        if str(obj_id) != office_ops.DECLARATION["id"]:
            return self._ops_json({"success": False, "code": "unknown", "message": office_objects.words("unknown")})
        office_ops.collector().live(on is True or str(on).lower() == "true")
        return self._ops_json({"success": True})
