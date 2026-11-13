"""api_push — push notifications to the paired phones (sf_push), wired into the app.

Frame Link routes (frame_link.py → api_remote → here; a one-way pairing gets 403):
  POST /link/push/register {token, env, topic, device_name, prefs}
  POST /link/push/unregister {}
  GET  /link/push/status
  POST /link/push/test {}
  POST /link/peer/name {name}       a paired phone renames itself here, without pairing again

The cues, all independent of the Telegram bridge (docs/mobile-link.md):
  decision  a [[SF:RED]] question, exactly as /link/bots shows it (_link_decision). Caught at
            the end of every turn here too: the Stop hook that catches it otherwise is off by
            default and exists only for Claude Code.
  stuck     a tab stopping on a dialog (the status monitor's BLOCKED) or [[SF:YELLOW:…]]
  done      a finished turn of the primary bot (every bot when the phone asks for that), and
            the result of a delegated task (a2a-result)
  info      sfctl notify
A question answered anywhere or replaced by a new turn is withdrawn from every phone.
Nothing happens until a phone has registered: the monitor hook returns at once.
"""

import re
import threading
import time

import agent_link
import agent_status
import bot_memory
import bots
import bridge_home
import sf_lang
import sf_log
import sf_notify
import sf_push
from api_host import main

_YELLOW = re.compile(r"\[\[\s*SF\s*:\s*YELLOW\s*:\s*([^\]]+?)\s*\]\]")
_MARKER = re.compile(r"\[\[\s*SF\s*:[^\]]*\]\]")      # [[SF:GREEN]], [[SF:TO:…|…]], [[SF:MEMORY:SAVED]]…
WITHDRAW_DEDUP_S = 60.0


def yellow_reason(evs) -> str:
    """The reason of a [[SF:YELLOW:…]] in the newest turn's last words, or ''."""
    last_user = max((i for i, e in enumerate(evs or ()) if e.get("kind") == "user_msg"), default=-1)
    texts = [e.get("text") or "" for e in (evs or ())[last_user + 1:] if e.get("kind") == "assistant_text"]
    m = _YELLOW.search(texts[-1]) if texts else None
    return m.group(1).strip() if m else ""


def last_words(evs) -> str:
    """The last line the person would read: a turn often ends on a marker line for ShellBot,
    [[SF:…]] or the Telegram reply's closing [[/TG_REPLY_…]] (agent_link.preview_of takes that one off)."""
    texts = [e.get("text") or "" for e in (evs or ()) if e.get("kind") == "assistant_text"]
    for line in reversed(texts[-1].splitlines() if texts else []):
        line = agent_link.preview_of(_MARKER.sub("", line), 400, agent=True)
        if line:
            return line
    return ""


