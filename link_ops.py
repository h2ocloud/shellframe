"""Frame Link's read-only view of a computer's ops board (the paired computers' TVs in the office).

Both halves of three signed routes live here, so frame_link.py (at its line budget) only lists the paths:

  GET  /link/ops                    this computer's summary: {level, reason, since, muted_until, updated, face}. `level`
                                    is the ops TV's own (ok warn alert muted none) or `off`: the switch is off here
  GET  /link/ops/view?lang=&sort=   the detail screen (office_ops.Collector.view, cleaned like the local one): the rules
                                    widget carries `readonly: true`; there is nothing to write except the top five's order,
                                    and `sort` shows that order for this one answer only (it is not saved here)
  POST /link/ops/live {on}          a paired computer's window is open (`on`) or closed: 2 s readings for it, on the
                                    same 60 s heartbeat as the local window, kept apart per peer so one closing does not
                                    end another's (or this computer's own)

`serve` answers them (api_remote's dispatcher calls it with `on`, whether this computer's `experimental_ops` is on).
`peer_summary` / `peer_view` / `peer_live` ask a paired computer and clean what comes back, as if it were a stranger's:
the answers are `{success: false, code}` with code `offline` (not reachable), `old` (HTTP 404: it has no such route),
`denied` (HTTP 403: one-way pairing) or `error`; a peer whose switch is off is a success with `level: "off"`.
Nothing here samples, writes a file or keeps a thread.
"""
import json
from urllib.parse import quote

import office_objects as oo
import office_ops

FACE_KEYS = ("level", "reason", "since", "muted_until", "updated", "face")
_NO_FACE = {"level": "off", "reason": "", "since": None, "muted_until": None, "updated": None, "face": None}
WORDS = {"off": ("Ops is off on this computer.", "這台沒有打開維運戰情。"),
         "bad": ("Bad request.", "請求有誤。")}


def _w(code, lang):
    en, zh = WORDS[code]
    return zh if oo.lang_of(lang) == "zh" else en


# ── this computer answering ──────────────────────────────────────────────────
def summary(lang) -> dict:
    """The TV's summary without the object's badge and hint."""
    raw = oo.clean_summary(office_ops.collector().summary(oo.lang_of(lang)))
    return dict({"success": True}, **{k: raw.get(k) for k in FACE_KEYS})


def readonly(view: dict) -> dict:
    """Every rules widget marked `readonly: true` (the rules are changed on the computer that has them)."""
    for sec in view.get("sections") or []:
        for w in sec.get("widgets") or []:
            if w.get("kind") == "rules":
                w["readonly"] = True
    return view


def view(lang, sort=None) -> dict:
    out = oo.clean_view(office_ops.collector().view(oo.lang_of(lang), sort if sort in office_ops.TOP_SORTS else None))
    return dict({"success": True}, **readonly(out))


def serve(on: bool, cmd: str, args: dict) -> dict:
    """The answer to one signed route. `on`: this computer's ops switch."""
    args = args if isinstance(args, dict) else {}
    lang = oo.lang_of(args.get("lang"))
    if not on:
        if cmd == "ops":
            return dict({"success": True}, **_NO_FACE)
        return {"success": False, "code": "off", "message": _w("off", lang)}
    if cmd == "ops":
        return summary(lang)
    if cmd == "ops_view":
        return view(lang, str(args.get("sort") or ""))
    if cmd == "ops_live":
        peer = str(args.get("peer_id") or "")
        office_ops.collector().live(args.get("on") is True, who="peer:" + peer)
        return {"success": True}
    return {"success": False, "code": "bad", "message": _w("bad", lang)}


# ── asking a paired computer ─────────────────────────────────────────────────
def ask(fl, peer_id: str, method: str, path_qs: str, body: bytes = b"", timeout: float = 6.0) -> dict:
    """One signed request to a paired computer: its answer, or `{success: false, code, message}`. A computer the poll
    loop finds unreachable is not asked."""
    try:
        peer, err = fl._peer_or_err(peer_id)
        if err or not fl._peer_status.get(peer_id, {}).get("reachable"):
            return {"success": False, "code": "offline", "message": (err or {}).get("message", "offline")}
        res = fl._signed_request(peer, method, path_qs, body, timeout=timeout)
    except Exception as e:
        status = getattr(e, "status", None)
        code = "old" if status == 404 else "denied" if status == 403 else "error" if status else "offline"
        return {"success": False, "code": code, "message": str(e)[:200]}
    return res if isinstance(res, dict) else {"success": False, "code": "error", "message": "bad answer"}


def peer_summary(fl, peer_id: str, lang) -> dict:
    res = ask(fl, peer_id, "GET", f"/link/ops?lang={quote(oo.lang_of(lang))}")
    if not res.get("success"):
        return res
    if res.get("level") == "off":                       # `off` is not one of the TV's levels: kept apart before cleaning
        return dict({"success": True}, **_NO_FACE)
    raw = oo.clean_summary(res)
    return dict({"success": True}, **{k: raw.get(k) for k in FACE_KEYS})


def peer_view(fl, peer_id: str, lang, sort=None) -> dict:
    q = f"lang={quote(oo.lang_of(lang))}" + (f"&sort={quote(sort)}" if sort in office_ops.TOP_SORTS else "")
    res = ask(fl, peer_id, "GET", f"/link/ops/view?{q}")
    if not res.get("success"):
        if res.get("code") == "off":                    # the peer switched it off after the summary
            return {"success": False, "code": "off", "message": _w("off", lang)}
        return res
    return dict({"success": True}, **readonly(oo.clean_view(res)))     # forced here whatever the peer sent


def peer_live(fl, peer_id: str, on: bool) -> dict:
    res = ask(fl, peer_id, "POST", "/link/ops/live", json.dumps({"on": bool(on)}).encode(), timeout=5.0)
    return {"success": bool(res.get("success"))} if res.get("success") else res
