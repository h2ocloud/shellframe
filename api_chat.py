"""Api mixin — the desktop's chat view (settings.ui_style, web/chat.js).

The chat view is a screen over a tab and nothing more. It reads what a paired phone
reads and answers the way the phone answers, so each of these has one implementation:

  chat_view    the tab's turns (sfctl `conversation`, as GET /link/conversation) and,
               while the tab waits on the person, its question (`_link_decision`, as
               the `decision` of GET /link/bots)
  chat_send    text for the tab (sfctl `send`, as POST /link/send: it waits for a turn,
               a menu or the user's draft to end, and is queued meanwhile)
  chat_answer  a question answered by its option number (as POST /link/answer)
  chat_send_now  a queued send typed in at once (sfctl send --now), into what the tab is doing

What the page does with them (wording, grouping, which options it dares to translate)
is display only and lives in web/chat.js; see docs/chat-shell.md.
"""

import base64
import hashlib
import json
import os
import subprocess
import sys
from pathlib import Path

import agent_link
import agent_status
import sf_lang
import tab_delivery

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
        q = body["queue"] = tab_delivery.queue_state(sid)     # its own sends: still waiting, or given up
        why = tab_delivery.busy_reason(self, sid) if q["queued"] else ""
        if why:              # what holds them now, and since when its turn runs: the bubble says so (chat.js waitWords)
            q["busy"] = {"reason": why, "since": int(float(res.get("prompt_at") or 0))}
        stamp = hashlib.sha1(json.dumps(body, sort_keys=True, ensure_ascii=False,
                                        default=str).encode()).hexdigest()[:12]
        if rev and str(rev) == stamp:
            return json.dumps({"success": True, "unchanged": True, "rev": stamp})
        return json.dumps({"success": True, "rev": stamp, **body}, ensure_ascii=False, default=str)

    def chat_send(self, sid: str, text: str, clear_draft: bool = False) -> str:
        """Type `text` into tab `sid` and submit it, the way a paired phone's message box
        does. {"success", "queued", "reason", "id"}: queued means the tab was busy and the
        text goes in when that ends; `id` is how chat_view's `queue` names it.

        Text the person left unsent in the terminal's input box is not something the chat
        view can see, and the send would wait behind it for good (measured: queued, then
        dropped after 30 min with a debug line only). So that answers {"code": "draft",
        "draft": its first 60 characters} and sends nothing: the view asks whether to clear
        it. `clear_draft`: the person said yes; it is cleared, then the text goes in."""
        text = str(text or "")
        if not text.strip():
            return json.dumps({"success": False, "code": "empty"})
        draft = "" if clear_draft else tab_delivery.draft_of(self, sid)
        if draft:
            flat = " ".join(draft.split())
            return json.dumps({"success": False, "code": "draft",
                               "draft": flat[:60] + ("…" if len(flat) > 60 else "")}, ensure_ascii=False)
        agent_link.clear_chain(sid)          # the person wrote this: what the tab sends next starts a fresh A2A chain
        res = self._execute_sfctl("send", {"sid": sid, "text": text, "submit": True, "what": "chat",
                                           "clear_draft": bool(clear_draft)}) or {}
        s = self.sessions.get(sid)
        if res.get("success") and s is not None:
            self._auto_name(sid, s, text)      # the first message names a temporary bot, as Enter in the terminal does
        details = res.get("details") or {}
        return json.dumps({"success": bool(res.get("success")), "queued": bool(details.get("queued")),
                           "position": details.get("position"), "id": details.get("id"),
                           "reason": details.get("reason"), "code": res.get("code"),
                           "message": res.get("message", "")}, ensure_ascii=False)

    def chat_send_now(self, sid: str, qid: str) -> str:
        """The queued bubble's "send now": take chat send `qid` out of tab `sid`'s queue and type
        it in at once (sfctl send --now), into whatever the tab is doing. {"success"}; "gone"
        when it already went in, is being typed in, or was given up."""
        item = tab_delivery.take(sid, str(qid or ""))
        if item is None:
            return json.dumps({"success": False, "code": "gone", "message": sf_lang.pick(
                "It has already gone in, or was given up.", "它已經送進去了，或已經放棄。")}, ensure_ascii=False)
        if item.get("clear_draft") and not item.get("cleared"):
            tab_delivery.clear_draft_now(self, sid)          # the person chose to drop the draft before it waited
        res = self._execute_sfctl("send", {"sid": sid, "text": item["text"], "submit": True, "what": "chat",
                                           "now": True}) or {}
        return json.dumps({"success": bool(res.get("success")), "message": res.get("message", "")}, ensure_ascii=False)

    # A path in a message: a picture is shown (at most this size, read as one data: URL), anything else
    # is shown in Finder or Explorer. Only the one file asked for is read.
    _PREVIEW_IMG = {".png": "image/png", ".jpg": "image/jpeg", ".jpeg": "image/jpeg", ".gif": "image/gif",
                    ".webp": "image/webp", ".bmp": "image/bmp"}
    _PREVIEW_MAX = 12 * 1024 * 1024

    @staticmethod
    def _chat_path(path) -> Path:
        return Path(os.path.expanduser(str(path or "").strip().strip("`'\""))).resolve()

    def chat_file_preview(self, path: str) -> str:
        """{"success", "kind": "image" | "file" | "dir", "name", "data" (a picture's data: URL)}; "missing"."""
        try:
            p = self._chat_path(path)
            if not p.exists():
                return json.dumps({"success": False, "code": "missing"})
            mime = None if p.is_dir() else self._PREVIEW_IMG.get(p.suffix.lower())
            if not mime or p.stat().st_size > self._PREVIEW_MAX:
                return json.dumps({"success": True, "kind": "dir" if p.is_dir() else "file", "name": p.name}, ensure_ascii=False)
            data = f"data:{mime};base64," + base64.b64encode(p.read_bytes()).decode("ascii")
            return json.dumps({"success": True, "kind": "image", "name": p.name, "data": data}, ensure_ascii=False)
        except OSError as e:
            return json.dumps({"success": False, "code": "error", "message": str(e)}, ensure_ascii=False)

    def chat_reveal(self, path: str) -> str:
        """Show a path from a message in Finder (Explorer on Windows), selected."""
        p = self._chat_path(path)
        if not p.exists():
            return json.dumps({"success": False, "code": "missing"})
        if sys.platform == "darwin":
            cmd = ["open", "-R", str(p)]
        elif sys.platform.startswith("win"):
            cmd = ["explorer", f"/select,{p}"]
        else:
            cmd = ["xdg-open", str(p if p.is_dir() else p.parent)]
        try:
            subprocess.Popen(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        except OSError as e:
            return json.dumps({"success": False, "code": "error", "message": str(e)}, ensure_ascii=False)
        return json.dumps({"success": True})

    def chat_answer(self, sid: str, n, qid: str) -> str:
        """Answer the question chat_view showed with option `n`, only while `qid` is still the
        tab's question: the phone's /link/answer, with Telegram told it was answered here."""
        res = self._bots_link_answer({"sid": sid, "n": n, "id": qid}, via="desktop")
        return json.dumps(res, ensure_ascii=False, default=str)
