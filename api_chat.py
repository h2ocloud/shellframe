"""Api mixin — the desktop's chat view (settings.ui_style, web/chat.js).

The chat view is a screen over a tab and nothing more. It reads what a paired phone
reads and answers the way the phone answers, so each of these has one implementation:

  chat_view    the tab's turns (sfctl `conversation`, as GET /link/conversation) and,
               while the tab waits on the person, its question (`_link_decision`, as
               the `decision` of GET /link/bots)
  chat_send    text for the tab (sfctl `send`, as POST /link/send: it waits for a turn,
               a menu or the user's draft to end, and is queued meanwhile)
  chat_answer  a question answered by its option number (as POST /link/answer)

What the page does with them (wording, grouping, which options it dares to translate)
is display only and lives in web/chat.js; see docs/chat-shell.md.
"""

import hashlib
import json
import re
import subprocess

import agent_status
import bridge_home

# The CLIs whose conversation the view draws. Every other tab shows its terminal, with
# a line saying why.
CHAT_PROVIDERS = ("claude", "codex")
STYLES = ("chat", "cli")
_ACTIVITIES = ("working", "idle", "done", "decision", "stuck")


def ensure_ui_style(cfg: dict) -> bool:
    """settings.ui_style for a config.json that exists but predates the setting: this
    computer already used ShellBot, so it keeps the terminal it knows ("cli") until the
    user switches. A first launch has no config.json and gets DEFAULT_CONFIG ("chat").
    True when the value was written (the caller saves)."""
    st = cfg.get("settings")
    if not isinstance(st, dict):
        st = cfg["settings"] = {}
    if st.get("ui_style") in STYLES:
        return False
    st["ui_style"] = "cli"
    return True


class ChatApiMixin:
    def chat_view(self, sid: str, limit: int = 120, rev: str = "") -> str:
        """{"readable", "provider", "activity", "turns", "decision", "rev"} for the chat view
        of tab `sid`; {"unchanged": true} when nothing differs from `rev`. `why` says what
        an unreadable tab lacks: "provider" (a CLI the view does not draw) or
        "no_transcript" (nothing written yet, or no record found)."""
        s = self.sessions.get(sid)
        if not s:
            return json.dumps({"success": False, "code": "no_tab"})
        provider = agent_status.worker_kind(getattr(s, "cmd", "") or "")
        snap = self._agent_status_snapshot(sid)
        res = (snap[0] if isinstance(snap, tuple) else snap) or {}
        activity = str(res.get("state") or "")
        activity = activity if activity in _ACTIVITIES else ""
        body = {"sid": sid, "provider": provider, "activity": activity,
                "readable": False, "why": "provider", "turns": [], "format": ""}
        if provider in CHAT_PROVIDERS:
            try:
                limit = max(1, min(int(limit or 120), 400))
            except (TypeError, ValueError):
                limit = 120
            conv = self._execute_sfctl("conversation", {"sid": sid, "limit": limit}) or {}
            details = conv.get("details") or {}
            if conv.get("success"):
                body.update(readable=True, why="", turns=details.get("turns") or [],
                            format=details.get("format") or provider)
            else:
                body["why"] = "no_transcript"
        decision = self._link_decision(sid, activity, res)
        if decision and not self._chat_decision_on_screen(sid, decision):
            decision, body["decision_hidden"] = None, "not_on_screen"
        body["decision"] = decision
        stamp = hashlib.sha1(json.dumps(body, sort_keys=True, ensure_ascii=False,
                                        default=str).encode()).hexdigest()[:12]
        if rev and str(rev) == stamp:
            return json.dumps({"success": True, "unchanged": True, "rev": stamp})
        return json.dumps({"success": True, "rev": stamp, **body}, ensure_ascii=False, default=str)

    def chat_send(self, sid: str, text: str) -> str:
        """Type `text` into tab `sid` and submit it, the way a paired phone's message box
        does. {"success", "queued", "reason"}: queued means the tab was busy and the text
        goes in when that ends."""
        text = str(text or "")
        if not text.strip():
            return json.dumps({"success": False, "code": "empty"})
        res = self._execute_sfctl("send", {"sid": sid, "text": text, "submit": True}) or {}
        details = res.get("details") or {}
        return json.dumps({"success": bool(res.get("success")), "queued": bool(details.get("queued")),
                           "position": details.get("position"), "message": res.get("message", "")},
                          ensure_ascii=False)

    def chat_answer(self, sid: str, n, qid: str) -> str:
        """Answer the question chat_view showed with option `n`, only while `qid` is still the
        tab's question: the phone's /link/answer, with Telegram told it was answered here."""
        snap = self._agent_status_snapshot(sid) if sid in self.sessions else None
        res = (snap[0] if isinstance(snap, tuple) else snap) or {}
        state = str(res.get("state") or "")
        cur = self._link_decision(sid, state if state in _ACTIVITIES else "", res) if sid in self.sessions else None
        if cur and cur.get("id") == str(qid or "") and not self._chat_decision_on_screen(sid, cur):
            return json.dumps({"success": False, "code": "not_on_screen", "message": (
                "Those options are not on the tab's screen now; nothing was sent")}, ensure_ascii=False)
        res = self._bots_link_answer({"sid": sid, "n": n, "id": qid}, via="desktop")
        return json.dumps(res, ensure_ascii=False, default=str)

    # The shared reader (_link_decision) reads a menu from the screen with 40 lines of
    # scrollback above it and takes the first menu it finds. An older menu still up there
    # (measured: Codex's start-up update menu, answered, then a tool approval) would be
    # offered instead of the one on screen, and its "2" typed into the new one. Until that
    # reader changes, the chat view shows and answers a menu only when its options are on
    # the visible screen; a [[SF:RED]] question is the agent's own words and needs no screen.
    def _chat_visible_screen(self, sid: str):
        name = getattr(self.sessions.get(sid), "_tmux_name", None)
        if not name:
            return None                    # no tmux (Windows): nothing better to compare with
        try:
            r = subprocess.run(["tmux", "capture-pane", "-p", "-t", name], capture_output=True, text=True, timeout=2)
            return r.stdout if r.returncode == 0 else None
        except Exception:
            return None

    def _chat_decision_on_screen(self, sid: str, decision: dict) -> bool:
        red = bridge_home.red_pending(sid)
        if red and decision.get("reason") == agent_status.redact(red["reason"], 300):
            return True
        screen = self._chat_visible_screen(sid)
        if screen is None:
            return True
        rows = [re.sub(r"^\s*│\s?|\s*│\s*$", "", ln).strip().lstrip("❯›").strip() for ln in screen.splitlines()]
        for o in decision.get("options") or []:
            key = str(o.get("text") or "").split("<redacted>")[0][:24].strip()
            if not any(re.match(rf"{int(o.get('n') or 0)}[.)]\s", row) and key in row for row in rows):
                return False
        return True
