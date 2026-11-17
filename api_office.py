"""Api mixin — the office (settings.experimental_office): web/office.js's calls.

js_api:
  office_prefs()            what the office keeps for this computer: seats moved by hand,
                            accessories picked, flat/3D view, legend folded (office.json in the
                            state folder). pywebview starts in private mode and clears
                            localStorage on every start, so the page cannot keep them itself.
  office_prefs_save(json)   writes it (atomic; known keys only, size-capped). Not config.json.
  office_helpers()          the open Claude Code tabs' running subagents (office_helpers.scan),
                            sid → list. Called by the page only while the office is open, every
                            ~3 s; the transcript path comes from the status monitor's own lookup
                            (StatusTracker.transcript_path); only a folder listing, stats and the
                            small meta files. Refused while the switch is off.
"""
import json
import os
import tempfile
import threading
import time

import agent_status
import office_helpers
import sf_config
from api_host import main
from sf_log import _swallow

PREF_KEYS = ("seats", "items", "mode", "legendMin")
PREFS_MAX = 256 * 1024
_PREFS_LOCK = threading.Lock()


def prefs_file():
    return sf_config.state_dir() / "office.json"


def read_prefs() -> dict:
    try:
        with open(prefs_file(), encoding="utf-8") as f:
            got = json.load(f)
        return {k: v for k, v in got.items() if k in PREF_KEYS} if isinstance(got, dict) else {}
    except (OSError, ValueError):
        return {}


def write_prefs(prefs: dict) -> None:
    path = prefs_file()
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=str(path.parent), prefix=".office-", suffix=".json")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(prefs, f, ensure_ascii=False)
        os.replace(tmp, path)
    except Exception:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise


class OfficeApiMixin:
    def _office_on(self) -> bool:
        try:
            return (main.load_config().get("settings", {}) or {}).get("experimental_office", False) is True
        except Exception:
            return False

    def office_prefs(self) -> str:
        with _PREFS_LOCK:
            return json.dumps({"success": True, "prefs": read_prefs()}, ensure_ascii=False)

    def office_prefs_save(self, prefs_json: str) -> str:
        if len(prefs_json or "") > PREFS_MAX:
            return json.dumps({"success": False, "message": "too large"})
        try:
            got = json.loads(prefs_json or "{}")
        except ValueError:
            return json.dumps({"success": False, "message": "not JSON"})
        if not isinstance(got, dict):
            return json.dumps({"success": False, "message": "not an object"})
        keep = {k: v for k, v in got.items() if k in PREF_KEYS}
        try:
            with _PREFS_LOCK:
                write_prefs(keep)
        except Exception as e:
            _swallow("office_prefs_save")
            return json.dumps({"success": False, "message": str(e)[:200]})
        return json.dumps({"success": True})

    def office_helpers(self) -> str:
        if not self._office_on():
            return json.dumps({"success": False, "message": "experimental_office is off", "helpers": {}})
        now = time.time()
        out = {}
        tracker = getattr(self, "_status_tracker", None)
        for sid, s in list(self.sessions.items()):
            if not getattr(s, "alive", True) or tracker is None:
                continue
            if agent_status.worker_kind(str(getattr(s, "cmd", "") or "")) != "claude":
                continue
            try:
                path = tracker.transcript_path(sid, self._worker_ctx(sid, s))
                rows = office_helpers.scan(office_helpers.folder_of(path or ""), now)
            except Exception:
                _swallow(f"office_helpers:{sid}")
                continue
            if rows:
                out[sid] = rows
        return json.dumps({"success": True, "now": now, "helpers": out}, ensure_ascii=False)
