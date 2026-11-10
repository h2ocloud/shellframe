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

import agent_status

# The CLIs whose conversation the view draws: the ones sfctl `conversation` can read
# (opencode from its SQLite, pi through agent_pi, grok and cursor from their session
# files). Antigravity keeps its steps as protobuf; it and every other tab show their
# terminal, with a line saying why.
CHAT_PROVIDERS = ("claude", "codex", "opencode", "grok", "cursor", "pi")
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
        body["decision"] = self._link_decision(sid, activity, res)
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
        res = self._bots_link_answer({"sid": sid, "n": n, "id": qid}, via="desktop")
        return json.dumps(res, ensure_ascii=False, default=str)