class PushApiMixin:
    def _pusher(self):
        p = getattr(self, "_sf_pusher", None)
        if p is None:
            p = self._sf_pusher = sf_push.Pusher(log=sf_log._dlog, redact=lambda t: agent_status.redact(t, 400))
        return p

    def _push_link_obj(self):
        """The running Frame Link, never started from here (phones reach this computer only through it)."""
        return getattr(self, "frame_link", None)

    # ── the phone's routes ──────────────────────────────────────────────────
    def _push_route(self, cmd: str, args: dict) -> dict:
        peer = str((args or {}).get("peer_id") or "")
        if cmd == "push_register":
            return sf_push.register(peer, args)
        if cmd == "push_unregister":
            return sf_push.unregister(peer)
        if cmd == "push_status":
            return sf_push.status(peer)
        if cmd == "push_test":
            link = self._push_link_obj()
            ok, msg = self._pusher().test(peer, frame_id=link.frame_id() if link else "",
                                          frame_name=link.frame_name() if link else "",
                                          text=sf_lang.pick("A test notification from this computer",
                                                            "這台電腦送來的測試通知"))
            return {"success": ok, "message": msg}
        if cmd == "peer_name":
            return self._peer_rename(peer, args.get("name"))
        return {"success": False, "message": f"Unknown command: {cmd}"}

    def _peer_rename(self, peer_id: str, name) -> dict:
        """A paired phone names itself here (say, after its model), without pairing again.
        Only its own entry, at most 60 characters."""
        name = " ".join(str(name or "").split())[:60]
        link = self._push_link_obj()
        if not name:
            return {"success": False, "message": "name required"}
        if not link or peer_id not in link.peers():
            return {"success": False, "message": "unknown peer"}

        def fn(block):
            entry = (block.get("peers") or {}).get(peer_id)
            if isinstance(entry, dict):
                entry["name"] = name
        link._mutate_cfg(fn)
        sf_log._dlog("link", f"peer {peer_id[:8]} renamed itself")
        return {"success": True, "message": f"renamed to {name}"}

    # ── sending ─────────────────────────────────────────────────────────────
    def _push_cue(self, kind, sid, *, text="", qid="", options=(), primary=None, title=None, exclude=()):
        link = self._push_link_obj()
        if link is None or not sf_push.devices():
            return 0
        try:
            role = self._bots_role_of(sid) if sid else ""
            raw = (main.load_config().get("agent_roster") or {}).get(role) if role else None
            primary_role = self._bots_primary_tab()[0]
        except Exception:
            role, raw, primary_role = "", None, ""
        s = self.sessions.get(sid) if sid else None
        name = ((bots.label_of(role, raw) if raw else "") or (getattr(s, "_custom_label", None) if s else "")
                or sid or link.frame_name() or "ShellBot")
        if primary is None:
            primary = bool(role) and role == primary_role
        return self._pusher().send(kind, sid=sid, bot=name, role=role, frame_id=link.frame_id(),
                                   frame_name=link.frame_name(), text=text, qid=qid, options=options,
                                   primary=primary, title=title, exclude=exclude, paired=set(link.peers()))

    def _push_withdraw(self, ids, exclude=()):
        """Take notifications off the phones; the same ids at most once a minute."""
        link = self._push_link_obj()
        if link is None:
            return 0
        now = time.time()
        recent = self.__dict__.setdefault("_push_withdrawn", {})
        for k in [k for k, t in recent.items() if now - t > WITHDRAW_DEDUP_S]:
            recent.pop(k, None)
        fresh = [i for i in ids if i not in recent]
        if not fresh:
            return 0
        for i in fresh:
            recent[i] = now
        return self._pusher().withdraw(fresh, frame_id=link.frame_id(), exclude=exclude)

    def _push_answered(self, sid: str, qid: str, peer_id: str):
        """/link/answer went in: the other phones drop that question now."""
        if qid:
            (self.__dict__.setdefault("_push_asked", {})).pop(sid, None)
            self._push_withdraw([f"q.{qid}"], exclude={peer_id} if peer_id else ())

    def _push_notify(self, args: dict) -> dict:
        """sfctl notify: the same event the webhook gets, on the phones as `info`."""
        sid = str(args.get("sid") or "")
        status = str(args.get("status") or "INFO").upper()
        word = {"DONE": ("done", "完成"), "BLOCKED": ("is stuck", "卡住了"),
                "QUESTION": ("has a question", "有問題")}.get(status)
        n = 0
        try:
            label = str(args.get("label") or "")
            title = None
            if word:
                s = self.sessions.get(sid)
                who = label or (getattr(s, "_custom_label", None) if s else "") or "ShellBot"
                title = f"{who}｜{sf_lang.pick(*word)}"
            n = self._push_cue("info", sid, text=str(args.get("message") or ""), title=title)
        except Exception:
            sf_log._swallow("push:notify")
        return {"success": True, "message": f"push: {n} device(s)"}

    def _push_a2a_result(self, sid: str, label: str, text: str):
        """A delegated task's result reached its sender: `done`, whoever the bot is."""
        try:
            self._push_cue("done", sid, text=(text or "").strip(), primary=True,
                           title=f"{label or sid}｜{sf_lang.pick('done', '完成')}")
        except Exception:
            sf_log._swallow("push:a2a_result")

    # ── the status monitor ──────────────────────────────────────────────────
    def _push_observe(self, out: dict):
        """Once per status-monitor pass (api_status._notify_transitions). Never raises."""
        try:
            if self._push_link_obj() is None or not sf_push.devices():
                return
            tracker = self.__dict__.setdefault("_push_tracker", sf_notify.TransitionTracker())
            asked = self.__dict__.setdefault("_push_asked", {})      # sid -> the question on the phones
            tracker.prune(out.keys())
            for sid, res in list(out.items()):
                res = res if isinstance(res, dict) else {}
                event = tracker.observe(sid, res)
                if res.get("state") == "working" and bridge_home.red_pending(sid):
                    bridge_home.forget(sid)           # a new turn answered the question or moved on
                if event == "BLOCKED":
                    self._push_cue("stuck", sid, text=str(res.get("blocked") or "")
                                   or sf_lang.pick("Waiting for you", "等你回應"))
                elif event == "DONE":
                    threading.Thread(target=self._push_turn_end, args=(sid, res), daemon=True,
                                     name=f"push-turn-{sid}").start()
                self._push_decision(sid, asked)
            for sid in [s for s in asked if s not in out]:              # the tab is gone
                self._push_withdraw([f"q.{asked.pop(sid)}"])
        except Exception:
            sf_log._swallow("push:observe")

    def _push_decision(self, sid, asked):
        """Push the tab's open question once; withdraw it when it is answered or replaced."""
        qid, d = "", None
        if bridge_home.red_pending(sid):
            snap = self._agent_status_snapshot(sid)
            d = self._link_decision(sid, "", (snap[0] if isinstance(snap, tuple) else snap) or {})
            qid = (d or {}).get("id") or ""
        old = asked.get(sid, "")
        if qid and qid != old:
            if old:
                self._push_withdraw([f"q.{old}"])
            asked[sid] = qid
            self._push_cue("decision", sid, text=d.get("reason") or "", qid=qid,
                           options=[(o["n"], o["text"]) for o in d.get("options") or []])
        elif old and not qid:
            asked.pop(sid, None)
            self._push_withdraw([f"q.{old}"])

    def _push_turn_end(self, sid, res):
        """A turn ended. Catch its [[SF:RED]] the way the Stop hook does (the next monitor pass
        pushes it), a [[SF:YELLOW]], and otherwise say it is done."""
        try:
            s = self.sessions.get(sid)
            path = agent_status.resolve_transcript(self._worker_ctx(sid, s)) if s else None
            evs = agent_status._read_tail_events(path)[1] if path else []
            if bot_memory.ended_by_request(evs):
                return                       # a bot saving its memory: nobody is told it finished
            bridge_home.on_turn_end(self, sid, read_events=lambda _sid: evs)
            if bridge_home.red_pending(sid):
                return
            reason = yellow_reason(evs)
            if reason:
                self._push_cue("stuck", sid, text=reason)
                return
            self._push_cue("done", sid, text=last_words(evs) or str((res or {}).get("task")
                                                                  or (res or {}).get("summary") or ""))
        except Exception:
            sf_log._swallow(f"push:turn_end:{sid}")
